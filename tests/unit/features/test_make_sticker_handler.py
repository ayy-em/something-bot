"""Tests for the /make-sticker command (#44)."""

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.make_sticker.handler import (
    COMMAND_NAME,
    PROMPT_TEXT,
    MakeStickerHandler,
)
from something_really_bot.features.make_sticker.transform import (
    MAX_DIMENSION,
    StickerTransformError,
    transform,
)
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.services.pending_actions import PendingAction
from something_really_bot.telegram.client import TelegramFileError, TelegramSendError
from something_really_bot.telegram.models import (
    CommandContent,
    PhotoContent,
    PhotoSize,
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


def _ctx(
    pending_action: PendingAction | None = None,
    persistence: Any = None,
) -> BotContext:
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
        content=CommandContent(command=COMMAND_NAME, text=f"/{COMMAND_NAME}", args=None),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )


def _photo_message() -> PrivateMessage:
    return PrivateMessage(
        update_id=2,
        message_id=43,
        chat_id=100,
        date=1234567891,
        content=PhotoContent(
            photo=[
                PhotoSize(file_id="sm", file_unique_id="sm-u", width=64, height=64, file_size=100),
                PhotoSize(
                    file_id="big", file_unique_id="big-u", width=512, height=512, file_size=9000
                ),
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
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    sent_documents: list[dict[str, Any]] = field(default_factory=list)
    file_path_to_return: str = "photo/x.jpg"
    download_bytes: bytes = b""
    get_file_raises: BaseException | None = None
    download_raises: BaseException | None = None
    send_message_raises: BaseException | None = None
    send_document_raises: BaseException | None = None

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
        return {"message_id": 1}

    async def send_document(
        self,
        chat_id,
        document_bytes,
        *,
        filename,
        mime_type="application/octet-stream",
        caption=None,
        reply_to_message_id=None,
    ):
        if self.send_document_raises is not None:
            raise self.send_document_raises
        self.sent_documents.append(
            {
                "chat_id": chat_id,
                "bytes_len": len(document_bytes),
                "filename": filename,
                "mime_type": mime_type,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"message_id": 2}

    async def get_file_path(self, file_id):
        if self.get_file_raises is not None:
            raise self.get_file_raises
        return self.file_path_to_return

    async def download_file(self, file_path):
        if self.download_raises is not None:
            raise self.download_raises
        return self.download_bytes


@dataclass
class _FakeGCS:
    uploads: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None

    async def upload(self, *, object_key, data, content_type=None):
        if self.raises is not None:
            raise self.raises
        self.uploads.append(
            {"object_key": object_key, "data_len": len(data), "content_type": content_type}
        )
        return f"gs://bucket/{object_key}"


@dataclass
class _FakePendingStore:
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

    def record_event(self, record: EventRecord) -> None:
        self.events.append(record)


def _build_handler(
    *,
    telegram: _FakeTelegram | None = None,
    gcs: _FakeGCS | None = None,
    pending: _FakePendingStore | None = None,
    scheduler=None,
):
    tg = telegram or _FakeTelegram()
    g = gcs or _FakeGCS()
    p = pending or _FakePendingStore()
    handler = MakeStickerHandler(
        scheduler=scheduler or (lambda c: c.close()),
        gcs_storage_factory=lambda: g,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: p,
    )
    return handler, tg, g, p


def _png_bytes(*, width: int = 200, height: int = 100, with_alpha: bool = True) -> bytes:
    img = Image.new("RGBA" if with_alpha else "RGB", (width, height), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------- Transform unit tests ---------------- #


async def test_transform_resizes_landscape_to_512_max_edge() -> None:
    source = _png_bytes(width=1024, height=600)
    result = await transform(source)
    assert max(result.width, result.height) == MAX_DIMENSION
    assert (result.width / result.height) == (1024 / 600)


async def test_transform_does_not_upscale_small_images() -> None:
    source = _png_bytes(width=64, height=32)
    result = await transform(source)
    assert result.width == 64
    assert result.height == 32


async def test_transform_preserves_alpha() -> None:
    source = _png_bytes(width=128, height=128, with_alpha=True)
    result = await transform(source)
    out = Image.open(io.BytesIO(result.png_bytes))
    assert out.mode == "RGBA"


async def test_transform_rejects_non_image_bytes() -> None:
    import pytest

    with pytest.raises(StickerTransformError):
        await transform(b"not an image")


# ---------------- Handler tests ---------------- #


def test_matches_command_in_private() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_command(), _ctx()) is True


def test_matches_photo_with_pending_action() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(), _ctx(_pending())) is True


def test_does_not_match_photo_without_pending() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(), _ctx()) is False


def test_does_not_match_photo_with_different_pending() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(), _ctx(_pending("dutch"))) is False


def test_does_not_match_text_with_pending_image() -> None:
    handler, *_ = _build_handler()
    update = PrivateMessage(
        update_id=3,
        message_id=44,
        chat_id=100,
        date=1234567892,
        content=TextContent(text="hi"),
        from_user=User(id=999, is_bot=False, first_name="t"),
    )
    assert handler.matches(update, _ctx(_pending())) is False


async def test_command_sets_pending_and_prompts() -> None:
    handler, tg, _, pending = _build_handler()

    await handler.handle(_command(), _ctx())

    assert len(pending.set_calls) == 1
    assert pending.set_calls[0]["command"] == COMMAND_NAME
    assert pending.set_calls[0]["expected_input"] == "image"
    assert tg.sent_messages[0]["text"] == PROMPT_TEXT


async def test_photo_followup_happy_path() -> None:
    persistence = _RecordingPersistence()
    handler, tg, gcs, pending = _build_handler(
        telegram=_FakeTelegram(download_bytes=_png_bytes(width=800, height=600)),
        scheduler=lambda c: scheduled.append(c),
    )
    scheduled: list[Any] = []
    # Rebuild now that 'scheduled' exists (the lambda captures it).
    handler = MakeStickerHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: pending,
    )

    await handler.handle(_photo_message(), _ctx(_pending(), persistence=persistence))
    assert len(scheduled) == 1
    await scheduled[0]

    # ack + final document
    assert len(tg.sent_messages) == 1
    assert "Working on your sticker" in tg.sent_messages[0]["text"]
    assert len(tg.sent_documents) == 1
    doc = tg.sent_documents[0]
    assert doc["mime_type"] == "image/png"
    assert doc["filename"].endswith(".png")
    assert doc["reply_to_message_id"] == 43

    # Two GCS uploads: input + output
    assert len(gcs.uploads) == 2
    input_upload, output_upload = gcs.uploads
    assert input_upload["object_key"].startswith("sticker_requests/100/43/")
    assert output_upload["object_key"].startswith("sticker_outputs/100/43/")
    assert output_upload["content_type"] == "image/png"

    # Picked the larger photo size
    # (handler used the bigger photo's file_unique_id "big-u")
    assert "big-u" in output_upload["object_key"]

    # Pending cleared, persistence event emitted
    assert len(pending.clear_calls) == 1
    assert any(e.event == "make_sticker_succeeded" for e in persistence.events)


