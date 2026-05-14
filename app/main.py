import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

import app.models  # noqa: F401 - registers SQLModel table metadata
from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    get_session_secret,
    sign_session,
    verify_session,
)
from app.database import create_db_and_tables, engine
from app.keys import encrypt_api_key, init_master_key
from app.models import ApiKey, Job
from app.worker import (
    get_analyze_status,
    get_gather_status,
    get_job_backlog,
    is_job_terminal,
    recover_interrupted_jobs,
    run_job,
)

load_dotenv()

_password = os.environ.get("APP_PASSWORD", "").strip()
if not _password:
    print("ERROR: APP_PASSWORD is not set or empty in .env", file=sys.stderr)
    sys.exit(1)

PROVIDERS = {"anthropic", "perplexity", "xai", "fmp"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_db_and_tables()
    get_session_secret()  # ensure SESSION_SECRET exists in .env
    init_master_key()
    recover_interrupted_jobs()
    yield


app = FastAPI(lifespan=lifespan)

_DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if (_DIST_DIR / "assets").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_DIST_DIR / "assets")),
        name="assets",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runs_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"
    )
    return Path(local_app_data) / "sa-runner" / "runs"


def _job_to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "subject": j.subject,
        "status": j.status,
        "current_phase": j.current_phase,
        "verdict": j.verdict,
        "score": j.score,
        "error_message": j.error_message,
        "degraded_sources": json.loads(j.degraded_sources)
        if j.degraded_sources
        else [],
        "clarification_questions": json.loads(j.clarification_questions)
        if j.clarification_questions
        else [],
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
    }


def _read_results(run_dir_str: str) -> dict:
    """Read executive.json and scores.json from run_dir for a completed job."""
    run_dir = Path(run_dir_str)
    result: dict = {
        "pursue_reasons": [],
        "pass_reasons": [],
        "confidence": None,
        "angle_scores": {},
    }
    try:
        exec_data = json.loads((run_dir / "executive.json").read_text(encoding="utf-8"))
        result["pursue_reasons"] = exec_data.get("pursue_reasons", [])
        result["pass_reasons"] = exec_data.get("pass_reasons", [])
        result["confidence"] = exec_data.get("confidence")
    except Exception:
        pass
    try:
        scores_data = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
        result["angle_scores"] = scores_data.get("scores", {})
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_auth(sa_session: Optional[str] = Cookie(default=None)) -> None:
    secret = get_session_secret()
    if not sa_session or not verify_session(sa_session, secret):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@app.post("/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    password = body.get("password", "")
    if password != _password:
        return JSONResponse({"error": "invalid password"}, status_code=401)
    secret = get_session_secret()
    token = sign_session(secret)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        secure=False,
    )
    return {"ok": True}


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status(sa_session: Optional[str] = Cookie(default=None)):
    """Returns authentication state without raising 401, so the SPA can check
    auth on load without generating a console error."""
    secret = get_session_secret()
    authenticated = bool(sa_session and verify_session(sa_session, secret))
    return {"authenticated": authenticated}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobPayload(BaseModel):
    subject: str
    time_window: Literal["30d", "90d", "180d", "1y", "2y", "5y", "all"] = "1y"
    focus_areas: str = ""
    include_public_market: bool = False
    tickers: str = ""


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
async def list_jobs():
    with Session(engine) as session:
        jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
    return [_job_to_dict(j) for j in jobs]


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job(payload: JobPayload):
    with Session(engine) as session:
        active = session.exec(
            select(Job).where(Job.status.in_(["running", "awaiting_clarification"]))
        ).first()
        if active:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A job is already {active.status}. Cancel or finish it before starting a new one."
                ),
            )

    job_id = str(uuid.uuid4())
    run_dir = _runs_dir() / job_id
    run_dir.mkdir(parents=True, exist_ok=True)

    payload_data = payload.model_dump()
    # The planner (skill's plan_research.py) expects different field names than the
    # form uses. Translate before writing payload.json. Without this, the planner
    # silently ignores time_window/focus_areas/include_public_market/tickers and
    # falls back to its argparse defaults (window=1y, no focus, no FMP).
    skill_payload = {
        "subject": payload.subject,
        "window": payload.time_window,
        "focus": [s.strip() for s in payload.focus_areas.split(",") if s.strip()],
        "include_fmp": payload.include_public_market,
        "fmp_tickers": [s.strip() for s in payload.tickers.split(",") if s.strip()],
    }
    (run_dir / "payload.json").write_text(
        json.dumps(skill_payload, indent=2), encoding="utf-8"
    )

    job = Job(
        id=job_id,
        subject=payload.subject,
        payload_json=json.dumps(payload_data),
        status="running",
        run_dir=str(run_dir),
        started_at=datetime.utcnow(),
    )
    with Session(engine) as session:
        session.add(job)
        session.commit()

    # SA_RUNNER_SKIP_WORKER=1 prevents the worker from launching (used in tests
    # that don't need end-to-end pipeline execution).
    if os.environ.get("SA_RUNNER_SKIP_WORKER") != "1":
        task = asyncio.create_task(run_job(job_id))
        from app.worker import register_job_task
        register_job_task(job_id, task)

    return {"id": job_id}


