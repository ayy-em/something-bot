"""Shared test configuration.

Sets the minimum required environment variables so that ``Settings`` can be
built when the FastAPI app or its dependencies are imported in tests.

Also installs no-op stubs for ``get_telegram_client`` and
``get_persistence_service`` so webhook integration tests never make real
HTTP / BigQuery calls. Tests that want to inspect those interactions can
import :class:`RecordingTelegramClient` / :class:`RecordingPersistence`
and patch the factories on a per-test basis.
"""

import os
from typing import Any

import pytest

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-bot-token")


class RecordingTelegramClient:
    """Stub :class:`TelegramClient` that records calls in-memory."""

    def __init__(self, message_id: int = 1) -> None:
        self.sends: list[dict[str, Any]] = []
        self._message_id = message_id

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        self.sends.append({"chat_id": chat_id, "text": text})
        return {"message_id": self._message_id}


class RecordingPersistence:
    """Stub :class:`PersistenceService` that captures every record() call."""

    def __init__(self) -> None:
        self.raw_updates: list[Any] = []
        self.messages: list[Any] = []
        self.files: list[Any] = []
        self.responses: list[Any] = []
        self.events: list[Any] = []

    def record_raw_update(self, record: Any) -> None:
        self.raw_updates.append(record)

    def record_message(self, record: Any) -> None:
        self.messages.append(record)

    def record_file(self, record: Any) -> None:
        self.files.append(record)

    def record_response(self, record: Any) -> None:
        self.responses.append(record)

    def record_event(self, record: Any) -> None:
        self.events.append(record)


@pytest.fixture(autouse=True)
def stub_external_services(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RecordingTelegramClient, RecordingPersistence]:
    """Replace the webhook's Telegram + persistence factories with stubs.

    Returns the stubs so individual tests can assert on captured calls.
    """
    from something_really_bot import main as app_main
    from something_really_bot.persistence import bigquery as bq_persistence
    from something_really_bot.telegram import client as tg_client

    tg = RecordingTelegramClient()
    persistence = RecordingPersistence()

    tg_client.get_telegram_client.cache_clear()
    bq_persistence.get_persistence_service.cache_clear()
    monkeypatch.setattr(app_main, "get_telegram_client", lambda: tg)
    monkeypatch.setattr(app_main, "get_persistence_service", lambda: persistence)

    return tg, persistence
