"""P4-T3: Report file download endpoints.

AC1 — Authenticated GET /api/jobs/{id}/download/pdf for a done Job returns 200
      with Content-Type: application/pdf and Content-Disposition: attachment;
      filename="report.pdf", body is actual PDF bytes.
AC2 — Authenticated GET /api/jobs/{id}/download/docx for a done Job returns 200
      with Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
      and delivers actual DOCX bytes.
AC3 — Unauthenticated request to either endpoint returns 401.
AC4 — Request for a Job whose report file does not exist on disk returns 404.
AC5 — Request for a non-existent job id returns 404.
"""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

PDF_CONTENT = b"%PDF-1.4 fake pdf bytes for p4t3 test"
DOCX_CONTENT = b"PK\x03\x04 fake docx bytes for p4t3 test"

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _clear_job(job_id: str) -> None:
    from app.database import create_db_and_tables, engine
    from app.models import Job

    create_db_and_tables()
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()


def _insert_done_job(job_id: str, run_dir: Path) -> None:
    from app.database import create_db_and_tables, engine
    from app.models import Job

    create_db_and_tables()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="download test",
                payload_json="{}",
                status="done",
                run_dir=str(run_dir),
                completed_at=datetime.utcnow(),
            )
        )
        session.commit()


def _insert_running_job(job_id: str, run_dir: Path) -> None:
    from app.database import create_db_and_tables, engine
    from app.models import Job

    create_db_and_tables()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="download test running",
                payload_json="{}",
                status="running",
                run_dir=str(run_dir),
            )
        )
        session.commit()


@pytest.fixture
def auth_client():
    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c


@pytest.fixture
def anon_client():
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# AC1 — PDF download: 200, correct content-type and content-disposition, real bytes
# ---------------------------------------------------------------------------


def test_download_pdf_returns_200_with_correct_headers(tmp_path, auth_client):
    """AC1: done job PDF download returns 200 with correct content-type and disposition."""
    job_id = "p4t3-ac1-pdf"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.pdf").write_bytes(PDF_CONTENT)

    _insert_done_job(job_id, run_dir)
    try:
        resp = auth_client.get(f"/api/jobs/{job_id}/download/pdf")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd, f"Expected attachment disposition, got: {cd!r}"
        assert 'filename="report.pdf"' in cd, (
            f"Expected filename in disposition, got: {cd!r}"
        )
        assert resp.content == PDF_CONTENT, "Response body must be the actual PDF bytes"
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC2 — DOCX download: 200, correct content-type, real bytes
# ---------------------------------------------------------------------------


def test_download_docx_returns_200_with_correct_headers(tmp_path, auth_client):
    """AC2: done job DOCX download returns 200 with correct content-type and actual bytes."""
    job_id = "p4t3-ac2-docx"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.docx").write_bytes(DOCX_CONTENT)

    _insert_done_job(job_id, run_dir)
    try:
        resp = auth_client.get(f"/api/jobs/{job_id}/download/docx")
        assert resp.status_code == 200, resp.text
        assert DOCX_CONTENT_TYPE in resp.headers["content-type"], (
            f"Expected DOCX content-type, got: {resp.headers['content-type']!r}"
        )
        assert resp.content == DOCX_CONTENT, (
            "Response body must be the actual DOCX bytes"
        )
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC3 — Unauthenticated requests return 401
# ---------------------------------------------------------------------------


def test_download_pdf_unauthenticated_returns_401(anon_client):
    """AC3: unauthenticated PDF download returns 401."""
    resp = anon_client.get("/api/jobs/some-job-id/download/pdf")
    assert resp.status_code == 401, resp.text


def test_download_docx_unauthenticated_returns_401(anon_client):
    """AC3: unauthenticated DOCX download returns 401."""
    resp = anon_client.get("/api/jobs/some-job-id/download/docx")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# AC4 — File not on disk returns 404
# ---------------------------------------------------------------------------


def test_download_pdf_file_missing_returns_404(tmp_path, auth_client):
    """AC4: done job whose report.pdf is missing on disk returns 404."""
    job_id = "p4t3-ac4-pdf-missing"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    # No report.pdf written

    _insert_done_job(job_id, run_dir)
    try:
        resp = auth_client.get(f"/api/jobs/{job_id}/download/pdf")
        assert resp.status_code == 404, resp.text
    finally:
        _clear_job(job_id)


def test_download_docx_file_missing_returns_404(tmp_path, auth_client):
    """AC4: done job whose report.docx is missing on disk returns 404."""
    job_id = "p4t3-ac4-docx-missing"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    # No report.docx written

    _insert_done_job(job_id, run_dir)
    try:
        resp = auth_client.get(f"/api/jobs/{job_id}/download/docx")
        assert resp.status_code == 404, resp.text
    finally:
        _clear_job(job_id)


def test_download_pdf_running_job_returns_404(tmp_path, auth_client):
    """AC4: running job (files not yet created) returns 404 for PDF download."""
    job_id = "p4t3-ac4-pdf-running"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)

    _insert_running_job(job_id, run_dir)
    try:
        resp = auth_client.get(f"/api/jobs/{job_id}/download/pdf")
        assert resp.status_code == 404, resp.text
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC5 — Non-existent job id returns 404
# ---------------------------------------------------------------------------


def test_download_pdf_nonexistent_job_returns_404(auth_client):
    """AC5: non-existent job id returns 404 for PDF download."""
    resp = auth_client.get("/api/jobs/does-not-exist-xyz/download/pdf")
    assert resp.status_code == 404, resp.text


def test_download_docx_nonexistent_job_returns_404(auth_client):
    """AC5: non-existent job id returns 404 for DOCX download."""
    resp = auth_client.get("/api/jobs/does-not-exist-xyz/download/docx")
    assert resp.status_code == 404, resp.text
