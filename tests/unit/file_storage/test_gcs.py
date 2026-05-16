"""Tests for :mod:`something_really_bot.file_storage.gcs`."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from something_really_bot.file_storage.gcs import GCSStorage, GCSUploadError


def _make_storage() -> tuple[GCSStorage, MagicMock]:
    client = MagicMock()
    return GCSStorage(bucket_name="test-bucket", client=client), client


def test_object_key_format() -> None:
    key = GCSStorage.object_key(
        bot_id="default",
        chat_id=42,
        received_at=datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC),
        file_unique_id="abc123",
        original_filename="vacation.pdf",
    )

    assert key == "telegram/default/42/2026-05-16/abc123__vacation.pdf"


def test_object_key_defaults_filename_when_missing() -> None:
    key = GCSStorage.object_key(
        bot_id="default",
        chat_id=42,
        received_at=datetime(2026, 5, 16, tzinfo=UTC),
        file_unique_id="abc123",
        original_filename=None,
    )

    assert key.endswith("/abc123__file")


def test_object_key_sanitizes_path_separators_in_filename() -> None:
    key = GCSStorage.object_key(
        bot_id="default",
        chat_id=42,
        received_at=datetime(2026, 5, 16, tzinfo=UTC),
        file_unique_id="abc123",
        original_filename="../sneaky/path.pdf",
    )

    assert "/" not in key.split("__", 1)[1]
    assert key.endswith("__.._sneaky_path.pdf")


async def test_upload_calls_bucket_blob_upload_from_string() -> None:
    storage, client = _make_storage()
    blob = MagicMock()
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client.bucket.return_value = bucket

    uri = await storage.upload(
        object_key="telegram/default/42/2026-05-16/u__file.jpg",
        data=b"hello",
        content_type="image/jpeg",
    )

    client.bucket.assert_called_once_with("test-bucket")
    bucket.blob.assert_called_once_with("telegram/default/42/2026-05-16/u__file.jpg")
    blob.upload_from_string.assert_called_once_with(b"hello", content_type="image/jpeg")
    assert uri == "gs://test-bucket/telegram/default/42/2026-05-16/u__file.jpg"


async def test_upload_translates_client_exception_into_gcs_upload_error() -> None:
    storage, client = _make_storage()
    bucket = MagicMock()
    blob = MagicMock()
    bucket.blob.return_value = blob
    blob.upload_from_string.side_effect = RuntimeError("network unreachable")
    client.bucket.return_value = bucket

    with pytest.raises(GCSUploadError):
        await storage.upload(
            object_key="some/key",
            data=b"x",
            content_type=None,
        )
