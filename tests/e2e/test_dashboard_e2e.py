"""
Playwright E2E tests for P1-T10: Live Dashboard view.

AC1 — Phase Timeline renders four labeled nodes; active node has a glow/pulse
      CSS class that completed/pending nodes do not have.
AC2 — During Gather, four Phase Cards (Perplexity, xAI, Trends, FMP) appear
      and update their step-count and angle text as Progress Lines arrive.
AC3 — Log Panel auto-scrolls to the most recently received SSE line; lines from
      different source tags have visually distinct colors.
AC4 — Each Phase Card's elapsed-time counter increments while the phase is
      active and stops after the phase completes.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

_PASSWORD = "test-password"
_SECRET = "e2e-dash-secret-32bytes-xxxxxxxx"
_PROJECT_ROOT = Path(__file__).parents[2]

# Step delay must be large enough that the gather-elapsed-perplexity element
# (which only appears after 1s of gather activity) is still visible when we
# read t2. With _STEP_DELAY_MS=6000, gather takes ~6s concurrently and the
# first gather line arrives ~2s in, giving a comfortable observation window.
_STEP_DELAY_MS = "6000"


# ---------------------------------------------------------------------------
# Live-server fixture (module scope — one server for all tests in this module)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_dash_e2e_")
    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_MOCK_PIPELINE": "1",
        "SA_RUNNER_MOCK_STEP_DELAY_MS": _STEP_DELAY_MS,
        "SA_RUNNER_PYTHON_EXE": sys.executable,
        "SA_RUNNER_NODE_EXE": str(Path(tmpdir) / "node"),
        "SA_RUNNER_WORD_NOT_NEEDED": str(Path(tmpdir) / "word_convert"),
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
            import httpx

            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Server failed to start on port {port} within 15s")

    yield base_url

    proc.terminate()
    proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Playwright page fixture — logs in and returns a ready Page
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_page(page: Page, live_server: str):
    page.goto(f"{live_server}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    # Wait for the new-job form to appear (post-login redirect)
    page.wait_for_selector("form", timeout=5000)
    return page


def _wait_for_idle(page: Page, live_server: str, timeout_s: float = 180) -> None:
    """Poll /api/jobs until no job has status='running'.

    Required because the single-job worker enforces one-at-a-time execution.
    Without this, back-to-back test job submissions get a 409 while the
    previous mock pipeline is still completing.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = page.request.get(f"{live_server}/api/jobs")
            if resp.ok:
                jobs = resp.json()
                if not any(j.get("status") == "running" for j in jobs):
                    return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail(f"Server did not become idle within {timeout_s}s")


def _create_job(page: Page, live_server: str) -> str:
    """Submit the new-job form and return the job ID extracted from the URL."""
    # Wait for any previously-running job to finish before submitting a new one.
    # The backend enforces single-job execution; without this, concurrent tests
    # in the same module get 409 responses and the form never navigates away.
    _wait_for_idle(page, live_server)

    page.goto(f"{live_server}/")
    page.wait_for_selector("form", timeout=5000)
    # Fill the subject field — it is a <textarea id="subject">, not an <input>
    page.fill("textarea#subject", "Dashboard E2E Test Subject")
    page.click("button[type='submit']")
    # Wait until the URL changes to /jobs/<id>
    page.wait_for_url("**/jobs/**", timeout=8000)
    job_id = page.url.split("/jobs/")[1]
    return job_id


# ---------------------------------------------------------------------------
# AC1 — Phase Timeline: four labeled nodes, active node has animate-pulse
# ---------------------------------------------------------------------------


def test_phase_timeline_four_nodes_and_active_glow(authed_page: Page, live_server: str):
    """AC1: Timeline renders 4 nodes; active node has animate-pulse class."""
    page = authed_page
    _create_job(page, live_server)

    # Wait for the Phase Timeline to render
    timeline = page.locator('[data-testid="phase-timeline"]')
    expect(timeline).to_be_visible(timeout=6000)

    # All four phase nodes must be present
    for phase in ("plan", "gather", "analyze", "render"):
        node = page.locator(f'[data-testid="phase-node-{phase}"]')
        expect(node).to_be_visible(timeout=4000)

    # Exactly one node must be active (data-phase-state="active") at a time
    active_nodes = page.locator('[data-phase-state="active"]')
    expect(active_nodes).to_have_count(1, timeout=6000)

    # The active node's inner div must carry the Tailwind animate-pulse class
    active_inner = active_nodes.locator("> div").first
    classes = active_inner.get_attribute("class") or ""
    assert "animate-pulse" in classes, (
        f"Active phase node inner div is missing 'animate-pulse'. Classes: {classes}"
    )

    # The other nodes must NOT have animate-pulse
    for phase in ("plan", "gather", "analyze", "render"):
        node = page.locator(f'[data-testid="phase-node-{phase}"]')
        state = node.get_attribute("data-phase-state")
        if state != "active":
            inner = node.locator("> div").first
            c = inner.get_attribute("class") or ""
            assert "animate-pulse" not in c, (
                f"Non-active node '{phase}' (state={state}) has 'animate-pulse'. Classes: {c}"
            )


# ---------------------------------------------------------------------------
# AC2 — Gather Phase Cards update with step/angle text
# ---------------------------------------------------------------------------


