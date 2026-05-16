"""Smoke tests for the FastAPI app shell.

Covers the two endpoints that exist before any business logic lands:
``GET /healthz`` (Cloud Run liveness probe) and ``POST /webhook`` (hello-world
Telegram target).
"""

from fastapi.testclient import TestClient

from something_really_bot.main import app

client = TestClient(app)


def test_healthz_returns_healthy() -> None:
    """``GET /healthz`` responds 200 with the canonical liveness payload."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_webhook_post_returns_ok_for_arbitrary_json() -> None:
    """``POST /webhook`` returns 200 + ``{"status": "ok"}`` for any JSON body."""
    payload = {"update_id": 42, "message": {"text": "hello"}}

    response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_post_returns_ok_for_empty_body() -> None:
    """``POST /webhook`` still returns 200 + ok when the body is empty."""
    response = client.post("/webhook", content=b"", headers={"content-type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_rejects_get_with_405() -> None:
    """``GET /webhook`` is not allowed; FastAPI returns 405 Method Not Allowed."""
    response = client.get("/webhook")

    assert response.status_code == 405
