"""Tests for :mod:`something_really_bot.features.tiktok_reminder.handler`."""

import random
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.tiktok_reminder.handler import (
    FRIDAY_MESSAGES,
    TikTokReminderJob,
)
from something_really_bot.routing.types import BotContext

IRINDICA_CHAT_ID = 159278882


@dataclass
class _FakeTelegramClient:
    sends: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None
    message_id: int = 4242

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        if self.raises is not None:
            raise self.raises
        self.sends.append({"chat_id": chat_id, "text": text})
        return {"message_id": self.message_id}


@dataclass
class _RecordingPersistence:
    responses: list[Any] = field(default_factory=list)

    def record_raw_update(self, _r: Any) -> None: ...
    def record_message(self, _r: Any) -> None: ...
    def record_file(self, _r: Any) -> None: ...
    def record_event(self, _r: Any) -> None: ...

    def record_response(self, record: Any) -> None:
        self.responses.append(record)


def _settings(*, irindica_chat_id: int | None = IRINDICA_CHAT_ID) -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        irindica_chat_id=irindica_chat_id,
    )


def _ctx(
    *,
    irindica_chat_id: int | None = IRINDICA_CHAT_ID,
    telegram_client: Any = None,
    persistence: Any = None,
) -> BotContext:
    return BotContext(
        settings=_settings(irindica_chat_id=irindica_chat_id),
        telegram_client=telegram_client,
        persistence=persistence,
    )


def _deterministic_rng(index: int) -> random.Random:
    """Return an RNG whose ``choice`` always picks ``FRIDAY_MESSAGES[index]``."""

    class _Pinned(random.Random):
        def choice(self, seq):  # type: ignore[override]
            return seq[index]

    return _Pinned()


async def test_run_sends_message_to_irindica_and_persists_success() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = TikTokReminderJob(rng=_deterministic_rng(0))
    ctx = _ctx(telegram_client=tg, persistence=persistence)

    await job.run(ctx)

    assert len(tg.sends) == 1
    sent = tg.sends[0]
    assert sent["chat_id"] == IRINDICA_CHAT_ID
    assert sent["text"] == FRIDAY_MESSAGES[0]
    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.chat_id == IRINDICA_CHAT_ID
    assert row.text == FRIDAY_MESSAGES[0]
    assert row.response_type == "scheduled_tiktok_reminder"
    assert row.success is True
    assert row.message_id == 4242
    assert row.error is None


async def test_run_picks_from_full_message_pool() -> None:
    """Without a deterministic RNG, sends *some* legacy message."""
    tg = _FakeTelegramClient()
    job = TikTokReminderJob()
    ctx = _ctx(telegram_client=tg)

    await job.run(ctx)

    assert tg.sends[0]["text"] in FRIDAY_MESSAGES


async def test_run_does_nothing_when_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = TikTokReminderJob()
    ctx = _ctx(irindica_chat_id=None, telegram_client=tg, persistence=persistence)

    await job.run(ctx)

    assert tg.sends == []
    assert persistence.responses == []


async def test_run_persists_failure_when_send_raises_and_does_not_propagate() -> None:
    tg = _FakeTelegramClient(raises=RuntimeError("network down"))
    persistence = _RecordingPersistence()
    job = TikTokReminderJob(rng=_deterministic_rng(1))
    ctx = _ctx(telegram_client=tg, persistence=persistence)

    # Must not raise — otherwise Cloud Scheduler retries and double-sends.
    await job.run(ctx)

    assert tg.sends == []  # the send raised before append
    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.success is False
    assert row.error is not None
    assert "network down" in row.error
    assert row.message_id is None


async def test_run_handles_missing_telegram_client() -> None:
    persistence = _RecordingPersistence()
    job = TikTokReminderJob()
    ctx = _ctx(telegram_client=None, persistence=persistence)

    await job.run(ctx)

    assert len(persistence.responses) == 1
    assert persistence.responses[0].success is False
    assert persistence.responses[0].error == "telegram_client_unavailable"


async def test_run_swallows_persistence_failure() -> None:
    class _BadPersistence:
        def record_response(self, _r: Any) -> None:
            raise RuntimeError("BQ down")

        def record_raw_update(self, _r: Any) -> None: ...
        def record_message(self, _r: Any) -> None: ...
        def record_file(self, _r: Any) -> None: ...
        def record_event(self, _r: Any) -> None: ...

    tg = _FakeTelegramClient()
    job = TikTokReminderJob()
    ctx = _ctx(telegram_client=tg, persistence=_BadPersistence())

    # Must not raise.
    await job.run(ctx)

    assert len(tg.sends) == 1
