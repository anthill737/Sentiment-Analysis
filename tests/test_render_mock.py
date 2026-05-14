"""P4-T2: Render phase fixtures and Mock Pipeline Mode extension.

AC1 — tests/fixtures/skill_outputs/report.docx exists and is a valid DOCX
       (ZIP magic bytes PK\\x03\\x04).
AC2 — tests/fixtures/skill_outputs/report.pdf exists and starts with %PDF-.
AC3 — Mock Pipeline Mode run produces report.docx and report.pdf in the Run
       Directory copied from the fixtures, without invoking real node or soffice.
AC4 — The SSE Log Stream for a mocked Render phase contains at least one
       [render]-tagged Progress Line.
AC5 — If the render fixture is missing (SA_RUNNER_FIXTURES_DIR points to a dir
       without report.docx), the Job transitions to status=failed.
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
    get_job_queue,
    run_job,
)

# Canonical fixtures directory used by the mock seam.
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "skill_outputs"


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
                subject="Render Mock Test",
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
# AC1 — report.docx fixture is a valid DOCX (ZIP magic bytes)
# ---------------------------------------------------------------------------


def test_fixture_docx_exists_and_is_valid_docx():
    """AC1: report.docx fixture exists and has ZIP/DOCX magic bytes (PK\\x03\\x04)."""
    docx_path = _FIXTURES_DIR / "report.docx"
    assert docx_path.exists(), f"fixture not found: {docx_path}"
    data = docx_path.read_bytes()
    assert len(data) > 100, (
        f"report.docx is too small to be a valid DOCX: {len(data)} bytes"
    )
    # DOCX files are ZIP archives; ZIP local file header magic = PK\x03\x04
    assert data[:4] == b"PK\x03\x04", (
        f"report.docx does not start with ZIP magic PK\\x03\\x04; "
        f"got {data[:4].hex()!r}"
    )


def test_fixture_docx_openable_by_python_docx():
    """AC1: report.docx fixture can be opened by python-docx without error."""
    from docx import Document

    docx_path = _FIXTURES_DIR / "report.docx"
    doc = Document(str(docx_path))
    assert doc is not None


# ---------------------------------------------------------------------------
# AC2 — report.pdf fixture starts with %PDF-
# ---------------------------------------------------------------------------


def test_fixture_pdf_exists_and_starts_with_pdf_header():
    """AC2: report.pdf fixture exists and starts with the PDF magic bytes %PDF-."""
    pdf_path = _FIXTURES_DIR / "report.pdf"
    assert pdf_path.exists(), f"fixture not found: {pdf_path}"
    data = pdf_path.read_bytes()
    assert data[:5] == b"%PDF-", (
        f"report.pdf does not start with %PDF-; got {data[:10]!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — Mock run copies report.docx and report.pdf from fixtures into Run Dir
# ---------------------------------------------------------------------------


def test_mock_render_copies_docx_from_fixture(tmp_path, monkeypatch):
    """AC3: report.docx in Run Directory matches the fixture content after mock run."""
    job_id = "p4t2-ac3-docx-copy"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"

        result_docx = run_dir / "report.docx"
        assert result_docx.exists(), "report.docx missing from Run Directory"

        fixture_bytes = (_FIXTURES_DIR / "report.docx").read_bytes()
        result_bytes = result_docx.read_bytes()
        assert result_bytes == fixture_bytes, (
            "report.docx in Run Directory does not match the fixture; "
            "the mock must copy from fixtures, not generate inline bytes"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_mock_render_copies_pdf_from_fixture(tmp_path, monkeypatch):
    """AC3: report.pdf in Run Directory matches the fixture content after mock run."""
    job_id = "p4t2-ac3-pdf-copy"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"

        result_pdf = run_dir / "report.pdf"
        assert result_pdf.exists(), "report.pdf missing from Run Directory"

        fixture_bytes = (_FIXTURES_DIR / "report.pdf").read_bytes()
        result_bytes = result_pdf.read_bytes()
        assert result_bytes == fixture_bytes, (
            "report.pdf in Run Directory does not match the fixture; "
            "the mock must copy from fixtures, not generate inline bytes"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — SSE Log Stream contains at least one [render]-tagged Progress Line
# ---------------------------------------------------------------------------


def test_mock_render_emits_render_progress_lines(tmp_path, monkeypatch):
    """AC4: SSE Log Stream contains at least one [render]-tagged Progress Line."""
    job_id = "p4t2-ac4-render-lines"
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

        render_lines = [ln for ln in lines if ln.startswith("[render]")]
        assert render_lines, (
            "No [render]-tagged Progress Lines in SSE stream. All lines:\n"
            + "\n".join(lines)
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC5 — Missing render fixture causes Job to transition to status=failed
# ---------------------------------------------------------------------------


def test_missing_docx_fixture_fails_job(tmp_path, monkeypatch):
    """AC5: If report.docx fixture is missing the Job transitions to status=failed."""
    # Build a fixtures dir that has all JSON fixtures but is missing report.docx.
    sparse_fixtures = tmp_path / "sparse_fixtures"
    sparse_fixtures.mkdir()
    for src in _FIXTURES_DIR.iterdir():
        if src.is_file() and src.name != "report.docx":
            shutil.copy2(str(src), str(sparse_fixtures / src.name))
    # Subdirectories (charts, sections) can be absent — we only care about render.
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(sparse_fixtures))

    job_id = "p4t2-ac5-missing-docx"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", (
            f"Expected status=failed when report.docx fixture is missing, "
            f"got {job.status!r} (error: {job.error_message!r})"
        )
        assert job.error_message, (
            "error_message must be non-empty when render fixture is missing"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_missing_pdf_fixture_fails_job(tmp_path, monkeypatch):
    """AC5: If report.pdf fixture is missing the Job transitions to status=failed."""
    # Build a fixtures dir that has report.docx but is missing report.pdf.
    sparse_fixtures = tmp_path / "sparse_fixtures_nopdf"
    sparse_fixtures.mkdir()
    for src in _FIXTURES_DIR.iterdir():
        if src.is_file() and src.name != "report.pdf":
            shutil.copy2(str(src), str(sparse_fixtures / src.name))
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(sparse_fixtures))

    job_id = "p4t2-ac5-missing-pdf"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", (
            f"Expected status=failed when report.pdf fixture is missing, "
            f"got {job.status!r} (error: {job.error_message!r})"
        )
        assert job.error_message, (
            "error_message must be non-empty when report.pdf fixture is missing"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
