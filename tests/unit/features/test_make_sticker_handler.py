"""Tests for the /make_sticker command (#44)."""

import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
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
    OUTLINE_PX,
    REMBG_MODEL,
    NoSubjectError,
    StickerTransformError,
    transform,
)
from something_really_bot.persistence import EventRecord
from something_really_bot.routing.types import BotContext
from something_really_bot.services.pending_actions import PendingAction
from something_really_bot.telegram.client import TelegramFileError, TelegramSendError
from something_really_bot.telegram.models import (
    CommandContent,
    Document,
    DocumentContent,
    GroupMessage,
    MessageContent,
    PhotoContent,
    PhotoSize,
    PrivateMessage,
    RepliedMessage,
    SupergroupMessage,
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


_USER = User(id=999, is_bot=False, first_name="t")


def _command(*, reply_to: RepliedMessage | None = None) -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=42,
        chat_id=100,
        date=1234567890,
        content=CommandContent(command=COMMAND_NAME, text=COMMAND_NAME, args=None),
        from_user=_USER,
        reply_to=reply_to,
    )


def _photo_content(caption: str | None = None) -> PhotoContent:
    return PhotoContent(
        photo=[
            PhotoSize(file_id="sm", file_unique_id="sm-u", width=64, height=64, file_size=100),
            PhotoSize(file_id="big", file_unique_id="big-u", width=512, height=512, file_size=9000),
        ],
        caption=caption,
    )


def _document_content(mime_type: str, caption: str | None = None) -> DocumentContent:
    return DocumentContent(
        document=Document(
            file_id="doc", file_unique_id="doc-u", file_name="x.png", mime_type=mime_type
        ),
        caption=caption,
    )


def _photo_message(caption: str | None = None) -> PrivateMessage:
    return PrivateMessage(
        update_id=2,
        message_id=43,
        chat_id=100,
        date=1234567891,
        content=_photo_content(caption),
        from_user=_USER,
    )


def _group_message(
    content: MessageContent, *, reply_to: RepliedMessage | None = None
) -> GroupMessage:
    return GroupMessage(
        update_id=4,
        message_id=50,
        chat_id=-200,
        date=1234567893,
        content=content,
        from_user=_USER,
        reply_to=reply_to,
    )


