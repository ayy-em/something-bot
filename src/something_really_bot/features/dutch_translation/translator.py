"""OpenAI wrapper for Dutch → English translation (#47)."""

import asyncio
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 25.0

_SYSTEM_PROMPT = (
    "You are a Dutch-to-English translator. Translate the user's message "
    "from Dutch into natural, idiomatic English. Preserve meaning, tone, "
    "and register. If the source already looks English or mixed, translate "
    "any Dutch portions and pass English portions through unchanged. Reply "
    "with the translation only — no preamble, no quotes, no notes."
)


class TranslationError(Exception):
    """Raised when the OpenAI translation call fails."""


class DutchTranslator:
    """Thin wrapper around chat.completions for one-shot translation."""

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

    async def translate(self, dutch_text: str) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": dutch_text},
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
            raise TranslationError(f"Translation timed out after {self._timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "dutch_translation_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise TranslationError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise TranslationError("Translation returned no choices")
        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise TranslationError("Translation returned empty content")
        return content.strip()


@lru_cache(maxsize=1)
def get_dutch_translator() -> DutchTranslator | None:
    """Process-wide singleton, or ``None`` if no OpenAI key is configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return DutchTranslator(
        api_key=settings.openai_api_key,
        chat_model=settings.openai_model,
    )
