"""Tests for the /dutch translation command (#47)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.dutch_translation.handler import (
    COMMAND_NAME,
    PROMPT_TEXT,
    DutchTranslationHandler,
)
from something_really_bot.features.dutch_translation.translator import TranslationError
from something_really_bot.routing.types import BotContext
from something_really_bot.services.pending_actions import PendingAction
from something_really_bot.telegram.client import TelegramSendError
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    PrivateMessage,
    TextContent,
    User,
    Voice,
    VoiceContent,
)


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )


def _ctx(pending_action: PendingAction | None = None) -> BotContext:
    return BotContext(settings=_settings(), pending_action=pending_action)


def _command(args: str | None = None) -> PrivateMessage:
    text = f"/dutch {args}".strip() if args else "/dutch"
    return PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=CommandContent(command="dutch", text=text, args=args),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _text(text: str) -> PrivateMessage:
    return PrivateMessage(
        update_id=2,
        message_id=43,
        chat_id=100,
        date=1234567891,
        content=TextContent(text=text),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _group_command() -> GroupMessage:
    return GroupMessage(
        update_id=3,
        message_id=77,
        chat_id=-1001,
        date=1234567890,
        content=CommandContent(command="dutch", text="/dutch", args=None),
        chat_title="grp",
        from_user=User(id=888, is_bot=False),
    )


def _pending(command: str = COMMAND_NAME) -> PendingAction:
    now = datetime.now(UTC)
    return PendingAction(
        bot_id="default",
        chat_id=100,
        user_id=999,
        command=command,
        expected_input="text",
        metadata={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )


@dataclass
class _FakeTelegram:
    sent: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None

    async def send_message(self, chat_id, text, *, reply_to_message_id=None, parse_mode=None):
        if self.raises is not None:
            raise self.raises
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": 1}


@dataclass
class _FakeTranslator:
    response: str = "Good afternoon"
    calls: list[str] = field(default_factory=list)
    raises: BaseException | None = None

    async def translate(self, text):
        if self.raises is not None:
            raise self.raises
        self.calls.append(text)
        return self.response


@dataclass
class _FakePendingStore:
    set_calls: list[dict[str, Any]] = field(default_factory=list)
    clear_calls: list[dict[str, Any]] = field(default_factory=list)

    async def set(self, **kwargs):
        self.set_calls.append(kwargs)

    async def clear(self, **kwargs):
        self.clear_calls.append(kwargs)


def _build_handler(
    *,
    telegram: _FakeTelegram | None = None,
    translator: _FakeTranslator | None = None,
    pending: _FakePendingStore | None = None,
) -> tuple[DutchTranslationHandler, _FakeTelegram, _FakeTranslator, _FakePendingStore]:
    tg = telegram or _FakeTelegram()
    tr = translator if translator is not None else _FakeTranslator()
    ps = pending or _FakePendingStore()
    return (
        DutchTranslationHandler(
            translator_factory=lambda: tr,
            telegram_client_factory=lambda: tg,
            pending_action_store_factory=lambda: ps,
        ),
        tg,
        tr,
        ps,
    )


def test_matches_dutch_command_in_private() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_command(), _ctx()) is True


def test_matches_dutch_command_in_group() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_group_command(), _ctx()) is True


def test_does_not_match_text_without_pending_action() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_text("Hoe gaat het"), _ctx()) is False


def test_matches_text_when_pending_dutch_action() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_text("Hoe gaat het"), _ctx(_pending())) is True


def test_does_not_match_text_when_pending_other_command() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_text("Hoe gaat het"), _ctx(_pending("ocr"))) is False


def test_does_not_match_non_text_voice_with_pending() -> None:
    handler, *_ = _build_handler()
    # Even with a pending /dutch, a voice memo isn't a dutch text input.
    voice_msg = PrivateMessage(
        update_id=4,
        message_id=44,
        chat_id=100,
        date=1234567892,
        content=VoiceContent(
            voice=Voice(file_id="v", file_unique_id="v", duration=5, mime_type="audio/ogg"),
        ),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )
    assert handler.matches(voice_msg, _ctx(_pending())) is False


async def test_command_without_args_sets_pending_and_prompts() -> None:
    handler, tg, _, pending = _build_handler()

    result = await handler.handle(_command(), _ctx())

    assert result.handled is True
    assert len(pending.set_calls) == 1
    assert pending.set_calls[0]["command"] == COMMAND_NAME
    assert pending.set_calls[0]["expected_input"] == "text"
    assert len(tg.sent) == 1
    assert tg.sent[0]["text"] == PROMPT_TEXT
    assert tg.sent[0]["reply_to_message_id"] == 42


async def test_command_with_inline_args_translates_immediately() -> None:
    handler, tg, tr, pending = _build_handler(translator=_FakeTranslator(response="Good afternoon"))

    await handler.handle(_command(args="Goedemiddag"), _ctx())

    assert tr.calls == ["Goedemiddag"]
    # No pending state set — inline mode bypasses it.
    assert pending.set_calls == []
    # Two messages: the "Translating…" ack and then the reply.
    assert len(tg.sent) == 2
    assert tg.sent[0]["text"] == "Translating…"
    assert "<i>Good afternoon</i>" in tg.sent[1]["text"]
    assert tg.sent[1]["parse_mode"] == "HTML"


async def test_followup_text_translates_and_clears_pending() -> None:
    handler, tg, tr, pending = _build_handler(translator=_FakeTranslator(response="How are you"))

    await handler.handle(_text("Hoe gaat het"), _ctx(_pending()))

    assert tr.calls == ["Hoe gaat het"]
    assert len(pending.clear_calls) == 1
    assert pending.clear_calls[0]["chat_id"] == 100
    assert pending.clear_calls[0]["user_id"] == 999
    assert tg.sent[0]["text"] == "Translating…"
    assert "<i>How are you</i>" in tg.sent[1]["text"]


async def test_followup_text_with_missing_translator_replies_unavailable() -> None:
    handler, tg, _, _ = _build_handler(translator=None)
    # Need to reconstruct with translator_factory returning None.
    handler = DutchTranslationHandler(
        translator_factory=lambda: None,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: _FakePendingStore(),
    )

    await handler.handle(_text("Hoe gaat het"), _ctx(_pending()))

    assert "isn't configured" in tg.sent[0]["text"]


async def test_followup_text_translator_error_replies_user_error() -> None:
    handler, tg, _, _ = _build_handler(translator=_FakeTranslator(raises=TranslationError("x")))

    await handler.handle(_text("Hoe gaat het"), _ctx(_pending()))

    # Two messages: ack + user-facing error.
    assert len(tg.sent) == 2
    assert tg.sent[0]["text"] == "Translating…"
    assert "translation service" in tg.sent[1]["text"]


async def test_empty_followup_text_replies_with_prompt() -> None:
    handler, tg, tr, _ = _build_handler()

    await handler.handle(_text("   "), _ctx(_pending()))

    # No actual call to the translator on empty input.
    assert tr.calls == []
    assert tg.sent[0]["text"] == PROMPT_TEXT


async def test_send_failure_during_reply_is_swallowed() -> None:
    handler, tg, _, _ = _build_handler(
        telegram=_FakeTelegram(raises=TelegramSendError("nope")),
        translator=_FakeTranslator(response="Good"),
    )

    # Should not raise even though Telegram is angry.
    await handler.handle(_command(args="Goed"), _ctx())
