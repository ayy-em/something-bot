"""Reunion countdown section."""

from collections.abc import Awaitable, Callable
from datetime import date

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.reunion import (
    ENJOYING_MESSAGE,
    format_reunion_line,
    is_during_reunion,
    is_reunion_expired,
)
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

_NOT_YET_KNOWN = "The next reunion date is not yet known :("

ReunionFetcher = Callable[[], Awaitable[date | None]]
DurationFetcher = Callable[[], Awaitable[int | None]]


async def _default_reunion_fetcher() -> date | None:
    from something_really_bot.features.daily_message.reunion import get_reunion_date
    from something_really_bot.persistence.postgres import get_postgres_storage

    storage = get_postgres_storage()
    if storage is None:
        return None
    return await get_reunion_date(storage)


async def _default_duration_fetcher() -> int | None:
    from something_really_bot.features.daily_message.reunion import get_reunion_duration
    from something_really_bot.persistence.postgres import get_postgres_storage

    storage = get_postgres_storage()
    if storage is None:
        return None
    return await get_reunion_duration(storage)


class ReunionSection:
    """Renders the reunion countdown or a 'not yet known' fallback."""

    name = "reunion"

    def __init__(
        self,
        *,
        reunion_fetcher: ReunionFetcher | None = None,
        duration_fetcher: DurationFetcher | None = None,
    ) -> None:
        self._reunion_fetcher = reunion_fetcher or _default_reunion_fetcher
        self._duration_fetcher = duration_fetcher or _default_duration_fetcher

    async def render(self, today: date) -> str | None:
        """Return reunion countdown, 'enjoying', 'not yet known', or ``None``."""
        reunion_date = await self._safe_fetch()
        duration = await self._safe_fetch_duration()

        if reunion_date is None:
            return f"💔 {md(_NOT_YET_KNOWN)}"

        if duration is not None:
            return self._render_with_duration(reunion_date, duration, today)

        line = format_reunion_line(reunion_date, today)
        if line is not None:
            return f"❤️ {md(line)}"
        return None

    def _render_with_duration(
        self, target: date, duration: int, today: date
    ) -> str | None:
        """Handle all three phases: countdown, enjoying, expired."""
        if is_during_reunion(target, today, duration):
            return f"❤️ {md(ENJOYING_MESSAGE)}"
        if is_reunion_expired(target, today, duration):
            return f"💔 {md(_NOT_YET_KNOWN)}"
        line = format_reunion_line(target, today)
        if line is not None:
            return f"❤️ {md(line)}"
        return None

    async def _safe_fetch(self) -> date | None:
        try:
            return await self._reunion_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_message_reunion_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def _safe_fetch_duration(self) -> int | None:
        try:
            return await self._duration_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_message_reunion_duration_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None