class ClarificationResponse(BaseModel):
    response: str


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_job(job_id: str):
    from app.worker import get_job_task
    with Session(engine) as session:
        job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("running", "awaiting_clarification"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status}; only running or awaiting-clarification jobs can be cancelled.",
        )
    task = get_job_task(job_id)
    if task and not task.done():
        task.cancel()
        return {"status": "cancelling"}
    # Job is in awaiting_clarification with no active task — just mark cancelled directly.
    with Session(engine) as session:
        j = session.get(Job, job_id)
        if j:
            j.status = "cancelled"
            j.error_message = "Cancelled by user"
            j.completed_at = datetime.utcnow()
            session.add(j)
            session.commit()
    return {"status": "cancelled"}


@app.post("/api/jobs/{job_id}/respond", dependencies=[Depends(require_auth)])
async def respond_to_clarification(job_id: str, body: ClarificationResponse):
    """Append the user's clarification response to the job's subject and re-run Phase 1."""
    from app.worker import clear_job_terminal, register_job_task

    response_text = body.response.strip()
    if not response_text:
        raise HTTPException(status_code=400, detail="Response cannot be empty.")

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "awaiting_clarification":
            raise HTTPException(
                status_code=409,
                detail=f"Job is {job.status}; only awaiting-clarification jobs accept responses.",
            )

        # Append the clarification to the subject in payload.json and re-run plan.
        run_dir = Path(job.run_dir)
        payload_path = run_dir / "payload.json"
        try:
            payload_data = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=500, detail="Could not read job payload.")

        payload_data["subject"] = (
            f"{payload_data.get('subject', '')}\n\n[Clarification from user]: {response_text}"
        )
        payload_path.write_text(json.dumps(payload_data, indent=2), encoding="utf-8")

        # Update the job row: clear clarification, return to running.
        job.subject = payload_data["subject"]
        job.status = "running"
        job.clarification_questions = None
        job.error_message = None
        session.add(job)
        session.commit()

    # Clear terminal flag so the SSE endpoint will re-open.
    clear_job_terminal(job_id)

    # Re-run from Phase 1.
    if os.environ.get("SA_RUNNER_SKIP_WORKER") != "1":
        task = asyncio.create_task(run_job(job_id))
        register_job_task(job_id, task)

    return {"status": "resumed"}


