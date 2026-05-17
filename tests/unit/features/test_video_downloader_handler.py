"""Handler + background-orchestrator tests for the video downloader (#42).

The handler and the background task are exercised through a single
synchronous scheduler that runs the spawned coroutine to completion
inline, so each test can assert the full happy-path / failure-path
ordering without juggling real asyncio tasks.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.video_downloader.downloader import (
    DownloadedVideo,
    VideoDownloadError,
    VideoTooLargeError,
)
from something_really_bot.features.video_downloader.handler import (
    VideoDownloaderHandler,
)
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.client import TelegramSendError
from something_really_bot.telegram.models import (
    GroupMessage,
    PrivateMessage,
    TextContent,
    User,
)


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )


def _ctx(persistence: Any = None) -> BotContext:
    return BotContext(settings=_settings(), persistence=persistence)


def _private_message(text: str) -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=TextContent(text=text),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _group_message(text: str) -> GroupMessage:
    return GroupMessage(
        update_id=2,
        message_id=77,
        chat_id=-1001,
        date=1234567890,
        content=TextContent(text=text),
        chat_title="grp",
        from_user=User(id=888, is_bot=False),
    )


@dataclass
class _FakeTelegram:
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    sent_videos: list[dict[str, Any]] = field(default_factory=list)
    edited_messages: list[dict[str, Any]] = field(default_factory=list)
    deleted_messages: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    send_message_raises: BaseException | None = None
    send_video_raises: BaseException | None = None
    edit_message_raises: BaseException | None = None
    delete_message_raises: BaseException | None = None
    reaction_raises: BaseException | None = None
    send_video_message_id: int = 555
    ack_message_id: int = 1

    async def send_message(self, chat_id, text, *, reply_to_message_id=None, parse_mode=None):
        if self.send_message_raises is not None:
            raise self.send_message_raises
        record = {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "parse_mode": parse_mode,
        }
        self.sent_messages.append(record)
        return {"message_id": self.ack_message_id}

    async def edit_message_text(self, chat_id, message_id, text, *, parse_mode=None):
        if self.edit_message_raises is not None:
            raise self.edit_message_raises
        # Mirror Telegram: editing a deleted message returns
        # ``message to edit not found``.
        if any(d["message_id"] == message_id for d in self.deleted_messages):
            from something_really_bot.telegram.client import TelegramSendError as _Err

            raise _Err("editMessageText not ok: message to edit not found")
        self.edited_messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": message_id}

    async def delete_message(self, chat_id, message_id):
        if self.delete_message_raises is not None:
            raise self.delete_message_raises
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return {"ok": True}

    async def send_video(
        self,
        chat_id,
        video_path,
        *,
        caption=None,
        reply_to_message_id=None,
        duration_seconds=None,
        width=None,
        height=None,
        supports_streaming=True,
    ):
        if self.send_video_raises is not None:
            raise self.send_video_raises
        self.sent_videos.append(
            {
                "chat_id": chat_id,
                "path": str(video_path),
                "reply_to_message_id": reply_to_message_id,
                "duration_seconds": duration_seconds,
            }
        )
        return {"message_id": self.send_video_message_id}

    async def set_message_reaction(self, chat_id, message_id, emoji):
        if self.reaction_raises is not None:
            raise self.reaction_raises
        self.reactions.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "emoji": emoji,
            }
        )
        return {"ok": True}


@dataclass
class _FakeGCS:
    uploads: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None
    returned_uri: str = "gs://bucket/video_downloads/100/42/test.mp4"

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
            {
                "id": job_id,
                "error_class": error_class,
                "error_message": error_message,
            }
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


class _InlineScheduler:
    """Runs the scheduled coroutine to completion synchronously."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, coro):
        self.calls.append(coro)
        asyncio.get_event_loop().run_until_complete(coro)


def _build_handler(
    *,
    telegram: _FakeTelegram,
    gcs: _FakeGCS,
    jobs: _FakeJobStorage | None = None,
    download_result: DownloadedVideo | Exception | None = None,
    scheduler=None,
) -> VideoDownloaderHandler:
    async def fake_download(url, *, output_dir):  # type: ignore[no-untyped-def]
        if isinstance(download_result, Exception):
            raise download_result
        assert download_result is not None
        # Materialize a tiny file so cleanup works realistically.
        path = Path(output_dir) / download_result.path.name
        path.write_bytes(b"fake-video-bytes")
        return DownloadedVideo(
            path=path,
            size_bytes=download_result.size_bytes,
            duration_seconds=download_result.duration_seconds,
            width=download_result.width,
            height=download_result.height,
            ext=download_result.ext,
            title=download_result.title,
        )

    # Patch the module-level download with a callable injected via a wrapper
    # subclass so tests don't monkeypatch globally.
    handler = VideoDownloaderHandler(
        scheduler=scheduler or asyncio.create_task,
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: telegram,
        job_storage_factory=lambda: jobs,
    )
    # The handler reaches `download` from the module namespace; patch via
    # attribute on the imported reference.
    from something_really_bot.features.video_downloader import handler as handler_mod

    handler_mod.download = fake_download  # type: ignore[assignment]
    return handler


