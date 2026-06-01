"""Tests for the /ocr command (#45)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.ocr.handler import (
    COMMAND_NAME,
    PROMPT_TEXT,
    OCRHandler,
)
from something_really_bot.features.ocr.ocr_client import OCRError
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.services.pending_actions import PendingAction
from something_really_bot.telegram.client import TelegramFileError, TelegramSendError
from something_really_bot.telegram.models import (
    CommandContent,
    PhotoContent,
    PhotoSize,
    PrivateMessage,
    User,
)


def _settings() -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )


def _ctx(pending_action: PendingAction | None = None, persistence: Any = None) -> BotContext:
    return BotContext(
        settings=_settings(),
        pending_action=pending_action,
        persistence=persistence,
    )


def _command() -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=CommandContent(command=COMMAND_NAME, text=COMMAND_NAME, args=None),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _photo() -> PrivateMessage:
    return PrivateMessage(
        update_id=2,
        message_id=43,
        chat_id=100,
        date=1234567891,
        content=PhotoContent(
            photo=[
                PhotoSize(file_id="big", file_unique_id="bu", width=512, height=512, file_size=5000)
            ],
        ),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _pending(command: str = COMMAND_NAME) -> PendingAction:
    now = datetime.now(UTC)
    return PendingAction(
        bot_id="default",
        chat_id=100,
        user_id=999,
        command=command,
        expected_input="image",
        metadata={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )


@dataclass
class _FakeTelegram:
    sent: list[dict[str, Any]] = field(default_factory=list)
    download_bytes: bytes = b"fake-image-bytes"
    get_file_raises: BaseException | None = None
    download_raises: BaseException | None = None
    send_raises: BaseException | None = None

    async def send_message(self, chat_id, text, *, reply_to_message_id=None, parse_mode=None):
        if self.send_raises is not None:
            raise self.send_raises
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": 1}

    async def get_file_path(self, file_id):
        if self.get_file_raises is not None:
            raise self.get_file_raises
        return "image/x.jpg"

    async def download_file(self, file_path):
        if self.download_raises is not None:
            raise self.download_raises
        return self.download_bytes


@dataclass
class _FakeGCS:
    uploads: list[dict[str, Any]] = field(default_factory=list)

    async def upload(self, *, object_key, data, content_type=None):
        self.uploads.append(
            {"object_key": object_key, "data_len": len(data), "content_type": content_type}
        )
        return f"gs://bucket/{object_key}"


@dataclass
class _FakeOCR:
    return_text: str = "Hello world"
    raises: BaseException | None = None
    calls: list[bytes] = field(default_factory=list)

    async def extract_text(self, image_bytes, *, mime_type="image/jpeg"):  # noqa: ARG002
        if self.raises is not None:
            raise self.raises
        self.calls.append(image_bytes)
        return self.return_text


@dataclass
class _FakePending:
    set_calls: list[dict[str, Any]] = field(default_factory=list)
    clear_calls: list[dict[str, Any]] = field(default_factory=list)

    async def set(self, **kwargs):
        self.set_calls.append(kwargs)

    async def clear(self, **kwargs):
        self.clear_calls.append(kwargs)


@dataclass
class _RecordingPersistence:
    events: list[EventRecord] = field(default_factory=list)

    def record_raw_update(self, _r): ...
    def record_message(self, _r): ...
    def record_file(self, _r): ...
    def record_response(self, _r): ...

    def record_event(self, record):
        self.events.append(record)


def _build_handler(
    *,
    telegram: _FakeTelegram | None = None,
    gcs: _FakeGCS | None = None,
    ocr: _FakeOCR | None = None,
    pending: _FakePending | None = None,
    scheduler=None,
):
    tg = telegram or _FakeTelegram()
    g = gcs or _FakeGCS()
    o = ocr if ocr is not None else _FakeOCR()
    p = pending or _FakePending()
    handler = OCRHandler(
        scheduler=scheduler or (lambda c: c.close()),
        gcs_storage_factory=lambda: g,
        telegram_client_factory=lambda: tg,
        ocr_client_factory=lambda: o,
        pending_action_store_factory=lambda: p,
    )
    return handler, tg, g, o, p


def test_matches_command() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_command(), _ctx()) is True


def test_matches_photo_with_pending_ocr() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo(), _ctx(_pending())) is True


def test_does_not_match_photo_without_pending() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo(), _ctx()) is False


def test_does_not_match_photo_with_other_pending() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo(), _ctx(_pending("/make_sticker"))) is False


async def test_command_sets_pending_and_prompts() -> None:
    handler, tg, _, _, pending = _build_handler()
    await handler.handle(_command(), _ctx())
    assert pending.set_calls[0]["command"] == COMMAND_NAME
    assert pending.set_calls[0]["expected_input"] == "image"
    assert tg.sent[0]["text"] == PROMPT_TEXT


async def test_photo_happy_path() -> None:
    scheduled: list[Any] = []
    persistence = _RecordingPersistence()
    handler, tg, gcs, ocr, pending = _build_handler(
        ocr=_FakeOCR(return_text="HELLO WORLD"),
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_photo(), _ctx(_pending(), persistence=persistence))
    await scheduled[0]

    # ack + final OCR reply
    assert len(tg.sent) == 2
    assert tg.sent[0]["text"] == "Reading the text…"
    assert "<i>HELLO WORLD</i>" in tg.sent[1]["text"]
    assert tg.sent[1]["parse_mode"] == "HTML"

    # GCS upload
    assert len(gcs.uploads) == 1
    assert gcs.uploads[0]["object_key"].startswith("ocr_requests/100/43/image_bu")

    # OCR was called with the downloaded bytes
    assert ocr.calls == [b"fake-image-bytes"]

    # Pending cleared, success event emitted
    assert len(pending.clear_calls) == 1
    assert any(e.event == "ocr_succeeded" for e in persistence.events)


async def test_no_text_translates_to_friendly_reply() -> None:
    scheduled: list[Any] = []
    handler, tg, *_ = _build_handler(
        ocr=_FakeOCR(return_text="NO_TEXT"),
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_photo(), _ctx(_pending()))
    await scheduled[0]

    assert "couldn't find any readable text" in tg.sent[1]["text"]
    # No italics — friendly fallback is plain.
    assert tg.sent[1]["parse_mode"] is None


async def test_download_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    tg = _FakeTelegram(get_file_raises=TelegramFileError("not ok"))
    handler, _, gcs, ocr, _ = _build_handler(telegram=tg, scheduler=lambda c: scheduled.append(c))

    await handler.handle(_photo(), _ctx(_pending()))
    await scheduled[0]

    assert len(tg.sent) == 2
    assert "pull that image" in tg.sent[1]["text"]
    assert not gcs.uploads
    assert ocr.calls == []


async def test_ocr_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    handler, tg, gcs, _, _ = _build_handler(
        ocr=_FakeOCR(raises=OCRError("boom")),
        scheduler=lambda c: scheduled.append(c),
    )

    await handler.handle(_photo(), _ctx(_pending()))
    await scheduled[0]

    assert "OCR service" in tg.sent[1]["text"]
    # Upload still happened before OCR failed.
    assert len(gcs.uploads) == 1


async def test_missing_ocr_client_replies_unavailable() -> None:
    scheduled: list[Any] = []
    handler = OCRHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: _FakeGCS(),
        telegram_client_factory=lambda: _FakeTelegram(),
        ocr_client_factory=lambda: None,
        pending_action_store_factory=lambda: _FakePending(),
    )

    tg = _FakeTelegram()
    handler = OCRHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: _FakeGCS(),
        telegram_client_factory=lambda: tg,
        ocr_client_factory=lambda: None,
        pending_action_store_factory=lambda: _FakePending(),
    )

    await handler.handle(_photo(), _ctx(_pending()))
    await scheduled[0]

    assert "isn't configured" in tg.sent[1]["text"]


async def test_send_failure_during_ack_is_swallowed() -> None:
    scheduled: list[Any] = []
    handler, _, _, _, _ = _build_handler(
        telegram=_FakeTelegram(send_raises=TelegramSendError("nope")),
        scheduler=lambda c: scheduled.append(c),
    )

    # Should not raise.
    await handler.handle(_photo(), _ctx(_pending()))
    # Drain the queued background task so pytest doesn't warn about a
    # never-awaited coroutine.
    if scheduled:
        await scheduled[0]
