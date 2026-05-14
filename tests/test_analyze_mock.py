"""Tests for P3-T3: Mock Pipeline Mode — Analyze Phase fixtures and Step replay.

AC1 — All 7 Analyze Phase Checkpoint Files placed in Run Directory with
       valid, parseable JSON (chart stubs may be empty files), no real
       Skill Scripts invoked.
AC2 — Mock emits ≥3 scripted Progress Lines per Analyze Step; write_sections
       emits at least one '[sections] [N/M] writing '<title>'' line.
AC3 — synthesis.json fixture contains the same top-level keys as example_synthesis.json.
AC4 — executive.json fixture contains non-empty verdict, score, confidence,
       and at least one pursue-reason and one pass-reason.
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from app.database import create_db_and_tables, engine
from app.models import Job
from app.worker import (
    _job_queues,
    clear_mock_invocations,
    get_job_queue,
    run_job,
)

# -----------------------------------------------------------------------
# Expected top-level keys in synthesis.json — must match example_synthesis.json
# -----------------------------------------------------------------------
_EXAMPLE_SYNTHESIS_PATH = Path(__file__).parent.parent / "example_synthesis.json"
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "skill_outputs"

# Analyze Phase tags in order, used to verify ≥3 Progress Lines each.
_ANALYZE_STEP_TAGS = [
    "extract",
    "cluster",
    "score",
    "sections",
    "executive",
    "charts",
    "assemble",
]

_SECTIONS_LINE_RE = re.compile(r"^\[sections\] \[\d+/\d+\] writing '.+'$")


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


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
                subject="Analyze Mock Test",
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
    clear_mock_invocations()
    asyncio.run(run_job(job_id))


# -----------------------------------------------------------------------
# AC1 — All 7 Analyze Phase Checkpoint Files present with valid JSON
# -----------------------------------------------------------------------


def test_analyze_mock_no_real_subprocesses(tmp_path, monkeypatch):
    """AC1: asyncio.create_subprocess_exec is never called during an Analyze mock run."""
    job_id = "p3t3-ac1-no-subproc"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            _run_mock(job_id, run_dir, monkeypatch, tmp_path)
            mock_exec.assert_not_called()

        job = _get_job(job_id)
        assert job.status == "done", f"Job failed: {job.error_message}"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_analyze_mock_json_checkpoint_files_present(tmp_path, monkeypatch):
    """AC1: The 5 JSON Checkpoint Files from Analyze Phase exist and are parseable."""
    job_id = "p3t3-ac1-json-files"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        json_checkpoints = [
            "evidence.json",
            "themes.json",
            "scores.json",
            "executive.json",
            "synthesis.json",
        ]
        for fname in json_checkpoints:
            fpath = run_dir / fname
            assert fpath.exists(), f"Missing Checkpoint File: {fname}"
            try:
                json.loads(fpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                pytest.fail(f"{fname} is not valid JSON: {exc}")
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_analyze_mock_sections_directory_present(tmp_path, monkeypatch):
    """AC1: write_sections Step creates sections/ directory with JSON files in Run Directory."""
    job_id = "p3t3-ac1-sections"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        sections_dir = run_dir / "sections"
        assert sections_dir.is_dir(), "sections/ directory missing from Run Directory"

        section_files = sorted(sections_dir.iterdir())
        assert section_files, "sections/ directory is empty"

        for sf in section_files:
            if sf.suffix == ".json":
                try:
                    json.loads(sf.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    pytest.fail(f"Section file {sf.name} is not valid JSON: {exc}")
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_analyze_mock_charts_directory_present(tmp_path, monkeypatch):
    """AC1: generate_charts Step creates charts/ directory (stubs may be empty) in Run Directory."""
    job_id = "p3t3-ac1-charts"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        charts_dir = run_dir / "charts"
        assert charts_dir.is_dir(), "charts/ directory missing from Run Directory"
        chart_files = list(charts_dir.iterdir())
        assert chart_files, "charts/ directory is empty"
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# -----------------------------------------------------------------------
# AC2 — ≥3 Progress Lines per Analyze Step; sections format check
# -----------------------------------------------------------------------


def _collect_lines(job_id: str) -> list[str]:
    queue = get_job_queue(job_id)
    lines = []
    while not queue.empty():
        lines.append(queue.get_nowait())
    return lines


def test_analyze_mock_three_or_more_lines_per_step(tmp_path, monkeypatch):
    """AC2: Each Analyze Phase Step emits ≥3 Progress Lines into the SSE queue."""
    job_id = "p3t3-ac2-three-lines"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        lines = _collect_lines(job_id)

        for tag in _ANALYZE_STEP_TAGS:
            tag_lines = [ln for ln in lines if ln.startswith(f"[{tag}]")]
            assert len(tag_lines) >= 3, (
                f"Analyze Step '[{tag}]' emitted only {len(tag_lines)} Progress Line(s); "
                f"expected ≥3. All lines: {lines}"
            )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


def test_analyze_mock_sections_step_writing_format(tmp_path, monkeypatch):
    """AC2: write_sections Step emits ≥1 line matching '[sections] [N/M] writing '<title>''."""
    job_id = "p3t3-ac2-sections-fmt"
    run_dir = tmp_path / "runs" / job_id
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text('{"subject": "test"}', encoding="utf-8")
    _insert_job(job_id, run_dir)

    try:
        _run_mock(job_id, run_dir, monkeypatch, tmp_path)

        lines = _collect_lines(job_id)
        matching = [ln for ln in lines if _SECTIONS_LINE_RE.match(ln)]
        assert matching, (
            "No '[sections] [N/M] writing '<title>'' Progress Line found. "
            f"Sections lines: {[ln for ln in lines if ln.startswith('[sections]')]}"
        )
    finally:
        _delete_job(job_id)
        _job_queues.pop(job_id, None)


# -----------------------------------------------------------------------
# AC3 — synthesis.json fixture matches example_synthesis.json top-level keys
# -----------------------------------------------------------------------


def test_synthesis_fixture_top_level_keys_match_example():
    """AC3: synthesis.json fixture has the same top-level keys as example_synthesis.json."""
    example = json.loads(_EXAMPLE_SYNTHESIS_PATH.read_text(encoding="utf-8"))
    fixture = json.loads((_FIXTURE_DIR / "synthesis.json").read_text(encoding="utf-8"))

    example_keys = set(example.keys())
    fixture_keys = set(fixture.keys())

    missing = example_keys - fixture_keys
    assert not missing, (
        f"synthesis.json fixture is missing top-level keys present in "
        f"example_synthesis.json: {sorted(missing)}"
    )


# -----------------------------------------------------------------------
# AC4 — executive.json fixture fields
# -----------------------------------------------------------------------


def test_executive_fixture_required_fields():
    """AC4: executive.json fixture has verdict, score, confidence, pursue_reasons, pass_reasons."""
    data = json.loads((_FIXTURE_DIR / "executive.json").read_text(encoding="utf-8"))

    assert data.get("verdict"), "executive.json missing non-empty 'verdict'"
    assert data.get("score") is not None, "executive.json missing 'score'"
    assert data.get("confidence") is not None, "executive.json missing 'confidence'"

    pursue = data.get("pursue_reasons")
    assert pursue and len(pursue) >= 1, (
        "executive.json 'pursue_reasons' must be a non-empty list"
    )

    pass_r = data.get("pass_reasons")
    assert pass_r and len(pass_r) >= 1, (
        "executive.json 'pass_reasons' must be a non-empty list"
    )
