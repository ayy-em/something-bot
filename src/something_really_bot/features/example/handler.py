"""Example handler: ``/ping`` → ``pong``.

Exists to demonstrate the :class:`Handler` protocol. Doesn't overlap with
the real command handlers (/start, /help) landing in #16 or the QA parrot
in #15, so it's safe to keep through those issues. Remove whenever a real
handler taking precedence makes it dead weight.
"""

from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
)

PING_COMMAND = "/ping"


class PingHandler:
    """Reply ``pong`` to ``/ping`` in private, group, and supergroup chats."""

    name = "example.ping"

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        """True for ``/ping`` commands in chat types that have a ``from_user``."""
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        if not isinstance(update.content, CommandContent):
            return False
        return update.content.command == PING_COMMAND

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        return HandlerResult(handled=True, handler_name=self.name, reply_text="pong")
