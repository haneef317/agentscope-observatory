"""
Application configuration.

All settings are read from environment variables with sensible defaults so the
platform works out of the box with `docker compose up` (Postgres + Redis
defaults) and also falls back to a fully self-contained demo mode when the
databases are unavailable.
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env file lives in the project root (one level up from backend/).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = os.environ.get("AGENTSCOPE_ENV_FILE", str(_PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )

    # ------------------------------------------------------------------ API
    APP_NAME: str = "AgentScope Observatory"
    DEBUG: bool = False
    API_PREFIX: str = "/api"

    # ------------------------------------------------------------ Database
    DATABASE_URL: str = "postgresql+psycopg://agentscope:agentscope@localhost:5432/agentscope"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10

    # ---------------------------------------------------------------- Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CHANNEL_PREFIX: str = "run"

    # --------------------------------------------------- LLM (real agent)
    # Optional. When unset the platform runs in SIMULATOR mode: the demo agent
    # still executes, emits full traces, tokens and costs, but responses are
    # generated locally so the platform is demoable without an API key.
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE: str | None = None  # e.g. an OpenAI-compatible proxy
    DEFAULT_MODEL: str = "gpt-4o-mini"

    # ------------------------------------------------------- Frontend build
    FRONTEND_DIR: str = "frontend/dist"

    @property
    def real_llm_enabled(self) -> bool:
        return bool(self.OPENAI_API_KEY)


settings = Settings()
