"""
Playwright E2E tests for P1-T11: Results view & History view.

AC1 — Results View renders a Verdict Pill, a bar chart with exactly six labeled
      bars each showing a numeric score, and at least one pursue-reason card and
      one pass-reason card.
AC2 — Clicking 'Download PDF' in the Results View delivers HTTP 200 with a
      non-zero Content-Length from the mock fixture.
AC3 — History view at /history lists jobs most-recent-first; a completed mock
      job appears at the top with correct subject, verdict, score, status, and
      timestamps visible on its card.
AC4 — History cards for completed jobs render download links that return HTTP
      200 with non-empty bodies when requested.
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
_SECRET = "e2e-results-secret-32bytes-xxxxx"
_PROJECT_ROOT = Path(__file__).parents[2]
_FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "skill_outputs"

_NEWER_JOB_ID = "results-newer"
_OLDER_JOB_ID = "results-older"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Module-scoped live server seeded with two completed jobs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def results_server():
    """Live uvicorn instance with two seeded done jobs at known run dirs."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_results_e2e_")

    # Create run directories with all required fixture files
    for job_id in (_NEWER_JOB_ID, _OLDER_JOB_ID):
        run_dir = Path(tmpdir) / "sa-runner" / "runs" / job_id
        run_dir.mkdir(parents=True)
        for fname in ("executive.json", "scores.json", "report.docx", "report.pdf"):
            src = _FIXTURES_DIR / fname
            if src.exists():
                shutil.copy2(str(src), str(run_dir / fname))

    newer_run = str(Path(tmpdir) / "sa-runner" / "runs" / _NEWER_JOB_ID)
    older_run = str(Path(tmpdir) / "sa-runner" / "runs" / _OLDER_JOB_ID)

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
        id={repr(_NEWER_JOB_ID)},
        subject="Monte Carlo Simulation Tool",
        payload_json="{{}}",
        status="done",
        run_dir={repr(newer_run)},
        verdict="Worth Exploring",
        score=7.9,
        created_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    ))
    session.add(Job(
        id={repr(_OLDER_JOB_ID)},
        subject="Old Research Subject",
        payload_json="{{}}",
        status="done",
        run_dir={repr(older_run)},
        verdict="Skip",
        score=4.2,
        created_at=datetime.utcnow() - timedelta(days=1),
        completed_at=datetime.utcnow() - timedelta(days=1),
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
        "newer_job_id": _NEWER_JOB_ID,
        "older_job_id": _OLDER_JOB_ID,
    }

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def authed_page(page: Page, results_server: dict):
    """Return a Playwright Page that is logged in and at the new-job form."""
    page.goto(f"{results_server['base_url']}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


# ---------------------------------------------------------------------------
# AC1 — Results View: Verdict Pill, six-bar chart, pursue/pass reason cards
# ---------------------------------------------------------------------------


def test_results_view_renders_verdict_chart_and_reasons(
    authed_page: Page, results_server: dict
):
    """AC1: Results View shows Verdict Pill, 6-bar chart, and pursue/pass reason cards."""
    page = authed_page
    job_id = results_server["newer_job_id"]

    page.goto(f"{results_server['base_url']}/jobs/{job_id}")
    page.wait_for_selector('[data-testid="results-view"]', timeout=8000)

    # Verdict Pill is present and shows the expected verdict
    verdict_pill = page.locator('[data-testid="verdict-pill"]')
    expect(verdict_pill).to_be_visible(timeout=4000)
    pill_text = verdict_pill.inner_text()
    assert "Worth Exploring" in pill_text, f"Verdict Pill text: {pill_text!r}"

    # Bar chart is present
    chart = page.locator('[data-testid="angle-score-chart"]')
    expect(chart).to_be_visible(timeout=4000)

    # Exactly six labeled bars, each with a numeric score
    bars = page.locator('[data-testid="score-bar"]')
    expect(bars).to_have_count(6, timeout=4000)

    # Each bar must display a numeric score
    for i in range(6):
        bar = bars.nth(i)
        score_el = bar.locator('[data-testid^="bar-score-"]')
        score_text = score_el.inner_text().strip()
        try:
            float(score_text)
        except ValueError:
            pytest.fail(f"Bar {i} score is not numeric: {score_text!r}")

    # At least one pursue-reason card
    pursue_card = page.locator('[data-testid="pursue-reason-0"]')
    expect(pursue_card).to_be_visible(timeout=2000)

    # At least one pass-reason card
    pass_card = page.locator('[data-testid="pass-reason-0"]')
    expect(pass_card).to_be_visible(timeout=2000)


# ---------------------------------------------------------------------------
# AC2 — Download PDF delivers HTTP 200 with non-zero Content-Length
# ---------------------------------------------------------------------------


def test_download_pdf_returns_nonempty_file(authed_page: Page, results_server: dict):
    """AC2: PDF download endpoint returns HTTP 200 with a non-empty body."""
    page = authed_page
    job_id = results_server["newer_job_id"]
    base_url = results_server["base_url"]

    # Navigate to Results View first so the page context has the auth cookie
    page.goto(f"{base_url}/jobs/{job_id}")
    page.wait_for_selector('[data-testid="results-view"]', timeout=8000)

    # Use page.request which shares the browser context's session cookie
    resp = page.request.get(f"{base_url}/api/jobs/{job_id}/download/pdf")
    assert resp.status == 200, f"PDF download returned HTTP {resp.status}"
    body = resp.body()
    assert len(body) > 0, "PDF download returned an empty body"


# ---------------------------------------------------------------------------
# AC3 — History view lists jobs most-recent-first with all required info
# ---------------------------------------------------------------------------


def test_history_view_most_recent_first_with_correct_info(
    authed_page: Page, results_server: dict
):
    """AC3: /history lists jobs most-recent-first; newer card shows subject, verdict,
    score, status badge, and timestamps."""
    page = authed_page
    newer_id = results_server["newer_job_id"]
    older_id = results_server["older_job_id"]

    page.goto(f"{results_server['base_url']}/history")
    page.wait_for_selector('[data-testid^="history-card-"]', timeout=6000)

    # Both cards present
    newer_card = page.locator(f'[data-testid="history-card-{newer_id}"]')
    older_card = page.locator(f'[data-testid="history-card-{older_id}"]')
    expect(newer_card).to_be_visible(timeout=4000)
    expect(older_card).to_be_visible(timeout=4000)

    # Most-recent-first: newer job is the first card in DOM order
    all_cards = page.locator('[data-testid^="history-card-"]')
    first_testid = all_cards.nth(0).get_attribute("data-testid")
    assert first_testid == f"history-card-{newer_id}", (
        f"Expected newer job first; got: {first_testid}"
    )

    card_text = newer_card.inner_text()

    # Subject text visible
    assert "Monte Carlo Simulation Tool" in card_text, (
        f"Subject not found in card text: {card_text!r}"
    )

    # Verdict visible (either via VerdictPill or plain text)
    assert "Worth Exploring" in card_text, (
        f"Verdict not found in card text: {card_text!r}"
    )

    # Score visible in the VerdictPill (seeded job has score=7.9)
    assert "7.9" in card_text, (
        f"Score '7.9' not found in history card text: {card_text!r}"
    )

    # Status badge visible
    assert "Done" in card_text, (
        f"'Done' status badge not found in card text: {card_text!r}"
    )

    # Created-at timestamp element is present and non-empty
    created_el = page.locator(f'[data-testid="card-created-at-{newer_id}"]')
    expect(created_el).to_be_visible(timeout=2000)
    assert created_el.inner_text().strip() != "Created: —", (
        "Created-at timestamp is empty/missing"
    )

    # Completed-at timestamp element is present
    completed_el = page.locator(f'[data-testid="card-completed-at-{newer_id}"]')
    expect(completed_el).to_be_visible(timeout=2000)


# ---------------------------------------------------------------------------
# AC4 — History card download links return HTTP 200 with non-empty bodies
# ---------------------------------------------------------------------------


def test_history_card_download_links_return_200(
    authed_page: Page, results_server: dict
):
    """AC4: Download links on history cards return HTTP 200 with non-empty bodies."""
    page = authed_page
    job_id = results_server["newer_job_id"]
    base_url = results_server["base_url"]

    page.goto(f"{results_server['base_url']}/history")
    page.wait_for_selector(f'[data-testid="history-card-{job_id}"]', timeout=6000)

    # PDF download link
    pdf_link = page.locator(f'[data-testid="history-download-pdf-{job_id}"]')
    expect(pdf_link).to_be_visible(timeout=2000)

    pdf_resp = page.request.get(f"{base_url}/api/jobs/{job_id}/download/pdf")
    assert pdf_resp.status == 200, f"PDF download returned HTTP {pdf_resp.status}"
    assert len(pdf_resp.body()) > 0, "PDF download returned empty body"

    # DOCX download link
    docx_link = page.locator(f'[data-testid="history-download-docx-{job_id}"]')
    expect(docx_link).to_be_visible(timeout=2000)

    docx_resp = page.request.get(f"{base_url}/api/jobs/{job_id}/download/docx")
    assert docx_resp.status == 200, f"DOCX download returned HTTP {docx_resp.status}"
    assert len(docx_resp.body()) > 0, "DOCX download returned empty body"
