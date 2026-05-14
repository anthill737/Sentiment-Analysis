"""Tests for P2-T2: Source partial-failure & Degraded Coverage handling.

AC1 — One error envelope → Job proceeds to done, degraded_sources names that source.
AC2 — degraded_sources persists on the job row after completion (API exposes it).
AC3 — All four error envelopes → Job fails, error_message names all four sources.
AC4 — No errors → Job done, degraded_sources empty / null.
AC4 (missing file) — A missing Checkpoint File is treated as a failed Source.

P2-T6 additions — Gather Phase failure modes (unit + integration):
  - 0/4 failed: current_phase advances past Gather, degraded_sources null.
  - 2/4 failed: status 'running' during transition, degraded_sources set, phase→Analyze.
  - Missing fixture file: source counted as failed in degraded_sources (full pipeline).
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import app.worker as worker_mod
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import _job_queues, clear_mock_invocations, get_job_queue, run_job

_REAL_FIXTURES = Path(__file__).parent / "fixtures" / "skill_outputs"

_ERROR_ENVELOPE = {"error": "API timeout", "source": "mock-error"}

_SOURCE_FILES = {
    "perplexity": "perplexity.json",
    "xai": "xai.json",
    "trends": "trends.json",
    "fmp": "fmp.json",
}


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
                subject="Degraded Coverage Test",
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


def _make_fixtures_dir(tmp_path: Path, error_sources: list[str]) -> Path:
    """Copy canonical fixtures to tmp, replacing named sources with Error Envelopes."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for src_file in _REAL_FIXTURES.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, fixtures_dir / src_file.name)
    for source in error_sources:
        fname = _SOURCE_FILES[source]
        (fixtures_dir / fname).write_text(json.dumps(_ERROR_ENVELOPE), encoding="utf-8")
    return fixtures_dir


def _run_mock(
    job_id: str, run_dir: Path, monkeypatch, tmp_path: Path, error_sources: list[str]
) -> None:
    skill_dir = tmp_path / "skill"
    fixtures_dir = _make_fixtures_dir(tmp_path, error_sources)
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", str(skill_dir / "node"))
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(fixtures_dir))
    _job_queues.pop(job_id, None)
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# ---------------------------------------------------------------------------
# AC1 — One error envelope → job=done, degraded_sources names that source
# ---------------------------------------------------------------------------


def test_one_error_envelope_job_proceeds_to_done(tmp_path, monkeypatch):
    """AC1: Single error envelope → status=done with perplexity in degraded_sources."""
    job_id = "t8-ac1-one-error"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=["perplexity"])

        job = _get_job(job_id)
        assert job is not None
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        assert job.degraded_sources is not None, "degraded_sources should be set"
        sources = json.loads(job.degraded_sources)
        assert "perplexity" in sources, f"Expected perplexity in {sources}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_one_error_envelope_only_failed_source_listed(tmp_path, monkeypatch):
    """AC1: degraded_sources contains only the source that errored."""
    job_id = "t8-ac1-one-error-only"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=["xai"])

        job = _get_job(job_id)
        sources = json.loads(job.degraded_sources)
        assert sources == ["xai"], f"Expected ['xai'], got {sources}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_three_error_envelopes_job_proceeds_to_done(tmp_path, monkeypatch):
    """AC1 boundary: three sources errored → still done (Degraded Coverage)."""
    job_id = "t8-ac1-three-errors"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(
            job_id,
            run_dir,
            monkeypatch,
            tmp_path,
            error_sources=["perplexity", "xai", "trends"],
        )

        job = _get_job(job_id)
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        sources = json.loads(job.degraded_sources)
        assert set(sources) == {"perplexity", "xai", "trends"}
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC2 — degraded_sources persists and is exposed via API
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client():
    import app.keys as keys_mod

    keys_mod._fernet = None
    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c


