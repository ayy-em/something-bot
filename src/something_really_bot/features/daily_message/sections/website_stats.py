"""Weekly website stats section with week-on-week comparison.

Fetches GA4 visitor counts and GSC search metrics for two consecutive
7-day windows (ending yesterday), then renders a per-site comparison.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.sites import SITES, SiteConfig
from something_really_bot.features.daily_message.sources.google_analytics import (
    SiteMetrics,
    fetch_site_metrics,
)
from something_really_bot.features.daily_message.sources.google_search_console import (
    SiteSearchMetrics,
    fetch_site_search_metrics,
)
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

GA4Fetcher = Callable[[str, date, date], Awaitable[SiteMetrics]]
GSCFetcher = Callable[[str, date, date], Awaitable[SiteSearchMetrics]]


@dataclass(frozen=True)
class _SiteWeeklyResult:
    site: SiteConfig
    ga4_current: SiteMetrics | None
    ga4_previous: SiteMetrics | None
    gsc_current: SiteSearchMetrics | None
    gsc_previous: SiteSearchMetrics | None


class WebsiteStatsSection:
    """Renders weekly website stats with week-on-week comparison."""

    name = "website_stats"

    def __init__(
        self,
        *,
        sites: tuple[SiteConfig, ...] = SITES,
        ga4_fetcher: GA4Fetcher | None = None,
        gsc_fetcher: GSCFetcher | None = None,
    ) -> None:
        self._sites = sites
        self._ga4_fetcher = ga4_fetcher or fetch_site_metrics
        self._gsc_fetcher = gsc_fetcher or fetch_site_search_metrics

    async def render(self, today: date) -> str | None:
        """Fetch weekly stats for all sites and compose MarkdownV2."""
        yesterday = today - timedelta(days=1)
        cur_end = yesterday
        cur_start = cur_end - timedelta(days=6)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)

        results = await asyncio.gather(
            *(self._fetch_site(s, cur_start, cur_end, prev_start, prev_end) for s in self._sites)
        )

        blocks: list[str] = []
        for result in results:
            block = self._format_site(result)
            if block is not None:
                blocks.append(block)

        if not blocks:
            return None

        header = f"\U0001f4ca {md('Weekly Website Stats')}"
        return "\n\n".join([header, *blocks])

    async def _fetch_site(
        self,
        site: SiteConfig,
        cur_start: date,
        cur_end: date,
        prev_start: date,
        prev_end: date,
    ) -> _SiteWeeklyResult:
        ga4_cur, ga4_prev, gsc_cur, gsc_prev = await asyncio.gather(
            self._safe_ga4(site, cur_start, cur_end),
            self._safe_ga4(site, prev_start, prev_end),
            self._safe_gsc(site, cur_start, cur_end),
            self._safe_gsc(site, prev_start, prev_end),
        )
        return _SiteWeeklyResult(
            site=site,
            ga4_current=ga4_cur,
            ga4_previous=ga4_prev,
            gsc_current=gsc_cur,
            gsc_previous=gsc_prev,
        )

    async def _safe_ga4(self, site: SiteConfig, start: date, end: date) -> SiteMetrics | None:
        try:
            return await self._ga4_fetcher(site.ga4_property_id, start, end)
        except BaseException as exc:
            _logger.warning(
                "daily_message_ga4_failed",
                extra={"site": site.label, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def _safe_gsc(self, site: SiteConfig, start: date, end: date) -> SiteSearchMetrics | None:
        if site.gsc_site_url is None:
            return None
        try:
            return await self._gsc_fetcher(site.gsc_site_url, start, end)
        except BaseException as exc:
            _logger.warning(
                "daily_message_gsc_failed",
                extra={"site": site.label, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    def _format_site(self, result: _SiteWeeklyResult) -> str | None:
        has_ga4 = result.ga4_current is not None
        has_gsc = result.gsc_current is not None
        if not has_ga4 and not has_gsc:
            return None

        site = result.site
        lines: list[str] = []

        if has_ga4:
            ga4 = result.ga4_current
            assert ga4 is not None
            visitors_str = f"{ga4.total_users:,}"
            wow = _wow_pct(ga4.total_users, _prev_users(result.ga4_previous))
            if wow is not None:
                lines.append(f"  \U0001f440 Visitors: {md(visitors_str)} \\({md(wow)}\\)")
            else:
                lines.append(f"  \U0001f440 Visitors: {md(visitors_str)}")

            if ga4.top_pages:
                lines.append("  \U0001f51d Top pages:")
                for idx, page in enumerate(ga4.top_pages, start=1):
                    display = page.page_path.removeprefix("/") or "Homepage"
                    lines.append(f"    {idx}\\. {md(display)} — {md(f'{page.views:,}')}")

        if has_gsc:
            gsc = result.gsc_current
            assert gsc is not None
            clicks_str = f"{gsc.clicks:,}"
            impr_str = f"{gsc.impressions:,}"
            wow_clicks = _wow_pct(gsc.clicks, _prev_clicks(result.gsc_previous))
            if wow_clicks is not None:
                lines.append(
                    f"  \U0001f50d Search: {md(clicks_str)} clicks \\({md(wow_clicks)}\\), "
                    f"{md(impr_str)} impressions"
                )
            else:
                lines.append(
                    f"  \U0001f50d Search: {md(clicks_str)} clicks, {md(impr_str)} impressions"
                )

        header = f"{md(site.label)} \\({md(site.domain)}\\)"
        return "\n".join([header, *lines])


def _prev_users(prev: SiteMetrics | None) -> int | None:
    return prev.total_users if prev is not None else None


def _prev_clicks(prev: SiteSearchMetrics | None) -> int | None:
    return prev.clicks if prev is not None else None


def _wow_pct(current: int, previous: int | None) -> str | None:
    """Return a formatted WoW change string like ``+15%`` or ``-3%``."""
    if previous is None or previous == 0:
        return None
    change = ((current - previous) / previous) * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.0f}% WoW"
