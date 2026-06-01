"""``/dutch`` — Dutch to English translation command (#47).

Two-turn flow:

1. User sends ``/dutch`` (optionally with text directly attached, e.g.
   ``/dutch Goedemiddag``). If text is attached, the handler translates
   it inline and replies. If not, the handler sets a pending action and
   asks the user to send Dutch text in their next message.
2. While a pending action exists for this (chat, user), the next plain
   text message from the same user is treated as the input and gets
   translated. The pending action is cleared after one attempt.

Works in DM and in groups (per the feature spec).
"""

import html
from collections.abc import Callable
from functools import lru_cache

from something_really_bot.features.dutch_translation.translator import (
    DutchTranslator,
    TranslationError,
    get_dutch_translator,
)
from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.services.pending_actions import (
    PendingActionStore,
    get_pending_action_store,
)
from something_really_bot.telegram.client import (
    TelegramClient,
    TelegramSendError,
    get_telegram_client,
)
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    ParsedUpdate,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
)

_logger = get_logger(__name__)

COMMAND_NAME = "/dutch"
PROMPT_TEXT = "Now send the Dutch text you want translated to English."
TRANSLATING_ACK = "Translating…"
REPLY_PARSE_MODE = "HTML"

_REPLY_TEMPLATE = "<i>{translation}</i>"

_ERROR_TRANSLATOR_UNAVAILABLE = "Translation isn't configured right now. Logged for review."
_ERROR_TRANSLATION_FAILED = (
    "Couldn't translate that. The translation service might be having a moment — try again shortly."
)

_SupportedMessage = PrivateMessage | GroupMessage | SupergroupMessage


class DutchTranslationHandler:
    """``/dutch`` command + follow-up text routing."""

    name = "dutch_translation"

    def __init__(
        self,
        *,
        translator_factory: Callable[[], DutchTranslator | None] = get_dutch_translator,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        pending_action_store_factory: Callable[
            [], PendingActionStore | None
        ] = get_pending_action_store,
    ) -> None:
        self._translator_factory = translator_factory
        self._tg_factory = telegram_client_factory
        self._pending_factory = pending_action_store_factory

    def matches(self, update: ParsedUpdate, ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage):
            return False
        # /dutch command always claims.
        if isinstance(update.content, CommandContent) and update.content.command == COMMAND_NAME:
            return True
        # Plain text from a user with a pending /dutch action.
        if isinstance(update.content, TextContent):
            pending = ctx.pending_action
            return pending is not None and pending.command == COMMAND_NAME
        return False

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage | GroupMessage | SupergroupMessage)
        content = update.content

        if isinstance(content, CommandContent) and content.command == COMMAND_NAME:
            inline = (content.args or "").strip()
            if inline:
                # Translate the inline argument straight away; no pending state.
                return await self._translate_and_reply(update, inline)
            return await self._prompt(update, ctx)

        assert isinstance(content, TextContent)
        # Follow-up text. Clear the pending state regardless of outcome
        # so the user doesn't get stuck in a loop on errors.
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.clear(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("dutch_translation_clear_pending_failed")
        return await self._translate_and_reply(update, content.text.strip())

    async def _prompt(self, update: _SupportedMessage, ctx: BotContext) -> HandlerResult:
        store = ctx.pending_action_store or self._pending_factory()
        if store is not None:
            try:
                await store.set(
                    bot_id=ctx.bot_id,
                    chat_id=update.chat_id,
                    user_id=update.from_user.id,
                    command=COMMAND_NAME,
                    expected_input="text",
                )
            except Exception:  # noqa: BLE001
                _logger.exception("dutch_translation_set_pending_failed")
        telegram_client = self._tg_factory()
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=PROMPT_TEXT,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning(
                "dutch_translation_prompt_send_failed",
                extra={"chat_id": update.chat_id},
            )
        return HandlerResult(handled=True, handler_name=self.name)

    async def _translate_and_reply(
        self, update: _SupportedMessage, dutch_text: str
    ) -> HandlerResult:
        translator = self._translator_factory()
        telegram_client = self._tg_factory()
        if translator is None:
            await self._send_error(telegram_client, update, _ERROR_TRANSLATOR_UNAVAILABLE)
            return HandlerResult(handled=True, handler_name=self.name)

        if not dutch_text:
            # Empty follow-up — gently re-prompt.
            await self._send_error(telegram_client, update, PROMPT_TEXT)
            return HandlerResult(handled=True, handler_name=self.name)

        # Immediate ack so the user sees their query was received even
        # when the OpenAI call takes a beat.
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=TRANSLATING_ACK,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning(
                "dutch_translation_ack_send_failed",
                extra={"chat_id": update.chat_id},
            )

        try:
            translation = await translator.translate(dutch_text)
        except TranslationError as exc:
            _logger.warning(
                "dutch_translation_failed",
                extra={"chat_id": update.chat_id, "exception_type": type(exc).__name__},
            )
            await self._send_error(telegram_client, update, _ERROR_TRANSLATION_FAILED)
            return HandlerResult(handled=True, handler_name=self.name)

        reply_text = _REPLY_TEMPLATE.format(translation=html.escape(translation))
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=reply_text,
                reply_to_message_id=update.message_id,
                parse_mode=REPLY_PARSE_MODE,
            )
        except TelegramSendError:
            _logger.warning(
                "dutch_translation_reply_send_failed",
                extra={"chat_id": update.chat_id},
            )
        return HandlerResult(handled=True, handler_name=self.name)

    @staticmethod
    async def _send_error(
        telegram_client: TelegramClient, update: _SupportedMessage, text: str
    ) -> None:
        try:
            await telegram_client.send_message(
                chat_id=update.chat_id,
                text=text,
                reply_to_message_id=update.message_id,
            )
        except TelegramSendError:
            _logger.warning(
                "dutch_translation_error_send_failed",
                extra={"chat_id": update.chat_id},
            )


@lru_cache(maxsize=1)
def get_dutch_translation_handler() -> DutchTranslationHandler:
    """Process-wide singleton, wired with production dependencies."""
    return DutchTranslationHandler()
