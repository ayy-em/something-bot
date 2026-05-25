"""EUR/RUB exchange rate section."""

from collections.abc import Awaitable, Callable
from datetime import date

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.sources.fx_rates import fetch_eur_rub_rate
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

RateFetcher = Callable[[], Awaitable[float]]


class FxRateSection:
    """Renders the daily EUR/RUB exchange rate."""

    name = "fx_rate"

    def __init__(self, *, rate_fetcher: RateFetcher | None = None) -> None:
        self._rate_fetcher = rate_fetcher or fetch_eur_rub_rate

    async def render(self, today: date) -> str | None:
        """Fetch rate and format as MarkdownV2, or ``None`` on failure."""
        rate = await self._safe_fetch()
        if rate is None:
            return None
        rate_str = f"{rate:.2f}".replace(".", ",")
        rate_text = f"Today's exchange rate: €1 = {rate_str} RUB."
        return f"\U0001f4b6 {md(rate_text)}"

    async def _safe_fetch(self) -> float | None:
        try:
            return await self._rate_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_message_rate_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None
