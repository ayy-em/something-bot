"""OpenAI chat-completion fallback for unmatched QA private text (#23).

Matches the same shape as :class:`HelloWorldHandler` (private chat,
TextContent, from a QA user), but supersedes it: with
``settings.hello_world_mode == False`` (the default), HelloWorld
silently doesn't match and this handler runs instead. Sending
``HELLO_WORLD_MODE=true`` flips the precedence for the brief
degraded-mode window if OpenAI is broken.

No conversation memory; every message is treated fresh. #26 adds a
context pipeline.
"""

from something_really_bot.logging import get_logger
from something_really_bot.routing.types import (
    BotContext,
    HandlerError,
    HandlerResult,
)
from something_really_bot.telegram.models import (
    ParsedUpdate,
    PrivateMessage,
    TextContent,
)

_logger = get_logger(__name__)

APOLOGY_REPLY = "Sorry — I couldn't reach the brain on that one. Please try again in a bit."


class OpenAIFallbackHandler:
    """Send the message to OpenAI; reply with the response."""

    name = "openai_fallback"
    description = "Chat with me — I'll reply via OpenAI."
    help_usage = "Send any text message"

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        if not isinstance(update.content, TextContent):
            return False
        return update.from_user.id in ctx.settings.telegram_qa_user_ids

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)
        assert isinstance(update.content, TextContent)

        client = ctx.openai_client
        if client is None:
            _logger.warning(
                "openai_client_unavailable_using_apology",
                extra={"update_id": update.update_id},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=APOLOGY_REPLY,
                error=HandlerError(
                    handler_name=self.name,
                    exception_type="OpenAIClientUnavailable",
                    message="OPENAI_API_KEY not configured.",
                ),
            )

        try:
            reply = await client.complete(update.content.text)
        except Exception as exc:  # noqa: BLE001 — translate any failure into apology
            _logger.warning(
                "openai_fallback_failed",
                extra={"update_id": update.update_id, "exception_type": type(exc).__name__},
            )
            return HandlerResult(
                handled=True,
                handler_name=self.name,
                reply_text=APOLOGY_REPLY,
                error=HandlerError(
                    handler_name=self.name,
                    exception_type=type(exc).__name__,
                    message=str(exc),
                ),
            )

        return HandlerResult(handled=True, handler_name=self.name, reply_text=reply)