def test_api_job_returns_degraded_sources(tmp_path, monkeypatch, authed_client):
    """AC2: GET /api/jobs/{id} returns degraded_sources list after job completes."""
    job_id = "t8-ac2-api"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=["fmp"])

        resp = authed_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "done"
        assert "fmp" in data["degraded_sources"], (
            f"Expected fmp in degraded_sources, got {data['degraded_sources']}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_api_jobs_list_includes_degraded_sources(tmp_path, monkeypatch, authed_client):
    """AC2: GET /api/jobs includes degraded_sources on each job entry."""
    job_id = "t8-ac2-list"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=["trends"])

        resp = authed_client.get("/api/jobs")
        assert resp.status_code == 200, resp.text
        jobs = resp.json()
        matching = [j for j in jobs if j["id"] == job_id]
        assert matching, "Job not found in list"
        assert "trends" in matching[0]["degraded_sources"]
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC3 — All four error envelopes → job=failed, error_message names all sources
# ---------------------------------------------------------------------------


def test_all_four_error_envelopes_job_fails(tmp_path, monkeypatch):
    """AC3: All four error envelopes → status=failed."""
    job_id = "t8-ac3-all-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(
            job_id,
            run_dir,
            monkeypatch,
            tmp_path,
            error_sources=["perplexity", "xai", "trends", "fmp"],
        )

        job = _get_job(job_id)
        assert job.status == "failed", f"Expected failed, got {job.status}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_all_four_error_envelopes_error_message_names_sources(tmp_path, monkeypatch):
    """AC3: error_message names all four failing sources."""
    job_id = "t8-ac3-names"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(
            job_id,
            run_dir,
            monkeypatch,
            tmp_path,
            error_sources=["perplexity", "xai", "trends", "fmp"],
        )

        job = _get_job(job_id)
        msg = job.error_message or ""
        for source in ("perplexity", "xai", "trends", "fmp"):
            assert source in msg, f"Expected '{source}' in error_message: {msg!r}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — No errors → job=done, degraded_sources null/empty
# ---------------------------------------------------------------------------


