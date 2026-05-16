"""Record dataclasses mirroring the BigQuery schema (RFC #17 / decision 0001).

One class per table. Field order, names, and nullability match the schema
file in ``docs/decisions/0001-bigquery-schema.md``. ``datetime`` fields are
assumed UTC; the persistence implementation is responsible for serializing
them.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RawUpdateRecord:
    """One row for ``telegram_updates_raw``."""

    update_id: int
    bot_id: str
    update_type: str
    raw_payload: dict[str, Any]
    received_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    """One row for ``telegram_messages``."""

    update_id: int
    bot_id: str
    message_id: int
    chat_id: int
    chat_type: str
    message_type: str
    received_at: datetime
    processing_status: str
    chat_title: str | None = None
    user_id: int | None = None
    username: str | None = None
    command: str | None = None
    text: str | None = None
    processed_at: datetime | None = None
    handler_name: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class FileRecord:
    """One row for ``telegram_files``."""

    update_id: int
    bot_id: str
    chat_id: int
    message_id: int
    file_id: str
    file_unique_id: str
    file_type: str
    download_status: str
    received_at: datetime
    mime_type: str | None = None
    file_size_bytes: int | None = None
    original_filename: str | None = None
    gcs_uri: str | None = None
    downloaded_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class ResponseRecord:
    """One row for ``bot_responses``."""

    bot_id: str
    chat_id: int
    response_type: str
    sent_at: datetime
    success: bool
    in_response_to_update_id: int | None = None
    message_id: int | None = None
    text: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class EventRecord:
    """One row for ``processing_events``."""

    bot_id: str
    event: str
    status: str
    occurred_at: datetime
    update_id: int | None = None
    handler_name: str | None = None
    details: str | None = None
