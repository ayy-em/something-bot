"""Hello-World / parrot handler (SPEC §6.4 / §6.5).

Matches plain text messages in a *private* chat. Group, supergroup,
and channel updates never match. Commands (``/start``, ``/help``)
don't match either; the dispatcher routes them to their own handlers.

The handler is pure: it returns the reply text in :class:`HandlerResult`
and lets the webhook do the actual send + response persistence (#18).

Reply format::

    Hello World

    You said: <original message text>
"""

from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    ParsedUpdate,
    PrivateMessage,
    TextContent,
)


class HelloWorldHandler:
    """Parrots back text messages in private chats."""

    name = "hello_world.parrot"
    description = "Parrot mode (only active when HELLO_WORLD_MODE=true)."
    help_usage = "Send a text message"

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not ctx.settings.hello_world_mode:
            return False
        if not isinstance(update, PrivateMessage):
            return False
        return isinstance(update.content, TextContent)

    async def handle(self, update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        assert isinstance(update.content, TextContent)

        reply = f"Hello World\n\nYou said: {update.content.text}"
        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
