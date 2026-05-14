"""P3-T4: Tests — pytest Worker coverage for Analyze Phase.

Three Analyze-Phase-specific tests verifying:
AC1(a) — All 7 Steps execute in the correct sequence, evidenced by the
          order Checkpoint Files appear in the Run Directory.
AC1(b) — A simulated non-zero exit from Step 3 (score_angles.py) leaves
          the Job with status=failed and no Checkpoint Files for Steps 4-7.
AC1(c) — A successful Analyze Phase produces all 7 Checkpoint Files with
          non-empty content.
"""

import asyncio
import json
import sys
from pathlib import Path

from sqlmodel import Session

import app.worker as worker_mod
from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import (
    _job_analyze_status,
    _job_queues,
    clear_mock_invocations,
    run_job,
)


# ---------------------------------------------------------------------------
# Shared helpers (same pattern as test_analyze_phase.py)
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
                subject="analyze phase coverage test",
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


def _run_mock(job_id: str, run_dir: Path, monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", str(skill_dir / "node"))
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    _job_queues.pop(job_id, None)
    _job_analyze_status.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# Analyze Phase scripts in their required sequential order.
# Each script produces a Checkpoint File / directory in the Run Directory.
_ANALYZE_SCRIPTS_IN_ORDER = [
    "extract_evidence.py",  # → evidence.json
    "cluster_themes.py",  # → themes.json
    "score_angles.py",  # → scores.json
    "write_sections.py",  # → sections/
    "write_executive.py",  # → executive.json
    "generate_charts.py",  # → charts/
    "assemble_report.py",  # → synthesis.json
]

# Matching Checkpoint Files / directories for each Analyze Step, in order.
# Used to verify presence in the Run Directory after a successful run.
_ANALYZE_CHECKPOINTS_IN_ORDER = [
    "evidence.json",
    "themes.json",
    "scores.json",
    "sections",
    "executive.json",
    "charts",
    "synthesis.json",
]


# ---------------------------------------------------------------------------
# AC1(a) — All 7 Steps execute in the correct sequence
# ---------------------------------------------------------------------------


def test_analyze_steps_execute_in_correct_sequence(tmp_path, monkeypatch):
    """AC1(a): All 7 Analyze Steps execute in the correct sequence.

    The test wraps _run_mock_step to record the order in which Analyze Phase
    scripts are invoked.  Each invocation creates one Checkpoint File (or
    directory) in the Run Directory, so the invocation order is equivalent to
    the Checkpoint File appearance order.  Both the invocation sequence and
    the existence of every Checkpoint File are asserted.

    Note: file st_mtime is intentionally not used for ordering because the
    mock uses shutil.copy2, which preserves the fixture file's modification
    time.  Invocation order is a more reliable and direct form of evidence.
    """
    invocation_order: list[str] = []
    original_mock = worker_mod._run_mock_step

    async def recording_mock(
        cmd: list,
        log_path: Path,
        queue: asyncio.Queue,
        api_keys: dict,
        job_id: str = "",
        cwd=None,
        log_original: bool = False,
    ) -> int:
        script = Path(cmd[1]).name if len(cmd) > 1 else ""
        invocation_order.append(script)
        return await original_mock(
            cmd, log_path, queue, api_keys, job_id, cwd, log_original
        )

    job_id = "p3t4-ac1a-sequence"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)
    monkeypatch.setattr(worker_mod, "_run_mock_step", recording_mock)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", (
            f"Job did not complete successfully: {job.error_message}"
        )

        # Filter the full pipeline invocation list to just Analyze Phase scripts.
        # The position of each script in this filtered list equals the position of
        # its Checkpoint File in the Run Directory appearance order.
        analyze_invocations = [
            s for s in invocation_order if s in _ANALYZE_SCRIPTS_IN_ORDER
        ]
        assert analyze_invocations == _ANALYZE_SCRIPTS_IN_ORDER, (
            f"Analyze steps were not invoked in the expected order.\n"
            f"  Expected: {_ANALYZE_SCRIPTS_IN_ORDER}\n"
            f"  Actual:   {analyze_invocations}"
        )

        # Verify every Checkpoint File / directory was produced in the Run Directory.
        for checkpoint_name in _ANALYZE_CHECKPOINTS_IN_ORDER:
            cp = run_dir / checkpoint_name
            assert cp.exists(), (
                f"Checkpoint File missing from Run Directory: {checkpoint_name}"
            )

    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC1(b) — Step 3 failure: Job=failed, Steps 4-7 produce no Checkpoint Files
# ---------------------------------------------------------------------------


