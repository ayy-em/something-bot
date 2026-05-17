"""Routing-layer types: handler protocol, context, result.

Keeps the dispatcher (``dispatcher.py``) independent of concrete handler
implementations and lets feature modules depend only on these primitives.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from something_really_bot.config import Settings
from something_really_bot.file_storage import FileFetcher
from something_really_bot.persistence import PersistenceService
from something_really_bot.services.pending_actions import (
    PendingAction,
    PendingActionStore,
)
from something_really_bot.telegram.models import ParsedUpdate


@dataclass(frozen=True)
class BotContext:
    """Per-request bag of handles passed to every handler.

    ``settings`` is the only field guaranteed to be populated today. Service
    handles are pre-declared as ``None`` placeholders so the shape is stable
    and the issues that introduce them (#15, #18, #20) only add wiring, not
    type churn.

    ``pending_action`` is pre-resolved by the webhook orchestrator so
    ``Handler.matches()`` (which is synchronous) can read it without an
    awaitable call. ``None`` means "no un-expired pending action for this
    (chat, user)."
    """

    settings: Settings
    bot_id: str = "default"
    telegram_client: Any | None = None
    persistence: PersistenceService | None = None
    file_fetcher: FileFetcher | None = None
    openai_client: Any | None = None
    pending_action: PendingAction | None = None
    pending_action_store: PendingActionStore | None = None
    job_history_logger: Any | None = None


@dataclass(frozen=True)
class HandlerError:
    """Captured exception from a handler. Webhook still returns 200."""

    handler_name: str
    exception_type: str
    message: str


@dataclass(frozen=True)
class HandlerResult:
    """Outcome of a single dispatch.

    Attributes:
        handled: True if any handler (including fallback) ran.
        handler_name: Name of the handler that ran; ``None`` when unhandled.
        reply_text: Text to send back to the user (wired up to Telegram
            send in #15). ``None`` means "don't reply".
        persist_response: Whether the response (if any) should be persisted
            to BigQuery (#18). Default ``True``.
        error: Set when the handler raised; the dispatcher still returns a
            result so the webhook can ack 200 and log the failure.
        extras: Open-ended dict for handler-specific signals (e.g. file
            metadata for downstream async processing in #20).
        job_name: Folder-name derived from the handler's module path
            (e.g. ``voice_transcription``), populated by the dispatcher
            so the webhook can write the row to ``job_history_log`` (#53).
        started_at / finished_at: Wall-clock bounds of the
            ``handler.handle`` call, populated by the dispatcher.
    """

    handled: bool
    handler_name: str | None = None
    reply_text: str | None = None
    persist_response: bool = True
    error: HandlerError | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    job_name: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@runtime_checkable
class Handler(Protocol):
    """Contract every feature handler implements.

    Implementations live under ``src/something_really_bot/features/<name>/``.
    Registered with a :class:`Dispatcher` instance at app startup.
    """

    name: str

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        """Return True if this handler wants to process ``update``."""
        ...

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        """Process ``update`` and return what to reply / persist / log."""
        ...
