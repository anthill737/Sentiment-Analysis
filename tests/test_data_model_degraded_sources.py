"""Tests for P2-T3: degraded_sources data model and API exposure.

AC1 — sqlite3 .schema shows a degraded_sources column after app starts.
AC2 — GET /api/jobs/{id} for a Degraded Coverage job returns a degraded_sources array.
AC3 — GET /api/jobs/{id} for a non-degraded job returns degraded_sources as null/empty.
AC4 — degraded_sources persists correctly after app restart (stored in SQLite, not memory).
AC5 — GET /api/jobs (history) includes degraded_sources for each job entry.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.models import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_job(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()


def _insert_job(job_id: str, degraded_sources: str | None = None) -> None:
    create_db_and_tables()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="P2-T3 schema test",
                payload_json="{}",
                status="done",
                run_dir="/tmp/test",
                degraded_sources=degraded_sources,
            )
        )
        session.commit()


@pytest.fixture
def authed_client():
    import app.keys as keys_mod

    keys_mod._fernet = None
    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c


# ---------------------------------------------------------------------------
# AC1 — Schema check
# ---------------------------------------------------------------------------


def test_schema_has_degraded_sources_column():
    """AC1: jobs table has a degraded_sources column after create_db_and_tables()."""
    create_db_and_tables()
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
    col_names = [row[1] for row in rows]
    assert "degraded_sources" in col_names, (
        f"degraded_sources column not found in jobs table. Columns: {col_names}"
    )


def test_schema_degraded_sources_column_is_nullable():
    """AC1: degraded_sources is TEXT and nullable (no NOT NULL constraint)."""
    create_db_and_tables()
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    col_info = {row[1]: {"type": row[2], "notnull": row[3]} for row in rows}
    assert "degraded_sources" in col_info
    assert col_info["degraded_sources"]["notnull"] == 0, (
        "degraded_sources should be nullable"
    )


# ---------------------------------------------------------------------------
# AC4 — Persistence after restart (fresh session reads stored value)
# ---------------------------------------------------------------------------


def test_degraded_sources_persists_in_sqlite():
    """AC4: degraded_sources written in one session is readable in a new session."""
    job_id = "p2t3-persist-test"
    sources = ["perplexity", "fmp"]

    _insert_job(job_id, degraded_sources=json.dumps(sources))
    try:
        # Simulate restart: open a completely fresh Session object
        with Session(engine) as fresh_session:
            job = fresh_session.get(Job, job_id)
        assert job is not None
        assert job.degraded_sources is not None, "degraded_sources should not be None"
        stored = json.loads(job.degraded_sources)
        assert stored == sources, f"Expected {sources}, got {stored}"
    finally:
        _clear_job(job_id)


def test_null_degraded_sources_persists_in_sqlite():
    """AC4: null degraded_sources also persists correctly across sessions."""
    job_id = "p2t3-persist-null-test"
    _insert_job(job_id, degraded_sources=None)
    try:
        with Session(engine) as fresh_session:
            job = fresh_session.get(Job, job_id)
        assert job is not None
        assert job.degraded_sources is None, (
            f"Expected None, got {job.degraded_sources!r}"
        )
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC2 — GET /api/jobs/{id} returns degraded_sources array for degraded job
# ---------------------------------------------------------------------------


def test_get_job_returns_degraded_sources_array(authed_client):
    """AC2: GET /api/jobs/{id} includes degraded_sources list for a degraded job."""
    job_id = "p2t3-api-degraded"
    sources = ["perplexity", "fmp"]
    _insert_job(job_id, degraded_sources=json.dumps(sources))
    try:
        resp = authed_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "degraded_sources" in data, "degraded_sources key missing from response"
        assert isinstance(data["degraded_sources"], list)
        assert set(data["degraded_sources"]) == set(sources), (
            f"Expected {sources}, got {data['degraded_sources']}"
        )
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC3 — GET /api/jobs/{id} returns null/empty for non-degraded job
# ---------------------------------------------------------------------------


def test_get_job_returns_empty_degraded_sources_for_clean_job(authed_client):
    """AC3: GET /api/jobs/{id} returns null or [] for a job with no errored sources."""
    job_id = "p2t3-api-clean"
    _insert_job(job_id, degraded_sources=None)
    try:
        resp = authed_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "degraded_sources" in data
        assert data["degraded_sources"] in (None, []), (
            f"Expected null or [], got {data['degraded_sources']!r}"
        )
    finally:
        _clear_job(job_id)


# ---------------------------------------------------------------------------
# AC5 — GET /api/jobs (history) includes degraded_sources per entry
# ---------------------------------------------------------------------------


def test_history_list_includes_degraded_sources(authed_client):
    """AC5: GET /api/jobs returns degraded_sources for every job in the history list."""
    job_id_deg = "p2t3-list-degraded"
    job_id_clean = "p2t3-list-clean"
    _insert_job(job_id_deg, degraded_sources=json.dumps(["xai"]))
    _insert_job(job_id_clean, degraded_sources=None)
    try:
        resp = authed_client.get("/api/jobs")
        assert resp.status_code == 200, resp.text
        jobs = {j["id"]: j for j in resp.json()}

        assert job_id_deg in jobs, "Degraded job not found in history list"
        assert "degraded_sources" in jobs[job_id_deg]
        assert "xai" in jobs[job_id_deg]["degraded_sources"], (
            f"Expected xai in degraded_sources: {jobs[job_id_deg]['degraded_sources']}"
        )

        assert job_id_clean in jobs, "Clean job not found in history list"
        assert "degraded_sources" in jobs[job_id_clean]
        assert jobs[job_id_clean]["degraded_sources"] in (None, []), (
            f"Expected null/[] for clean job: {jobs[job_id_clean]['degraded_sources']}"
        )
    finally:
        _clear_job(job_id_deg)
        _clear_job(job_id_clean)
