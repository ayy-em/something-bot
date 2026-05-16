"""Inline-async file fetcher (decision 0002).

Orchestrates the Telegram → GCS download for one file:

1. ``getFile`` against the Telegram Bot API to resolve the file path.
2. Download the file body from Telegram's CDN.
3. Upload the bytes to GCS under the canonical object key.
4. Write a ``telegram_files`` completion row to BigQuery with
   ``download_status="success"`` and the ``gs://...`` URI, *or* a
   ``"failed"`` row carrying the exception message.

The fetch runs in an ``asyncio.create_task`` so the caller's request can
return 200 to Telegram first. Cloud Run is configured with
``cpu_idle=false`` (decision 0002) so the task keeps running after the
response is flushed.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from something_really_bot.file_storage import FileFetcher, FileFetchRequest
from something_really_bot.file_storage.gcs import GCSStorage, get_gcs_storage
from something_really_bot.logging import get_logger
from something_really_bot.persistence import FileRecord, PersistenceService
from something_really_bot.persistence.bigquery import get_persistence_service
from something_really_bot.telegram.client import TelegramClient, get_telegram_client

_logger = get_logger(__name__)


# Type alias for the scheduler — injectable so tests run the coroutine
# synchronously instead of spawning real asyncio tasks.
Scheduler = Callable[[Coroutine[Any, Any, None]], Any]


class InlineFileFetcher:
    """Default :class:`FileFetcher` impl: in-process asyncio task."""

    def __init__(
        self,
        *,
        bot_id: str,
        telegram_client: TelegramClient,
        gcs_storage: GCSStorage,
        persistence: PersistenceService,
        scheduler: Scheduler = asyncio.create_task,
    ) -> None:
        self._bot_id = bot_id
        self._telegram = telegram_client
        self._gcs = gcs_storage
        self._persistence = persistence
        self._scheduler = scheduler

    def schedule(self, request: FileFetchRequest) -> None:
        self._scheduler(self._fetch_and_record(request))

    async def _fetch_and_record(self, request: FileFetchRequest) -> None:
        received_at = datetime.now(UTC)
        try:
            file_path = await self._telegram.get_file_path(request.file_id)
            data = await self._telegram.download_file(file_path)
            object_key = GCSStorage.object_key(
                bot_id=request.bot_id,
                chat_id=request.chat_id,
                received_at=received_at,
                file_unique_id=request.file_unique_id,
                original_filename=request.original_filename,
            )
            gcs_uri = await self._gcs.upload(
                object_key=object_key,
                data=data,
                content_type=request.mime_type,
            )
        except Exception as exc:  # noqa: BLE001 — must never escape the task
            _logger.warning(
                "file_fetch_failed",
                extra={
                    "update_id": request.update_id,
                    "file_unique_id": request.file_unique_id,
                    "exception_type": type(exc).__name__,
                },
            )
            self._persistence.record_file(
                self._build_record(
                    request,
                    received_at=received_at,
                    downloaded_at=datetime.now(UTC),
                    gcs_uri=None,
                    download_status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return

        self._persistence.record_file(
            self._build_record(
                request,
                received_at=received_at,
                downloaded_at=datetime.now(UTC),
                gcs_uri=gcs_uri,
                download_status="success",
                error=None,
            )
        )

    @staticmethod
    def _build_record(
        request: FileFetchRequest,
        *,
        received_at: datetime,
        downloaded_at: datetime,
        gcs_uri: str | None,
        download_status: str,
        error: str | None,
    ) -> FileRecord:
        return FileRecord(
            update_id=request.update_id,
            bot_id=request.bot_id,
            chat_id=request.chat_id,
            message_id=request.message_id,
            file_id=request.file_id,
            file_unique_id=request.file_unique_id,
            file_type=request.file_type,
            mime_type=request.mime_type,
            file_size_bytes=request.file_size_bytes,
            original_filename=request.original_filename,
            gcs_uri=gcs_uri,
            download_status=download_status,
            received_at=received_at,
            downloaded_at=downloaded_at,
            error=error,
        )


@lru_cache(maxsize=1)
def get_file_fetcher() -> FileFetcher:
    """Return the process-wide :class:`InlineFileFetcher`."""
    return InlineFileFetcher(
        bot_id="default",
        telegram_client=get_telegram_client(),
        gcs_storage=get_gcs_storage(),
        persistence=get_persistence_service(),
    )
