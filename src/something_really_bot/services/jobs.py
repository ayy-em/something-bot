"""Scheduled-job registry and dispatch (#22).

Jobs run from Cloud Scheduler hitting ``POST /jobs/<name>``. The
registry is filled at app startup; the webhook route looks the
requested name up and runs it, or returns 404. Each job is a plain
:class:`JobHandler` — same shape as a regular feature handler but
without an inbound update.
"""

from typing import Protocol, runtime_checkable

from something_really_bot.routing.types import BotContext


@runtime_checkable
class JobHandler(Protocol):
    """Contract every scheduled-job handler implements."""

    name: str

    async def run(self, ctx: BotContext) -> None:
        """Execute the job. Exceptions propagate to the caller, which
        translates them into structured logs and a 5xx so Cloud Scheduler
        records the failure and retries per policy."""


class UnknownJobError(KeyError):
    """Raised when the registry has no handler for the requested name."""


class JobRegistry:
    """In-memory mapping of ``name`` → :class:`JobHandler`."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, handler: JobHandler) -> None:
        if handler.name in self._handlers:
            raise ValueError(f"Duplicate job registration: {handler.name!r}")
        self._handlers[handler.name] = handler

    def names(self) -> list[str]:
        return sorted(self._handlers)

    async def dispatch(self, name: str, ctx: BotContext) -> None:
        handler = self._handlers.get(name)
        if handler is None:
            raise UnknownJobError(name)
        await handler.run(ctx)
