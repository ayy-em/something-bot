"""Unit tests for the OpenAI wrapper used by voice transcription (#43)."""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.features.voice_transcription.transcriber import (
    Analysis,
    AnalysisError,
    TranscriptionError,
    VoiceTranscriber,
)


@dataclass
class _FakeTranscriptionResponse:
    text: str


@dataclass
class _FakeChatMessage:
    content: str


@dataclass
class _FakeChatChoice:
    message: _FakeChatMessage


@dataclass
class _FakeChatResponse:
    choices: list[_FakeChatChoice]


@dataclass
class _FakeAudioNamespace:
    transcriptions: Any = None


@dataclass
class _FakeTranscriptions:
    response: _FakeTranscriptionResponse | Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@dataclass
class _FakeChat:
    completions: Any = None


@dataclass
class _FakeChatCompletions:
    response: _FakeChatResponse | Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@dataclass
class _FakeAsyncOpenAI:
    audio: _FakeAudioNamespace
    chat: _FakeChat


def _client(
    *,
    transcribe: _FakeTranscriptions | None = None,
    chat: _FakeChatCompletions | None = None,
) -> tuple[VoiceTranscriber, _FakeTranscriptions, _FakeChatCompletions]:
    t = transcribe or _FakeTranscriptions()
    c = chat or _FakeChatCompletions()
    fake = _FakeAsyncOpenAI(
        audio=_FakeAudioNamespace(transcriptions=t),
        chat=_FakeChat(completions=c),
    )
    transcriber = VoiceTranscriber(
        api_key=SecretStr("sk-test"),
        chat_model="gpt-4o-mini",
        client=fake,  # type: ignore[arg-type]
    )
    return transcriber, t, c


async def test_transcribe_returns_stripped_text() -> None:
    transcriber, t, _ = _client(
        transcribe=_FakeTranscriptions(response=_FakeTranscriptionResponse(text="  hi there\n"))
    )

    result = await transcriber.transcribe(b"audio", filename="x.ogg")

    assert result == "hi there"
    assert t.calls[0]["model"] == "gpt-4o-transcribe"
    file_obj = t.calls[0]["file"]
    assert getattr(file_obj, "name", None) == "x.ogg"


async def test_transcribe_empty_text_raises() -> None:
    transcriber, _, _ = _client(
        transcribe=_FakeTranscriptions(response=_FakeTranscriptionResponse(text="   "))
    )

    with pytest.raises(TranscriptionError):
        await transcriber.transcribe(b"audio", filename="x.ogg")


async def test_transcribe_sdk_error_wrapped() -> None:
    transcriber, _, _ = _client(transcribe=_FakeTranscriptions(response=RuntimeError("boom")))

    with pytest.raises(TranscriptionError):
        await transcriber.transcribe(b"audio", filename="x.ogg")


async def test_analyze_parses_json() -> None:
    payload = json.dumps({"summary": "Got it.", "emotion": "Calm."})
    transcriber, _, c = _client(
        chat=_FakeChatCompletions(
            response=_FakeChatResponse(choices=[_FakeChatChoice(_FakeChatMessage(payload))])
        )
    )

    result = await transcriber.analyze("Hello there.")

    assert result == Analysis(summary="Got it.", emotion="Calm.")
    call = c.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["response_format"] == {"type": "json_object"}


async def test_analyze_missing_fields_raises() -> None:
    payload = json.dumps({"summary": "only summary"})
    transcriber, _, _ = _client(
        chat=_FakeChatCompletions(
            response=_FakeChatResponse(choices=[_FakeChatChoice(_FakeChatMessage(payload))])
        )
    )

    with pytest.raises(AnalysisError):
        await transcriber.analyze("hi")


async def test_analyze_non_json_raises() -> None:
    transcriber, _, _ = _client(
        chat=_FakeChatCompletions(
            response=_FakeChatResponse(choices=[_FakeChatChoice(_FakeChatMessage("not json"))])
        )
    )

    with pytest.raises(AnalysisError):
        await transcriber.analyze("hi")


async def test_analyze_sdk_error_wrapped() -> None:
    transcriber, _, _ = _client(chat=_FakeChatCompletions(response=RuntimeError("boom")))

    with pytest.raises(AnalysisError):
        await transcriber.analyze("hi")
