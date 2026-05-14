"""P4-T6: pytest tests for Render Phase Worker logic.

AC1 — Complete mock Render phase produces report.docx and report.pdf in Run Directory.
AC2 — Node step failure: Job reaches status=failed, error_message non-empty, soffice not invoked.
AC3 — Soffice step failure: Job reaches status=failed, error_message non-empty.
AC4 — Subprocess arguments: node invocation uses absolute node.exe path, cwd=SKILL_DIR,
       and passes --input <RUN_DIR>/synthesis.json --out <RUN_DIR>/report.docx.
"""

import asyncio
import shutil
import sys
from pathlib import Path

from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import (
    _job_queues,
    clear_mock_invocations,
    get_mock_invocations,
    run_job,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "skill_outputs"


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
                subject="Render Phase Test",
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


def _run_mock(
    job_id: str,
    run_dir: Path,
    monkeypatch,
    tmp_path: Path,
    node_exe: str | None = None,
    skill_dir: Path | None = None,
) -> None:
    if skill_dir is None:
        skill_dir = tmp_path / "skill"
    if node_exe is None:
        node_exe = str(skill_dir / "node")
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", node_exe)
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    _job_queues.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC1 — Full mock Render phase produces both output files
# ---------------------------------------------------------------------------


def test_render_mock_produces_docx_and_pdf(tmp_path, monkeypatch):
    """AC1: A complete mock Render phase leaves report.docx and report.pdf in Run Directory."""
    job_id = "p4t6-ac1-both-files"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job did not reach done: {job.error_message!r}"
        assert (run_dir / "report.docx").exists(), (
            "report.docx missing from Run Directory after Render phase"
        )
        assert (run_dir / "report.pdf").exists(), (
            "report.pdf missing from Run Directory after Render phase"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — Node step failure: Job fails, soffice not invoked
# ---------------------------------------------------------------------------


def test_node_step_failure_fails_job_and_skips_soffice(tmp_path, monkeypatch):
    """AC2: Node step failure → Job status=failed, error_message non-empty, soffice not called."""
    # Fixtures dir without report.docx causes render_report.js mock step to return 1.
    sparse_fixtures = tmp_path / "sparse_no_docx"
    sparse_fixtures.mkdir()
    for src in _FIXTURES_DIR.iterdir():
        if src.is_file() and src.name != "report.docx":
            shutil.copy2(str(src), str(sparse_fixtures / src.name))
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(sparse_fixtures))

    job_id = "p4t6-ac2-node-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", (
            f"Expected status=failed after node step failure, got {job.status!r}"
        )
        assert job.error_message, "error_message must be non-empty when node step fails"

        invocations = get_mock_invocations()
        soffice_calls = [
            inv
            for inv in invocations
            if Path(inv.cmd[0]).stem.lower() in ("word_convert", "soffice.bin")
        ]
        assert not soffice_calls, (
            f"soffice should not be invoked after node step failure; "
            f"found {len(soffice_calls)} soffice call(s)"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC3 — Soffice step failure: Job fails
# ---------------------------------------------------------------------------


def test_soffice_step_failure_fails_job(tmp_path, monkeypatch):
    """AC3: Soffice step failure → Job status=failed, error_message non-empty."""
    # Fixtures dir with report.docx but WITHOUT report.pdf causes soffice mock to return 1.
    sparse_fixtures = tmp_path / "sparse_no_pdf"
    sparse_fixtures.mkdir()
    for src in _FIXTURES_DIR.iterdir():
        if src.is_file() and src.name != "report.pdf":
            shutil.copy2(str(src), str(sparse_fixtures / src.name))
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(sparse_fixtures))

    job_id = "p4t6-ac3-soffice-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", (
            f"Expected status=failed after soffice step failure, got {job.status!r}"
        )
        assert job.error_message, (
            "error_message must be non-empty when soffice step fails"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — Subprocess argument construction for the node invocation
# ---------------------------------------------------------------------------


def test_node_invocation_uses_correct_args(tmp_path, monkeypatch):
    """AC4: Node invocation uses absolute path, cwd=SKILL_DIR, correct --input/--out args."""
    skill_dir = tmp_path / "skill"
    node_exe = str(skill_dir / "node.exe")

    job_id = "p4t6-ac4-node-args"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(
            job_id,
            run_dir,
            monkeypatch,
            tmp_path,
            node_exe=node_exe,
            skill_dir=skill_dir,
        )

        invocations = get_mock_invocations()
        node_inv = next(
            (
                inv
                for inv in invocations
                if len(inv.cmd) > 1 and Path(inv.cmd[1]).name == "render_report.js"
            ),
            None,
        )
        assert node_inv is not None, (
            "render_report.js invocation not found in mock invocations. "
            f"All commands: {[inv.cmd for inv in invocations]}"
        )

        assert node_inv.cmd[0] == node_exe, (
            f"cmd[0] should be {node_exe!r}, got {node_inv.cmd[0]!r}"
        )

        assert node_inv.cwd == str(skill_dir), (
            f"cwd should be {str(skill_dir)!r}, got {node_inv.cwd!r}"
        )

        expected_input = str(run_dir / "synthesis.json")
        assert "--input" in node_inv.cmd, "Node invocation missing --input flag"
        input_idx = node_inv.cmd.index("--input")
        assert node_inv.cmd[input_idx + 1] == expected_input, (
            f"--input arg should be {expected_input!r}, "
            f"got {node_inv.cmd[input_idx + 1]!r}"
        )

        expected_out = str(run_dir / "report.docx")
        assert "--out" in node_inv.cmd, "Node invocation missing --out flag"
        out_idx = node_inv.cmd.index("--out")
        assert node_inv.cmd[out_idx + 1] == expected_out, (
            f"--out arg should be {expected_out!r}, got {node_inv.cmd[out_idx + 1]!r}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
