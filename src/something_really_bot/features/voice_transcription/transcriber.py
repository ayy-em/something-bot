"""OpenAI calls for voice transcription (#43).

Two calls per voice memo:

1. ``audio.transcriptions.create`` with ``gpt-4o-transcribe`` to turn
   the OGG/Opus voice file into text.
2. ``chat.completions.create`` with the chat model from settings to
   produce summary + emotion in a single JSON response.

Network failures funnel into :class:`TranscriptionError` /
:class:`AnalysisError` so the handler can map them to user-facing
messages.
"""

import asyncio
import io
import json
from dataclasses import dataclass
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

TRANSCRIBE_MODEL = "gpt-4o-transcribe"
ANALYSIS_TIMEOUT_SECONDS = 25.0
TRANSCRIBE_TIMEOUT_SECONDS = 60.0

_ANALYSIS_SYSTEM_PROMPT = (
    "You analyze short voice-memo transcripts. Given a transcript, respond "
    "with a single JSON object exactly matching this schema:\n"
    '{"summary": "<1-3 sentence factual summary of what the speaker said>", '
    '"emotion": "<1 sentence describing the speaker\'s apparent emotional '
    'tone>"}\n'
    "Do not include any other text, markdown, or code fences. Reply in the "
    "transcript's language."
)


class TranscriptionError(Exception):
    """Raised when OpenAI audio transcription fails."""


class AnalysisError(Exception):
    """Raised when OpenAI chat completion for summary/emotion fails."""


@dataclass(frozen=True)
class Analysis:
    """Output of the summary+emotion call."""

    summary: str
    emotion: str


class VoiceTranscriber:
    """Wraps the two OpenAI calls needed to transcribe + analyze a voice memo."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        chat_model: str,
        client: AsyncOpenAI | None = None,
        transcribe_timeout_seconds: float = TRANSCRIBE_TIMEOUT_SECONDS,
        analysis_timeout_seconds: float = ANALYSIS_TIMEOUT_SECONDS,
    ) -> None:
        self._chat_model = chat_model
        self._client = client or AsyncOpenAI(api_key=api_key.get_secret_value())
        self._transcribe_timeout = transcribe_timeout_seconds
        self._analysis_timeout = analysis_timeout_seconds

    async def transcribe(self, audio_bytes: bytes, *, filename: str) -> str:
        """Transcribe ``audio_bytes`` and return the text."""
        # OpenAI's SDK reads from a file-like; wrapping bytes in BytesIO
        # keeps everything in memory without writing to disk.
        buffer = io.BytesIO(audio_bytes)
        buffer.name = filename
        try:
            response = await asyncio.wait_for(
                self._client.audio.transcriptions.create(
                    model=TRANSCRIBE_MODEL,
                    file=buffer,
                ),
                timeout=self._transcribe_timeout,
            )
        except TimeoutError as exc:
            raise TranscriptionError(
                f"Transcription timed out after {self._transcribe_timeout}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — translate every SDK error type
            _logger.warning(
                "voice_transcription_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise TranscriptionError(str(exc)) from exc

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionError("Transcription returned empty text")
        return text.strip()

    async def analyze(self, transcript: str) -> Analysis:
        """One chat call → summary + emotion as a JSON object."""
        messages = [
            {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ]
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._chat_model,
                    messages=messages,
                    response_format={"type": "json_object"},
                ),
                timeout=self._analysis_timeout,
            )
        except TimeoutError as exc:
            raise AnalysisError(f"Analysis timed out after {self._analysis_timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "voice_analysis_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise AnalysisError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise AnalysisError("Analysis returned no choices")
        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise AnalysisError("Analysis returned empty content")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"Analysis returned non-JSON content: {content!r}") from exc
        summary = parsed.get("summary")
        emotion = parsed.get("emotion")
        if not isinstance(summary, str) or not summary.strip():
            raise AnalysisError("Analysis JSON missing 'summary'")
        if not isinstance(emotion, str) or not emotion.strip():
            raise AnalysisError("Analysis JSON missing 'emotion'")
        return Analysis(summary=summary.strip(), emotion=emotion.strip())


@lru_cache(maxsize=1)
def get_voice_transcriber() -> VoiceTranscriber | None:
    """Process-wide singleton, or ``None`` if no OpenAI key is configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return VoiceTranscriber(
        api_key=settings.openai_api_key,
        chat_model=settings.openai_model,
    )
