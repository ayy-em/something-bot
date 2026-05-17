"""GCS-backed context loader for the OpenAI fallback handler (#26).

A small set of ``.md`` files in a dedicated GCS bucket is fetched on
each call, concatenated into ``{"role": "system", ...}`` messages, and
prepended after the canonical ``SYSTEM_PROMPT`` but before the user's
prompt. Sync to/from the bucket happens via ``scripts/context-sync.sh``
— the markdown is never committed.

Lookups are cached with a small TTL so we're not hitting GCS once per
request. A GCS failure is logged and yields an empty context — the
caller still gets a useful reply, just without the context layer.

Token budget: the combined context is capped at
:data:`MAX_CONTEXT_BYTES`. If it would exceed the cap, files are
truncated in iteration order until the cap is met. The truncated state
is logged so it's visible in Cloud Logging.
"""

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 60.0
MAX_CONTEXT_BYTES = 32 * 1024  # 32 KiB — issue #26 documented budget
OBJECT_PREFIX = "context/"


@dataclass(frozen=True)
class _CachedContext:
    expires_at: float
    messages: tuple[str, ...]


class OpenAIContextLoader:
    """Async loader for shared OpenAI context files in GCS."""

    def __init__(
        self,
        bucket_name: str,
        *,
        prefix: str = OBJECT_PREFIX,
        client: object | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_bytes: int = MAX_CONTEXT_BYTES,
        clock: object | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._clock = clock or time.monotonic
        self._cached: _CachedContext | None = None
        self._lock = asyncio.Lock()

    async def get_context_messages(self) -> tuple[str, ...]:
        """Return the cached context strings, refreshing if stale."""
        now = self._now()
        cached = self._cached
        if cached is not None and cached.expires_at > now:
            return cached.messages

        async with self._lock:
            # Re-check after acquiring the lock — another waiter may have refreshed.
            now = self._now()
            cached = self._cached
            if cached is not None and cached.expires_at > now:
                return cached.messages

            try:
                messages = await asyncio.to_thread(self._fetch_sync)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "openai_context_fetch_failed",
                    extra={
                        "bucket": self._bucket_name,
                        "exception_type": type(exc).__name__,
                    },
                )
                messages = ()

            self._cached = _CachedContext(
                expires_at=self._now() + self._ttl_seconds,
                messages=messages,
            )
            return messages

    def _now(self) -> float:
        clock = self._clock
        return clock() if callable(clock) else 0.0

    def _fetch_sync(self) -> tuple[str, ...]:
        client = self._client or _build_default_client()
        blobs = list(client.list_blobs(self._bucket_name, prefix=self._prefix))
        # Sort by name so prepended order is stable.
        blobs.sort(key=lambda b: getattr(b, "name", ""))
        return _assemble(blobs, self._max_bytes, self._bucket_name)


def _assemble(blobs: Iterable[object], max_bytes: int, bucket_name: str) -> tuple[str, ...]:
    out: list[str] = []
    remaining = max_bytes
    truncated = False
    for blob in blobs:
        name = getattr(blob, "name", "")
        if not name.endswith(".md"):
            continue
        try:
            raw = blob.download_as_bytes()
        except Exception as exc:  # noqa: BLE001 — one bad blob shouldn't kill the rest
            _logger.warning(
                "openai_context_blob_download_failed",
                extra={
                    "bucket": bucket_name,
                    "object_key": name,
                    "exception_type": type(exc).__name__,
                },
            )
            continue
        if remaining <= 0:
            truncated = True
            break
        if len(raw) > remaining:
            raw = raw[:remaining]
            truncated = True
        remaining -= len(raw)
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            out.append(text)
    if truncated:
        _logger.warning(
            "openai_context_truncated_to_budget",
            extra={"bucket": bucket_name, "max_bytes": max_bytes},
        )
    return tuple(out)


def _build_default_client() -> object:
    # Deferred import keeps test doubles independent of the GCS SDK.
    from google.cloud import storage

    return storage.Client()


@lru_cache(maxsize=1)
def get_openai_context_loader() -> OpenAIContextLoader | None:
    """Return the process-wide loader, or ``None`` if no bucket is configured."""
    settings = get_settings()
    bucket = settings.openai_context_bucket
    if not bucket:
        return None
    return OpenAIContextLoader(bucket_name=bucket)
