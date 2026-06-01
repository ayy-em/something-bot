"""Tests for :mod:`something_really_bot.features.commands.handler`.

Handlers are pure — they return :class:`HandlerResult` with ``reply_text``
set; webhook orchestration covers the actual send.
"""

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.commands.handler import (
    START_HEADER,
    HelpCommandHandler,
    StartCommandHandler,
)
from something_really_bot.routing.command_registry import CommandRegistry, FeatureEntry
from something_really_bot.routing.help_registry import HelpRegistry
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    User,
)
from something_really_bot.telegram.parser import parse_update

USER_ID = 42


def _ctx() -> BotContext:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )
    return BotContext(settings=settings)


def _private_command(command: str) -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=CommandContent(command=command, text=command),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _group_command(command: str) -> GroupMessage:
    return GroupMessage(
        update_id=1,
        message_id=2,
        chat_id=-1001,
        date=1715000000,
        content=CommandContent(command=command, text=command),
        chat_title="g",
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _supergroup_command(command: str) -> SupergroupMessage:
    return SupergroupMessage(
        update_id=1,
        message_id=2,
        chat_id=-1002,
        date=1715000000,
        content=CommandContent(command=command, text=command),
        chat_title="sg",
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


async def test_start_handler_renders_welcome_with_feature_list() -> None:
    cmd_reg = CommandRegistry(
        [
            FeatureEntry(
                handler_name="fake",
                description="Do a fake thing.",
                help_usage="/fake",
                command="/fake",
            ),
        ]
    )
    registry = HelpRegistry(cmd_reg)
    handler = StartCommandHandler(registry)

    result = await handler.handle(_private_command("/start"), _ctx())

    assert result.handled is True
    assert result.handler_name == StartCommandHandler.name
    assert result.reply_text is not None
    assert result.reply_text.startswith(START_HEADER)
    assert "/fake — Do a fake thing." in result.reply_text


async def test_start_handler_without_registry_falls_back_to_placeholder() -> None:
    handler = StartCommandHandler()

    result = await handler.handle(_private_command("/start"), _ctx())

    assert result.handled is True
    assert result.reply_text is not None
    assert START_HEADER in result.reply_text
    assert "(no documented features yet)" in result.reply_text


async def test_help_handler_returns_rendered_registry_body() -> None:
    """``/help`` body comes from the injected HelpRegistry (#27)."""
    handler = HelpCommandHandler()

    result = await handler.handle(_private_command("/help"), _ctx())

    assert result.handled is True
    assert result.handler_name == HelpCommandHandler.name
    # Default registry is empty → placeholder line is present.
    assert "Here's what I can do:" in (result.reply_text or "")
    assert "(no documented features yet)" in (result.reply_text or "")


@pytest.mark.parametrize(
    ("handler_cls", "command"),
    [(StartCommandHandler, "/start"), (HelpCommandHandler, "/help")],
)
async def test_command_in_group_chat_does_not_match(handler_cls: type, command: str) -> None:
    handler = handler_cls()

    assert handler.matches(_group_command(command), _ctx()) is False


@pytest.mark.parametrize(
    ("handler_cls", "command"),
    [(StartCommandHandler, "/start"), (HelpCommandHandler, "/help")],
)
async def test_command_in_supergroup_does_not_match(handler_cls: type, command: str) -> None:
    handler = handler_cls()

    assert handler.matches(_supergroup_command(command), _ctx()) is False


async def test_start_handler_ignores_help_command_and_vice_versa() -> None:
    """Only the matching command activates its handler."""
    start = StartCommandHandler()
    help_ = HelpCommandHandler()
    ctx = _ctx()

    assert start.matches(_private_command("/help"), ctx) is False
    assert help_.matches(_private_command("/start"), ctx) is False


async def test_unknown_command_does_not_match_either_handler() -> None:
    ctx = _ctx()

    assert StartCommandHandler().matches(_private_command("/foo"), ctx) is False
    assert HelpCommandHandler().matches(_private_command("/foo"), ctx) is False


async def test_plain_text_does_not_match_command_handlers() -> None:
    """Non-command text shouldn't match even if it equals the command string."""
    update = PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=TextContent(text="/start"),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )

    assert StartCommandHandler().matches(update, _ctx()) is False


@pytest.mark.parametrize(
    ("handler_cls", "command_with_suffix"),
    [
        (StartCommandHandler, "/start@SomethingReallyBot"),
        (HelpCommandHandler, "/help@SomethingReallyBot"),
    ],
)
async def test_command_with_at_bot_suffix_still_matches_via_parser(
    handler_cls: type, command_with_suffix: str
) -> None:
    """End-to-end: a Telegram payload with the ``@botname`` suffix is stripped
    by the parser and the handler still matches."""
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "date": 1715000000,
            "chat": {"id": USER_ID, "type": "private"},
            "from": {"id": USER_ID, "is_bot": False, "first_name": "T"},
            "text": command_with_suffix,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(command_with_suffix)}],
        },
    }

    parsed = parse_update(payload)

    assert handler_cls().matches(parsed, _ctx()) is True
