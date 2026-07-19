"""Voice transcription handler + background orchestrator (#43).

Matches voice messages in private, group, and supergroup chats. The
handler does the bare minimum on the request hot path:

1. Reject voice memos over the duration cap with a clear reply.
2. Ack the user with a "transcribing…" reply pinned to the trigger.
3. Stamp a 👀 reaction (best-effort).
4. Schedule download + GCS upload + transcribe + analyze + send as an
   asyncio task so the webhook returns 200 immediately.

Every failure in the background path is funneled into a user-visible
message instead of leaking SDK errors.
"""

import asyncio
import contextlib
import html
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.features.voice_transcription.storage import (
    JobRow,
    VoiceJobStorage,
)
from something_really_bot.features.voice_transcription.transcriber import (
    AnalysisError,
    TranscriptionError,
    VoiceTranscriber,
    get_voice_transcriber,
)
from something_really_bot.file_storage.gcs import GCSStorage, get_gcs_storage
from something_really_bot.logging import get_logger
from something_really_bot.persistence import EventRecord
from something_really_bot.persistence.postgres import get_postgres_storage
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.client import (
    TelegramClient,
    TelegramFileError,
    TelegramSendError,
    get_telegram_client,
)
from something_really_bot.telegram.models import (
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
    Voice,
    VoiceContent,
)

_logger = get_logger(__name__)

MAX_DURATION_SECONDS = 10 * 60  # 10 min
# Whisper / gpt-4o-transcribe accepts up to 25 MB per request. 10 min
# of Opus voice is ~3-5 MB so this is a defensive cap rather than a
# practical one.
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
# Voice memos at or under this duration skip the OpenAI chat summary
# step — the transcript itself is shorter than any commentary would be
# (#56).
SHORT_DURATION_THRESHOLD_SECONDS = 120
TELEGRAM_MESSAGE_LIMIT = 4096

ACK_REACTION = "👀"
ACK_TEXT = "Transcribing your voice memo…"
REPLY_PARSE_MODE = "HTML"

# Long-memo reply: summary + emotion read share one blockquote; the
# transcript gets its own. Free-form OpenAI output is ``html.escape``-d
# before interpolation so literal angle brackets in the transcript or
# summary don't break Telegram's HTML parse.
_LONG_REPLY_TEMPLATE = (
    "Summary & Vibe:\n<blockquote>{summary}\n{emotion}</blockquote>\n\n"
    "Transcript:\n<blockquote>{transcript}</blockquote>"
)
# Short-memo reply: just the transcript in a blockquote.
_SHORT_REPLY_TEMPLATE = "Voice-to-text:\n<blockquote>{transcript}</blockquote>"

_ERROR_TOO_LONG = "That voice memo is over the 10-minute limit. Try sending a shorter one."
_ERROR_TOO_LARGE = "That voice memo is too large to transcribe. Try sending a shorter one."
_ERROR_DOWNLOAD_FAILED = (
    "Couldn't pull that voice memo from Telegram. Try sending it again in a moment."
)
_ERROR_TRANSCRIPTION_FAILED = (
    "Couldn't transcribe that voice memo. The transcription service might be "
    "having a moment — try again shortly."
)
_ERROR_ANALYSIS_FAILED = "Transcribed your voice memo but couldn't summarize it. Try again shortly."
_ERROR_TRANSCRIBER_UNAVAILABLE = (
    "Voice transcription isn't configured right now. Logged for review."
)
_ERROR_GENERIC = "Something went wrong handling that voice memo. Logged."

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]

_SupportedMessage = PrivateMessage | GroupMessage | SupergroupMessage


@dataclass(frozen=True)
class _BackgroundContext:
    """Frozen handles + ids handed off to the background task."""

    bot_id: str
    chat_id: int
    user_id: int
    message_id: int
    voice: Voice
    telegram_client: TelegramClient
    gcs_storage: GCSStorage
    transcriber: VoiceTranscriber | None
    job_storage: VoiceJobStorage | None
    persistence_record_event: Callable[[EventRecord], None] | None
    # Message id of the "Transcribing your voice memo…" ack, if the
    # ack send succeeded. The background task edits this message in
    # place with the final reply instead of double-posting (#56). When
    # ``None`` the edit path is skipped and we fall back to a fresh
    # ``send_message``.
    ack_message_id: int | None = None


