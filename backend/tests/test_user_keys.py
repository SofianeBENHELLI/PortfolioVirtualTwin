"""Per-user OpenAI keys: encryption at rest, resolution order, endpoint behavior."""
import pytest
from fastapi.testclient import TestClient

from app.core.crypto import decrypt_secret, encrypt_secret


@pytest.fixture()
def client(db):
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "k@example.com", "password": "password1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_crypto_roundtrip():
    assert decrypt_secret(encrypt_secret("sk-test-1234567890abcdef")) == "sk-test-1234567890abcdef"
    assert decrypt_secret("") is None
    assert decrypt_secret("not-a-token") is None


def test_set_key_masked_and_encrypted(client, auth, db):
    me = client.get("/api/auth/me", headers=auth).json()
    assert me["has_personal_key"] is False and me["llm_available"] is False

    r = client.put("/api/auth/me/openai-key",
                   json={"api_key": "sk-proj-abcdefghijklmnop1234"}, headers=auth)
    body = r.json()
    assert body["has_personal_key"] is True
    assert body["personal_key_hint"] == "…1234"
    assert body["llm_available"] is True and body["key_source"] == "personal"

    # stored encrypted, never in plaintext
    from app.models import User
    user = db.query(User).filter_by(email="k@example.com").one()
    assert "sk-proj" not in user.openai_api_key_enc
    assert decrypt_secret(user.openai_api_key_enc) == "sk-proj-abcdefghijklmnop1234"

    # full key never returned by the API
    me = client.get("/api/auth/me", headers=auth).json()
    assert "sk-proj" not in str(me)


def test_clear_key(client, auth):
    client.put("/api/auth/me/openai-key", json={"api_key": "sk-proj-abcdefghijklmnop1234"}, headers=auth)
    r = client.put("/api/auth/me/openai-key", json={"api_key": ""}, headers=auth)
    assert r.json()["has_personal_key"] is False and r.json()["llm_available"] is False


def test_invalid_key_rejected(client, auth):
    assert client.put("/api/auth/me/openai-key", json={"api_key": "short"}, headers=auth).status_code == 422


def test_resolution_personal_over_shared(client, auth, db, monkeypatch):
    from app.agents.llm import resolve_openai_key
    from app.core.config import get_settings
    from app.models import User
    user = db.query(User).filter_by(email="k@example.com").one()

    # no keys at all
    assert resolve_openai_key(db, user.id) == ("", "")
    # shared fallback
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-shared-key-000000000")
    assert resolve_openai_key(db, user.id) == ("sk-shared-key-000000000", "shared")
    # personal wins over shared
    client.put("/api/auth/me/openai-key", json={"api_key": "sk-personal-key-11111111"}, headers=auth)
    db.expire_all()
    assert resolve_openai_key(db, user.id) == ("sk-personal-key-11111111", "personal")
    monkeypatch.setattr(get_settings(), "openai_api_key", "")


def test_agent_503_mentions_settings(client, auth):
    r = client.post("/api/agents/capture", json={"description": "momentum"}, headers=auth)
    assert r.status_code == 503 and "Settings" in r.json()["detail"]