@app.post("/api/jobs/{job_id}/resynthesize", dependencies=[Depends(require_auth)])
async def resynthesize_existing_job(job_id: str):
    """Re-run Analyze + Render on an existing job to regenerate the report.

    Reuses the prior plan and fetched data. Cheap compared to a fresh run
    because the expensive Gather phase is skipped — only re-pays for the
    Claude calls inside Analyze (cluster, score, write sections, write
    executive) plus local chart-gen and rendering.
    """
    from app.worker import register_job_task, resynthesize_job

    with Session(engine) as session:
        # Don't let resynthesize start while another job is active.
        active = session.exec(
            select(Job).where(Job.status.in_(["running", "awaiting_clarification"]))
        ).first()
        if active and active.id != job_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Another job is {active.status}. Cancel or finish it before "
                    "re-synthesizing."
                ),
            )

        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Allow re-synthesize on jobs that have completed (done/failed/cancelled)
        # but not on ones currently in progress.
        if job.status in ("running", "awaiting_clarification"):
            raise HTTPException(
                status_code=409,
                detail=f"Job is {job.status}; let it finish before re-synthesizing.",
            )

    if os.environ.get("SA_RUNNER_SKIP_WORKER") != "1":
        task = asyncio.create_task(resynthesize_job(job_id))
        register_job_task(job_id, task)

    return {"status": "resynthesizing"}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_job(job_id: str):
    with Session(engine) as session:
        job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = {
        **_job_to_dict(job),
        "gather_status": get_gather_status(job_id),
        "analyze_status": get_analyze_status(job_id),
    }
    if job.status == "done" and job.run_dir:
        data.update(_read_results(job.run_dir))
    return data


@app.get("/api/jobs/{job_id}/stream", dependencies=[Depends(require_auth)])
async def stream_job_logs(job_id: str):
    with Session(engine) as session:
        job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generator():
        backlog = get_job_backlog(job_id)
        cursor = 0
        while True:
            current = len(backlog)
            while cursor < current:
                yield {"data": backlog[cursor]}
                cursor += 1
            if is_job_terminal(job_id):
                # Drain any lines added between the last read and terminal check.
                current = len(backlog)
                while cursor < current:
                    yield {"data": backlog[cursor]}
                    cursor += 1
                return
            await asyncio.sleep(0.1)

    return EventSourceResponse(generator())


@app.get("/api/jobs/{job_id}/download/pdf", dependencies=[Depends(require_auth)])
async def download_pdf(job_id: str):
    with Session(engine) as session:
        job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=404, detail="Job not complete")
    pdf_path = Path(job.run_dir) / "report.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename="report.pdf",
    )


@app.get("/api/jobs/{job_id}/download/docx", dependencies=[Depends(require_auth)])
async def download_docx(job_id: str):
    with Session(engine) as session:
        job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=404, detail="Job not complete")
    docx_path = Path(job.run_dir) / "report.docx"
    if not docx_path.exists():
        raise HTTPException(status_code=404, detail="DOCX not found")
    return FileResponse(
        str(docx_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="report.docx",
    )


# ---------------------------------------------------------------------------
# Settings - API key management
# ---------------------------------------------------------------------------


class ApiKeyValue(BaseModel):
    value: str


@app.get("/api/settings/keys", dependencies=[Depends(require_auth)])
async def get_settings_keys():
    with Session(engine) as session:
        stored = {row.provider for row in session.exec(select(ApiKey)).all()}
    return {p: ("configured" if p in stored else "not configured") for p in PROVIDERS}


@app.put("/api/settings/keys/{provider}", dependencies=[Depends(require_auth)])
async def put_settings_key(provider: str, body: ApiKeyValue):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {provider}")
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="value must not be empty")
    ciphertext = encrypt_api_key(value)
    with Session(engine) as session:
        existing = session.get(ApiKey, provider)
        if existing:
            existing.ciphertext = ciphertext
            session.add(existing)
        else:
            session.add(ApiKey(provider=provider, ciphertext=ciphertext))
        session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# SPA
# ---------------------------------------------------------------------------


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    index_html = _DIST_DIR / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return JSONResponse({"status": "ok"})
