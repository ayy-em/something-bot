"""Static placeholder handlers for ``/start`` and ``/help`` (SPEC §6.6).

Both commands reply to *any* user (QA allowlist does not apply — these are
the discovery commands new users hit before authorization matters), but
only in private chats; SPEC §6.3 forbids replying in groups/channels.

The parser (:mod:`something_really_bot.telegram.parser`) already strips
Telegram's ``@bot_name`` suffix from the ``command`` field, so matching
``"/start"`` is sufficient — both ``/start`` and ``/start@SomethingReallyBot``
arrive here as ``command="/start"``.

The reply text lives in module-level constants so #27 (auto-generated /help
from the feature registry) can swap the help body without touching the
matching logic.
"""

from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    CommandContent,
    ParsedUpdate,
    PrivateMessage,
)

_logger = get_logger(__name__)

START_REPLY = "Something Really Bot is online. More features coming soon."
HELP_REPLY = "Help is not implemented yet. This bot is being rebuilt."


class _StaticCommandHandler:
    """Base class: match a single command in a private chat, reply with static text."""

    name: str
    command: str
    reply_text: str

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)

        client = ctx.telegram_client
        if client is None:
            _logger.warning(
                "telegram_client_unavailable_skipping_reply",
                extra={"update_id": update.update_id, "handler": self.name},
            )
            return HandlerResult(handled=True, handler_name=self.name, reply_text=self.reply_text)

        await client.send_message(chat_id=update.chat_id, text=self.reply_text)
        return HandlerResult(handled=True, handler_name=self.name, reply_text=self.reply_text)


class StartCommandHandler(_StaticCommandHandler):
    name = "commands.start"
    command = "/start"
    reply_text = START_REPLY


class HelpCommandHandler(_StaticCommandHandler):
    name = "commands.help"
    command = "/help"
    reply_text = HELP_REPLY
