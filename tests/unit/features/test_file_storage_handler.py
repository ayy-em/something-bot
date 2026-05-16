"""Tests for :mod:`something_really_bot.features.file_storage.handler`."""

from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.file_storage.handler import FileStorageHandler
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.models import (
    ChannelPost,
    Document,
    DocumentContent,
    GroupMessage,
    PhotoContent,
    PhotoSize,
    PrivateMessage,
    TextContent,
    User,
    Voice,
    VoiceContent,
)

USER_ID = 42


class _RecordingFetcher:
    def __init__(self) -> None:
        self.scheduled: list[Any] = []

    def schedule(self, request: Any) -> None:
        self.scheduled.append(request)


def _ctx(fetcher: _RecordingFetcher | None) -> BotContext:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
    )
    return BotContext(settings=settings, file_fetcher=fetcher)


def _private_photo() -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=PhotoContent(
            photo=[
                PhotoSize(file_id="small", file_unique_id="s", width=90, height=90, file_size=100),
                PhotoSize(
                    file_id="big", file_unique_id="b", width=1280, height=1280, file_size=9999
                ),
            ],
            caption="hi",
        ),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _private_document() -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=DocumentContent(
            document=Document(
                file_id="doc-id",
                file_unique_id="doc-uniq",
                file_name="vacation.pdf",
                mime_type="application/pdf",
                file_size=4321,
            ),
        ),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _private_voice() -> PrivateMessage:
    return PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=VoiceContent(
            voice=Voice(
                file_id="voice-id",
                file_unique_id="voice-uniq",
                duration=5,
                mime_type="audio/ogg",
                file_size=2222,
            ),
        ),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _group_photo() -> GroupMessage:
    return GroupMessage(
        update_id=1,
        message_id=2,
        chat_id=-1001,
        date=1715000000,
        content=PhotoContent(
            photo=[PhotoSize(file_id="g", file_unique_id="g", width=90, height=90, file_size=100)],
        ),
        chat_title="g",
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )


def _channel_photo() -> ChannelPost:
    return ChannelPost(
        update_id=1,
        message_id=2,
        chat_id=-1003,
        date=1715000000,
        content=PhotoContent(
            photo=[PhotoSize(file_id="c", file_unique_id="c", width=90, height=90, file_size=100)],
        ),
        chat_title="c",
    )


@pytest.mark.parametrize(
    ("factory", "expected_type"),
    [
        (_private_photo, "photo"),
        (_private_document, "document"),
        (_private_voice, "voice"),
    ],
)
async def test_matches_and_schedules_private_file_uploads(factory, expected_type: str) -> None:
    handler = FileStorageHandler()
    fetcher = _RecordingFetcher()
    ctx = _ctx(fetcher)
    update = factory()

    assert handler.matches(update, ctx) is True

    result = await handler.handle(update, ctx)

    assert result.handled is True
    assert result.handler_name == "file_storage.download"
    assert result.reply_text is None  # files don't get a Telegram reply
    assert len(fetcher.scheduled) == 1
    assert fetcher.scheduled[0].file_type == expected_type


async def test_does_not_match_text_content() -> None:
    update = PrivateMessage(
        update_id=1,
        message_id=2,
        chat_id=USER_ID,
        date=1715000000,
        content=TextContent(text="hi"),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
    )

    assert FileStorageHandler().matches(update, _ctx(_RecordingFetcher())) is False


async def test_does_not_match_file_in_group_chat() -> None:
    assert FileStorageHandler().matches(_group_photo(), _ctx(_RecordingFetcher())) is False


async def test_does_not_match_file_in_channel_post() -> None:
    assert FileStorageHandler().matches(_channel_photo(), _ctx(_RecordingFetcher())) is False


async def test_handle_without_fetcher_does_not_crash() -> None:
    handler = FileStorageHandler()

    result = await handler.handle(_private_photo(), _ctx(None))

    assert result.handled is True
    assert result.reply_text is None


async def test_photo_handler_picks_largest_size() -> None:
    fetcher = _RecordingFetcher()
    await FileStorageHandler().handle(_private_photo(), _ctx(fetcher))

    assert fetcher.scheduled[0].file_id == "big"
    assert fetcher.scheduled[0].file_size_bytes == 9999


async def test_document_handler_carries_filename_and_mime() -> None:
    fetcher = _RecordingFetcher()
    await FileStorageHandler().handle(_private_document(), _ctx(fetcher))

    request = fetcher.scheduled[0]
    assert request.original_filename == "vacation.pdf"
    assert request.mime_type == "application/pdf"


async def test_voice_handler_carries_mime() -> None:
    fetcher = _RecordingFetcher()
    await FileStorageHandler().handle(_private_voice(), _ctx(fetcher))

    request = fetcher.scheduled[0]
    assert request.mime_type == "audio/ogg"
    assert request.file_type == "voice"
