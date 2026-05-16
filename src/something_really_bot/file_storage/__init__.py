"""File-storage layer interface.

Handlers depend only on :class:`FileFetcher` — the abstract operation of
"take a Telegram file_id, end up with bytes in GCS, write completion
metadata to BigQuery". The concrete implementation
(:class:`something_really_bot.file_storage.fetcher.InlineFileFetcher`)
runs the download → upload → persist sequence inline via
``asyncio.create_task``; see ``docs/decisions/0002-async-file-processing.md``.

The interface keeps the door open for swapping in a Cloud Tasks /
Pub/Sub fetcher later without touching handlers.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "FileFetchRequest",
    "FileFetcher",
]


@dataclass(frozen=True)
class FileFetchRequest:
    """Everything the fetcher needs to download a file and persist its row."""

    bot_id: str
    update_id: int
    chat_id: int
    message_id: int
    file_id: str
    file_unique_id: str
    file_type: str
    mime_type: str | None = None
    file_size_bytes: int | None = None
    original_filename: str | None = None


@runtime_checkable
class FileFetcher(Protocol):
    """Background download orchestrator."""

    def schedule(self, request: FileFetchRequest) -> None:
        """Fire-and-forget: kick off the download for ``request``.

        Implementations decide *how* (asyncio.create_task, Cloud Tasks
        enqueue, etc.). The method itself returns immediately; failures
        during the actual fetch are surfaced through the persistence layer
        (``telegram_files`` row with ``download_status="failed"``) and
        structured logs, never raised back to the caller.
        """
