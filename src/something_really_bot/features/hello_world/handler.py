"""Hello-World / parrot handler for authorized QA users (SPEC §6.4 / §6.5).

Matches plain text messages in a *private* chat from a user whose ID is in
the QA allowlist (``ctx.settings.telegram_qa_user_ids``). Group, supergroup,
and channel updates never match — SPEC §6.3 forbids the bot from replying
anywhere other than 1:1 private chats. Commands (``/start``, ``/help``)
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
    """Parrots back text messages from authorized QA users."""

    name = "hello_world.parrot"

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not ctx.settings.hello_world_mode:
            # OpenAI fallback (#23) supersedes this handler unless the
            # user explicitly enables degraded mode via env flag.
            return False
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, TextContent):
            return False
        return update.from_user.id in ctx.settings.telegram_qa_user_ids

    async def handle(self, update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        assert isinstance(update.content, TextContent)

        reply = f"Hello World\n\nYou said: {update.content.text}"
        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
