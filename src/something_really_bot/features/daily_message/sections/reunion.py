"""Reunion countdown section."""

from collections.abc import Awaitable, Callable
from datetime import date

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.reunion import format_reunion_line
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

ReunionFetcher = Callable[[], Awaitable[date | None]]


async def _default_reunion_fetcher() -> date | None:
    from something_really_bot.features.daily_message.reunion import get_reunion_date
    from something_really_bot.persistence.postgres import get_postgres_storage

    storage = get_postgres_storage()
    if storage is None:
        return None
    return await get_reunion_date(storage)


class ReunionSection:
    """Renders the reunion countdown or a 'not yet known' fallback."""

    name = "reunion"

    def __init__(self, *, reunion_fetcher: ReunionFetcher | None = None) -> None:
        self._reunion_fetcher = reunion_fetcher or _default_reunion_fetcher

    async def render(self, today: date) -> str | None:
        """Return reunion countdown text, 'not yet known', or ``None`` if past."""
        reunion_date = await self._safe_fetch()
        if reunion_date is not None:
            line = format_reunion_line(reunion_date, today)
            if line is not None:
                return f"❤️ {md(line)}"
            return None
        return f"💔 {md('The next reunion date is not yet known :(')}"

    async def _safe_fetch(self) -> date | None:
        try:
            return await self._reunion_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_message_reunion_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None
