import base64
import os
import struct
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

SESSION_COOKIE = "sa_session"


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _make_backdated_token(secret: str, days_ago: int) -> str:
    """Forge a valid-signature but time-expired session token using the known secret."""
    signer = TimestampSigner(secret)
    old_ts = int(time.time()) - days_ago * 24 * 3600
    timestamp_b64 = base64.b64encode(struct.pack(">I", old_ts))
    sep = b"."
    value_with_ts = b"authenticated" + sep + timestamp_b64
    signature = signer.get_signature(value_with_ts)
    return (value_with_ts + sep + signature).decode()


def test_login_correct_password_sets_cookie(client):
    """AC1: correct password sets an HTTP-only session cookie."""
    resp = client.post("/auth/login", json={"password": "test-password"})
    assert resp.status_code == 200
    assert SESSION_COOKIE in resp.cookies


def test_login_grants_api_access(client):
    """AC1: session cookie from login grants access to GET /api/jobs."""
    client.post("/auth/login", json={"password": "test-password"})
    resp = client.get("/api/jobs")
    assert resp.status_code == 200


def test_login_wrong_password_returns_error(client):
    """AC2: wrong password returns non-2xx and does not set a session cookie."""
    resp = client.post("/auth/login", json={"password": "hunter2"})
    assert resp.status_code >= 400
    assert SESSION_COOKIE not in resp.cookies


def test_unauthenticated_api_returns_401(client):
    """AC3: GET /api/jobs without a valid session cookie returns 401."""
    resp = client.get("/api/jobs")
    assert resp.status_code == 401


def test_expired_cookie_rejected(client):
    """AC4: cookie with a timestamp older than 30 days is rejected."""
    secret = os.environ["SESSION_SECRET"]
    old_token = _make_backdated_token(secret, days_ago=31)
    resp = client.get("/api/jobs", cookies={SESSION_COOKIE: old_token})
    assert resp.status_code == 401


def test_logout_invalidates_session(client):
    """AC5: POST /auth/logout clears the cookie so GET /api/jobs returns 401."""
    client.post("/auth/login", json={"password": "test-password"})
    assert client.get("/api/jobs").status_code == 200

    client.post("/auth/logout")
    resp = client.get("/api/jobs")
    assert resp.status_code == 401
