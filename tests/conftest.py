import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Force-set test credentials before any test module (including app.main) is imported.
# Using direct assignment rather than setdefault so that OS-level or shell-exported
# values of APP_PASSWORD / SESSION_SECRET don't bleed into the test suite.
# load_dotenv(override=False) in app.main will NOT override these because they are
# already present in os.environ by the time app.main is first imported.
os.environ["APP_PASSWORD"] = "test-password"
os.environ["SESSION_SECRET"] = "test-session-secret-32-bytes-xxxx"
# Redirect master key to a throwaway temp dir so tests never touch the real
# %LOCALAPPDATA%\sa-runner\master.key and remain isolated from each other.
_test_key_dir = tempfile.mkdtemp(prefix="sa_runner_test_")
os.environ.setdefault(
    "SA_RUNNER_MASTER_KEY_PATH", str(Path(_test_key_dir) / "master.key")
)
# Prevent the Pipeline Worker from auto-launching when tests POST /api/jobs.
# Tests that need the worker call run_job() directly (see test_worker.py).
os.environ.setdefault("SA_RUNNER_SKIP_WORKER", "1")


@pytest.fixture(autouse=True)
def reset_sse_app_status():
    """Reset sse_starlette AppStatus singleton and asyncio running-loop between tests.

    Two issues are addressed here:

    1. AppStatus.should_exit_event: Each asyncio.run() creates a new event loop.
       AppStatus.should_exit_event is an anyio.Event created on the first loop; if
       not cleared, subsequent tests fail with 'bound to a different event loop'.

    2. asyncio running loop: playwright.sync_api calls asyncio._set_running_loop()
       after each sync Playwright operation, leaving the thread-local running loop
       set even after the operation completes. Subsequent asyncio.run() calls in
       non-Playwright tests then fail with 'cannot be called from a running event
       loop'. Fix: save the running-loop state before the test and restore it after,
       so Playwright tests still work (they re-set the loop on each operation) while
       non-Playwright tests start with a clean loop state.
    """
    saved_running_loop = asyncio._get_running_loop()

    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    asyncio._set_running_loop(saved_running_loop)
