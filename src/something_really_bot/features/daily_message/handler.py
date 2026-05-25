"""Daily message job.

Cloud Scheduler fires ``POST /jobs/daily-message`` once a day at 05:05 UTC.
The job loads a YAML schedule to determine which sections to include for
today's weekday, fetches each section's data in parallel, composes a
single MarkdownV2 message, and sends it silently to the configured
group chat.

Mon-Thu & Sat-Sun: weather, reunion, FX rate, on-this-day.
Friday: all of the above + weekly website stats.

The job never raises -- failure to send is logged + persisted with
``success=false`` and the HTTP response stays 200 so Cloud Scheduler
does not retry and double-send.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from something_really_bot.config import Settings
from something_really_bot.features.daily_message.composer import DailyMessageComposer
from something_really_bot.features.daily_message.schedule import Schedule
from something_really_bot.features.daily_message.section import Section
from something_really_bot.features.daily_message.sections.fx_rate import FxRateSection
from something_really_bot.features.daily_message.sections.on_this_day import OnThisDaySection
from something_really_bot.features.daily_message.sections.reunion import ReunionSection
from something_really_bot.features.daily_message.sections.weather import WeatherSection
from something_really_bot.features.daily_message.sections.website_stats import WebsiteStatsSection
from something_really_bot.logging import get_logger
from something_really_bot.persistence import ResponseRecord
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)


def _default_sections() -> list[Section]:
    """Build the production section instances with real fetchers."""
    return [
        WeatherSection(),
        ReunionSection(),
        FxRateSection(),
        OnThisDaySection(),
        WebsiteStatsSection(),
    ]


class DailyMessageJob:
    """Scheduled job: modular daily message with YAML-driven section schedule."""

    def __init__(
        self,
        *,
        name: str = "daily-message",
        chat_id_override: Callable[[Settings], int | None] | None = None,
        sections: list[Section] | None = None,
        schedule: Schedule | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.name = name
        self._chat_id_override = chat_id_override
        self._composer = DailyMessageComposer(
            sections=sections or _default_sections(),
            schedule=schedule or Schedule.from_yaml(),
        )
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, ctx: BotContext) -> None:
        """Execute the daily message job."""
        if self._chat_id_override is not None:
            chat_id = self._chat_id_override(ctx.settings)
        else:
            chat_id = ctx.settings.something_group_chat_id
        if chat_id is None:
            _logger.error("daily_message_no_recipient_skipping")
            return

        today = self._now().date()
        text = await self._composer.compose(today)
        await self._send_and_persist(ctx, chat_id, text)

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
                response = await client.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                _logger.warning("daily_message_send_failed", extra={"error": error})
            else:
                success = True
                message_id = response.get("message_id") if isinstance(response, dict) else None

        if ctx.persistence is not None:
            try:
                ctx.persistence.record_response(
                    ResponseRecord(
                        bot_id=ctx.bot_id,
                        chat_id=chat_id,
                        response_type="scheduled_daily_message",
                        text=text,
                        sent_at=sent_at,
                        success=success,
                        error=error,
                        message_id=message_id,
                    )
                )
            except Exception:  # noqa: BLE001
                _logger.exception("daily_message_persist_response_raised")