@pytest.fixture(autouse=True)
def _restore_download():
    """Restore the real ``download`` function after each test."""
    from something_really_bot.features.video_downloader import downloader as dl_mod
    from something_really_bot.features.video_downloader import handler as handler_mod

    original = handler_mod.download
    try:
        yield
    finally:
        handler_mod.download = original  # type: ignore[assignment]
        # Sanity: ensure we didn't permanently rebind the source module.
        assert dl_mod.download is not None


def test_matches_text_with_instagram_url() -> None:
    handler = VideoDownloaderHandler(
        scheduler=lambda c: None,
        gcs_storage_factory=lambda: _FakeGCS(),
        telegram_client_factory=lambda: _FakeTelegram(),
        job_storage_factory=lambda: None,
    )
    assert handler.matches(
        _private_message("look https://www.instagram.com/reel/CxYzAbC1234/"),
        _ctx(),
    )


def test_does_not_match_plain_text() -> None:
    handler = VideoDownloaderHandler(
        scheduler=lambda c: None,
        gcs_storage_factory=lambda: _FakeGCS(),
        telegram_client_factory=lambda: _FakeTelegram(),
        job_storage_factory=lambda: None,
    )
    assert not handler.matches(_private_message("hello there"), _ctx())


def test_does_not_match_command_content() -> None:
    handler = VideoDownloaderHandler(
        scheduler=lambda c: None,
        gcs_storage_factory=lambda: _FakeGCS(),
        telegram_client_factory=lambda: _FakeTelegram(),
        job_storage_factory=lambda: None,
    )
    # Command content is not text content, so detect() never runs.
    from something_really_bot.telegram.models import CommandContent

    msg = PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=CommandContent(
            command="start",
            text="/start https://www.instagram.com/reel/CxYzAbC1234/",
        ),
        from_user=User(id=999, is_bot=False),
    )
    assert not handler.matches(msg, _ctx())


async def test_handle_happy_path_dm() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    jobs = _FakeJobStorage()
    persistence = _RecordingPersistence()

    scheduled: list[Any] = []

    def collecting_scheduler(coro):
        scheduled.append(coro)

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=jobs,
        download_result=DownloadedVideo(
            path=Path("ignored.mp4"),
            size_bytes=12_345_678,
            duration_seconds=14.5,
            width=720,
            height=1280,
            ext="mp4",
            title="cool clip",
        ),
        scheduler=collecting_scheduler,
    )

    result = await handler.handle(
        _private_message("https://www.instagram.com/reel/CxYzAbC1234/"),
        _ctx(persistence=persistence),
    )

    assert result.handled is True
    # Ack happened synchronously, pinned to the trigger message, in italics.
    assert len(telegram.sent_messages) == 1
    ack = telegram.sent_messages[0]
    assert ack["chat_id"] == 100
    assert ack["reply_to_message_id"] == 42
    assert "video" in ack["text"].lower()
    assert ack["text"].startswith("<i>") and ack["text"].endswith("</i>")
    assert ack["parse_mode"] == "HTML"
    assert telegram.reactions == [{"chat_id": 100, "message_id": 42, "emoji": "👀"}]
    assert len(scheduled) == 1

    # Run the scheduled background task to completion and assert the rest.
    await scheduled[0]

    assert len(gcs.uploads) == 1
    upload = gcs.uploads[0]
    assert upload["object_key"].startswith("video_downloads/100/42/")
    assert upload["content_type"] == "video/mp4"
    assert upload["data_len"] == len(b"fake-video-bytes")

    assert len(telegram.sent_videos) == 1
    video = telegram.sent_videos[0]
    assert video["chat_id"] == 100
    assert video["reply_to_message_id"] == 42
    assert video["duration_seconds"] == 14
    # Ack was deleted right before the video send so the user only sees
    # the video, not a stale "fetching…" message above it.
    assert telegram.deleted_messages == [{"chat_id": 100, "message_id": 1}]

    assert jobs.inserted[0].source_url == "https://www.instagram.com/reel/CxYzAbC1234/"
    assert "downloading" in [s for _, s in jobs.status_history]
    assert "uploading" in [s for _, s in jobs.status_history]
    assert "sending" in [s for _, s in jobs.status_history]
    assert len(jobs.succeeded) == 1
    assert jobs.succeeded[0]["telegram_video_message_id"] == 555

    assert any(e.event == "video_download_succeeded" for e in persistence.events)


