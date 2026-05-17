"""Postgres persistence for voice transcription jobs (#43).

Mirrors the ``video_download_jobs`` shape from #42: one row per
transcription attempt in ``public.voice_transcription_jobs``, created
on first use via ``ensure_table()`` so the bot can bootstrap a fresh
DB without a separate migration step.

Status lifecycle:

    pending → downloading → uploading → transcribing → analyzing
                                                          │
                                                          ▼
                                                       sending → succeeded
                                                          │
                                                          └──────► failed
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import PostgresError, PostgresStorage

_logger = get_logger(__name__)

JobStatus = Literal[
    "pending",
    "downloading",
    "uploading",
    "transcribing",
    "analyzing",
    "sending",
    "succeeded",
    "failed",
]

TABLE_FQN = "public.voice_transcription_jobs"

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_FQN} (
        id BIGSERIAL PRIMARY KEY,
        bot_id TEXT NOT NULL,
        chat_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        telegram_file_id TEXT NOT NULL,
        telegram_file_unique_id TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        file_size_bytes BIGINT,
        mime_type TEXT,
        status TEXT NOT NULL,
        gcs_object_path TEXT,
        transcript TEXT,
        summary TEXT,
        emotion TEXT,
        telegram_reply_message_id BIGINT,
        error_class TEXT,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_CREATE_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS voice_transcription_jobs_chat_id_created_at_idx "
    f"ON {TABLE_FQN} (chat_id, created_at DESC)"
)


@dataclass(frozen=True)
class JobRow:
    """Columns the caller controls at insert time."""

    bot_id: str
    chat_id: int
    user_id: int
    message_id: int
    telegram_file_id: str
    telegram_file_unique_id: str
    duration_seconds: int
    file_size_bytes: int | None
    mime_type: str | None


class VoiceJobStorage:
    """CRUD over ``public.voice_transcription_jobs``."""

    def __init__(self, storage: PostgresStorage) -> None:
        self._pg = storage
        self._table_ready = False

    async def ensure_table(self) -> None:
        if self._table_ready:
            return
        await self._pg.execute(_CREATE_TABLE_SQL)
        await self._pg.execute(_CREATE_INDEX_SQL)
        self._table_ready = True

    async def insert_pending(self, job: JobRow) -> int:
        await self.ensure_table()
        sql = (
            f"INSERT INTO {TABLE_FQN} "
            "(bot_id, chat_id, user_id, message_id, telegram_file_id, "
            "telegram_file_unique_id, duration_seconds, file_size_bytes, "
            "mime_type, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending') "
            "RETURNING id"
        )
        rows = await self._pg.fetch_all(
            sql,
            (
                job.bot_id,
                job.chat_id,
                job.user_id,
                job.message_id,
                job.telegram_file_id,
                job.telegram_file_unique_id,
                job.duration_seconds,
                job.file_size_bytes,
                job.mime_type,
            ),
        )
        if not rows:
            raise PostgresError("insert returning id produced no rows")
        return int(rows[0]["id"])

    async def update_status(self, job_id: int, status: JobStatus) -> None:
        sql = f"UPDATE {TABLE_FQN} SET status = %s, updated_at = %s WHERE id = %s"
        await self._pg.execute(sql, (status, datetime.now(UTC), job_id))

    async def mark_succeeded(
        self,
        job_id: int,
        *,
        gcs_object_path: str,
        transcript: str,
        summary: str,
        emotion: str,
        telegram_reply_message_id: int | None,
    ) -> None:
        sql = (
            f"UPDATE {TABLE_FQN} SET status = 'succeeded', "
            "gcs_object_path = %s, transcript = %s, summary = %s, "
            "emotion = %s, telegram_reply_message_id = %s, "
            "updated_at = %s WHERE id = %s"
        )
        await self._pg.execute(
            sql,
            (
                gcs_object_path,
                transcript,
                summary,
                emotion,
                telegram_reply_message_id,
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
