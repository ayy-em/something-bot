"""FinCo daily multi-site stats digest job (#25).

Cloud Scheduler fires ``POST /jobs/finco-daily-stats`` once a day. The
job fans out per site to GA4, composes a single digest message with
graceful per-site degradation, and sends it to
``settings.something_group_chat_id``.

The job never raises — failure to send is logged + persisted with
``success=false`` and the HTTP response stays 200 so Cloud Scheduler
does not retry and double-send.

GSC integration is deferred to a separate backlog issue: see the
feature README and SPEC.md §FinCo daily stats. The runtime SA cannot
be granted GSC access via the UI (Google rejects non-Google-account
emails), so it needs a personal-OAuth + refresh-token flow that is
out of scope for the initial cutover.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from something_really_bot.features.finco_daily_stats.sites import SITES, SiteConfig
from something_really_bot.features.finco_daily_stats.source.google_analytics import (
    GoogleAnalyticsError,
    SiteMetrics,
    fetch_site_metrics,
)
from something_really_bot.logging import get_logger
from something_really_bot.persistence import ResponseRecord
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)

REPORT_TIMEZONE = ZoneInfo("Europe/Amsterdam")

GA4Fetcher = Callable[[str, date, date], Awaitable[SiteMetrics]]


@dataclass(frozen=True)
class _SitePart:
    site: SiteConfig
    ga4: SiteMetrics | None


class FinCoDailyStatsJob:
    """Scheduled job: multi-site daily website stats digest."""

    name = "finco-daily-stats"

    def __init__(
        self,
        *,
        sites: tuple[SiteConfig, ...] = SITES,
        ga4_fetcher: GA4Fetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sites = sites
        self._ga4_fetcher = ga4_fetcher or fetch_site_metrics
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, ctx: BotContext) -> None:
        chat_id = ctx.settings.something_group_chat_id
        if chat_id is None:
            _logger.error("finco_daily_stats_no_recipient_skipping")
            return

        report_date = self._yesterday_in_report_tz()
        parts = await asyncio.gather(*(self._fetch_one(site, report_date) for site in self._sites))
        text = self._compose_message(report_date, parts)
        await self._send_and_persist(ctx, chat_id, text)

    def _yesterday_in_report_tz(self) -> date:
        local_now = self._now().astimezone(REPORT_TIMEZONE)
        return (local_now - timedelta(days=1)).date()

    async def _fetch_one(self, site: SiteConfig, report_date: date) -> _SitePart:
        try:
            ga4_value = await self._ga4_fetcher(site.ga4_property_id, report_date, report_date)
        except BaseException as exc:
            _logger.warning(
                "finco_daily_stats_ga4_failed",
                extra={
                    "site": site.label,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            ga4_value = None

        return _SitePart(site=site, ga4=ga4_value)

    def _compose_message(self, report_date: date, parts: tuple[_SitePart, ...]) -> str:
        lines = [f"\U0001f4ca Daily Website Stats: {report_date.isoformat()}"]
        body_sections: list[str] = []
        for part in parts:
            section = self._compose_site_section(part)
            if section:
                body_sections.append(section)

        if not body_sections:
            return f"{lines[0]}\n\nNo data today."

        return "\n\n".join([lines[0], *body_sections])

    def _compose_site_section(self, part: _SitePart) -> str | None:
        site = part.site
        if part.ga4 is None:
            return None

        ga4 = part.ga4
        site_lines = [f"  Visitors: {ga4.total_users:,} (new: {ga4.new_users:,})"]
        if ga4.top_pages:
            site_lines.append("  Top pages:")
            for idx, page in enumerate(ga4.top_pages, start=1):
                site_lines.append(f"    {idx}. {page.page_path} — {page.views:,}")

        header = f"{site.label} ({site.domain})"
        return "\n".join([header, *site_lines])

    async def _send_and_persist(self, ctx: BotContext, chat_id: int, text: str) -> None:
        sent_at = datetime.now(UTC)
        success = False
        error: str | None = None
        message_id: int | None = None

        client = ctx.telegram_client
        if client is None:
            error = "telegram_client_unavailable"
            _logger.warning(error)
        else:
            try:
                response = await client.send_message(chat_id=chat_id, text=text)
            except Exception as exc:  # noqa: BLE001 — never let the scheduler retry
                error = f"{type(exc).__name__}: {exc}"
                _logger.warning("finco_daily_stats_send_failed", extra={"error": error})
            else:
                success = True
                message_id = response.get("message_id") if isinstance(response, dict) else None

        if ctx.persistence is not None:
            try:
                ctx.persistence.record_response(
                    ResponseRecord(
                        bot_id=ctx.bot_id,
                        chat_id=chat_id,
                        response_type="scheduled_finco_daily_stats",
                        text=text,
                        sent_at=sent_at,
                        success=success,
                        error=error,
                        message_id=message_id,
                    )
                )
            except Exception:  # noqa: BLE001 — webhook reliability promise
                _logger.exception("finco_daily_stats_persist_response_raised")


__all__ = [
    "REPORT_TIMEZONE",
    "FinCoDailyStatsJob",
    "GoogleAnalyticsError",
]
