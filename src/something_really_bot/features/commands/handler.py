"""``/start`` and ``/help`` command handlers (SPEC §6.6, #27).

Both commands reply to *any* user (QA allowlist does not apply — these are
the discovery commands new users hit before authorization matters), but
only in private chats; SPEC §6.3 forbids replying in groups/channels.

The parser (:mod:`something_really_bot.telegram.parser`) already strips
Telegram's ``@bot_name`` suffix from the ``command`` field, so matching
``"/start"`` is sufficient — both ``/start`` and ``/start@SomethingReallyBot``
arrive here as ``command="/start"``.

Both ``/start`` and ``/help`` render their feature list from
:class:`HelpRegistry`, which walks the dispatcher's registered handlers
and prints one bullet per handler with a non-empty ``description``.
``/start`` adds a welcome header; ``/help`` uses the registry's default
header. Either way, a new feature lands in both commands automatically
once its handler is registered.
"""

from something_really_bot.routing.help_registry import HelpRegistry
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    CommandContent,
    ParsedUpdate,
    PrivateMessage,
)

START_HEADER = "👋 Something Really Bot here. I can do the following:"


class _CommandHandlerBase:
    """Match a single ``/command`` in a private chat. Subclasses implement ``handle``."""

    name: str
    command: str
    description: str = ""
    help_usage: str | None = None

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == self.command


class StartCommandHandler(_CommandHandlerBase):
    """``/start`` — welcome + same feature list as ``/help``."""

    name = "commands.start"
    command = "/start"
    description = "Greeting + intro with the full feature list."
    help_usage = "/start"

    def __init__(self, registry: HelpRegistry | None = None) -> None:
        self._registry = registry or HelpRegistry(lambda: ())

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        body = self._registry.render(header=START_HEADER)
        return HandlerResult(handled=True, handler_name=self.name, reply_text=body)


class HelpCommandHandler(_CommandHandlerBase):
    """``/help`` — rendered from the feature registry on each call."""

    name = "commands.help"
    command = "/help"
    description = "Show this help message."
    help_usage = "/help"

    def __init__(self, registry: HelpRegistry | None = None) -> None:
        self._registry = registry or HelpRegistry(lambda: ())

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        return HandlerResult(
            handled=True,
            handler_name=self.name,
            reply_text=self._registry.render(),
        )
