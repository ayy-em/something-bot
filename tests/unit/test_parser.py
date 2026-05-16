"""Tests for the Telegram update parser (SPEC §6.3)."""

import json
from pathlib import Path
from typing import Any

import pytest

from something_really_bot.telegram.models import (
    ChannelPost,
    CommandContent,
    DocumentContent,
    GroupMessage,
    PhotoContent,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    UnsupportedUpdate,
    VoiceContent,
)
from something_really_bot.telegram.parser import MalformedUpdateError, parse_update

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "telegram"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


# --------------------------------------------------------------------------- #
# Supported variants
# --------------------------------------------------------------------------- #


def test_private_text_message_parses_to_private_with_text_content() -> None:
    result = parse_update(_load("private_text"))

    assert isinstance(result, PrivateMessage)
    assert result.chat_type == "private"
    assert result.chat_id == 135499785
    assert result.from_user.id == 135499785
    assert isinstance(result.content, TextContent)
    assert result.content.text == "hello world"


def test_private_start_command_parses_to_command_content() -> None:
    result = parse_update(_load("private_start_command"))

    assert isinstance(result, PrivateMessage)
    assert isinstance(result.content, CommandContent)
    assert result.content.command == "/start"
    assert result.content.args is None


def test_private_help_command_extracts_args() -> None:
    result = parse_update(_load("private_help_command"))

    assert isinstance(result, PrivateMessage)
    assert isinstance(result.content, CommandContent)
    assert result.content.command == "/help"
    assert result.content.args == "me please"


def test_private_photo_parses_with_all_size_variants() -> None:
    result = parse_update(_load("private_photo"))

    assert isinstance(result, PrivateMessage)
    assert isinstance(result.content, PhotoContent)
    assert len(result.content.photo) == 3
    assert result.content.photo[-1].width == 1280
    assert result.content.caption == "look at this"


def test_private_document_parses_with_metadata() -> None:
    result = parse_update(_load("private_document"))

    assert isinstance(result, PrivateMessage)
    assert isinstance(result.content, DocumentContent)
    assert result.content.document.file_name == "report.pdf"
    assert result.content.document.mime_type == "application/pdf"


def test_private_voice_parses_with_duration() -> None:
    result = parse_update(_load("private_voice"))

    assert isinstance(result, PrivateMessage)
    assert isinstance(result.content, VoiceContent)
    assert result.content.voice.duration == 7
    assert result.content.voice.mime_type == "audio/ogg"


def test_group_text_message_yields_group_variant_with_text_content() -> None:
    result = parse_update(_load("group_text"))

    assert isinstance(result, GroupMessage)
    assert result.chat_type == "group"
    assert result.chat_title == "Test group chat"
    assert isinstance(result.content, TextContent)


def test_group_command_strips_bot_suffix_and_extracts_args() -> None:
    result = parse_update(_load("group_command_with_bot_suffix"))

    assert isinstance(result, GroupMessage)
    assert isinstance(result.content, CommandContent)
    assert result.content.command == "/help"
    assert result.content.args == "please"


def test_supergroup_text_message_yields_supergroup_variant() -> None:
    result = parse_update(_load("supergroup_text"))

    assert isinstance(result, SupergroupMessage)
    assert result.chat_type == "supergroup"
    assert isinstance(result.content, TextContent)


def test_channel_post_yields_channel_post_variant_without_from_user() -> None:
    result = parse_update(_load("channel_post_text"))

    assert isinstance(result, ChannelPost)
    assert result.chat_type == "channel"
    assert result.chat_title == "Test channel"
    assert isinstance(result.content, TextContent)
    assert not hasattr(result, "from_user")


# --------------------------------------------------------------------------- #
# Unsupported variants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fixture", "expected_reason"),
    [
        ("edited_message", "edited_message"),
        ("callback_query", "callback_query"),
    ],
)
def test_unsupported_update_types_classify_as_unsupported_without_raising(
    fixture: str, expected_reason: str
) -> None:
    result = parse_update(_load(fixture))

    assert isinstance(result, UnsupportedUpdate)
    assert result.reason == expected_reason
    assert "update_id" not in result.raw or result.raw["update_id"] == result.update_id


def test_supported_envelope_with_unmodelled_content_is_classified_unsupported() -> None:
    payload = {
        "update_id": 999,
        "message": {
            "message_id": 1,
            "date": 1715850000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "sticker": {"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1},
        },
    }

    result = parse_update(payload)

    assert isinstance(result, UnsupportedUpdate)
    assert "unsupported_content" in result.reason


# --------------------------------------------------------------------------- #
# Malformed payloads
# --------------------------------------------------------------------------- #


def test_malformed_payload_missing_update_id_raises() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_update(_load("malformed_no_update_id"))


def test_non_dict_payload_raises() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_update("not a dict")


def test_message_with_no_chat_raises() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_update({"update_id": 1, "message": {"message_id": 1, "date": 1, "text": "hi"}})