def _pending(command: str = COMMAND_NAME, chat_id: int = 100) -> PendingAction:
    now = datetime.now(UTC)
    return PendingAction(
        bot_id="default",
        chat_id=chat_id,
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


def _keep_everything(image: Image.Image) -> Image.Image:
    """Stand-in for rembg: treats the whole frame as foreground."""
    return image


def _centre_square(image: Image.Image) -> Image.Image:
    """Stand-in for rembg: keeps an opaque red square in the middle, drops the rest."""
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    w, h = image.size
    out.paste((255, 0, 0, 255), (w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    return out


def _nothing(image: Image.Image) -> Image.Image:
    return Image.new("RGBA", image.size, (0, 0, 0, 0))


def _build_handler(
    *,
    telegram: _FakeTelegram | None = None,
    gcs: _FakeGCS | None = None,
    pending: _FakePendingStore | None = None,
    scheduler=None,
    background_remover=_keep_everything,
):
    tg = telegram or _FakeTelegram()
    g = gcs or _FakeGCS()
    p = pending or _FakePendingStore()
    handler = MakeStickerHandler(
        scheduler=scheduler or (lambda c: c.close()),
        gcs_storage_factory=lambda: g,
        telegram_client_factory=lambda: tg,
        pending_action_store_factory=lambda: p,
        background_remover=background_remover,
    )
    return handler, tg, g, p


def _png_bytes(*, width: int = 200, height: int = 100, with_alpha: bool = True) -> bytes:
    img = Image.new("RGBA" if with_alpha else "RGB", (width, height), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


# ---------------- Transform unit tests ---------------- #


async def test_transform_downscales_so_outlined_result_is_512_on_long_edge() -> None:
    result = await transform(_png_bytes(width=1024, height=600), remove_background=_keep_everything)
    assert (result.width, result.height) == (MAX_DIMENSION, 295 + 2 * OUTLINE_PX)
    assert _decode(result.png_bytes).size == (result.width, result.height)


async def test_transform_upscales_small_images() -> None:
    result = await transform(_png_bytes(width=64, height=32), remove_background=_keep_everything)
    assert (result.width, result.height) == (MAX_DIMENSION, 252 + 2 * OUTLINE_PX)


async def test_transform_hands_the_fitted_image_to_background_removal() -> None:
    seen: list[tuple[int, int]] = []

    def _spy(image: Image.Image) -> Image.Image:
        seen.append(image.size)
        return image

    await transform(_png_bytes(width=300, height=600), remove_background=_spy)
    assert seen == [(252, MAX_DIMENSION - 2 * OUTLINE_PX)]


async def test_transform_draws_white_outline_around_the_cutout() -> None:
    result = await transform(_png_bytes(width=400, height=400), remove_background=_centre_square)
    out = _decode(result.png_bytes).convert("RGBA")
    assert out.size == (MAX_DIMENSION, MAX_DIMENSION)
    # The fitted 504 px frame sits at offset OUTLINE_PX; the square spans
    # its middle half, i.e. [126, 378) inside it.
    left_edge = OUTLINE_PX + 126
    mid = MAX_DIMENSION // 2
    assert out.getpixel((mid, mid)) == (255, 0, 0, 255)
    assert out.getpixel((left_edge - 2, mid)) == (255, 255, 255, 255)
    # Outermost ring is anti-aliased: mostly opaque white, not a hard step.
    assert out.getpixel((left_edge - OUTLINE_PX, mid))[:3] == (255, 255, 255)
    assert 128 < out.getpixel((left_edge - OUTLINE_PX, mid))[3] < 255
    assert out.getpixel((left_edge - OUTLINE_PX - 2, mid))[3] < 16
    assert out.getpixel((left_edge - OUTLINE_PX - 4, mid))[3] == 0
    # Round brush: the diagonal corner is not filled out to a full square.
    corner = (left_edge - OUTLINE_PX, OUTLINE_PX + 126 - OUTLINE_PX)
    assert out.getpixel(corner)[3] < 64


async def test_transform_outline_is_crisp_under_a_feathered_mask() -> None:
    def _half_transparent_square(image: Image.Image) -> Image.Image:
        out = _centre_square(image)
        out.putalpha(out.getchannel("A").point(lambda a: a // 2 + 1 if a else 0))
        return out

    result = await transform(
        _png_bytes(width=400, height=400), remove_background=_half_transparent_square
    )
    out = _decode(result.png_bytes).convert("RGBA")
    # Subject alpha is ~128, but the border underneath is solid white.
    assert out.getpixel((OUTLINE_PX + 126 - 2, MAX_DIMENSION // 2)) == (255, 255, 255, 255)


async def test_transform_outline_reaches_the_frame_edge_for_full_bleed_subjects() -> None:
    opaque = _png_bytes(width=400, height=400, with_alpha=False)
    result = await transform(opaque, remove_background=_keep_everything)
    out = _decode(result.png_bytes).convert("RGBA")
    assert out.getpixel((0, MAX_DIMENSION // 2)) == (255, 255, 255, 255)
    assert out.getpixel((OUTLINE_PX, MAX_DIMENSION // 2)) == (255, 0, 0, 255)


async def test_transform_applies_exif_orientation() -> None:
    img = Image.new("RGB", (200, 100), (0, 128, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    result = await transform(buf.getvalue(), remove_background=_keep_everything)
    assert result.height == MAX_DIMENSION
    assert result.width < result.height


async def test_transform_raises_no_subject_when_cutout_is_empty() -> None:
    with pytest.raises(NoSubjectError):
        await transform(_png_bytes(), remove_background=_nothing)


async def test_transform_rejects_non_image_bytes() -> None:
    with pytest.raises(StickerTransformError):
        await transform(b"not an image", remove_background=_keep_everything)


def test_dockerfile_bakes_the_pinned_rembg_model() -> None:
    dockerfile = Path(__file__).resolve().parents[3] / "Dockerfile"
    baked = re.findall(r"rembg\.sessions\.(\w+) import", dockerfile.read_text())
    assert baked == [REMBG_MODEL]


# ---------------- Matching ---------------- #


def test_matches_command_in_private() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_command(), _ctx()) is True


def test_matches_command_in_group_and_supergroup() -> None:
    handler, *_ = _build_handler()
    command = CommandContent(command=COMMAND_NAME, text=COMMAND_NAME)
    supergroup = SupergroupMessage(
        update_id=5, message_id=51, chat_id=-300, date=1, content=command, from_user=_USER
    )
    assert handler.matches(_group_message(command), _ctx()) is True
    assert handler.matches(supergroup, _ctx()) is True


def test_matches_photo_with_pending_action() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(), _ctx(_pending())) is True


def test_matches_group_photo_with_pending_action() -> None:
    handler, *_ = _build_handler()
    update = _group_message(_photo_content())
    assert handler.matches(update, _ctx(_pending(chat_id=-200))) is True


def test_does_not_match_photo_without_pending() -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(), _ctx()) is False
    assert handler.matches(_group_message(_photo_content()), _ctx()) is False


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
        from_user=_USER,
    )
    assert handler.matches(update, _ctx(_pending())) is False


@pytest.mark.parametrize(
    "caption",
    ["/make_sticker", "/make-sticker", "/make_sticker@SomethingBot", "  /make_sticker please"],
)
def test_matches_photo_captioned_with_command_without_pending(caption: str) -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(caption), _ctx()) is True
    assert handler.matches(_group_message(_photo_content(caption)), _ctx()) is True


@pytest.mark.parametrize("caption", ["look at this", "/ocr", "make_sticker", "/make_stickers"])
def test_does_not_match_photo_with_other_caption(caption: str) -> None:
    handler, *_ = _build_handler()
    assert handler.matches(_photo_message(caption), _ctx()) is False


def test_matches_image_document_with_caption_or_pending() -> None:
    handler, *_ = _build_handler()
    captioned = _group_message(_document_content("image/png", caption=COMMAND_NAME))
    uncaptioned = _group_message(_document_content("image/webp"))
    assert handler.matches(captioned, _ctx()) is True
    assert handler.matches(uncaptioned, _ctx(_pending(chat_id=-200))) is True


@pytest.mark.parametrize("mime_type", ["application/pdf", "image/svg+xml", None])
def test_does_not_match_non_raster_documents(mime_type: str | None) -> None:
    handler, *_ = _build_handler()
    content = DocumentContent(
        document=Document(file_id="d", file_unique_id="d-u", mime_type=mime_type),
        caption=COMMAND_NAME,
    )
    assert handler.matches(_group_message(content), _ctx()) is False


def test_matches_command_replying_to_photo() -> None:
    handler, *_ = _build_handler()
    reply_to = RepliedMessage(message_id=10, content=_photo_content())
    assert handler.matches(_group_message(_command().content, reply_to=reply_to), _ctx()) is True


# ---------------- Prompt ---------------- #


async def test_command_sets_pending_and_prompts() -> None:
    handler, tg, _, pending = _build_handler()

    await handler.handle(_command(), _ctx())

    assert len(pending.set_calls) == 1
    assert pending.set_calls[0]["command"] == COMMAND_NAME
    assert pending.set_calls[0]["expected_input"] == "image"
    assert tg.sent_messages[0]["text"] == PROMPT_TEXT


async def test_group_command_sets_pending_for_that_chat_and_user() -> None:
    handler, tg, _, pending = _build_handler()

    await handler.handle(_group_message(_command().content), _ctx())

    assert pending.set_calls[0]["chat_id"] == -200
    assert pending.set_calls[0]["user_id"] == _USER.id
    assert tg.sent_messages[0]["reply_to_message_id"] == 50


async def test_command_replying_to_non_image_prompts() -> None:
    handler, tg, _, pending = _build_handler()
    reply_to = RepliedMessage(message_id=10, content=TextContent(text="hi"))

    await handler.handle(_command(reply_to=reply_to), _ctx())

    assert len(pending.set_calls) == 1
    assert tg.sent_messages[0]["text"] == PROMPT_TEXT


# ---------------- Processing ---------------- #


async def test_photo_followup_happy_path() -> None:
    persistence = _RecordingPersistence()
    scheduled: list[Any] = []
    handler, tg, gcs, pending = _build_handler(
        telegram=_FakeTelegram(download_bytes=_png_bytes(width=800, height=600)),
        scheduler=scheduled.append,
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
    assert input_upload["content_type"] == "image/jpeg"
    assert output_upload["object_key"].startswith("sticker_outputs/100/43/")
    assert output_upload["content_type"] == "image/png"

    # Picked the larger photo size
    assert "big-u" in output_upload["object_key"]

    # Pending cleared, persistence event emitted
    assert len(pending.clear_calls) == 1
    assert any(e.event == "make_sticker_succeeded" for e in persistence.events)


async def test_captioned_group_photo_is_processed_without_touching_pending_state() -> None:
    scheduled: list[Any] = []
    handler, tg, gcs, pending = _build_handler(
        telegram=_FakeTelegram(download_bytes=_png_bytes()),
        scheduler=scheduled.append,
    )

    await handler.handle(_group_message(_photo_content(COMMAND_NAME)), _ctx())
    await scheduled[0]

    assert not pending.set_calls
    assert not pending.clear_calls
    assert tg.sent_documents[0]["chat_id"] == -200
    assert tg.sent_documents[0]["reply_to_message_id"] == 50
    assert gcs.uploads[0]["object_key"].startswith("sticker_requests/-200/50/input_big-u")


async def test_command_reply_uses_the_replied_image_document() -> None:
    scheduled: list[Any] = []
    handler, tg, gcs, pending = _build_handler(
        telegram=_FakeTelegram(download_bytes=_png_bytes()),
        scheduler=scheduled.append,
    )
    fetched: list[str] = []
    original_get_file_path = tg.get_file_path

    async def _record_file_id(file_id):
        fetched.append(file_id)
        return await original_get_file_path(file_id)

    tg.get_file_path = _record_file_id  # type: ignore[method-assign]
    reply_to = RepliedMessage(message_id=10, content=_document_content("image/png"))

    await handler.handle(_group_message(_command().content, reply_to=reply_to), _ctx())
    await scheduled[0]

    assert fetched == ["doc"]
    assert not pending.set_calls
    assert gcs.uploads[0]["content_type"] == "image/png"
    assert gcs.uploads[1]["object_key"] == "sticker_outputs/-200/50/sticker_doc-u.png"
    assert len(tg.sent_documents) == 1


async def test_photo_download_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    handler, tg, gcs, _ = _build_handler(
        telegram=_FakeTelegram(get_file_raises=TelegramFileError("getFile not ok")),
        scheduler=scheduled.append,
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
    handler, tg, gcs, _ = _build_handler(
        telegram=_FakeTelegram(download_bytes=b"not a real image"),
        scheduler=scheduled.append,
    )

    await handler.handle(_photo_message(), _ctx(_pending()))
    await scheduled[0]

    # Input upload still happened, but transform failed → user error
    assert len(gcs.uploads) == 1
    assert gcs.uploads[0]["object_key"].startswith("sticker_requests/")
    assert "Couldn't turn that into a sticker" in tg.sent_messages[1]["text"]
    assert not tg.sent_documents


async def test_empty_cutout_replies_no_subject_error() -> None:
    persistence = _RecordingPersistence()
    scheduled: list[Any] = []
    handler, tg, gcs, _ = _build_handler(
        telegram=_FakeTelegram(download_bytes=_png_bytes()),
        scheduler=scheduled.append,
        background_remover=_nothing,
    )

    await handler.handle(_photo_message(), _ctx(_pending(), persistence=persistence))
    await scheduled[0]

    assert "Couldn't find anything to cut out" in tg.sent_messages[1]["text"]
    assert len(gcs.uploads) == 1
    assert not tg.sent_documents
    failed = [e for e in persistence.events if e.event == "make_sticker_failed"]
    assert failed and failed[0].details.startswith("NoSubjectError")


async def test_send_document_failure_replies_user_error() -> None:
    scheduled: list[Any] = []
    handler, tg, gcs, _ = _build_handler(
        telegram=_FakeTelegram(
            download_bytes=_png_bytes(width=200, height=200),
            send_document_raises=TelegramSendError("rejected"),
        ),
        scheduler=scheduled.append,
    )

    await handler.handle(_photo_message(), _ctx(_pending()))
    await scheduled[0]

    # Both uploads happened, but the send failed → generic error reply
    assert len(gcs.uploads) == 2
    assert "Something went wrong" in tg.sent_messages[1]["text"]