def test_no_errors_job_done_no_degraded_sources(tmp_path, monkeypatch):
    """AC4: No error envelopes → status=done, degraded_sources is null/empty."""
    job_id = "t8-ac4-clean"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=[])

        job = _get_job(job_id)
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        assert not job.degraded_sources or job.degraded_sources == "[]", (
            f"Expected no degraded_sources, got {job.degraded_sources!r}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_no_errors_api_returns_empty_degraded_sources(
    tmp_path, monkeypatch, authed_client
):
    """AC4: GET /api/jobs/{id} returns empty degraded_sources when all sources pass."""
    job_id = "t8-ac4-api-clean"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=[])

        resp = authed_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["degraded_sources"] == [], (
            f"Expected empty degraded_sources, got {data['degraded_sources']}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4 — Missing Checkpoint File is treated as a failed Source
# ---------------------------------------------------------------------------


def test_missing_checkpoint_file_treated_as_failed_source(tmp_path):
    """AC4: _detect_errored_sources treats a missing Checkpoint File as a failed Source."""
    from app.worker import _detect_errored_sources

    run_dir = tmp_path / "runs" / "ac4-missing"
    run_dir.mkdir(parents=True)
    # rc=0 for all subprocesses, but no output files were written
    gather_results = [0, 0, 0, 0]
    errored = _detect_errored_sources(gather_results, run_dir)
    assert set(errored) == {"perplexity", "xai", "trends", "fmp"}, (
        f"Expected all four sources errored (missing files), got {errored}"
    )


def test_missing_single_checkpoint_file_is_degraded_source(tmp_path):
    """AC4: A single missing Checkpoint File → that source is in the errored list."""
    from app.worker import _detect_errored_sources

    run_dir = tmp_path / "runs" / "ac4-one-missing"
    run_dir.mkdir(parents=True)
    # Write all files except xai.json
    (run_dir / "perplexity.json").write_text(
        '{"source": "perplexity", "results": []}', encoding="utf-8"
    )
    (run_dir / "trends.json").write_text(
        '{"source": "trends", "results": []}', encoding="utf-8"
    )
    (run_dir / "fmp.json").write_text(
        '{"source": "fmp", "results": []}', encoding="utf-8"
    )
    # xai.json intentionally not written

    gather_results = [0, 0, 0, 0]
    errored = _detect_errored_sources(gather_results, run_dir)
    assert errored == ["xai"], (
        f"Expected only xai in errored (missing file), got {errored}"
    )


def test_empty_checkpoint_file_treated_as_failed_source(tmp_path):
    """AC4: A Checkpoint File containing empty JSON {} is treated as a failed Source."""
    from app.worker import _detect_errored_sources

    run_dir = tmp_path / "runs" / "ac4-empty"
    run_dir.mkdir(parents=True)
    # Write valid files for three sources, empty {} for trends
    (run_dir / "perplexity.json").write_text(
        '{"source": "perplexity", "results": []}', encoding="utf-8"
    )
    (run_dir / "xai.json").write_text(
        '{"source": "xai", "results": []}', encoding="utf-8"
    )
    (run_dir / "trends.json").write_text("{}", encoding="utf-8")
    (run_dir / "fmp.json").write_text(
        '{"source": "fmp", "results": []}', encoding="utf-8"
    )

    gather_results = [0, 0, 0, 0]
    errored = _detect_errored_sources(gather_results, run_dir)
    assert errored == ["trends"], (
        f"Expected only trends in errored (empty JSON), got {errored}"
    )


# ---------------------------------------------------------------------------
# Progress Line emitted for degraded coverage
# ---------------------------------------------------------------------------


def test_degraded_coverage_progress_line_emitted(tmp_path, monkeypatch):
    """Worker emits a [gather] progress line naming errored sources."""
    job_id = "t8-prog-line"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=["xai"])

        queue = get_job_queue(job_id)
        lines = []
        while not queue.empty():
            lines.append(queue.get_nowait())

        degraded_lines = [ln for ln in lines if "degraded" in ln.lower()]
        assert degraded_lines, (
            f"No degraded coverage progress line found. Lines: {lines}"
        )
        assert any("xai" in ln for ln in degraded_lines), (
            f"Expected 'xai' in degraded line. Lines: {degraded_lines}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# ---------------------------------------------------------------------------
# P2-T6: Gather Phase failure modes — unit / integration tests
# ---------------------------------------------------------------------------


def test_zero_failures_current_phase_advances_past_gather(tmp_path, monkeypatch):
    """AC1 (P2-T6): 0/4 sources fail — current_phase transitions gather→analyze,
    proving the worker advanced past Gather. degraded_sources remains null."""
    job_id = "p2t6-ac1-zero-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    phases_seen: list[str] = []
    original_update = worker_mod._update_job

    def spy_update(jid: str, **kwargs: object) -> None:
        if "current_phase" in kwargs and kwargs["current_phase"] is not None:
            phases_seen.append(str(kwargs["current_phase"]))
        original_update(jid, **kwargs)

    monkeypatch.setattr(worker_mod, "_update_job", spy_update)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path, error_sources=[])

        assert "gather" in phases_seen, f"gather never set; phases: {phases_seen}"
        assert "analyze" in phases_seen, (
            f"current_phase never advanced past gather; phases: {phases_seen}"
        )
        assert phases_seen.index("gather") < phases_seen.index("analyze"), (
            f"gather must precede analyze; phases: {phases_seen}"
        )

        job = _get_job(job_id)
        assert job is not None
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        assert not job.degraded_sources or job.degraded_sources == "[]", (
            f"Expected null degraded_sources, got {job.degraded_sources!r}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_two_failures_degraded_sources_set_and_phase_advances_to_analyze(
    tmp_path, monkeypatch
):
    """AC2 (P2-T6): 2/4 error envelopes — degraded_sources names both sources,
    current_phase transitions to analyze (status remains 'running' throughout that
    transition), and final status is done."""
    job_id = "p2t6-ac2-two-fail"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    update_calls: list[dict] = []
    original_update = worker_mod._update_job

    def spy_update(jid: str, **kwargs: object) -> None:
        update_calls.append(dict(kwargs))
        original_update(jid, **kwargs)

    monkeypatch.setattr(worker_mod, "_update_job", spy_update)

    try:
        _run_mock(
            job_id,
            run_dir,
            monkeypatch,
            tmp_path,
            error_sources=["perplexity", "xai"],
        )

        # degraded_sources must be set with exactly the two failing sources
        degraded_updates = [u for u in update_calls if "degraded_sources" in u]
        assert degraded_updates, "No _update_job call set degraded_sources"
        sources = json.loads(degraded_updates[0]["degraded_sources"])
        assert set(sources) == {"perplexity", "xai"}, (
            f"Expected {{perplexity, xai}}, got {set(sources)}"
        )

        # current_phase must have advanced to 'analyze'
        phase_values = [
            u["current_phase"]
            for u in update_calls
            if "current_phase" in u and u["current_phase"] is not None
        ]
        assert "analyze" in phase_values, (
            f"current_phase never reached 'analyze'; phases: {phase_values}"
        )

        # No failure was set before the analyze phase (status='running' throughout)
        analyze_idx = next(
            i for i, u in enumerate(update_calls) if u.get("current_phase") == "analyze"
        )
        for prior in update_calls[:analyze_idx]:
            assert prior.get("status") != "failed", (
                f"Job transitioned to failed before analyze phase: {prior}"
            )

        job = _get_job(job_id)
        assert job.status == "done", (
            f"Expected done, got {job.status}: {job.error_message}"
        )
        final_sources = json.loads(job.degraded_sources)
        assert set(final_sources) == {"perplexity", "xai"}
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_missing_fixture_source_counted_as_failed_in_degraded_sources(
    tmp_path, monkeypatch
):
    """AC4 (P2-T6 integration): Fixture dir missing one Source's output file →
    that source is counted as failed in degraded_sources on the live job row.

    The mock seam writes {} when the fixture is absent (same effect as a
    crashed subprocess that writes no Checkpoint File — _detect_errored_sources
    treats both missing files and empty {} as failed sources).
    """
    job_id = "p2t6-ac4-missing-fixture"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    # Build a fixtures dir that omits 'fmp.json', simulating a crashed subprocess.
    missing_source = "fmp"
    fixtures_dir = tmp_path / "fixtures_no_fmp"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for src_file in _REAL_FIXTURES.iterdir():
        if src_file.is_file() and src_file.name != _SOURCE_FILES[missing_source]:
            shutil.copy2(src_file, fixtures_dir / src_file.name)
    # fmp.json intentionally absent from fixtures_dir

    skill_dir = tmp_path / "skill"
    monkeypatch.setenv("SA_RUNNER_MOCK_PIPELINE", "1")
    monkeypatch.setenv("SA_RUNNER_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("SA_RUNNER_NODE_EXE", str(skill_dir / "node"))
    monkeypatch.setenv("SA_RUNNER_WORD_NOT_NEEDED", str(skill_dir / "word_convert"))
    monkeypatch.setenv("SA_RUNNER_SKILL_DIR", str(skill_dir))
    monkeypatch.setenv("SA_RUNNER_FIXTURES_DIR", str(fixtures_dir))
    _job_queues.pop(job_id, None)
    clear_mock_invocations()

    try:
        asyncio.run(run_job(job_id))

        job = _get_job(job_id)
        assert job is not None
        # 1/4 failed → Degraded Coverage, job still completes
        assert job.status == "done", (
            f"Expected done (degraded), got {job.status}: {job.error_message}"
        )
        assert job.degraded_sources is not None, (
            "degraded_sources must be set for the missing-fixture source"
        )
        sources = json.loads(job.degraded_sources)
        assert missing_source in sources, (
            f"Expected '{missing_source}' in degraded_sources, got {sources}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)
