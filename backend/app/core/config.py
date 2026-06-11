from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "PortfolioVirtualTwin"
    # SAFETY: the only mode that exists in MVP 1. Not exposed via API.
    trading_mode: str = "paper"

    database_url: str = f"sqlite:///{BACKEND_DIR / 'pvt.db'}"
    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    max_users: int = 10

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    agent_max_tokens_per_run: int = 60_000

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""

    market_data_provider: str = "yfinance"  # yfinance | alpaca
    quote_refresh_seconds: int = 60
    reports_dir: Path = BACKEND_DIR / "reports"

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.reports_dir.mkdir(exist_ok=True)
    return s
