"""LLM access with per-user keys.

Each user can store their own OpenAI key (encrypted at rest) — agents run on the key
of whoever triggered them, so each friend pays for their own usage. A server-wide
OPENAI_API_KEY in .env acts as an optional shared fallback."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models import User


def resolve_openai_key(db: Session, user_id: int) -> tuple[str, str]:
    """Returns (api_key, source) where source is 'personal' | 'shared' | ''."""
    user = db.get(User, user_id)
    if user and user.openai_api_key_enc:
        key = decrypt_secret(user.openai_api_key_enc)
        if key:
            return key, "personal"
    shared = get_settings().openai_api_key
    if shared:
        return shared, "shared"
    return "", ""


def llm_available(db: Session, user_id: int) -> bool:
    return bool(resolve_openai_key(db, user_id)[0])


def require_llm(db: Session, user_id: int) -> str:
    key, _ = resolve_openai_key(db, user_id)
    if not key:
        raise HTTPException(503, "No OpenAI key for your account — add yours in Settings "
                                 "(each user brings their own key). Everything except the "
                                 "AI agents works without it.")
    return key


def get_chat_model(api_key: str, temperature: float = 0.2):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(model=s.openai_model, api_key=api_key, temperature=temperature)


def usage_from(message) -> tuple[int, int]:
    meta = getattr(message, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
