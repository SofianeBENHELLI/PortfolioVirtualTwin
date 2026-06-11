from fastapi import HTTPException

from app.core.config import get_settings


def llm_available() -> bool:
    return bool(get_settings().openai_api_key)


def require_llm() -> None:
    if not llm_available():
        raise HTTPException(503, "OPENAI_API_KEY not configured — agent features are disabled. "
                                 "Everything else (backtests, paper trading, dashboards) works without it.")


def get_chat_model(temperature: float = 0.2):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=temperature)


def usage_from(message) -> tuple[int, int]:
    meta = getattr(message, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
