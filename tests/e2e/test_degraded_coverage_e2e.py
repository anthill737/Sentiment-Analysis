"""
Live-server E2E tests for Degraded Coverage:
  API surface (P1-T8) plus Playwright browser tests (P2-T7).

Playwright tests cover:
  AC1 — Degraded Coverage banner visible on the live Dashboard View before
        the job reaches 'done' (while still in Analyze/Render phase after
        Gather completes with an Error Envelope).
  AC2 — Banner persists on the Results View after the job reaches 'done'.
  AC3 — Banner appears on the History card for the affected job.
  AC4 — With 3 Source Error Envelopes the banner names all three failing sources.
  AC5 — With all four Source Error Envelopes the Job ends in status=failed and
        the Dashboard View shows a non-empty error-message (P5-T5 AC6).
"""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

_PASSWORD = "test-password"
_SECRET = "e2e-session-secret-32bytes-xxxxx"
_PROJECT_ROOT = Path(__file__).parents[2]
_CANONICAL_FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "skill_outputs"

# Per-step delay for the mock pipeline.  With 1 plan step + 4 parallel gather
# steps + 7 analyze steps + 2 render steps the total wall-clock time is
# roughly:  1.5 + 1.5 + 7×1.5 + 2×1.5 = 16.5 s.
# The banner (driven by API polling) appears ~5 s in — 11+ s before done —
# giving AC1 a comfortable window to assert "job still running".
_PW_STEP_DELAY_MS = "1500"


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ===========================================================================
# P1-T8 — API-level degraded-sources tests (httpx, no browser)
# ===========================================================================


@pytest.fixture(scope="module")
def live_degraded_server():
    """Start uvicorn with three seeded jobs covering all degraded_sources states."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_dc_e2e_")

    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_SKIP_WORKER": "1",
    }

    seed_code = f"""
import sys
sys.path.insert(0, {repr(str(_PROJECT_ROOT))})
import os
os.environ["LOCALAPPDATA"] = {repr(tmpdir)}
os.environ["SA_RUNNER_MASTER_KEY_PATH"] = {repr(str(Path(tmpdir) / "master.key"))}

from app.database import engine, create_db_and_tables
from app.models import Job
from sqlmodel import Session

create_db_and_tables()
with Session(engine) as session:
    session.add(Job(id="dc-degraded", subject="Degraded Job",
                    payload_json="{{}}", status="done",
                    run_dir={repr(tmpdir)},
                    degraded_sources='["perplexity", "xai"]'))
    session.add(Job(id="dc-allfail", subject="All Sources Failed",
                    payload_json="{{}}", status="failed",
                    run_dir={repr(tmpdir)},
                    error_message="All Gather sources failed: perplexity, xai, trends, fmp",
                    degraded_sources=None))
    session.add(Job(id="dc-clean", subject="Clean Job",
                    payload_json="{{}}", status="done",
                    run_dir={repr(tmpdir)},
                    degraded_sources=None))
    session.commit()
