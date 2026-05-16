"""Tests for :mod:`something_really_bot.features.hello_world.handler`."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.hello_world.handler import HelloWorldHandler
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.models import (
    ChannelPost,
    CommandContent,
    GroupMessage,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    UnsupportedUpdate,
    User,
)

JM_TG_ID = 135499785
IRINDICA_CHAT_ID = 159278882
RANDO_ID = 99999999


@dataclass
class _FakeClient:
    """Records send_message calls so tests can assert against them."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        self.calls.append({"chat_id": chat_id, "text": text})
        return {"message_id": 1}


def _settings(allowlist: frozenset[int]) -> Settings:
    """Build a Settings with the two required secrets and the given allowlist."""
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=allowlist,
    )


def _ctx(allowlist: frozenset[int], client: _FakeClient | None = None) -> BotContext:
    return BotContext(settings=_settings(allowlist), telegram_client=client)


def _private_text(user_id: int, text: str = "hi there") -> PrivateMessage:
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


@pytest.mark.parametrize("user_id", [JM_TG_ID, IRINDICA_CHAT_ID])
async def test_matches_private_text_from_qa_user(user_id: int) -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}))

    assert handler.matches(_private_text(user_id), ctx) is True


@pytest.mark.parametrize("user_id", [JM_TG_ID, IRINDICA_CHAT_ID])
async def test_handle_sends_parrot_reply(user_id: int) -> None:
    handler = HelloWorldHandler()
    fake = _FakeClient()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}), client=fake)
    update = _private_text(user_id, text="pong me")

    result = await handler.handle(update, ctx)

    assert result.handled is True
    assert result.handler_name == "hello_world.parrot"
    assert result.reply_text == "Hello World\n\nYou said: pong me"
    assert fake.calls == [{"chat_id": user_id, "text": "Hello World\n\nYou said: pong me"}]


async def test_does_not_match_unknown_user() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}))

    assert handler.matches(_private_text(RANDO_ID), ctx) is False


async def test_does_not_match_in_group_even_from_qa_user() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}))

    assert handler.matches(_group_text(JM_TG_ID), ctx) is False


async def test_does_not_match_in_supergroup_even_from_qa_user() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}))

    assert handler.matches(_supergroup_text(IRINDICA_CHAT_ID), ctx) is False


async def test_does_not_match_channel_post() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID, IRINDICA_CHAT_ID}))

    assert handler.matches(_channel_post(), ctx) is False


async def test_does_not_match_command_from_qa_user() -> None:
    """Commands (/start, /help) belong to #16, not this handler."""
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID}))
    update = PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=JM_TG_ID,
        date=1715000000,
        content=CommandContent(command="/start", text="/start"),
        from_user=User(id=JM_TG_ID, is_bot=False, first_name="J"),
    )

    assert handler.matches(update, ctx) is False


async def test_does_not_match_unsupported_update() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID}))
    update = UnsupportedUpdate(update_id=1, reason="callback_query", raw={})

    assert handler.matches(update, ctx) is False


async def test_does_not_match_when_allowlist_empty() -> None:
    """Empty QA allowlist (e.g. parsing the secret failed) → handler is a no-op."""
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset())

    assert handler.matches(_private_text(JM_TG_ID), ctx) is False


async def test_handle_without_client_does_not_crash() -> None:
    """Defensive: if telegram_client is None we still return cleanly, no HTTP."""
    handler = HelloWorldHandler()
    ctx = _ctx(frozenset({JM_TG_ID}), client=None)

    result = await handler.handle(_private_text(JM_TG_ID, text="x"), ctx)

    assert result.handled is True
    assert result.reply_text == "Hello World\n\nYou said: x"