def test_step3_failure_fails_job_no_checkpoint_files_steps_4_to_7(
    tmp_path, monkeypatch
):
    """AC1(b): Non-zero exit from score_angles.py (Step 3) fails the Job.

    After score_angles.py returns exit code 1:
    - Job status must be 'failed' with a non-empty error_message.
    - Steps 4-7 must produce no Checkpoint Files in the Run Directory:
      sections/ must not exist (or be empty), executive.json must be absent,
      charts/ must not exist (or be empty), synthesis.json must be absent.
    """
    original_mock = worker_mod._run_mock_step

    async def failing_step3_mock(
        cmd: list,
        log_path: Path,
        queue: asyncio.Queue,
        api_keys: dict,
        job_id: str = "",
        cwd=None,
        log_original: bool = False,
    ) -> int:
        script = Path(cmd[1]).name if len(cmd) > 1 else ""
        if script == "score_angles.py":
            return 1  # simulate non-zero exit from Step 3
        return await original_mock(
            cmd, log_path, queue, api_keys, job_id, cwd, log_original
        )

    job_id = "p3t4-ac1b-step3fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)
    monkeypatch.setattr(worker_mod, "_run_mock_step", failing_step3_mock)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", (
            f"Expected status='failed' after Step 3 failure, got '{job.status}'"
        )
        assert job.error_message, "error_message must be non-empty after step failure"

        # Steps 1-3 may have produced output; Steps 4-7 must NOT.
        sections_dir = run_dir / "sections"
        assert not sections_dir.exists() or not any(sections_dir.iterdir()), (
            "sections/ must not exist or be empty after score_angles.py failure"
        )
        assert not (run_dir / "executive.json").exists(), (
            "executive.json must not exist after score_angles.py failure"
        )
        charts_dir = run_dir / "charts"
        assert not charts_dir.exists() or not any(charts_dir.iterdir()), (
            "charts/ must not exist or be empty after score_angles.py failure"
        )
        assert not (run_dir / "synthesis.json").exists(), (
            "synthesis.json must not exist after score_angles.py failure"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC1(c) — Successful Analyze Phase: all 7 Checkpoint Files with non-empty content
# ---------------------------------------------------------------------------


def test_successful_analyze_produces_nonempty_checkpoint_files(tmp_path, monkeypatch):
    """AC1(c): A successful Analyze Phase produces all 7 Checkpoint Files with non-empty content.

    JSON Checkpoint Files must parse successfully and contain at least one key/element.
    Directory Checkpoint Files (sections/, charts/) must contain at least one non-empty file.
    """
    job_id = "p3t4-ac1c-nonempty"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"

        # Step 1: extract_evidence.py → evidence.json (non-empty JSON)
        evidence_path = run_dir / "evidence.json"
        assert evidence_path.exists(), "evidence.json missing"
        assert evidence_path.stat().st_size > 0, "evidence.json is empty"
        evidence_data = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence_data, "evidence.json contains no data"

        # Step 2: cluster_themes.py → themes.json (non-empty JSON)
        themes_path = run_dir / "themes.json"
        assert themes_path.exists(), "themes.json missing"
        assert themes_path.stat().st_size > 0, "themes.json is empty"
        themes_data = json.loads(themes_path.read_text(encoding="utf-8"))
        assert themes_data, "themes.json contains no data"

        # Step 3: score_angles.py → scores.json (non-empty JSON)
        scores_path = run_dir / "scores.json"
        assert scores_path.exists(), "scores.json missing"
        assert scores_path.stat().st_size > 0, "scores.json is empty"
        scores_data = json.loads(scores_path.read_text(encoding="utf-8"))
        assert scores_data, "scores.json contains no data"

        # Step 4: write_sections.py → sections/ directory with non-empty files
        sections_dir = run_dir / "sections"
        assert sections_dir.is_dir(), "sections/ directory missing"
        section_files = list(sections_dir.iterdir())
        assert section_files, "sections/ directory is empty"
        for sf in section_files:
            assert sf.stat().st_size > 0, f"Section file {sf.name} is empty"

        # Step 5: write_executive.py → executive.json (non-empty JSON)
        exec_path = run_dir / "executive.json"
        assert exec_path.exists(), "executive.json missing"
        assert exec_path.stat().st_size > 0, "executive.json is empty"
        exec_data = json.loads(exec_path.read_text(encoding="utf-8"))
        assert exec_data, "executive.json contains no data"

        # Step 6: generate_charts.py → charts/ directory with non-empty files
        charts_dir = run_dir / "charts"
        assert charts_dir.is_dir(), "charts/ directory missing"
        chart_files = list(charts_dir.iterdir())
        assert chart_files, "charts/ directory is empty"
        for cf in chart_files:
            assert cf.stat().st_size > 0, f"Chart file {cf.name} is empty"

        # Step 7: assemble_report.py → synthesis.json (non-empty JSON)
        synth_path = run_dir / "synthesis.json"
        assert synth_path.exists(), "synthesis.json missing"
        assert synth_path.stat().st_size > 0, "synthesis.json is empty"
        synth_data = json.loads(synth_path.read_text(encoding="utf-8"))
        assert synth_data, "synthesis.json contains no data"

    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)