"""
    r = subprocess.run(
        [sys.executable, "-c", seed_code],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"Seed failed: {r.stderr}")

    proc = subprocess.Popen(
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
        pytest.fail(f"Server did not start within 15s on port {port}")

    yield {"base_url": base_url, "port": port}

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def authed_dc_client(live_degraded_server):
    client = httpx.Client(
        base_url=live_degraded_server["base_url"], follow_redirects=True
    )
    r = client.post("/auth/login", json={"password": _PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return client


def test_e2e_degraded_job_single_endpoint(authed_dc_client):
    """AC2: GET /api/jobs/{id} returns non-empty degraded_sources for a degraded job."""
    r = authed_dc_client.get("/api/jobs/dc-degraded")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "done"
    assert isinstance(data["degraded_sources"], list), (
        f"degraded_sources is not a list: {data['degraded_sources']!r}"
    )
    assert set(data["degraded_sources"]) == {"perplexity", "xai"}, (
        f"unexpected degraded_sources: {data['degraded_sources']}"
    )


def test_e2e_allfail_job_error_message_names_all_sources(authed_dc_client):
    """AC3: All-failed job has error_message naming all four sources; degraded_sources=[]."""
    r = authed_dc_client.get("/api/jobs/dc-allfail")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "failed"
    assert isinstance(data["degraded_sources"], list)
    assert data["degraded_sources"] == [], (
        f"allfail job should have empty degraded_sources: {data['degraded_sources']}"
    )
    msg = data.get("error_message") or ""
    for src in ("perplexity", "xai", "trends", "fmp"):
        assert src in msg, f"'{src}' missing from error_message: {msg!r}"


def test_e2e_clean_job_has_empty_degraded_sources(authed_dc_client):
    """AC4: Clean job (no errors) has empty degraded_sources list."""
    r = authed_dc_client.get("/api/jobs/dc-clean")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "done"
    assert isinstance(data["degraded_sources"], list)
    assert data["degraded_sources"] == [], (
        f"clean job should have empty degraded_sources: {data['degraded_sources']}"
    )


def test_e2e_jobs_list_includes_degraded_sources_for_all(authed_dc_client):
    """AC2: GET /api/jobs exposes degraded_sources on every entry in the list."""
    r = authed_dc_client.get("/api/jobs")
    assert r.status_code == 200, r.text
    jobs = {j["id"]: j for j in r.json()}

    assert "dc-degraded" in jobs
    dj = jobs["dc-degraded"]
    assert isinstance(dj["degraded_sources"], list), (
        f"degraded_sources not a list in job list: {dj['degraded_sources']!r}"
    )
    assert set(dj["degraded_sources"]) == {"perplexity", "xai"}

    assert "dc-allfail" in jobs
    assert jobs["dc-allfail"]["degraded_sources"] == [], (
        f"allfail in list: {jobs['dc-allfail']['degraded_sources']}"
    )

    assert "dc-clean" in jobs
    assert jobs["dc-clean"]["degraded_sources"] == [], (
        f"clean in list: {jobs['dc-clean']['degraded_sources']}"
    )


def test_e2e_unauthed_access_returns_401(live_degraded_server):
    """Auth gate is enforced on the jobs API."""
    r = httpx.get(f"{live_degraded_server['base_url']}/api/jobs")
    assert r.status_code == 401


def test_e2e_spa_routes_return_200(authed_dc_client):
    """SPA routes for job detail and history pages return HTTP 200."""
    for path in ("/jobs/dc-degraded", "/jobs/dc-allfail", "/history"):
        r = authed_dc_client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text[:200]}"


# ===========================================================================
# P2-T7 — Playwright browser tests for Degraded Coverage banner
# ===========================================================================


def _make_degraded_fixtures_dir(error_sources: list) -> str:
    """Copy canonical fixtures to a temp dir; replace specified sources with Error Envelopes."""
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_pw_fix_")
    for f in _CANONICAL_FIXTURES_DIR.iterdir():
        if f.is_file():
            shutil.copy2(str(f), tmpdir)
    _error_fixture = {
        "perplexity": "perplexity_error.json",
        "xai": "xai_error.json",
        "trends": "trends_error.json",
        "fmp": "fmp_error.json",
    }
    _output_fixture = {
        "perplexity": "perplexity.json",
        "xai": "xai.json",
        "trends": "trends.json",
        "fmp": "fmp.json",
    }
    for src in error_sources:
        shutil.copy2(
            str(_CANONICAL_FIXTURES_DIR / _error_fixture[src]),
            str(Path(tmpdir) / _output_fixture[src]),
        )
    return tmpdir


@contextlib.contextmanager
def _pw_dc_server_context(error_sources: list):
    """Yield a running uvicorn server in Mock Pipeline Mode with degraded fixture overrides."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_pw_dc_")
    fixtures_dir = _make_degraded_fixtures_dir(error_sources)
    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_MOCK_PIPELINE": "1",
        "SA_RUNNER_MOCK_STEP_DELAY_MS": _PW_STEP_DELAY_MS,
        "SA_RUNNER_FIXTURES_DIR": fixtures_dir,
        "SA_RUNNER_PYTHON_EXE": sys.executable,
        "SA_RUNNER_NODE_EXE": "node",
        "SA_RUNNER_WORD_NOT_NEEDED": "word_convert",
        "SA_RUNNER_SKILL_DIR": str(Path(tmpdir) / "skill"),
        "SA_RUNNER_SKIP_WORKER": "",  # allow the worker to run
    }
    proc = subprocess.Popen(
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
        pytest.fail(f"Mock pipeline server did not start within 15s on port {port}")
    try:
        yield {"base_url": base_url}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def pw_server_1_fail():
    """Live server where only perplexity produces an Error Envelope."""
    with _pw_dc_server_context(["perplexity"]) as server:
        yield server


@pytest.fixture(scope="module")
def pw_server_3_fail():
    """Live server where perplexity, xai, and trends produce Error Envelopes."""
    with _pw_dc_server_context(["perplexity", "xai", "trends"]) as server:
        yield server


@pytest.fixture(scope="module")
def pw_server_all_fail():
    """Live server where all four Sources produce Error Envelopes."""
    with _pw_dc_server_context(["perplexity", "xai", "trends", "fmp"]) as server:
        yield server


# ---------------------------------------------------------------------------
# Page-level helpers
# ---------------------------------------------------------------------------


def _dc_wait_for_idle(page: Page, base_url: str, timeout_s: float = 120) -> None:
    """Poll /api/jobs until no job has status='running'."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = page.request.get(f"{base_url}/api/jobs")
            if resp.ok:
                jobs = resp.json()
                if not any(j.get("status") == "running" for j in jobs):
                    return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail(f"Server did not become idle within {timeout_s}s")


def _dc_create_job(
    page: Page,
    base_url: str,
    subject: str = "Degraded Coverage E2E Test",
) -> str:
    """Wait for idle, submit the new-job form, return the job ID."""
    _dc_wait_for_idle(page, base_url)
    page.goto(f"{base_url}/")
    page.wait_for_selector("form", timeout=5000)
    page.fill("textarea#subject", subject)
    page.click("button[type='submit']")
    page.wait_for_url("**/jobs/**", timeout=8000)
    return page.url.split("/jobs/")[1]


# ---------------------------------------------------------------------------
# Per-test authenticated page fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_page_1_fail(page: Page, pw_server_1_fail: dict):
    page.goto(f"{pw_server_1_fail['base_url']}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


@pytest.fixture
def authed_page_3_fail(page: Page, pw_server_3_fail: dict):
    page.goto(f"{pw_server_3_fail['base_url']}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


@pytest.fixture
def authed_page_all_fail(page: Page, pw_server_all_fail: dict):
    page.goto(f"{pw_server_all_fail['base_url']}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


# ---------------------------------------------------------------------------
# Timing constants derived from step delay
# ---------------------------------------------------------------------------

# Generous upper bound for a full job run (all phases) plus headroom.
# 14 steps × 1500 ms + 8 s buffer ≈ 29 s.
_JOB_DONE_TIMEOUT_MS = int(_PW_STEP_DELAY_MS) * 14 + 8000

# Upper bound for the banner to appear after gather completes.
# gather (~1.5 s) + 2 poll cycles (4 s) + buffer → 25 s is very generous.
_BANNER_TIMEOUT_MS = 25000

# All-fail gather: plan(1500ms) + gather(1500ms parallel) + failure detection + headroom.
# The job fails right after gather so this is much shorter than a full run.
_ALL_FAIL_TIMEOUT_MS = int(_PW_STEP_DELAY_MS) * 3 + 10000


# ---------------------------------------------------------------------------
# AC1 + AC2 — Banner appears while running; persists on Results View
# ---------------------------------------------------------------------------


def test_pw_1_fail_banner_before_done_and_on_results_view(
    authed_page_1_fail: Page, pw_server_1_fail: dict
):
    """AC1 + AC2: Banner visible on live dashboard before done, persists on results view."""
    page = authed_page_1_fail
    base_url = pw_server_1_fail["base_url"]
    _dc_create_job(page, base_url)

    banner = page.locator('[data-testid="degraded-coverage-banner"]')
    results_view = page.locator('[data-testid="results-view"]')

    # AC1: banner must appear while the job is still running (results-view absent).
    expect(banner).to_be_visible(timeout=_BANNER_TIMEOUT_MS)
    assert not results_view.is_visible(), (
        "Results view was already visible when the degraded-coverage banner first appeared; "
        "the banner did not surface during the running phase."
    )
    banner_text = banner.inner_text().lower()
    assert "perplexity" in banner_text, (
        f"Banner text does not mention 'perplexity': {banner.inner_text()!r}"
    )

    # AC2: wait for job to finish; banner must still be visible on the results view.
    expect(results_view).to_be_visible(timeout=_JOB_DONE_TIMEOUT_MS)
    expect(banner).to_be_visible(timeout=2000)
    assert "perplexity" in banner.inner_text().lower(), (
        f"Banner missing 'perplexity' after job done: {banner.inner_text()!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — Banner appears on the History card
# ---------------------------------------------------------------------------


def test_pw_1_fail_banner_on_history_card(
    authed_page_1_fail: Page, pw_server_1_fail: dict
):
    """AC3: History card for a degraded job shows the Degraded Coverage banner."""
    page = authed_page_1_fail
    base_url = pw_server_1_fail["base_url"]
    job_id = _dc_create_job(page, base_url)

    # Wait for the job to complete so it appears in history as a finished card.
    results_view = page.locator('[data-testid="results-view"]')
    expect(results_view).to_be_visible(timeout=_JOB_DONE_TIMEOUT_MS)

    # Navigate to /history and find the job's card.
    page.goto(f"{base_url}/history")
    history_card = page.locator(f'[data-testid="history-card-{job_id}"]')
    expect(history_card).to_be_visible(timeout=6000)

    # The Degraded Coverage banner must appear inside the card.
    card_banner = history_card.locator('[data-testid="degraded-coverage-banner"]')
    expect(card_banner).to_be_visible(timeout=4000)
    assert "perplexity" in card_banner.inner_text().lower(), (
        f"History card banner does not mention 'perplexity': {card_banner.inner_text()!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — Banner names all three failing sources when 3 sources fail
# ---------------------------------------------------------------------------


def test_pw_3_fail_banner_names_all_failing_sources(
    authed_page_3_fail: Page, pw_server_3_fail: dict
):
    """AC4: With 3 Error Envelopes the banner names all three failing sources."""
    page = authed_page_3_fail
    base_url = pw_server_3_fail["base_url"]
    _dc_create_job(page, base_url)

    results_view = page.locator('[data-testid="results-view"]')
    expect(results_view).to_be_visible(timeout=_JOB_DONE_TIMEOUT_MS)

    banner = page.locator('[data-testid="degraded-coverage-banner"]')
    expect(banner).to_be_visible(timeout=2000)
    banner_text = banner.inner_text().lower()
    for src in ("perplexity", "xai", "trends"):
        assert src in banner_text, (
            f"Banner does not mention '{src}' among failing sources. "
            f"Full banner text: {banner.inner_text()!r}"
        )


# ---------------------------------------------------------------------------
# AC5 (P5-T5 AC6) — All four Source failures → Job fails; Dashboard shows error
# ---------------------------------------------------------------------------


def test_pw_all_fail_dashboard_shows_error_message(
    authed_page_all_fail: Page, pw_server_all_fail: dict
):
    """AC5 (P5-T5 AC6): With all four Source Error Envelopes the Job ends in
    status=failed and the Dashboard View shows a non-empty error-message element
    naming the failed sources."""
    page = authed_page_all_fail
    base_url = pw_server_all_fail["base_url"]
    _dc_create_job(page, base_url)

    # Wait for the job-level error-message element to become visible.
    # The all-fail path is faster than a full run (job fails right after Gather).
    error_div = page.locator('[data-testid="error-message"]')
    expect(error_div).to_be_visible(timeout=_ALL_FAIL_TIMEOUT_MS)

    error_text = error_div.inner_text().strip()
    assert error_text, "error-message element is visible but contains no text"

    # The error must mention all four failed sources (the worker sets
    # error_message = "All Gather sources failed: perplexity, xai, trends, fmp").
    for src in ("perplexity", "xai", "trends", "fmp"):
        assert src in error_text.lower(), (
            f"Error message does not mention source '{src}': {error_text!r}"
        )