def test_gather_phase_cards_appear_and_update(authed_page: Page, live_server: str):
    """AC2: Four gather cards appear; step-count and angle update via SSE."""
    page = authed_page
    _create_job(page, live_server)

    # Wait for the gather phase to become active
    gather_node = page.locator('[data-testid="phase-node-gather"]')
    expect(gather_node).to_have_attribute("data-phase-state", "active", timeout=15000)

    # All four source cards must be present
    for source in ("perplexity", "xai", "trends", "fmp"):
        card = page.locator(f'[data-testid="gather-card-{source}"]')
        expect(card).to_be_visible(timeout=6000)

    # Wait for at least one source card to show a step indicator (SSE line arrived)
    # The mock emits [source] [1/3] angle=market-sizing, which should populate the card.
    step_el = page.locator('[data-testid="gather-step-perplexity"]').first
    expect(step_el).to_be_visible(timeout=10000)

    # Capture initial step text
    initial_step = step_el.inner_text()

    # Wait for the step to advance (mock emits 3 lines per source with delay between)
    page.wait_for_function(
        f"""() => {{
            const el = document.querySelector('[data-testid="gather-step-perplexity"]');
            return el && el.textContent !== {repr(initial_step)};
        }}""",
        timeout=int(_STEP_DELAY_MS) * 2 + 2000,
    )

    updated_step = step_el.inner_text()
    assert updated_step != initial_step, (
        f"gather-step-perplexity did not update: still '{initial_step}'"
    )

    # Angle text must also be present
    angle_el = page.locator('[data-testid="gather-angle-perplexity"]').first
    expect(angle_el).to_be_visible(timeout=2000)
    assert angle_el.inner_text().strip() != "", "Angle text is empty"


# ---------------------------------------------------------------------------
# AC3 — Log Panel: auto-scroll and color distinction between sources
# ---------------------------------------------------------------------------


def test_log_panel_autoscroll_and_source_colors(authed_page: Page, live_server: str):
    """AC3: Log Panel scrolls to bottom automatically; different sources have different colors."""
    page = authed_page
    _create_job(page, live_server)

    # Wait for log panel and some lines
    log_panel = page.locator('[data-testid="log-panel"]')
    expect(log_panel).to_be_visible(timeout=6000)

    # Wait for gather phase so multiple sources have emitted lines
    gather_node = page.locator('[data-testid="phase-node-gather"]')
    expect(gather_node).to_have_attribute("data-phase-state", "active", timeout=15000)

    # Wait for lines from multiple sources
    page.wait_for_selector('[data-log-source="perplexity"]', timeout=12000)
    page.wait_for_selector('[data-log-source="xai"]', timeout=12000)

    # Verify auto-scroll: scrollTop should be at/near scrollHeight
    scroll_at_bottom = page.evaluate("""() => {
        const el = document.querySelector('[data-testid="log-panel"]');
        if (!el) return false;
        return el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    }""")
    assert scroll_at_bottom, "Log Panel is not scrolled to the bottom"

    # Verify color distinction: perplexity and xai lines must have different colors
    perplexity_color = page.evaluate("""() => {
        const el = document.querySelector('[data-log-source="perplexity"]');
        return el ? window.getComputedStyle(el).color : null;
    }""")
    xai_color = page.evaluate("""() => {
        const el = document.querySelector('[data-log-source="xai"]');
        return el ? window.getComputedStyle(el).color : null;
    }""")

    assert perplexity_color is not None, "No perplexity log line found"
    assert xai_color is not None, "No xai log line found"
    assert perplexity_color != xai_color, (
        f"perplexity and xai log lines have the same color: {perplexity_color}"
    )


# ---------------------------------------------------------------------------
# AC4 — Phase Card elapsed-time counter increments while active, stops when done
# ---------------------------------------------------------------------------


def test_phase_elapsed_timer_increments_then_stops(authed_page: Page, live_server: str):
    """AC4: Elapsed timer increments during active phase; stops after completion."""
    page = authed_page
    _create_job(page, live_server)

    # Wait for gather phase to become active so we have a long-enough window
    gather_node = page.locator('[data-testid="phase-node-gather"]')
    expect(gather_node).to_have_attribute("data-phase-state", "active", timeout=15000)

    # Wait for the elapsed timer on a gather card to appear (after ≥1 second)
    elapsed_el = page.locator('[data-testid="gather-elapsed-perplexity"]').first
    expect(elapsed_el).to_be_visible(timeout=8000)

    # Capture first reading
    t1 = elapsed_el.inner_text()

    # Wait for the timer to display a different value using JavaScript polling.
    # This avoids a fixed sleep that risks outlasting the gather phase window.
    page.wait_for_function(
        f"""() => {{
            const el = document.querySelector('[data-testid="gather-elapsed-perplexity"]');
            return el != null && el.textContent !== {repr(t1)};
        }}""",
        timeout=5000,
    )

    t2 = elapsed_el.inner_text(timeout=3000)
    assert t2 != t1, f"Elapsed timer did not increment: t1='{t1}', t2='{t2}'"

    # Wait for the job to complete (results-view appears when status=done)
    results_view = page.locator('[data-testid="results-view"]')
    expect(results_view).to_be_visible(timeout=int(_STEP_DELAY_MS) * 15 + 5000)

    # After completion the gather elapsed display on the Phase Timeline node
    # shows the final time and should no longer increment
    phase_elapsed = page.locator('[data-testid="phase-elapsed-gather"]')
    if phase_elapsed.is_visible():
        final1 = phase_elapsed.inner_text()
        page.wait_for_timeout(2000)
        final2 = phase_elapsed.inner_text()
        assert final1 == final2, (
            f"Elapsed timer kept incrementing after phase completed: '{final1}' → '{final2}'"
        )
