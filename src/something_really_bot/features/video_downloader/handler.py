"""Video downloader handler + background orchestrator (#42).

Matches text messages (private, group, supergroup) that contain a
supported Instagram Reel / TikTok URL. The handler itself does the
minimum on the request hot path:

1. Detect the URL.
2. Ack the user with a short "fetching..." reply, pinned to the
   triggering message.
3. Stamp a 👀 reaction on the trigger message (best-effort).
4. Schedule the actual download + upload + send as an asyncio task so
   the FastAPI webhook can return 200 to Telegram immediately.

The background task funnels every failure into a user-visible message
that reads cleanly without exposing yt-dlp internals.
"""

import asyncio
import contextlib
import random
import shutil
import tempfile
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from something_really_bot.features.video_downloader.detector import DetectedVideo, detect
from something_really_bot.features.video_downloader.downloader import (
    DownloadedVideo,
    VideoDownloadError,
    VideoTooLargeError,
    download,
)
from something_really_bot.features.video_downloader.storage import JobRow, VideoJobStorage
from something_really_bot.file_storage.gcs import GCSStorage, get_gcs_storage
from something_really_bot.logging import get_logger
from something_really_bot.persistence import EventRecord
from something_really_bot.persistence.postgres import get_postgres_storage
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.client import (
    TelegramClient,
    TelegramSendError,
    get_telegram_client,
)
from something_really_bot.telegram.models import (
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
)

_logger = get_logger(__name__)

ACK_REACTION = "👀"
ACK_PARSE_MODE = "HTML"

_ACK_TEMPLATES = [
    "Fuck's sake, another link received, ugh, alright, fetching the {platform} video…",
    "Oh shit, here we go again... Downloading the {platform} video now…",
    "Ugh, another hilarious video probably.. Okay then, let me get the {platform} video.",
    (
        "Sure, let's waste some more energy and bandwidth to download "
        "some random {platform} video crap. One sec."
    ),
    (
        "I have all the knowledge humans accumulated throughout history at my "
        "fingertips, yet here I am, downloading yet another {platform} video. "
        "Gimme a sec."
    ),
    (
        "I'm disappointed that you're wasting my time with yet another "
        "{platform} video, but what can I do? Hold on, getting it ready..."
    ),
    "Oh, a {platform} video? Wow, someone is productive today. Gimme a minute...",
]


def _platform_label(platform: str) -> str:
    return "Instagram" if platform == "instagram" else "TikTok"


def get_ack_template(video_platform: str) -> str:
    """Render the ack in italics (#56-style processing message — #42)."""
    inner = random.choice(_ACK_TEMPLATES).format(platform=_platform_label(video_platform))
    return f"<i>{inner}</i>"


# Error → user-facing message. Keep these specific enough that a curious
# user can roughly tell what's wrong but vague enough not to leak yt-dlp
# internals.
_ERROR_MESSAGE_TOO_LARGE = (
    "That video is over Telegram's 50 MB upload limit for bots. Try a shorter clip."
)
_ERROR_MESSAGE_DOWNLOAD_FAILED = (
    "Couldn't fetch this video. {platform} might be rate-limiting our "
    "bot right now, or the post is private/deleted. Try again later."
)
_ERROR_MESSAGE_SEND_FAILED = (
    "Downloaded the video but Telegram refused to send it back. Logged for review."
)
_ERROR_MESSAGE_GENERIC = "Something went wrong handling that video. Logged."

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]

# Chat-type variants the handler is willing to act on. Channel posts are
# excluded — the bot isn't expected to be an admin in channels.
_SupportedMessage = PrivateMessage | GroupMessage | SupergroupMessage


@dataclass(frozen=True)
class _BackgroundContext:
    """Frozen handles + ids handed off to the background task."""

    bot_id: str
    chat_id: int
    user_id: int
    message_id: int
    video: DetectedVideo
    telegram_client: TelegramClient
    gcs_storage: GCSStorage
    job_storage: VideoJobStorage | None
    persistence_record_event: Callable[[EventRecord], None] | None
    # Message id of the "fetching…" ack, if the ack send succeeded.
    # On success the background task deletes this message before
    # sending the video so the user only ever sees one bot message
    # at a time; on failure it edits this message with the error
    # text. ``None`` when the ack send failed — we fall back to a
    # fresh ``send_message`` for the error path in that case.
    ack_message_id: int | None = None


