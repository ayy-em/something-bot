"""Centralised configuration loaded from environment variables.

The single :class:`Settings` instance is the authoritative source for any
config the app needs. Construction happens at startup via ``get_settings``;
required values that are missing raise :class:`pydantic.ValidationError` so
the service fails fast instead of starting in a half-configured state
(SPEC §9).

Only ``TELEGRAM_WEBHOOK_SECRET`` is currently required. Other fields are
declared ahead of the issues that wire them up so the shape is stable and
future commits add behaviour, not config knobs.
"""

from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core GCP / project ---
    gcp_project_id: str = "something-bot-338300"

    # --- Telegram bot config (single-bot for now; multi-bot in SPEC §16) ---
    telegram_webhook_secret: SecretStr = Field(
        description="Shared secret echoed by Telegram in X-Telegram-Bot-Api-Secret-Token."
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        description="Telegram bot token; wired up alongside the send-message client in #15.",
    )
    telegram_qa_user_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        description="Allowlisted Telegram user IDs; populated when #15 lands.",
    )

    # --- OpenAI / future feature secrets ---
    openai_api_key: SecretStr | None = Field(
        default=None,
        description="Wired up by the OpenAI fallback handler in #23.",
    )

    # --- BigQuery / GCS (#18, #20) ---
    bigquery_dataset: str | None = None
    gcs_bucket: str | None = None

    @field_validator("telegram_qa_user_ids", mode="before")
    @classmethod
    def _split_comma_separated(cls, raw: Any) -> Any:
        """Allow ``TELEGRAM_QA_USER_IDS=123,456`` in env files / Cloud Run env."""
        if isinstance(raw, str):
            return [int(part.strip()) for part in raw.split(",") if part.strip()]
        return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance.

    Cached so dependency injection across many requests doesn't re-parse the
    environment. Tests that need a fresh instance can call
    ``get_settings.cache_clear()``.

    Returns:
        The application settings.
    """
    return Settings()
