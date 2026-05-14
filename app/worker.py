"""
Pipeline Worker - subprocess invocation, API key injection, phase log files,
interrupted-job recovery, and mock seam (gated by SA_RUNNER_MOCK_PIPELINE=1).
"""

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.database import engine
from app.keys import decrypt_api_key
from app.models import ApiKey, Job
from app.pricing import aggregate_usage

# ---------------------------------------------------------------------------
# Per-job in-memory state - consumed by the SSE endpoint (P1-T7)
# ---------------------------------------------------------------------------

_job_queues: dict[str, asyncio.Queue] = {}
_job_backlogs: dict[str, list[str]] = {}
_job_terminal: dict[str, bool] = {}
_job_gather_status: dict[str, dict] = {}
_job_analyze_status: dict[str, dict] = {}

# Track the asyncio.Task for each running job so the cancel endpoint can abort it.
_job_tasks: dict[str, asyncio.Task] = {}

# Track the currently active subprocess for each job so cancel can also terminate
# the child process (asyncio.Task.cancel() only raises CancelledError into the
# coroutine; the spawned subprocess keeps running until we kill it).
_job_active_proc: dict[str, asyncio.subprocess.Process] = {}


def register_job_task(job_id: str, task: asyncio.Task) -> None:
    _job_tasks[job_id] = task


def get_job_task(job_id: str) -> Optional[asyncio.Task]:
    return _job_tasks.get(job_id)


def get_active_proc(job_id: str) -> Optional[asyncio.subprocess.Process]:
    return _job_active_proc.get(job_id)


def get_job_queue(job_id: str) -> asyncio.Queue:
    if job_id not in _job_queues:
        _job_queues[job_id] = asyncio.Queue()
    return _job_queues[job_id]


def get_job_backlog(job_id: str) -> list[str]:
    return _job_backlogs.setdefault(job_id, [])


def is_job_terminal(job_id: str) -> bool:
    if _job_terminal.get(job_id, False):
        return True
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job and job.status in ("done", "failed", "cancelled", "awaiting_clarification"):
            _job_terminal[job_id] = True
            return True
    return False


def clear_job_terminal(job_id: str) -> None:
    """Used when a job resumes after clarification — SSE stream needs to re-open."""
    _job_terminal.pop(job_id, None)


def get_gather_status(job_id: str) -> dict:
    return _job_gather_status.get(job_id, {})


def get_analyze_status(job_id: str) -> dict:
    return _job_analyze_status.get(job_id, {})


# ---------------------------------------------------------------------------
# Runtime path resolution - never falls back to PATH
# ---------------------------------------------------------------------------


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def resolve_runtimes() -> dict[str, str]:
    """Return absolute paths to python.exe and node.exe.

    SA_RUNNER_{PYTHON,NODE}_EXE env vars override defaults so tests can
    substitute real executables without needing the full bundled runtime.

    PDF conversion is handled separately by Word via PowerShell COM automation
    (see _convert_docx_to_pdf_with_word). No external converter binary is
    bundled — the user is assumed to have Microsoft Word installed.
    """
    base = _local_app_data() / "sa-runner"
    return {
        "python": os.environ.get("SA_RUNNER_PYTHON_EXE")
        or str(base / "runtime" / "python" / "python.exe"),
        "node": os.environ.get("SA_RUNNER_NODE_EXE")
        or str(base / "runtime" / "node" / "node.exe"),
    }


def _skill_dir() -> Path:
    return Path(
        os.environ.get("SA_RUNNER_SKILL_DIR")
        or str(_local_app_data() / "sa-runner" / "skill")
    )


# ---------------------------------------------------------------------------
# Interrupted-job recovery - called at app startup
# ---------------------------------------------------------------------------


def recover_interrupted_jobs() -> None:
    """Transition any jobs still 'running' to 'failed' (app was hard-killed)."""
    with Session(engine) as session:
        for job in session.exec(select(Job).where(Job.status == "running")).all():
            job.status = "failed"
            job.error_message = "interrupted (app was closed)"
            session.add(job)
        session.commit()


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

_PROVIDER_ENV_MAP: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "xai": "XAI_API_KEY",
    "fmp": "FMP_API_KEY",
    "github": "GITHUB_TOKEN",
}


def _get_api_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    with Session(engine) as session:
        for row in session.exec(select(ApiKey)).all():
            try:
                keys[row.provider] = decrypt_api_key(row.ciphertext)
            except Exception:
                pass
    return keys


