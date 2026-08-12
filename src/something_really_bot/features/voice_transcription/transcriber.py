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
# A 4-minute memo produced a roast that was a chore to sit through, so the
# ceiling is deliberately brutal: this is a punchline, not a summary.
#
# The model does not respect the number it is given. Measured 2026-08-12
# against gpt-5.2, 5 samples per setting: asking for 40 words returned
# 44-49 every time; asking 30 returned 37-45; asking 25 returned 26-31.
# So the prompt asks for PROMPT_WORD_TARGET and the code enforces
# MAX_PARODY_WORDS, which is the limit that actually holds.
PROMPT_WORD_TARGET = 25
MAX_PARODY_WORDS = 40
# Belt-and-braces on characters too — a reply of 40 very long words is
# still billed and still spoken aloud.
MAX_PARODY_CHARS = 400

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
    "You are a savage impressionist. You receive the transcript of a voice "
    "memo. Perform its point back as the speaker — but as a caricature of "
    "them: their tics, their favourite words, their absolute certainty, "
    "cranked until it is ridiculous.\n"
    "Technique:\n"
    "- Open mid-thought, as if we caught you already rambling.\n"
    "- Steal their actual verbal habits — filler words, pet phrases, the "
    "one metaphor they were pleased with — and lean on them too hard.\n"
    "- Inflate the stakes of whatever mundane thing they described until "
    "the self-importance does the mocking for you.\n"
    "- End on a flat, deflating line that admits what it was really about.\n"
    "Hard rules:\n"
    f"- {PROMPT_WORD_TARGET} words maximum. This is a punchline, not a "
    "summary. Every word earns its place or gets cut.\n"
    "- First person, in character, no warm-up.\n"
    "- Mock what they said and how they said it. Never their appearance, "
    "identity, intelligence, or anything outside this memo.\n"
    "- Reply in the transcript's language.\n"
    "- Plain text only: no markdown, no quotation marks, no stage "
    "directions, no preamble. Your entire reply is spoken aloud verbatim."
)

# The TTS model steers on accent, emotional range, intonation, impressions,
# speed and tone — so the performance note is written along those axes
# rather than as one adjective. A flat read kills the joke no matter how
# good the words are.
_SPEECH_INSTRUCTIONS = (
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


def _trim_to_words(text: str, max_words: int) -> str:
    """Enforce the word ceiling the model keeps overshooting.

    Prefers to end on a sentence boundary so a clipped roast still sounds
    finished when spoken. Falls back to a hard cut when the whole thing is
    one long run-on.
    """
    words = text.split()
    if len(words) <= max_words:
        return text

    budget = " ".join(words[:max_words])
    boundary = max(budget.rfind(c) for c in ".!?…")
    if boundary > 0:
        return budget[: boundary + 1]
    return budget


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
        return _trim_to_words(content.strip(), MAX_PARODY_WORDS)[:MAX_PARODY_CHARS]

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
