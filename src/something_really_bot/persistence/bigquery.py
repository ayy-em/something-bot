"""BigQuery implementation of :class:`PersistenceService`.

Streaming-insert append (``insert_rows_json``) into the five tables defined
in ``docs/decisions/0001-bigquery-schema.md``. Every write is best-effort:
partial-row errors from BQ are logged, never raised. The webhook must keep
returning 200 to Telegram regardless of persistence health (SPEC §6.9).
"""

import json
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from typing import Any

from google.cloud import bigquery

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger
from something_really_bot.persistence.records import (
    EventRecord,
    FileRecord,
    MessageRecord,
    RawUpdateRecord,
    ResponseRecord,
)

_logger = get_logger(__name__)


class BigQueryPersistence:
    """Write rows to the ``something_bot`` dataset."""

    TABLE_RAW = "telegram_updates_raw"
    TABLE_MESSAGES = "telegram_messages"
    TABLE_FILES = "telegram_files"
    TABLE_RESPONSES = "bot_responses"
    TABLE_EVENTS = "processing_events"

    def __init__(
        self,
        *,
        project_id: str,
        dataset_id: str,
        client: bigquery.Client | None = None,
    ) -> None:
        self._project_id = project_id
        self._dataset_id = dataset_id
        self._client = client or bigquery.Client(project=project_id)

    # --- Public API ------------------------------------------------------ #

    def record_raw_update(self, record: RawUpdateRecord) -> None:
        row = _to_row(record)
        row["raw_payload"] = json.dumps(record.raw_payload, default=str)
        self._insert(self.TABLE_RAW, row)

    def record_message(self, record: MessageRecord) -> None:
        self._insert(self.TABLE_MESSAGES, _to_row(record))

    def record_file(self, record: FileRecord) -> None:
        self._insert(self.TABLE_FILES, _to_row(record))

    def record_response(self, record: ResponseRecord) -> None:
        self._insert(self.TABLE_RESPONSES, _to_row(record))

    def record_event(self, record: EventRecord) -> None:
        self._insert(self.TABLE_EVENTS, _to_row(record))

    # --- Internal -------------------------------------------------------- #

    def _table_ref(self, table_id: str) -> str:
        return f"{self._project_id}.{self._dataset_id}.{table_id}"

    def _insert(self, table_id: str, row: dict[str, Any]) -> None:
        try:
            errors = self._client.insert_rows_json(self._table_ref(table_id), [row])
        except Exception as exc:  # noqa: BLE001 — persistence must never crash the webhook
            _logger.exception(
                "bigquery_insert_raised",
                extra={"table": table_id, "exception_type": type(exc).__name__},
            )
            return

        if errors:
            _logger.warning(
                "bigquery_insert_partial_failure",
                extra={"table": table_id, "errors": errors},
            )


def _to_row(record: Any) -> dict[str, Any]:
    """Serialize a record dataclass into a BigQuery-friendly dict.

    Datetimes become ISO-8601 strings; None values are kept (BigQuery treats
    them as NULL). ``raw_payload`` is handled specially in
    :meth:`BigQueryPersistence.record_raw_update`.
    """
    row: dict[str, Any] = {}
    for key, value in asdict(record).items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
        else:
            row[key] = value
    return row


@lru_cache(maxsize=1)
def get_persistence_service() -> BigQueryPersistence:
    """Return the process-wide :class:`BigQueryPersistence`."""
    settings = get_settings()
    return BigQueryPersistence(
        project_id=settings.gcp_project_id,
        dataset_id=settings.bigquery_dataset,
    )
