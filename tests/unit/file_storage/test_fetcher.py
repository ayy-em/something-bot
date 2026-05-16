"""Tests for :mod:`something_really_bot.file_storage.fetcher`."""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from something_really_bot.file_storage import FileFetchRequest
from something_really_bot.file_storage.fetcher import InlineFileFetcher
from something_really_bot.file_storage.gcs import GCSUploadError
from something_really_bot.telegram.client import TelegramFileError


@dataclass
class _Persistence:
    files: list[Any] = field(default_factory=list)

    def record_raw_update(self, _r: Any) -> None: ...
    def record_message(self, _r: Any) -> None: ...
    def record_response(self, _r: Any) -> None: ...
    def record_event(self, _r: Any) -> None: ...

    def record_file(self, record: Any) -> None:
        self.files.append(record)


def _request() -> FileFetchRequest:
    return FileFetchRequest(
        bot_id="default",
        update_id=1,
        chat_id=42,
        message_id=99,
        file_id="file-id-1",
        file_unique_id="uniq-1",
        file_type="photo",
        mime_type="image/jpeg",
        file_size_bytes=12345,
    )


def _make_fetcher(
    *, telegram: Any | None = None, gcs: Any | None = None, persistence: Any | None = None
) -> tuple[InlineFileFetcher, Any, Any, _Persistence]:
    telegram = telegram or MagicMock()
    gcs = gcs or MagicMock()
    persistence = persistence or _Persistence()
    fetcher = InlineFileFetcher(
        bot_id="default",
        telegram_client=telegram,
        gcs_storage=gcs,
        persistence=persistence,
        scheduler=lambda coro: coro,  # synchronous for tests
    )
    return fetcher, telegram, gcs, persistence


async def test_success_writes_success_row_with_gcs_uri() -> None:
    fetcher, telegram, gcs, persistence = _make_fetcher()
    telegram.get_file_path = AsyncMock(return_value="photos/file.jpg")
    telegram.download_file = AsyncMock(return_value=b"bytes")
    gcs.upload = AsyncMock(return_value="gs://bucket/telegram/default/42/2026-05-16/uniq-1__file")

    await fetcher._fetch_and_record(_request())

    telegram.get_file_path.assert_awaited_once_with("file-id-1")
    telegram.download_file.assert_awaited_once_with("photos/file.jpg")
    gcs.upload.assert_awaited_once()
    assert len(persistence.files) == 1
    row = persistence.files[0]
    assert row.download_status == "success"
    assert row.gcs_uri == "gs://bucket/telegram/default/42/2026-05-16/uniq-1__file"
    assert row.error is None
    assert row.downloaded_at is not None


@pytest.mark.parametrize(
    "failing_step",
    ["get_file_path", "download_file", "gcs_upload"],
)
async def test_failure_writes_failed_row_with_error(failing_step: str) -> None:
    fetcher, telegram, gcs, persistence = _make_fetcher()
    telegram.get_file_path = AsyncMock(return_value="path")
    telegram.download_file = AsyncMock(return_value=b"x")
    gcs.upload = AsyncMock(return_value="gs://x")

    if failing_step == "get_file_path":
        telegram.get_file_path = AsyncMock(side_effect=TelegramFileError("getFile 403"))
    elif failing_step == "download_file":
        telegram.download_file = AsyncMock(side_effect=TelegramFileError("download 404"))
    elif failing_step == "gcs_upload":
        gcs.upload = AsyncMock(side_effect=GCSUploadError("bucket missing"))

    # Must not raise — the fetcher promises to swallow all failures.
    await fetcher._fetch_and_record(_request())

    assert len(persistence.files) == 1
    row = persistence.files[0]
    assert row.download_status == "failed"
    assert row.gcs_uri is None
    assert row.error is not None
    assert row.downloaded_at is not None


def test_schedule_dispatches_via_injected_scheduler() -> None:
    captured: list[Any] = []
    fetcher, _telegram, _gcs, _persistence = _make_fetcher()
    fetcher._scheduler = captured.append

    fetcher.schedule(_request())

    assert len(captured) == 1
    captured[0].close()  # close the unawaited coroutine to silence the warning
