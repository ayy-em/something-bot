"""Tests for the routing dispatcher."""

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.routing.command_registry import CommandRegistry, FeatureEntry
from something_really_bot.routing.dispatcher import UNAUTHORIZED_REPLY, Dispatcher
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
async def test_handler_result_carries_job_name_and_timings() -> None:
    """Dispatcher stamps ``job_name`` (#53) and start/finish timestamps."""
    dispatcher = Dispatcher()
    dispatcher.register(_StubHandler("ok", matches=True))

    result = await dispatcher.dispatch(_private_text(), _ctx())

    # _StubHandler lives in this test module → derived job name is the
    # leaf module name since there's no ``features/`` segment.
    assert result.job_name is not None
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.finished_at >= result.started_at


@pytest.mark.asyncio
async def test_handler_exception_still_records_timings() -> None:
    dispatcher = Dispatcher()
    dispatcher.register(_StubHandler("raising", matches=True, raises=True))

    result = await dispatcher.dispatch(_private_text(), _ctx())

    assert result.error is not None
    assert result.job_name is not None
    assert result.started_at is not None
    assert result.finished_at is not None


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


# --------------------------------------------------------------------------- #
# trusted_users_only gating
# --------------------------------------------------------------------------- #

TRUSTED_USER_ID = 42
UNTRUSTED_USER_ID = 999


def _gated_registry(handler_name: str = "gated") -> CommandRegistry:
    return CommandRegistry(
        [
            FeatureEntry(
                handler_name=handler_name,
                description="Gated cmd.",
                command="/gated",
                trusted_users_only=True,
            ),
        ]
    )


def _ctx_with_qa(*, qa_ids: frozenset[int], bot_id: str = "default") -> BotContext:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=qa_ids,
    )
    return BotContext(settings=settings, bot_id=bot_id)


def _private_text_from(user_id: int, text: str = "hi") -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=1,
        chat_id=user_id,
        date=0,
        content=TextContent(text=text),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
    )


@pytest.mark.asyncio
async def test_gated_handler_allows_trusted_user() -> None:
    handler = _StubHandler("gated", matches=True)
    dispatcher = Dispatcher(command_registry=_gated_registry())
    dispatcher.register(handler)

    result = await dispatcher.dispatch(
        _private_text_from(TRUSTED_USER_ID),
        _ctx_with_qa(qa_ids=frozenset({TRUSTED_USER_ID})),
    )

    assert handler.called is True
    assert result.handler_name == "gated"
    assert result.reply_text == "gated"


@pytest.mark.asyncio
async def test_gated_handler_rejects_untrusted_user() -> None:
    handler = _StubHandler("gated", matches=True)
    dispatcher = Dispatcher(command_registry=_gated_registry())
    dispatcher.register(handler)

    result = await dispatcher.dispatch(
        _private_text_from(UNTRUSTED_USER_ID),
        _ctx_with_qa(qa_ids=frozenset({TRUSTED_USER_ID})),
    )

    assert handler.called is False
    assert result.handled is True
    assert result.reply_text == UNAUTHORIZED_REPLY


@pytest.mark.asyncio
async def test_ungated_handler_ignores_registry() -> None:
    registry = CommandRegistry(
        [
            FeatureEntry(handler_name="open", description="Open.", command="/open"),
        ]
    )
    handler = _StubHandler("open", matches=True)
    dispatcher = Dispatcher(command_registry=registry)
    dispatcher.register(handler)

    result = await dispatcher.dispatch(
        _private_text_from(UNTRUSTED_USER_ID),
        _ctx_with_qa(qa_ids=frozenset()),
    )

    assert handler.called is True
    assert result.handler_name == "open"


@pytest.mark.asyncio
async def test_gating_without_registry_is_noop() -> None:
    handler = _StubHandler("any", matches=True)
    dispatcher = Dispatcher()
    dispatcher.register(handler)

    result = await dispatcher.dispatch(_private_text_from(UNTRUSTED_USER_ID), _ctx())

    assert handler.called is True
    assert result.handler_name == "any"
