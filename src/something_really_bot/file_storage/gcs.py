"""Thin async wrapper around the synchronous ``google-cloud-storage`` client.

We use the sync client from inside ``run_in_executor`` because gcloud's
official async story is still patchy and the per-file upload is small
enough that the executor hop costs nothing meaningful.

Object keys follow the convention defined in #20:
``telegram/{bot_id}/{chat_id}/{YYYY-MM-DD}/{file_unique_id}__{filename}``.
"""

import asyncio
from datetime import datetime
from functools import lru_cache
from typing import Any

from google.cloud import storage

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


class GCSUploadError(Exception):
    """Raised when uploading a file to GCS fails."""


class GCSStorage:
    """Upload Telegram files to the configured GCS bucket."""

    def __init__(self, bucket_name: str, client: Any | None = None) -> None:
        self._bucket_name = bucket_name
        self._client = client or storage.Client()

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @staticmethod
    def object_key(
        *,
        bot_id: str,
        chat_id: int,
        received_at: datetime,
        file_unique_id: str,
        original_filename: str | None,
    ) -> str:
        """Build the canonical object key for a file."""
        date_prefix = received_at.strftime("%Y-%m-%d")
        suffix = original_filename or "file"
        # Strip anything path-like in the filename Telegram surfaces.
        safe_suffix = suffix.replace("/", "_").replace("\\", "_")
        return f"telegram/{bot_id}/{chat_id}/{date_prefix}/{file_unique_id}__{safe_suffix}"

    async def upload(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        """Upload bytes to GCS and return the ``gs://`` URI.

        Raises:
            GCSUploadError: any underlying client exception.
        """
        try:
            return await asyncio.to_thread(self._upload_sync, object_key, data, content_type)
        except Exception as exc:  # noqa: BLE001 — translate into our error type
            _logger.warning(
                "gcs_upload_failed",
                extra={
                    "bucket": self._bucket_name,
                    "object_key": object_key,
                    "exception_type": type(exc).__name__,
                },
            )
            raise GCSUploadError(str(exc)) from exc

    def _upload_sync(self, object_key: str, data: bytes, content_type: str | None) -> str:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(object_key)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket_name}/{object_key}"


@lru_cache(maxsize=1)
def get_gcs_storage() -> GCSStorage:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise RuntimeError("GCS_BUCKET is not configured; cannot build GCSStorage.")
    return GCSStorage(bucket_name=settings.gcs_bucket)
