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
        ):
            try:
                conn.execute(text(col_sql))
            except Exception:
                pass  # Column already exists — safe to ignore
