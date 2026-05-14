"""Tests for the Pipeline Worker (P1-T6).

All four acceptance criteria are covered:
  AC1 — Mock seam captures exact command-line arguments from the pipeline contract.
  AC2 — Plaintext API keys never appear in log files or any run-dir file.
  AC3 — Log files exist for all four phases after a complete mock run.
  AC4 — App startup transitions status=running jobs to status=failed.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.keys import encrypt_api_key, init_master_key
from app.models import ApiKey, Job
from app.worker import (
    StepInvocation,
    _job_queues,
    clear_mock_invocations,
    get_mock_invocations,
    recover_interrupted_jobs,
    run_job,
)

# ---------------------------------------------------------------------------
# Shared helpers
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
                subject="test subject",
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


def _get_job(job_id: str) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def _run_mock_job(job_id: str, run_dir: Path, monkeypatch, skill_dir: Path) -> None:
    """Run a complete mock pipeline for the given job_id synchronously."""
    python_exe = sys.executable  # a real executable so the path resolves
    node_exe = str(skill_dir / "node")
    powershell.exe = str(skill_dir / "word_convert")

    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", python_exe)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", node_exe)
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", powershell.exe)
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))

    _job_queues.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC4 — Interrupted-job recovery
# ---------------------------------------------------------------------------


def test_recover_interrupted_jobs_transitions_running_to_failed():
    """AC4: recover_interrupted_jobs() marks running rows as failed on startup."""
    job_id = "test-ac4-interrupted"
    run_dir = Path("/nonexistent/path")
    _insert_job(job_id, run_dir, status="running")

    try:
        recover_interrupted_jobs()
        job = _get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error_message == "interrupted (app was closed)"
    finally:
        _delete_job(job_id)


def test_recover_interrupted_jobs_leaves_done_jobs_alone():
    """recover_interrupted_jobs() must not touch jobs that are already done or failed."""
    done_id = "test-ac4-done"
    failed_id = "test-ac4-already-failed"
    _insert_job(done_id, Path("/nonexistent"), status="done")
    _insert_job(failed_id, Path("/nonexistent"), status="failed")

    try:
        recover_interrupted_jobs()
        assert _get_job(done_id).status == "done"
        assert _get_job(failed_id).status == "failed"
    finally:
        _delete_job(done_id)
        _delete_job(failed_id)


def test_recover_interrupted_jobs_multiple_running():
    """All running jobs are transitioned, not just the first one."""
    ids = ["test-ac4-multi-1", "test-ac4-multi-2", "test-ac4-multi-3"]
    for jid in ids:
        _insert_job(jid, Path("/nonexistent"), status="running")

    try:
        recover_interrupted_jobs()
        for jid in ids:
            job = _get_job(jid)
            assert job.status == "failed"
            assert job.error_message == "interrupted (app was closed)"
    finally:
        for jid in ids:
            _delete_job(jid)


def test_new_submission_accepted_after_interrupted_job_recovery(tmp_path, monkeypatch):
    """AC4/P5-T6-AC2: After app restart recovers interrupted jobs, new submissions succeed.

    Simulates a hard-kill scenario: a running job exists in the DB.  Starting
    the app (via TestClient lifespan) triggers recover_interrupted_jobs(), which
    transitions the job to failed and releases the single-job lock.  A subsequent
    POST /api/jobs must return 200, not 409.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    from app.main import app

    interrupted_id = "test-ac4-accept-after-recovery"
    run_dir = tmp_path / "sa-runner" / "runs" / interrupted_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "payload.json").write_text(
        '{"subject": "interrupted job"}', encoding="utf-8"
    )
    _insert_job(interrupted_id, run_dir, status="running")

    new_job_id = None
    try:
        # Starting the app via TestClient lifespan triggers recover_interrupted_jobs(),
        # transitioning the running job to failed and clearing the lock.
        with TestClient(app) as client:
            client.post("/auth/login", json={"password": "test-password"})
            resp = client.post(
                "/api/jobs",
                json={
                    "subject": "Post-recovery new job",
                    "time_window": "1y",
                    "focus_areas": "",
                    "include_public_market": False,
                    "tickers": "",
                },
            )
        assert resp.status_code == 200, (
            f"Expected 200 after interrupted-job recovery, got {resp.status_code}: {resp.text}"
        )
        new_job_id = resp.json().get("id")
    finally:
        _delete_job(interrupted_id)
        if new_job_id:
            _delete_job(new_job_id)


# ---------------------------------------------------------------------------
# AC3 — Log files exist after a complete mock run
# ---------------------------------------------------------------------------


