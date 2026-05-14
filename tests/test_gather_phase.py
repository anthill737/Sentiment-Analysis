"""P2-T1: Gather Phase parallel subprocess execution.

AC1 — current_phase is set to 'gather' before the Gather Phase scripts run.
AC2 — Four fetchers run concurrently; wall-clock elapsed is well below sequential.
AC3 — Stderr lines from each fetcher appear in the SSE backlog with the correct source tag.
AC4 — A fetcher script writing a fake API key to stderr has it redacted in SSE and gather.log.
AC5 — gather.log is created and contains post-redaction lines from all four Gather sources.
"""

import asyncio
import sys
import time
from pathlib import Path

from sqlmodel import Session

import app.worker as worker_mod
from app.database import create_db_and_tables, engine
from app.keys import init_master_key
from app.models import Job
from app.worker import (
    _job_queues,
    _run_real_step,
    clear_mock_invocations,
    get_job_backlog,
    run_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_job(job_id: str, run_dir: Path, status: str = "running") -> None:
    create_db_and_tables()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="gather test",
                payload_json="{}",
                status=status,
                run_dir=str(run_dir),
            )
        )
        session.commit()


def _delete_job(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()


def _run_mock(job_id: str, run_dir: Path, monkeypatch, tmp_path: Path) -> None:
    """Run a complete mock pipeline for job_id synchronously."""
    skill_dir = tmp_path / "skill"
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", str(skill_dir / "node"))
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    _job_queues.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC1 — current_phase='gather' is written to the DB before fetchers run
# ---------------------------------------------------------------------------


def test_gather_phase_sets_current_phase(tmp_path, monkeypatch):
    """AC1: _update_job(current_phase='gather') is called before Gather Phase scripts launch."""
    phases_recorded: list[str] = []
    original_update = worker_mod._update_job

    def capturing_update(jid: str, **kwargs: object) -> None:
        if "current_phase" in kwargs and kwargs["current_phase"] is not None:
            phases_recorded.append(str(kwargs["current_phase"]))
        original_update(jid, **kwargs)

    job_id = "p2t1-ac1-phase"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)
    monkeypatch.setattr(worker_mod, "_update_job", capturing_update)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)
        assert "gather" in phases_recorded, (
            f"current_phase='gather' was never set; recorded phases: {phases_recorded}"
        )
        # Verify it appears BEFORE 'analyze' — gather is set before fetchers run
        gather_idx = phases_recorded.index("gather")
        if "analyze" in phases_recorded:
            analyze_idx = phases_recorded.index("analyze")
            assert gather_idx < analyze_idx, (
                f"'gather' must be set before 'analyze'; order was: {phases_recorded}"
            )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — four fetchers launch concurrently (wall-clock << sequential)
# ---------------------------------------------------------------------------


def test_gather_runs_concurrently(tmp_path):
    """AC2: Four real subprocesses run via asyncio.gather; wall-clock is well below sequential."""
    sleep_s = 0.3
    sources = ["perplexity", "xai", "trends", "fmp"]

    # Write four tiny Python scripts that each sleep briefly then write a tagged line.
    scripts: list[Path] = []
    for src in sources:
        script = tmp_path / f"fetch_{src}.py"
        script.write_text(
            "import sys, time\n"
            f"print('[{src}] start', file=sys.stderr, flush=True)\n"
            f"time.sleep({sleep_s})\n"
            f"print('[{src}] done', file=sys.stderr, flush=True)\n",
            encoding="utf-8",
        )
        scripts.append(script)

    log_path = tmp_path / "gather.log"

    async def run_gather() -> None:
        q: asyncio.Queue = asyncio.Queue()
        await asyncio.gather(
            *[
                _run_real_step(
                    cmd=[sys.executable, str(s)],
                    log_path=log_path,
                    queue=q,
                    api_keys={},
                    job_id="p2t1-ac2",
                )
                for s in scripts
            ]
        )

    start = time.monotonic()
    asyncio.run(run_gather())
    elapsed = time.monotonic() - start

    # Sequential floor: 4 × sleep_s = 1.2 s. Concurrent should be ~sleep_s.
    # Assert well below sequential to prove overlap, allowing generous startup overhead.
    sequential_floor = 4 * sleep_s
    assert elapsed < sequential_floor * 0.75, (
        f"Gather took {elapsed:.2f}s — expected concurrent execution (~{sleep_s}s); "
        f"sequential would be ≥{sequential_floor:.2f}s."
    )

    # All four source tags must appear in gather.log
    content = log_path.read_text(encoding="utf-8")
    for src in sources:
        assert f"[{src}]" in content, f"gather.log missing output from source '[{src}]'"


