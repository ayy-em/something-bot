"""``/ocr`` — image OCR via OpenAI vision (#45).

Two-turn command, private chat only (same constraint as #44).

1. ``/ocr`` → set pending action, prompt for an image.
2. User uploads photo → download, store in GCS under ``ocr_requests/``,
   run OpenAI vision OCR, reply with the extracted text (or
   ``NO_TEXT`` translated into a friendly message).
"""

import asyncio
import html
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.features.ocr.ocr_client import (
    OCRClient,
    OCRError,
    get_ocr_client,
)
from something_really_bot.file_storage.gcs import GCSStorage, get_gcs_storage
from something_really_bot.logging import get_logger
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.services.pending_actions import (
    PendingActionStore,
    get_pending_action_store,
)
from something_really_bot.telegram.client import (
    TelegramClient,
    TelegramFileError,
    TelegramSendError,
    get_telegram_client,
)
from something_really_bot.telegram.models import (
    CommandContent,
    ParsedUpdate,
    PhotoContent,
    PrivateMessage,
)

_logger = get_logger(__name__)

COMMAND_NAME = "/ocr"
PROMPT_TEXT = "Sure! Send me an image and I'll extract the text from it."
REPLY_PARSE_MODE = "HTML"

_REPLY_TEMPLATE = "<i>{text}</i>"
_NO_TEXT_REPLY = "I couldn't find any readable text in that image."

_ERROR_DOWNLOAD_FAILED = "Couldn't pull that image from Telegram. Try sending it again in a moment."
_ERROR_OCR_FAILED = (
    "Couldn't read text from that image. The OCR service might be having a "
    "moment — try again shortly."
)
_ERROR_OCR_UNAVAILABLE = "OCR isn't configured right now. Logged for review."
_ERROR_GENERIC = "Something went wrong handling that image. Logged."

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]


@dataclass(frozen=True)
class _BackgroundContext:
    bot_id: str
    chat_id: int
    user_id: int
    trigger_message_id: int
    photo_file_id: str
    photo_file_unique_id: str
    telegram_client: TelegramClient
    gcs_storage: GCSStorage
    ocr_client: OCRClient | None
    persistence_record_event: Callable[[EventRecord], None] | None


class OCRHandler:
    """``/ocr`` command + follow-up photo routing."""

    name = "ocr"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        ocr_client_factory: Callable[[], OCRClient | None] = get_ocr_client,
        pending_action_store_factory: Callable[
            [], PendingActionStore | None
        ] = get_pending_action_store,
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
        self._ocr_factory = ocr_client_factory
        self._pending_factory = pending_action_store_factory

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        content = update.content
        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            return True
        if isinstance(content, PhotoContent):
            pending = ctx.pending_action
            return pending is not None and pending.command == COMMAND_NAME
        return False

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        content = update.content

        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            return await self._prompt(update, ctx)

        assert isinstance(content, PhotoContent)
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.clear(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("ocr_clear_pending_failed")

        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text="Reading the text…",
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("ocr_ack_failed", extra={"chat_id": update.chat_id})

        largest = max(content.photo, key=lambda p: p.file_size or 0)
        bg = _BackgroundContext(
            bot_id=ctx.bot_id,
            chat_id=update.chat_id,
            user_id=update.from_user.id,
            trigger_message_id=update.message_id,
            photo_file_id=largest.file_id,
            photo_file_unique_id=largest.file_unique_id,
            telegram_client=telegram_client,
            gcs_storage=self._gcs_factory(),
            ocr_client=self._ocr_factory(),
            persistence_record_event=(
                ctx.persistence.record_event if ctx.persistence is not None else None
            ),
        )
        self._schedule(_run_background(bg))
        return HandlerResult(handled=True, handler_name=self.name)

    async def _prompt(self, update: PrivateMessage, ctx: BotContext) -> HandlerResult:
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.set(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                    command=COMMAND_NAME,
                    expected_input="image",
                )
            except Exception:  # noqa: BLE001
                _logger.exception("ocr_set_pending_failed")
        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=PROMPT_TEXT,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("ocr_prompt_send_failed", extra={"chat_id": update.chat_id})
        return HandlerResult(handled=True, handler_name=self.name)


async def _run_background(ctx: _BackgroundContext) -> None:
    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    image_bytes: bytes | None = None
    text: str | None = None
    received_at = datetime.now(UTC)

    if ctx.ocr_client is None:
        user_facing_error = _ERROR_OCR_UNAVAILABLE
        error_class = "OCRUnavailable"
        error_message = "OPENAI_API_KEY not configured"

    if user_facing_error is None:
        try:
            file_path = await ctx.telegram_client.get_file_path(ctx.photo_file_id)
            image_bytes = await ctx.telegram_client.download_file(file_path)
        except TelegramFileError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_DOWNLOAD_FAILED

    object_key: str | None = None
    if user_facing_error is None and image_bytes is not None:
        object_key = (
            f"ocr_requests/{ctx.chat_id}/{ctx.trigger_message_id}/image_{ctx.photo_file_unique_id}"
        )
        try:
            await ctx.gcs_storage.upload(
                object_key=object_key,
                data=image_bytes,
                content_type="image/jpeg",
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and image_bytes is not None:
        assert ctx.ocr_client is not None
        try:
            text = await ctx.ocr_client.extract_text(image_bytes, mime_type="image/jpeg")
        except OCRError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_OCR_FAILED

    if user_facing_error is None and text is not None:
        if text.strip() == "NO_TEXT":
            reply_text = _NO_TEXT_REPLY
            parse_mode: str | None = None
        else:
            reply_text = _REPLY_TEMPLATE.format(text=html.escape(text))
            parse_mode = REPLY_PARSE_MODE
        try:
            await ctx.telegram_client.send_message(
                chat_id=ctx.chat_id,
                text=reply_text,
                reply_to_message_id=ctx.trigger_message_id,
                parse_mode=parse_mode,
            )
        except TelegramSendError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None:
        _record_event(
            ctx,
            event="ocr_succeeded",
            status="success",
            details=f"{object_key} {len(text or '')}chars",
            occurred_at=received_at,
        )
        return

    try:
        await ctx.telegram_client.send_message(
            chat_id=ctx.chat_id,
            text=user_facing_error,
            reply_to_message_id=ctx.trigger_message_id,
        )
    except TelegramSendError:
        _logger.warning("ocr_user_error_send_failed", extra={"chat_id": ctx.chat_id})
    _record_event(
        ctx,
        event="ocr_failed",
        status="error",
        details=f"{error_class}: {error_message}",
        occurred_at=datetime.now(UTC),
    )


def _record_event(
    ctx: _BackgroundContext,
    *,
    event: str,
    status: str,
    details: str,
    occurred_at: datetime,
) -> None:
    if ctx.persistence_record_event is None:
        return
    try:
        ctx.persistence_record_event(
            EventRecord(
                bot_id=ctx.bot_id,
                event=event,
                status=status,
                details=details,
                occurred_at=occurred_at,
            )
        )
    except Exception:  # noqa: BLE001
        _logger.exception("ocr_record_event_failed")


@lru_cache(maxsize=1)
def get_ocr_handler() -> OCRHandler:
    """Process-wide singleton."""
    return OCRHandler()
