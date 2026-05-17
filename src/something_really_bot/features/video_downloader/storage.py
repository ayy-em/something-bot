"""Postgres persistence for video download jobs (#42).

Stores one row per download attempt in ``public.video_download_jobs``.
The table is created on first use via ``ensure_table()`` so the bot can
bootstrap a fresh DB without a separate migration step (matches the
``CREATE SCHEMA IF NOT EXISTS`` pattern used elsewhere in the codebase).

Status lifecycle:

    pending → downloading → uploading → sending → succeeded
                              │             │
                              └────────────►failed
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import PostgresError, PostgresStorage

_logger = get_logger(__name__)

JobStatus = Literal["pending", "downloading", "uploading", "sending", "succeeded", "failed"]

# We bypass PostgresStorage's auto-schema-qualification by issuing raw
# SQL; the table lives in ``public`` per the operator's directive (#42
# scope discussion). Keep the schema name in one constant so the
# qualification is obvious at the call sites.
TABLE_FQN = "public.video_download_jobs"

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_FQN} (
        id BIGSERIAL PRIMARY KEY,
        bot_id TEXT NOT NULL,
        chat_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        source_url TEXT NOT NULL,
        platform TEXT NOT NULL,
        status TEXT NOT NULL,
        gcs_object_path TEXT,
        file_size_bytes BIGINT,
        duration_seconds REAL,
        telegram_video_message_id BIGINT,
        error_class TEXT,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_CREATE_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS video_download_jobs_chat_id_created_at_idx "
    f"ON {TABLE_FQN} (chat_id, created_at DESC)"
)


@dataclass(frozen=True)
class JobRow:
    """One ``video_download_jobs`` row, with the columns the caller controls."""

    bot_id: str
    chat_id: int
    user_id: int
    message_id: int
    source_url: str
    platform: str


class VideoJobStorage:
    """CRUD over ``public.video_download_jobs``."""

    def __init__(self, storage: PostgresStorage) -> None:
        self._pg = storage
        self._table_ready = False

    async def ensure_table(self) -> None:
        """Create the table + index if they don't exist. Idempotent."""
        if self._table_ready:
            return
        await self._pg.execute(_CREATE_TABLE_SQL)
        await self._pg.execute(_CREATE_INDEX_SQL)
        self._table_ready = True

    async def insert_pending(self, job: JobRow) -> int:
        """Insert a fresh ``pending`` row, return its ``id``."""
        await self.ensure_table()
        sql = (
            f"INSERT INTO {TABLE_FQN} "
            "(bot_id, chat_id, user_id, message_id, source_url, platform, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id"
        )
        rows = await self._pg.fetch_all(
            sql,
            (
                job.bot_id,
                job.chat_id,
                job.user_id,
                job.message_id,
                job.source_url,
                job.platform,
            ),
        )
        if not rows:
            raise PostgresError("insert returning id produced no rows")
        return int(rows[0]["id"])

    async def update_status(self, job_id: int, status: JobStatus) -> None:
        """Bump status (and ``updated_at``)."""
        sql = f"UPDATE {TABLE_FQN} SET status = %s, updated_at = %s WHERE id = %s"
        await self._pg.execute(sql, (status, datetime.now(UTC), job_id))

    async def mark_succeeded(
        self,
        job_id: int,
        *,
        gcs_object_path: str,
        file_size_bytes: int,
        duration_seconds: float | None,
        telegram_video_message_id: int | None,
    ) -> None:
        sql = (
            f"UPDATE {TABLE_FQN} SET status = 'succeeded', "
            "gcs_object_path = %s, file_size_bytes = %s, "
            "duration_seconds = %s, telegram_video_message_id = %s, "
            "updated_at = %s WHERE id = %s"
        )
        await self._pg.execute(
            sql,
            (
                gcs_object_path,
                file_size_bytes,
                duration_seconds,
                telegram_video_message_id,
                datetime.now(UTC),
                job_id,
            ),
        )

    async def mark_failed(self, job_id: int, *, error_class: str, error_message: str) -> None:
        sql = (
            f"UPDATE {TABLE_FQN} SET status = 'failed', "
            "error_class = %s, error_message = %s, updated_at = %s "
            "WHERE id = %s"
        )
        await self._pg.execute(
            sql,
            (error_class, error_message[:2000], datetime.now(UTC), job_id),
        )
