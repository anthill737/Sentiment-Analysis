"""Tests for the SSE Log Stream endpoint (P1-T7).

AC1 — Completed job: SSE replays all backlog lines then closes connection.
AC2 — Mid-run: backlog lines delivered first, then new live lines, no duplicates.
AC3 — Progress Line parsing updates per-source gather_status, visible via GET /api/jobs/{id}.
AC4 — API-key patterns are [REDACTED] in backlog, log files, and SSE stream.
"""

import asyncio
import sys

import httpx
from sqlmodel import Session

from app.auth import SESSION_COOKIE, get_session_secret, sign_session
from app.database import create_db_and_tables, engine
from app.main import app
from app.models import Job
from app.worker import (
    _job_backlogs,
    _job_gather_status,
    _job_terminal,
    _parse_progress,
    _redact_line,
    _run_real_step,
    get_gather_status,
    get_job_backlog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup():
    create_db_and_tables()


def _insert_job(job_id: str, status: str = "running") -> None:
    _setup()
    with Session(engine) as session:
        existing = session.get(Job, job_id)
        if existing:
            session.delete(existing)
            session.commit()
        session.add(
            Job(
                id=job_id,
                subject="stream-test",
                payload_json="{}",
                status=status,
                run_dir="/tmp",
            )
        )
        session.commit()


def _delete_job(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()


def _worker_cleanup(*job_ids: str) -> None:
    for jid in job_ids:
        _job_backlogs.pop(jid, None)
        _job_terminal.pop(jid, None)
        _job_gather_status.pop(jid, None)


def _auth_cookie() -> str:
    return sign_session(get_session_secret())


async def _collect_sse(job_id: str, timeout: float = 5.0) -> list[str]:
    """Stream /api/jobs/{id}/stream until the connection closes; return data lines."""
    transport = httpx.ASGITransport(app=app)
    events: list[str] = []
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=timeout
    ) as client:
        async with client.stream(
            "GET",
            f"/api/jobs/{job_id}/stream",
            cookies={SESSION_COOKIE: _auth_cookie()},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    content = line[6:]
                    if content:  # skip empty keep-alive data frames
                        events.append(content)
    return events


async def _get_job_json(job_id: str) -> dict:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/jobs/{job_id}",
            cookies={SESSION_COOKIE: _auth_cookie()},
        )
    return {"status_code": resp.status_code, "data": resp.json()}


# ---------------------------------------------------------------------------
# AC1 — Completed job replays backlog and closes
# ---------------------------------------------------------------------------


def test_sse_completed_job_replays_and_closes():
    """AC1: completed job streams all backlog lines and then the connection closes."""
    job_id = "t7-ac1-replay"
    _insert_job(job_id, status="done")
    _job_backlogs[job_id] = ["alpha line", "beta line", "gamma line"]
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == ["alpha line", "beta line", "gamma line"]
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_completed_job_empty_backlog_closes_immediately():
    """AC1: completed job with empty backlog still closes the connection cleanly."""
    job_id = "t7-ac1-empty"
    _insert_job(job_id, status="done")
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == []
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_failed_job_replays_and_closes():
    """AC1: failed job also replays backlog and closes."""
    job_id = "t7-ac1-failed"
    _insert_job(job_id, status="failed")
    _job_backlogs[job_id] = ["plan line", "error line"]
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == ["plan line", "error line"]
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_terminal_detected_via_db():
    """AC1: terminal is detected from DB status when in-memory flag is not set."""
    job_id = "t7-ac1-db-terminal"
    _insert_job(job_id, status="done")  # DB marks done
    _job_backlogs[job_id] = ["only line"]
    # Do NOT set _job_terminal[job_id] — must be detected from DB.

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == ["only line"]
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_404_for_unknown_job():
    """AC1: non-existent job returns 404 (not a hanging SSE connection)."""
    transport = httpx.ASGITransport(app=app)

    async def _check():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/jobs/no-such-job/stream",
                cookies={SESSION_COOKIE: _auth_cookie()},
            )
        return resp.status_code

    status = asyncio.run(_check())
    assert status == 404


# ---------------------------------------------------------------------------
# AC2 — Mid-run: backlog first, then live lines, no duplicates
# ---------------------------------------------------------------------------


