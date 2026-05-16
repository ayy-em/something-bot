"""Persistence layer interface.

Handlers and the webhook route depend only on :class:`PersistenceService`
(this module) — never on the BigQuery client directly. This keeps tests
trivially mockable and leaves room for swapping backends later without
touching call sites.

Concrete implementation: :class:`something_really_bot.persistence.bigquery.BigQueryPersistence`.
Records shape: :mod:`something_really_bot.persistence.records`.
Schema source of truth: ``docs/decisions/0001-bigquery-schema.md``.
"""

from typing import Protocol, runtime_checkable

from something_really_bot.persistence.records import (
    EventRecord,
    FileRecord,
    MessageRecord,
    RawUpdateRecord,
    ResponseRecord,
)

__all__ = [
    "EventRecord",
    "FileRecord",
    "MessageRecord",
    "PersistenceService",
    "RawUpdateRecord",
    "ResponseRecord",
]


@runtime_checkable
class PersistenceService(Protocol):
    """Append-only sink for everything the webhook observes."""

    def record_raw_update(self, record: RawUpdateRecord) -> None:
        """Write a row to ``telegram_updates_raw``. Must not raise."""

    def record_message(self, record: MessageRecord) -> None:
        """Write a row to ``telegram_messages``. Must not raise."""

    def record_file(self, record: FileRecord) -> None:
        """Write a row to ``telegram_files``. Must not raise."""

    def record_response(self, record: ResponseRecord) -> None:
        """Write a row to ``bot_responses``. Must not raise."""

    def record_event(self, record: EventRecord) -> None:
        """Write a row to ``processing_events``. Must not raise."""