def _make_subprocess_env(api_keys: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    for provider, value in api_keys.items():
        env_var = _PROVIDER_ENV_MAP.get(provider)
        if env_var:
            env[env_var] = value
    return env


# Matches sk- prefixed API keys and 40+ char hex tokens.
_API_KEY_PATTERN_RE = re.compile(
    r"sk-[a-zA-Z0-9\-_]{20,}"
    r"|\b[a-fA-F0-9]{40,}\b"
)

# Matches Progress Lines from Gather sources.
_PROGRESS_RE = re.compile(
    r"^\[(perplexity|xai|trends|fmp|github|steam)\] \[(\d+)/(\d+)\] angle=(.+)$"
)

# Matches Progress Lines from write_sections Step.
_SECTIONS_PROGRESS_RE = re.compile(r"^\[sections\] \[(\d+)/(\d+)\] writing '(.+)'$")


def _redact_line(line: str, api_keys: dict[str, str]) -> str:
    for value in api_keys.values():
        if value and value in line:
            line = line.replace(value, "[REDACTED]")
    return _API_KEY_PATTERN_RE.sub("[REDACTED]", line)


def _parse_progress(job_id: str, line: str) -> None:
    """Parse a Progress Line and update in-memory gather/analyze status."""
    m = _PROGRESS_RE.match(line)
    if m:
        source = m.group(1)
        step = int(m.group(2))
        total = int(m.group(3))
        angle = m.group(4).strip()
        if job_id not in _job_gather_status:
            _job_gather_status[job_id] = {}
        _job_gather_status[job_id][source] = {
            "step": step,
            "total": total,
            "angle": angle,
        }
        return

    ms = _SECTIONS_PROGRESS_RE.match(line)
    if ms:
        step = int(ms.group(1))
        total = int(ms.group(2))
        title = ms.group(3)
        if job_id not in _job_analyze_status:
            _job_analyze_status[job_id] = {}
        _job_analyze_status[job_id]["sections"] = {
            "step": step,
            "total": total,
            "text": title,
        }


# ---------------------------------------------------------------------------
# Real step runner
# ---------------------------------------------------------------------------


async def _run_real_step(
    cmd: list[str],
    log_path: Path,
    queue: asyncio.Queue,
    api_keys: dict[str, str],
    job_id: str = "",
    cwd: Optional[str] = None,
    log_original: bool = False,
) -> int:
    """Run a Skill Script subprocess, streaming stderr to the log and SSE queue.

    When log_original=True the unredacted line is written to the log file while
    only the redacted form is forwarded to the SSE queue and backlog.  This is
    the correct behavior for Analyze Phase steps per the pipeline spec.
    """
    env = _make_subprocess_env(api_keys)
    # Inject SA_RUN_DIR so skill scripts can write usage.json to the run dir
    # without each one needing a --run-dir flag passed.
    try:
        env["SA_RUN_DIR"] = str(log_path.parent.parent)
    except Exception:
        pass
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        env=env,
        cwd=cwd,
    )
    if job_id:
        _job_active_proc[job_id] = proc
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_f:
            async for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                safe = _redact_line(line, api_keys)
                if log_original:
                    log_f.write(line + "\n")
                else:
                    log_f.write(safe + "\n")
                log_f.flush()
                if job_id:
                    _job_backlogs.setdefault(job_id, []).append(safe)
                    _parse_progress(job_id, safe)
                await queue.put(safe)
        return await proc.wait()
    except asyncio.CancelledError:
        # Cancel was requested. Terminate the subprocess and re-raise so the
        # outer coroutine can mark the job cancelled.
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        raise
    finally:
        if job_id and _job_active_proc.get(job_id) is proc:
            _job_active_proc.pop(job_id, None)


# ---------------------------------------------------------------------------
# Mock seam - captures invocations, writes fixture outputs, emits log lines
# ---------------------------------------------------------------------------


@dataclass
class StepInvocation:
    cmd: list
    cwd: Optional[str]


_mock_invocations: list[StepInvocation] = []


def get_mock_invocations() -> list[StepInvocation]:
    """Snapshot of subprocess invocations captured by the mock seam (test-facing)."""
    return list(_mock_invocations)


def clear_mock_invocations() -> None:
    _mock_invocations.clear()


def _fixtures_dir() -> Path:
    # SA_RUNNER_FIXTURES_DIR allows tests to inject custom fixture content
    # (e.g. Error Envelopes) without touching the canonical fixtures directory.
    return Path(
        os.environ.get("SA_RUNNER_FIXTURES_DIR")
        or str(Path(__file__).parent.parent / "tests" / "fixtures" / "skill_outputs")
    )


# Script filename -> fixture filename that the script would produce
_SCRIPT_FIXTURE: dict[str, str] = {
    "plan_research.py": "plan.json",
    "fetch_perplexity.py": "perplexity.json",
    "fetch_xai.py": "xai.json",
    "fetch_google_trends.py": "trends.json",
    "fetch_fmp.py": "fmp.json",
    "fetch_github.py": "github.json",
    "fetch_steam.py": "steam.json",
    "extract_evidence.py": "evidence.json",
    "cluster_themes.py": "themes.json",
    "score_angles.py": "scores.json",
    "write_executive.py": "executive.json",
    "assemble_report.py": "synthesis.json",
    "render_report.js": "report.docx",
}

_SCRIPT_LOG_TAG: dict[str, str] = {
    "plan_research.py": "plan",
    "fetch_perplexity.py": "perplexity",
    "fetch_xai.py": "xai",
    "fetch_google_trends.py": "trends",
    "fetch_fmp.py": "fmp",
    "fetch_github.py": "github",
    "fetch_steam.py": "steam",
    "extract_evidence.py": "extract",
    "cluster_themes.py": "cluster",
    "score_angles.py": "score",
    "write_sections.py": "sections",
    "write_executive.py": "executive",
    "generate_charts.py": "charts",
    "assemble_report.py": "assemble",
    "render_report.js": "render",
    "word_convert": "render",
}

# Gather source scripts — emit angle-format Progress Lines for realistic mock output.
_GATHER_SCRIPTS: frozenset[str] = frozenset(
    {"fetch_perplexity.py", "fetch_xai.py", "fetch_google_trends.py",
     "fetch_fmp.py", "fetch_github.py", "fetch_steam.py"}
)

# Angles emitted by mock gather sources so the frontend can show live updates.
_MOCK_GATHER_ANGLES: list[str] = [
    "market-sizing",
    "competitive-landscape",
    "adoption-signals",
]

# Section titles emitted as [sections] [N/M] writing '<title>' Progress Lines.
_MOCK_SECTION_TITLES: list[str] = [
    "Idea framing & hypotheses",
    "Demand validation",
    "Competitive landscape",
]

