"""Tests for :mod:`something_really_bot.features.hello_world.handler`.

The handler is pure — it returns :class:`HandlerResult` with ``reply_text``
set; the webhook is responsible for sending. Sending behavior is covered
in :mod:`tests.unit.test_webhook_dispatch`.
"""

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


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        hello_world_mode=True,
    )


def _ctx() -> BotContext:
    return BotContext(settings=_settings())


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


async def test_matches_private_text_from_any_user() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()

    assert handler.matches(_private_text(JM_TG_ID), ctx) is True
    assert handler.matches(_private_text(RANDO_ID), ctx) is True


async def test_handle_returns_parrot_reply() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()
    update = _private_text(RANDO_ID, text="pong me")

    result = await handler.handle(update, ctx)

    assert result.handled is True
    assert result.handler_name == "hello_world.parrot"
    assert result.reply_text == "Hello World\n\nYou said: pong me"


async def test_does_not_match_in_group() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()

    assert handler.matches(_group_text(JM_TG_ID), ctx) is False


async def test_does_not_match_in_supergroup() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()

    assert handler.matches(_supergroup_text(IRINDICA_CHAT_ID), ctx) is False


async def test_does_not_match_channel_post() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()

    assert handler.matches(_channel_post(), ctx) is False


async def test_does_not_match_command() -> None:
    handler = HelloWorldHandler()
    ctx = _ctx()
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
    ctx = _ctx()
    update = UnsupportedUpdate(update_id=1, reason="callback_query", raw={})

    assert handler.matches(update, ctx) is False
