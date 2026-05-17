"""Tests for the pending_user_actions store (partial #48).

Exercises the SQL-level wrapper with a fake PostgresStorage so the
upsert / expiration / clear behaviour is covered without needing a
real database.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from something_really_bot.services.pending_actions import (
    PendingActionStore,
    safe_get_pending_action,
)


@dataclass
class _FakePostgres:
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    fetched: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    next_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    fetch_raises: BaseException | None = None

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))

    async def fetch_all(self, sql, params=()):
        if self.fetch_raises is not None:
            raise self.fetch_raises
        self.fetched.append((sql, params))
        if not self.next_rows:
            return []
        return self.next_rows.pop(0)


async def test_set_upserts_with_expected_params() -> None:
    pg = _FakePostgres()
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    await store.set(
        bot_id="default",
        chat_id=100,
        user_id=999,
        command="dutch",
        expected_input="text",
        metadata={"foo": "bar"},
    )

    # ensure_table + upsert
    assert len(pg.executed) == 2
    upsert_sql, params = pg.executed[1]
    assert "INSERT INTO public.pending_user_actions" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert params[0] == "default"
    assert params[1] == 100
    assert params[2] == 999
    assert params[3] == "dutch"
    assert params[4] == "text"
    assert json.loads(params[5]) == {"foo": "bar"}


async def test_get_returns_pending_action_when_row_present() -> None:
    now = datetime.now(UTC)
    pg = _FakePostgres(
        next_rows=[
            [
                {
                    "bot_id": "default",
                    "chat_id": 100,
                    "user_id": 999,
                    "command": "dutch",
                    "expected_input": "text",
                    "metadata": {"foo": "bar"},
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=10),
                }
            ]
        ]
    )
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    result = await store.get(bot_id="default", chat_id=100, user_id=999)

    assert result is not None
    assert result.command == "dutch"
    assert result.expected_input == "text"
    assert result.metadata == {"foo": "bar"}
    # The "expires_at > now()" clause is right there in the SQL.
    _, fetch_sql, _ = pg.fetched[0][0], pg.fetched[0][0], pg.fetched[0][1]
    assert "expires_at > now()" in fetch_sql


async def test_get_parses_metadata_when_returned_as_string() -> None:
    # Some drivers surface jsonb as str; the store has to cope.
    pg = _FakePostgres(
        next_rows=[
            [
                {
                    "bot_id": "default",
                    "chat_id": 100,
                    "user_id": 999,
                    "command": "ocr",
                    "expected_input": "image",
                    "metadata": '{"bar": "baz"}',
                    "created_at": datetime.now(UTC),
                    "expires_at": datetime.now(UTC) + timedelta(minutes=1),
                }
            ]
        ]
    )
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    result = await store.get(bot_id="default", chat_id=100, user_id=999)

    assert result is not None
    assert result.metadata == {"bar": "baz"}


async def test_get_returns_none_when_no_row() -> None:
    pg = _FakePostgres(next_rows=[[]])
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    assert await store.get(bot_id="default", chat_id=100, user_id=999) is None


async def test_clear_emits_delete() -> None:
    pg = _FakePostgres()
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    await store.clear(bot_id="default", chat_id=100, user_id=999)

    delete_sql, params = pg.executed[1]
    assert "DELETE FROM public.pending_user_actions" in delete_sql
    assert params == ("default", 100, 999)


async def test_safe_get_returns_none_on_postgres_error() -> None:
    from something_really_bot.persistence.postgres import PostgresError

    pg = _FakePostgres(fetch_raises=PostgresError("db down"))
    store = PendingActionStore(pg)  # type: ignore[arg-type]

    result = await safe_get_pending_action(store, bot_id="default", chat_id=100, user_id=999)
    assert result is None


async def test_safe_get_returns_none_when_store_none() -> None:
    assert await safe_get_pending_action(None, bot_id="default", chat_id=1, user_id=2) is None


@pytest.mark.parametrize("ttl", [60, 600])
async def test_set_respects_ttl(ttl: int) -> None:
    pg = _FakePostgres()
    store = PendingActionStore(pg)  # type: ignore[arg-type]
    before = datetime.now(UTC)

    await store.set(
        bot_id="default",
        chat_id=100,
        user_id=999,
        command="x",
        expected_input="text",
        ttl_seconds=ttl,
    )

    _, params = pg.executed[1]
    created_at = params[6]
    expires_at = params[7]
    assert (expires_at - created_at) == timedelta(seconds=ttl)
    assert before <= created_at <= datetime.now(UTC)
