"""Tests for the routing dispatcher."""

import pytest

from something_really_bot.config import Settings
from something_really_bot.routing.dispatcher import Dispatcher
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    ParsedUpdate,
    PrivateMessage,
    TextContent,
    User,
)


def _settings() -> Settings:
    return Settings(_env_file=None, telegram_webhook_secret="x")


def _ctx(bot_id: str = "default") -> BotContext:
    return BotContext(settings=_settings(), bot_id=bot_id)


def _private_text(text: str = "hi") -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=1,
        chat_id=1,
        date=0,
        content=TextContent(text=text),
        from_user=User(id=1, is_bot=False, first_name="T"),
    )


class _StubHandler:
    def __init__(self, name: str, matches: bool, *, raises: bool = False) -> None:
        self.name = name
        self._matches = matches
        self._raises = raises
        self.called = False

    def matches(self, _update: ParsedUpdate, _ctx: BotContext) -> bool:
        return self._matches

    async def handle(self, _update: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
        self.called = True
        if self._raises:
            raise RuntimeError("boom")
        return HandlerResult(handled=True, handler_name=self.name, reply_text=self.name)


@pytest.mark.asyncio
async def test_first_matching_handler_wins() -> None:
    matching = _StubHandler("matching", matches=True)
    also_matching = _StubHandler("also_matching", matches=True)
    dispatcher = Dispatcher()
    dispatcher.register(matching)
    dispatcher.register(also_matching)

    result = await dispatcher.dispatch(_private_text(), _ctx())

    assert result.handler_name == "matching"
    assert matching.called is True
    assert also_matching.called is False


@pytest.mark.asyncio
async def test_no_match_returns_unhandled_without_fallback() -> None:
    dispatcher = Dispatcher()
    dispatcher.register(_StubHandler("nope", matches=False))

    result = await dispatcher.dispatch(_private_text(), _ctx())

    assert result.handled is False
    assert result.handler_name is None


@pytest.mark.asyncio
async def test_fallback_runs_when_no_handler_matches() -> None:
    dispatcher = Dispatcher()
    dispatcher.register(_StubHandler("nope", matches=False))
    fallback = _StubHandler("fallback", matches=True)
    dispatcher.set_fallback(fallback)

    result = await dispatcher.dispatch(_private_text(), _ctx())

    assert result.handler_name == "fallback"
    assert fallback.called is True


@pytest.mark.asyncio
async def test_handler_exception_is_captured_and_not_raised() -> None:
    dispatcher = Dispatcher()
    raising = _StubHandler("raising", matches=True, raises=True)
    dispatcher.register(raising)

    result = await dispatcher.dispatch(_private_text(), _ctx())

    assert result.handled is True
    assert result.error is not None
    assert result.error.handler_name == "raising"
    assert result.error.exception_type == "RuntimeError"
    assert result.error.message == "boom"


@pytest.mark.asyncio
async def test_bot_id_flows_through_to_handlers() -> None:
    seen: dict[str, str] = {}

    class _Recorder:
        name = "recorder"

        def matches(self, _u: ParsedUpdate, ctx: BotContext) -> bool:
            seen["match"] = ctx.bot_id
            return True

        async def handle(self, _u: ParsedUpdate, ctx: BotContext) -> HandlerResult:
            seen["handle"] = ctx.bot_id
            return HandlerResult(handled=True, handler_name=self.name)

    dispatcher = Dispatcher()
    dispatcher.register(_Recorder())

    await dispatcher.dispatch(_private_text(), _ctx(bot_id="another_one"))

    assert seen == {"match": "another_one", "handle": "another_one"}
