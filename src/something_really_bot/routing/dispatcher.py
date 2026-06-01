"""Dispatcher: routes a parsed Telegram update to the right handler.

First-match-wins. Handlers are iterated in registration order; the first
one whose :meth:`Handler.matches` returns True receives the update. If no
handler matches, the fallback (if registered) is called. If neither
applies, the dispatcher returns an unhandled :class:`HandlerResult` and
the webhook still acks 200.

Handler exceptions are captured and surfaced as ``HandlerResult.error``;
the webhook layer never lets them turn into 5xx (SPEC §6.9 — avoid
Telegram retry storms).

When a :class:`CommandRegistry` is provided, the dispatcher enforces
``trusted_users_only`` gating: if a matched handler's registry entry has
``trusted_users_only: true`` and the sender is not in
``Settings.telegram_qa_user_ids``, the handler is not called and a
rejection reply is returned instead.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime

from something_really_bot.routing.command_registry import CommandRegistry
from something_really_bot.routing.types import (
    BotContext,
    Handler,
    HandlerError,
    HandlerResult,
)
from something_really_bot.services.job_history import derive_job_name
from something_really_bot.telegram.models import (
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
)

_logger = logging.getLogger(__name__)

UNAUTHORIZED_REPLY = "Sorry, this command is restricted."


class Dispatcher:
    """Holds the handler registry for one bot.

    Args:
        command_registry: When provided, enables ``trusted_users_only``
            gating declared in ``commands.yaml``.
    """

    def __init__(self, command_registry: CommandRegistry | None = None) -> None:
        self._handlers: list[Handler] = []
        self._fallback: Handler | None = None
        self._registry = command_registry

    def register(self, handler: Handler) -> None:
        """Append a handler to the match-order list."""
        self._handlers.append(handler)

    @property
    def handlers(self) -> tuple[Handler, ...]:
        """Snapshot of the registered handlers in match order."""
        return tuple(self._handlers)

    def set_fallback(self, handler: Handler) -> None:
        """Set the catch-all handler used when no registered handler matches."""
        self._fallback = handler

    async def dispatch(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        """Find and invoke the matching handler."""
        for handler in self._handlers:
            if handler.matches(update, ctx):
                if self._is_gated(handler, update, ctx):
                    return HandlerResult(
                        handled=True,
                        handler_name=handler.name,
                        reply_text=UNAUTHORIZED_REPLY,
                    )
                return await self._safe_handle(handler, update, ctx)

        if self._fallback is not None:
            return await self._safe_handle(self._fallback, update, ctx)

        _logger.info(
            "no_handler_matched",
            extra={"update_id": getattr(update, "update_id", None), "bot_id": ctx.bot_id},
        )
        return HandlerResult(handled=False)

    def _is_gated(self, handler: Handler, update: ParsedUpdate, ctx: BotContext) -> bool:
        """Return True if the handler requires trusted users and the sender isn't one."""
        if self._registry is None:
            return False
        entry = self._registry.get(handler.name)
        if entry is None or not entry.trusted_users_only:
            return False
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return True
        return update.from_user.id not in ctx.settings.telegram_qa_user_ids

    @staticmethod
    async def _safe_handle(
        handler: Handler, update: ParsedUpdate, ctx: BotContext
    ) -> HandlerResult:
        job_name = derive_job_name(handler)
        started_at = datetime.now(UTC)
        try:
            result = await handler.handle(update, ctx)
        except Exception as exc:  # noqa: BLE001 — webhook must never bubble
            finished_at = datetime.now(UTC)
            _logger.exception(
                "handler_raised",
                extra={
                    "handler": handler.name,
                    "update_id": getattr(update, "update_id", None),
                    "bot_id": ctx.bot_id,
                },
            )
            return HandlerResult(
                handled=True,
                handler_name=handler.name,
                error=HandlerError(
                    handler_name=handler.name,
                    exception_type=type(exc).__name__,
                    message=str(exc),
                ),
                job_name=job_name,
                started_at=started_at,
                finished_at=finished_at,
            )
        finished_at = datetime.now(UTC)
        return replace(
            result,
            job_name=result.job_name or job_name,
            started_at=result.started_at or started_at,
            finished_at=result.finished_at or finished_at,
        )
