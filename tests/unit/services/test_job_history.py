"""Tests for ``services.job_history`` (#53, #54).

Exercises the SQL wrapper, the job-name derivation helper, the
best-effort recording semantics, and the 24h summary query feeding
the daily digest tally — all without touching a real database.
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
    fetched: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    next_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    execute_raises: BaseException | None = None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.execute_raises is not None:
            raise self.execute_raises
        self.executed.append((sql, params))

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.fetched.append((sql, params))
        if not self.next_rows:
            return []
        return self.next_rows.pop(0)


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


async def test_fetch_recent_summary_aggregates_and_sorts() -> None:
    pg = _FakePostgres(
        next_rows=[
            [
                {"job_name": "video_downloader", "status": "succeeded", "count": 3},
                {"job_name": "video_downloader", "status": "failed", "count": 1},
                {"job_name": "voice_transcription", "status": "succeeded", "count": 2},
                {"job_name": "daily-digest", "status": "succeeded", "count": 1},
            ]
        ]
    )
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]

    tally = await logger.fetch_recent_summary(bot_id="default", window=timedelta(hours=24))

    # Sorted by descending total then alphabetically by name on ties.
    names = [t.job_name for t in tally]
    assert names == ["video_downloader", "voice_transcription", "daily-digest"]
    vd = tally[0]
    assert vd.succeeded == 3
    assert vd.failed == 1
    assert vd.total == 4


async def test_fetch_recent_summary_returns_empty_when_no_rows() -> None:
    pg = _FakePostgres(next_rows=[[]])
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]

    tally = await logger.fetch_recent_summary(bot_id="default")

    assert tally == []


async def test_fetch_recent_summary_window_parameter_is_passed_to_query() -> None:
    pg = _FakePostgres(next_rows=[[]])
    logger = JobHistoryLogger(pg)  # type: ignore[arg-type]
    before = datetime.now(UTC)

    await logger.fetch_recent_summary(bot_id="bot-x", window=timedelta(hours=12))

    assert len(pg.fetched) == 1
    _, params = pg.fetched[0]
    assert params[0] == "bot-x"
    since = params[1]
    # `since` should be ~12 hours before now.
    delta = before - since
    assert timedelta(hours=11, minutes=59) < delta < timedelta(hours=12, minutes=1)
