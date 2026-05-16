"""Integration tests covering /webhook → parser → dispatcher → 200."""

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


def test_ping_payload_dispatches_to_example_handler() -> None:
    """Real payload → parse_update → PingHandler matches → 200."""
    response = client.post("/webhook", json=_payload("/ping"), headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unhandled_payload_still_returns_200() -> None:
    """A text the example handler doesn't match still acks 200."""
    response = client.post("/webhook", json=_payload("hello"), headers=_headers())

    assert response.status_code == 200


def test_malformed_payload_still_returns_200() -> None:
    """Missing update_id (parser raises) → caught → 200."""
    response = client.post(
        "/webhook",
        json={"message": {"text": "broken"}},
        headers=_headers(),
    )

    assert response.status_code == 200


def test_non_json_body_still_returns_200() -> None:
    """Garbage body → safe_json returns {} → parser raises → 200."""
    response = client.post(
        "/webhook",
        content=b"\x00\x01not json",
        headers=_headers(),
    )

    assert response.status_code == 200


def test_handler_exception_still_returns_200(monkeypatch) -> None:
    """A handler that raises is caught inside the dispatcher; webhook returns 200."""

    class _Boom:
        name = "boom"

        def matches(self, _u: ParsedUpdate, _ctx: BotContext) -> bool:
            return True

        async def handle(self, _u: ParsedUpdate, _ctx: BotContext) -> HandlerResult:
            raise RuntimeError("crash")

    # Prepend the boom handler so it wins the first-match race.
    monkeypatch.setattr(dispatcher, "_handlers", [_Boom(), *dispatcher._handlers])

    response = client.post("/webhook", json=_payload("/ping"), headers=_headers())

    assert response.status_code == 200
