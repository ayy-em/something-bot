"""Integration tests covering /webhook → parser → dispatcher → send → persist → 200."""

from typing import Any

from fastapi.testclient import TestClient

from something_really_bot.main import app, dispatcher
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import ParsedUpdate
from something_really_bot.telegram.security import TELEGRAM_SECRET_HEADER

SECRET = "test-secret"

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "content-type": "application/json",
        TELEGRAM_SECRET_HEADER: SECRET,
    }


def _payload(text: str = "/ping") -> dict[str, Any]:
    return {
        "update_id": 5000,
        "message": {
            "message_id": 1,
            "date": 1715850000,
            "chat": {"id": 135499785, "type": "private"},
            "from": {"id": 135499785, "is_bot": False, "first_name": "T"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}]
            if text.startswith("/")
            else [],
        },
    }


def _photo_payload() -> dict[str, Any]:
    return {
        "update_id": 5100,
        "message": {
            "message_id": 7,
            "date": 1715850000,
            "chat": {"id": 999, "type": "private"},
            "from": {"id": 999, "is_bot": False, "first_name": "P"},
            "photo": [
                {
                    "file_id": "small",
                    "file_unique_id": "s",
                    "width": 90,
                    "height": 90,
                    "file_size": 100,
                },
                {
                    "file_id": "big",
                    "file_unique_id": "b",
                    "width": 1280,
                    "height": 1280,
                    "file_size": 9999,
                },
            ],
            "caption": "look",
        },
    }


def test_ping_payload_dispatches_handler_sends_reply_and_persists(stub_external_services) -> None:
    """/ping → PingHandler matches → reply sent + raw/message/response rows persisted."""
    tg, persistence, _fetcher, _openai = stub_external_services

    response = client.post("/webhook", json=_payload("/ping"), headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Reply was sent
    assert tg.sends == [{"chat_id": 135499785, "text": "pong"}]
    # Inbound persistence
    assert len(persistence.raw_updates) == 1
    assert persistence.raw_updates[0].update_id == 5000
    assert persistence.raw_updates[0].update_type == "private_message"
    assert len(persistence.messages) == 1
    assert persistence.messages[0].message_type == "command"
    assert persistence.messages[0].command == "/ping"
    # Outbound persistence
    assert len(persistence.responses) == 1
    assert persistence.responses[0].success is True
    assert persistence.responses[0].text == "pong"
    assert persistence.responses[0].message_id == 1
    # No file row, no error event, no unhandled event
    assert persistence.files == []
    assert persistence.events == []


def test_unhandled_payload_persists_event_no_send(stub_external_services) -> None:
    """Text that no handler matches → raw + message rows + update_unhandled event, no send."""
    tg, persistence, _fetcher, _openai = stub_external_services

    response = client.post("/webhook", json=_payload("totally random text"), headers=_headers())

    assert response.status_code == 200
    assert tg.sends == []  # no reply
    assert len(persistence.raw_updates) == 1
    assert len(persistence.messages) == 1
    assert persistence.responses == []
    events = [e.event for e in persistence.events]
    assert "update_unhandled" in events


def test_photo_payload_persists_file_row_and_schedules_fetch(stub_external_services) -> None:
    _tg, persistence, fetcher, _openai = stub_external_services

    response = client.post("/webhook", json=_photo_payload(), headers=_headers())

    assert response.status_code == 200
    # Orchestrator writes the pending file row at intake time
    assert len(persistence.files) == 1
    file_row = persistence.files[0]
    assert file_row.file_type == "photo"
    assert file_row.file_id == "big"  # largest photo size wins
    assert file_row.file_size_bytes == 9999
    assert file_row.download_status == "pending"
    # Message also recorded with photo type
    assert persistence.messages[0].message_type == "photo"
    assert persistence.messages[0].text == "look"  # caption stored in text column
    # File-storage handler scheduled the background fetch
    assert len(fetcher.scheduled) == 1
    assert fetcher.scheduled[0].file_id == "big"
    assert fetcher.scheduled[0].file_type == "photo"


def test_malformed_payload_records_event_only(stub_external_services) -> None:
    """Missing update_id → parser raises → malformed_update event, nothing else."""
    _tg, persistence, _fetcher, _openai = stub_external_services

    response = client.post(
        "/webhook",
        json={"message": {"text": "broken"}},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert persistence.raw_updates == []
    assert persistence.messages == []
    assert persistence.responses == []
    assert [e.event for e in persistence.events] == ["malformed_update"]


def test_non_json_body_still_returns_200(stub_external_services) -> None:
    """Garbage body → safe_json returns {} → parser raises → 200."""
    response = client.post("/webhook", content=b"\x00\x01not json", headers=_headers())

    assert response.status_code == 200


def test_handler_exception_records_error_event_and_returns_200(
    monkeypatch, stub_external_services
) -> None:
    """A handler that raises is caught; webhook returns 200 and records the error event."""
    _tg, persistence, _fetcher, _openai = stub_external_services

    class _Boom:
        name = "boom"

        def matches(self, _u: ParsedUpdate, _ctx: BotContext) -> bool:
            return True

        async def handle(self, _u: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
            raise RuntimeError("crash")

    monkeypatch.setattr(dispatcher, "_handlers", [_Boom(), *dispatcher._handlers])

    response = client.post("/webhook", json=_payload("/ping"), headers=_headers())

    assert response.status_code == 200
    error_events = [e for e in persistence.events if e.event == "handler_errored"]
    assert len(error_events) == 1
    assert error_events[0].handler_name == "boom"


def test_persistence_failure_does_not_break_webhook(monkeypatch) -> None:
    """If persistence raises, webhook still returns 200."""
    from something_really_bot import main as app_main

    class _Boom:
        def record_raw_update(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

        def record_message(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

        def record_file(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

        def record_response(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

        def record_event(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

    monkeypatch.setattr(app_main, "get_persistence_service", lambda: _Boom())

    response = client.post("/webhook", json=_payload("/ping"), headers=_headers())

    assert response.status_code == 200
