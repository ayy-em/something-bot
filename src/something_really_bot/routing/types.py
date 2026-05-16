"""Routing-layer types: handler protocol, context, result.

Keeps the dispatcher (``dispatcher.py``) independent of concrete handler
implementations and lets feature modules depend only on these primitives.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from something_really_bot.config import Settings
from something_really_bot.persistence import PersistenceService
from something_really_bot.telegram.models import ParsedUpdate


@dataclass(frozen=True)
class BotContext:
    """Per-request bag of handles passed to every handler.

    ``settings`` is the only field guaranteed to be populated today. Service
    handles are pre-declared as ``None`` placeholders so the shape is stable
    and the issues that introduce them (#15, #18, #20) only add wiring, not
    type churn.
    """

    settings: Settings
    bot_id: str = "default"
    telegram_client: Any | None = None
    persistence: PersistenceService | None = None
    # Filled in by the GCS file-storage issue (#20). Typed as Any so feature
    # code can be written against it before the concrete service exists.
    gcs_client: Any | None = None


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
    """

    handled: bool
    handler_name: str | None = None
    reply_text: str | None = None
    persist_response: bool = True
    error: HandlerError | None = None
    extras: dict[str, Any] = field(default_factory=dict)


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
