"""FastAPI application entrypoint.

``GET /health`` is an unauthenticated liveness probe used by Cloud Run.
(``/healthz`` is intercepted by Google Frontend on ``*.run.app`` domains
and never reaches the container, so we use ``/health``.)
``POST /webhook`` is the Telegram target: requests must carry the secret
header validated by :func:`verify_telegram_webhook_secret`; the body is
parsed into a :class:`ParsedUpdate`, persisted to BigQuery (#18), then
routed to the matching handler via the module-level :class:`Dispatcher`.
The handler returns a :class:`HandlerResult`; the webhook performs the
Telegram send (#15) and persists the response.

The endpoint always returns 200 to Telegram, even when parsing, dispatch,
sending, or persistence fails — failures are caught and logged so Telegram
doesn't retry-storm us (SPEC §6.9).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request

from something_really_bot import __version__
from something_really_bot.config import Settings, get_settings
from something_really_bot.features.commands.handler import (
    HelpCommandHandler,
    StartCommandHandler,
)
from something_really_bot.features.daily_message.command_handler import (
    DailyMessageQACommandHandler,
)
from something_really_bot.features.daily_message.handler import DailyMessageJob
from something_really_bot.features.dutch_translation.handler import (
    get_dutch_translation_handler,
)
from something_really_bot.features.ensure_webhook.handler import EnsureWebhookJob
from something_really_bot.features.example.handler import PingHandler
from something_really_bot.features.file_storage.handler import FileStorageHandler
from something_really_bot.features.hello_world.handler import HelloWorldHandler
from something_really_bot.features.make_sticker.handler import (
    get_make_sticker_handler,
)
from something_really_bot.features.next_reunion.handler import NextReunionHandler
from something_really_bot.features.ocr.handler import get_ocr_handler
from something_really_bot.features.openai_fallback.handler import OpenAIFallbackHandler
from something_really_bot.features.summarize.handler import get_summarize_handler
from something_really_bot.features.tiktok_reminder.handler import TikTokReminderJob
from something_really_bot.features.video_downloader.handler import (
    get_video_downloader_handler,
)
from something_really_bot.features.voice_transcription.handler import (
    get_voice_transcription_handler,
)
from something_really_bot.file_storage.fetcher import get_file_fetcher
from something_really_bot.logging import configure_logging, get_logger
from something_really_bot.persistence import (
    EventRecord,
    FileRecord,
    MessageRecord,
    PersistenceService,
    RawUpdateRecord,
    ResponseRecord,
)
from something_really_bot.persistence.bigquery import get_persistence_service
from something_really_bot.routing.command_registry import get_command_registry
from something_really_bot.routing.dispatcher import Dispatcher
from something_really_bot.routing.help_registry import HelpRegistry
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.services.job_history import (
    JobHistoryRow,
    get_job_history_logger,
    safe_record,
)
from something_really_bot.services.jobs import JobRegistry, UnknownJobError
from something_really_bot.services.openai_client import get_openai_client
from something_really_bot.services.pending_actions import (
    get_pending_action_store,
    safe_get_pending_action,
)
from something_really_bot.services.scheduler_auth import verify_scheduler_oidc_token
from something_really_bot.telegram.client import get_telegram_client
from something_really_bot.telegram.models import (
    ChannelPost,
    CommandContent,
    DocumentContent,
    GroupMessage,
    ParsedUpdate,
    PhotoContent,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    UnsupportedUpdate,
    VoiceContent,
)
from something_really_bot.telegram.parser import MalformedUpdateError, parse_update
from something_really_bot.telegram.security import verify_telegram_webhook_secret

_logger = get_logger(__name__)


def build_default_dispatcher() -> Dispatcher:
    """Construct the production dispatcher with all enabled handlers.

    Registration order matters: it's both the dispatch precedence and
    the order the auto-generated ``/help`` (#27) lists features in.
    """
    dispatcher = Dispatcher()
    help_registry = HelpRegistry(get_command_registry())
    dispatcher.register(StartCommandHandler(help_registry))
    dispatcher.register(HelpCommandHandler(help_registry))
    # /make-sticker (#44), /ocr (#45), and /summarize (#46) must precede
    # FileStorageHandler: when a user is mid-flow, their next photo or
    # document belongs to the right pipeline, not the generic
    # file-to-GCS dump. Each handler matches only when its own pending
    # action is set, so they never collide on the same upload.
    dispatcher.register(get_make_sticker_handler())
    dispatcher.register(get_ocr_handler())
    dispatcher.register(get_summarize_handler())
    dispatcher.register(FileStorageHandler())
    # Voice transcription owns voice content (#43); FileStorageHandler
    # above intentionally does not match VoiceContent.
    dispatcher.register(get_voice_transcription_handler())
    # /next-reunion sets or queries the reunion countdown date (#58).
    dispatcher.register(NextReunionHandler())
    # Command-driven workflows: /dutch claims its trigger + follow-up
    # text replies via pending_action state (#47).
    dispatcher.register(get_dutch_translation_handler())
    dispatcher.register(DailyMessageQACommandHandler())
    # Video downloader must precede the OpenAI fallback so a Reel/TikTok
    # URL in plain text doesn't get routed to the LLM.
    dispatcher.register(get_video_downloader_handler())
    dispatcher.register(HelloWorldHandler())  # gated by settings.hello_world_mode
    dispatcher.register(OpenAIFallbackHandler())
    dispatcher.register(PingHandler())
    return dispatcher


def build_default_job_registry() -> JobRegistry:
    """Construct the production scheduled-job registry.

    The Cloud Scheduler entries that trigger these jobs live in
    ``infra/terraform/scheduler.tf`` — one ``locals.scheduled_jobs`` entry
    per handler registered here.
    """
    registry = JobRegistry()
    registry.register(TikTokReminderJob())
    registry.register(EnsureWebhookJob())
    registry.register(DailyMessageJob())
    registry.register(
        DailyMessageJob(
            name="daily-message-qa",
            chat_id_override=lambda s: s.jm_chat_id,
        )
    )
    return registry


dispatcher: Dispatcher = build_default_dispatcher()
job_registry: JobRegistry = build_default_job_registry()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Eagerly build :class:`Settings` so missing required config crashes the boot.

    Also installs the structured JSON log handler so Cloud Logging picks
    up severity and ``jsonPayload`` fields (#28).
    """
    configure_logging()
    get_settings()
    yield


app = FastAPI(
    title="Something Dashboard Telegram bot",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Cloud Run and local smoke tests."""
    return {"status": "healthy"}


@app.post(
    "/jobs/{job_name}",
    dependencies=[Depends(verify_scheduler_oidc_token)],
)
async def run_scheduled_job(
    job_name: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """Cloud Scheduler entry point. OIDC-verified by the dependency above."""
    history_logger = get_job_history_logger()
    ctx = BotContext(
        settings=settings,
        telegram_client=get_telegram_client(),
        persistence=get_persistence_service(),
        file_fetcher=get_file_fetcher(),
        openai_client=get_openai_client(),
        job_history_logger=history_logger,
    )
    started_at = datetime.now(UTC)
    try:
        await job_registry.dispatch(job_name, ctx)
    except UnknownJobError:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such job: {job_name!r}",
        ) from None
    except Exception as exc:
        await safe_record(
            history_logger,
            JobHistoryRow(
                bot_id=ctx.bot_id,
                job_name=job_name,
                status="failed",
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error_class=type(exc).__name__,
                error_message=str(exc),
            ),
        )
        raise
    await safe_record(
        history_logger,
        JobHistoryRow(
            bot_id=ctx.bot_id,
            job_name=job_name,
            status="succeeded",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        ),
    )
    return {"status": "ok", "job": job_name}


@app.post("/webhook", dependencies=[Depends(verify_telegram_webhook_secret)])
async def webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Receive, persist, dispatch, send a reply, and persist the response."""
    received_at = datetime.now(UTC)
    raw = await _safe_json(request)
    pending_action_store = get_pending_action_store()
    history_logger = get_job_history_logger()
    ctx = BotContext(
        settings=settings,
        telegram_client=get_telegram_client(),
        persistence=get_persistence_service(),
        file_fetcher=get_file_fetcher(),
        openai_client=get_openai_client(),
        pending_action_store=pending_action_store,
        job_history_logger=history_logger,
    )

    try:
        parsed = parse_update(raw)
    except MalformedUpdateError as exc:
        _logger.warning("malformed_update", extra={"error": str(exc), "bot_id": ctx.bot_id})
        _safe_persist_event(
            ctx.persistence,
            EventRecord(
                bot_id=ctx.bot_id,
                event="malformed_update",
                status="error",
                details=str(exc),
                occurred_at=received_at,
            ),
        )
        return {"status": "ok"}

    _safe_persist_raw(ctx.persistence, _build_raw_record(parsed, raw, ctx.bot_id, received_at))
    message_record = _build_message_record(parsed, ctx.bot_id, received_at)
    if message_record is not None:
        _safe_persist_message(ctx.persistence, message_record)
    file_record = _build_file_record(parsed, ctx.bot_id, received_at)
    if file_record is not None:
        _safe_persist_file(ctx.persistence, file_record)

    # Resolve pending workflow state for the sender (if any) before
    # dispatch so handlers can read it synchronously from matches().
    pending_action = await _resolve_pending_action(parsed, ctx, pending_action_store)
    ctx_with_pending = (
        ctx if pending_action is None else _replace_pending_action(ctx, pending_action)
    )

    result = await dispatcher.dispatch(parsed, ctx_with_pending)
    await _send_and_persist_reply(parsed, result, ctx_with_pending, received_at)
    _emit_dispatch_events(parsed, result, ctx_with_pending, received_at)
    await _safe_record_job_history(parsed, result, ctx_with_pending)

    return {"status": "ok"}


async def _resolve_pending_action(parsed: ParsedUpdate, ctx: BotContext, store: Any) -> Any | None:
    if store is None:
        return None
    if not isinstance(parsed, PrivateMessage | GroupMessage | SupergroupMessage):
        return None
    return await safe_get_pending_action(
        store,
        bot_id=ctx.bot_id,
        chat_id=parsed.chat_id,
        user_id=parsed.from_user.id,
    )


def _replace_pending_action(ctx: BotContext, pending_action: Any) -> BotContext:
    """Return a copy of ``ctx`` with ``pending_action`` populated."""
    from dataclasses import replace

    return replace(ctx, pending_action=pending_action)


# --------------------------------------------------------------------------- #
# Record builders
# --------------------------------------------------------------------------- #


def _build_raw_record(
    parsed: ParsedUpdate, raw: Any, bot_id: str, received_at: datetime
) -> RawUpdateRecord:
    return RawUpdateRecord(
        update_id=parsed.update_id,
        bot_id=bot_id,
        update_type=parsed.type,
        raw_payload=raw if isinstance(raw, dict) else {"_invalid_body": True},
        received_at=received_at,
    )


def _build_message_record(
    parsed: ParsedUpdate, bot_id: str, received_at: datetime
) -> MessageRecord | None:
    if isinstance(parsed, UnsupportedUpdate):
        return None

    chat_title: str | None = None
    user_id: int | None = None
    username: str | None = None
    if isinstance(parsed, GroupMessage | SupergroupMessage):
        chat_title = parsed.chat_title
        user_id = parsed.from_user.id
        username = parsed.from_user.username
    elif isinstance(parsed, PrivateMessage):
        user_id = parsed.from_user.id
        username = parsed.from_user.username
    elif isinstance(parsed, ChannelPost):
        chat_title = parsed.chat_title

    content = parsed.content
    command = content.command if isinstance(content, CommandContent) else None
    text = _extract_text(content)

    return MessageRecord(
        update_id=parsed.update_id,
        bot_id=bot_id,
        message_id=parsed.message_id,
        chat_id=parsed.chat_id,
        chat_type=parsed.chat_type,
        chat_title=chat_title,
        user_id=user_id,
        username=username,
        message_type=content.kind,
        command=command,
        text=text,
        received_at=received_at,
        processing_status="received",
    )


def _extract_text(content: Any) -> str | None:
    if isinstance(content, TextContent | CommandContent):
        return content.text
    if isinstance(content, PhotoContent | DocumentContent | VoiceContent):
        return content.caption
    return None


def _build_file_record(
    parsed: ParsedUpdate, bot_id: str, received_at: datetime
) -> FileRecord | None:
    # Channel posts and unsupported updates may carry files in the real
    # Telegram API, but the file-download flow (#20) only targets
    # user-initiated uploads. Widen this when #20 expands scope.
    if not isinstance(parsed, PrivateMessage | GroupMessage | SupergroupMessage):
        return None

    content = parsed.content
    base = {
        "update_id": parsed.update_id,
        "bot_id": bot_id,
        "chat_id": parsed.chat_id,
        "message_id": parsed.message_id,
        "received_at": received_at,
        "download_status": "pending",
    }

    if isinstance(content, PhotoContent):
        largest = max(content.photo, key=lambda p: p.file_size or 0)
        return FileRecord(
            **base,
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            file_type="photo",
            file_size_bytes=largest.file_size,
        )
    if isinstance(content, DocumentContent):
        doc = content.document
        return FileRecord(
            **base,
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            file_type="document",
            mime_type=doc.mime_type,
            file_size_bytes=doc.file_size,
            original_filename=doc.file_name,
        )
    if isinstance(content, VoiceContent):
        voice = content.voice
        return FileRecord(
            **base,
            file_id=voice.file_id,
            file_unique_id=voice.file_unique_id,
            file_type="voice",
            mime_type=voice.mime_type,
            file_size_bytes=voice.file_size,
        )
    return None


# --------------------------------------------------------------------------- #
# Send + response persistence
# --------------------------------------------------------------------------- #


async def _send_and_persist_reply(
    parsed: ParsedUpdate,
    result: HandlerResult,
    ctx: BotContext,
    received_at: datetime,
) -> None:
    if result.reply_text is None or isinstance(parsed, UnsupportedUpdate):
        return

    chat_id = parsed.chat_id
    sent_at = datetime.now(UTC)
    message_id: int | None = None
    error: str | None = None
    success = False

    client = ctx.telegram_client
    if client is None:
        error = "telegram_client_unavailable"
        _logger.warning(
            error, extra={"update_id": parsed.update_id, "handler": result.handler_name}
        )
    else:
        try:
            response = await client.send_message(chat_id=chat_id, text=result.reply_text)
        except Exception as exc:  # noqa: BLE001 — never bubble to Telegram
            error = f"{type(exc).__name__}: {exc}"
            _logger.warning(
                "telegram_send_failed",
                extra={"update_id": parsed.update_id, "error": error},
            )
        else:
            success = True
            message_id = response.get("message_id") if isinstance(response, dict) else None

    _safe_persist_response(
        ctx.persistence,
        ResponseRecord(
            bot_id=ctx.bot_id,
            in_response_to_update_id=parsed.update_id,
            chat_id=chat_id,
            message_id=message_id,
            response_type="text",
            text=result.reply_text,
            sent_at=sent_at,
            success=success,
            error=error,
        ),
    )

    _ = received_at  # kept for symmetry with the inbound flow; not used here yet.


async def _safe_record_job_history(
    parsed: ParsedUpdate,
    result: HandlerResult,
    ctx: BotContext,
) -> None:
    """Record one row in ``job_history_log`` for handled dispatches (#53).

    Skips unhandled updates entirely (the table tracks job invocations,
    not noise). Best-effort: Postgres failures are swallowed inside
    :func:`safe_record`.
    """
    if not result.handled or result.job_name is None:
        return
    if result.started_at is None or result.finished_at is None:
        return
    status = "failed" if result.error is not None else "succeeded"
    error_class = result.error.exception_type if result.error is not None else None
    error_message = result.error.message if result.error is not None else None
    chat_id, user_id = _extract_chat_and_user(parsed)
    await safe_record(
        ctx.job_history_logger,
        JobHistoryRow(
            bot_id=ctx.bot_id,
            job_name=result.job_name,
            chat_id=chat_id,
            user_id=user_id,
            status=status,
            error_class=error_class,
            error_message=error_message,
            started_at=result.started_at,
            finished_at=result.finished_at,
        ),
    )


def _extract_chat_and_user(parsed: ParsedUpdate) -> tuple[int | None, int | None]:
    if isinstance(parsed, PrivateMessage | GroupMessage | SupergroupMessage):
        return parsed.chat_id, parsed.from_user.id
    if isinstance(parsed, ChannelPost):
        return parsed.chat_id, None
    return None, None


def _emit_dispatch_events(
    parsed: ParsedUpdate,
    result: HandlerResult,
    ctx: BotContext,
    occurred_at: datetime,
) -> None:
    update_id = parsed.update_id
    if result.error is not None:
        _safe_persist_event(
            ctx.persistence,
            EventRecord(
                bot_id=ctx.bot_id,
                event="handler_errored",
                handler_name=result.error.handler_name,
                status="error",
                details=f"{result.error.exception_type}: {result.error.message}",
                update_id=update_id,
                occurred_at=occurred_at,
            ),
        )
    elif not result.handled:
        _safe_persist_event(
            ctx.persistence,
            EventRecord(
                bot_id=ctx.bot_id,
                event="update_unhandled",
                status="ok",
                update_id=update_id,
                occurred_at=occurred_at,
            ),
        )


# --------------------------------------------------------------------------- #
# Persistence wrappers — never raise out of the webhook
# --------------------------------------------------------------------------- #


def _safe_persist_raw(svc: PersistenceService | None, record: RawUpdateRecord) -> None:
    if svc is None:
        return
    try:
        svc.record_raw_update(record)
    except Exception:  # noqa: BLE001
        _logger.exception("persistence_record_raw_update_raised")


def _safe_persist_message(svc: PersistenceService | None, record: MessageRecord) -> None:
    if svc is None:
        return
    try:
        svc.record_message(record)
    except Exception:  # noqa: BLE001
        _logger.exception("persistence_record_message_raised")


def _safe_persist_file(svc: PersistenceService | None, record: FileRecord) -> None:
    if svc is None:
        return
    try:
        svc.record_file(record)
    except Exception:  # noqa: BLE001
        _logger.exception("persistence_record_file_raised")


def _safe_persist_response(svc: PersistenceService | None, record: ResponseRecord) -> None:
    if svc is None:
        return
    try:
        svc.record_response(record)
    except Exception:  # noqa: BLE001
        _logger.exception("persistence_record_response_raised")


def _safe_persist_event(svc: PersistenceService | None, record: EventRecord) -> None:
    if svc is None:
        return
    try:
        svc.record_event(record)
    except Exception:  # noqa: BLE001
        _logger.exception("persistence_record_event_raised")


async def _safe_json(request: Request) -> Any:
    """Read the request body as JSON; return ``{}`` on parse failures."""
    try:
        return await request.json()
    except Exception:
        body = await request.body()
        _logger.warning("non_json_webhook_body", extra={"body_bytes": len(body)})
        return {}
