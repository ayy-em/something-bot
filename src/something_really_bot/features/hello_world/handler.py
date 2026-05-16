"""Hello-World / parrot handler for authorized QA users (SPEC §6.4 / §6.5).

Matches plain text messages in a *private* chat from a user whose ID is in
the QA allowlist (``ctx.settings.telegram_qa_user_ids``). Group, supergroup,
and channel updates never match — SPEC §6.3 forbids the bot from replying
anywhere other than 1:1 private chats. Commands (``/start``, ``/help`` —
#16) don't match either; the dispatcher will route them to their own
handlers.

Reply format::

    Hello World

    You said: <original message text>
"""

from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    ParsedUpdate,
    PrivateMessage,
    TextContent,
)

_logger = get_logger(__name__)


class HelloWorldHandler:
    """Parrots back text messages from authorized QA users."""

    name = "hello_world.parrot"

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, TextContent):
            return False
        return update.from_user.id in ctx.settings.telegram_qa_user_ids

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        assert isinstance(update.content, TextContent)

        reply = f"Hello World\n\nYou said: {update.content.text}"

        client = ctx.telegram_client
        if client is None:
            _logger.warning(
                "telegram_client_unavailable_skipping_reply",
                extra={"update_id": update.update_id, "handler": self.name},
            )
            return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)

        await client.send_message(chat_id=update.chat_id, text=reply)
        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
