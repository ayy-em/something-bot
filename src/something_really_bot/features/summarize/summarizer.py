"""OpenAI wrapper for document summarization (#46)."""

import asyncio
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45.0

_SYSTEM_PROMPT = (
    "You summarize documents. Given the text of a document, produce a "
    "concise TL;DR: 3-6 sentences capturing the key points and overall "
    "purpose. Plain text, no markdown headings or bullet lists. Reply in "
    "the document's language."
)


class SummarizationError(Exception):
    """Raised when OpenAI summarization fails."""


class DocumentSummarizer:
    """Thin wrapper around chat.completions for one-shot summarization."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        chat_model: str,
        client: AsyncOpenAI | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model = chat_model
        self._client = client or AsyncOpenAI(api_key=api_key.get_secret_value())
        self._timeout = timeout_seconds

    async def summarize(self, document_text: str) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": document_text},
        ]
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise SummarizationError(f"Summarization timed out after {self._timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "summarize_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise SummarizationError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise SummarizationError("Summarization returned no choices")
        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise SummarizationError("Summarization returned empty content")
        return content.strip()


@lru_cache(maxsize=1)
def get_summarizer() -> DocumentSummarizer | None:
    """Process-wide singleton, or ``None`` if no OpenAI key is configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return DocumentSummarizer(
        api_key=settings.openai_api_key,
        chat_model=settings.openai_model,
    )