class VoiceTranscriptionHandler:
    """Telegram dispatcher entry point for voice transcription."""

    name = "voice_transcription.transcribe"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        transcriber_factory: Callable[[], VoiceTranscriber | None] = get_voice_transcriber,
        job_storage_factory: Callable[[], VoiceJobStorage | None] = (
            lambda: _build_job_storage_or_none()
        ),
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
        self._transcriber_factory = transcriber_factory
        self._jobs_factory = job_storage_factory

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        return isinstance(update.content, VoiceContent)

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage)
        assert isinstance(update.content, VoiceContent)
        voice = update.content.voice

        telegram_client = self._tg_factory()

        if voice.duration > MAX_DURATION_SECONDS:
            with contextlib.suppress(TelegramSendError):
                await telegram_client.send_message(
                    chat_id=update.chat_id,
                    text=_ERROR_TOO_LONG,
                    reply_to_message_id=update.message_id,
                )
            return HandlerResult(handled=True, handler_name=self.name)

        if voice.file_size is not None and voice.file_size > MAX_FILE_SIZE_BYTES:
            with contextlib.suppress(TelegramSendError):
                await telegram_client.send_message(
                    chat_id=update.chat_id,
                    text=_ERROR_TOO_LARGE,
                    reply_to_message_id=update.message_id,
                )
            return HandlerResult(handled=True, handler_name=self.name)

        ack_message_id: int | None = None
        try:
            ack_response = await telegram_client.send_message(
                chat_id=update.chat_id,
                text=ACK_TEXT,
                reply_to_message_id=update.message_id,
            )
            if isinstance(ack_response, dict):
                raw_id = ack_response.get("message_id")
                if isinstance(raw_id, int):
                    ack_message_id = raw_id
        except TelegramSendError as exc:
            _logger.warning(
                "voice_transcription_ack_failed",
                extra={
                    "chat_id": update.chat_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

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
            voice=voice,
            telegram_client=telegram_client,
            gcs_storage=self._gcs_factory(),
            transcriber=self._transcriber_factory(),
            job_storage=self._jobs_factory(),
            persistence_record_event=(
                ctx.persistence.record_event if ctx.persistence is not None else None
            ),
            ack_message_id=ack_message_id,
        )
        self._schedule(_run_background(bg))

        return HandlerResult(handled=True, handler_name=self.name)


async def _run_background(ctx: _BackgroundContext) -> None:
    """Download → upload → transcribe → analyze → send. Never raises."""
    job_id: int | None = None
    if ctx.job_storage is not None:
        try:
            job_id = await ctx.job_storage.insert_pending(
                JobRow(
                    bot_id=ctx.bot_id,
                    chat_id=ctx.chat_id,
                    user_id=ctx.user_id,
                    message_id=ctx.message_id,
                    telegram_file_id=ctx.voice.file_id,
                    telegram_file_unique_id=ctx.voice.file_unique_id,
                    duration_seconds=ctx.voice.duration,
                    file_size_bytes=ctx.voice.file_size,
                    mime_type=ctx.voice.mime_type,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "voice_transcription_job_insert_failed",
                extra={"exception_type": type(exc).__name__},
            )
            job_id = None

    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    audio_bytes: bytes | None = None
    gcs_uri: str | None = None
    object_key: str | None = None
    transcript: str | None = None
    summary: str | None = None
    emotion: str | None = None
    telegram_reply_message_id: int | None = None

    if ctx.transcriber is None:
        user_facing_error = _ERROR_TRANSCRIBER_UNAVAILABLE
        error_class = "TranscriberUnavailable"
        error_message = "OPENAI_API_KEY not configured"

    if user_facing_error is None:
        await _safe_status(ctx.job_storage, job_id, "downloading")
        try:
            file_path = await ctx.telegram_client.get_file_path(ctx.voice.file_id)
            audio_bytes = await ctx.telegram_client.download_file(file_path)
        except TelegramFileError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_DOWNLOAD_FAILED

    if user_facing_error is None and audio_bytes is not None:
        await _safe_status(ctx.job_storage, job_id, "uploading")
        ext = _ext_for_mime(ctx.voice.mime_type)
        object_key = (
            f"voice_transcription_requests/{ctx.chat_id}/{ctx.message_id}/"
            f"voice_{ctx.voice.file_unique_id}{ext}"
        )
        try:
            gcs_uri = await ctx.gcs_storage.upload(
                object_key=object_key,
                data=audio_bytes,
                content_type=ctx.voice.mime_type or "audio/ogg",
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and audio_bytes is not None:
        assert ctx.transcriber is not None
        await _safe_status(ctx.job_storage, job_id, "transcribing")
        try:
            transcript = await ctx.transcriber.transcribe(
                audio_bytes,
                filename=f"voice_{ctx.voice.file_unique_id}{_ext_for_mime(ctx.voice.mime_type)}",
            )
        except TranscriptionError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_TRANSCRIPTION_FAILED

    # Skip the OpenAI chat analyze call for short memos (#56). For
    # anything over the threshold we run the summary + emotion read.
    needs_analysis = (
        user_facing_error is None
        and transcript is not None
        and ctx.voice.duration > SHORT_DURATION_THRESHOLD_SECONDS
    )
    if needs_analysis:
        assert ctx.transcriber is not None
        await _safe_status(ctx.job_storage, job_id, "analyzing")
        try:
            analysis = await ctx.transcriber.analyze(transcript)  # type: ignore[arg-type]
        except AnalysisError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_ANALYSIS_FAILED
        else:
            summary = analysis.summary
            emotion = analysis.emotion

    if user_facing_error is None and transcript is not None:
        await _safe_status(ctx.job_storage, job_id, "sending")
        reply_messages = _compose_reply_messages(transcript, summary, emotion)
        try:
            telegram_reply_message_id = await _deliver_replies(ctx, reply_messages)
        except TelegramSendError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and transcript is not None:
        assert object_key is not None
        if job_id is not None and ctx.job_storage is not None:
            try:
                await ctx.job_storage.mark_succeeded(
                    job_id,
                    gcs_object_path=object_key,
                    transcript=transcript,
                    summary=summary,
                    emotion=emotion,
                    telegram_reply_message_id=telegram_reply_message_id,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("voice_transcription_mark_succeeded_failed")
        _record_event(
            ctx,
            event="voice_transcription_succeeded",
            status="success",
            details=f"{object_key} {len(transcript)}chars",
            occurred_at=datetime.now(UTC),
        )
        _ = gcs_uri  # used only to validate upload happened
        return

    # Failure path.
    if user_facing_error is not None:
        with contextlib.suppress(TelegramSendError):
            await _deliver_replies(ctx, [user_facing_error], parse_mode=None)
        if job_id is not None and ctx.job_storage is not None:
            try:
                await ctx.job_storage.mark_failed(
                    job_id,
                    error_class=error_class or "Unknown",
                    error_message=error_message or "",
                    transcript=transcript,
                    summary=summary,
                    emotion=emotion,
                    gcs_object_path=object_key,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("voice_transcription_mark_failed_failed")
        _record_event(
            ctx,
            event="voice_transcription_failed",
            status="error",
            details=f"{error_class}: {error_message}",
            occurred_at=datetime.now(UTC),
        )


def _chunk_transcript(escaped_transcript: str, *, max_chunk_size: int) -> list[str]:
    """Split already-HTML-escaped transcript text into chunks.

    Prefers splitting at newlines, then spaces, then hard-cuts.  Never
    splits inside an HTML entity (``&amp;`` etc.).
    """
    if len(escaped_transcript) <= max_chunk_size:
        return [escaped_transcript]

    chunks: list[str] = []
    remaining = escaped_transcript
    while remaining:
        if len(remaining) <= max_chunk_size:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, max_chunk_size)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_chunk_size)
        if split_at == -1:
            split_at = max_chunk_size

        amp = remaining.rfind("&", 0, split_at + 1)
        if amp != -1 and ";" not in remaining[amp : split_at + 1]:
            split_at = amp

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip(" ")
    return chunks


_SPLIT_NOTICE = "The transcript is too long and will be split into {n} messages."
_CHUNK_PREFIX = "Transcript pt. {i} of {n}:\n<blockquote>"
_CHUNK_SUFFIX = "</blockquote>"
_END_MARKER = "\nEnd of transcript"


def _compose_reply_messages(
    transcript: str,
    summary: str | None,
    emotion: str | None,
) -> list[str]:
    """Build one or more HTML reply messages for the transcription result.

    Returns a single-element list when everything fits in one Telegram
    message; returns multiple messages with a header/notice + numbered
    transcript chunks when it doesn't.
    """
    escaped_transcript = html.escape(transcript)
    has_analysis = summary is not None and emotion is not None

    if has_analysis:
        single = _LONG_REPLY_TEMPLATE.format(
            summary=html.escape(summary),
            emotion=html.escape(emotion),
            transcript=escaped_transcript,
        )
    else:
        single = _SHORT_REPLY_TEMPLATE.format(transcript=escaped_transcript)

    if len(single) <= TELEGRAM_MESSAGE_LIMIT:
        return [single]

    if has_analysis:
        header = (
            "Summary & Vibe:\n<blockquote>"
            + html.escape(summary)  # type: ignore[arg-type]
            + "\n"
            + html.escape(emotion)  # type: ignore[arg-type]
            + "</blockquote>"
        )
    else:
        header = None

    total_len = len(escaped_transcript)
    n_chunks = 2
    avail_middle = 0
    for _ in range(20):
        sample_prefix = _CHUNK_PREFIX.format(i=n_chunks, n=n_chunks)
        overhead = len(sample_prefix) + len(_CHUNK_SUFFIX)
        last_overhead = overhead + len(_END_MARKER)
        avail_middle = TELEGRAM_MESSAGE_LIMIT - overhead
        avail_last = TELEGRAM_MESSAGE_LIMIT - last_overhead
        if avail_middle <= 0 or avail_last <= 0:
            n_chunks += 1
            continue
        capacity = avail_middle * (n_chunks - 1) + avail_last
        if capacity >= total_len:
            break
        n_chunks += 1

    chunks = _chunk_transcript(escaped_transcript, max_chunk_size=avail_middle)

    messages: list[str] = []
    total = len(chunks)
    notice = _SPLIT_NOTICE.format(n=total)
    if header is not None:
        messages.append(header + "\n\n" + notice)
    else:
        messages.append(notice)

    for i, chunk in enumerate(chunks, start=1):
        prefix = _CHUNK_PREFIX.format(i=i, n=total)
        suffix = _CHUNK_SUFFIX
        if i == total:
            suffix += _END_MARKER
        messages.append(prefix + chunk + suffix)

    return messages


async def _deliver_replies(
    ctx: _BackgroundContext,
    messages: list[str],
    *,
    parse_mode: str | None = REPLY_PARSE_MODE,
) -> int | None:
    """Deliver one or more reply messages.

    The first message edits the ack (falling back to a fresh send).
    Subsequent messages are sent as new messages.  Returns the message
    id the user sees for the first message, when known.  Raises
    :class:`TelegramSendError` only if the *first* message delivery
    fails entirely.
    """
    first_id = await _deliver_single(ctx, messages[0], parse_mode=parse_mode)

    for msg in messages[1:]:
        try:
            await ctx.telegram_client.send_message(
                chat_id=ctx.chat_id,
                text=msg,
                parse_mode=parse_mode,
            )
        except TelegramSendError as exc:
            _logger.warning(
                "voice_transcription_chunk_send_failed",
                extra={
                    "chat_id": ctx.chat_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    return first_id


async def _deliver_single(
    ctx: _BackgroundContext,
    text: str,
    *,
    parse_mode: str | None,
) -> int | None:
    """Edit the ack message with ``text``; fall back to a fresh send.

    Returns the message id the user sees, when known.  Raises
    :class:`TelegramSendError` only if both the edit and the fallback
    send fail.
    """
    if ctx.ack_message_id is not None:
        try:
            edited = await ctx.telegram_client.edit_message_text(
                chat_id=ctx.chat_id,
                message_id=ctx.ack_message_id,
                text=text,
                parse_mode=parse_mode,
            )
            if isinstance(edited, dict) and isinstance(edited.get("message_id"), int):
                return int(edited["message_id"])
            return ctx.ack_message_id
        except TelegramSendError as exc:
            _logger.warning(
                "voice_transcription_edit_failed_falling_back_to_send",
                extra={
                    "chat_id": ctx.chat_id,
                    "ack_message_id": ctx.ack_message_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    sent = await ctx.telegram_client.send_message(
        chat_id=ctx.chat_id,
        text=text,
        reply_to_message_id=ctx.message_id,
        parse_mode=parse_mode,
    )
    if isinstance(sent, dict) and isinstance(sent.get("message_id"), int):
        return int(sent["message_id"])
    return None


def _ext_for_mime(mime_type: str | None) -> str:
    """Best-effort file extension. Defaults to .ogg (Telegram voice memos)."""
    if mime_type is None:
        return ".ogg"
    if "ogg" in mime_type:
        return ".ogg"
    if "mpeg" in mime_type or "mp3" in mime_type:
        return ".mp3"
    if "wav" in mime_type:
        return ".wav"
    if "m4a" in mime_type or "mp4" in mime_type:
        return ".m4a"
    return ".ogg"


async def _safe_status(jobs: VoiceJobStorage | None, job_id: int | None, status: str) -> None:
    if jobs is None or job_id is None:
        return
    try:
        await jobs.update_status(job_id, status)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        _logger.exception("voice_transcription_status_update_failed")


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
        _logger.exception("voice_transcription_record_event_failed")


def _build_job_storage_or_none() -> VoiceJobStorage | None:
    storage = get_postgres_storage()
    if storage is None:
        return None
    return VoiceJobStorage(storage)


@lru_cache(maxsize=1)
def get_voice_transcription_handler() -> VoiceTranscriptionHandler:
    """Process-wide singleton, wired with production dependencies."""
    return VoiceTranscriptionHandler()
