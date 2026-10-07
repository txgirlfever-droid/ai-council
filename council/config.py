"""
Application settings — loaded from environment / .env file.
API keys NEVER stored in DB; only here via env vars.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_group_id: int = 0
    ceo_telegram_user_id: int = 0

    # Database
    database_url: str = "postgresql://council:secret@localhost:5432/ai_council"

    # AI Providers (keys only in env — never in DB)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""

    # Agent models (configurable; Role ≠ Model principle)
    codex_model_normal: str = "gpt-4o"
    codex_model_escalated: str = "o1"
    claude_model_normal: str = "claude-sonnet-4-6"
    claude_model_escalated: str = "claude-opus-4-5"
    gemini_model_normal: str = "gemini-1.5-pro"
    gemini_model_escalated: str = "gemini-1.5-ultra"

    # Timezone — N4 LOCKED
    ceo_timezone: str = "Asia/Ho_Chi_Minh"

    # Cost defaults
    default_soft_limit_usd: float = 2.00
    default_hard_limit_usd: float = 5.00

    # Worker
    worker_lease_seconds: int = 300
    worker_heartbeat_seconds: int = 30
    worker_concurrency: int = 4

    # Webhook
    webhook_url: str = ""
    webhook_port: int = 8000
    webhook_secret: str = ""


settings = Settings()