async def test_handle_group_uses_supergroup_pattern() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    scheduled: list[Any] = []

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=None,
        download_result=DownloadedVideo(
            path=Path("clip.mp4"),
            size_bytes=1_000_000,
            duration_seconds=8.0,
            width=720,
            height=1280,
            ext="mp4",
            title=None,
        ),
        scheduler=scheduled.append,
    )

    await handler.handle(
        _group_message("https://www.tiktok.com/@user/video/123"),
        _ctx(),
    )

    assert "video" in telegram.sent_messages[0]["text"].lower()
    assert telegram.sent_messages[0]["parse_mode"] == "HTML"
    await scheduled[0]

    assert telegram.sent_videos[0]["chat_id"] == -1001
    assert telegram.sent_videos[0]["reply_to_message_id"] == 77


async def test_handle_download_failure_replies_with_platform_specific_message() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    jobs = _FakeJobStorage()
    persistence = _RecordingPersistence()
    scheduled: list[Any] = []

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=jobs,
        download_result=VideoDownloadError("TikTok said no"),
        scheduler=scheduled.append,
    )

    await handler.handle(
        _private_message("https://www.tiktok.com/@user/video/123"),
        _ctx(persistence=persistence),
    )
    await scheduled[0]

    # Only the ack was ever sent; the error edits it in place.
    assert len(telegram.sent_messages) == 1
    assert len(telegram.edited_messages) == 1
    error_msg = telegram.edited_messages[0]["text"]
    assert "TikTok" in error_msg
    assert "rate-limiting" in error_msg
    assert telegram.sent_videos == []
    assert jobs.failed[0]["error_class"] == "VideoDownloadError"
    assert any(e.event == "video_download_failed" for e in persistence.events)


async def test_handle_too_large_uses_clean_user_message() -> None:
    telegram = _FakeTelegram()
    gcs = _FakeGCS()
    scheduled: list[Any] = []

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=None,
        download_result=VideoTooLargeError("video is 80.0 MiB; Telegram cap is 50 MiB"),
        scheduler=scheduled.append,
    )

    await handler.handle(
        _private_message("https://www.instagram.com/reel/CxYzAbC1234/"),
        _ctx(),
    )
    await scheduled[0]

    assert telegram.sent_videos == []
    assert "50 MB" in telegram.edited_messages[0]["text"]


async def test_handle_send_video_failure_reports_to_user() -> None:
    telegram = _FakeTelegram(send_video_raises=TelegramSendError("nope"))
    gcs = _FakeGCS()
    scheduled: list[Any] = []

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=None,
        download_result=DownloadedVideo(
            path=Path("clip.mp4"),
            size_bytes=500_000,
            duration_seconds=5.0,
            width=720,
            height=1280,
            ext="mp4",
            title=None,
        ),
        scheduler=scheduled.append,
    )

    await handler.handle(
        _private_message("https://www.instagram.com/reel/CxYzAbC1234/"),
        _ctx(),
    )
    await scheduled[0]

    # GCS upload should have happened before the send failure.
    assert len(gcs.uploads) == 1
    # Final user message is the send-failure copy. The send-video step
    # first deletes the ack, so the error path falls back to a fresh
    # send_message — index 1 (after the ack) — rather than editing.
    assert "Downloaded the video" in telegram.sent_messages[1]["text"]
    assert len(telegram.deleted_messages) == 1


async def test_handle_swallows_ack_failure() -> None:
    telegram = _FakeTelegram(send_message_raises=TelegramSendError("flaky"))
    gcs = _FakeGCS()
    scheduled: list[Any] = []

    handler = _build_handler(
        telegram=telegram,
        gcs=gcs,
        jobs=None,
        download_result=DownloadedVideo(
            path=Path("clip.mp4"),
            size_bytes=500_000,
            duration_seconds=5.0,
            width=720,
            height=1280,
            ext="mp4",
            title=None,
        ),
        scheduler=scheduled.append,
    )

    # The handler must not raise even though the ack send_message fails.
    result = await handler.handle(
        _private_message("https://www.instagram.com/reel/CxYzAbC1234/"),
        _ctx(),
    )
    assert result.handled is True
    # No exception inside the background task either.
    await scheduled[0]
