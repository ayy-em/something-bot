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
    irindica_chat_id: Annotated[int | None, NoDecode] = Field(
        default=None,
        validation_alias="TELEGRAM_QA_USERS",
        description=(
            "Irindica's private chat_id, extracted from the IRINDICA_CHAT_ID key "
            "of the telegram-qa-users JSON secret. Used by the Friday TikTok "
            "reminder job (#24). ``None`` if the key is absent or malformed."
        ),
    )

    # --- OpenAI (#23) ---
    openai_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "OpenAI API key used by the fallback chat handler (#23). When "
            "unset, OpenAIFallbackHandler replies with a deterministic "
            "apology message instead of calling the API."
        ),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI chat model used by the fallback handler.",
    )

    # --- Feature flags ---
    hello_world_mode: bool = Field(
        default=False,
        description=(
            "When True, the Hello World/parrot handler from #15 supersedes the "
            "OpenAI fallback for QA private text. Default False — OpenAI wins."
        ),
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

    # --- Cloud Scheduler (#22) ---
    scheduler_service_account_email: str | None = Field(
        default=None,
        description=(
            "OIDC token issuer for incoming /jobs/<name> calls. Required for the "
            "scheduled-jobs endpoint to accept requests; if unset, /jobs/* always 401."
        ),
    )

    # --- Shared Postgres (#31) ---
    postgres_dsn: SecretStr | None = Field(
        default=None,
        description=(
            "Connection string for the shared Cloud SQL Postgres instance hosting "
            "the something-bot schema (#31). When unset, the Postgres wrapper "
            "is not built and any call site that depends on it gracefully "
            "no-ops. Format: postgres://<user>:<pass>@<host>:5432/<db>?sslmode=require "
            "(or the Cloud SQL Auth Proxy unix socket form: "
            "postgres://<user>:<pass>@/<db>?host=/cloudsql/<project>:<region>:<instance>)."
        ),
    )
    postgres_schema: str = Field(
        default="something_bot",
        description=(
            "Postgres schema the bot writes to inside the shared DB (#31). Created "
            "on first connection if missing (CREATE SCHEMA IF NOT EXISTS)."
        ),
    )

    @field_validator("telegram_qa_user_ids", mode="before")
    @classmethod
    def _parse_qa_users(cls, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        payload = _parse_qa_users_payload(raw)
        if payload is None:
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

    @field_validator("irindica_chat_id", mode="before")
    @classmethod
    def _parse_irindica_chat_id(cls, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        payload = _parse_qa_users_payload(raw)
        if payload is None:
            return None
        value = payload.get("IRINDICA_CHAT_ID")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _parse_qa_users_payload(raw: str) -> dict[str, Any] | None:
    """Decode the ``telegram-qa-users`` JSON secret into a dict.

    Returns ``None`` on any structural problem; specific field validators
    log the warning so we don't double-log on every Settings construction.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _logger.warning("telegram_qa_users_malformed_json")
        return None
    if not isinstance(payload, dict):
        _logger.warning(
            "telegram_qa_users_not_a_dict", extra={"actual_type": type(payload).__name__}
        )
        return None
    return payload


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance.

    Cached so dependency injection across many requests doesn't re-parse the
    environment. Tests that need a fresh instance can call
    ``get_settings.cache_clear()``.
    """
    return Settings()
