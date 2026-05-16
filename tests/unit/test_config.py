"""Tests for :mod:`something_really_bot.config`."""

import pytest
from pydantic import ValidationError

from something_really_bot.config import Settings, get_settings


def test_settings_loads_required_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Settings`` reads the webhook secret from the environment."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "from-env")

    settings = Settings(_env_file=None)

    assert settings.telegram_webhook_secret.get_secret_value() == "from-env"


def test_settings_fails_fast_when_webhook_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ``TELEGRAM_WEBHOOK_SECRET`` raises ``ValidationError``."""
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_parses_qa_user_ids_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated ``TELEGRAM_QA_USER_IDS`` is parsed into a list of ints."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_QA_USER_IDS", "111, 222 ,333")

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == [111, 222, 333]


def test_settings_qa_user_ids_default_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``TELEGRAM_QA_USER_IDS`` yields an empty list."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "x")
    monkeypatch.delenv("TELEGRAM_QA_USER_IDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == []


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns the same instance on repeated calls."""
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