# Scripted Progress Lines for Analyze and Render Phase Steps.
# Keys match _SCRIPT_LOG_TAG values for non-gather scripts, PLUS "word_convert"
# which needs distinct lines from render_report.js (both share tag "render").
_ANALYZE_MOCK_LINES: dict[str, list[str]] = {
    "plan": [
        "[plan] [1/3] parsing payload JSON",
        "[plan] [2/3] generating research queries",
        "[plan] [3/3] wrote plan.json",
    ],
    "extract": [
        "[extract] [1/3] scanning perplexity source",
        "[extract] [2/3] scanning xai source",
        "[extract] [3/3] extracted evidence units",
    ],
    "cluster": [
        "[cluster] [1/3] grouping evidence by angle",
        "[cluster] [2/3] naming themes",
        "[cluster] [3/3] wrote themes.json",
    ],
    "score": [
        "[score] [1/3] scoring demand angle",
        "[score] [2/3] scoring competition angle",
        "[score] [3/3] computed composite score",
    ],
    "executive": [
        "[executive] [1/3] drafting verdict block",
        "[executive] [2/3] compiling pursue and pass reasons",
        "[executive] [3/3] wrote executive.json",
    ],
    "charts": [
        "[charts] [1/3] rendering angle scores chart",
        "[charts] [2/3] rendering demand intensity heatmap",
        "[charts] [3/3] wrote charts to output directory",
    ],
    "assemble": [
        "[assemble] [1/3] loading section manifests",
        "[assemble] [2/3] loading chart references",
        "[assemble] [3/3] wrote synthesis.json",
    ],
    "render": [
        "[render] [1/3] loading synthesis manifest",
        "[render] [2/3] rendering document sections",
        "[render] [3/3] wrote report artifact",
    ],
    # word_convert (Word PDF convert via PowerShell COM) shares tag "render"
    # but needs distinct lines so the frontend can detect the PDF sub-step.
    "word_convert": [
        "[render] [1/3] starting Word convert",
        "[render] [2/3] populating TOC",
        "[render] [3/3] saving to PDF",
    ],
}


def _get_script_name(cmd: list[str]) -> str:
    exe_stem = Path(cmd[0]).stem.lower()
    if exe_stem in ("powershell", "pwsh"):
        # The only PowerShell invocation in the worker is the Word PDF converter.
        # If we ever add more PS invocations we should disambiguate by inspecting
        # the script string in cmd[-1].
        return "word_convert"
    return Path(cmd[1]).name if len(cmd) > 1 else ""


def _find_arg(cmd: list[str], flag: str) -> Optional[str]:
    for i, arg in enumerate(cmd):
        if arg == flag and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


