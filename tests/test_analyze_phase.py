"""P3-T1: Worker — sequential Analyze Phase Step dispatch.

AC1 — current_phase is set to 'analyze' before Analyze Steps run; all 7
      Checkpoint Files exist in the Run Directory after a successful mock run.
AC2 — <RUN_DIR>/logs/analyze.log is created and contains stderr attributed to
      all 7 Analyze Steps (by source tag).
AC3 — If score_angles.py (Step 3) exits non-zero, the Job transitions to
      status=failed with a non-empty error_message, and Steps 4-7 produce no
      output in the Run Directory.
AC4 — Progress Lines matching [sections] [N/M] writing '<title>' cause the
      Job's sections-step status field to update, observable via
      GET /api/jobs/{id} as analyze_status.sections.
AC5 — An SSE line with an API-key-shaped token is delivered as [REDACTED]
      while the original text is preserved in analyze.log.
"""

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

import app.worker as worker_mod
from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import (
    _job_analyze_status,
    _job_queues,
    _parse_progress,
    _run_real_step,
    clear_mock_invocations,
    get_analyze_status,
    run_job,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _insert_job(job_id: str, run_dir: Path, status: str = "running") -> None:
    create_db_and_tables()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="analyze test",
                payload_json="{}",
                status=status,
                run_dir=str(run_dir),
            )
        )
        session.commit()