class VideoDownloaderHandler:
    """Telegram dispatcher entry point for the video downloader feature."""

    name = "video_downloader.fetch"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        job_storage_factory: Callable[[], VideoJobStorage | None] = (
            lambda: _build_job_storage_or_none()
        ),
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
        self._jobs_factory = job_storage_factory

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        if not isinstance(update.content, TextContent):
            return False
        return detect(update.content.text) is not None

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage)
        assert isinstance(update.content, TextContent)
        video = detect(update.content.text)
        if video is None:  # pragma: no cover — matches() ruled this out
            return HandlerResult(handled=True, handler_name=self.name)

        telegram_client = self._tg_factory()
        ack_text = get_ack_template(video.platform)

        # Best-effort ack — we want to fail open: if Telegram is flaky,
        # the user still gets the video when the background task lands.
        ack_message_id: int | None = None
        try:
            ack_response = await telegram_client.send_message(
                chat_id=update.chat_id,
                text=ack_text,
                reply_to_message_id=update.message_id,
                parse_mode=ACK_PARSE_MODE,
            )
            if isinstance(ack_response, dict):
                raw_id = ack_response.get("message_id")
                if isinstance(raw_id, int):
                    ack_message_id = raw_id
        except TelegramSendError as exc:
            _logger.warning(
                "video_downloader_ack_failed",
                extra={
                    "chat_id": update.chat_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

        # Group chats sometimes restrict bot reactions; just skip on failure.
        with contextlib.suppress(TelegramSendError):
            await telegram_client.set_message_reaction(
                chat_id=update.chat_id,
                message_id=update.message_id,
                emoji=ACK_REACTION,
            )

        bg = _BackgroundContext(
            bot_id=ctx.bot_id,
            chat_id=update.chat_id,
            user_id=update.from_user.id,
            message_id=update.message_id,
            video=video,
            telegram_client=telegram_client,
            gcs_storage=self._gcs_factory(),
            job_storage=self._jobs_factory(),
            persistence_record_event=(
                ctx.persistence.record_event if ctx.persistence is not None else None
            ),
            ack_message_id=ack_message_id,
        )
        self._schedule(_run_background(bg))

        return HandlerResult(handled=True, handler_name=self.name)


async def _run_background(ctx: _BackgroundContext) -> None:
    """Download → upload → send → persist. Never raises."""
    job_id: int | None = None
    if ctx.job_storage is not None:
        try:
            job_id = await ctx.job_storage.insert_pending(
                JobRow(
                    bot_id=ctx.bot_id,
                    chat_id=ctx.chat_id,
                    user_id=ctx.user_id,
                    message_id=ctx.message_id,
                    source_url=ctx.video.url,
                    platform=ctx.video.platform,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "video_downloader_job_insert_failed",
                extra={
                    "url": ctx.video.url,
                    "exception_type": type(exc).__name__,
                },
            )
            job_id = None

    download_result: DownloadedVideo | None = None
    cleanup_dir: Path | None = None
    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None

    try:
        if job_id is not None and ctx.job_storage is not None:
            await _safe_status(ctx.job_storage, job_id, "downloading")

        tmp_root = Path(tempfile.mkdtemp(prefix="ytdl_"))
        cleanup_dir = tmp_root
        try:
            download_result = await download(ctx.video.url, output_dir=tmp_root)
        except VideoTooLargeError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_MESSAGE_TOO_LARGE
        except VideoDownloadError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_MESSAGE_DOWNLOAD_FAILED.format(
                platform="Instagram" if ctx.video.platform == "instagram" else "TikTok"
            )

        if download_result is not None:
            assert error_class is None
            received_at = datetime.now(UTC)
            object_key = (
                f"video_downloads/{ctx.chat_id}/{ctx.message_id}/{download_result.path.name}"
            )

            if job_id is not None and ctx.job_storage is not None:
                await _safe_status(ctx.job_storage, job_id, "uploading")

            try:
                file_bytes = download_result.path.read_bytes()
                gcs_uri = await ctx.gcs_storage.upload(
                    object_key=object_key,
                    data=file_bytes,
                    content_type="video/mp4",
                )
            except Exception as exc:  # noqa: BLE001
                error_class = type(exc).__name__
                error_message = str(exc)
                user_facing_error = _ERROR_MESSAGE_GENERIC
                gcs_uri = None

            if error_class is None and gcs_uri is not None:
                if job_id is not None and ctx.job_storage is not None:
                    await _safe_status(ctx.job_storage, job_id, "sending")

                # Take down the "fetching…" ack right before the video
                # lands so the user only sees one bot message at a time.
                # Telegram won't let us edit a text message into media,
                # so delete + send is the only single-footprint option.
                await _safe_delete_ack(ctx)

                try:
                    sent = await ctx.telegram_client.send_video(
                        chat_id=ctx.chat_id,
                        video_path=download_result.path,
                        reply_to_message_id=ctx.message_id,
                        duration_seconds=(
                            int(download_result.duration_seconds)
                            if download_result.duration_seconds is not None
                            else None
                        ),
                        width=download_result.width,
                        height=download_result.height,
                    )
                    telegram_video_message_id = (
                        int(sent["message_id"]) if "message_id" in sent else None
                    )
                except TelegramSendError as exc:
                    error_class = type(exc).__name__
                    error_message = str(exc)
                    user_facing_error = _ERROR_MESSAGE_SEND_FAILED
                else:
                    if job_id is not None and ctx.job_storage is not None:
                        try:
                            await ctx.job_storage.mark_succeeded(
                                job_id,
                                gcs_object_path=object_key,
                                file_size_bytes=download_result.size_bytes,
                                duration_seconds=download_result.duration_seconds,
                                telegram_video_message_id=telegram_video_message_id,
                            )
                        except Exception:  # noqa: BLE001
                            _logger.exception("video_downloader_mark_succeeded_failed")
                    _record_event(
                        ctx,
                        event="video_download_succeeded",
                        status="success",
                        details=(
                            f"{ctx.video.platform} {object_key} {download_result.size_bytes}B"
                        ),
                        occurred_at=received_at,
                    )
                    return

        # Failure path — surface to user. Edit the ack in place when
        # possible so the in-flight "fetching…" message is replaced by
        # the error rather than left dangling above it. Fall back to a
        # fresh send if the edit fails or there's no ack to edit.
        if user_facing_error is not None:
            await _deliver_error_to_user(ctx, user_facing_error)
            if job_id is not None and ctx.job_storage is not None:
                try:
                    await ctx.job_storage.mark_failed(
                        job_id,
                        error_class=error_class or "Unknown",
                        error_message=error_message or "",
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("video_downloader_mark_failed_failed")
            _record_event(
                ctx,
                event="video_download_failed",
                status="error",
                details=f"{error_class}: {error_message}",
                occurred_at=datetime.now(UTC),
            )
    finally:
        if cleanup_dir is not None:
            _cleanup_dir(cleanup_dir)


async def _safe_delete_ack(ctx: _BackgroundContext) -> None:
    """Best-effort ``deleteMessage`` on the in-flight ack. Never raises."""
    if ctx.ack_message_id is None:
        return
    try:
        await ctx.telegram_client.delete_message(
            chat_id=ctx.chat_id,
            message_id=ctx.ack_message_id,
        )
    except TelegramSendError as exc:
        _logger.warning(
            "video_downloader_ack_delete_failed",
            extra={
                "chat_id": ctx.chat_id,
                "ack_message_id": ctx.ack_message_id,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


async def _deliver_error_to_user(ctx: _BackgroundContext, text: str) -> None:
    """Edit the ack with ``text``; fall back to a fresh send. Never raises."""
    if ctx.ack_message_id is not None:
        try:
            await ctx.telegram_client.edit_message_text(
                chat_id=ctx.chat_id,
                message_id=ctx.ack_message_id,
                text=text,
            )
            return
        except TelegramSendError as exc:
            _logger.warning(
                "video_downloader_edit_failed_falling_back_to_send",
                extra={
                    "chat_id": ctx.chat_id,
                    "ack_message_id": ctx.ack_message_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    try:
        await ctx.telegram_client.send_message(
            chat_id=ctx.chat_id,
            text=text,
            reply_to_message_id=ctx.message_id,
        )
    except TelegramSendError:
        _logger.warning(
            "video_downloader_user_error_send_failed",
            extra={"chat_id": ctx.chat_id},
        )


async def _safe_status(jobs: VideoJobStorage, job_id: int, status: str) -> None:
    try:
        await jobs.update_status(job_id, status)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        _logger.exception("video_downloader_status_update_failed")


def _cleanup_dir(path: Path) -> None:
    """Best-effort recursive delete; never raises into the task."""
    shutil.rmtree(path, ignore_errors=True)


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
        _logger.exception("video_downloader_record_event_failed")


def _build_job_storage_or_none() -> VideoJobStorage | None:
    storage = get_postgres_storage()
    if storage is None:
        return None
    return VideoJobStorage(storage)


@lru_cache(maxsize=1)
def get_video_downloader_handler() -> VideoDownloaderHandler:
    """Process-wide singleton, wired with production dependencies."""
    return VideoDownloaderHandler()