def test_mock_run_creates_all_phase_log_files(tmp_path, monkeypatch):
    """AC3: plan.log, gather.log, analyze.log, render.log each contain at least one line."""
    job_id = "test-ac3-logs"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")

    _insert_job(job_id, run_dir)
    try:
        _run_mock_job(job_id, run_dir, monkeypatch, tmp_path / "skill")

        for phase in ("plan", "gather", "analyze", "render"):
            log_file = run_dir / "logs" / f"{phase}.log"
            assert log_file.exists(), f"{phase}.log was not created"
            content = log_file.read_text(encoding="utf-8")
            assert len(content.strip()) > 0, f"{phase}.log is empty"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_mock_run_job_reaches_done(tmp_path, monkeypatch):
    """Mock pipeline completes successfully and sets status=done."""
    job_id = "test-ac3-done"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")

    _insert_job(job_id, run_dir)
    try:
        _run_mock_job(job_id, run_dir, monkeypatch, tmp_path / "skill")
        job = _get_job(job_id)
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        assert job.verdict == "Worth Exploring"
        assert job.score == pytest.approx(7.9)
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — API keys not present in log files or any run-dir file
# ---------------------------------------------------------------------------


def test_api_keys_not_in_log_files(tmp_path, monkeypatch):
    """AC2: plaintext API key values do not appear in any log file."""
    init_master_key()

    plaintext_key = "sk-ant-test-secret-UNIQUE-9z2xq"
    ciphertext = encrypt_api_key(plaintext_key)
    with Session(engine) as session:
        row = session.get(ApiKey, "anthropic")
        if row:
            row.ciphertext = ciphertext
            session.add(row)
        else:
            session.add(ApiKey(provider="anthropic", ciphertext=ciphertext))
        session.commit()

    job_id = "test-ac2-keys"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")

    _insert_job(job_id, run_dir)
    try:
        _run_mock_job(job_id, run_dir, monkeypatch, tmp_path / "skill")

        # Check all log files
        for log_file in (run_dir / "logs").glob("*.log"):
            content = log_file.read_text(encoding="utf-8")
            assert plaintext_key not in content, (
                f"{log_file.name} contains the plaintext API key"
            )

        # Check no text file under run_dir contains the key
        for f in run_dir.rglob("*"):
            if f.is_file():
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    assert plaintext_key not in content, (
                        f"{f.relative_to(run_dir)} contains the plaintext API key"
                    )
                except Exception:
                    pass
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        with Session(engine) as session:
            row = session.get(ApiKey, "anthropic")
            if row:
                session.delete(row)
                session.commit()


# ---------------------------------------------------------------------------
# AC1 — Mock seam captures exact command-line arguments from the contract
# ---------------------------------------------------------------------------


def _invocations_for(
    job_id: str, run_dir: Path, monkeypatch, tmp_path: Path
) -> list[StepInvocation]:
    skill_dir = tmp_path / "skill"
    _insert_job(job_id, run_dir)
    try:
        _run_mock_job(job_id, run_dir, monkeypatch, skill_dir)
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
    return get_mock_invocations()


def test_mock_invocation_count(tmp_path, monkeypatch):
    """Exactly 14 subprocess invocations: 1+4+7+2."""
    job_id = "test-ac1-count"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text("{}", encoding="utf-8")

    invs = _invocations_for(job_id, run_dir, monkeypatch, tmp_path)
    assert len(invs) == 14, f"Expected 14 invocations, got {len(invs)}"


def test_mock_phase1_plan_command(tmp_path, monkeypatch):
    """AC1: Phase 1 — plan_research.py invoked with correct args."""
    job_id = "test-ac1-plan"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text("{}", encoding="utf-8")
    skill_dir = tmp_path / "skill"

    invs = _invocations_for(job_id, run_dir, monkeypatch, tmp_path)
    inv = invs[0]

    assert Path(inv.cmd[0]) == Path(sys.executable)
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "plan_research.py"
    assert "--from-payload" in inv.cmd
    fp_idx = inv.cmd.index("--from-payload") + 1
    assert Path(inv.cmd[fp_idx]) == run_dir / "payload.json"
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "plan.json"
    assert inv.cwd is None


