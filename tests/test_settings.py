"""Tests for encrypted API key storage and settings endpoints (P1-T4)."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def _clear_api_keys() -> None:
    from app.database import engine
    from app.models import ApiKey

    with Session(engine) as session:
        for row in session.exec(select(ApiKey)).all():
            session.delete(row)
        session.commit()


@pytest.fixture
def client():
    import app.keys as keys_mod

    _clear_api_keys()
    keys_mod._fernet = None  # force lifespan to re-read key from disk

    from app.main import app

    with TestClient(app) as c:
        c.post("/auth/login", json={"password": "test-password"})
        yield c

    _clear_api_keys()


def test_put_key_stores_ciphertext_not_plaintext(client):
    """AC1: PUT stores ciphertext, not the submitted plaintext, in the api_keys table."""
    plaintext = "sk-ant-test-key-12345"
    resp = client.put("/api/settings/keys/anthropic", json={"value": plaintext})
    assert resp.status_code == 200

    from app.database import engine
    from app.models import ApiKey

    with Session(engine) as session:
        row = session.get(ApiKey, "anthropic")

    assert row is not None
    assert row.ciphertext != plaintext
    # Fernet tokens are base64url strings starting with 'gAAAAA'
    assert plaintext not in row.ciphertext


def test_get_keys_returns_configured_status(client):
    """AC2: GET /api/settings/keys returns 'configured' (not the value) after a key is saved."""
    client.put("/api/settings/keys/perplexity", json={"value": "pplx-secret-key"})

    resp = client.get("/api/settings/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert data["perplexity"] == "configured"
    # Key value must never appear in the response
    assert "pplx-secret-key" not in str(data)


def test_get_keys_returns_not_configured_when_absent(client):
    """GET /api/settings/keys returns 'not configured' for providers with no stored key."""
    resp = client.get("/api/settings/keys")
    assert resp.status_code == 200
    data = resp.json()
    for provider in ("anthropic", "perplexity", "xai", "fmp"):
        assert data[provider] == "not configured"


def test_delete_master_key_and_reinit_clears_configured_keys(client):
    """AC3: Deleting master.key and re-initializing the server clears all api_keys rows."""
    # Arrange: save a key so it's 'configured'
    client.put("/api/settings/keys/anthropic", json={"value": "sk-ant-before"})
    assert client.get("/api/settings/keys").json()["anthropic"] == "configured"

    import app.keys as keys_mod

    # Act: simulate hard-delete of master.key (e.g. user manually deleted it)
    key_path = keys_mod._master_key_path()
    key_path.unlink()
    keys_mod._fernet = None
    keys_mod.init_master_key()  # simulates server restart with missing key

    # Assert: every provider should now show as 'not configured'
    resp = client.get("/api/settings/keys")
    assert resp.status_code == 200
    data = resp.json()
    for provider in ("anthropic", "perplexity", "xai", "fmp"):
        assert data[provider] == "not configured", (
            f"{provider} should be not configured"
        )


def test_rotating_key_produces_different_ciphertext(client):
    """AC5: Saving a key a second time results in a different ciphertext in the DB."""
    from app.database import engine
    from app.models import ApiKey

    client.put("/api/settings/keys/fmp", json={"value": "fmp-key-v1"})
    with Session(engine) as session:
        first_ciphertext = session.get(ApiKey, "fmp").ciphertext

    client.put("/api/settings/keys/fmp", json={"value": "fmp-key-v1"})  # same plaintext
    with Session(engine) as session:
        second_ciphertext = session.get(ApiKey, "fmp").ciphertext

    # Fernet uses a random IV, so the same plaintext produces different ciphertexts
    assert first_ciphertext != second_ciphertext


def test_encrypt_decrypt_round_trip(client):
    """AC: key encryption round-trip — encrypt stores ciphertext, decrypt returns original plaintext."""
    from app.keys import decrypt_api_key, encrypt_api_key

    plaintext = "sk-ant-round-trip-test-secret-xyz"
    ciphertext = encrypt_api_key(plaintext)

    # Ciphertext must differ from plaintext
    assert ciphertext != plaintext

    # Decrypted value must exactly match the original plaintext
    recovered = decrypt_api_key(ciphertext)
    assert recovered == plaintext


def test_unknown_provider_returns_422(client):
    """PUT with an unknown provider name returns 422."""
    resp = client.put("/api/settings/keys/unknown_provider", json={"value": "some-key"})
    assert resp.status_code == 422


def test_empty_value_returns_422(client):
    """PUT with an empty value returns 422."""
    resp = client.put("/api/settings/keys/anthropic", json={"value": "   "})
    assert resp.status_code == 422


def test_settings_requires_auth():
    """GET and PUT /api/settings/keys without a session cookie return 401."""
    import app.keys as keys_mod

    keys_mod._fernet = None

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/settings/keys").status_code == 401
        assert (
            c.put("/api/settings/keys/anthropic", json={"value": "x"}).status_code
            == 401
        )
