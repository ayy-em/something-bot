"""Tests for :mod:`something_really_bot.persistence.postgres` (#31).

Uses a hand-rolled fake connection rather than the real psycopg
driver: we want to verify the wrapper's commit/rollback/quoting
behaviour without needing a Postgres server in CI.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.persistence.postgres import (
    PostgresError,
    PostgresStorage,
)


@dataclass
class _FakeColumn:
    name: str


@dataclass
class _FakeCursor:
    rows: list[tuple] = field(default_factory=list)
    description: list[_FakeColumn] | None = None
    executed: list[tuple[str, tuple]] = field(default_factory=list)
    raise_on_execute: BaseException | None = None
    fetch_payload: list[tuple] = field(default_factory=list)

    def execute(self, sql: str, params: tuple = ()) -> None:
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        self.executed.append((sql, params))

    def fetchall(self) -> list[tuple]:
        return list(self.fetch_payload)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_a: Any) -> None: ...


@dataclass
class _FakeConnection:
    cursor_obj: _FakeCursor = field(default_factory=_FakeCursor)
    committed: bool = False
    rolled_back: bool = False
    closed: bool = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _storage_with(conn: _FakeConnection, *, schema: str = "something_bot") -> PostgresStorage:
    return PostgresStorage(
        dsn=SecretStr("postgres://stub"),
        schema=schema,
        connection_factory=lambda: conn,
    )


async def test_ensure_schema_runs_create_schema_if_not_exists() -> None:
    conn = _FakeConnection()
    storage = _storage_with(conn)

    await storage.ensure_schema()

    assert conn.cursor_obj.executed == [
        ('CREATE SCHEMA IF NOT EXISTS "something_bot"', ())
    ]
    assert conn.committed is True
    assert conn.closed is True


async def test_insert_row_uses_schema_and_table_quoting() -> None:
    conn = _FakeConnection()
    storage = _storage_with(conn)

    await storage.insert_row("messages", {"id": 1, "body": "hi"})

    sql, params = conn.cursor_obj.executed[0]
    assert sql == 'INSERT INTO "something_bot"."messages" ("id", "body") VALUES (%s, %s)'
    assert params == (1, "hi")


async def test_insert_row_rejects_unsafe_table_identifier() -> None:
    conn = _FakeConnection()
    storage = _storage_with(conn)

    with pytest.raises(PostgresError) as excinfo:
        await storage.insert_row("messages; DROP TABLE x;", {"id": 1})

    assert "Unsafe table identifier" in str(excinfo.value)
    assert conn.cursor_obj.executed == []
    assert conn.closed is False  # never connected


async def test_insert_row_rejects_empty_row() -> None:
    conn = _FakeConnection()
    storage = _storage_with(conn)

    with pytest.raises(PostgresError):
        await storage.insert_row("messages", {})


async def test_fetch_all_returns_rows_as_dicts() -> None:
    conn = _FakeConnection(
        cursor_obj=_FakeCursor(
            description=[_FakeColumn("id"), _FakeColumn("body")],
            fetch_payload=[(1, "a"), (2, "b")],
        )
    )
    storage = _storage_with(conn)

    rows = await storage.fetch_all("SELECT id, body FROM x", ())

    assert rows == [{"id": 1, "body": "a"}, {"id": 2, "body": "b"}]
    assert conn.committed is True


async def test_driver_exception_funnels_to_postgres_error_and_rolls_back() -> None:
    conn = _FakeConnection(
        cursor_obj=_FakeCursor(raise_on_execute=RuntimeError("connection lost"))
    )
    storage = _storage_with(conn)

    with pytest.raises(PostgresError) as excinfo:
        await storage.execute("SELECT 1", ())

    assert "connection lost" in str(excinfo.value)
    assert conn.rolled_back is True
    assert conn.closed is True