def test_mock_phase2_gather_commands(tmp_path, monkeypatch):
    """AC1: Phase 2 — four fetchers with correct --plan and --out args."""
    job_id = "test-ac1-gather"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text("{}", encoding="utf-8")
    skill_dir = tmp_path / "skill"

    invs = _invocations_for(job_id, run_dir, monkeypatch, tmp_path)
    # Gather is invocations[1..4]
    expected = [
        ("fetch_perplexity.py", run_dir / "perplexity.json"),
        ("fetch_xai.py", run_dir / "xai.json"),
        ("fetch_google_trends.py", run_dir / "trends.json"),
        ("fetch_fmp.py", run_dir / "fmp.json"),
    ]
    for i, (script, expected_out) in enumerate(expected):
        inv = invs[1 + i]
        assert Path(inv.cmd[1]) == skill_dir / "scripts" / script, (
            f"Gather[{i}] script mismatch"
        )
        assert "--plan" in inv.cmd
        plan_idx = inv.cmd.index("--plan") + 1
        assert Path(inv.cmd[plan_idx]) == run_dir / "plan.json"
        assert "--out" in inv.cmd
        out_idx = inv.cmd.index("--out") + 1
        assert Path(inv.cmd[out_idx]) == expected_out
        assert inv.cwd is None


def test_mock_phase3_analyze_commands(tmp_path, monkeypatch):
    """AC1: Phase 3 — seven analyze scripts in order with correct args."""
    job_id = "test-ac1-analyze"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text("{}", encoding="utf-8")
    skill_dir = tmp_path / "skill"

    invs = _invocations_for(job_id, run_dir, monkeypatch, tmp_path)
    # Analyze is invocations[5..11]
    base = 5

    # extract_evidence.py
    inv = invs[base]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "extract_evidence.py"
    assert "--run-dir" in inv.cmd
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "evidence.json"

    # cluster_themes.py
    inv = invs[base + 1]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "cluster_themes.py"
    assert "--evidence" in inv.cmd
    assert "--plan" in inv.cmd
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "themes.json"

    # score_angles.py
    inv = invs[base + 2]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "score_angles.py"
    assert "--themes" in inv.cmd
    assert "--evidence" in inv.cmd
    assert "--plan" in inv.cmd
    assert "--run-dir" in inv.cmd
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "scores.json"

    # write_sections.py
    inv = invs[base + 3]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "write_sections.py"
    assert "--run-dir" in inv.cmd
    assert "--section" in inv.cmd
    section_idx = inv.cmd.index("--section") + 1
    assert inv.cmd[section_idx] == "all"

    # write_executive.py
    inv = invs[base + 4]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "write_executive.py"
    assert "--run-dir" in inv.cmd
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "executive.json"

    # generate_charts.py
    inv = invs[base + 5]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "generate_charts.py"
    assert "--run-dir" in inv.cmd
    assert "--out-dir" in inv.cmd
    outdir_idx = inv.cmd.index("--out-dir") + 1
    assert Path(inv.cmd[outdir_idx]) == run_dir / "charts"

    # assemble_report.py
    inv = invs[base + 6]
    assert Path(inv.cmd[1]) == skill_dir / "scripts" / "assemble_report.py"
    assert "--run-dir" in inv.cmd
    assert "--out" in inv.cmd
    out_idx = inv.cmd.index("--out") + 1
    assert Path(inv.cmd[out_idx]) == run_dir / "synthesis.json"


def test_mock_phase4_render_commands(tmp_path, monkeypatch):
    """AC1: Phase 4 — render_report.js (node, cwd=skill) and soffice with correct args."""
    job_id = "test-ac1-render"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text("{}", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    node_exe = str(skill_dir / "node")
    powershell.exe = str(skill_dir / "word_convert")

    invs = _invocations_for(job_id, run_dir, monkeypatch, tmp_path)

    # render_report.js (invocations[12])
    node_inv = invs[12]
    assert node_inv.cmd[0] == node_exe
    assert Path(node_inv.cmd[1]) == skill_dir / "scripts" / "render_report.js"
    assert "--input" in node_inv.cmd
    input_idx = node_inv.cmd.index("--input") + 1
    assert Path(node_inv.cmd[input_idx]) == run_dir / "synthesis.json"
    assert "--out" in node_inv.cmd
    out_idx = node_inv.cmd.index("--out") + 1
    assert Path(node_inv.cmd[out_idx]) == run_dir / "report.docx"
    assert node_inv.cwd == str(skill_dir)

    # soffice (invocations[13])
    so_inv = invs[13]
    assert so_inv.cmd[0] == powershell.exe
    assert "--headless" in so_inv.cmd
    assert "--convert-to" in so_inv.cmd
    ct_idx = so_inv.cmd.index("--convert-to") + 1
    assert so_inv.cmd[ct_idx] == "pdf"
    assert "--outdir" in so_inv.cmd
    outdir_idx = so_inv.cmd.index("--outdir") + 1
    assert Path(so_inv.cmd[outdir_idx]) == run_dir
    # last arg is the input docx
    assert Path(so_inv.cmd[-1]) == run_dir / "report.docx"
    assert so_inv.cwd is None
