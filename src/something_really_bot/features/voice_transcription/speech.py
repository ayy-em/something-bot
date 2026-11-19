"""Text-to-speech providers for the parody roast (#63).

The roast is written by an OpenAI chat model and then spoken by one of two
interchangeable providers, picked by ``TTS_PROVIDER``:

* ``elevenlabs`` (default) — ``POST /v1/text-to-speech/{voice_id}`` with
  ``output_format=opus_48000_64``.
* ``openai`` — ``audio.speech.create`` with ``response_format="opus"``.

Both return Ogg/Opus bytes, which is what Telegram's ``sendVoice`` wants,
so the handler never needs to know which one ran. Every failure funnels
into :class:`SpeechError`; the handler treats that as "no roast this time".
"""

import asyncio
from typing import Literal, Protocol

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

TtsProvider = Literal["elevenlabs", "openai"]

SPEECH_TIMEOUT_SECONDS = 45.0

# Ogg-encapsulated Opus is exactly what Telegram's sendVoice wants, so the
# TTS output goes back out as a real voice message rather than a file
# attachment.
OPENAI_RESPONSE_FORMAT = "opus"
ELEVENLABS_OUTPUT_FORMAT = "opus_48000_64"
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"

# The OpenAI TTS model steers on accent, emotional range, intonation,
# impressions, speed and tone — so the performance note is written along
# those axes rather than as one adjective. A flat read kills the joke no
# matter how good the words are. ElevenLabs has no equivalent free-text
# field; its delivery comes from the chosen voice.
OPENAI_SPEECH_INSTRUCTIONS = (
    "Voice: a smug theatrical impressionist doing a merciless impersonation "
    "of the person who recorded the original memo.\n"
    "Delivery: wildly over-committed. Stretch the vowels on any word the "
    "speaker was clearly proud of. Sneer lightly through the self-important "
    "parts. Let a small, self-satisfied laugh colour the delivery.\n"
    "Pacing: fast and impatient, tumbling downhill toward the punchline — "
    "then a deliberate pause before the final line.\n"
    "Intonation: swooping and melodramatic through the middle, collapsing "
    "into flat, bored deadpan on the last sentence.\n"
    "Emotion: enormous confidence, zero self-awareness."
)


class SpeechError(Exception):
    """Raised when text-to-speech fails, whichever provider ran."""


class SpeechSynthesizer(Protocol):
    """Turns roast text into Ogg/Opus bytes."""

    async def synthesize(self, text: str) -> bytes: ...


class OpenAISpeechSynthesizer:
    """OpenAI ``audio.speech.create``."""

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        voice: str,
        timeout_seconds: float = SPEECH_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._model = model
        self._voice = voice
        self._timeout = timeout_seconds

    async def synthesize(self, text: str) -> bytes:
        try:
            response = await asyncio.wait_for(
                self._client.audio.speech.create(
                    model=self._model,
                    voice=self._voice,
                    input=text,
                    instructions=OPENAI_SPEECH_INSTRUCTIONS,
                    response_format=OPENAI_RESPONSE_FORMAT,
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise SpeechError(f"Speech synthesis timed out after {self._timeout}s") from exc
        except Exception as exc:  # noqa: BLE001 — translate every SDK error type
            _logger.warning(
                "voice_speech_call_failed",
                extra={"provider": "openai", "exception_type": type(exc).__name__},
            )
            raise SpeechError(str(exc)) from exc

        audio = getattr(response, "content", None)
        if not isinstance(audio, bytes) or not audio:
            raise SpeechError("Speech synthesis returned no audio")
        return audio


class ElevenLabsSpeechSynthesizer:
    """ElevenLabs ``POST /v1/text-to-speech/{voice_id}`` over plain httpx.

    No SDK: it is one POST, and httpx is already a dependency.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        voice_id: str,
        model_id: str,
        timeout_seconds: float = SPEECH_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = ELEVENLABS_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._timeout = timeout_seconds
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    async def synthesize(self, text: str) -> bytes:
        url = f"{self._base_url}/v1/text-to-speech/{self._voice_id}"
        try:
            if self._http is not None:
                response = await self._post(self._http, url, text)
            else:
                async with httpx.AsyncClient() as client:
                    response = await self._post(client, url, text)
        except httpx.TimeoutException as exc:
            raise SpeechError(f"Speech synthesis timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            _logger.warning(
                "voice_speech_call_failed",
                extra={"provider": "elevenlabs", "exception_type": type(exc).__name__},
            )
            raise SpeechError(str(exc)) from exc

        if response.status_code != 200:
            _logger.warning(
                "voice_speech_call_failed",
                extra={
                    "provider": "elevenlabs",
                    "status_code": response.status_code,
                    "body": response.text[:500],
                },
            )
            raise SpeechError(f"ElevenLabs returned HTTP {response.status_code}")

        audio = response.content
        if not audio:
            raise SpeechError("Speech synthesis returned no audio")
        return audio

    async def _post(self, client: httpx.AsyncClient, url: str, text: str) -> httpx.Response:
        return await client.post(
            url,
            params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
            headers={"xi-api-key": self._api_key.get_secret_value()},
            json={"text": text, "model_id": self._model_id},
            timeout=self._timeout,
        )
