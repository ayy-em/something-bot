"""Handler that schedules Telegram → GCS file downloads (#20).

Matches *private-chat* messages whose content is a photo, document, or
voice. Group/supergroup/channel file uploads are intentionally not
matched here — their metadata still lands in BigQuery via the webhook
orchestrator's ``telegram_files`` insert, but the bot does not download
them (SPEC §6.3 forbids the bot from acting in non-private chats).

The handler builds a :class:`FileFetchRequest` and hands it to the
configured :class:`FileFetcher`, which runs the actual download via an
``asyncio.create_task`` so the webhook returns 200 immediately
(decision 0002).
"""

from something_really_bot.file_storage import FileFetchRequest
from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext, HandlerResult
from something_really_bot.telegram.models import (
    DocumentContent,
    ParsedUpdate,
    PhotoContent,
    PrivateMessage,
    VoiceContent,
)

_logger = get_logger(__name__)


class FileStorageHandler:
    """Trigger background file download for private-chat file uploads."""

    name = "file_storage.download"

    def matches(self, update: ParsedUpdate, _ctx: BotContext) -> bool:
        if not isinstance(update, PrivateMessage):
            return False
        return isinstance(update.content, PhotoContent | DocumentContent | VoiceContent)

    async def handle(self, update: ParsedUpdate, ctx: BotContext) -> HandlerResult:
        assert isinstance(update, PrivateMessage)

        fetcher = ctx.file_fetcher
        if fetcher is None:
            _logger.warning(
                "file_fetcher_unavailable_skipping_download",
                extra={"update_id": update.update_id},
            )
            return HandlerResult(handled=True, handler_name=self.name)

        request = _build_request(update, ctx.bot_id)
        if request is None:
            return HandlerResult(handled=True, handler_name=self.name)

        fetcher.schedule(request)
        return HandlerResult(handled=True, handler_name=self.name)


def _build_request(update: PrivateMessage, bot_id: str) -> FileFetchRequest | None:
    content = update.content
    common = {
        "bot_id": bot_id,
        "update_id": update.update_id,
        "chat_id": update.chat_id,
        "message_id": update.message_id,
    }
    if isinstance(content, PhotoContent):
        largest = max(content.photo, key=lambda p: p.file_size or 0)
        return FileFetchRequest(
            **common,
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            file_type="photo",
            file_size_bytes=largest.file_size,
        )
    if isinstance(content, DocumentContent):
        doc = content.document
        return FileFetchRequest(
            **common,
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            file_type="document",
            mime_type=doc.mime_type,
            file_size_bytes=doc.file_size,
            original_filename=doc.file_name,
        )
    if isinstance(content, VoiceContent):
        voice = content.voice
        return FileFetchRequest(
            **common,
            file_id=voice.file_id,
            file_unique_id=voice.file_unique_id,
            file_type="voice",
            mime_type=voice.mime_type,
            file_size_bytes=voice.file_size,
        )
    return None
