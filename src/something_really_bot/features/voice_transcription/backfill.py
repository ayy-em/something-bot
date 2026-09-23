"""Re-run voice memos whose background task died mid-flight.

A transcription lives in a fire-and-forget asyncio task. If that task
dies without running its own failure path, the row in
``voice_transcription_jobs`` is stranded in a non-terminal status and
the user never hears back — exactly what happened on 2026-09-23, when
Telegram's ``getFile`` stalled and the raw ``httpx.ReadTimeout`` escaped
the client (fixed in the same change that added this job).

The job finds those rows and runs the pipeline again against the same
row: download → GCS → transcribe → reply. Safe to re-run — a job that
succeeds this time reaches a terminal status and drops out of the next
query.

Unscheduled, like ``ensure-webhook``: invoke by hand with
``GET /jobs/voice-transcription-backfill?token=…``.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from something_really_bot.features.voice_transcription.handler import (
    _build_job_storage_or_none,
    rerun_stuck_job,
)
from something_really_bot.features.voice_transcription.storage import VoiceJobStorage
from something_really_bot.features.voice_transcription.transcriber import (
    VoiceTranscriber,
    get_voice_transcriber,
)
from something_really_bot.file_storage.gcs import GCSStorage, get_gcs_storage
from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.client import TelegramClient, get_telegram_client

_logger = get_logger(__name__)

# A memo still being transcribed is not stuck. The longest legitimate
# run is bounded by the pipeline's own timeouts (60s transcribe + 25s
# analyze + 30s parody + 45s speech), so 15 minutes is far past any
# healthy job while still catching a fresh outage.
DEFAULT_STALE_AFTER_MINUTES = 15
# Telegram file_ids do not live forever and a chat that has moved on does
# not want a transcript of last month's memo. Anything older than this is
# left for the DB reconcile to close out, not re-run.
DEFAULT_MAX_AGE_HOURS = 24
# Each re-run costs a download, a transcription and up to two more
# OpenAI calls, and the whole job runs inside one HTTP request with a
# 300s Cloud Run timeout. Ten is roughly what fits.
DEFAULT_LIMIT = 10


class VoiceTranscriptionBackfillJob:
    """Job handler: re-run stranded voice transcription rows."""

    name = "voice-transcription-backfill"

    def __init__(
        self,
        *,
        stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
        max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
        limit: int = DEFAULT_LIMIT,
        job_storage_factory: Callable[[], VoiceJobStorage | None] = _build_job_storage_or_none,
        telegram_client_factory: Callable[[], TelegramClient] = get_telegram_client,
        gcs_storage_factory: Callable[[], GCSStorage] = get_gcs_storage,
        transcriber_factory: Callable[[], VoiceTranscriber | None] = get_voice_transcriber,
    ) -> None:
        self._stale_after_minutes = stale_after_minutes
        self._max_age_hours = max_age_hours
        self._limit = limit
        self._jobs_factory = job_storage_factory
        self._tg_factory = telegram_client_factory
        self._gcs_factory = gcs_storage_factory
        self._transcriber_factory = transcriber_factory

    async def run(self, ctx: BotContext) -> None:
        job_storage = self._jobs_factory()
        if job_storage is None:
            _logger.warning("voice_backfill_no_job_storage")
            return

        transcriber = self._transcriber_factory()
        if transcriber is None:
            # Re-running now would only replace silence with "voice
            # transcription isn't configured" in five chats. Leave the
            # rows alone; they will still be here once the key is back.
            _logger.warning("voice_backfill_no_transcriber")
            return

        now = datetime.now(UTC)
        stale_before = now - timedelta(minutes=self._stale_after_minutes)
        created_after = now - timedelta(hours=self._max_age_hours)
        stuck = await job_storage.fetch_stuck(
            stale_before=stale_before,
            created_after=created_after,
            limit=self._limit,
        )
        _logger.info(
            "voice_backfill_found",
            extra={
                "count": len(stuck),
                "stale_before": stale_before.isoformat(),
                "created_after": created_after.isoformat(),
            },
        )
        if not stuck:
            return

        telegram_client = self._tg_factory()
        gcs_storage = self._gcs_factory()
        record_event = ctx.persistence.record_event if ctx.persistence is not None else None

        for job in stuck:
            _logger.info(
                "voice_backfill_rerun",
                extra={
                    "job_id": job.job_id,
                    "chat_id": job.row.chat_id,
                    "message_id": job.row.message_id,
                    "previous_status": job.status,
                    "duration_seconds": job.row.duration_seconds,
                },
            )
            # rerun_stuck_job goes through _run_background, which never
            # raises — one poisonous row cannot abort the rest.
            await rerun_stuck_job(
                job,
                telegram_client=telegram_client,
                gcs_storage=gcs_storage,
                transcriber=transcriber,
                job_storage=job_storage,
                persistence_record_event=record_event,
            )

        _logger.info("voice_backfill_done", extra={"count": len(stuck)})