# ---------------------------------------------------------------------------
# AC3 — source tags appear in the SSE backlog
# ---------------------------------------------------------------------------


def test_gather_source_tags_in_sse_backlog(tmp_path, monkeypatch):
    """AC3: Each of the four fetcher source tags appears in the job's SSE log backlog."""
    job_id = "p2t1-ac3-tags"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)
        backlog = get_job_backlog(job_id)
        for source in ("perplexity", "xai", "trends", "fmp"):
            tagged = [ln for ln in backlog if ln.startswith(f"[{source}]")]
            assert tagged, (
                f"No SSE lines with tag '[{source}]' found in backlog. "
                f"All backlog lines: {backlog[:30]}"
            )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — API key strings written to stderr by a fetcher are redacted
# ---------------------------------------------------------------------------


def test_api_key_redacted_from_sse_and_gather_log(tmp_path):
    """AC4: A fetcher script that writes a fake API key to stderr leaks nothing raw."""
    init_master_key()

    # The key matches _API_KEY_PATTERN_RE (sk- prefix with 20+ word chars)
    fake_key = "sk-plx-FAKEKEY99XSECRET12345678901"

    leaky_script = tmp_path / "leaky_fetch.py"
    leaky_script.write_text(
        "import sys\n"
        f"print('[perplexity] key={fake_key}', file=sys.stderr, flush=True)\n"
        f"print('[perplexity] raw value: {fake_key}', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )

    log_path = tmp_path / "gather.log"

    async def run_step() -> list[str]:
        q: asyncio.Queue = asyncio.Queue()
        await _run_real_step(
            cmd=[sys.executable, str(leaky_script)],
            log_path=log_path,
            queue=q,
            api_keys={"perplexity": fake_key},
            job_id="p2t1-ac4",
        )
        lines: list[str] = []
        while not q.empty():
            lines.append(q.get_nowait())
        return lines

    sse_lines = asyncio.run(run_step())

    # Raw key must not appear in SSE stream
    for line in sse_lines:
        assert fake_key not in line, f"Raw API key leaked to SSE stream: {line!r}"

    # Raw key must not appear in gather.log
    log_content = log_path.read_text(encoding="utf-8")
    assert fake_key not in log_content, "Raw API key leaked to gather.log"
    # Redacted placeholder must be present
    assert "[REDACTED]" in log_content, (
        "Expected [REDACTED] placeholder in gather.log after redaction"
    )


# ---------------------------------------------------------------------------
# AC5 — gather.log is created with post-redaction lines from all four fetchers
# ---------------------------------------------------------------------------


def test_gather_log_has_all_source_lines(tmp_path, monkeypatch):
    """AC5: gather.log is created in <RUN_DIR>/logs/ and contains lines from all four Gather sources."""
    job_id = "p2t1-ac5-log"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        log_path = run_dir / "logs" / "gather.log"
        assert log_path.exists(), (
            "gather.log was not created at <RUN_DIR>/logs/gather.log"
        )

        content = log_path.read_text(encoding="utf-8")
        assert content.strip(), "gather.log is empty after Gather Phase completed"

        for source in ("perplexity", "xai", "trends", "fmp"):
            assert f"[{source}]" in content, (
                f"gather.log missing lines from Gather source '[{source}]'"
            )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
