"""Friday-morning TikTok reminder for Irindica (#24).

Cloud Scheduler fires ``POST /jobs/tiktok-reminder`` every Friday at
11:00 Europe/Amsterdam. The job picks a random message from
:data:`FRIDAY_MESSAGES` (carried over from the legacy
``stuff_for_ira/tiktok.py``) and sends it to Irindica's chat — chat_id
sourced from ``settings.irindica_chat_id`` (the IRINDICA_CHAT_ID key of
the ``telegram-qa-users`` secret).

The job never raises: a failure to send is logged and persisted in
``bot_responses`` with ``success=false``, but the HTTP response stays 200
so Cloud Scheduler doesn't retry and double-send.
"""

import random
from datetime import UTC, datetime

from something_really_bot.logging import get_logger
from something_really_bot.persistence import ResponseRecord
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)

FRIDAY_MESSAGES = (
    "👳🏾‍♂️Hello ma'am! A message from sir.\nПора бы уже запостить пятничный Тикток!",
    "👳🏾‍♂️pls👳🏾‍♂️ show👳🏾‍♂️ tiktak👳🏾‍♂️",
    "запости тикток и иди гуляй как свободный человек",
    "Ирочка, а можно тикточек, пожалуйста?",
)


class TikTokReminderJob:
    """Scheduled job: Friday 11:00 Europe/Amsterdam poke to Irindica."""

    name = "tiktok-reminder"

    def __init__(
        self, *, messages: tuple[str, ...] = FRIDAY_MESSAGES, rng: random.Random | None = None
    ) -> None:
        self._messages = messages
        self._rng = rng or random.SystemRandom()

    async def run(self, ctx: BotContext) -> None:
        chat_id = ctx.settings.irindica_chat_id
        if chat_id is None:
            _logger.error("tiktok_reminder_no_recipient_skipping")
            return

        text = self._rng.choice(self._messages)
        sent_at = datetime.now(UTC)
        success = False
        error: str | None = None
        message_id: int | None = None

        client = ctx.telegram_client
        if client is None:
            error = "telegram_client_unavailable"
            _logger.warning(error)
        else:
            try:
                response = await client.send_message(chat_id=chat_id, text=text)
            except Exception as exc:  # noqa: BLE001 — never let the scheduler retry
                error = f"{type(exc).__name__}: {exc}"
                _logger.warning("tiktok_reminder_send_failed", extra={"error": error})
            else:
                success = True
                message_id = response.get("message_id") if isinstance(response, dict) else None

        if ctx.persistence is not None:
            try:
                ctx.persistence.record_response(
                    ResponseRecord(
                        bot_id=ctx.bot_id,
                        chat_id=chat_id,
                        response_type="scheduled_tiktok_reminder",
                        text=text,
                        sent_at=sent_at,
                        success=success,
                        error=error,
                        message_id=message_id,
                    )
                )
            except Exception:  # noqa: BLE001 — webhook reliability promise
                _logger.exception("tiktok_reminder_persist_response_raised")
