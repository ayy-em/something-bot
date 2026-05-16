"""Smoke tests for the FastAPI app shell and Telegram webhook auth."""

from fastapi.testclient import TestClient

from something_really_bot.main import app
from something_really_bot.telegram.security import TELEGRAM_SECRET_HEADER

WEBHOOK_SECRET = "test-secret"

client = TestClient(app)


def test_health_returns_healthy() -> None:
    """``GET /health`` responds 200 with the canonical liveness payload."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_webhook_returns_ok_when_secret_header_matches() -> None:
    """``POST /webhook`` with the correct secret header returns 200."""
    response = client.post(
        "/webhook",
        json={"update_id": 42, "message": {"text": "hello"}},
        headers={TELEGRAM_SECRET_HEADER: WEBHOOK_SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_accepts_empty_body_with_correct_secret() -> None:
    """An empty JSON body is still accepted when the header matches."""
    response = client.post(
        "/webhook",
        content=b"",
        headers={
            "content-type": "application/json",
            TELEGRAM_SECRET_HEADER: WEBHOOK_SECRET,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_rejects_missing_secret_header_with_401() -> None:
    """A request without the secret header is rejected as Unauthorized."""
    response = client.post("/webhook", json={})

    assert response.status_code == 401


def test_webhook_rejects_wrong_secret_header_with_403() -> None:
    """A request with the wrong secret value is rejected as Forbidden."""
    response = client.post(
        "/webhook",
        json={},
        headers={TELEGRAM_SECRET_HEADER: "not-the-secret"},
    )

    assert response.status_code == 403


def test_webhook_rejects_get_with_405() -> None:
    """``GET /webhook`` is not allowed."""
    response = client.get("/webhook")

    assert response.status_code == 405
