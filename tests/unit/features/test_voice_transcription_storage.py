"""Tests for the ``fetch_stuck`` query behind the backfill job.

The rest of :class:`VoiceJobStorage` is exercised through the handler
tests; this one owns SQL the backfill runs against live rows, so the
parameter order is worth pinning down.
"""

from datetime import UTC, datetime
from typing import Any

from something_really_bot.features.voice_transcription.storage import (
    TABLE_FQN,
    VoiceJobStorage,
)


class _FakePostgres:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, Any]] = []
        self.fetched: list[tuple[str, Any]] = []

    async def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    async def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.fetched.append((sql, params))
        return self.rows


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 11,
        "status": "downloading",
        "bot_id": "default",
        "chat_id": 100,
        "user_id": 999,
        "message_id": 42,
        "telegram_file_id": "file-abc",
        "telegram_file_unique_id": "uniq-abc",
        "duration_seconds": 30,
        "file_size_bytes": 4096,
        "mime_type": "audio/ogg",
    }
    row.update(overrides)
    return row


async def test_fetch_stuck_excludes_terminal_in_flight_and_ancient_rows() -> None:
    pg = _FakePostgres()
    storage = VoiceJobStorage(pg)  # type: ignore[arg-type]
    stale_before = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    created_after = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)

    await storage.fetch_stuck(stale_before=stale_before, created_after=created_after, limit=10)

    sql, params = pg.fetched[0]
    assert TABLE_FQN in sql
    assert "status NOT IN (%s, %s)" in sql
    assert "updated_at < %s" in sql
    assert "created_at > %s" in sql
    assert params == ("succeeded", "failed", stale_before, created_after, 10)


async def test_fetch_stuck_maps_rows_to_stuck_jobs() -> None:
    pg = _FakePostgres([_row(), _row(id=12, mime_type=None, file_size_bytes=None)])
    storage = VoiceJobStorage(pg)  # type: ignore[arg-type]

    now = datetime.now(UTC)
    stuck = await storage.fetch_stuck(stale_before=now, created_after=now, limit=10)

    assert [j.job_id for j in stuck] == [11, 12]
    assert stuck[0].status == "downloading"
    assert stuck[0].row.telegram_file_id == "file-abc"
    assert stuck[0].row.file_size_bytes == 4096
    assert stuck[1].row.mime_type is None
    assert stuck[1].row.file_size_bytes is None
