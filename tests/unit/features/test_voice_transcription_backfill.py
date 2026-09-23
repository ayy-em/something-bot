"""Tests for the voice transcription backfill job.

Reuses the fakes from the handler tests: the backfill drives the very
same pipeline, and duplicating the doubles here would let the two drift.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from something_really_bot.features.voice_transcription.backfill import (
    VoiceTranscriptionBackfillJob,
)
from something_really_bot.features.voice_transcription.storage import JobRow, StuckJob
from tests.unit.features.test_voice_transcription_handler import (
    _ctx,
    _FakeGCS,
    _FakeJobStorage,
    _FakeTelegram,
    _FakeTranscriber,
    _RecordingPersistence,
)


@dataclass
class _FakeStuckStorage(_FakeJobStorage):
    """Job storage that also answers ``fetch_stuck``."""

    stuck: list[StuckJob] = field(default_factory=list)
    fetch_calls: list[dict[str, Any]] = field(default_factory=list)

    async def fetch_stuck(
        self, *, stale_before: datetime, created_after: datetime, limit: int
    ) -> list[StuckJob]:
        self.fetch_calls.append(
            {"stale_before": stale_before, "created_after": created_after, "limit": limit}
        )
        return list(self.stuck)


def _stuck(
    job_id: int, *, chat_id: int = 100, message_id: int = 42, duration: int = 30
) -> StuckJob:
    return StuckJob(
        job_id=job_id,
        status="downloading",
        row=JobRow(
            bot_id="default",
            chat_id=chat_id,
            user_id=999,
            message_id=message_id,
            telegram_file_id=f"file-{job_id}",
            telegram_file_unique_id=f"uniq-{job_id}",
            duration_seconds=duration,
            file_size_bytes=4096,
            mime_type="audio/ogg",
        ),
    )


def _build_job(
    *,
    telegram: _FakeTelegram,
    gcs: _FakeGCS,
    jobs: _FakeStuckStorage | None,
    transcriber: _FakeTranscriber | None,
) -> VoiceTranscriptionBackfillJob:
    return VoiceTranscriptionBackfillJob(
        job_storage_factory=lambda: jobs,
        telegram_client_factory=lambda: telegram,
        gcs_storage_factory=lambda: gcs,
        transcriber_factory=lambda: transcriber,
    )


async def test_reruns_each_stuck_job_and_replies_to_the_original_memo() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    jobs = _FakeStuckStorage(stuck=[_stuck(11), _stuck(12, chat_id=-1001, message_id=77)])
    job = _build_job(telegram=telegram, gcs=gcs, jobs=jobs, transcriber=_FakeTranscriber())

    await job.run(_ctx())

    assert len(telegram.get_file_path_calls) == 2
    assert len(telegram.download_calls) == 2
    # Fresh replies pinned to the original voice messages — there is no
    # ack message id on a re-run, so nothing is edited.
    assert len(telegram.sent_messages) == 2
    assert not telegram.edited_messages
    assert [m["chat_id"] for m in telegram.sent_messages] == [100, -1001]
    assert [m["reply_to_message_id"] for m in telegram.sent_messages] == [42, 77]


async def test_rerun_updates_the_existing_row_instead_of_inserting() -> None:
    telegram = _FakeTelegram()
    jobs = _FakeStuckStorage(stuck=[_stuck(11)])
    job = _build_job(telegram=telegram, gcs=_FakeGCS(), jobs=jobs, transcriber=_FakeTranscriber())

    await job.run(_ctx())

    assert not jobs.inserted
    assert [row["id"] for row in jobs.succeeded] == [11]
    assert all(job_id == 11 for job_id, _status in jobs.status_history)


async def test_records_an_event_per_rerun() -> None:
    persistence = _RecordingPersistence()
    jobs = _FakeStuckStorage(stuck=[_stuck(11)])
    job = _build_job(
        telegram=_FakeTelegram(), gcs=_FakeGCS(), jobs=jobs, transcriber=_FakeTranscriber()
    )

    await job.run(_ctx(persistence))

    assert [e.event for e in persistence.events] == ["voice_transcription_succeeded"]


async def test_no_stuck_jobs_is_a_no_op() -> None:
    telegram = _FakeTelegram()
    jobs = _FakeStuckStorage(stuck=[])
    job = _build_job(telegram=telegram, gcs=_FakeGCS(), jobs=jobs, transcriber=_FakeTranscriber())

    await job.run(_ctx())

    assert len(jobs.fetch_calls) == 1
    assert not telegram.sent_messages


async def test_query_window_excludes_in_flight_and_ancient_rows() -> None:
    """In-flight memos are not swept up; months-old strays are not resurrected."""
    jobs = _FakeStuckStorage(stuck=[])
    job = VoiceTranscriptionBackfillJob(
        stale_after_minutes=15,
        max_age_hours=24,
        limit=5,
        job_storage_factory=lambda: jobs,
        telegram_client_factory=_FakeTelegram,
        gcs_storage_factory=_FakeGCS,
        transcriber_factory=_FakeTranscriber,
    )

    await job.run(_ctx())

    call = jobs.fetch_calls[0]
    assert call["limit"] == 5
    now = datetime.now(UTC)
    stale_age_minutes = (now - call["stale_before"]).total_seconds() / 60
    created_age_hours = (now - call["created_after"]).total_seconds() / 3600
    assert 14.9 < stale_age_minutes < 15.1
    assert 23.9 < created_age_hours < 24.1


async def test_without_job_storage_nothing_runs() -> None:
    telegram = _FakeTelegram()
    job = _build_job(telegram=telegram, gcs=_FakeGCS(), jobs=None, transcriber=_FakeTranscriber())

    await job.run(_ctx())

    assert not telegram.sent_messages


async def test_without_transcriber_rows_are_left_alone() -> None:
    """Better a stuck row than "transcription isn't configured" in five chats."""
    telegram = _FakeTelegram()
    jobs = _FakeStuckStorage(stuck=[_stuck(11)])
    job = _build_job(telegram=telegram, gcs=_FakeGCS(), jobs=jobs, transcriber=None)

    await job.run(_ctx())

    assert not jobs.fetch_calls
    assert not telegram.sent_messages
    assert not jobs.failed


async def test_one_failing_row_does_not_abort_the_rest() -> None:
    telegram = _FakeTelegram()
    jobs = _FakeStuckStorage(stuck=[_stuck(11), _stuck(12)])
    transcriber = _FakeTranscriber()
    job = _build_job(telegram=telegram, gcs=_FakeGCS(), jobs=jobs, transcriber=transcriber)

    original_download = telegram.download_file
    calls: list[int] = []

    async def flaky_download(file_path: str) -> bytes:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("kaboom")
        return await original_download(file_path)

    telegram.download_file = flaky_download  # type: ignore[method-assign]

    await job.run(_ctx())

    assert len(calls) == 2
    assert [row["id"] for row in jobs.succeeded] == [12]
