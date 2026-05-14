"""Tests for P1-T9: Mock Pipeline Mode — fixture playback Worker.

AC1 — No real subprocess spawned when SA_RUNNER_MOCK_PIPELINE=1.
AC2 — All expected Checkpoint Files exist in Run Directory after mock run.
AC3 — At least one [<source>]-prefixed Progress Line emitted per Phase.
AC4 — GET /api/jobs/{id}/download/pdf returns HTTP 200 with non-empty body.
"""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import (
    _job_queues,
    clear_mock_invocations,
    get_job_queue,
    run_job,
)

CHECKPOINT_FILES = [
    "plan.json",
    "perplexity.json",
    "xai.json",
    "trends.json",
    "fmp.json",
    "evidence.json",
    "themes.json",
    "scores.json",
    "executive.json",
    "synthesis.json",
    "report.docx",
    "report.pdf",
]

_PHASE_PATTERNS = {
    "plan": re.compile(r"^\[plan\]"),
    "gather": re.compile(r"^\[(perplexity|xai|trends|fmp)\]"),
    "analyze": re.compile(
        r"^\[(extract|cluster|score|sections|executive|charts|assemble)\]"
    ),
    "render": re.compile(r"^\[render\]"),
}


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
                subject="Mock Pipeline Test",
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
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC1 — No real subprocess spawned
# ---------------------------------------------------------------------------


def test_mock_pipeline_spawns_no_subprocesses(tmp_path, monkeypatch):
    """AC1: asyncio.create_subprocess_exec is never called during a mock run."""
    job_id = "t9-ac1-no-subproc"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            _run_mock(job_id, run_dir, monkeypatch, tmp_path)
            mock_exec.assert_not_called()

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — All Checkpoint Files present in Run Directory
# ---------------------------------------------------------------------------


def test_mock_run_all_checkpoint_files_present(tmp_path, monkeypatch):
    """AC2: All 12 Checkpoint Files exist in Run Directory after a mock run."""
    job_id = "t9-ac2-files"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        missing = [f for f in CHECKPOINT_FILES if not (run_dir / f).exists()]
        assert not missing, f"Missing Checkpoint Files: {missing}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC3 — Progress Lines emitted per Phase
# ---------------------------------------------------------------------------


def test_mock_progress_lines_per_phase(tmp_path, monkeypatch):
    """AC3: At least one [<source>]-prefixed Progress Line is emitted per Phase."""
    job_id = "t9-ac3-progress"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        queue = get_job_queue(job_id)
        lines = []
        while not queue.empty():
            lines.append(queue.get_nowait())

        for phase, pattern in _PHASE_PATTERNS.items():
            matched = [ln for ln in lines if pattern.match(ln)]
            assert matched, f"No Progress Line for phase '{phase}'. Lines: {lines}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — Download PDF endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client():
    import app.keys as keys_mod

    keys_mod._fernet = None
    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c


def test_download_pdf_returns_200_nonempty(tmp_path, authed_client):
    """AC4: GET /api/jobs/{id}/download/pdf returns 200 with non-empty body."""
    job_id = "t9-ac4-download"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    pdf_content = b"%PDF-1.4 mock fixture content"
    (run_dir / "report.pdf").write_bytes(pdf_content)
    _insert_job(job_id, run_dir, status="done")

    try:
        resp = authed_client.get(f"/api/jobs/{job_id}/download/pdf")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert len(resp.content) > 0, "Response body is empty"
    finally:
        _delete_job(job_id)


def test_download_pdf_requires_auth():
    """AC4: Download endpoint requires authentication."""
    import app.keys as keys_mod

    keys_mod._fernet = None
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/api/jobs/any-job-id/download/pdf")
        assert resp.status_code == 401


def test_download_pdf_404_for_missing_job(authed_client):
    """GET /api/jobs/{id}/download/pdf returns 404 for non-existent job."""
    resp = authed_client.get("/api/jobs/nonexistent-job-id/download/pdf")
    assert resp.status_code == 404


def test_download_pdf_404_when_job_not_done(tmp_path, authed_client):
    """GET /api/jobs/{id}/download/pdf returns 404 when job is still running."""
    job_id = "t9-ac4-running"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    _insert_job(job_id, run_dir, status="running")

    try:
        resp = authed_client.get(f"/api/jobs/{job_id}/download/pdf")
        assert resp.status_code == 404
    finally:
        _delete_job(job_id)


def test_download_pdf_end_to_end_mock_run(tmp_path, monkeypatch):
    """AC4: PDF served for a job completed by mock pipeline is non-empty."""
    import app.keys as keys_mod

    keys_mod._fernet = None
    from app.main import app

    job_id = "t9-ac4-e2e"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"

        with TestClient(app) as c:
            c.post("/auth/login", json={"password": "test-password"})
            resp = c.get(f"/api/jobs/{job_id}/download/pdf")
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert len(resp.content) > 0, "Response body is empty"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
