"""``/make-sticker`` — image → sticker-ready PNG (#44).

Two-turn flow (private chat only — group photo uploads aren't tied
back to the originating user reliably enough for the pending-action
slot):

1. User sends ``/make-sticker``. Bot sets a pending action and asks for
   the image.
2. User sends a photo. Handler downloads it, stores the original in
   GCS under ``sticker_requests/``, runs the Pillow transform, stores
   the output in GCS under ``sticker_outputs/``, and replies with the
   PNG as a sendDocument (no Telegram re-compression).
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.features.make_sticker.transform import (
    StickerTransformError,
    transform,
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

COMMAND_NAME = "make-sticker"
PROMPT_TEXT = "Send me an image and I'll turn it into a Telegram sticker (within 10 minutes)."

_ERROR_NOT_AN_IMAGE = "That doesn't look like a photo. Send an image as a photo and I'll try again."
_ERROR_DOWNLOAD_FAILED = "Couldn't pull that image from Telegram. Try sending it again in a moment."
_ERROR_TRANSFORM_FAILED = (
    "Couldn't turn that into a sticker. The image might be corrupted or in an "
    "unusual format. Try a different one."
)
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
    persistence_record_event: Callable[[EventRecord], None] | None


class MakeStickerHandler:
    """``/make-sticker`` command + follow-up photo routing."""

    name = "make_sticker"
    description = "/make-sticker — convert an image into a Telegram sticker-ready PNG."
    help_usage = "/make-sticker, then send a photo"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        pending_action_store_factory: Callable[
            [], PendingActionStore | None
        ] = get_pending_action_store,
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
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
        # Always clear the pending row before scheduling work — even on
        # a downstream failure, we don't want the user trapped.
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.clear(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("make_sticker_clear_pending_failed")

        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text="Working on your sticker…",
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("make_sticker_ack_failed", extra={"chat_id": update.chat_id})

        # Pick the largest variant Telegram offered.
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
                _logger.exception("make_sticker_set_pending_failed")
        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=PROMPT_TEXT,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("make_sticker_prompt_send_failed", extra={"chat_id": update.chat_id})
        return HandlerResult(handled=True, handler_name=self.name)


async def _run_background(ctx: _BackgroundContext) -> None:
    """Download → transform → upload (input + output) → sendDocument."""
    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    image_bytes: bytes | None = None
    sticker_bytes: bytes | None = None
    received_at = datetime.now(UTC)

    try:
        file_path = await ctx.telegram_client.get_file_path(ctx.photo_file_id)
        image_bytes = await ctx.telegram_client.download_file(file_path)
    except TelegramFileError as exc:
        error_class = type(exc).__name__
        error_message = str(exc)
        user_facing_error = _ERROR_DOWNLOAD_FAILED

    if user_facing_error is None and image_bytes is not None:
        # Store the original first; we want a trail of inputs even when
        # the transform later fails.
        input_key = (
            f"sticker_requests/{ctx.chat_id}/{ctx.trigger_message_id}/"
            f"input_{ctx.photo_file_unique_id}"
        )
        try:
            await ctx.gcs_storage.upload(
                object_key=input_key,
                data=image_bytes,
                content_type="image/jpeg",
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and image_bytes is not None:
        try:
            sticker = await transform(image_bytes)
            sticker_bytes = sticker.png_bytes
        except StickerTransformError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_TRANSFORM_FAILED

    output_key: str | None = None
    if user_facing_error is None and sticker_bytes is not None:
        output_key = (
            f"sticker_outputs/{ctx.chat_id}/{ctx.trigger_message_id}/"
            f"sticker_{ctx.photo_file_unique_id}.png"
        )
        try:
            await ctx.gcs_storage.upload(
                object_key=output_key,
                data=sticker_bytes,
                content_type="image/png",
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and sticker_bytes is not None:
        try:
            await ctx.telegram_client.send_document(
                chat_id=ctx.chat_id,
                document_bytes=sticker_bytes,
                filename=f"sticker_{ctx.photo_file_unique_id}.png",
                mime_type="image/png",
                reply_to_message_id=ctx.trigger_message_id,
            )
        except TelegramSendError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None:
        _record_event(
            ctx,
            event="make_sticker_succeeded",
            status="success",
            details=output_key or "",
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
        _logger.warning("make_sticker_user_error_send_failed", extra={"chat_id": ctx.chat_id})
    _record_event(
        ctx,
        event="make_sticker_failed",
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
        _logger.exception("make_sticker_record_event_failed")


@lru_cache(maxsize=1)
def get_make_sticker_handler() -> MakeStickerHandler:
    """Process-wide singleton, wired with production dependencies."""
    return MakeStickerHandler()
