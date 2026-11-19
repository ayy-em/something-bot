"""Unit tests for the switchable parody-roast TTS providers."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.voice_transcription.speech import (
    ELEVENLABS_OUTPUT_FORMAT,
    ElevenLabsSpeechSynthesizer,
    OpenAISpeechSynthesizer,
    SpeechError,
)
from something_really_bot.features.voice_transcription.transcriber import (
    build_speech_synthesizer,
)


def _elevenlabs(handler) -> tuple[ElevenLabsSpeechSynthesizer, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    synth = ElevenLabsSpeechSynthesizer(
        SecretStr("xi-test-key"),
        voice_id="XNrB7jz2HCkpU5yK08kP",
        model_id="eleven_multilingual_v2",
        http_client=client,
    )
    return synth, seen


async def test_elevenlabs_posts_text_to_voice_and_returns_opus() -> None:
    synth, seen = _elevenlabs(lambda _: httpx.Response(200, content=b"OggS-bytes"))

    audio = await synth.synthesize("mock me")

    assert audio == b"OggS-bytes"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/text-to-speech/XNrB7jz2HCkpU5yK08kP"
    assert request.url.params["output_format"] == ELEVENLABS_OUTPUT_FORMAT
    assert ELEVENLABS_OUTPUT_FORMAT.startswith("opus_")
    assert request.headers["xi-api-key"] == "xi-test-key"
    assert json.loads(request.content) == {
        "text": "mock me",
        "model_id": "eleven_multilingual_v2",
    }


async def test_elevenlabs_http_error_status_raises_speech_error() -> None:
    synth, _ = _elevenlabs(lambda _: httpx.Response(401, json={"detail": "bad key"}))

    with pytest.raises(SpeechError, match="401"):
        await synth.synthesize("hi")


async def test_elevenlabs_empty_audio_raises() -> None:
    synth, _ = _elevenlabs(lambda _: httpx.Response(200, content=b""))

    with pytest.raises(SpeechError):
        await synth.synthesize("hi")


async def test_elevenlabs_transport_error_raises_speech_error() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    synth, _ = _elevenlabs(_boom)

    with pytest.raises(SpeechError):
        await synth.synthesize("hi")


async def test_elevenlabs_timeout_raises_speech_error() -> None:
    def _slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    synth, _ = _elevenlabs(_slow)

    with pytest.raises(SpeechError, match="timed out"):
        await synth.synthesize("hi")


def _settings(**overrides: Any) -> Settings:
    return Settings(
        telegram_webhook_secret=SecretStr("s"),
        telegram_bot_token=SecretStr("t"),
        **overrides,
    )


def test_settings_default_to_elevenlabs_with_configured_voice(monkeypatch) -> None:
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    settings = _settings()

    assert settings.tts_provider == "elevenlabs"
    assert settings.elevenlabs_voice_id == "XNrB7jz2HCkpU5yK08kP"


def test_build_picks_elevenlabs_when_key_present() -> None:
    settings = _settings(tts_provider="elevenlabs", elevenlabs_api_key=SecretStr("xi"))

    synth = build_speech_synthesizer(settings, client=object())  # type: ignore[arg-type]

    assert isinstance(synth, ElevenLabsSpeechSynthesizer)


def test_build_falls_back_to_openai_when_elevenlabs_key_missing() -> None:
    settings = _settings(tts_provider="elevenlabs", elevenlabs_api_key=None)

    synth = build_speech_synthesizer(settings, client=object())  # type: ignore[arg-type]

    assert isinstance(synth, OpenAISpeechSynthesizer)


def test_build_picks_openai_when_selected_even_with_elevenlabs_key() -> None:
    settings = _settings(tts_provider="openai", elevenlabs_api_key=SecretStr("xi"))

    synth = build_speech_synthesizer(settings, client=object())  # type: ignore[arg-type]

    assert isinstance(synth, OpenAISpeechSynthesizer)


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError):
        _settings(tts_provider="polly")
