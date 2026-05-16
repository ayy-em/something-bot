"""Parse raw Telegram webhook payloads into :class:`ParsedUpdate` instances.

The parser is intentionally conservative: anything we don't model maps to
:class:`UnsupportedUpdate` rather than raising. Only genuinely broken
payloads (non-dict, missing ``update_id``) raise :class:`MalformedUpdateError`,
which the webhook handler in #14 will catch so Telegram still gets a 200
and doesn't retry-storm us.
"""

from typing import Any

from something_really_bot.telegram.models import (
    ChannelPost,
    CommandContent,
    Document,
    DocumentContent,
    GroupMessage,
    MessageContent,
    ParsedUpdate,
    PhotoContent,
    PhotoSize,
    PrivateMessage,
    SupergroupMessage,
    TextContent,
    UnsupportedUpdate,
    User,
    Voice,
    VoiceContent,
)

_MESSAGE_KEYS_WE_SUPPORT = ("message", "channel_post")
_UPDATE_KEYS_WE_DO_NOT_SUPPORT = (
    "edited_message",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
    "message_reaction_count",
)


class MalformedUpdateError(Exception):
    """Raised when a payload is so broken we cannot even classify it.

    Examples: not a dict, missing ``update_id``, message present but with no
    chat object. The webhook handler catches this and acknowledges the
    delivery anyway to prevent Telegram retry storms.
    """


def parse_update(raw: Any) -> ParsedUpdate:
    """Classify a raw Telegram update payload.

    Args:
        raw: The JSON-decoded webhook body Telegram sent us.

    Returns:
        A :class:`ParsedUpdate` (one of the chat-type variants or
        :class:`UnsupportedUpdate`).

    Raises:
        MalformedUpdateError: When the payload is unrecognisable as a
            Telegram update.
    """
    if not isinstance(raw, dict):
        raise MalformedUpdateError(f"Expected dict, got {type(raw).__name__}.")

    update_id = raw.get("update_id")
    if not isinstance(update_id, int):
        raise MalformedUpdateError("Missing or non-integer update_id.")

    unsupported_reason = _detect_unsupported_top_level(raw)
    if unsupported_reason is not None:
        return UnsupportedUpdate(update_id=update_id, reason=unsupported_reason, raw=raw)

    message, message_key = _extract_message(raw)
    if message is None:
        return UnsupportedUpdate(update_id=update_id, reason="no_supported_message_field", raw=raw)

    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat or "type" not in chat:
        raise MalformedUpdateError("Message is missing a usable chat object.")

    chat_type = chat["type"]
    content = _extract_content(message)
    if content is None:
        return UnsupportedUpdate(
            update_id=update_id, reason=f"unsupported_content_in_{message_key}", raw=raw
        )

    return _build_chat_variant(
        update_id=update_id, message=message, chat=chat, chat_type=chat_type, content=content
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _detect_unsupported_top_level(raw: dict[str, Any]) -> str | None:
    for key in _UPDATE_KEYS_WE_DO_NOT_SUPPORT:
        if key in raw:
            return key
    return None


def _extract_message(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    for key in _MESSAGE_KEYS_WE_SUPPORT:
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            return candidate, key
    return None, ""


def _extract_content(message: dict[str, Any]) -> MessageContent | None:
    if "photo" in message:
        return _photo_content(message)
    if "document" in message:
        return _document_content(message)
    if "voice" in message:
        return _voice_content(message)
    if "text" in message:
        return _text_or_command_content(message)
    return None


def _photo_content(message: dict[str, Any]) -> PhotoContent | None:
    photos = message.get("photo")
    if not isinstance(photos, list) or not photos:
        return None
    return PhotoContent(
        photo=[PhotoSize.model_validate(p) for p in photos],
        caption=message.get("caption"),
    )


def _document_content(message: dict[str, Any]) -> DocumentContent | None:
    doc = message.get("document")
    if not isinstance(doc, dict):
        return None
    return DocumentContent(
        document=Document.model_validate(doc),
        caption=message.get("caption"),
    )


def _voice_content(message: dict[str, Any]) -> VoiceContent | None:
    voice = message.get("voice")
    if not isinstance(voice, dict):
        return None
    return VoiceContent(
        voice=Voice.model_validate(voice),
        caption=message.get("caption"),
    )


def _text_or_command_content(message: dict[str, Any]) -> MessageContent | None:
    text = message.get("text")
    if not isinstance(text, str):
        return None

    command = _extract_command(text, message.get("entities") or [])
    if command is not None:
        return command
    return TextContent(text=text)


def _extract_command(text: str, entities: list[Any]) -> CommandContent | None:
    if not text.startswith("/"):
        return None
    leading_entity = next(
        (
            e
            for e in entities
            if isinstance(e, dict) and e.get("type") == "bot_command" and e.get("offset") == 0
        ),
        None,
    )
    if leading_entity is None:
        return None

    length = leading_entity.get("length")
    if not isinstance(length, int) or length <= 0:
        return None

    command_full = text[:length]
    command, _, _ = command_full.partition("@")
    args = text[length:].strip() or None
    return CommandContent(command=command, text=text, args=args)


def _build_chat_variant(
    *,
    update_id: int,
    message: dict[str, Any],
    chat: dict[str, Any],
    chat_type: str,
    content: MessageContent,
) -> ParsedUpdate:
    base = {
        "update_id": update_id,
        "message_id": message.get("message_id"),
        "chat_id": chat["id"],
        "date": message.get("date"),
        "content": content,
    }

    if chat_type == "private":
        from_user = _user(message)
        if from_user is None:
            raise MalformedUpdateError("Private message has no `from` user.")
        return PrivateMessage(**base, from_user=from_user)

    if chat_type == "group":
        from_user = _user(message)
        if from_user is None:
            raise MalformedUpdateError("Group message has no `from` user.")
        return GroupMessage(
            **base,
            chat_title=chat.get("title"),
            from_user=from_user,
        )

    if chat_type == "supergroup":
        from_user = _user(message)
        if from_user is None:
            raise MalformedUpdateError("Supergroup message has no `from` user.")
        return SupergroupMessage(
            **base,
            chat_title=chat.get("title"),
            from_user=from_user,
        )

    if chat_type == "channel":
        return ChannelPost(**base, chat_title=chat.get("title"))

    return UnsupportedUpdate(
        update_id=update_id, reason=f"unsupported_chat_type:{chat_type}", raw=message
    )


def _user(message: dict[str, Any]) -> User | None:
    user = message.get("from")
    if not isinstance(user, dict) or "id" not in user:
        return None
    return User.model_validate(user)