def _delete_job(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()


def _get_job(job_id: str) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def _run_mock(job_id: str, run_dir: Path, monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", str(skill_dir / "node"))
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    _job_queues.pop(job_id, None)
    _job_analyze_status.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC1 — current_phase='analyze' set before Steps run; all 7 Checkpoint Files
# ---------------------------------------------------------------------------


def test_analyze_phase_sets_current_phase(tmp_path, monkeypatch):
    """AC1: current_phase='analyze' is set before any Analyze Step executes."""
    phases_recorded: list[str] = []
    original_update = worker_mod._update_job

    def capturing_update(jid: str, **kwargs: object) -> None:
        if "current_phase" in kwargs and kwargs["current_phase"] is not None:
            phases_recorded.append(str(kwargs["current_phase"]))
        original_update(jid, **kwargs)

    job_id = "p3t1-ac1-phase"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)
    monkeypatch.setattr(worker_mod, "_update_job", capturing_update)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)
        assert "analyze" in phases_recorded, (
            f"current_phase='analyze' was never set; recorded: {phases_recorded}"
        )
        # gather must precede analyze in the phase sequence
        gather_idx = phases_recorded.index("gather")
        analyze_idx = phases_recorded.index("analyze")
        assert gather_idx < analyze_idx, (
            f"'analyze' must be set after 'gather'; order: {phases_recorded}"
        )
        # analyze must precede render
        render_idx = phases_recorded.index("render")
        assert analyze_idx < render_idx, (
            f"'analyze' must be set before 'render'; order: {phases_recorded}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_analyze_phase_checkpoint_files_exist(tmp_path, monkeypatch):
    """AC1: All 7 Checkpoint File categories exist in Run Directory after mock run."""
    job_id = "p3t1-ac1-files"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"

        # Checkpoint Files from each Analyze Step
        assert (run_dir / "evidence.json").exists(), "evidence.json missing"
        assert (run_dir / "themes.json").exists(), "themes.json missing"
        assert (run_dir / "scores.json").exists(), "scores.json missing"
        assert (run_dir / "executive.json").exists(), "executive.json missing"
        assert (run_dir / "synthesis.json").exists(), "synthesis.json missing"

        # write_sections.py — sections directory with at least one file
        sections_dir = run_dir / "sections"
        assert sections_dir.is_dir(), "sections/ directory missing"
        assert any(sections_dir.iterdir()), "sections/ directory is empty"

        # generate_charts.py — charts directory with at least one file
        charts_dir = run_dir / "charts"
        assert charts_dir.is_dir(), "charts/ directory missing"
        assert any(charts_dir.iterdir()), "charts/ directory is empty"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — analyze.log contains stderr from all 7 Analyze Steps
# ---------------------------------------------------------------------------


def test_analyze_log_has_all_step_tags(tmp_path, monkeypatch):
    """AC2: analyze.log exists and contains lines from all 7 Analyze Steps."""
    job_id = "p3t1-ac2-log"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        log_path = run_dir / "logs" / "analyze.log"
        assert log_path.exists(), "analyze.log was not created"
        content = log_path.read_text(encoding="utf-8")
        assert content.strip(), "analyze.log is empty"

        for tag in (
            "extract",
            "cluster",
            "score",
            "sections",
            "executive",
            "charts",
            "assemble",
        ):
            assert f"[{tag}]" in content, (
                f"analyze.log missing output from Analyze Step '[{tag}]'"
            )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC3 — score_angles.py failure stops pipeline; Steps 4-7 produce no output
# ---------------------------------------------------------------------------


def test_score_angles_failure_fails_job_and_skips_remaining_steps(
    tmp_path, monkeypatch
):
    """AC3: score_angles.py exit non-zero → Job=failed, Steps 4-7 no output."""
    original_mock = worker_mod._run_mock_step

    async def failing_mock(
        cmd, log_path, queue, api_keys, job_id="", cwd=None, log_original=False
    ):
        script = Path(cmd[1]).name if len(cmd) > 1 else ""
        if script == "score_angles.py":
            return 1
        return await original_mock(
            cmd, log_path, queue, api_keys, job_id, cwd, log_original
        )

    job_id = "p3t1-ac3-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    monkeypatch.setattr(worker_mod, "_run_mock_step", failing_mock)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        job = _get_job(job_id)
        assert job.status == "failed", f"Expected 'failed', got '{job.status}'"
        assert job.error_message, "error_message must be non-empty after step failure"
        assert "score_angles.py" in job.error_message, (
            f"error_message should name the failing script; got: {job.error_message!r}"
        )

        # Steps 4-7 must produce no output
        assert not (run_dir / "sections").exists() or not any(
            (run_dir / "sections").iterdir()
        ), "sections/ must not exist after score_angles.py failure"
        assert not (run_dir / "executive.json").exists(), (
            "executive.json must not exist after score_angles.py failure"
        )
        assert not (run_dir / "charts").exists() or not any(
            (run_dir / "charts").iterdir()
        ), "charts/ must not exist after score_angles.py failure"
        assert not (run_dir / "synthesis.json").exists(), (
            "synthesis.json must not exist after score_angles.py failure"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — sections Progress Lines update analyze_status; observable via API
# ---------------------------------------------------------------------------


def test_parse_progress_sections_updates_analyze_status():
    """AC4: _parse_progress parses [sections] lines into _job_analyze_status."""
    job_id = "p3t1-ac4-parse"
    _job_analyze_status.pop(job_id, None)

    _parse_progress(job_id, "[sections] [3/12] writing 'Competitive landscape'")

    status = get_analyze_status(job_id)
    assert "sections" in status, f"sections key missing from analyze_status: {status}"
    assert status["sections"]["step"] == 3
    assert status["sections"]["total"] == 12
    assert "Competitive landscape" in status["sections"]["text"]

    _job_analyze_status.pop(job_id, None)


def test_sections_status_observable_via_api(tmp_path, monkeypatch):
    """AC4: GET /api/jobs/{id} returns analyze_status.sections after mock run."""
    import app.keys as keys_mod

    keys_mod._fernet = None

    job_id = "p3t1-ac4-api"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        from app.main import app

        with TestClient(app) as client:
            client.post("/auth/login", json={"password": "test-password"})
            resp = client.get(f"/api/jobs/{job_id}")

        assert resp.status_code == 200, (
            f"GET /api/jobs/{job_id} returned {resp.status_code}"
        )
        data = resp.json()

        assert "analyze_status" in data, "analyze_status key missing from response"
        analyze_status = data["analyze_status"]
        assert "sections" in analyze_status, (
            f"sections key missing from analyze_status: {analyze_status}"
        )
        sections = analyze_status["sections"]
        assert "step" in sections and "total" in sections and "text" in sections, (
            f"sections status missing fields: {sections}"
        )
        # The mock emits the last section title; text must be non-empty
        assert sections["text"], "sections.text must be non-empty after mock run"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
        _job_analyze_status.pop(job_id, None)


def test_analyze_status_returned_by_api_even_when_empty(tmp_path):
    """AC4: GET /api/jobs/{id} always includes analyze_status key (empty dict if not started)."""
    import app.keys as keys_mod

    keys_mod._fernet = None

    job_id = "p3t1-ac4-empty"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    _insert_job(job_id, run_dir, status="running")

    try:
        from app.main import app

        with TestClient(app) as client:
            client.post("/auth/login", json={"password": "test-password"})
            resp = client.get(f"/api/jobs/{job_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert "analyze_status" in data, "analyze_status key missing for running job"
        assert isinstance(data["analyze_status"], dict)
    finally:
        _delete_job(job_id)


# ---------------------------------------------------------------------------
# AC5 — SSE gets [REDACTED]; analyze.log retains original text
# ---------------------------------------------------------------------------


def test_analyze_log_retains_original_key_sse_gets_redacted(tmp_path):
    """AC5: log_original=True means analyze.log has the raw key; SSE has [REDACTED]."""
    fake_key = "sk-ant-ANALYZE-SECRET-12345678901234"

    leaky_script = tmp_path / "leaky_analyze.py"
    leaky_script.write_text(
        f"import sys\nprint('[extract] key={fake_key}', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )

    log_path = tmp_path / "analyze.log"

    async def run_step() -> list[str]:
        q: asyncio.Queue = asyncio.Queue()
        await _run_real_step(
            cmd=[sys.executable, str(leaky_script)],
            log_path=log_path,
            queue=q,
            api_keys={"anthropic": fake_key},
            job_id="p3t1-ac5",
            log_original=True,
        )
        lines: list[str] = []
        while not q.empty():
            lines.append(q.get_nowait())
        return lines

    sse_lines = asyncio.run(run_step())

    # SSE queue must have [REDACTED], not the raw key
    for line in sse_lines:
        assert fake_key not in line, f"Raw API key leaked to SSE stream: {line!r}"
    assert any("[REDACTED]" in ln for ln in sse_lines), (
        "Expected [REDACTED] in SSE stream"
    )

    # analyze.log must contain the original unredacted key
    log_content = log_path.read_text(encoding="utf-8")
    assert fake_key in log_content, (
        "analyze.log must retain original (unredacted) key text when log_original=True"
    )


def test_analyze_log_redaction_via_regex_pattern(tmp_path):
    """AC5: Regex-matched API-key patterns (sk- prefix) also get [REDACTED] in SSE."""
    # Use a key not passed in api_keys dict so only regex redaction fires
    key_pattern = "sk-plx-REGEXTEST99XYZABCDEFGH12345678"

    script = tmp_path / "regex_key.py"
    script.write_text(
        "import sys\n"
        f"print('[score] token={key_pattern}', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )

    log_path = tmp_path / "analyze_regex.log"

    async def run_step() -> list[str]:
        q: asyncio.Queue = asyncio.Queue()
        await _run_real_step(
            cmd=[sys.executable, str(script)],
            log_path=log_path,
            queue=q,
            api_keys={},  # no literal-match keys
            job_id="p3t1-ac5-regex",
            log_original=True,
        )
        lines: list[str] = []
        while not q.empty():
            lines.append(q.get_nowait())
        return lines

    sse_lines = asyncio.run(run_step())

    for line in sse_lines:
        assert key_pattern not in line, f"Regex key pattern leaked to SSE: {line!r}"
    assert any("[REDACTED]" in ln for ln in sse_lines), (
        "Expected [REDACTED] in SSE stream for regex-matched key"
    )

    # analyze.log retains the original token
    log_content = log_path.read_text(encoding="utf-8")
    assert key_pattern in log_content, (
        "analyze.log must retain regex-matched key when log_original=True"
    )
