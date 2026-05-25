"""``/next-reunion`` command handler (#58).

Sets or queries the next reunion date. The date is stored in Postgres
and consumed by the daily weather job to render a countdown line.

Works in private chats, groups, and supergroups.
"""

from datetime import date, datetime

from something_really_bot.features.daily_message.reunion import (
    get_reunion_date,
    set_reunion_date,
)
from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import PostgresStorage, get_postgres_storage
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
)

_logger = get_logger(__name__)


class NextReunionHandler:
    """``/next-reunion [YYYY-MM-DD]`` — set or view the next reunion date."""

    name = "next_reunion"
    command = "/next-reunion"
    description = "Set or view the next reunion date."
    help_usage = "/next-reunion [YYYY-MM-DD]"

    def __init__(
        self,
        *,
        storage_getter: type[None] | None = None,
        _storage: PostgresStorage | None = None,
    ) -> None:
        self._storage = _storage

    def _get_storage(self) -> PostgresStorage | None:
        """Resolve storage: injected instance for tests, global for production."""
        if self._storage is not None:
            return self._storage
        return get_postgres_storage()

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        """Match ``/next-reunion`` in private, group, and supergroup chats."""
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command

    async def handle(self, update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        """Process the command: set a new date or query the current one."""
        content = update.content
        if not isinstance(content, CommandContent):
            return HandlerResult(handled=False)

        storage = self._get_storage()
        if storage is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Storage is not configured. Cannot manage reunion dates.",
            )

        args = content.args
        if args:
            return await self._set_date(storage, args.strip())
        return await self._query_date(storage)

    async def _set_date(self, storage: PostgresStorage, raw_date: str) -> HandlerResult:
        """Parse and store the new reunion date."""
        try:
            target = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=f"Invalid date format: {raw_date}\nPlease use YYYY-MM-DD.",
            )

        try:
            await set_reunion_date(storage, target)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "next_reunion_set_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Failed to save the reunion date. Please try again later.",
            )

        days = (target - date.today()).days
        if days > 0:
            reply = f"Next reunion set to {target.isoformat()}. {days} days from now!"
        elif days == 0:
            reply = f"Next reunion set to {target.isoformat()}. That's today!"
        else:
            reply = f"Next reunion set to {target.isoformat()}. (That date is in the past.)"

        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)

    async def _query_date(self, storage: PostgresStorage) -> HandlerResult:
        """Return the current reunion date or a 'not set' message."""
        try:
            target = await get_reunion_date(storage)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "next_reunion_query_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Failed to retrieve the reunion date. Please try again later.",
            )

        if target is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="The next reunion date is not yet known :(",
            )

        days = (target - date.today()).days
        if days > 0:
            reply = f"Next reunion: {target.isoformat()} ({days} days from now)."
        elif days == 0:
            reply = f"Next reunion: {target.isoformat()} — that's today!"
        else:
            reply = (
                f"The last reunion date was {target.isoformat()} ({-days} days ago). "
                "Set a new one with /next-reunion YYYY-MM-DD."
            )

        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
