"""Minimal async Telegram Bot API client.

Centralises outbound calls so handlers never construct HTTP requests
themselves; this keeps the bot token in exactly one place and makes tests
trivially mockable. Only ``sendMessage`` is implemented for #15; ``getFile``
and file download arrive with #20.

Bot token is held as :class:`SecretStr` and only unwrapped inside the URL
that goes to ``api.telegram.org``. The token never appears in log records.
"""

from functools import lru_cache
from typing import Any

import httpx
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)


class TelegramSendError(Exception):
    """Raised when Telegram returns a non-OK response from sendMessage."""


class TelegramClient:
    """Async client for the Telegram Bot API."""

    def __init__(
        self,
        bot_token: SecretStr,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = TELEGRAM_API_BASE,
    ) -> None:
        self._token = bot_token
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        """POST ``sendMessage`` and return the decoded ``result`` field.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}

        if self._http is not None:
            response = await self._http.post(url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                response = await client.post(url, json=payload)

        if response.status_code >= 400:
            _logger.warning(
                "telegram_send_http_error",
                extra={"status": response.status_code, "chat_id": chat_id},
            )
            raise TelegramSendError(f"sendMessage HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            _logger.warning(
                "telegram_send_not_ok",
                extra={"chat_id": chat_id, "description": body.get("description")},
            )
            raise TelegramSendError(f"sendMessage not ok: {body.get('description')!r}")

        return body.get("result", {})


@lru_cache(maxsize=1)
def get_telegram_client() -> TelegramClient:
    """Return the process-wide :class:`TelegramClient`.

    Cached because Cloud Run instances are long-lived enough that re-building
    the client per request is wasteful. Tests can clear the cache via
    ``get_telegram_client.cache_clear()``.
    """
    settings = get_settings()
    return TelegramClient(bot_token=settings.telegram_bot_token)
