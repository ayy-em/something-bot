"""``/start`` and ``/help`` command handlers (SPEC §6.6, #27).

Both commands reply to *any* user (QA allowlist does not apply — these are
the discovery commands new users hit before authorization matters), but
only in private chats; SPEC §6.3 forbids replying in groups/channels.

The parser (:mod:`something_really_bot.telegram.parser`) already strips
Telegram's ``@bot_name`` suffix from the ``command`` field, so matching
``"/start"`` is sufficient — both ``/start`` and ``/start@SomethingReallyBot``
arrive here as ``command="/start"``.

``/help`` is auto-generated from the feature registry: it walks the
dispatcher's registered handlers and prints one bullet per handler with
a non-empty ``description``. See ``routing/help_registry.py``.
"""

from something_really_bot.routing.help_registry import HelpRegistry
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    CommandContent,
    ParsedUpdate,
    PrivateMessage,
)

START_REPLY = "Something Really Bot is online. More features coming soon."


class _StaticCommandHandler:
    """Base class: match a single command in a private chat, return static text."""

    name: str
    command: str
    reply_text: str
    description: str = ""
    help_usage: str | None = None

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        return HandlerResult(handled=True, handler_name=self.name, reply_text=self.reply_text)


class StartCommandHandler(_StaticCommandHandler):
    name = "commands.start"
    command = "/start"
    reply_text = START_REPLY
    description = "Greeting + intro message."
    help_usage = "/start"


class HelpCommandHandler:
    """``/help`` command — rendered from the feature registry on each call."""

    name = "commands.help"
    command = "/help"
    description = "Show this help message."
    help_usage = "/help"

    def __init__(self, registry: HelpRegistry | None = None) -> None:
        self._registry = registry or HelpRegistry(lambda: ())

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        return HandlerResult(
            handled=True,
            handler_name=self.name,
            reply_text=self._registry.render(),
        )
