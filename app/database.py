import os
from pathlib import Path

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine


def _data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"
    )
    d = Path(local_app_data) / "sa-runner"
    d.mkdir(parents=True, exist_ok=True)
    return d


DB_PATH = _data_dir() / "sa_runner.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    # Add columns to existing databases that predate these fields. Each ALTER
    # is wrapped in try/except because SQLite raises if the column already exists.
    with engine.begin() as conn:
        for col_sql in (
            "ALTER TABLE jobs ADD COLUMN degraded_sources TEXT",
            "ALTER TABLE jobs ADD COLUMN clarification_questions TEXT",
            "ALTER TABLE jobs ADD COLUMN total_cost_usd REAL",
            "ALTER TABLE jobs ADD COLUMN total_duration_seconds INTEGER",
            "ALTER TABLE jobs ADD COLUMN cost_breakdown_json TEXT",
        ):
            try:
                conn.execute(text(col_sql))
            except Exception:
                pass  # Column already exists — safe to ignore

        # Backfill total_duration_seconds for past completed jobs that have
        # created_at + completed_at but no recorded duration. Cost can't be
        # backfilled (no usage.json existed for those runs) but duration is
        # always computable from timestamps. This is idempotent — once a row
        # has a non-NULL duration, the WHERE clause skips it.
        try:
            conn.execute(text(
                "UPDATE jobs "
                "SET total_duration_seconds = "
                "    CAST((julianday(completed_at) - julianday(created_at)) * 86400 AS INTEGER) "
                "WHERE total_duration_seconds IS NULL "
                "  AND completed_at IS NOT NULL "
                "  AND created_at IS NOT NULL"
            ))
        except Exception:
            pass
