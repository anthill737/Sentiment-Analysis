"""
Playwright E2E tests for P5-T5: History View navigation, rejected second Job
submission, and full-Gather-failure scenario.

AC2a — Clicking a done Job's History card navigates to /jobs/:id and renders
       the Results View (data-testid="results-view").
AC2b — Clicking a failed Job's History card navigates to /jobs/:id and renders
       the error view (data-testid="error-message").
AC3  — Submitting a new Job while one has status=running shows a visible
       rejection message containing 'already running' in the UI; the form does
       NOT navigate away and no second Run Directory is created.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

_PASSWORD = "test-password"
_SECRET = "e2e-hist-nav-secret-32bytes-xxxx"
_PROJECT_ROOT = Path(__file__).parents[2]
_FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "skill_outputs"

_DONE_JOB_ID = "hist-nav-done"
_FAILED_JOB_ID = "hist-nav-failed"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Module-scoped server with a done job and a failed job (AC2a + AC2b)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nav_server():
    """Live uvicorn server with one done job and one failed job pre-seeded."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_hist_nav_e2e_")

    done_run = Path(tmpdir) / "sa-runner" / "runs" / _DONE_JOB_ID
    failed_run = Path(tmpdir) / "sa-runner" / "runs" / _FAILED_JOB_ID
    done_run.mkdir(parents=True)
    failed_run.mkdir(parents=True)

    # Provide result files for the done job so the Results View can render
    for fname in ("executive.json", "scores.json", "report.docx", "report.pdf"):
        src = _FIXTURES_DIR / fname
        if src.exists():
            shutil.copy2(str(src), str(done_run / fname))

    seed_code = f"""
import sys
sys.path.insert(0, {repr(str(_PROJECT_ROOT))})
import os
os.environ["LOCALAPPDATA"] = {repr(tmpdir)}
os.environ["SA_RUNNER_MASTER_KEY_PATH"] = {repr(str(Path(tmpdir) / "master.key"))}

from app.database import engine, create_db_and_tables
from app.models import Job
from sqlmodel import Session
from datetime import datetime, timedelta

create_db_and_tables()
with Session(engine) as session:
    session.add(Job(
        id={repr(_DONE_JOB_ID)},
        subject="Done Research Job",
        payload_json="{{}}",
        status="done",
        run_dir={repr(str(done_run))},
        verdict="Worth Exploring",
        score=7.9,
        created_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    ))
    session.add(Job(
        id={repr(_FAILED_JOB_ID)},
        subject="Failed Research Job",
        payload_json="{{}}",
        status="failed",
        run_dir={repr(str(failed_run))},
        error_message="All Gather sources failed: perplexity, xai, trends, fmp",
        created_at=datetime.utcnow() - timedelta(hours=1),
    ))
    session.commit()
"""
    r = subprocess.run(
        [sys.executable, "-c", seed_code],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"DB seed failed:\n{r.stderr}")

    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_SKIP_WORKER": "1",
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
            import httpx

            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Server did not start within 15s on port {port}")

    yield {
        "base_url": base_url,
        "done_job_id": _DONE_JOB_ID,
        "failed_job_id": _FAILED_JOB_ID,
    }

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def authed_nav_page(page: Page, nav_server: dict):
    page.goto(f"{nav_server['base_url']}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


# ---------------------------------------------------------------------------
# Module-scoped server with SA_RUNNER_SKIP_WORKER=1 for AC3.
# Jobs created via POST /api/jobs stay in status=running indefinitely because
# the Worker is never started.  recover_interrupted_jobs() on startup clears
# any pre-existing running rows, so the running job must be created AFTER the
# server is up — which is handled in authed_running_page below.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def running_job_server():
    """Server with SA_RUNNER_SKIP_WORKER=1; submitted jobs stay 'running'."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_hist_run_e2e_")

    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_SKIP_WORKER": "1",
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
            import httpx

            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Server did not start within 15s on port {port}")

    yield {"base_url": base_url, "tmpdir": tmpdir}

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def authed_running_page(page: Page, running_job_server: dict):
    """Log in, submit the first job (stays running), navigate back to the form."""
    base_url = running_job_server["base_url"]

    page.goto(f"{base_url}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)

    # Submit the first job — it stays 'running' because SA_RUNNER_SKIP_WORKER=1
    page.fill("textarea#subject", "First Running Job")
    page.click("button[type='submit']")
    page.wait_for_url("**/jobs/**", timeout=8000)

    # Navigate back to the new-job form so the test can attempt a second submission
    page.goto(f"{base_url}/")
    page.wait_for_selector("form", timeout=5000)
    return page


# ---------------------------------------------------------------------------
# AC2a — Clicking a done History card opens the Results View
# ---------------------------------------------------------------------------


def test_clicking_done_history_card_opens_results_view(
    authed_nav_page: Page, nav_server: dict
):
    """AC2a: Clicking a done Job's History card navigates to /jobs/:id and renders
    the Results View (data-testid='results-view')."""
    page = authed_nav_page
    base_url = nav_server["base_url"]
    job_id = nav_server["done_job_id"]

    page.goto(f"{base_url}/history")
    page.wait_for_selector(f'[data-testid="history-card-{job_id}"]', timeout=6000)
    page.click(f'[data-testid="history-card-{job_id}"]')

    page.wait_for_url(f"**/jobs/{job_id}", timeout=6000)
    results_view = page.locator('[data-testid="results-view"]')
    expect(results_view).to_be_visible(timeout=8000)


# ---------------------------------------------------------------------------
# AC2b — Clicking a failed History card opens the error view
# ---------------------------------------------------------------------------


def test_clicking_failed_history_card_opens_error_view(
    authed_nav_page: Page, nav_server: dict
):
    """AC2b: Clicking a failed Job's History card navigates to /jobs/:id and renders
    the error-message element."""
    page = authed_nav_page
    base_url = nav_server["base_url"]
    job_id = nav_server["failed_job_id"]

    page.goto(f"{base_url}/history")
    page.wait_for_selector(f'[data-testid="history-card-{job_id}"]', timeout=6000)
    page.click(f'[data-testid="history-card-{job_id}"]')

    page.wait_for_url(f"**/jobs/{job_id}", timeout=6000)
    error_div = page.locator('[data-testid="error-message"]')
    expect(error_div).to_be_visible(timeout=8000)

    error_text = error_div.inner_text().strip()
    assert error_text, "error-message element is visible but contains no text"


# ---------------------------------------------------------------------------
# AC3 — UI shows rejection when second Job submitted while one is running
# ---------------------------------------------------------------------------


def test_ui_rejection_second_job_while_running(
    authed_running_page: Page, running_job_server: dict
):
    """AC3: Submitting a second Job while one has status=running shows a visible
    rejection message in the UI containing 'already running'; the form does NOT
    navigate to a /jobs/ route; no second Run Directory is created."""
    page = authed_running_page
    tmpdir = running_job_server["tmpdir"]

    # Count Run Directories before the rejected submission (should be exactly 1
    # from the first job submitted by authed_running_page).
    runs_dir = Path(tmpdir) / "sa-runner" / "runs"
    initial_count = (
        len([d for d in runs_dir.iterdir() if d.is_dir()]) if runs_dir.exists() else 0
    )

    page.fill("textarea#subject", "Rejected Second Job")
    page.click("button[type='submit']")

    # The error message must appear in the form (the red <p> element in NewJobForm)
    error_el = page.locator("p.text-red-300")
    expect(error_el).to_be_visible(timeout=6000)
    error_text = error_el.inner_text()
    assert "already running" in error_text.lower(), (
        f"Expected 'already running' in UI error text, got: {error_text!r}"
    )

    # The form must NOT have navigated to a /jobs/:id URL
    assert "/jobs/" not in page.url, (
        f"Form should not navigate on rejection, but URL is now: {page.url!r}"
    )

    # No second Run Directory should have been created
    final_count = (
        len([d for d in runs_dir.iterdir() if d.is_dir()]) if runs_dir.exists() else 0
    )
    assert final_count == initial_count, (
        f"Expected {initial_count} Run Director(y/ies) after rejection, "
        f"found {final_count}"
    )
