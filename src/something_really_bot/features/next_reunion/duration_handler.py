"""``/next_reunion_duration`` command handler (#60).

Sets or queries how many days the next reunion lasts. When a duration is
stored, the daily message shows an "enjoying time together" line for the
full reunion period, then falls back to "next date unknown" once it ends.

Works in private chats, groups, and supergroups.
"""

from something_really_bot.features.daily_message.reunion import (
    get_reunion_date,
    get_reunion_duration,
    set_reunion_duration,
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


class NextReunionDurationHandler:
    """``/next_reunion_duration [days]`` — set or view reunion duration."""

    name = "next_reunion_duration"
    command = "/next_reunion_duration"

    def __init__(
        self,
        *,
        _storage: PostgresStorage | None = None,
    ) -> None:
        self._storage = _storage

    def _get_storage(self) -> PostgresStorage | None:
        """Resolve storage: injected instance for tests, global for production."""
        if self._storage is not None:
            return self._storage
        return get_postgres_storage()

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        """Match ``/next_reunion_duration`` in private, group, and supergroup chats."""
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command

    async def handle(self, update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        """Process the command: set a new duration or query the current one."""
        content = update.content
        if not isinstance(content, CommandContent):
            return HandlerResult(handled=False)

        storage = self._get_storage()
        if storage is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Storage is not configured. Cannot manage reunion duration.",
            )

        args = content.args
        if args:
            return await self._set_duration(storage, args.strip())
        return await self._query_duration(storage)

    async def _set_duration(self, storage: PostgresStorage, raw: str) -> HandlerResult:
        """Parse and store the reunion duration."""
        try:
            days = int(raw)
        except ValueError:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=(
                    f"Invalid duration: {raw}\nPlease provide a positive whole number of days."
                ),
            )

        if days < 1:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Duration must be at least 1 day.",
            )

        try:
            reunion_date = await get_reunion_date(storage)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "next_reunion_duration_check_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Failed to check the reunion date. Please try again later.",
            )

        if reunion_date is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="No reunion date is set. Use /next_reunion YYYY-MM-DD first.",
            )

        try:
            await set_reunion_duration(storage, days)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "next_reunion_duration_set_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Failed to save the reunion duration. Please try again later.",
            )

        reply = (
            f"Reunion duration set to {days} day{'s' if days != 1 else ''} "
            f"(from {reunion_date.isoformat()})."
        )
        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)

    async def _query_duration(self, storage: PostgresStorage) -> HandlerResult:
        """Return the current reunion duration or a 'not set' message."""
        try:
            duration = await get_reunion_duration(storage)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "next_reunion_duration_query_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Failed to retrieve the reunion duration. Please try again later.",
            )

        if duration is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=(
                    "No reunion duration is set. Use /next_reunion_duration <days> to set one."
                ),
            )

        reply = f"Current reunion duration: {duration} day{'s' if duration != 1 else ''}."
        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
