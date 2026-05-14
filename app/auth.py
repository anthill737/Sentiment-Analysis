import os
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "sa_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

_secret: str | None = None


def get_session_secret() -> str:
    global _secret
    if _secret:
        return _secret
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        secret = _generate_and_persist_secret()
    _secret = secret
    return _secret


def _generate_and_persist_secret() -> str:
    new_secret = secrets.token_hex(32)
    env_path = Path(__file__).parent.parent / ".env"
    with open(env_path, "a") as f:
        f.write(f"\nSESSION_SECRET={new_secret}\n")
    os.environ["SESSION_SECRET"] = new_secret
    return new_secret


def sign_session(secret: str) -> str:
    return TimestampSigner(secret).sign(b"authenticated").decode()


def verify_session(token: str, secret: str) -> bool:
    try:
        TimestampSigner(secret).unsign(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
