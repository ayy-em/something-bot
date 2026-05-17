"""Async OpenAI client wrapper used by the fallback handler (#23, #26).

Wraps :class:`openai.AsyncOpenAI` so handlers depend on a small surface
(``complete(prompt) -> str``) rather than the SDK directly. Keeping it
isolated lets us swap to a different LLM provider, add caching, or
inject test doubles without touching feature code.

The system prompt is intentionally minimal and *neutral*. Persistent
shared context (#26) is loaded by an injected
:class:`OpenAIContextLoader` and prepended after ``SYSTEM_PROMPT`` but
before the user's prompt. A GCS failure yields no context — the
completion still runs.
"""

import asyncio
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger
from something_really_bot.services.openai_context import (
    OpenAIContextLoader,
    get_openai_context_loader,
)

_logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 25.0  # Cloud Run request timeout is 60s; leave headroom.

SYSTEM_PROMPT = (
    "You are Something Really Bot, a concise Telegram assistant. "
    "Reply in plain text suitable for a Telegram message: no markdown "
    "fences, no headings, no bullet lists unless the user explicitly "
    "asks. Keep answers under ~200 words when possible."
)


class OpenAIRequestError(Exception):
    """Raised when the OpenAI request fails (HTTP, timeout, parsing)."""


class OpenAIClient:
    """Thin async wrapper around the OpenAI chat completions API."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str,
        client: AsyncOpenAI | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        context_loader: OpenAIContextLoader | None = None,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client or AsyncOpenAI(api_key=api_key.get_secret_value())
        self._context_loader = context_loader

    async def complete(self, prompt: str) -> str:
        """Call chat completions with ``SYSTEM_PROMPT`` + ``prompt``.

        Raises:
            OpenAIRequestError: any failure (network, parse, timeout).
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self._context_loader is not None:
            for chunk in await self._context_loader.get_context_messages():
                messages.append({"role": "system", "content": chunk})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            _logger.warning("openai_request_timeout", extra={"model": self._model})
            raise OpenAIRequestError(f"OpenAI timeout after {self._timeout_seconds}s") from exc
        except Exception as exc:  # noqa: BLE001 — translate every SDK error type
            _logger.warning(
                "openai_request_failed",
                extra={"model": self._model, "exception_type": type(exc).__name__},
            )
            raise OpenAIRequestError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise OpenAIRequestError("OpenAI returned no choices")
        text = getattr(choices[0].message, "content", None)
        if not isinstance(text, str) or not text.strip():
            raise OpenAIRequestError("OpenAI returned empty content")
        return text.strip()


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAIClient | None:
    """Return the process-wide :class:`OpenAIClient`, or ``None`` if no key.

    Returning ``None`` rather than raising lets the rest of the app boot
    even if ``OPENAI_API_KEY`` is missing — the fallback handler reports
    the unavailable client through a deterministic apology reply.
    """
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        context_loader=get_openai_context_loader(),
    )
