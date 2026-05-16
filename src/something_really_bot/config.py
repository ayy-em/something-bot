"""Centralised configuration loaded from environment variables.

The single :class:`Settings` instance is the authoritative source for any
config the app needs. Construction happens at startup via ``get_settings``;
required values that are missing raise :class:`pydantic.ValidationError` so
the service fails fast instead of starting in a half-configured state
(SPEC §9).
"""

import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

# Keys inside the ``telegram-qa-users`` JSON secret whose values are
# *authorized QA Telegram user IDs*. Other keys in that secret (chat/group/
# channel targets used by legacy cron jobs) must NOT be allowlisted.
QA_USER_ID_KEYS: tuple[str, ...] = ("JM_TG_ID", "IRINDICA_CHAT_ID")


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
    telegram_bot_token: SecretStr = Field(
        description="Telegram bot token used by TelegramClient.send_message."
    )
    telegram_qa_user_ids: Annotated[frozenset[int], NoDecode] = Field(
        default_factory=frozenset,
        validation_alias="TELEGRAM_QA_USERS",
        description=(
            "Authorized QA user IDs, parsed from the telegram-qa-users JSON "
            "secret. Only the JM_TG_ID and IRINDICA_CHAT_ID keys are extracted; "
            "other keys in that secret (group/channel chat IDs) are intentionally "
            "ignored — they are routing targets, not QA users."
        ),
    )

    # --- OpenAI / future feature secrets ---
    openai_api_key: SecretStr | None = Field(
        default=None,
        description="Wired up by the OpenAI fallback handler in #23.",
    )

    # --- BigQuery / GCS (#18, #20) ---
    bigquery_dataset: str = Field(
        default="something_bot",
        description="BigQuery dataset for persistence (RFC #17 / decision 0001).",
    )
    gcs_bucket: str = Field(
        default="something-bot-telegram-files",
        description="GCS bucket for Telegram-uploaded files (RFC #19 / decision 0002).",
    )

    @field_validator("telegram_qa_user_ids", mode="before")
    @classmethod
    def _parse_qa_users(cls, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _logger.warning("telegram_qa_users_malformed_json")
            return frozenset()
        if not isinstance(payload, dict):
            _logger.warning(
                "telegram_qa_users_not_a_dict", extra={"actual_type": type(payload).__name__}
            )
            return frozenset()

        ids: set[int] = set()
        for key in QA_USER_ID_KEYS:
            if key not in payload:
                _logger.warning("telegram_qa_users_missing_key", extra={"key": key})
                continue
            try:
                ids.add(int(payload[key]))
            except (TypeError, ValueError):
                _logger.warning("telegram_qa_users_unparseable_value", extra={"key": key})
        return frozenset(ids)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance.

    Cached so dependency injection across many requests doesn't re-parse the
    environment. Tests that need a fresh instance can call
    ``get_settings.cache_clear()``.
    """
    return Settings()
