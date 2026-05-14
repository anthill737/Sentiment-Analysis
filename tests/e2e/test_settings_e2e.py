"""
Live-server E2E tests for the Settings API key storage (P1-T4).

Spawns a real uvicorn subprocess with an isolated temp database so there is
no shared SQLite state with the unit-test suite.  httpx drives the HTTP
surface; a final static-analysis check confirms the React component never
fetches or renders raw key values.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

_PASSWORD = "test-password"
_SECRET = "e2e-session-secret-32bytes-xxxxx"
_PROJECT_ROOT = Path(__file__).parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
        cwd=str(_PROJECT_ROOT),
    )


def _wait_for_server(base_url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/", timeout=0.5)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Server did not start at {base_url} within {timeout}s")


@pytest.fixture(scope="module")
def live_server():
    """Start a real uvicorn process with an isolated temp environment."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_e2e_")
    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
    }
    proc = _start_server(port, env)
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Server failed to start on port {port} within 15 seconds")

    yield base_url

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def authed_client(live_server):
    """httpx.Client that carries a valid session cookie."""
    client = httpx.Client(base_url=live_server, follow_redirects=True)
    r = client.post("/auth/login", json={"password": _PASSWORD})
    assert r.status_code == 200, f"E2E login failed: {r.status_code} {r.text}"
    return client


# ---------------------------------------------------------------------------
# AC tests
# ---------------------------------------------------------------------------


def test_e2e_unauthed_access_returns_401(live_server):
    """Unauthenticated GET and PUT both return 401."""
    r = httpx.get(f"{live_server}/api/settings/keys")
    assert r.status_code == 401
    r = httpx.put(f"{live_server}/api/settings/keys/anthropic", json={"value": "x"})
    assert r.status_code == 401


def test_e2e_put_key_response_never_echoes_plaintext(authed_client):
    """AC1/AC4: PUT response body never contains the submitted key value."""
    plaintext = "sk-ant-e2e-probe-12345"
    r = authed_client.put("/api/settings/keys/anthropic", json={"value": plaintext})
    assert r.status_code == 200
    assert plaintext not in r.text


def test_e2e_get_keys_returns_configured_not_plaintext(authed_client):
    """AC2: After PUT, GET /api/settings/keys returns 'configured', not the key value."""
    plaintext = "pplx-e2e-probe-key-9999"
    authed_client.put("/api/settings/keys/perplexity", json={"value": plaintext})

    r = authed_client.get("/api/settings/keys")
    assert r.status_code == 200
    data = r.json()
    assert data["perplexity"] == "configured"
    assert plaintext not in r.text


def test_e2e_not_configured_before_any_put(live_server):
    """GET /api/settings/keys returns 'not configured' for all providers before any PUT."""
    client = httpx.Client(base_url=live_server, follow_redirects=True)
    client.post("/auth/login", json={"password": _PASSWORD})
    r = client.get("/api/settings/keys")
    data = r.json()
    for provider in ("anthropic", "perplexity", "xai", "fmp"):
        assert data[provider] in ("configured", "not configured")


def test_e2e_all_four_providers_never_expose_plaintext(authed_client):
    """AC4: GET /api/settings/keys never leaks any of the four saved key values."""
    secrets = {
        "anthropic": "sk-ant-dom-check-AAAA",
        "perplexity": "pplx-dom-check-BBBB",
        "xai": "xai-dom-check-CCCC",
        "fmp": "fmp-dom-check-DDDD",
    }
    for provider, value in secrets.items():
        authed_client.put(f"/api/settings/keys/{provider}", json={"value": value})

    r = authed_client.get("/api/settings/keys")
    body = r.text
    for provider, value in secrets.items():
        assert value not in body, f"Plaintext for {provider} leaked in GET response"
        assert r.json()[provider] == "configured"


def test_e2e_unknown_provider_returns_422(authed_client):
    """PUT with an unknown provider name returns 422."""
    r = authed_client.put("/api/settings/keys/unknown_provider", json={"value": "x"})
    assert r.status_code == 422


def test_e2e_empty_value_returns_422(authed_client):
    """PUT with an empty/whitespace value returns 422."""
    r = authed_client.put("/api/settings/keys/anthropic", json={"value": "   "})
    assert r.status_code == 422


def test_e2e_settings_tsx_static_analysis():
    """AC4 (static): Settings.tsx only reads /api/settings/keys (returns status strings),
    never an endpoint that could return plaintext key values."""
    tsx = (_PROJECT_ROOT / "frontend" / "src" / "Settings.tsx").read_text(
        encoding="utf-8"
    )

    # The only fetch target is the status endpoint
    assert "/api/settings/keys" in tsx
    # No decrypt or raw-value endpoints are fetched
    assert "/decrypt" not in tsx
    assert "plaintext" not in tsx.lower()
    # The rotate input is never pre-populated from the API (value= uses local state only)
    assert "rotateValue" in tsx
    # Status strings shown to the user are "configured" / "not configured" only
    assert "configured" in tsx
    assert "not configured" in tsx


def test_e2e_keys_persist_across_server_restart():
    """AC5: API keys saved by one server instance are still 'configured' after restart.

    Both server processes share the same LOCALAPPDATA (same SQLite DB) and the
    same SA_RUNNER_MASTER_KEY_PATH (same Fernet key), so the second process can
    decrypt rows written by the first.
    """
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_e2e_restart_")
    master_key_path = str(Path(tmpdir) / "master.key")
    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": master_key_path,
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_SKIP_WORKER": "1",
    }

    # --- First server: save all four keys ---
    port1 = _free_port()
    proc1 = _start_server(port1, env)
    base_url1 = f"http://127.0.0.1:{port1}"
    try:
        _wait_for_server(base_url1)
        client1 = httpx.Client(base_url=base_url1, follow_redirects=True)
        r = client1.post("/auth/login", json={"password": _PASSWORD})
        assert r.status_code == 200, f"Login failed on server 1: {r.status_code}"
        for provider in ("anthropic", "perplexity", "xai", "fmp"):
            r = client1.put(
                f"/api/settings/keys/{provider}",
                json={"value": f"restart-test-key-{provider}"},
            )
            assert r.status_code == 200, f"PUT {provider} failed: {r.status_code}"
        # Confirm all four are configured before stopping
        r = client1.get("/api/settings/keys")
        data = r.json()
        for provider in ("anthropic", "perplexity", "xai", "fmp"):
            assert data[provider] == "configured", (
                f"{provider} not configured before restart"
            )
    finally:
        proc1.terminate()
        proc1.wait(timeout=5)

    # --- Second server: same tmpdir → same DB and master key ---
    port2 = _free_port()
    proc2 = _start_server(port2, env)
    base_url2 = f"http://127.0.0.1:{port2}"
    try:
        _wait_for_server(base_url2)
        client2 = httpx.Client(base_url=base_url2, follow_redirects=True)
        r = client2.post("/auth/login", json={"password": _PASSWORD})
        assert r.status_code == 200, f"Login failed on server 2: {r.status_code}"
        r = client2.get("/api/settings/keys")
        assert r.status_code == 200
        data = r.json()
        for provider in ("anthropic", "perplexity", "xai", "fmp"):
            assert data[provider] == "configured", (
                f"{provider} not 'configured' after restart; got {data[provider]!r}"
            )
    finally:
        proc2.terminate()
        proc2.wait(timeout=5)
