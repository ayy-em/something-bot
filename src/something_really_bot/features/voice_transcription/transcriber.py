"""OpenAI calls for voice transcription (#43, #63).

Up to four calls per voice memo:

1. ``audio.transcriptions.create`` with ``gpt-transcribe`` to turn
   the OGG/Opus voice file into text.
2. ``chat.completions.create`` with the chat model from settings to
   produce summary + emotion in a single JSON response.

Memos over the analysis threshold get two more, which run concurrently
with (2) and are pure garnish (#63):

3. ``chat.completions.create`` with the parody model for a <=100 word
   roast written in the speaker's own voice.
4. ``audio.speech.create`` to vocalize that roast as Ogg/Opus, ready to
   go straight back to Telegram as a voice message.

Network failures funnel into :class:`TranscriptionError` /
:class:`AnalysisError` so the handler can map them to user-facing
messages. :class:`ParodyError` / :class:`SpeechError` are never shown to
the user — the transcript is the product, the roast is a bonus.
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

# OpenAI's offline ASR model, released 2026-07-28 to replace
# ``gpt-4o-transcribe``: lower word error rate, cheaper ($0.0045/min vs
# $0.006/min), and it accepts ``keywords``/``languages`` hints we do not
# use yet. The old model still works but is no longer recommended for new
# integrations. Same 25 MB request ceiling, same ``.text`` response shape.
TRANSCRIBE_MODEL = "gpt-transcribe"
ANALYSIS_TIMEOUT_SECONDS = 25.0
TRANSCRIBE_TIMEOUT_SECONDS = 60.0
PARODY_TIMEOUT_SECONDS = 30.0
SPEECH_TIMEOUT_SECONDS = 45.0
# Ogg-encapsulated Opus is exactly what Telegram's sendVoice wants, so the
# TTS output goes back out as a real voice message rather than a file
# attachment.
SPEECH_RESPONSE_FORMAT = "opus"
# Hard ceiling on what we hand to TTS. The prompt asks for <=100 words;
# this is the backstop for when the model gets carried away, since every
# character is billed and then spoken aloud.
MAX_PARODY_CHARS = 900

_ANALYSIS_SYSTEM_PROMPT = (
    "You analyze short voice-memo transcripts. Given a transcript, respond "
    "with a single JSON object exactly matching this schema:\n"
    '{"summary": "<1-3 sentence factual summary of what the speaker said>", '
    '"emotion": "<1 sentence describing the speaker\'s apparent emotional '
    'tone>"}\n'
    "Do not include any other text, markdown, or code fences. Reply in the "
    "transcript's language."
)


_PARODY_SYSTEM_PROMPT = (
    "You are a merciless impressionist. You receive the transcript of a "
    "voice memo. Reply with a TL;DR of that memo performed *as* the "
    "speaker: their voice, their verbal tics, their pet phrases, their "
    "self-importance — dialled up until it collapses into self-parody.\n"
    "Rules:\n"
    "- Under 100 words. Shorter lands harder.\n"
    "- First person, in character, start talking immediately.\n"
    "- The actual point of the memo must stay recognisable. This is a "
    "summary wearing a costume, not a non sequitur.\n"
    "- Dry, ironic, over-exaggerated. Mock how they said it and what they "
    "said, never their appearance, identity, or anything outside the memo.\n"
    "- Reply in the transcript's language.\n"
    "- Plain text only: no markdown, no quotation marks, no stage "
    "directions, no preamble. Your entire reply is spoken aloud verbatim."
)

# The TTS model takes free-form direction on delivery; a flat read kills
# the joke, so the performance note matters as much as the words.
_SPEECH_INSTRUCTIONS = (
    "Perform this as a theatrical, deadpan impression of the person who "
    "said it: smug, over-confident, thoroughly delighted with yourself. "
    "Lean into the irony. Slightly too fast, like you cannot wait to reach "
    "your own punchline."
)


class TranscriptionError(Exception):
    """Raised when OpenAI audio transcription fails."""


class AnalysisError(Exception):
    """Raised when OpenAI chat completion for summary/emotion fails."""


class ParodyError(Exception):
    """Raised when the OpenAI chat completion for the parody roast fails."""


class SpeechError(Exception):
    """Raised when OpenAI text-to-speech fails."""


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
        parody_model: str = "gpt-5.2",
        tts_model: str = "gpt-4o-mini-tts",
        tts_voice: str = "marin",
        client: AsyncOpenAI | None = None,
        transcribe_timeout_seconds: float = TRANSCRIBE_TIMEOUT_SECONDS,
        analysis_timeout_seconds: float = ANALYSIS_TIMEOUT_SECONDS,
        parody_timeout_seconds: float = PARODY_TIMEOUT_SECONDS,
        speech_timeout_seconds: float = SPEECH_TIMEOUT_SECONDS,
    ) -> None:
        self._chat_model = chat_model
        self._parody_model = parody_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._client = client or AsyncOpenAI(api_key=api_key.get_secret_value())
        self._transcribe_timeout = transcribe_timeout_seconds
        self._analysis_timeout = analysis_timeout_seconds
        self._parody_timeout = parody_timeout_seconds
        self._speech_timeout = speech_timeout_seconds

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

    async def parody(self, transcript: str) -> str:
        """One chat call → a <=100 word roast in the speaker's own voice."""
        messages = [
            {"role": "system", "content": _PARODY_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ]
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._parody_model,
                    messages=messages,
                ),
                timeout=self._parody_timeout,
            )
        except TimeoutError as exc:
            raise ParodyError(f"Parody timed out after {self._parody_timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "voice_parody_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise ParodyError(str(exc)) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise ParodyError("Parody returned no choices")
        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ParodyError("Parody returned empty content")
        return content.strip()[:MAX_PARODY_CHARS]

    async def synthesize(self, text: str) -> bytes:
        """One TTS call → Ogg/Opus audio bytes for Telegram's sendVoice."""
        try:
            response = await asyncio.wait_for(
                self._client.audio.speech.create(
                    model=self._tts_model,
                    voice=self._tts_voice,
                    input=text,
                    instructions=_SPEECH_INSTRUCTIONS,
                    response_format=SPEECH_RESPONSE_FORMAT,
                ),
                timeout=self._speech_timeout,
            )
        except TimeoutError as exc:
            raise SpeechError(f"Speech synthesis timed out after {self._speech_timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "voice_speech_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise SpeechError(str(exc)) from exc

        audio = getattr(response, "content", None)
        if not isinstance(audio, bytes) or not audio:
            raise SpeechError("Speech synthesis returned no audio")
        return audio


@lru_cache(maxsize=1)
def get_voice_transcriber() -> VoiceTranscriber | None:
    """Process-wide singleton, or ``None`` if no OpenAI key is configured."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    return VoiceTranscriber(
        api_key=settings.openai_api_key,
        chat_model=settings.openai_model,
        parody_model=settings.openai_parody_model,
        tts_model=settings.openai_tts_model,
        tts_voice=settings.openai_tts_voice,
    )
