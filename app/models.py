from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    subject: str
    payload_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str
    current_phase: Optional[str] = None
    run_dir: str
    verdict: Optional[str] = None
    score: Optional[float] = None
    error_message: Optional[str] = None
    degraded_sources: Optional[str] = None  # JSON list of errored source names
    clarification_questions: Optional[str] = None  # JSON list of strings from planner


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    provider: str = Field(primary_key=True)
    ciphertext: str
