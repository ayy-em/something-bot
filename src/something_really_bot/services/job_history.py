"""Per-invocation job-history log (#53).

Every handled webhook update and every scheduled-job invocation
produces one row in ``public.job_history_log``. The table is the
ground truth for "what did the bot do recently" — feeds the daily
digest tally (#54) and answers "did this job run today?" without
grepping logs.

Recording is best-effort: a Postgres failure must not break the
caller. The :func:`safe_record` helper swallows
:class:`PostgresError` and logs a warning, matching the same pattern
used by ``persistence.bigquery`` in SPEC §6.9.

Job names mirror the source folder under ``features/`` (for handler
dispatches) or the registered job name (for scheduled jobs). See
:func:`derive_job_name`.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import (
    PostgresError,
    PostgresStorage,
    get_postgres_storage,
)

_logger = get_logger(__name__)

_ERROR_MESSAGE_MAX = 2000

TABLE_FQN = "public.job_history_log"

JobStatus = Literal["succeeded", "failed"]

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_FQN} (
        id BIGSERIAL PRIMARY KEY,
        bot_id TEXT NOT NULL,
        job_name TEXT NOT NULL,
        chat_id BIGINT,
        user_id BIGINT,
        status TEXT NOT NULL,
        error_class TEXT,
        error_message TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ NOT NULL
    )
"""

_CREATE_INDEX_STARTED = (
    f"CREATE INDEX IF NOT EXISTS job_history_log_started_at_idx ON {TABLE_FQN} (started_at DESC)"
)
_CREATE_INDEX_NAME_STARTED = (
    f"CREATE INDEX IF NOT EXISTS job_history_log_job_name_started_at_idx "
    f"ON {TABLE_FQN} (job_name, started_at DESC)"
)


@dataclass(frozen=True)
class JobHistoryRow:
    """One invocation record."""

    bot_id: str
    job_name: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime
    chat_id: int | None = None
    user_id: int | None = None
    error_class: str | None = None
    error_message: str | None = None


class JobHistoryLogger:
    """Async writer for ``public.job_history_log``."""

    def __init__(self, storage: PostgresStorage) -> None:
        self._pg = storage
        self._table_ready = False

    async def ensure_table(self) -> None:
        if self._table_ready:
            return
        await self._pg.execute(_CREATE_TABLE_SQL)
        await self._pg.execute(_CREATE_INDEX_STARTED)
        await self._pg.execute(_CREATE_INDEX_NAME_STARTED)
        self._table_ready = True

    async def record(self, row: JobHistoryRow) -> None:
        """Insert one row. Raises :class:`PostgresError` on failure."""
        await self.ensure_table()
        error_message = row.error_message
        if error_message is not None and len(error_message) > _ERROR_MESSAGE_MAX:
            error_message = error_message[:_ERROR_MESSAGE_MAX]
        sql = (
            f"INSERT INTO {TABLE_FQN} "
            "(bot_id, job_name, chat_id, user_id, status, "
            " error_class, error_message, started_at, finished_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        await self._pg.execute(
            sql,
            (
                row.bot_id,
                row.job_name,
                row.chat_id,
                row.user_id,
                row.status,
                row.error_class,
                error_message,
                row.started_at,
                row.finished_at,
            ),
        )


def derive_job_name_from_module(module: str) -> str:
    """Return the ``features/<name>`` segment, or the leaf module otherwise.

    ``something_really_bot.features.voice_transcription.handler``
        → ``voice_transcription``
    ``something_really_bot.services.jobs`` → ``jobs``
    """
    parts = module.split(".")
    try:
        idx = parts.index("features")
    except ValueError:
        return parts[-1]
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return parts[-1]


def derive_job_name(handler: Any) -> str:
    """Derive the job name from a handler instance's module path."""
    module = type(handler).__module__
    return derive_job_name_from_module(module)


async def safe_record(logger: JobHistoryLogger | None, row: JobHistoryRow) -> None:
    """Best-effort record: Postgres failures are logged, not raised."""
    if logger is None:
        return
    try:
        await logger.record(row)
    except PostgresError as exc:
        _logger.warning(
            "job_history_record_failed",
            extra={
                "exception_type": type(exc).__name__,
                "job_name": row.job_name,
                "bot_id": row.bot_id,
            },
        )


@lru_cache(maxsize=1)
def get_job_history_logger() -> JobHistoryLogger | None:
    """Process-wide singleton, or ``None`` if Postgres isn't configured."""
    storage = get_postgres_storage()
    if storage is None:
        return None
    return JobHistoryLogger(storage)
