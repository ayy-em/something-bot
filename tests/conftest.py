"""Shared test configuration.

Sets the minimum required environment variables so that ``Settings`` can be
built when the FastAPI app or its dependencies are imported in tests.

Also installs no-op stubs for ``get_telegram_client``,
``get_persistence_service``, and ``get_file_fetcher`` so webhook
integration tests never make real HTTP / BigQuery / GCS calls. Tests that
want to inspect those interactions request the
:func:`stub_external_services` fixture by name to receive the recording
stubs.
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


class RecordingFileFetcher:
    """Stub :class:`FileFetcher` that records ``schedule()`` calls and never
    actually fetches anything — deterministic, no race against asyncio tasks."""

    def __init__(self) -> None:
        self.scheduled: list[Any] = []

    def schedule(self, request: Any) -> None:
        self.scheduled.append(request)


class RecordingOpenAIClient:
    """Stub :class:`OpenAIClient` with a configurable canned response."""

    def __init__(self, reply: str = "Mocked OpenAI response.") -> None:
        self.calls: list[str] = []
        self._reply = reply

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._reply


class RecordingJobHistoryLogger:
    """Stub :class:`JobHistoryLogger` that captures every ``record()`` call."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def record(self, row: Any) -> None:
        self.rows.append(row)


@pytest.fixture(autouse=True)
def stub_external_services(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    RecordingTelegramClient,
    RecordingPersistence,
    RecordingFileFetcher,
    RecordingOpenAIClient,
]:
    """Replace the webhook's external-service factories with stubs.

    Returns the stubs so individual tests can assert on captured calls.
    """
    from something_really_bot import main as app_main
    from something_really_bot.file_storage import fetcher as file_fetcher_module
    from something_really_bot.persistence import bigquery as bq_persistence
    from something_really_bot.services import job_history as job_history_module
    from something_really_bot.services import openai_client as openai_module
    from something_really_bot.telegram import client as tg_client

    tg = RecordingTelegramClient()
    persistence = RecordingPersistence()
    fetcher = RecordingFileFetcher()
    openai = RecordingOpenAIClient()

    tg_client.get_telegram_client.cache_clear()
    bq_persistence.get_persistence_service.cache_clear()
    file_fetcher_module.get_file_fetcher.cache_clear()
    openai_module.get_openai_client.cache_clear()
    job_history_module.get_job_history_logger.cache_clear()
    monkeypatch.setattr(app_main, "get_telegram_client", lambda: tg)
    monkeypatch.setattr(app_main, "get_persistence_service", lambda: persistence)
    monkeypatch.setattr(app_main, "get_file_fetcher", lambda: fetcher)
    monkeypatch.setattr(app_main, "get_openai_client", lambda: openai)
    monkeypatch.setattr(app_main, "get_job_history_logger", lambda: None)

    return tg, persistence, fetcher, openai


@pytest.fixture
def stub_job_history(monkeypatch: pytest.MonkeyPatch) -> RecordingJobHistoryLogger:
    """Install a recording :class:`JobHistoryLogger` for the webhook + /jobs route."""
    from something_really_bot import main as app_main

    logger = RecordingJobHistoryLogger()
    monkeypatch.setattr(app_main, "get_job_history_logger", lambda: logger)
    return logger
