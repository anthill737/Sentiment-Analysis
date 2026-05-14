"""
Playwright E2E tests for P4-T7: Render phase failure.

AC4 — A Playwright test where the Render phase is configured to fail (report.docx
      fixture missing from SA_RUNNER_FIXTURES_DIR) asserts the Dashboard View shows
      `failed` status with a non-empty error message.
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
_SECRET = "e2e-render-fail-secret-32bytesxx"
_PROJECT_ROOT = Path(__file__).parents[2]
_CANONICAL_FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "skill_outputs"

# 800ms per step matches the canonical Playwright config; render fails at step 1
# of Phase 4 so the total wall-clock time is approximately:
#   plan(800ms) + gather(800ms) + analyze(7×800ms=5.6s) + instant render fail ≈ 7.2s
_STEP_DELAY_MS = "800"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_render_fail_fixtures_dir() -> str:
    """Copy all canonical fixtures to a temp dir, omitting report.docx.

    The mock seam returns exit code 1 when render_report.js is invoked and
    the output fixture is missing and its extension is not .json, causing the
    Job to transition to status=failed.
    """
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_pw_render_fail_fix_")
    for src in _CANONICAL_FIXTURES_DIR.iterdir():
        if src.is_file() and src.name != "report.docx":
            shutil.copy2(str(src), tmpdir)
    for subdir_name in ("charts", "sections"):
        src_dir = _CANONICAL_FIXTURES_DIR / subdir_name
        if src_dir.is_dir():
            dst_dir = Path(tmpdir) / subdir_name
            dst_dir.mkdir(exist_ok=True)
            for f in src_dir.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(dst_dir / f.name))
    return tmpdir


@pytest.fixture(scope="module")
def render_fail_server():
    """Live uvicorn server in Mock Pipeline Mode with report.docx fixture removed."""
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="sa_runner_pw_render_fail_srv_")
    fixtures_dir = _make_render_fail_fixtures_dir()

    env = {
        **os.environ,
        "APP_PASSWORD": _PASSWORD,
        "SESSION_SECRET": _SECRET,
        "SA_RUNNER_MASTER_KEY_PATH": str(Path(tmpdir) / "master.key"),
        "LOCALAPPDATA": tmpdir,
        "SA_RUNNER_MOCK_PIPELINE": "1",
        "SA_RUNNER_MOCK_STEP_DELAY_MS": _STEP_DELAY_MS,
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
            import httpx

            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Render-fail server did not start within 15s on port {port}")

    yield {"base_url": base_url}

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def authed_page_render_fail(page: Page, render_fail_server: dict):
    base_url = render_fail_server["base_url"]
    page.goto(f"{base_url}/")
    page.wait_for_selector("input[type='password']", timeout=5000)
    page.fill("input[type='password']", _PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_selector("form", timeout=5000)
    return page


def _wait_for_idle(page: Page, base_url: str, timeout_s: float = 60) -> None:
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
        time.sleep(0.5)
    pytest.fail(f"Server did not become idle within {timeout_s}s")


def test_render_failure_shows_failed_status_and_error_message(
    authed_page_render_fail: Page, render_fail_server: dict
):
    """AC4: Render failure → Dashboard View shows failed status with non-empty error message."""
    page = authed_page_render_fail
    base_url = render_fail_server["base_url"]

    _wait_for_idle(page, base_url)

    # Submit a new job via the form
    page.goto(f"{base_url}/")
    page.wait_for_selector("form", timeout=5000)
    page.fill("textarea#subject", "P4-T7 AC4 Render Failure Test")
    page.click("button[type='submit']")
    page.wait_for_url("**/jobs/**", timeout=10_000)

    # Wait for the job-level error-message div to become visible.
    # Timing: plan(800ms) + gather(800ms) + analyze(7×800ms≈5.6s) + render fail ≈ 7.2s.
    # The frontend polls the job status every 2s, so add generous headroom.
    error_div = page.locator('[data-testid="error-message"]')
    expect(error_div).to_be_visible(timeout=60_000)

    error_text = error_div.inner_text().strip()
    assert error_text, "error-message div is visible but contains no text"

    # The error message must reference the render phase failure
    assert "render" in error_text.lower() or "report" in error_text.lower(), (
        f"Error message does not mention render or report: {error_text!r}"
    )

    # The Render Phase Card must also show the error indicator
    render_error = page.locator('[data-testid="render-error"]')
    expect(render_error).to_be_visible(timeout=5000)
