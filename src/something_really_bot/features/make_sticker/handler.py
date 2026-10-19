"""``/make_sticker`` — image → cut-out, outlined sticker PNG (#44).

Works in private chats, groups and supergroups. Three ways in:

* **Two-turn.** ``/make_sticker`` sets a pending action for that
  (chat, user) and asks for an image; the same user's next photo or
  image file in that chat is the input.
* **Caption.** A photo or image file captioned ``/make_sticker``.
* **Reply.** ``/make_sticker`` sent as a reply to a photo or image file.

Either way the handler downloads the image, stores the original in GCS
under ``sticker_requests/``, runs :func:`transform` (fit to 512 → rembg
→ white outline), stores the output under ``sticker_outputs/``, and
replies with the PNG as a sendDocument (no Telegram re-compression).
"""

import asyncio
import resource
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.features.make_sticker.transform import (
    BackgroundRemover,
    NoSubjectError,
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
    DocumentContent,
    GroupMessage,
    MessageContent,
    ParsedUpdate,
    PhotoContent,
    PrivateMessage,
    SupergroupMessage,
)

_logger = get_logger(__name__)

COMMAND_NAME = "/make_sticker"
PROMPT_TEXT = (
    "Sure! Send me an image and I'll cut it out into a sticker. Next time you "
    "can also caption a photo with /make_sticker, or reply /make_sticker to one."
)

_ERROR_DOWNLOAD_FAILED = "Couldn't pull that image from Telegram. Try sending it again in a moment."
_ERROR_TRANSFORM_FAILED = (
    "Couldn't turn that into a sticker. The image might be corrupted or in an "
    "unusual format. Try a different one."
)
_ERROR_NO_SUBJECT = "Couldn't find anything to cut out in that image. Try one with a clear subject."
_ERROR_GENERIC = "Something went wrong handling that image. Logged."

# Pillow can't rasterize SVG, so an "image/*" document isn't automatically
# something we can work with.
_UNSUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/svg+xml"})

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]
_SupportedMessage = PrivateMessage | GroupMessage | SupergroupMessage


@dataclass(frozen=True)
class _ImageRef:
    """The Telegram file to turn into a sticker."""

    file_id: str
    file_unique_id: str
    content_type: str


@dataclass(frozen=True)
class _BackgroundContext:
    bot_id: str
    chat_id: int
    user_id: int
    trigger_message_id: int
    image: _ImageRef
    telegram_client: TelegramClient
    gcs_storage: GCSStorage
    persistence_record_event: Callable[[EventRecord], None] | None
    background_remover: BackgroundRemover | None


class MakeStickerHandler:
    """``/make-sticker`` command + follow-up photo routing."""

    name = "make_sticker"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        pending_action_store_factory: Callable[
            [], PendingActionStore | None
        ] = get_pending_action_store,
        background_remover: BackgroundRemover | None = None,
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
        self._pending_factory = pending_action_store_factory
        self._background_remover = background_remover

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, _SupportedMessage):
            return False
        content = update.content
        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            return True
        return _resolve(update, ctx) is not None

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, _SupportedMessage)
        image = _resolve(update, ctx)
        if image is None:
            return await self._prompt(update, ctx)

        # Clear our pending row before scheduling work — even on a
        # downstream failure, we don't want the user trapped. Caption and
        # reply invocations usually have none, so skip the round-trip.
        pending = ctx.pending_action
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None and pending is not None and pending.command == COMMAND_NAME:
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

        bg = _BackgroundContext(
            bot_id=ctx.bot_id,
            chat_id=update.chat_id,
            user_id=update.from_user.id,
            trigger_message_id=update.message_id,
            image=image,
            telegram_client=telegram_client,
            gcs_storage=self._gcs_factory(),
            persistence_record_event=(
                ctx.persistence.record_event if ctx.persistence is not None else None
            ),
            background_remover=self._background_remover,
        )
        self._schedule(_run_background(bg))
        return HandlerResult(handled=True, handler_name=self.name)

    async def _prompt(self, update: _SupportedMessage, ctx: BotContext) -> HandlerResult:
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


