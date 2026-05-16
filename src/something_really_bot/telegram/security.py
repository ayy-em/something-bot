"""Telegram webhook secret-header validation (SPEC §6.2).

Telegram echoes the value configured via ``setWebhook`` in the
``X-Telegram-Bot-Api-Secret-Token`` header on every webhook delivery. We
compare it against the value in :class:`Settings`. Mismatches are rejected
before any payload parsing happens.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from something_really_bot.config import Settings, get_settings

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def verify_telegram_webhook_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> None:
    """Reject requests without a valid Telegram webhook secret header.

    Args:
        settings: Application settings, injected by FastAPI.
        x_telegram_bot_api_secret_token: Header value sent by Telegram. FastAPI
            translates the underscore-cased parameter name to the dashed
            header automatically.

    Raises:
        HTTPException: 401 if the header is absent; 403 if it does not match
            the configured secret.
    """
    if x_telegram_bot_api_secret_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram webhook secret header.",
        )

    expected = settings.telegram_webhook_secret.get_secret_value()
    if not secrets.compare_digest(x_telegram_bot_api_secret_token, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret.",
        )
