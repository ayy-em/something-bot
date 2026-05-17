"""Handler + background-orchestrator tests for voice transcription (#43).

Same pattern as the video downloader tests: a synchronous inline
scheduler runs the spawned coroutine to completion so each test can
assert the full happy-path / failure-path ordering without juggling
real asyncio tasks.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.voice_transcription.handler import (
    VoiceTranscriptionHandler,
)
from something_really_bot.features.voice_transcription.transcriber import (
    Analysis,
    AnalysisError,
    TranscriptionError,
)
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.client import TelegramFileError, TelegramSendError
from something_really_bot.telegram.models import (
    GroupMessage,
    PrivateMessage,
    TextContent,
    User,
    Voice,
    VoiceContent,
)


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )


def _ctx(persistence: Any = None) -> BotContext:
    return BotContext(settings=_settings(), persistence=persistence)


def _voice(duration: int = 30, size: int | None = 4096) -> Voice:
    return Voice(
        file_id="voice-id",
        file_unique_id="voice-uniq",
        duration=duration,
        mime_type="audio/ogg",
        file_size=size,
    )


def _private_voice_msg(voice: Voice | None = None) -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=VoiceContent(voice=voice or _voice()),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _group_voice_msg(voice: Voice | None = None) -> GroupMessage:
    return GroupMessage(
        update_id=2,
        message_id=77,
        chat_id=-1001,
        date=1234567890,
        content=VoiceContent(voice=voice or _voice()),
        chat_title="grp",
        from_user=User(id=888, is_bot=False),
    )


@dataclass
class _FakeTelegram:
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    edited_messages: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    get_file_path_calls: list[str] = field(default_factory=list)
    download_calls: list[str] = field(default_factory=list)
    file_path_to_return: str = "voice/file_42.ogg"
    download_bytes: bytes = b"audio-bytes"
    get_file_raises: BaseException | None = None
    download_raises: BaseException | None = None
    send_message_raises: BaseException | None = None
    edit_message_raises: BaseException | None = None
    reaction_raises: BaseException | None = None
    next_message_id: int = 9001

    async def send_message(self, chat_id, text, *, reply_to_message_id=None, parse_mode=None):
        if self.send_message_raises is not None:
            raise self.send_message_raises
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": self.next_message_id}

    async def edit_message_text(self, chat_id, message_id, text, *, parse_mode=None):
        if self.edit_message_raises is not None:
            raise self.edit_message_raises
        self.edited_messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": message_id}

    async def set_message_reaction(self, chat_id, message_id, emoji):
        if self.reaction_raises is not None:
            raise self.reaction_raises
        self.reactions.append({"chat_id": chat_id, "message_id": message_id, "emoji": emoji})
        return {"ok": True}

    async def get_file_path(self, file_id):
        if self.get_file_raises is not None:
            raise self.get_file_raises
        self.get_file_path_calls.append(file_id)
        return self.file_path_to_return

    async def download_file(self, file_path):
        if self.download_raises is not None:
            raise self.download_raises
        self.download_calls.append(file_path)
        return self.download_bytes


@dataclass
class _FakeGCS:
    uploads: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None
    returned_uri: str = "gs://bucket/voice/x.ogg"

    async def upload(self, *, object_key, data, content_type=None):
        if self.raises is not None:
            raise self.raises
        self.uploads.append(
            {
                "object_key": object_key,
                "data_len": len(data),
                "content_type": content_type,
            }
        )
        return self.returned_uri


@dataclass
class _FakeTranscriber:
    transcripts: list[bytes] = field(default_factory=list)
    analyses: list[str] = field(default_factory=list)
    transcribe_return: str = "Hello there."
    analyze_return: Analysis = field(
        default_factory=lambda: Analysis(
            summary="A friendly greeting.",
            emotion="The speaker sounds cheerful.",
        )
    )
    transcribe_raises: BaseException | None = None
    analyze_raises: BaseException | None = None

    async def transcribe(self, audio_bytes, *, filename):  # noqa: ARG002
        if self.transcribe_raises is not None:
            raise self.transcribe_raises
        self.transcripts.append(audio_bytes)
        return self.transcribe_return

    async def analyze(self, transcript):
        if self.analyze_raises is not None:
            raise self.analyze_raises
        self.analyses.append(transcript)
        return self.analyze_return


@dataclass
class _FakeJobStorage:
    inserted: list[Any] = field(default_factory=list)
    status_history: list[tuple[int, str]] = field(default_factory=list)
    succeeded: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 1
    insert_raises: BaseException | None = None

    async def insert_pending(self, job):
        if self.insert_raises is not None:
            raise self.insert_raises
        self.inserted.append(job)
        return self.next_id

    async def update_status(self, job_id, status):
        self.status_history.append((job_id, status))

    async def mark_succeeded(self, job_id, **kwargs):
        self.succeeded.append({"id": job_id, **kwargs})

    async def mark_failed(self, job_id, *, error_class, error_message):
        self.failed.append(
            {"id": job_id, "error_class": error_class, "error_message": error_message}
        )


@dataclass
class _RecordingPersistence:
    events: list[EventRecord] = field(default_factory=list)

    def record_raw_update(self, _r): ...
    def record_message(self, _r): ...
    def record_file(self, _r): ...
    def record_response(self, _r): ...

    def record_event(self, record: EventRecord) -> None:
        self.events.append(record)


def _build_handler(
    *,
    telegram: _FakeTelegram,
    gcs: _FakeGCS,
    transcriber: _FakeTranscriber | None = None,
    jobs: _FakeJobStorage | None = None,
    scheduler=None,
) -> VoiceTranscriptionHandler:
    return VoiceTranscriptionHandler(
        scheduler=scheduler or asyncio.create_task,
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: telegram,
        transcriber_factory=lambda: transcriber,
        job_storage_factory=lambda: jobs,
    )


def test_matches_private_voice() -> None:
    handler = _build_handler(telegram=_FakeTelegram(), gcs=_FakeGCS())
    assert handler.matches(_private_voice_msg(), _ctx())


def test_matches_group_voice() -> None:
    handler = _build_handler(telegram=_FakeTelegram(), gcs=_FakeGCS())
    assert handler.matches(_group_voice_msg(), _ctx())


def test_does_not_match_text() -> None:
    handler = _build_handler(telegram=_FakeTelegram(), gcs=_FakeGCS())
    update = PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=TextContent(text="hi"),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )
    assert handler.matches(update, _ctx()) is False


async def test_happy_path_long_memo_private() -> None:
    """> 60s memo: analyze runs, long template, edits the ack in place."""
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    jobs = _FakeJobStorage()
    persistence = _RecordingPersistence()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(voice=_voice(duration=90)), _ctx(persistence))

    # Ack happened synchronously, background queued.
    assert len(telegram.sent_messages) == 1
    assert telegram.sent_messages[0]["text"] == "Transcribing your voice memo…"
    assert telegram.sent_messages[0]["reply_to_message_id"] == 42
    assert len(scheduled) == 1

    await scheduled[0]

    # No second send_message — the result was *edited* into the ack.
    assert len(telegram.sent_messages) == 1
    assert len(telegram.edited_messages) == 1
    edited = telegram.edited_messages[0]
    assert edited["chat_id"] == 100
    assert edited["message_id"] == 9001  # ack message_id
    assert edited["parse_mode"] == "HTML"

    final = edited["text"]
    assert final.startswith("Summary & Vibe:")
    assert "A friendly greeting." in final
    assert "The speaker sounds cheerful." in final
    # Summary + emotion live in one blockquote together, the transcript in
    # another below it.
    assert "<blockquote>A friendly greeting.\nThe speaker sounds cheerful.</blockquote>" in final
    assert "Transcript:" in final
    assert "<blockquote>Hello there.</blockquote>" in final

    # GCS upload happened with the expected key shape
    assert len(gcs.uploads) == 1
    assert gcs.uploads[0]["object_key"].startswith(
        "voice_transcription_requests/100/42/voice_voice-uniq"
    )
    assert gcs.uploads[0]["object_key"].endswith(".ogg")
    assert gcs.uploads[0]["content_type"] == "audio/ogg"

    # OpenAI calls received the downloaded bytes / transcript
    assert transcriber.transcripts == [b"audio-bytes"]
    assert transcriber.analyses == ["Hello there."]

    # Postgres job lifecycle (analyzing step ran for the long memo)
    assert len(jobs.inserted) == 1
    assert [s for _, s in jobs.status_history] == [
        "downloading",
        "uploading",
        "transcribing",
        "analyzing",
        "sending",
    ]
    assert len(jobs.succeeded) == 1
    assert jobs.succeeded[0]["transcript"] == "Hello there."
    assert jobs.succeeded[0]["summary"] == "A friendly greeting."
    assert jobs.succeeded[0]["emotion"] == "The speaker sounds cheerful."
    assert not jobs.failed

    # Persistence event fired
    assert len(persistence.events) == 1
    assert persistence.events[0].event == "voice_transcription_succeeded"


async def test_happy_path_short_memo_skips_analyze_and_uses_short_template() -> None:
    """≤ 60s memo: analyze does NOT run, short template, ack edited."""
    telegram = _FakeTelegram()
    transcriber = _FakeTranscriber()
    jobs = _FakeJobStorage()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=_FakeGCS(),
        transcriber=transcriber,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    # 45s ≤ 60s → short path.
    await handler.handle(_private_voice_msg(voice=_voice(duration=45)), _ctx())
    await scheduled[0]

    # Transcribe ran; analyze did NOT.
    assert transcriber.transcripts == [b"audio-bytes"]
    assert transcriber.analyses == []

    # Ack was edited with the short template.
    assert len(telegram.edited_messages) == 1
    final = telegram.edited_messages[0]["text"]
    assert final == "Voice-to-text:\n<blockquote>Hello there.</blockquote>"

    # Status sequence skipped "analyzing".
    assert [s for _, s in jobs.status_history] == [
        "downloading",
        "uploading",
        "transcribing",
        "sending",
    ]
    # Row was marked succeeded with NULL summary/emotion.
    assert jobs.succeeded[0]["summary"] is None
    assert jobs.succeeded[0]["emotion"] is None


async def test_boundary_exactly_60s_uses_short_template() -> None:
    """Threshold is ``duration > 60`` — 60s exactly stays on the short path."""
    telegram = _FakeTelegram()
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=_FakeGCS(),
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(voice=_voice(duration=60)), _ctx())
    await scheduled[0]

    assert transcriber.analyses == []
    assert telegram.edited_messages[0]["text"].startswith("Voice-to-text:")


async def test_edit_failure_falls_back_to_fresh_send() -> None:
    """If ``editMessageText`` fails, post the reply as a new message instead."""
    telegram = _FakeTelegram(edit_message_raises=TelegramSendError("can't edit"))
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=_FakeGCS(),
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(voice=_voice(duration=30)), _ctx())
    await scheduled[0]

    # Edit was attempted; fallback send ran.
    assert len(telegram.edited_messages) == 0  # raised, so nothing recorded
    # Two sends: the ack, then the fallback reply.
    assert len(telegram.sent_messages) == 2
    assert telegram.sent_messages[1]["text"].startswith("Voice-to-text:")


async def test_happy_path_group_uses_group_chat_id() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_group_voice_msg(voice=_voice(duration=90)), _ctx())
    await scheduled[0]

    assert telegram.sent_messages[0]["chat_id"] == -1001
    assert telegram.edited_messages[0]["chat_id"] == -1001
    assert gcs.uploads[0]["object_key"].startswith("voice_transcription_requests/-1001/77/")


async def test_too_long_replies_short_circuit() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(
        _private_voice_msg(_voice(duration=11 * 60)),
        _ctx(),
    )

    # Only the rejection reply — no ack, no GCS, no background task scheduled.
    assert len(telegram.sent_messages) == 1
    assert "10-minute limit" in telegram.sent_messages[0]["text"]
    assert not gcs.uploads
    assert not scheduled


async def test_too_large_replies_short_circuit() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(
        _private_voice_msg(_voice(size=30 * 1024 * 1024)),
        _ctx(),
    )

    assert len(telegram.sent_messages) == 1
    assert "too large" in telegram.sent_messages[0]["text"].lower()
    assert not gcs.uploads
    assert not scheduled


async def test_download_failure_replies_user_error() -> None:
    telegram = _FakeTelegram(get_file_raises=TelegramFileError("getFile not ok"))
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    jobs = _FakeJobStorage()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(), _ctx())
    await scheduled[0]

    # Only the ack was sent; the error message edits the ack in place.
    assert len(telegram.sent_messages) == 1
    assert len(telegram.edited_messages) == 1
    assert "pull that voice memo" in telegram.edited_messages[0]["text"]
    assert not transcriber.transcripts
    assert len(jobs.failed) == 1
    assert jobs.failed[0]["error_class"] == "TelegramFileError"


async def test_transcription_failure_replies_user_error() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber(transcribe_raises=TranscriptionError("boom"))
    jobs = _FakeJobStorage()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(), _ctx())
    await scheduled[0]

    assert "transcribe" in telegram.edited_messages[0]["text"].lower()
    # Upload still happened before transcription failed.
    assert len(gcs.uploads) == 1
    assert len(jobs.failed) == 1
    assert jobs.failed[0]["error_class"] == "TranscriptionError"


async def test_analysis_failure_replies_user_error() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber(analyze_raises=AnalysisError("bad json"))
    jobs = _FakeJobStorage()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    # Analysis only runs for long memos — use 90s.
    await handler.handle(_private_voice_msg(voice=_voice(duration=90)), _ctx())
    await scheduled[0]

    assert "summarize" in telegram.edited_messages[0]["text"].lower()
    assert len(jobs.failed) == 1
    assert jobs.failed[0]["error_class"] == "AnalysisError"


async def test_missing_transcriber_replies_user_error() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    jobs = _FakeJobStorage()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=None,
        jobs=jobs,
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_private_voice_msg(), _ctx())
    await scheduled[0]

    assert "isn't configured" in telegram.edited_messages[0]["text"]
    assert not gcs.uploads
    assert len(jobs.failed) == 1
    assert jobs.failed[0]["error_class"] == "TranscriberUnavailable"


async def test_ack_failure_is_swallowed() -> None:
    telegram = _FakeTelegram(send_message_raises=TelegramSendError("nope"))
    gcs = _FakeGCS()
    transcriber = _FakeTranscriber()
    scheduled: list[Any] = []
    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        transcriber=transcriber,
        scheduler=lambda c: scheduled.append(c),
    )

    # Should not raise even though every send_message call fails.
    await handler.handle(_private_voice_msg(), _ctx())
    await scheduled[0]

    # Every send_message call raised, so nothing got recorded.
    assert telegram.sent_messages == []