def test_sse_midrun_backlog_then_live():
    """AC2: pre-existing backlog lines arrive first; new live lines arrive after."""
    job_id = "t7-ac2-midrun"
    _insert_job(job_id, status="running")
    _job_backlogs[job_id] = ["early-1", "early-2"]
    _job_terminal[job_id] = False

    try:
        received: list[str] = []

        async def _run():
            async def consumer():
                nonlocal received
                received = await _collect_sse(job_id)

            async def producer():
                await asyncio.sleep(0.25)
                _job_backlogs[job_id].append("live-1")
                await asyncio.sleep(0.25)
                _job_terminal[job_id] = True

            await asyncio.gather(consumer(), producer())

        asyncio.run(_run())

        assert received == ["early-1", "early-2", "live-1"]
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_no_duplicate_lines():
    """AC2: each backlog line appears exactly once in the SSE stream."""
    job_id = "t7-ac2-nodups"
    _insert_job(job_id, status="done")
    lines = [f"line-{i}" for i in range(6)]
    _job_backlogs[job_id] = lines[:]
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == lines, f"Got {received}"
        assert len(received) == len(set(received)), "Duplicate lines found"
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_sse_backlog_order_preserved():
    """AC2: lines are delivered in the exact order they were added to the backlog."""
    job_id = "t7-ac2-order"
    _insert_job(job_id, status="done")
    ordered = ["first", "second", "third", "fourth", "fifth"]
    _job_backlogs[job_id] = ordered[:]
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert received == ordered
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


# ---------------------------------------------------------------------------
# AC3 — Progress Line parsing updates per-source gather_status
# ---------------------------------------------------------------------------


def test_parse_progress_perplexity():
    """AC3: perplexity Progress Line parsed into gather_status correctly."""
    job_id = "t7-ac3-perplexity"
    _worker_cleanup(job_id)
    _parse_progress(job_id, "[perplexity] [3/9] angle=pricing-signals")

    try:
        status = get_gather_status(job_id)
        assert status["perplexity"] == {
            "step": 3,
            "total": 9,
            "angle": "pricing-signals",
        }
    finally:
        _worker_cleanup(job_id)


def test_parse_progress_all_four_sources():
    """AC3: all four Gather source Progress Lines are parsed correctly."""
    job_id = "t7-ac3-all"
    _worker_cleanup(job_id)
    cases = [
        ("[perplexity] [1/9] angle=demand", "perplexity", 1, 9, "demand"),
        ("[xai] [2/7] angle=competition", "xai", 2, 7, "competition"),
        ("[trends] [5/5] angle=timing", "trends", 5, 5, "timing"),
        ("[fmp] [3/4] angle=pricing", "fmp", 3, 4, "pricing"),
    ]
    try:
        for line, source, step, total, angle in cases:
            _parse_progress(job_id, line)
            gs = get_gather_status(job_id)
            assert gs[source] == {"step": step, "total": total, "angle": angle}
    finally:
        _worker_cleanup(job_id)


def test_parse_progress_non_matching_lines_ignored():
    """AC3: non-matching lines don't update gather_status."""
    job_id = "t7-ac3-nomatch"
    _worker_cleanup(job_id)
    _parse_progress(job_id, "[plan] mock: step complete")
    _parse_progress(job_id, "some random text without brackets")
    _parse_progress(job_id, "[perplexity] working without step format")

    try:
        assert get_gather_status(job_id) == {}
    finally:
        _worker_cleanup(job_id)


def test_parse_progress_overwrites_previous_value():
    """AC3: a later Progress Line for the same source overwrites the earlier one."""
    job_id = "t7-ac3-overwrite"
    _worker_cleanup(job_id)
    _parse_progress(job_id, "[perplexity] [1/9] angle=demand")
    _parse_progress(job_id, "[perplexity] [4/9] angle=competition")

    try:
        gs = get_gather_status(job_id)
        assert gs["perplexity"] == {"step": 4, "total": 9, "angle": "competition"}
    finally:
        _worker_cleanup(job_id)


def test_get_job_endpoint_includes_gather_status():
    """AC3: GET /api/jobs/{id} returns gather_status reflecting parsed Progress Lines."""
    job_id = "t7-ac3-endpoint"
    _insert_job(job_id, status="done")
    _worker_cleanup(job_id)
    _parse_progress(job_id, "[perplexity] [3/9] angle=pricing-signals")
    _parse_progress(job_id, "[xai] [2/7] angle=competition")

    try:
        result = asyncio.run(_get_job_json(job_id))
        assert result["status_code"] == 200
        data = result["data"]
        assert data["id"] == job_id
        gs = data["gather_status"]
        assert gs["perplexity"] == {"step": 3, "total": 9, "angle": "pricing-signals"}
        assert gs["xai"] == {"step": 2, "total": 7, "angle": "competition"}
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_get_job_endpoint_404_for_unknown():
    """GET /api/jobs/{id} returns 404 for a non-existent job."""
    result = asyncio.run(_get_job_json("no-such-job-id"))
    assert result["status_code"] == 404


