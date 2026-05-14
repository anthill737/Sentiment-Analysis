import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

_VALID_PAYLOAD = {
    "subject": "Monte Carlo simulation tool for RIAs",
    "time_window": "1y",
    "focus_areas": "pricing, competition",
    "include_public_market": False,
    "tickers": "",
}


def _clear_jobs():
    from app.database import create_db_and_tables, engine
    from app.models import Job

    create_db_and_tables()  # idempotent; ensures tables exist before we query
    with Session(engine) as session:
        jobs = session.exec(select(Job)).all()
        for j in jobs:
            session.delete(j)
        session.commit()


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _clear_jobs()

    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c

    _clear_jobs()


def test_create_job_creates_payload_json(auth_client, tmp_path):
    """AC1: payload.json written to run dir containing all submitted fields."""
    resp = auth_client.post("/api/jobs", json=_VALID_PAYLOAD)
    assert resp.status_code == 200, resp.text

    job_id = resp.json()["id"]
    payload_file = tmp_path / "sa-runner" / "runs" / job_id / "payload.json"
    assert payload_file.exists(), "payload.json was not created in run dir"

    data = json.loads(payload_file.read_text())
    assert data["subject"] == _VALID_PAYLOAD["subject"]
    assert data["time_window"] == _VALID_PAYLOAD["time_window"]
    assert data["focus_areas"] == _VALID_PAYLOAD["focus_areas"]
    assert data["include_public_market"] == _VALID_PAYLOAD["include_public_market"]
    assert "tickers" in data


def test_create_job_sets_status_running(auth_client):
    """AC2: jobs row exists with status=running after creation."""
    from app.database import engine
    from app.models import Job

    resp = auth_client.post("/api/jobs", json=_VALID_PAYLOAD)
    assert resp.status_code == 200, resp.text

    job_id = resp.json()["id"]
    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job is not None
    assert job.status == "running"


def test_create_job_requires_auth():
    """AC2: unauthenticated POST /api/jobs returns 401."""
    _clear_jobs()
    from app.main import app

    with TestClient(app) as c:
        resp = c.post("/api/jobs", json=_VALID_PAYLOAD)
    assert resp.status_code == 401


def test_create_job_409_while_running(auth_client, tmp_path):
    """AC3/AC4: second POST while first job is running returns 409 with
    human-readable message; no second Job row or Run Directory is created."""
    from app.database import engine
    from app.models import Job

    resp1 = auth_client.post("/api/jobs", json=_VALID_PAYLOAD)
    assert resp1.status_code == 200, resp1.text

    resp2 = auth_client.post(
        "/api/jobs", json={**_VALID_PAYLOAD, "subject": "Another job"}
    )
    assert resp2.status_code == 409

    detail = resp2.json().get("detail", "")
    assert len(detail) > 20, f"Expected a descriptive error message, got: {detail!r}"
    assert "already running" in detail.lower(), (
        f"Expected 'already running' in detail: {detail!r}"
    )

    # Exactly one Job row in the database after the rejected submission.
    with Session(engine) as session:
        all_jobs = session.exec(select(Job)).all()
    assert len(all_jobs) == 1, f"Expected exactly 1 Job row, found {len(all_jobs)}"

    # Exactly one Run Directory under the runs root after the rejected submission.
    runs_dir = tmp_path / "sa-runner" / "runs"
    if runs_dir.exists():
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1, (
            f"Expected 1 Run Directory, found {len(run_dirs)}: "
            f"{[d.name for d in run_dirs]}"
        )


def test_create_job_tickers_in_payload(auth_client, tmp_path):
    """AC1: tickers present in payload.json when include_public_market=True."""
    payload = {**_VALID_PAYLOAD, "include_public_market": True, "tickers": "AAPL,MSFT"}
    resp = auth_client.post("/api/jobs", json=payload)
    assert resp.status_code == 200, resp.text

    job_id = resp.json()["id"]
    payload_file = tmp_path / "sa-runner" / "runs" / job_id / "payload.json"
    data = json.loads(payload_file.read_text())
    assert data["tickers"] == "AAPL,MSFT"
    assert data["include_public_market"] is True
