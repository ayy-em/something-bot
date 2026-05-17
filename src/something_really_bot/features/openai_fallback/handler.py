"""OpenAI chat-completion fallback for unmatched QA private text (#23).

Matches the same shape as :class:`HelloWorldHandler` (private chat,
TextContent, from a QA user), but supersedes it: with
``settings.hello_world_mode == False`` (the default), HelloWorld
silently doesn't match and this handler runs instead. Sending
``HELLO_WORLD_MODE=true`` flips the precedence for the brief
degraded-mode window if OpenAI is broken.

No conversation memory; every message is treated fresh. #26 adds a
context pipeline.

Sends a short "Thinking…" ack via the Telegram client before the
completion call so the user sees their query was received even when
the OpenAI call takes a beat. The substantive reply still goes back
through ``HandlerResult.reply_text`` so the webhook layer handles the
response persistence in one place.
"""

import contextlib
from collections.abc import Callable

from something_really_bot.logging import get_logger
from something_really_bot.routing.types import (
    BotContext,
    HandlerError,
    HandlerResult,
)
from something_really_bot.telegram.client import (
    TelegramClient,
    TelegramSendError,
    get_telegram_client,
)
from something_really_bot.telegram.models import (
    ParsedUpdate,
    PrivateMessage,
    TextContent,
)

_logger = get_logger(__name__)

APOLOGY_REPLY = "Sorry — I couldn't reach the brain on that one. Please try again in a bit."
THINKING_ACK = "Thinking…"


class OpenAIFallbackHandler:
    """Send the message to OpenAI; reply with the response."""

    name = "openai_fallback"
    description = "Chat with me — I'll reply via OpenAI."
    help_usage = "Send any text message"

    def __init__(
        self,
        *,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
    ) -> None:
        self._tg_factory = telegram_client_factory

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

        # Immediate ack so the user sees their query was received even
        # when the OpenAI call takes a beat. Failure to ack is benign —
        # the substantive reply below is the load-bearing message.
        telegram_client = self._tg_factory()
        with contextlib.suppress(TelegramSendError):
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=THINKING_ACK,
                reply_to_message_id=update.message_id,
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
