"""Direct unit tests for :mod:`something_really_bot.telegram.security`."""

import pytest
from fastapi import HTTPException

from something_really_bot.config import Settings
from something_really_bot.telegram.security import verify_telegram_webhook_secret


def _settings(secret: str = "expected") -> Settings:
    return Settings(_env_file=None, telegram_webhook_secret=secret)


def test_verify_passes_when_header_matches() -> None:
    """No exception when the header value equals the configured secret."""
    verify_telegram_webhook_secret(
        settings=_settings("expected"),
        x_telegram_bot_api_secret_token="expected",
    )


def test_verify_raises_401_when_header_missing() -> None:
    """Missing header → 401."""
    with pytest.raises(HTTPException) as exc:
        verify_telegram_webhook_secret(
            settings=_settings(),
            x_telegram_bot_api_secret_token=None,
        )

    assert exc.value.status_code == 401


def test_verify_raises_403_when_header_mismatches() -> None:
    """Wrong header value → 403."""
    with pytest.raises(HTTPException) as exc:
        verify_telegram_webhook_secret(
            settings=_settings("expected"),
            x_telegram_bot_api_secret_token="something-else",
        )

    assert exc.value.status_code == 403