async def test_photo_download_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    tg = _FakeTelegram(get_file_raises=TelegramFileError("getFile not ok"))
    gcs = _FakeGCS()
    pending = _FakePendingStore()
    handler = MakeStickerHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: pending,
    )

    await handler.handle(_photo_message(), _ctx(_pending()))
    await scheduled[0]

    # ack + error reply
    assert len(tg.sent_messages) == 2
    assert "pull that image" in tg.sent_messages[1]["text"]
    assert not gcs.uploads
    assert not tg.sent_documents


async def test_photo_transform_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    tg = _FakeTelegram(download_bytes=b"not a real image")
    gcs = _FakeGCS()
    pending = _FakePendingStore()
    handler = MakeStickerHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: pending,
    )

    await handler.handle(_photo_message(), _ctx(_pending()))
    await scheduled[0]

    # Input upload still happened, but transform failed → user error
    assert len(gcs.uploads) == 1
    assert gcs.uploads[0]["object_key"].startswith("sticker_requests/")
    assert "Couldn't turn that into a sticker" in tg.sent_messages[1]["text"]
    assert not tg.sent_documents


async def test_send_document_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    tg = _FakeTelegram(
        download_bytes=_png_bytes(width=200, height=200),
        send_document_raises=TelegramSendError("rejected"),
    )
    gcs = _FakeGCS()
    pending = _FakePendingStore()
    handler = MakeStickerHandler(
        scheduler=lambda c: scheduled.append(c),
        gcs_storage_factory=lambda: gcs,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: pending,
    )

    await handler.handle(_photo_message(), _ctx(_pending()))
    await scheduled[0]

    # Both uploads happened, but the send failed → generic error reply
    assert len(gcs.uploads) == 2
    assert "Something went wrong" in tg.sent_messages[1]["text"]
