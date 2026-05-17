"""Daily digest job (#25, generalized in #54, GSC added in #51).

Cloud Scheduler fires ``POST /jobs/daily-digest`` once a day. The job
fans out per site to GA4 + GSC in parallel, composes a single digest
message with graceful per-source / per-site degradation, appends a
tally of job invocations over the trailing 24 hours when
``job_history_log`` has rows in that window, and sends the result to
``settings.something_group_chat_id``.

The job never raises — failure to send is logged + persisted with
``success=false`` and the HTTP response stays 200 so Cloud Scheduler
does not retry and double-send.

GSC rides on a personal-OAuth refresh token (#51) because the runtime
SA can't be added as a GSC user. The relevant secrets are
``GOOGLE_OAUTH_SECRET_JSON`` (Desktop OAuth client JSON) and
``GSC_OAUTH_REFRESH_TOKEN`` (minted via
``scripts/grant_gsc_refresh_token.py``). When either is unset, the
GSC section drops per-site without affecting GA4 lines.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from something_really_bot.features.daily_digest.sites import SITES, SiteConfig
from something_really_bot.features.daily_digest.source.google_analytics import (
    GoogleAnalyticsError,
    SiteMetrics,
    fetch_site_metrics,
)
from something_really_bot.features.daily_digest.source.google_search_console import (
    SiteSearchMetrics,
    fetch_site_search_metrics,
)
from something_really_bot.logging import get_logger
from something_really_bot.persistence import ResponseRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.services.job_history import JobTallyRow

_logger = get_logger(__name__)

REPORT_TIMEZONE = ZoneInfo("Europe/Amsterdam")
JOB_TALLY_WINDOW = timedelta(hours=24)

GA4Fetcher = Callable[[str, date, date], Awaitable[SiteMetrics]]
GSCFetcher = Callable[[str, date, date], Awaitable[SiteSearchMetrics]]


@dataclass(frozen=True)
class _SitePart:
    site: SiteConfig
    ga4: SiteMetrics | None
    gsc: SiteSearchMetrics | None


class DailyDigestJob:
    """Scheduled job: daily website-stats digest + 24h job tally."""

    name = "daily-digest"

    def __init__(
        self,
        *,
        sites: tuple[SiteConfig, ...] = SITES,
        ga4_fetcher: GA4Fetcher | None = None,
        gsc_fetcher: GSCFetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sites = sites
        self._ga4_fetcher = ga4_fetcher or fetch_site_metrics
        self._gsc_fetcher = gsc_fetcher or fetch_site_search_metrics
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, ctx: BotContext) -> None:
        chat_id = ctx.settings.something_group_chat_id
        if chat_id is None:
            _logger.error("daily_digest_no_recipient_skipping")
            return

        report_date = self._yesterday_in_report_tz()
        parts = await asyncio.gather(*(self._fetch_one(site, report_date) for site in self._sites))
        tally = await self._fetch_tally(ctx)
        text = self._compose_message(report_date, parts, tally)
        await self._send_and_persist(ctx, chat_id, text)

    def _yesterday_in_report_tz(self) -> date:
        local_now = self._now().astimezone(REPORT_TIMEZONE)
        return (local_now - timedelta(days=1)).date()

    async def _fetch_one(self, site: SiteConfig, report_date: date) -> _SitePart:
        ga4_task = self._safe_fetch_ga4(site, report_date)
        gsc_task = self._safe_fetch_gsc(site, report_date)
        ga4_value, gsc_value = await asyncio.gather(ga4_task, gsc_task)
        return _SitePart(site=site, ga4=ga4_value, gsc=gsc_value)

    async def _safe_fetch_ga4(self, site: SiteConfig, report_date: date) -> SiteMetrics | None:
        try:
            return await self._ga4_fetcher(site.ga4_property_id, report_date, report_date)
        except BaseException as exc:
            _logger.warning(
                "daily_digest_ga4_failed",
                extra={
                    "site": site.label,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

    async def _safe_fetch_gsc(
        self, site: SiteConfig, report_date: date
    ) -> SiteSearchMetrics | None:
        if site.gsc_site_url is None:
            return None
        try:
            return await self._gsc_fetcher(site.gsc_site_url, report_date, report_date)
        except BaseException as exc:
            _logger.warning(
                "daily_digest_gsc_failed",
                extra={
                    "site": site.label,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

    async def _fetch_tally(self, ctx: BotContext) -> list[JobTallyRow]:
        """Aggregate 24h job-history. Postgres failures degrade to empty."""
        logger = ctx.job_history_logger
        if logger is None:
            return []
        try:
            return await logger.fetch_recent_summary(
                bot_id=ctx.bot_id,
                window=JOB_TALLY_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't break the digest
            _logger.warning(
                "daily_digest_tally_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return []

    def _compose_message(
        self,
        report_date: date,
        parts: tuple[_SitePart, ...],
        tally: list[JobTallyRow],
    ) -> str:
        header = f"\U0001f4ca Daily Website Stats: **{report_date.isoformat()}**"
        body_sections: list[str] = []
        for part in parts:
            section = self._compose_site_section(part)
            if section:
                body_sections.append(section)

        tally_section = self._compose_tally_section(tally)

        if not body_sections and tally_section is None:
            return f"{header}\n\nNo data today."

        sections: list[str] = [header, *body_sections]
        if tally_section is not None:
            sections.append(tally_section)
        return "\n\n".join(sections)

    def _compose_site_section(self, part: _SitePart) -> str | None:
        # Drop the site entirely only if every source we tried failed.
        # GA4-only failure still renders the GSC line and vice versa.
        if part.ga4 is None and part.gsc is None:
            return None

        site = part.site
        site_lines: list[str] = []

        if part.ga4 is not None:
            ga4 = part.ga4
            site_lines.append(
                f"  :eyes: Visitors: {ga4.total_users:,} ({ga4.new_users:,} new), "
                f"{ga4.total_users_7d:,} last 7 days"
            )

        if part.gsc is not None:
            gsc = part.gsc
            site_lines.append(
                f"  :mag: Search: {gsc.clicks:,} clicks, {gsc.impressions:,} impressions"
            )

        if part.ga4 is not None and part.ga4.top_pages:
            site_lines.append("  :top: Top pages:")
            for idx, page in enumerate(part.ga4.top_pages, start=1):
                display_path = page.page_path.removeprefix("/") or "Homepage"
                site_lines.append(f"    {idx}. {display_path} — {page.views:,}")

        header = f"{site.label} ({site.domain})"
        return "\n".join([header, *site_lines])

    def _compose_tally_section(self, tally: list[JobTallyRow]) -> str | None:
        if not tally:
            return None
        max_name_width = max(len(row.job_name) for row in tally)
        lines = ["Jobs (last 24h)"]
        for row in tally:
            counts: list[str] = []
            if row.succeeded:
                counts.append(f"{row.succeeded} ok")
            if row.failed:
                counts.append(f"{row.failed} failed")
            lines.append(f"  {row.job_name.ljust(max_name_width)}  {', '.join(counts)}")
        return "\n".join(lines)

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
                _logger.warning("daily_digest_send_failed", extra={"error": error})
            else:
                success = True
                message_id = response.get("message_id") if isinstance(response, dict) else None

        if ctx.persistence is not None:
            try:
                ctx.persistence.record_response(
                    ResponseRecord(
                        bot_id=ctx.bot_id,
                        chat_id=chat_id,
                        response_type="scheduled_daily_digest",
                        text=text,
                        sent_at=sent_at,
                        success=success,
                        error=error,
                        message_id=message_id,
                    )
                )
            except Exception:  # noqa: BLE001 — webhook reliability promise
                _logger.exception("daily_digest_persist_response_raised")


__all__ = [
    "JOB_TALLY_WINDOW",
    "REPORT_TIMEZONE",
    "DailyDigestJob",
    "GoogleAnalyticsError",
]
