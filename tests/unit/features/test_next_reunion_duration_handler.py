"""Tests for :mod:`something_really_bot.features.next_reunion.duration_handler`."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unittest.mock import patch

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.next_reunion.duration_handler import (
    NextReunionDurationHandler,
)
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
    """In-memory stub for the Postgres storage used by reunion handlers."""

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
        if "ALTER TABLE" in sql:
            return
        if "INSERT" in sql:
            self._rows = [{"target_date": params[0], "duration_days": None}]
            return
        if "UPDATE" in sql and "duration_days" in sql:
            if self._rows:
                self._rows[0]["duration_days"] = params[0]
            return

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.raises_on_fetch is not None:
            raise self.raises_on_fetch
        if "duration_days" in sql:
            return [{"duration_days": r.get("duration_days")} for r in self._rows]
        return [{"target_date": r["target_date"]} for r in self._rows]


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


def test_matches_private_duration_command() -> None:
    handler = NextReunionDurationHandler()
    assert handler.matches(_private_command("/next_reunion_duration"), _ctx()) is True


def test_matches_group_duration_command() -> None:
    handler = NextReunionDurationHandler()
    assert handler.matches(_group_command("/next_reunion_duration"), _ctx()) is True


def test_matches_supergroup_duration_command() -> None:
    handler = NextReunionDurationHandler()
    update = SupergroupMessage(
        update_id=4,
        message_id=40,
        chat_id=-100456,
        date=1700000000,
        from_user=_user(),
        chat_title="Super Group",
        content=CommandContent(
            command="/next_reunion_duration",
            text="/next_reunion_duration",
        ),
    )
    assert handler.matches(update, _ctx()) is True


def test_does_not_match_other_commands() -> None:
    handler = NextReunionDurationHandler()
    assert handler.matches(_private_command("/next_reunion"), _ctx()) is False


def test_does_not_match_text_messages() -> None:
    handler = NextReunionDurationHandler()
    assert handler.matches(_private_text("hello"), _ctx()) is False


# --------------------------------------------------------------------------- #
# Set duration
# --------------------------------------------------------------------------- #


async def test_set_duration_happy_path() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "5")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "5 days" in result.reply_text
    assert "2026-06-15" in result.reply_text
    assert storage._rows[0]["duration_days"] == 5


async def test_set_duration_single_day() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "1")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "1 day" in result.reply_text
    assert "1 days" not in result.reply_text


async def test_set_duration_invalid_format() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "five")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Invalid duration" in result.reply_text


async def test_set_duration_zero_rejected() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "0")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "at least 1 day" in result.reply_text


async def test_set_duration_negative_rejected() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "-3")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "at least 1 day" in result.reply_text


async def test_set_duration_no_reunion_date() -> None:
    storage = _FakePostgresStorage()
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "5")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "No reunion date" in result.reply_text
    assert "/next_reunion" in result.reply_text


async def test_set_duration_storage_failure() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": None}]
    storage.raises_on_execute = RuntimeError("PG down")
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration", "5")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Failed" in result.reply_text


# --------------------------------------------------------------------------- #
# Query duration
# --------------------------------------------------------------------------- #


async def test_query_duration_shows_value() -> None:
    storage = _FakePostgresStorage()
    storage._rows = [{"target_date": date(2026, 6, 15), "duration_days": 5}]
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "5 days" in result.reply_text


async def test_query_duration_not_set() -> None:
    storage = _FakePostgresStorage()
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "No reunion duration" in result.reply_text


async def test_query_duration_storage_failure() -> None:
    storage = _FakePostgresStorage()
    storage.raises_on_fetch = RuntimeError("PG down")
    handler = NextReunionDurationHandler(_storage=storage)
    update = _private_command("/next_reunion_duration")

    result = await handler.handle(update, _ctx())

    assert result.handled is True
    assert "Failed to retrieve" in result.reply_text


# --------------------------------------------------------------------------- #
# No storage configured
# --------------------------------------------------------------------------- #


async def test_handle_without_storage_returns_error() -> None:
    handler = NextReunionDurationHandler(_storage=None)
    with patch(
        "something_really_bot.features.next_reunion.duration_handler.get_postgres_storage",
        return_value=None,
    ):
        result = await handler.handle(_private_command("/next_reunion_duration"), _ctx())

    assert result.handled is True
    assert "not configured" in result.reply_text.lower()
