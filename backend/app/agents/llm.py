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


def friendly_llm_error(error: str) -> str:
    """Translate raw OpenAI errors into actionable messages for the UI."""
    e = error.lower()
    if "insufficient_quota" in e or "exceeded your current quota" in e or "429" in e and "quota" in e:
        return ("Your OpenAI account has no API credit. A ChatGPT Plus subscription does NOT "
                "include API credits — add billing or prepaid credits at "
                "platform.openai.com → Settings → Billing, then retry. (OpenAI said: quota exceeded)")
    if "invalid_api_key" in e or "incorrect api key" in e or "401" in e:
        return ("OpenAI rejected your API key as invalid — re-copy it from "
                "platform.openai.com/api-keys and save it again in Settings.")
    if "model_not_found" in e or "does not have access to model" in e:
        return ("Your OpenAI project doesn't have access to the configured model — check the "
                "project of your key, or set OPENAI_MODEL to one you can use (e.g. gpt-4o-mini).")
    if "rate limit" in e or ("429" in e and "rate" in e):
        return "OpenAI rate limit hit — wait a minute and retry, or run fewer symbols at once."
    return error


def usage_from(message) -> tuple[int, int]:
    meta = getattr(message, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