async def _emit_line(
    line: str,
    log_path: Path,
    queue: asyncio.Queue,
    job_id: str,
) -> None:
    """Write a log line to disk, backlog, and the SSE queue."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if job_id:
        _job_backlogs.setdefault(job_id, []).append(line)
        _parse_progress(job_id, line)
    await queue.put(line)


async def _run_mock_step(
    cmd: list[str],
    log_path: Path,
    queue: asyncio.Queue,
    api_keys: dict[str, str],
    job_id: str = "",
    cwd: Optional[str] = None,
    log_original: bool = False,
) -> int:
    # SA_RUNNER_MOCK_STEP_DELAY_MS: per-step pause used by E2E tests so the
    # frontend has time to observe intermediate states (e.g. active Phase Cards).
    delay_ms = int(os.environ.get("SA_RUNNER_MOCK_STEP_DELAY_MS", "0") or "0")

    _mock_invocations.append(StepInvocation(cmd=list(cmd), cwd=cwd))

    script_name = _get_script_name(cmd)
    tag = _SCRIPT_LOG_TAG.get(script_name, script_name)

    # --- Phase 1: File production ---

    if script_name == "word_convert":
        # The inline PS script contains $Pdf='<path>' — extract it to know where
        # the fixture should be written.
        ps_script = cmd[-1] if cmd else ""
        m = re.search(r"\$Pdf='([^']*(?:''[^']*)*)'", ps_script)
        if m:
            pdf_path = Path(m.group(1).replace("''", "'"))
            pdf_src = _fixtures_dir() / "report.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            if pdf_src.exists():
                shutil.copy2(str(pdf_src), str(pdf_path))
            else:
                await _emit_line(
                    f"[{tag}] ERROR: fixture report.pdf missing",
                    log_path,
                    queue,
                    job_id,
                )
                return 1

    elif script_name == "generate_charts.py":
        out_dir_val = _find_arg(cmd, "--out-dir")
        if out_dir_val:
            charts_dir = Path(out_dir_val)
            charts_dir.mkdir(parents=True, exist_ok=True)
            src_charts = _fixtures_dir() / "charts"
            if src_charts.is_dir():
                for chart_src in sorted(src_charts.iterdir()):
                    shutil.copy2(str(chart_src), str(charts_dir / chart_src.name))
            else:
                (charts_dir / "angles.png").write_bytes(b"MOCK_PNG")

    elif script_name == "write_sections.py":
        run_dir_val = _find_arg(cmd, "--run-dir")
        if run_dir_val:
            sections_dir = Path(run_dir_val) / "sections"
            sections_dir.mkdir(parents=True, exist_ok=True)
            src_sections = _fixtures_dir() / "sections"
            if src_sections.is_dir():
                for sec_src in sorted(src_sections.iterdir()):
                    shutil.copy2(str(sec_src), str(sections_dir / sec_src.name))

    else:
        fixture_name = _SCRIPT_FIXTURE.get(script_name)
        out_val = _find_arg(cmd, "--out")
        if fixture_name and out_val:
            out_path = Path(out_val)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            src = _fixtures_dir() / fixture_name
            if src.exists():
                shutil.copy2(str(src), str(out_path))
            elif out_path.suffix == ".json":
                out_path.write_text("{}", encoding="utf-8")
            else:
                # Non-JSON fixture missing — emit error and signal failure so the
                # Job transitions to status=failed (simulates a broken render step).
                await _emit_line(
                    f"[{tag}] ERROR: fixture {fixture_name} missing",
                    log_path,
                    queue,
                    job_id,
                )
                return 1

    # --- Phase 2: Progress line emission (at least 2 lines per Step) ---

    if script_name in _GATHER_SCRIPTS:
        # Emit realistic angle-format Progress Lines spread over the delay window
        # so the frontend Phase Cards show live step/angle updates.
        angles = _MOCK_GATHER_ANGLES
        n = len(angles)
        per_line_delay = delay_ms / n / 1000 if delay_ms > 0 else 0
        for i, angle in enumerate(angles, 1):
            if per_line_delay > 0:
                await asyncio.sleep(per_line_delay)
            await _emit_line(
                f"[{tag}] [{i}/{n}] angle={angle}", log_path, queue, job_id
            )

    elif script_name == "write_sections.py":
        # Emit [sections] [N/M] writing '<title>' for each section title.
        titles = _MOCK_SECTION_TITLES
        n = len(titles)
        per_line_delay = delay_ms / n / 1000 if delay_ms > 0 else 0
        for i, title in enumerate(titles, 1):
            if per_line_delay > 0:
                await asyncio.sleep(per_line_delay)
            await _emit_line(
                f"[sections] [{i}/{n}] writing '{title}'", log_path, queue, job_id
            )

    else:
        # Use script_name first so word_convert gets its own distinct lines
        # rather than sharing the render_report.js lines (both have tag="render").
        scripted = _ANALYZE_MOCK_LINES.get(script_name) or _ANALYZE_MOCK_LINES.get(tag)
        if scripted:
            lines = scripted
        else:
            lines = [
                f"[{tag}] [1/3] mock: step starting",
                f"[{tag}] [2/3] mock: step running",
                f"[{tag}] [3/3] mock: step complete",
            ]
        n = len(lines)
        per_line_delay = delay_ms / n / 1000 if delay_ms > 0 else 0
        for line in lines:
            if per_line_delay > 0:
                await asyncio.sleep(per_line_delay)
            await _emit_line(line, log_path, queue, job_id)

    return 0


# ---------------------------------------------------------------------------
# Job DB update helper
# ---------------------------------------------------------------------------


def _update_job(job_id: str, **kwargs: object) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            session.add(job)
            session.commit()


def _compute_job_totals(run_dir: Path, created_at: Optional[datetime],
                         completed_at: Optional[datetime]) -> dict:
    """Read usage.json from the run dir and produce DB-ready totals.

    Returns a dict with keys:
      - total_cost_usd (float or None)
      - total_duration_seconds (int or None)
      - cost_breakdown_json (str JSON or None)

    Missing files / errors are non-fatal — we just return Nones for those bits.
    Job completion never fails because cost computation hit a snag.
    """
    out = {
        "total_cost_usd": None,
        "total_duration_seconds": None,
        "cost_breakdown_json": None,
    }
    # Duration: simple delta
    if created_at and completed_at:
        try:
            seconds = (completed_at - created_at).total_seconds()
            out["total_duration_seconds"] = int(max(0, seconds))
        except Exception:
            pass

    # Cost: read usage.json, aggregate
    usage_path = run_dir / "usage.json"
    if usage_path.exists():
        try:
            usage_doc = json.loads(usage_path.read_text(encoding="utf-8"))
            totals = aggregate_usage(usage_doc.get("calls") or [])
            out["total_cost_usd"] = totals["total_usd"]
            out["cost_breakdown_json"] = json.dumps(totals)
        except Exception as exc:
            print(f"[worker] non-fatal: could not aggregate usage: {exc}",
                  file=sys.stderr)

    return out


def _extract_verdict_score(run_dir: Path) -> tuple[Optional[str], Optional[float]]:
    """Read executive.json and return (verdict_label, score) suitable for DB binding.

    The skill's executive.json has a `verdict` field that is itself a DICT
    ({"label": "Strong Signal", "opportunity_rating": 8.1, "confidence": 0.68,
    "color": "green"}), not a flat string. We flatten it to the label for the
    DB column (which is a string) and grab opportunity_rating as the score.
    Falls back gracefully if the file is missing or in an older flat-string shape.
    """
    try:
        exec_data = json.loads((run_dir / "executive.json").read_text(encoding="utf-8"))
    except Exception:
        return None, None

    raw_verdict = exec_data.get("verdict")
    raw_score = exec_data.get("score")

    verdict_label: Optional[str] = None
    score: Optional[float] = None

    if isinstance(raw_verdict, dict):
        # Modern shape: {label, opportunity_rating, confidence, color}
        verdict_label = raw_verdict.get("label")
        if raw_score is None:
            rating = raw_verdict.get("opportunity_rating")
            if isinstance(rating, (int, float)):
                score = float(rating)
    elif isinstance(raw_verdict, str):
        verdict_label = raw_verdict

    if score is None and isinstance(raw_score, (int, float)):
        score = float(raw_score)

    return verdict_label, score


# ---------------------------------------------------------------------------
# Log tail helper — used to include recent stderr context in error messages
# ---------------------------------------------------------------------------


def _read_log_tail(log_path: Path, n: int = 5) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]) if lines else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# DOCX → PDF conversion via Microsoft Word (PowerShell COM automation)
# ---------------------------------------------------------------------------


def _word_convert_cmd(docx_path: Path, pdf_path: Path) -> list[str]:
    """Build the PowerShell command line that converts a .docx to .pdf via Word.

    Word must be installed; it doesn't need to be running. We drive it via COM
    in invisible mode, open the .docx, update fields (so the TOC populates
    with real page numbers), then ExportAsFixedFormat to PDF, close the doc
    without saving, quit Word, and release the COM references.

    Progress lines use [Console]::Error.WriteLine — a direct write to stderr
    that does NOT raise a PowerShell error record. Write-Error with
    $ErrorActionPreference='Stop' would terminate on the first call.

    Open(): ConfirmConversions=$false, ReadOnly=$false (must be writable so
    Fields.Update() can rewrite the TOC; doc is closed without saving),
    AddToRecentFiles=$false. DisplayAlerts is set to -2 (wdAlertsMessageBox)
    which suppresses dialog prompts (e.g. 'Update fields?' for the TOC) but
    DOES allow Word to silently recover any minor structural issues in the
    file. Setting it to 0 (wdAlertsNone) is too aggressive — Word interprets
    the no-dialog policy as 'reject anything that would normally prompt',
    producing spurious 'file appears to be corrupted' errors.

    Fields.Update(): walks the document and computes real page numbers for
    TOC entries, replacing the placeholder values the renderer wrote. Without
    this, every TOC line says 'page 1'.

    Export: ExportAsFixedFormat is the modern PDF-export API (Word 2010+) and
    handles python-docx-generated content far more reliably than SaveAs with
    wdFormatPDF=17. Format=17 here means wdExportFormatPDF.
    """
    def _ps_escape(p: Path) -> str:
        return str(p).replace("'", "''")

    script = (
        "$ErrorActionPreference='Stop';"
        f"$Docx='{_ps_escape(docx_path)}';"
        f"$Pdf='{_ps_escape(pdf_path)}';"
        "$word=$null; $doc=$null;"
        "[Console]::Error.WriteLine('[render] [1/3] starting Word convert');"
        "try {"
        "  $word = New-Object -ComObject Word.Application;"
        "  $word.Visible = $false;"
        "  $word.DisplayAlerts = -2;"  # wdAlertsMessageBox — suppress prompts but allow recovery
        "  $doc = $word.Documents.Open("
        "    $Docx,"            # FileName
        "    $false,"           # ConfirmConversions
        "    $false,"           # ReadOnly — must be false so Fields.Update() can write
        "    $false"            # AddToRecentFiles
        "  );"
        "  [Console]::Error.WriteLine('[render] [2/3] populating TOC');"
        "  if ($doc.TablesOfContents.Count -gt 0) {"
        "    $doc.TablesOfContents.Item(1).Update() | Out-Null;"  # Rebuild the actual TOC structure
        "  };"
        "  $doc.Fields.Update() | Out-Null;"  # Refresh any other fields (page numbers, refs, etc.)
        "  [Console]::Error.WriteLine('[render] [3/3] saving to PDF');"
        "  $doc.ExportAsFixedFormat($Pdf, 17);"  # 17 = wdExportFormatPDF
        "  [Console]::Error.WriteLine('[render] DONE');"
        "} catch {"
        "  [Console]::Error.WriteLine('Word convert failed: ' + $_.Exception.Message);"
        "  exit 1;"
        "} finally {"
        "  if ($doc) { try { $doc.Close($false) } catch {}; "
        "    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null };"
        "  if ($word) { try { $word.Quit() } catch {}; "
        "    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null };"
        "  [System.GC]::Collect();"
        "  [System.GC]::WaitForPendingFinalizers();"
        "}"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
    ]


# ---------------------------------------------------------------------------
# Gather-phase error detection
# ---------------------------------------------------------------------------

# Ordered to match the asyncio.gather call below
_GATHER_SOURCES: list[tuple[str, str]] = [
    ("perplexity", "perplexity.json"),
    ("xai", "xai.json"),
    ("trends", "trends.json"),
    ("fmp", "fmp.json"),
    ("github", "github.json"),
    ("steam", "steam.json"),
]


def _detect_errored_sources(
    gather_results: list,
    run_dir: Path,
) -> list[str]:
    """Return names of sources that produced an Error Envelope or non-zero exit.

    A source is failed if:
    - subprocess exited non-zero or raised an exception
    - Checkpoint File is missing or unparseable
    - Checkpoint File has a top-level 'error' key (Error Envelope)
    - Checkpoint File is an empty JSON object {} (no payload written)

    A source whose Checkpoint File has status='skipped' is NOT counted as
    failed — that's an opt-in fetcher (fmp/github/steam) intentionally
    sitting this run out.
    """
    errored: list[str] = []
    for (source_name, out_file), rc in zip(_GATHER_SOURCES, gather_results):
        if isinstance(rc, Exception) or (isinstance(rc, int) and rc != 0):
            errored.append(source_name)
            continue
        # Check the checkpoint file for an Error Envelope or missing payload.
        try:
            data = json.loads((run_dir / out_file).read_text(encoding="utf-8"))
            if data.get("status") == "skipped":
                continue  # opt-in fetcher, gate flag was false — don't fail
            if "error" in data or not data:
                errored.append(source_name)
        except Exception:
            # File missing or not valid JSON — treat as failed source.
            errored.append(source_name)
    return errored


# ---------------------------------------------------------------------------
# Main worker coroutine
# ---------------------------------------------------------------------------


async def resynthesize_job(job_id: str) -> None:
    """Re-run as much of the pipeline as needed to produce a fresh report.pdf.

    Smart resume: inspects the run dir and picks the cheapest path that will
    yield a valid PDF. Three tiers:

      Tier 3 (PDF-only re-export, ~5s, $0):
        Both synthesis.json AND report.docx exist and are intact. Just re-run
        the Word convert step on the existing .docx.

      Tier 2 (render-only, ~30s, $0):
        synthesis.json exists and is intact, but report.docx is missing or
        stale. Re-run render_report.js (the Node DOCX renderer) plus the Word
        convert. No Claude calls.

      Tier 1 (full re-synthesize, ~3-5 min, $2-4):
        synthesis.json is missing or invalid, or one of its referenced section
        files is missing. Re-run extract → cluster → score → write_sections →
        write_executive → charts → assemble, then render + Word convert.

    All three tiers reuse Phase 1 (plan.json) and Phase 2 (perplexity.json/
    xai.json/trends.json/fmp.json) from the previous run — those are the
    expensive parts and are never re-fetched.
    """
    runtimes = resolve_runtimes()
    is_mock = os.environ.get("SA_RUNNER_MOCK_PIPELINE", "").strip() == "1"
    _run_step = _run_mock_step if is_mock else _run_real_step

    # Fresh queue + reset state for the streaming dashboard.
    queue = get_job_queue(job_id)
    _job_backlogs[job_id] = []
    _job_gather_status.pop(job_id, None)
    _job_analyze_status.pop(job_id, None)
    clear_job_terminal(job_id)

    api_keys = _get_api_keys()

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return
        run_dir = Path(job.run_dir)

    skill = _skill_dir()
    logs = run_dir / "logs"

    # Required Phase 1+2 inputs from the prior run — needed for any tier.
    required_inputs = ["plan.json", "perplexity.json", "xai.json"]
    missing = [p for p in required_inputs if not (run_dir / p).exists()]
    if missing:
        _update_job(
            job_id,
            status="failed",
            error_message=(
                f"Cannot re-synthesize — prior run is missing: {', '.join(missing)}. "
                "Run a fresh job from the New Job form instead."
            ),
            completed_at=datetime.utcnow(),
        )
        return

    # ------------------------------------------------------------------
    # Detect which tier we can use.
    # ------------------------------------------------------------------
    tier = _detect_resynthesize_tier(run_dir)
    await queue.put(f"[resynthesize] resume tier: {tier} ({_TIER_DESCRIPTIONS[tier]})")

    # Tier 1 needs a clean slate for all derived artifacts. Tier 2 keeps the
    # synthesis intact but rebuilds the .docx and .pdf. Tier 3 keeps everything
    # except the .pdf.
    if tier == 1:
        for stale in ("report.docx", "report.pdf", "synthesis.json", "executive.json"):
            try:
                (run_dir / stale).unlink()
            except FileNotFoundError:
                pass
        sections_dir = run_dir / "sections"
        if sections_dir.exists():
            try:
                shutil.rmtree(sections_dir)
            except Exception:
                pass
    elif tier == 2:
        for stale in ("report.docx", "report.pdf"):
            try:
                (run_dir / stale).unlink()
            except FileNotFoundError:
                pass
    elif tier == 3:
        try:
            (run_dir / "report.pdf").unlink()
        except FileNotFoundError:
            pass

    try:
        # ----------------------------------------------------------------
        # Tier 1: Phase 3 — Analyze (only if synthesis.json is missing/stale)
        # ----------------------------------------------------------------
        if tier == 1:
            _update_job(
                job_id,
                status="running",
                current_phase="analyze",
                error_message=None,
                completed_at=None,
            )
            analyze_steps: list[tuple[list[str], Optional[str]]] = [
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "extract_evidence.py"),
                        "--run-dir", str(run_dir),
                        "--out", str(run_dir / "evidence.json"),
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "cluster_themes.py"),
                        "--evidence", str(run_dir / "evidence.json"),
                        "--plan", str(run_dir / "plan.json"),
                        "--out", str(run_dir / "themes.json"),
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "score_angles.py"),
                        "--themes", str(run_dir / "themes.json"),
                        "--evidence", str(run_dir / "evidence.json"),
                        "--plan", str(run_dir / "plan.json"),
                        "--run-dir", str(run_dir),
                        "--out", str(run_dir / "scores.json"),
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "write_sections.py"),
                        "--run-dir", str(run_dir),
                        "--section", "all",
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "write_executive.py"),
                        "--run-dir", str(run_dir),
                        "--out", str(run_dir / "executive.json"),
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "generate_charts.py"),
                        "--run-dir", str(run_dir),
                        "--out-dir", str(run_dir / "charts"),
                    ],
                    None,
                ),
                (
                    [
                        runtimes["python"],
                        str(skill / "scripts" / "assemble_report.py"),
                        "--run-dir", str(run_dir),
                        "--out", str(run_dir / "synthesis.json"),
                    ],
                    None,
                ),
            ]
            for cmd, step_cwd in analyze_steps:
                rc = await _run_step(
                    cmd=cmd,
                    log_path=logs / "analyze.log",
                    queue=queue,
                    api_keys=api_keys,
                    job_id=job_id,
                    cwd=step_cwd,
                    log_original=True,
                )
                if rc != 0:
                    script = Path(cmd[1]).name
                    _update_job(
                        job_id,
                        status="failed",
                        error_message=f"Re-synthesize failed at {script} (exit {rc})",
                        completed_at=datetime.utcnow(),
                    )
                    return

        # ----------------------------------------------------------------
        # Tiers 1+2: Phase 4 — Render docx (skip for tier 3, docx is reused)
        # ----------------------------------------------------------------
        _update_job(job_id, current_phase="render", status="running",
                    error_message=None, completed_at=None)
        render_log = logs / "render.log"

        if tier in (1, 2):
            rc = await _run_step(
                cmd=[
                    runtimes["node"],
                    str(skill / "scripts" / "render_report.js"),
                    "--input", str(run_dir / "synthesis.json"),
                    "--out", str(run_dir / "report.docx"),
                ],
                log_path=render_log,
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
                cwd=str(skill),
            )
            if rc != 0:
                tail = _read_log_tail(render_log)
                _update_job(
                    job_id,
                    status="failed",
                    error_message=(f"Re-synthesize render failed (exit {rc})\n{tail}").strip(),
                    completed_at=datetime.utcnow(),
                )
                return

        # ----------------------------------------------------------------
        # All tiers: Word PDF convert
        # ----------------------------------------------------------------
        rc = await _run_step(
            cmd=_word_convert_cmd(run_dir / "report.docx", run_dir / "report.pdf"),
            log_path=render_log,
            queue=queue,
            api_keys=api_keys,
            job_id=job_id,
        )
        if rc != 0:
            tail = _read_log_tail(render_log)
            _update_job(
                job_id,
                status="failed",
                error_message=(f"Re-synthesize PDF convert failed (exit {rc})\n{tail}").strip(),
                completed_at=datetime.utcnow(),
            )
            return

        # ----------------------------------------------------------------
        # Done — refresh verdict/score from executive.json (tier 1 wrote a
        # new one; tiers 2/3 reuse the existing one).
        # ----------------------------------------------------------------
        verdict, score = _extract_verdict_score(run_dir)
        completed = datetime.utcnow()
        # Compute cost + duration totals from usage.json (best-effort)
        with Session(engine) as session:
            job_for_created = session.get(Job, job_id)
            created_at = job_for_created.created_at if job_for_created else None
        totals = _compute_job_totals(run_dir, created_at, completed)

        _update_job(
            job_id,
            status="done",
            current_phase="render",
            completed_at=completed,
            verdict=verdict,
            score=score,
            total_cost_usd=totals["total_cost_usd"],
            total_duration_seconds=totals["total_duration_seconds"],
            cost_breakdown_json=totals["cost_breakdown_json"],
        )

    except asyncio.CancelledError:
        _update_job(
            job_id,
            status="cancelled",
            error_message="Re-synthesize cancelled by user",
            completed_at=datetime.utcnow(),
        )
        raise
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            error_message=f"Re-synthesize failed: {exc}",
            completed_at=datetime.utcnow(),
        )
    finally:
        _job_terminal[job_id] = True
        _job_tasks.pop(job_id, None)
        _job_active_proc.pop(job_id, None)


_TIER_DESCRIPTIONS = {
    1: "full re-synthesize (~3-5 min, costs API calls)",
    2: "re-render docx + PDF (~30s, $0)",
    3: "re-export PDF only (~5s, $0)",
}


def _detect_resynthesize_tier(run_dir: Path) -> int:
    """Inspect the run dir and pick the cheapest resume tier.

    Returns:
        3 if both synthesis.json and report.docx exist and look valid →
          just re-run Word convert
        2 if synthesis.json exists and looks valid but report.docx is
          missing/empty → re-render docx + PDF
        1 otherwise → full Analyze + Render

    'Valid' means: file exists, is non-empty, and (for synthesis.json) parses
    as JSON with the expected top-level keys plus all the referenced section
    files actually exist on disk.
    """
    synthesis_path = run_dir / "synthesis.json"
    docx_path = run_dir / "report.docx"

    # Synthesis must exist, parse, and reference section files that all exist.
    if not synthesis_path.exists() or synthesis_path.stat().st_size == 0:
        return 1
    try:
        synth = json.loads(synthesis_path.read_text(encoding="utf-8"))
    except Exception:
        return 1
    # synthesis.json should have a 'sections' list with non-empty content for
    # each. If any required section block is empty or missing-from-disk, full
    # re-synth is needed.
    sections = synth.get("sections")
    if not isinstance(sections, list) or len(sections) == 0:
        return 1
    # Every section in synthesis.json should have at least one block. If the
    # skill recorded a section as failed (stub with single "generation failed"
    # paragraph), force a full re-synth.
    for sec in sections:
        blocks = sec.get("blocks") if isinstance(sec, dict) else None
        if not blocks:
            return 1
        # Heuristic: if the section has just 1-2 blocks AND any block contains
        # "generation failed", it's a failure stub from a prior crashed run.
        if len(blocks) <= 2:
            for blk in blocks:
                text = (blk.get("text") or "") if isinstance(blk, dict) else ""
                if "generation failed" in text.lower():
                    return 1

    # Synthesis is intact. Now check the docx.
    if not docx_path.exists() or docx_path.stat().st_size < 1024:
        # docx missing or suspiciously small (a real report is hundreds of KB)
        return 2

    # Both intact — just re-export PDF.
    return 3


async def run_job(job_id: str) -> None:
    """Orchestrate all four Phases for a Job. Updates the jobs row throughout."""
    runtimes = resolve_runtimes()
    is_mock = os.environ.get("SA_RUNNER_MOCK_PIPELINE", "").strip() == "1"
    _run_step = _run_mock_step if is_mock else _run_real_step

    queue = get_job_queue(job_id)
    api_keys = _get_api_keys()

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return
        run_dir = Path(job.run_dir)

    skill = _skill_dir()
    logs = run_dir / "logs"

    try:
        # ----------------------------------------------------------------
        # Phase 1 - Plan
        # ----------------------------------------------------------------
        _update_job(job_id, current_phase="plan")
        rc = await _run_step(
            cmd=[
                runtimes["python"],
                str(skill / "scripts" / "plan_research.py"),
                "--from-payload",
                str(run_dir / "payload.json"),
                "--out",
                str(run_dir / "plan.json"),
            ],
            log_path=logs / "plan.log",
            queue=queue,
            api_keys=api_keys,
            job_id=job_id,
        )
        if rc != 0:
            _update_job(
                job_id,
                status="failed",
                error_message=f"Plan phase failed (exit {rc})",
                completed_at=datetime.utcnow(),
            )
            return

        # Check if the planner asked for clarification. If so, pause the job
        # and surface the questions to the user via the API. The user submits
        # answers, which append to the subject, and the job re-runs Phase 1.
        plan_path = run_dir / "plan.json"
        if plan_path.exists():
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            except Exception:
                plan_data = {}
            if plan_data.get("needs_clarification"):
                questions = plan_data.get("clarification_questions") or []
                _update_job(
                    job_id,
                    status="awaiting_clarification",
                    clarification_questions=json.dumps(questions),
                )
                # Terminate the SSE stream and return — resume_after_clarification
                # will re-invoke run_job to pick up where we left off.
                _job_terminal[job_id] = True
                await queue.put("__TERMINAL__")
                return

        # ----------------------------------------------------------------
        # Phase 2 - Gather (four Sources in parallel)
        # ----------------------------------------------------------------
        _update_job(job_id, current_phase="gather")
        gather_results = await asyncio.gather(
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_perplexity.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "perplexity.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_xai.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "xai.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_google_trends.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "trends.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_fmp.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "fmp.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_github.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "github.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            _run_step(
                cmd=[
                    runtimes["python"],
                    str(skill / "scripts" / "fetch_steam.py"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "steam.json"),
                ],
                log_path=logs / "gather.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
            ),
            return_exceptions=True,
        )

        errored_sources = _detect_errored_sources(list(gather_results), run_dir)

        # The 3 mandatory sources are perplexity, xai, trends. If all 3 of
        # those errored, gather has effectively no data — fail the job.
        mandatory_errored = [s for s in errored_sources if s in ("perplexity", "xai", "trends")]
        if len(mandatory_errored) >= 3:
            msg = f"[gather] all sources failed: {', '.join(errored_sources)}"
            _job_backlogs.setdefault(job_id, []).append(msg)
            _update_job(
                job_id,
                status="failed",
                error_message=(
                    f"All Gather sources failed: {', '.join(errored_sources)}"
                ),
                completed_at=datetime.utcnow(),
            )
            await queue.put(msg)
            return

        if errored_sources:
            msg = f"[gather] degraded coverage: {', '.join(errored_sources)} failed"
            _job_backlogs.setdefault(job_id, []).append(msg)
            _update_job(
                job_id,
                degraded_sources=json.dumps(errored_sources),
            )
            await queue.put(msg)

        # ----------------------------------------------------------------
        # Phase 3 - Analyze (seven Steps, sequential)
        # ----------------------------------------------------------------
        _update_job(job_id, current_phase="analyze")
        analyze_steps: list[tuple[list[str], Optional[str]]] = [
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "extract_evidence.py"),
                    "--run-dir",
                    str(run_dir),
                    "--out",
                    str(run_dir / "evidence.json"),
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "cluster_themes.py"),
                    "--evidence",
                    str(run_dir / "evidence.json"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--out",
                    str(run_dir / "themes.json"),
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "score_angles.py"),
                    "--themes",
                    str(run_dir / "themes.json"),
                    "--evidence",
                    str(run_dir / "evidence.json"),
                    "--plan",
                    str(run_dir / "plan.json"),
                    "--run-dir",
                    str(run_dir),
                    "--out",
                    str(run_dir / "scores.json"),
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "write_sections.py"),
                    "--run-dir",
                    str(run_dir),
                    "--section",
                    "all",
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "write_executive.py"),
                    "--run-dir",
                    str(run_dir),
                    "--out",
                    str(run_dir / "executive.json"),
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "generate_charts.py"),
                    "--run-dir",
                    str(run_dir),
                    "--out-dir",
                    str(run_dir / "charts") + "/",
                ],
                None,
            ),
            (
                [
                    runtimes["python"],
                    str(skill / "scripts" / "assemble_report.py"),
                    "--run-dir",
                    str(run_dir),
                    "--out",
                    str(run_dir / "synthesis.json"),
                ],
                None,
            ),
        ]
        for cmd, step_cwd in analyze_steps:
            rc = await _run_step(
                cmd=cmd,
                log_path=logs / "analyze.log",
                queue=queue,
                api_keys=api_keys,
                job_id=job_id,
                cwd=step_cwd,
                log_original=True,
            )
            if rc != 0:
                script = Path(cmd[1]).name
                _update_job(
                    job_id,
                    status="failed",
                    error_message=f"Analyze phase failed at {script} (exit {rc})",
                    completed_at=datetime.utcnow(),
                )
                return

        # ----------------------------------------------------------------
        # Phase 4 - Render
        # ----------------------------------------------------------------
        _update_job(job_id, current_phase="render")

        render_log = logs / "render.log"

        # Node renderer (cwd = SKILL_DIR per pipeline contract)
        rc = await _run_step(
            cmd=[
                runtimes["node"],
                str(skill / "scripts" / "render_report.js"),
                "--input",
                str(run_dir / "synthesis.json"),
                "--out",
                str(run_dir / "report.docx"),
            ],
            log_path=render_log,
            queue=queue,
            api_keys=api_keys,
            job_id=job_id,
            cwd=str(skill),
        )
        if rc != 0:
            tail = _read_log_tail(render_log)
            _update_job(
                job_id,
                status="failed",
                error_message=(
                    f"Render phase failed at render_report.js (exit {rc})\n{tail}"
                ).strip(),
                completed_at=datetime.utcnow(),
            )
            return

        # PDF conversion via Microsoft Word (PowerShell COM)
        rc = await _run_step(
            cmd=_word_convert_cmd(run_dir / "report.docx", run_dir / "report.pdf"),
            log_path=render_log,
            queue=queue,
            api_keys=api_keys,
            job_id=job_id,
        )
        if rc != 0:
            tail = _read_log_tail(render_log)
            _update_job(
                job_id,
                status="failed",
                error_message=(
                    f"Render phase failed at Word PDF convert (exit {rc})\n{tail}"
                ).strip(),
                completed_at=datetime.utcnow(),
            )
            return

        # ----------------------------------------------------------------
        # Job complete - extract verdict/score from executive.json
        # ----------------------------------------------------------------
        verdict, score = _extract_verdict_score(run_dir)
        completed = datetime.utcnow()
        with Session(engine) as session:
            job_for_created = session.get(Job, job_id)
            created_at = job_for_created.created_at if job_for_created else None
        totals = _compute_job_totals(run_dir, created_at, completed)

        _update_job(
            job_id,
            status="done",
            current_phase="render",
            completed_at=completed,
            verdict=verdict,
            score=score,
            total_cost_usd=totals["total_cost_usd"],
            total_duration_seconds=totals["total_duration_seconds"],
            cost_breakdown_json=totals["cost_breakdown_json"],
        )

    except asyncio.CancelledError:
        # Cancellation requested by the user. The active subprocess has already
        # been terminated by _run_real_step's CancelledError handler.
        _update_job(
            job_id,
            status="cancelled",
            error_message="Cancelled by user",
            completed_at=datetime.utcnow(),
        )
        # Re-raise so the task is properly marked as cancelled.
        raise
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            error_message=str(exc),
            completed_at=datetime.utcnow(),
        )
    finally:
        _job_terminal[job_id] = True
        _job_tasks.pop(job_id, None)
        _job_active_proc.pop(job_id, None)
