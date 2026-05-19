"""Tests for :mod:`something_really_bot.telegram.client`."""

import json

import httpx
import pytest
from pydantic import SecretStr

from something_really_bot.telegram.client import TelegramClient, TelegramSendError


def _client_with_handler(handler) -> TelegramClient:
    """Build a TelegramClient backed by an httpx.MockTransport handler."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramClient(bot_token=SecretStr("super-secret-token"), http=http)


async def test_send_message_posts_expected_url_and_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client = _client_with_handler(handler)

    result = await client.send_message(chat_id=12345, text="hello")

    assert captured["method"] == "POST"
    assert captured["url"] == ("https://api.telegram.org/botsuper-secret-token/sendMessage")
    assert captured["body"] == {"chat_id": 12345, "text": "hello"}
    assert result == {"message_id": 42}


async def test_send_message_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False, "description": "bad gateway"})

    client = _client_with_handler(handler)

    with pytest.raises(TelegramSendError):
        await client.send_message(chat_id=1, text="x")


async def test_send_message_raises_when_ok_false() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    client = _client_with_handler(handler)

    with pytest.raises(TelegramSendError):
        await client.send_message(chat_id=1, text="x")


async def test_send_message_does_not_log_token(caplog: pytest.LogCaptureFixture) -> None:
    """Smoke check: even in failure paths we never log the bot token verbatim."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "forbidden"})

    client = _client_with_handler(handler)

    with caplog.at_level("WARNING"), pytest.raises(TelegramSendError):
        await client.send_message(chat_id=1, text="x")

    for record in caplog.records:
        assert "super-secret-token" not in record.getMessage()


async def test_send_message_includes_parse_mode_when_set() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _client_with_handler(handler)

    await client.send_message(chat_id=1, text="<b>hi</b>", parse_mode="HTML")

    assert captured["body"]["parse_mode"] == "HTML"


async def test_send_message_includes_disable_notification_when_set() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _client_with_handler(handler)

    await client.send_message(chat_id=1, text="hi", disable_notification=True)

    assert captured["body"]["disable_notification"] is True


async def test_send_message_omits_disable_notification_by_default() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _client_with_handler(handler)

    await client.send_message(chat_id=1, text="hi")

    assert "disable_notification" not in captured["body"]


async def test_send_message_includes_reply_parameters() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    client = _client_with_handler(handler)

    await client.send_message(chat_id=42, text="hi", reply_to_message_id=99)

    assert captured["body"]["reply_parameters"] == {
        "message_id": 99,
        "allow_sending_without_reply": True,
    }


async def test_set_message_reaction_posts_expected_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    client = _client_with_handler(handler)

    await client.set_message_reaction(chat_id=1, message_id=2, emoji="👀")

    assert captured["url"].endswith("/setMessageReaction")
    assert captured["body"] == {
        "chat_id": 1,
        "message_id": 2,
        "reaction": [{"type": "emoji", "emoji": "👀"}],
    }


async def test_send_video_uploads_multipart(tmp_path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 333}})

    client = _client_with_handler(handler)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x01\x02\x03fake")

    result = await client.send_video(
        chat_id=42,
        video_path=video,
        reply_to_message_id=99,
        duration_seconds=12,
    )

    assert captured["url"].endswith("/sendVideo")
    assert "multipart/form-data" in captured["content_type"]
    # The chat id, reply parameters, duration, and the file bytes all appear
    # somewhere in the multipart body.
    body = captured["body"]
    assert b'name="chat_id"' in body
    assert b"42" in body
    assert b'name="reply_parameters"' in body
    assert b'"message_id": 99' in body
    assert b'name="duration"' in body
    assert b"12" in body
    assert b"\x00\x01\x02\x03fake" in body
    assert result == {"message_id": 333}
