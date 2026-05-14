import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlmodel import SQLModel, create_engine


def test_root_path_returns_200():
    """AC1 / AC3: GET / returns HTTP 200."""
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200


def test_db_tables_exist():
    """AC2: SQLModel metadata defines jobs and api_keys tables."""
    import app.models  # noqa: F401 — registers SQLModel table metadata

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())
    assert "jobs" in tables, f"Missing 'jobs' table; found: {tables}"
    assert "api_keys" in tables, f"Missing 'api_keys' table; found: {tables}"


def test_jobs_table_columns():
    """AC2: jobs table has all required columns."""
    import app.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    inspector = sa_inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("jobs")}
    required = {
        "id",
        "subject",
        "payload_json",
        "created_at",
        "started_at",
        "completed_at",
        "status",
        "current_phase",
        "run_dir",
        "verdict",
        "score",
        "error_message",
    }
    missing = required - cols
    assert not missing, f"Missing columns in jobs: {missing}"


def test_api_keys_table_columns():
    """AC2: api_keys table has required columns."""
    import app.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    inspector = sa_inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("api_keys")}
    assert "provider" in cols
    assert "ciphertext" in cols


def test_missing_app_password_exits_nonzero():
    """AC4: Server refuses to start if APP_PASSWORD is missing."""
    env = {k: v for k, v in os.environ.items()}
    env.pop("APP_PASSWORD", None)
    project_root = str(Path(__file__).parent.parent)
    # PYTHONPATH allows 'import app.main' to resolve from the temp CWD.
    # Temp CWD ensures load_dotenv() cannot find the project .env file and
    # inadvertently re-supply APP_PASSWORD, which would make the app start
    # successfully and give a false-passing test.
    env["PYTHONPATH"] = project_root
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [sys.executable, "-c", "import app.main"],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )
    assert result.returncode != 0, (
        f"Expected non-zero exit when APP_PASSWORD is missing; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "APP_PASSWORD" in result.stderr, (
        f"Expected 'APP_PASSWORD' in stderr; got: {result.stderr!r}"
    )


def test_empty_app_password_exits_nonzero():
    """AC4: Server refuses to start if APP_PASSWORD is empty string."""
    env = {k: v for k, v in os.environ.items()}
    env["APP_PASSWORD"] = ""
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit when APP_PASSWORD is empty; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "APP_PASSWORD" in result.stderr, (
        f"Expected 'APP_PASSWORD' in stderr; got: {result.stderr!r}"
    )
