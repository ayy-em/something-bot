"""Tests for :mod:`something_really_bot.features.ensure_webhook.handler`."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.ensure_webhook.handler import EnsureWebhookJob
from something_really_bot.routing.types import BotContext

CLOUD_RUN_URL = "https://something-really-bot-cloudrun-abc123-ew.a.run.app"


def _settings(*, cloud_run_url: str | None = CLOUD_RUN_URL) -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("test-secret"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        irindica_chat_id=None,
        something_group_chat_id=None,
        cloud_run_url=cloud_run_url,
    )


def _ctx(
    *,
    cloud_run_url: str | None = CLOUD_RUN_URL,
    telegram_client: Any = None,
) -> BotContext:
    return BotContext(
        settings=_settings(cloud_run_url=cloud_run_url),
        telegram_client=telegram_client,
    )


@dataclass
class _FakeTelegramClient:
    webhook_info: dict[str, Any] = field(default_factory=dict)
    set_webhook_calls: list[dict[str, Any]] = field(default_factory=list)
    set_my_commands_calls: list[list[dict[str, str]]] = field(default_factory=list)

    async def get_webhook_info(self) -> dict[str, Any]:
        return self.webhook_info

    async def set_webhook(self, url: str, *, secret_token: str | None = None) -> dict[str, Any]:
        self.set_webhook_calls.append({"url": url, "secret_token": secret_token})
        return {}

    async def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        self.set_my_commands_calls.append(commands)
        return {}

    async def send_message(self, **kwargs: Any) -> dict[str, Any]:
        return {}


async def test_does_nothing_when_webhook_already_correct() -> None:
    tg = _FakeTelegramClient(webhook_info={"url": f"{CLOUD_RUN_URL}/webhook"})
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=tg))

    assert tg.set_webhook_calls == []


async def test_restores_webhook_when_empty() -> None:
    tg = _FakeTelegramClient(webhook_info={"url": ""})
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=tg))

    assert len(tg.set_webhook_calls) == 1
    call = tg.set_webhook_calls[0]
    assert call["url"] == f"{CLOUD_RUN_URL}/webhook"
    assert call["secret_token"] == "test-secret"


async def test_restores_webhook_when_wrong_url() -> None:
    tg = _FakeTelegramClient(webhook_info={"url": "https://old-service.run.app/webhook"})
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=tg))

    assert len(tg.set_webhook_calls) == 1
    assert tg.set_webhook_calls[0]["url"] == f"{CLOUD_RUN_URL}/webhook"


async def test_restores_webhook_when_url_key_missing() -> None:
    tg = _FakeTelegramClient(webhook_info={})
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=tg))

    assert len(tg.set_webhook_calls) == 1


async def test_skips_when_cloud_run_url_not_set() -> None:
    tg = _FakeTelegramClient()
    job = EnsureWebhookJob()

    await job.run(_ctx(cloud_run_url=None, telegram_client=tg))

    assert tg.set_webhook_calls == []


async def test_skips_when_telegram_client_unavailable() -> None:
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=None))


async def test_strips_trailing_slash_from_cloud_run_url() -> None:
    tg = _FakeTelegramClient(webhook_info={"url": ""})
    job = EnsureWebhookJob()

    await job.run(_ctx(cloud_run_url=f"{CLOUD_RUN_URL}/", telegram_client=tg))

    assert tg.set_webhook_calls[0]["url"] == f"{CLOUD_RUN_URL}/webhook"


async def test_syncs_commands_on_every_run() -> None:
    tg = _FakeTelegramClient(webhook_info={"url": f"{CLOUD_RUN_URL}/webhook"})
    job = EnsureWebhookJob()

    await job.run(_ctx(telegram_client=tg))

    assert len(tg.set_my_commands_calls) == 1
    commands = tg.set_my_commands_calls[0]
    command_names = [c["command"] for c in commands]
    assert "help" in command_names
    assert "start" in command_names
    assert "ping" in command_names
    assert "make_sticker" in command_names
    assert "next-reunion" not in command_names
    assert "next_reunion" not in command_names
    for cmd in commands:
        assert not cmd["command"].startswith("/")
        assert "-" not in cmd["command"]
        assert cmd["description"]
