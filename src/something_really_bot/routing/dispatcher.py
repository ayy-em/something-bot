"""Dispatcher: routes a parsed Telegram update to the right handler.

First-match-wins. Handlers are iterated in registration order; the first
one whose :meth:`Handler.matches` returns True receives the update. If no
handler matches, the fallback (if registered) is called. If neither
applies, the dispatcher returns an unhandled :class:`HandlerResult` and
the webhook still acks 200.

Handler exceptions are captured and surfaced as ``HandlerResult.error``;
the webhook layer never lets them turn into 5xx (SPEC §6.9 — avoid
Telegram retry storms).
"""

import logging

from something_really_bot.routing.types import (
    BotContext,
    Handler,
    HandlerError,
    HandlerResult,
)
from something_really_bot.telegram.models import ParsedUpdate

_logger = logging.getLogger(__name__)


class Dispatcher:
    """Holds the handler registry for one bot.

    Adding a new feature is a two-step recipe:

    1. Implement a class satisfying :class:`Handler` under
       ``src/something_really_bot/features/<name>/``.
    2. Instantiate and ``register`` it on the default dispatcher in
       ``main.py``.

    Tests construct a fresh :class:`Dispatcher` per case rather than
    relying on a shared global.
    """

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._fallback: Handler | None = None

    def register(self, handler: Handler) -> None:
        """Append a handler to the match-order list."""
        self._handlers.append(handler)

    def set_fallback(self, handler: Handler) -> None:
        """Set the catch-all handler used when no registered handler matches."""
        self._fallback = handler

    async def dispatch(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        """Find and invoke the matching handler.

        Args:
            update: The classified Telegram update from
                :func:`something_really_bot.telegram.parser.parse_update`.
            ctx: The request-scoped bot context.

        Returns:
            A :class:`HandlerResult`. Always returned, never raised.
        """
        for handler in self._handlers:
            if handler.matches(update, ctx):
                return await self._safe_handle(handler, update, ctx)

        if self._fallback is not None:
            return await self._safe_handle(self._fallback, update, ctx)

        _logger.info(
            "no_handler_matched",
            extra={"update_id": getattr(update, "update_id", None), "bot_id": ctx.bot_id},
        )
        return HandlerResult(handled=False)

    @staticmethod
    async def _safe_handle(
        handler: Handler, update: ParsedUpdate, ctx: BotContext
    ) -> HandlerResult:
        try:
            return await handler.handle(update, ctx)
        except Exception as exc:  # noqa: BLE001 — webhook must never bubble
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
            )
