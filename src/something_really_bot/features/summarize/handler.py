"""``/summarize`` — document → TL;DR (#46).

Two-turn command, private chat only.

1. ``/summarize`` → set pending action, prompt for a document.
2. User uploads a document → download, store in GCS under
   ``summarizer/``, extract text, hard-cap at 60k chars (warn the
   user if truncated), summarize via OpenAI, reply with the TL;DR.
"""

import asyncio
import html
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.features.summarize.extractor import (
    DocumentExtractionError,
    UnsupportedDocumentError,
    extract,
)
from something_really_bot.features.summarize.summarizer import (
    DocumentSummarizer,
    SummarizationError,
    get_summarizer,
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
    ParsedUpdate,
    PrivateMessage,
)

_logger = get_logger(__name__)

COMMAND_NAME = "summarize"
PROMPT_TEXT = "Now send a PDF, DOCX, TXT, or Markdown file and I'll give you a TL;DR."
REPLY_PARSE_MODE = "HTML"

_REPLY_TEMPLATE = "<i>{summary}</i>"
_TRUNCATION_NOTICE = "\n\n<i>(Document was long — summarized the first 60k characters only.)</i>"

_ERROR_DOWNLOAD_FAILED = "Couldn't pull that file from Telegram. Try sending it again in a moment."
_ERROR_UNSUPPORTED_TYPE = (
    "I can summarize PDF, DOCX, TXT, and Markdown files. That file type isn't on the list."
)
_ERROR_EXTRACTION_FAILED = (
    "Couldn't read the text out of that file. It might be corrupted or use an unusual format."
)
_ERROR_EMPTY_DOCUMENT = "That document looks empty — there's nothing to summarize."
_ERROR_SUMMARIZER_UNAVAILABLE = "Summarization isn't configured right now. Logged for review."
_ERROR_SUMMARIZATION_FAILED = (
    "Couldn't summarize that document. The summarization service might be "
    "having a moment — try again shortly."
)
_ERROR_GENERIC = "Something went wrong handling that file. Logged."

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]


@dataclass(frozen=True)
class _BackgroundContext:
    bot_id: str
    chat_id: int
    user_id: int
    trigger_message_id: int
    file_id: str
    file_unique_id: str
    filename: str | None
    mime_type: str | None
    telegram_client: TelegramClient
    gcs_storage: GCSStorage
    summarizer: DocumentSummarizer | None
    persistence_record_event: Callable[[EventRecord], None] | None


class SummarizeHandler:
    """``/summarize`` command + follow-up document routing."""

    name = "summarize"
    description = "/summarize — get a TL;DR of a PDF, DOCX, TXT, or Markdown file."
    help_usage = "/summarize, then send a file"

    def __init__(
        self,
        *,
        scheduler: Scheduler = asyncio.create_task,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        summarizer_factory: Callable[[], DocumentSummarizer | None] = get_summarizer,
        pending_action_store_factory: Callable[
            [], PendingActionStore | None
        ] = get_pending_action_store,
    ) -> None:
        self._schedule = scheduler
        self._gcs_factory = gcs_storage_factory
        self._tg_factory = telegram_client_factory
        self._summarizer_factory = summarizer_factory
        self._pending_factory = pending_action_store_factory

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        content = update.content
        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            return True
        if isinstance(content, DocumentContent):
            pending = ctx.pending_action
            return pending is not None and pending.command == COMMAND_NAME
        return False

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        content = update.content

        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            return await self._prompt(update, ctx)

        assert isinstance(content, DocumentContent)
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.clear(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("summarize_clear_pending_failed")

        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text="Reading your document…",
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("summarize_ack_failed", extra={"chat_id": update.chat_id})

        doc = content.document
        bg = _BackgroundContext(
            bot_id=ctx.bot_id,
            chat_id=update.chat_id,
            user_id=update.from_user.id,
            trigger_message_id=update.message_id,
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            filename=doc.file_name,
            mime_type=doc.mime_type,
            telegram_client=telegram_client,
            gcs_storage=self._gcs_factory(),
            summarizer=self._summarizer_factory(),
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
                    expected_input="document",
                )
            except Exception:  # noqa: BLE001
                _logger.exception("summarize_set_pending_failed")
        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=PROMPT_TEXT,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning("summarize_prompt_send_failed", extra={"chat_id": update.chat_id})
        return HandlerResult(handled=True, handler_name=self.name)


async def _run_background(ctx: _BackgroundContext) -> None:
    user_facing_error: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    document_bytes: bytes | None = None
    extracted_text: str | None = None
    truncated = False
    summary: str | None = None
    received_at = datetime.now(UTC)

    if ctx.summarizer is None:
        user_facing_error = _ERROR_SUMMARIZER_UNAVAILABLE
        error_class = "SummarizerUnavailable"
        error_message = "OPENAI_API_KEY not configured"

    if user_facing_error is None:
        try:
            file_path = await ctx.telegram_client.get_file_path(ctx.file_id)
            document_bytes = await ctx.telegram_client.download_file(file_path)
        except TelegramFileError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_DOWNLOAD_FAILED

    object_key: str | None = None
    if user_facing_error is None and document_bytes is not None:
        object_key = (
            f"summarizer/{ctx.chat_id}/{ctx.trigger_message_id}/"
            f"{ctx.file_unique_id}_{ctx.filename or 'document'}"
        )
        try:
            await ctx.gcs_storage.upload(
                object_key=object_key,
                data=document_bytes,
                content_type=ctx.mime_type or "application/octet-stream",
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None and document_bytes is not None:
        try:
            extracted = await extract(
                document_bytes,
                filename=ctx.filename,
                mime_type=ctx.mime_type,
            )
        except UnsupportedDocumentError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_UNSUPPORTED_TYPE
        except DocumentExtractionError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_EXTRACTION_FAILED
        else:
            extracted_text = extracted.text
            truncated = extracted.truncated

    if user_facing_error is None and extracted_text is not None and not extracted_text.strip():
        user_facing_error = _ERROR_EMPTY_DOCUMENT
        error_class = "EmptyDocument"
        error_message = "Extractor produced empty text"

    if user_facing_error is None and extracted_text is not None:
        assert ctx.summarizer is not None
        try:
            summary = await ctx.summarizer.summarize(extracted_text)
        except SummarizationError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_SUMMARIZATION_FAILED

    if user_facing_error is None and summary is not None:
        reply_text = _REPLY_TEMPLATE.format(summary=html.escape(summary))
        if truncated:
            reply_text += _TRUNCATION_NOTICE
        try:
            await ctx.telegram_client.send_message(
                chat_id=ctx.chat_id,
                text=reply_text,
                reply_to_message_id=ctx.trigger_message_id,
                parse_mode=REPLY_PARSE_MODE,
            )
        except TelegramSendError as exc:
            error_class = type(exc).__name__
            error_message = str(exc)
            user_facing_error = _ERROR_GENERIC

    if user_facing_error is None:
        _record_event(
            ctx,
            event="summarize_succeeded",
            status="success",
            details=f"{object_key} truncated={truncated}",
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
        _logger.warning("summarize_user_error_send_failed", extra={"chat_id": ctx.chat_id})
    _record_event(
        ctx,
        event="summarize_failed",
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
        _logger.exception("summarize_record_event_failed")


@lru_cache(maxsize=1)
def get_summarize_handler() -> SummarizeHandler:
    """Process-wide singleton."""
    return SummarizeHandler()
