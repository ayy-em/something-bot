"""Tests for ``services.job_history`` (#53).

Exercises the SQL wrapper, the job-name derivation helper, and the
best-effort recording semantics — all without touching a real database.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from something_really_bot.persistence.postgres import PostgresError
from something_really_bot.services.job_history import (
    JobHistoryLogger,
    JobHistoryRow,
    derive_job_name,
    derive_job_name_from_module,
    safe_record,
)


@dataclass
class _FakePostgres:
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    execute_raises: BaseException | None = None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.execute_raises is not None:
            raise self.execute_raises
        self.executed.append((sql, params))


def _row(**overrides: Any) -> JobHistoryRow:
    now = datetime.now(UTC)
    base = {
        "bot_id": "default",
        "job_name": "voice_transcription",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now + timedelta(seconds=1),
    }
    base.update(overrides)
    return JobHistoryRow(**base)


async def test_record_inserts_with_expected_columns() -> None:
    pg = _FakePostgres()
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]
    row = _row(chat_id=100, user_id=999)

    await logger.record(row)

    # ensure_table issues CREATE TABLE + 2 indexes, then the INSERT.
    assert len(pg.executed) == 4
    insert_sql, params = pg.executed[-1]
    assert "INSERT INTO public.job_history_log" in insert_sql
    assert params[0] == "default"
    assert params[1] == "voice_transcription"
    assert params[2] == 100
    assert params[3] == 999
    assert params[4] == "succeeded"
    assert params[5] is None  # error_class
    assert params[6] is None  # error_message
    assert params[7] == row.started_at
    assert params[8] == row.finished_at


async def test_record_truncates_long_error_message() -> None:
    pg = _FakePostgres()
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]
    long_message = "x" * 5000

    await logger.record(
        _row(status="failed", error_class="RuntimeError", error_message=long_message)
    )

    _, params = pg.executed[-1]
    assert params[5] == "RuntimeError"
    assert len(params[6]) == 2000


async def test_ensure_table_runs_only_once() -> None:
    pg = _FakePostgres()
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]

    await logger.record(_row())
    await logger.record(_row())

    # 3 DDL + 2 INSERTs = 5 executes (table-setup happens only on first call).
    assert len(pg.executed) == 5


async def test_safe_record_swallows_postgres_error() -> None:
    pg = _FakePostgres(execute_raises=PostgresError("connection refused"))
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]

    # Should not raise.
    await safe_record(logger, _row())


async def test_safe_record_with_none_logger_is_noop() -> None:
    # Should not raise — the logger may legitimately be ``None`` when
    # Postgres isn't configured.
    await safe_record(None, _row())


def test_derive_job_name_from_features_module() -> None:
    assert (
        derive_job_name_from_module("something_really_bot.features.voice_transcription.handler")
        == "voice_transcription"
    )


def test_derive_job_name_from_non_features_module_falls_back_to_leaf() -> None:
    assert derive_job_name_from_module("something_really_bot.services.jobs") == "jobs"


def test_derive_job_name_from_instance() -> None:
    class _Sentinel:
        pass

    _Sentinel.__module__ = "something_really_bot.features.commands.handler"
    assert derive_job_name(_Sentinel()) == "commands"
