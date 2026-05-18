"""Tests for :mod:`something_really_bot.features.openai_fallback.handler`."""

from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.openai_fallback.handler import (
    APOLOGY_REPLY,
    OpenAIFallbackHandler,
)
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.models import (
    ChannelPost,
    GroupMessage,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    User,
)

JM_TG_ID = 135499785
RANDO_ID = 99999999


class _FakeOpenAI:
    def __init__(self, reply: str = "Paris.") -> None:
        self.calls: list[str] = []
        self._reply = reply

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._reply


class _RaisingOpenAI:
    async def complete(self, _prompt: str) -> str:
        raise RuntimeError("API down")


def _ctx(
    openai_client: Any | None = None,
) -> BotContext:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        hello_world_mode=False,
    )
    return BotContext(settings=settings, openai_client=openai_client)


def _private_text(user_id: int, text: str = "What is the capital of France?") -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=user_id,
        date=1715000000,
        content=TextContent(text=text),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
    )


def _group_text(user_id: int) -> GroupMessage:
    return GroupMessage(
        update_id=1,
        message_id=2,
        chat_id=-1001,
        date=1715000000,
        content=TextContent(text="hi"),
        chat_title="g",
        from_user=User(id=user_id, is_bot=False, first_name="T"),
    )


def _supergroup_text(user_id: int) -> SupergroupMessage:
    return SupergroupMessage(
        update_id=1,
        message_id=2,
        chat_id=-1002,
        date=1715000000,
        content=TextContent(text="hi"),
        chat_title="sg",
        from_user=User(id=user_id, is_bot=False, first_name="T"),
    )


def _channel_post() -> ChannelPost:
    return ChannelPost(
        update_id=1,
        message_id=2,
        chat_id=-1003,
        date=1715000000,
        content=TextContent(text="hi"),
        chat_title="c",
    )


async def test_matches_private_text_from_any_user() -> None:
    handler = OpenAIFallbackHandler()
    assert handler.matches(_private_text(JM_TG_ID), _ctx()) is True
    assert handler.matches(_private_text(RANDO_ID), _ctx()) is True


@pytest.mark.parametrize("factory", [_group_text, _supergroup_text])
async def test_does_not_match_group_or_supergroup(factory) -> None:
    handler = OpenAIFallbackHandler()
    assert handler.matches(factory(JM_TG_ID), _ctx()) is False


async def test_does_not_match_channel_post() -> None:
    handler = OpenAIFallbackHandler()
    assert handler.matches(_channel_post(), _ctx()) is False


async def test_handle_calls_openai_and_returns_reply() -> None:
    handler = OpenAIFallbackHandler()
    fake = _FakeOpenAI(reply="The capital of France is Paris.")
    ctx = _ctx(openai_client=fake)

    result = await handler.handle(
        _private_text(JM_TG_ID, text="What is the capital of France?"),
        ctx,
    )

    assert result.handled is True
    assert result.handler_name == "openai_fallback"
    assert result.reply_text == "The capital of France is Paris."
    assert result.error is None
    assert fake.calls == ["What is the capital of France?"]


async def test_handle_returns_apology_when_openai_unavailable() -> None:
    handler = OpenAIFallbackHandler()
    ctx = _ctx(openai_client=None)

    result = await handler.handle(_private_text(JM_TG_ID), ctx)

    assert result.handled is True
    assert result.reply_text == APOLOGY_REPLY
    assert result.error is not None
    assert result.error.exception_type == "OpenAIClientUnavailable"


async def test_handle_returns_apology_when_openai_raises() -> None:
    handler = OpenAIFallbackHandler()
    ctx = _ctx(openai_client=_RaisingOpenAI())

    result = await handler.handle(_private_text(JM_TG_ID), ctx)

    assert result.handled is True
    assert result.reply_text == APOLOGY_REPLY
    assert result.error is not None
    assert result.error.exception_type == "RuntimeError"
    assert "API down" in result.error.message