# ---------------------------------------------------------------------------
# AC4 — API key patterns redacted in backlog, log file, and SSE stream
# ---------------------------------------------------------------------------


def test_redact_line_sk_prefixed_token():
    """AC4: _redact_line replaces sk- prefixed tokens via pattern matching."""
    key = "sk-" + "x" * 40
    result = _redact_line(f"[plan] using key={key} for request", {})
    assert key not in result
    assert "[REDACTED]" in result


def test_redact_line_sk_token_surrounded_by_text():
    """AC4: sk- token embedded in surrounding text is redacted."""
    key = "sk-ant-api03-" + "a" * 30
    line = f"Authorization: Bearer {key}"
    result = _redact_line(line, {})
    assert key not in result
    assert "[REDACTED]" in result


def test_redact_line_40char_hex_token():
    """AC4: 40+ char hex strings are redacted by pattern."""
    hex_key = "a" * 40
    result = _redact_line(f"token={hex_key} received", {})
    assert hex_key not in result
    assert "[REDACTED]" in result


def test_redact_line_known_api_key_by_value():
    """AC4: explicitly known API key is redacted by value match."""
    known = "short-known-key-99"
    result = _redact_line(f"request with {known} done", {"provider": known})
    assert known not in result
    assert "[REDACTED]" in result


def test_key_redacted_before_backlog_and_log(tmp_path):
    """AC4: subprocess stderr with sk- token → [REDACTED] in backlog and log file."""
    job_id = "t7-ac4-step"
    key = "sk-" + "x" * 40
    _worker_cleanup(job_id)

    async def _run():
        queue = asyncio.Queue()
        log_path = tmp_path / "step.log"
        rc = await _run_real_step(
            cmd=[
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write('[plan] token={key}\\n')",
            ],
            log_path=log_path,
            queue=queue,
            api_keys={},
            job_id=job_id,
        )
        return rc, log_path

    rc, log_path = asyncio.run(_run())

    try:
        assert rc == 0

        log_content = log_path.read_text(encoding="utf-8")
        assert key not in log_content, "Plaintext key found in log file"
        assert "[REDACTED]" in log_content

        backlog = get_job_backlog(job_id)
        combined = " ".join(backlog)
        assert key not in combined, "Plaintext key found in backlog"
        assert "[REDACTED]" in combined
    finally:
        _worker_cleanup(job_id)


def test_sse_stream_delivers_redacted_not_original():
    """AC4: SSE stream delivers [REDACTED], not the original sk- token."""
    job_id = "t7-ac4-sse"
    key = "sk-" + "x" * 40
    _insert_job(job_id, status="done")
    # Backlog is populated by the worker after redaction; simulate that here.
    _job_backlogs[job_id] = ["[plan] token=[REDACTED] received"]
    _job_terminal[job_id] = True

    try:
        received = asyncio.run(_collect_sse(job_id))
        assert len(received) == 1
        assert key not in received[0]
        assert "[REDACTED]" in received[0]
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)


def test_end_to_end_key_redaction(tmp_path):
    """AC4 end-to-end: subprocess emits sk- key → backlog redacted → SSE delivers [REDACTED]."""
    job_id = "t7-ac4-e2e"
    key = "sk-" + "x" * 40
    _insert_job(job_id, status="running")
    _worker_cleanup(job_id)

    async def _full():
        # Run the real step — populates backlog with redacted line.
        queue = asyncio.Queue()
        await _run_real_step(
            cmd=[
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write('[gather] key={key}\\n')",
            ],
            log_path=tmp_path / "e2e.log",
            queue=queue,
            api_keys={},
            job_id=job_id,
        )
        # Signal terminal so SSE generator closes.
        _job_terminal[job_id] = True
        return await _collect_sse(job_id)

    try:
        received = asyncio.run(_full())
        assert len(received) >= 1, "Expected at least one SSE event"
        for event in received:
            assert key not in event, f"Plaintext key leaked in SSE event: {event!r}"
        assert any("[REDACTED]" in ev for ev in received)
    finally:
        _delete_job(job_id)
        _worker_cleanup(job_id)
