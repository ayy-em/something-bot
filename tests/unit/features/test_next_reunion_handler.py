"""Tests for :mod:`something_really_bot.features.next_reunion.handler`."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unittest.mock import patch

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.next_reunion.handler import NextReunionHandler
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.models import (
    CommandContent,
    GroupMessage,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    User,
)


@dataclass
class _FakePostgresStorage:
    """In-memory stub for the Postgres storage used by reunion date handlers."""

    _rows: list[dict[str, Any]] = field(default_factory=list)
    _table_created: bool = False
    raises_on_execute: BaseException | None = None
    raises_on_fetch: BaseException | None = None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.raises_on_execute is not None:
            raise self.raises_on_execute
        if "CREATE TABLE" in sql:
            self._table_created = True
            return
        if "INSERT" in sql:
            self._rows = [{"target_date": params[0]}]
            return

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.raises_on_fetch is not None:
            raise self.raises_on_fetch
        return list(self._rows)


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        irindica_chat_id=None,
    )


def _ctx() -> BotContext:
    return BotContext(settings=_settings())


def _user() -> User:
    return User(id=12345, first_name="Test", username="testuser")


def _private_command(command: str, args: str | None = None) -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=10,
        chat_id=12345,
        date=1700000000,
        from_user=_user(),
        content=CommandContent(
            command=command,
            text=f"{command} {args}" if args else command,
            args=args,
        ),
    )


def _group_command(command: str, args: str | None = None) -> GroupMessage:
    return GroupMessage(
        update_id=2,
        message_id=20,
        chat_id=-100123,
        date=1700000000,
        from_user=_user(),
        chat_title="Test Group",
        content=CommandContent(
            command=command,
            text=f"{command} {args}" if args else command,
            args=args,
        ),
    )


def _private_text(text: str) -> PrivateMessage:
    return PrivateMessage(
        update_id=3,
        message_id=30,
        chat_id=12345,
        date=1700000000,
        from_user=_user(),
        content=TextContent(text=text),
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_matches_private_next_reunion_command() -> None:
    handler = NextReunionHandler()
    assert handler.matches(_private_command("/next_reunion"), _ctx()) is True


def test_matches_group_next_reunion_command() -> None:
    handler = NextReunionHandler()
    assert handler.matches(_group_command("/next_reunion"), _ctx()) is True


def test_does_not_match_other_commands() -> None:
    handler = NextReunionHandler()
    assert handler.matches(_private_command("/start"), _ctx()) is False


def test_does_not_match_text_messages() -> None:
    handler = NextReunionHandler()
    assert handler.matches(_private_text("hello"), _ctx()) is False


# --------------------------------------------------------------------------- #
# Set date
# --------------------------------------------------------------------------- #


@patch("something_really_bot.features.next_reunion.handler.date")
async def test_set_date_happy_path(mock_date: Any) -> None:
    mock_date.today.return_value = date(2026, 5, 25)
    mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

    storage = _FakePostgresStorage()
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion", "2026-06-15")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "2026-06-15" in result.reply_text
    assert "21 days" in result.reply_text
    assert storage._rows[0]["target_date"] == date(2026, 6, 15)


async def test_set_date_invalid_format() -> None:
    storage = _FakePostgresStorage()
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion", "not-a-date")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Invalid date format" in result.reply_text
    assert "YYYY-MM-DD" in result.reply_text


async def test_set_date_storage_failure() -> None:
    storage = _FakePostgresStorage()
    storage.raises_on_execute = RuntimeError("PG down")
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion", "2026-06-15")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Failed to save" in result.reply_text


# --------------------------------------------------------------------------- #
# Query date
# --------------------------------------------------------------------------- #


@patch("something_really_bot.features.next_reunion.handler.date")
async def test_query_date_shows_countdown(mock_date: Any) -> None:
    mock_date.today.return_value = date(2026, 5, 25)
    mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 7)}]
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "2026-06-07" in result.reply_text
    assert "13 days" in result.reply_text


async def test_query_date_not_set() -> None:
    storage = _FakePostgresStorage()
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "not yet known" in result.reply_text


async def test_query_date_storage_failure() -> None:
    storage = _FakePostgresStorage()
    storage.raises_on_fetch = RuntimeError("PG down")
    handler = NextReunionHandler(_storage=storage)
    update = _private_command("/next_reunion")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Failed to retrieve" in result.reply_text


# --------------------------------------------------------------------------- #
# No storage configured
# --------------------------------------------------------------------------- #


async def test_handle_without_storage_returns_error() -> None:
    handler = NextReunionHandler(_storage=None)
    # Patch get_postgres_storage to return None
    with patch(
        "something_really_bot.features.next_reunion.handler.get_postgres_storage",
        return_value=None,
    ):
        result = await handler.handle(_private_command("/next_reunion"), _ctx())

    assert result.handled is True
    assert "not configured" in result.reply_text.lower()


# --------------------------------------------------------------------------- #
# Works in supergroup
# --------------------------------------------------------------------------- #


def test_matches_supergroup_command() -> None:
    handler = NextReunionHandler()
    update = SupergroupMessage(
        update_id=4,
        message_id=40,
        chat_id=-100456,
        date=1700000000,
        from_user=_user(),
        chat_title="Super Group",
        content=CommandContent(
            command="/next_reunion",
            text="/next_reunion",
        ),
    )
    assert handler.matches(update, _ctx()) is True
