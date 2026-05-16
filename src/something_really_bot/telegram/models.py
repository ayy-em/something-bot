"""Typed Pydantic models for Telegram webhook updates (SPEC §6.3).

We model only the subset of the Telegram Bot API surface needed to classify
incoming updates, route them, and persist them — not the full schema. Any
update type or message subtype that isn't represented here is classified as
:class:`UnsupportedUpdate`; downstream code treats it as "log + persist raw,
no further processing".

Two layers of discrimination:

1. Top-level :data:`ParsedUpdate` discriminates on ``type`` — chat-type-first
   (``private_message``, ``group_message``, ``supergroup_message``,
   ``channel_post``) plus ``unsupported``.
2. Inside each chat-type variant, ``content`` discriminates on ``kind`` —
   ``text``, ``command``, ``photo``, ``document``, ``voice``.

This means a photo in a private chat is a ``PrivateMessage`` whose
``content`` is :class:`PhotoContent`; a photo in a group is a
``GroupMessage`` whose ``content`` is also :class:`PhotoContent`. Same
content shape, different routing context.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    """Base model for value-object semantics."""

    model_config = ConfigDict(frozen=True, extra="ignore")


# --------------------------------------------------------------------------- #
# Leaf entities
# --------------------------------------------------------------------------- #


class User(_Frozen):
    """A Telegram user (``from`` on a message)."""

    id: int
    is_bot: bool = False
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class PhotoSize(_Frozen):
    """One size variant of a photo (Telegram delivers an array of these)."""

    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: int | None = None


class Document(_Frozen):
    """A file attachment."""

    file_id: str
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class Voice(_Frozen):
    """A voice note."""

    file_id: str
    file_unique_id: str
    duration: int
    mime_type: str | None = None
    file_size: int | None = None


# --------------------------------------------------------------------------- #
# Content union — what kind of message body this is
# --------------------------------------------------------------------------- #


class TextContent(_Frozen):
    """A plain text message that is not a command."""

    kind: Literal["text"] = "text"
    text: str


class CommandContent(_Frozen):
    """A bot command (``/start`` etc.).

    ``command`` is the bare command name without the ``@bot`` suffix Telegram
    appends in groups. ``args`` is the substring after the command, stripped;
    ``None`` when the command had no arguments.
    """

    kind: Literal["command"] = "command"
    command: str
    text: str
    args: str | None = None


class PhotoContent(_Frozen):
    """A photo message; carries all size variants Telegram offered."""

    kind: Literal["photo"] = "photo"
    photo: list[PhotoSize]
    caption: str | None = None


class DocumentContent(_Frozen):
    """A document/file message."""

    kind: Literal["document"] = "document"
    document: Document
    caption: str | None = None


class VoiceContent(_Frozen):
    """A voice message."""

    kind: Literal["voice"] = "voice"
    voice: Voice
    caption: str | None = None


MessageContent = Annotated[
    TextContent | CommandContent | PhotoContent | DocumentContent | VoiceContent,
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------- #
# Chat-type variants
# --------------------------------------------------------------------------- #


class _MessageBase(_Frozen):
    """Fields shared by every non-unsupported variant."""

    update_id: int
    message_id: int
    chat_id: int
    date: int
    content: MessageContent


class PrivateMessage(_MessageBase):
    """A message sent to the bot in a 1:1 private chat."""

    type: Literal["private_message"] = "private_message"
    chat_type: Literal["private"] = "private"
    from_user: User


class GroupMessage(_MessageBase):
    """A message in a small group chat the bot is a member of."""

    type: Literal["group_message"] = "group_message"
    chat_type: Literal["group"] = "group"
    chat_title: str | None = None
    from_user: User


class SupergroupMessage(_MessageBase):
    """A message in a supergroup."""

    type: Literal["supergroup_message"] = "supergroup_message"
    chat_type: Literal["supergroup"] = "supergroup"
    chat_title: str | None = None
    from_user: User


class ChannelPost(_MessageBase):
    """A post in a channel; channels post anonymously, so ``from_user`` is absent."""

    type: Literal["channel_post"] = "channel_post"
    chat_type: Literal["channel"] = "channel"
    chat_title: str | None = None


class UnsupportedUpdate(_Frozen):
    """Any update we choose not to model.

    Examples: callback queries, inline queries, edited messages, polls,
    reactions, stickers — and any chat-type-supported message whose content
    kind isn't one of text / command / photo / document / voice.

    The raw Telegram payload is preserved so downstream persistence (#18)
    can still archive it for audit.
    """

    type: Literal["unsupported"] = "unsupported"
    update_id: int
    reason: str
    raw: dict[str, Any]


ParsedUpdate = Annotated[
    PrivateMessage | GroupMessage | SupergroupMessage | ChannelPost | UnsupportedUpdate,
    Field(discriminator="type"),
]
