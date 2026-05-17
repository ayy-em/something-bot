"""OpenAI vision wrapper for OCR (#45).

Uses chat.completions with an image input ("image_url" content part
with a base64 data: URL). The chat model from settings is used —
gpt-4o-mini and gpt-4o both accept image inputs.
"""

import asyncio
import base64
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = (
    "You are an OCR engine. Given an image, return the text content "
    "visible in the image as plain text. Preserve line breaks and "
    "rough structure where useful. If the image contains no readable "
    "text, return exactly: NO_TEXT. Do not summarize or describe the "
    "image — return the literal text only."
)


class OCRError(Exception):
    """Raised when OpenAI vision OCR fails."""


class OCRClient:
    """Thin wrapper over chat.completions with a base64 image input."""

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

    async def extract_text(self, image_bytes: bytes, *, mime_type: str = "image/jpeg") -> str:
        data_url = self._build_data_url(image_bytes, mime_type)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
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
            raise OCRError(f"OCR timed out after {self._timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "ocr_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise OCRError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise OCRError("OCR returned no choices")
        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise OCRError("OCR returned empty content")
        return content.strip()

    @staticmethod
    def _build_data_url(image_bytes: bytes, mime_type: str) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{b64}"


@lru_cache(maxsize=1)
def get_ocr_client() -> OCRClient | None:
    """Process-wide singleton, or ``None`` if no OpenAI key is configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return OCRClient(api_key=settings.openai_api_key, chat_model=settings.openai_model)
