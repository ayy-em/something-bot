"""Minimal async Telegram Bot API client.

Centralises outbound calls so handlers never construct HTTP requests
themselves; this keeps the bot token in exactly one place and makes tests
trivially mockable. Implements ``sendMessage`` (#15), ``getFile`` + file
download (#20), ``sendVideo`` + ``setMessageReaction`` (#42).

Bot token is held as :class:`SecretStr` and only unwrapped inside the URL
that goes to ``api.telegram.org``. The token never appears in log records.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)
# sendVideo can take a while for 30–50 MB uploads; the default 10s read
# timeout would 504 us before Telegram acks.
_UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=10.0)


class TelegramSendError(Exception):
    """Raised when Telegram returns a non-OK response from sendMessage."""


class TelegramFileError(Exception):
    """Raised when getFile / file download cannot complete successfully."""


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

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """POST ``sendMessage`` and return the decoded ``result`` field.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if disable_notification:
            payload["disable_notification"] = True
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        response = await self._request("POST", url, json=payload)

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

    async def send_video(
        self,
        chat_id: int,
        video_path: Path,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        duration_seconds: int | None = None,
        width: int | None = None,
        height: int | None = None,
        supports_streaming: bool = True,
    ) -> dict[str, Any]:
        """POST ``sendVideo`` with a local file as multipart upload.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/sendVideo"
        data: dict[str, Any] = {
            "chat_id": str(chat_id),
            "supports_streaming": "true" if supports_streaming else "false",
        }
        if caption is not None:
            data["caption"] = caption
        if reply_to_message_id is not None:
            data["reply_parameters"] = json.dumps(
                {
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                }
            )
        if duration_seconds is not None:
            data["duration"] = str(duration_seconds)
        if width is not None:
            data["width"] = str(width)
        if height is not None:
            data["height"] = str(height)

        with open(video_path, "rb") as fp:
            files = {"video": (video_path.name, fp, "video/mp4")}
            response = await self._request(
                "POST",
                url,
                data=data,
                files=files,
                timeout=_UPLOAD_TIMEOUT,
            )

        if response.status_code >= 400:
            _logger.warning(
                "telegram_send_video_http_error",
                extra={"status": response.status_code, "chat_id": chat_id},
            )
            raise TelegramSendError(f"sendVideo HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            _logger.warning(
                "telegram_send_video_not_ok",
                extra={"chat_id": chat_id, "description": body.get("description")},
            )
            raise TelegramSendError(f"sendVideo not ok: {body.get('description')!r}")

        return body.get("result", {})

    async def send_document(
        self,
        chat_id: int,
        document_bytes: bytes,
        *,
        filename: str,
        mime_type: str = "application/octet-stream",
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """POST ``sendDocument`` with raw bytes as multipart upload.

        Used by features that need Telegram to deliver a file without
        re-compressing it (sticker-ready PNGs, raw audio attachments).

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/sendDocument"
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        if reply_to_message_id is not None:
            data["reply_parameters"] = json.dumps(
                {
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                }
            )

        files = {"document": (filename, document_bytes, mime_type)}
        response = await self._request(
            "POST",
            url,
            data=data,
            files=files,
            timeout=_UPLOAD_TIMEOUT,
        )

        if response.status_code >= 400:
            _logger.warning(
                "telegram_send_document_http_error",
                extra={"status": response.status_code, "chat_id": chat_id},
            )
            raise TelegramSendError(f"sendDocument HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            _logger.warning(
                "telegram_send_document_not_ok",
                extra={"chat_id": chat_id, "description": body.get("description")},
            )
            raise TelegramSendError(f"sendDocument not ok: {body.get('description')!r}")

        return body.get("result", {})

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """POST ``editMessageText`` to overwrite an existing bot message.

        Used when we want to replace a "working on it…" ack with the real
        result instead of posting a second message.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        response = await self._request("POST", url, json=payload)

        if response.status_code >= 400:
            _logger.warning(
                "telegram_edit_http_error",
                extra={
                    "status": response.status_code,
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )
            raise TelegramSendError(f"editMessageText HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            _logger.warning(
                "telegram_edit_not_ok",
                extra={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "description": body.get("description"),
                },
            )
            raise TelegramSendError(f"editMessageText not ok: {body.get('description')!r}")

        return body.get("result", {})

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> dict[str, Any]:
        """POST ``deleteMessage``. Removes a message the bot can act on.

        Used to take down an in-flight "processing…" ack right before
        replacing it with media (e.g. the downloaded video). Telegram
        forbids editing a text message into media, so the only way to
        keep a single bot-message footprint is delete + send.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/deleteMessage"
        payload = {"chat_id": chat_id, "message_id": message_id}
        response = await self._request("POST", url, json=payload)

        if response.status_code >= 400:
            _logger.warning(
                "telegram_delete_http_error",
                extra={
                    "status": response.status_code,
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )
            raise TelegramSendError(f"deleteMessage HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            _logger.warning(
                "telegram_delete_not_ok",
                extra={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "description": body.get("description"),
                },
            )
            raise TelegramSendError(f"deleteMessage not ok: {body.get('description')!r}")

        return body

    async def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> dict[str, Any]:
        """POST ``setMessageReaction`` with a single emoji.

        Raises:
            TelegramSendError: HTTP error or ``ok=false`` in the response.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/setMessageReaction"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        }
        response = await self._request("POST", url, json=payload)

        if response.status_code >= 400:
            raise TelegramSendError(f"setMessageReaction HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            raise TelegramSendError(f"setMessageReaction not ok: {body.get('description')!r}")
        return body

    async def set_my_commands(
        self,
        commands: list[dict[str, str]],
    ) -> dict[str, Any]:
        """POST ``setMyCommands`` to register the bot's autocomplete menu.

        Args:
            commands: List of ``{"command": "...", "description": "..."}``
                dicts.  The ``command`` value must not include a leading ``/``.

        Returns:
            The Telegram API result on success.

        Raises:
            TelegramSendError: Telegram returned ``ok=false``.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/setMyCommands"
        payload: dict[str, Any] = {"commands": commands}
        response = await self._request("POST", url, json=payload)
        body = response.json()
        if not body.get("ok"):
            raise TelegramSendError(f"setMyCommands not ok: {body.get('description')!r}")
        return body.get("result", {})

    async def get_webhook_info(self) -> dict[str, Any]:
        """GET ``getWebhookInfo`` and return the ``result`` dict.

        Returns:
            A dict with keys like ``url``, ``has_custom_certificate``,
            ``pending_update_count``, etc.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/getWebhookInfo"
        response = await self._request("GET", url)
        body = response.json()
        return body.get("result", {})

    async def set_webhook(
        self,
        webhook_url: str,
        *,
        secret_token: str | None = None,
    ) -> dict[str, Any]:
        """POST ``setWebhook`` to point the bot at ``webhook_url``.

        Args:
            webhook_url: Full HTTPS URL Telegram should POST updates to.
            secret_token: Optional secret for the X-Telegram-Bot-Api-Secret-Token header.

        Returns:
            The Telegram API result on success.

        Raises:
            TelegramSendError: Telegram returned ``ok=false``.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/setWebhook"
        payload: dict[str, Any] = {"url": webhook_url}
        if secret_token is not None:
            payload["secret_token"] = secret_token
        response = await self._request("POST", url, json=payload)
        body = response.json()
        if not body.get("ok"):
            raise TelegramSendError(f"setWebhook not ok: {body.get('description')!r}")
        return body.get("result", {})

    async def get_file_path(self, file_id: str) -> str:
        """Resolve a ``file_id`` to the relative ``file_path`` Telegram uses
        for downloads.

        Raises:
            TelegramFileError: getFile failed or returned no path.
        """
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/getFile"
        response = await self._request("POST", url, json={"file_id": file_id})

        if response.status_code >= 400:
            _logger.warning(
                "telegram_get_file_http_error",
                extra={"status": response.status_code, "file_id": file_id},
            )
            raise TelegramFileError(f"getFile HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            raise TelegramFileError(f"getFile not ok: {body.get('description')!r}")

        file_path = (body.get("result") or {}).get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise TelegramFileError("getFile result missing file_path")
        return file_path

    async def download_file(self, file_path: str) -> bytes:
        """Download the file body referenced by ``file_path`` (relative path
        returned from :meth:`get_file_path`).

        Raises:
            TelegramFileError: download failed.
        """
        url = f"{self._base_url}/file/bot{self._token.get_secret_value()}/{file_path}"
        response = await self._request("GET", url)

        if response.status_code >= 400:
            _logger.warning(
                "telegram_download_http_error",
                extra={"status": response.status_code, "file_path": file_path},
            )
            raise TelegramFileError(f"download HTTP {response.status_code}")

        return response.content

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._http is not None:
            return await self._http.request(method, url, **kwargs)
        timeout = kwargs.pop("timeout", _DEFAULT_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, **kwargs)


@lru_cache(maxsize=1)
def get_telegram_client() -> TelegramClient:
    """Return the process-wide :class:`TelegramClient`.

    Cached because Cloud Run instances are long-lived enough that re-building
    the client per request is wasteful. Tests can clear the cache via
    ``get_telegram_client.cache_clear()``.
    """
    settings = get_settings()
    return TelegramClient(bot_token=settings.telegram_bot_token)
