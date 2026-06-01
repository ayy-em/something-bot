"""``/daily_message_qa`` — trigger a QA daily message in the current chat.

Composes today's daily message and sends it to whichever chat the
command was invoked from. Intended for QA/testing; hidden from
the Telegram autocomplete menu (``show_in_menu: false``).
"""

from datetime import UTC, datetime

from something_really_bot.features.daily_message.composer import DailyMessageComposer
from something_really_bot.features.daily_message.schedule import Schedule
from something_really_bot.features.daily_message.section import Section
from something_really_bot.features.daily_message.sections.fx_rate import FxRateSection
from something_really_bot.features.daily_message.sections.on_this_day import OnThisDaySection
from something_really_bot.features.daily_message.sections.reunion import ReunionSection
from something_really_bot.features.daily_message.sections.weather import WeatherSection
from something_really_bot.features.daily_message.sections.website_stats import WebsiteStatsSection
from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.client import TelegramSendError
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
)

_logger = get_logger(__name__)

COMMAND_NAME = "/daily_message_qa"


def _default_sections() -> list[Section]:
    return [
        WeatherSection(),
        ReunionSection(),
        FxRateSection(),
        OnThisDaySection(),
        WebsiteStatsSection(),
    ]


class DailyMessageQACommandHandler:
    """``/daily_message_qa`` — send today's daily message to the current chat."""

    name = "daily_message_qa_command"

    def __init__(self) -> None:
        self._composer = DailyMessageComposer(
            sections=_default_sections(),
            schedule=Schedule.from_yaml(),
        )

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == COMMAND_NAME

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage)

        client = ctx.telegram_client
        if client is None:
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text="Telegram client unavailable.",
            )

        today = datetime.now(UTC).date()
        try:
            text = await self._composer.compose(today)
        except Exception as exc:
            _logger.exception("daily_message_qa_compose_failed")
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=f"Failed to compose daily message: {type(exc).__name__}: {exc}",
            )

        try:
            await client.send_message(
                chat_id=update.chat_id,
                text=text,
                parse_mode="MarkdownV2",
                disable_notification=True,
            )
        except TelegramSendError as exc:
            _logger.warning(
                "daily_message_qa_send_failed",
                extra={"chat_id": update.chat_id, "error": str(exc)},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=f"Composed OK but failed to send: {exc}",
            )

        return HandlerResult(handled=True, handler_name=self.name)
