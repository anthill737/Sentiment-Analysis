import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet


def _master_key_path() -> Path:
    override = os.environ.get("SA_RUNNER_MASTER_KEY_PATH")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"
    )
    return Path(local_app_data) / "sa-runner" / "master.key"


def _set_user_only_permissions(path: Path) -> None:
    if sys.platform == "win32":
        try:
            import subprocess

            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        f"{username}:(R,W)",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
        except Exception:
            pass
    else:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


_fernet: Fernet | None = None


def init_master_key() -> None:
    """Load or generate the Fernet master key. Clears api_keys rows if a new key is generated."""
    global _fernet
    from app.database import engine
    from app.models import ApiKey
    from sqlmodel import Session, select

    path = _master_key_path()
    if path.exists():
        _fernet = Fernet(path.read_bytes().strip())
    else:
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        _set_user_only_permissions(path)
        _fernet = Fernet(key)
        with Session(engine) as session:
            for row in session.exec(select(ApiKey)).all():
                session.delete(row)
            session.commit()


def get_fernet() -> Fernet:
    if _fernet is None:
        raise RuntimeError("Master key not initialized — call init_master_key() first")
    return _fernet


def encrypt_api_key(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    return get_fernet().decrypt(ciphertext.encode()).decode()
