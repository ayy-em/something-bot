"""'On This Day' historical fact section."""

from collections.abc import Awaitable, Callable
from datetime import date

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.sources.wikipedia_otd import fetch_on_this_day
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

OTDFetcher = Callable[[date], Awaitable[str]]


class OnThisDaySection:
    """Renders a random historical event for today's date."""

    name = "on_this_day"

    def __init__(self, *, otd_fetcher: OTDFetcher | None = None) -> None:
        self._otd_fetcher = otd_fetcher or fetch_on_this_day

    async def render(self, today: date) -> str | None:
        """Fetch and format a historical fact, or ``None`` on failure."""
        text = await self._safe_fetch(today)
        if text is None:
            return None
        return f"\U0001f4dc{md('This day in history:')}\n{md(text)}"

    async def _safe_fetch(self, today: date) -> str | None:
        try:
            return await self._otd_fetcher(today)
        except BaseException as exc:
            _logger.warning(
                "daily_message_otd_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None
