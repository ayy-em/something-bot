"""Smoke test ensuring the FastAPI app boots and the health probe responds."""

from fastapi.testclient import TestClient

from something_really_bot.main import app


def test_healthz_returns_ok() -> None:
    """``GET /healthz`` should respond 200 with the canonical payload."""
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