def _resolve(update: _SupportedMessage, ctx: BotContext) -> _ImageRef | None:
    """Return the image this update asks us to process, if any.

    ``None`` covers both "not ours" and "bare ``/make_sticker``, prompt
    for an image"; :meth:`MakeStickerHandler.matches` tells them apart.
    """
    content = update.content
    if isinstance(content, CommandContent):
        if content.command != COMMAND_NAME:
            return None
        replied = update.reply_to
        return _image_ref(replied.content) if replied is not None else None

    image = _image_ref(content)
    if image is None:
        return None
    if _caption_command(content) == COMMAND_NAME:
        return image
    pending = ctx.pending_action
    if pending is not None and pending.command == COMMAND_NAME:
        return image
    return None


def _image_ref(content: MessageContent | None) -> _ImageRef | None:
    if isinstance(content, PhotoContent):
        # Pick the largest variant Telegram offered.
        largest = max(content.photo, key=lambda p: p.file_size or 0)
        return _ImageRef(
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            content_type="image/jpeg",
        )
    if isinstance(content, DocumentContent):
        mime_type = content.document.mime_type or ""
        if mime_type.startswith("image/") and mime_type not in _UNSUPPORTED_IMAGE_MIME_TYPES:
            return _ImageRef(
                file_id=content.document.file_id,
                file_unique_id=content.document.file_unique_id,
                content_type=mime_type,
            )
    return None


def _caption_command(content: PhotoContent | DocumentContent) -> str | None:
    """Normalize a leading ``/command[@bot]`` in a caption, like the parser does for text."""
    caption = (content.caption or "").strip()
    if not caption.startswith("/"):
        return None
    command, _, _ = caption.split(maxsplit=1)[0].partition("@")
    return command.replace("-", "_")


async def _run_background(ctx: _BackgroundContext) -> None:
    """Download → transform → upload (input + output) → sendDocument."""
    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    image_bytes: bytes | None = None
    sticker_bytes: bytes | None = None
    received_at = datetime.now(UTC)
    started = time.monotonic()
    transform_ms: int | None = None

    try:
        file_path = await ctx.telegram_client.get_file_path(ctx.image.file_id)
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
            f"input_{ctx.image.file_unique_id}"
        )
        try:
            await ctx.gcs_storage.upload(
                object_key=input_key,
                data=image_bytes,
                content_type=ctx.image.content_type,
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and image_bytes is not None:
        try:
            transform_started = time.monotonic()
            sticker = await transform(image_bytes, remove_background=ctx.background_remover)
            sticker_bytes = sticker.png_bytes
            transform_ms = round((time.monotonic() - transform_started) * 1000)
        except NoSubjectError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_NO_SUBJECT
        except StickerTransformError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_TRANSFORM_FAILED

    output_key: str | None = None
    if user_facing_error is None and sticker_bytes is not None:
        output_key = (
            f"sticker_outputs/{ctx.chat_id}/{ctx.trigger_message_id}/"
            f"sticker_{ctx.image.file_unique_id}.png"
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
                filename=f"sticker_{ctx.image.file_unique_id}.png",
                mime_type="image/png",
                reply_to_message_id=ctx.trigger_message_id,
            )
        except TelegramSendError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    _log_timing(ctx, started=started, transform_ms=transform_ms, error_class=error_class)
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


def _log_timing(
    ctx: _BackgroundContext,
    *,
    started: float,
    transform_ms: int | None,
    error_class: str | None,
) -> None:
    """One structured line per sticker, for the latency / memory review.

    ``process_peak_rss_mb`` is the process-lifetime high-water mark (Linux
    reports ``ru_maxrss`` in KiB), so the first sticker after a cold start
    shows what rembg costs on top of the app.
    """
    _logger.info(
        "make_sticker_finished",
        extra={
            "chat_id": ctx.chat_id,
            "outcome": error_class or "success",
            "total_ms": round((time.monotonic() - started) * 1000),
            "transform_ms": transform_ms,
            "process_peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        },
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
