"""Tests for :mod:`something_really_bot.persistence.bigquery`."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from something_really_bot.persistence import (
    EventRecord,
    FileRecord,
    MessageRecord,
    RawUpdateRecord,
    ResponseRecord,
)
from something_really_bot.persistence.bigquery import BigQueryPersistence

DATASET = "test_ds"
PROJECT = "test-proj"
NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _make_persistence() -> tuple[BigQueryPersistence, MagicMock]:
    client = MagicMock()
    client.insert_rows_json.return_value = []  # no errors
    return BigQueryPersistence(project_id=PROJECT, dataset_id=DATASET, client=client), client


def test_record_raw_update_serializes_payload_as_json_string() -> None:
    persistence, client = _make_persistence()

    persistence.record_raw_update(
        RawUpdateRecord(
            update_id=1,
            bot_id="default",
            update_type="private_message",
            raw_payload={"update_id": 1, "message": {"text": "hi"}},
            received_at=NOW,
        )
    )

    client.insert_rows_json.assert_called_once()
    args, _ = client.insert_rows_json.call_args
    table, rows = args
    assert table == f"{PROJECT}.{DATASET}.telegram_updates_raw"
    assert len(rows) == 1
    row = rows[0]
    assert row["update_id"] == 1
    assert row["bot_id"] == "default"
    assert row["update_type"] == "private_message"
    assert row["received_at"] == NOW.isoformat()
    assert json.loads(row["raw_payload"]) == {"update_id": 1, "message": {"text": "hi"}}


def test_record_message_serializes_datetimes_and_keeps_nulls() -> None:
    persistence, client = _make_persistence()

    persistence.record_message(
        MessageRecord(
            update_id=1,
            bot_id="default",
            message_id=99,
            chat_id=42,
            chat_type="private",
            message_type="text",
            received_at=NOW,
            processing_status="received",
            text="hi",
        )
    )

    args, _ = client.insert_rows_json.call_args
    table, rows = args
    assert table == f"{PROJECT}.{DATASET}.telegram_messages"
    row = rows[0]
    assert row["received_at"] == NOW.isoformat()
    assert row["text"] == "hi"
    assert row["processed_at"] is None
    assert row["handler_name"] is None
    assert row["error"] is None


def test_record_file_writes_to_telegram_files() -> None:
    persistence, client = _make_persistence()

    persistence.record_file(
        FileRecord(
            update_id=1,
            bot_id="default",
            chat_id=42,
            message_id=99,
            file_id="AgACAg",
            file_unique_id="uniq",
            file_type="photo",
            download_status="pending",
            received_at=NOW,
            file_size_bytes=12345,
        )
    )

    args, _ = client.insert_rows_json.call_args
    table, rows = args
    assert table == f"{PROJECT}.{DATASET}.telegram_files"
    assert rows[0]["file_type"] == "photo"
    assert rows[0]["file_size_bytes"] == 12345


def test_record_response_writes_to_bot_responses() -> None:
    persistence, client = _make_persistence()

    persistence.record_response(
        ResponseRecord(
            bot_id="default",
            in_response_to_update_id=1,
            chat_id=42,
            message_id=1234,
            response_type="text",
            text="hi",
            sent_at=NOW,
            success=True,
        )
    )

    args, _ = client.insert_rows_json.call_args
    table, rows = args
    assert table == f"{PROJECT}.{DATASET}.bot_responses"
    assert rows[0]["success"] is True
    assert rows[0]["sent_at"] == NOW.isoformat()


def test_record_event_writes_to_processing_events() -> None:
    persistence, client = _make_persistence()

    persistence.record_event(
        EventRecord(
            bot_id="default",
            event="update_unhandled",
            status="ok",
            update_id=1,
            occurred_at=NOW,
        )
    )

    args, _ = client.insert_rows_json.call_args
    table, rows = args
    assert table == f"{PROJECT}.{DATASET}.processing_events"
    assert rows[0]["event"] == "update_unhandled"


def test_insert_failure_does_not_raise() -> None:
    """A BigQuery exception is caught and logged; webhook must keep returning 200."""
    persistence, client = _make_persistence()
    client.insert_rows_json.side_effect = RuntimeError("BQ outage")

    # No assertion needed beyond "doesn't raise" — the persistence interface
    # promises to swallow failures (SPEC §6.9, webhook reliability).
    persistence.record_raw_update(
        RawUpdateRecord(
            update_id=1,
            bot_id="default",
            update_type="private_message",
            raw_payload={},
            received_at=NOW,
        )
    )


def test_partial_failure_is_logged_but_does_not_raise(caplog) -> None:
    persistence, client = _make_persistence()
    client.insert_rows_json.return_value = [{"index": 0, "errors": ["bad row"]}]

    with caplog.at_level("WARNING"):
        persistence.record_message(
            MessageRecord(
                update_id=1,
                bot_id="default",
                message_id=99,
                chat_id=42,
                chat_type="private",
                message_type="text",
                received_at=NOW,
                processing_status="received",
            )
        )

    assert any("bigquery_insert_partial_failure" in r.getMessage() for r in caplog.records)
