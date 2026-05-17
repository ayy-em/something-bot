"""Shared multi-turn workflow state (partial #48).

Command-driven features (``/dutch``, ``/make-sticker``, ``/ocr``,
``/summarize``) need to remember "this user just invoked ``/foo`` and
their next message is the input for that workflow." This module owns
that bit of state.

Storage is Postgres: ``public.pending_user_actions`` with a TTL column
so stale rows are simply ignored on read (and overwritten on next set).
UPSERT on ``(bot_id, chat_id, user_id)`` means setting a new pending
action atomically replaces any prior one for that user, so the user
can re-invoke a command or switch commands without confusion.

The webhook orchestrator resolves the pending action *before* dispatch
and stashes the result on ``BotContext.pending_action``; handlers read
that synchronously from ``matches()``. After processing, the handler
clears or advances the state via the async store.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import (
    PostgresError,
    PostgresStorage,
    get_postgres_storage,
)

_logger = get_logger(__name__)

ExpectedInput = Literal["text", "image", "document", "voice"]

DEFAULT_TTL_SECONDS = 10 * 60  # 10 minutes

_TABLE_FQN = "public.pending_user_actions"

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {_TABLE_FQN} (
        bot_id TEXT NOT NULL,
        chat_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        command TEXT NOT NULL,
        expected_input TEXT NOT NULL,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (bot_id, chat_id, user_id)
    )
"""


@dataclass(frozen=True)
class PendingAction:
    """One row in ``public.pending_user_actions``."""

    bot_id: str
    chat_id: int
    user_id: int
    command: str
    expected_input: ExpectedInput
    metadata: dict[str, Any]
    created_at: datetime
    expires_at: datetime


class PendingActionStore:
    """Async wrapper over the ``public.pending_user_actions`` table."""

    def __init__(self, storage: PostgresStorage) -> None:
        self._pg = storage
        self._table_ready = False

    async def ensure_table(self) -> None:
        if self._table_ready:
            return
        await self._pg.execute(_CREATE_TABLE_SQL)
        self._table_ready = True

    async def set(
        self,
        *,
        bot_id: str,
        chat_id: int,
        user_id: int,
        command: str,
        expected_input: ExpectedInput,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        """Upsert a pending action; any prior row for this user is replaced."""
        await self.ensure_table()
        import json

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        sql = f"""
            INSERT INTO {_TABLE_FQN}
                (bot_id, chat_id, user_id, command, expected_input,
                 metadata, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (bot_id, chat_id, user_id) DO UPDATE SET
                command = EXCLUDED.command,
                expected_input = EXCLUDED.expected_input,
                metadata = EXCLUDED.metadata,
                created_at = EXCLUDED.created_at,
                expires_at = EXCLUDED.expires_at
        """
        await self._pg.execute(
            sql,
            (
                bot_id,
                chat_id,
                user_id,
                command,
                expected_input,
                json.dumps(metadata or {}),
                now,
                expires_at,
            ),
        )

    async def get(self, *, bot_id: str, chat_id: int, user_id: int) -> PendingAction | None:
        """Return the un-expired pending action for this user, or ``None``."""
        await self.ensure_table()
        sql = (
            f"SELECT bot_id, chat_id, user_id, command, expected_input, "
            f"metadata, created_at, expires_at FROM {_TABLE_FQN} "
            f"WHERE bot_id = %s AND chat_id = %s AND user_id = %s "
            f"AND expires_at > now()"
        )
        rows = await self._pg.fetch_all(sql, (bot_id, chat_id, user_id))
        if not rows:
            return None
        row = rows[0]
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            import json

            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        return PendingAction(
            bot_id=row["bot_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            command=row["command"],
            expected_input=row["expected_input"],
            metadata=metadata,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    async def clear(self, *, bot_id: str, chat_id: int, user_id: int) -> None:
        """Delete the row for this (bot, chat, user), if any."""
        await self.ensure_table()
        sql = f"DELETE FROM {_TABLE_FQN} WHERE bot_id = %s AND chat_id = %s AND user_id = %s"
        await self._pg.execute(sql, (bot_id, chat_id, user_id))


async def safe_get_pending_action(
    store: PendingActionStore | None,
    *,
    bot_id: str,
    chat_id: int,
    user_id: int,
) -> PendingAction | None:
    """Best-effort lookup: Postgres failures yield ``None`` and a log line.

    Called by the webhook orchestrator before dispatch. If Postgres is
    unreachable we'd rather treat every user as having no pending state
    than hard-fail the webhook (SPEC §6.9).
    """
    if store is None:
        return None
    try:
        return await store.get(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    except PostgresError as exc:
        _logger.warning(
            "pending_action_lookup_failed",
            extra={"exception_type": type(exc).__name__, "chat_id": chat_id},
        )
        return None


@lru_cache(maxsize=1)
def get_pending_action_store() -> PendingActionStore | None:
    """Process-wide singleton, or ``None`` if Postgres isn't configured."""
    storage = get_postgres_storage()
    if storage is None:
        return None
    return PendingActionStore(storage)
