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

# Columns added after the table shipped. ``ADD COLUMN IF NOT EXISTS`` keeps
# ensure_table() idempotent for both a fresh DB (already covered by the
# CREATE above) and the live one, which predates #63.
_ADD_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE_FQN} ADD COLUMN IF NOT EXISTS parody_text TEXT",
    f"ALTER TABLE {TABLE_FQN} ADD COLUMN IF NOT EXISTS parody_gcs_object_path TEXT",
)


TERMINAL_STATUSES: tuple[JobStatus, ...] = ("succeeded", "failed")


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


@dataclass(frozen=True)
class StuckJob:
    """A row that never reached a terminal status, plus the id to resume it.

    Produced by :meth:`VoiceJobStorage.fetch_stuck` for the backfill job:
    everything needed to rebuild the background context and run the
    pipeline again against the same row.
    """

    job_id: int
    status: str
    row: JobRow


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
        for statement in _ADD_COLUMNS_SQL:
            await self._pg.execute(statement)
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

    async def fetch_stuck(
        self,
        *,
        stale_before: datetime,
        created_after: datetime,
        limit: int,
    ) -> list[StuckJob]:
        """Rows left mid-flight: not terminal, idle, and recent enough to retry.

        A job only strands like this when the background task died without
        running its failure path — which is what the 2026-09-23 getFile
        timeouts did to five memos.

        Two cutoffs, and both matter:

        - ``stale_before`` (on ``updated_at``) keeps in-progress work — a
          memo being transcribed right now — out of the result.
        - ``created_after`` keeps ancient strays out. Telegram
          ``file_id`` values do not live forever, and re-running a
          months-old row would put a fresh failure message into a chat
          that has long since moved on.
        """
        await self.ensure_table()
        placeholders = ", ".join(["%s"] * len(TERMINAL_STATUSES))
        sql = (
            "SELECT id, status, bot_id, chat_id, user_id, message_id, "
            "telegram_file_id, telegram_file_unique_id, duration_seconds, "
            "file_size_bytes, mime_type "
            f"FROM {TABLE_FQN} "
            f"WHERE status NOT IN ({placeholders}) "
            "AND updated_at < %s AND created_at > %s "
            "ORDER BY created_at LIMIT %s"
        )
        rows = await self._pg.fetch_all(
            sql, (*TERMINAL_STATUSES, stale_before, created_after, limit)
        )
        return [
            StuckJob(
                job_id=int(row["id"]),
                status=str(row["status"]),
                row=JobRow(
                    bot_id=str(row["bot_id"]),
                    chat_id=int(row["chat_id"]),
                    user_id=int(row["user_id"]),
                    message_id=int(row["message_id"]),
                    telegram_file_id=str(row["telegram_file_id"]),
                    telegram_file_unique_id=str(row["telegram_file_unique_id"]),
                    duration_seconds=int(row["duration_seconds"]),
                    file_size_bytes=(
                        int(row["file_size_bytes"]) if row["file_size_bytes"] is not None else None
                    ),
                    mime_type=(str(row["mime_type"]) if row["mime_type"] is not None else None),
                ),
            )
            for row in rows
        ]

    async def update_status(self, job_id: int, status: JobStatus) -> None:
        sql = f"UPDATE {TABLE_FQN} SET status = %s, updated_at = %s WHERE id = %s"
        await self._pg.execute(sql, (status, datetime.now(UTC), job_id))

    async def mark_succeeded(
        self,
        job_id: int,
        *,
        gcs_object_path: str,
        transcript: str,
        summary: str | None,
        emotion: str | None,
        telegram_reply_message_id: int | None,
        parody_text: str | None = None,
        parody_gcs_object_path: str | None = None,
    ) -> None:
        sql = (
            f"UPDATE {TABLE_FQN} SET status = 'succeeded', "
            "gcs_object_path = %s, transcript = %s, summary = %s, "
            "emotion = %s, telegram_reply_message_id = %s, "
            "parody_text = %s, parody_gcs_object_path = %s, "
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
                parody_text,
                parody_gcs_object_path,
                datetime.now(UTC),
                job_id,
            ),
        )

    async def mark_failed(
        self,
        job_id: int,
        *,
        error_class: str,
        error_message: str,
        transcript: str | None = None,
        summary: str | None = None,
        emotion: str | None = None,
        gcs_object_path: str | None = None,
    ) -> None:
        """Mark a job as failed, optionally preserving partial results."""
        sql = (
            f"UPDATE {TABLE_FQN} SET status = 'failed', "
            "error_class = %s, error_message = %s, "
            "transcript = %s, summary = %s, emotion = %s, "
            "gcs_object_path = %s, updated_at = %s "
            "WHERE id = %s"
        )
        await self._pg.execute(
            sql,
            (
                error_class,
                error_message[:2000],
                transcript,
                summary,
                emotion,
                gcs_object_path,
                datetime.now(UTC),
                job_id,
            ),
        )
