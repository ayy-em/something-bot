"""Tests for :mod:`something_really_bot.services.jobs`."""

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.routing.types import BotContext
from something_really_bot.services.jobs import JobRegistry, UnknownJobError


class _CountingJob:
    name = "test.counter"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _ctx: BotContext) -> None:
        self.calls += 1


def _ctx() -> BotContext:
    return BotContext(
        settings=Settings.model_construct(
            telegram_webhook_secret=SecretStr("x"),
            telegram_bot_token=SecretStr("tok"),
            telegram_qa_user_ids=frozenset(),
        )
    )


async def test_dispatch_runs_registered_handler() -> None:
    registry = JobRegistry()
    job = _CountingJob()
    registry.register(job)

    await registry.dispatch(job.name, _ctx())

    assert job.calls == 1


async def test_dispatch_unknown_job_raises() -> None:
    registry = JobRegistry()

    with pytest.raises(UnknownJobError):
        await registry.dispatch("nope", _ctx())


def test_duplicate_registration_raises() -> None:
    registry = JobRegistry()
    registry.register(_CountingJob())

    with pytest.raises(ValueError):
        registry.register(_CountingJob())


def test_names_returns_sorted_list() -> None:
    registry = JobRegistry()

    class _A:
        name = "zebra"

        async def run(self, _ctx: BotContext) -> None: ...

    class _B:
        name = "alpha"

        async def run(self, _ctx: BotContext) -> None: ...

    registry.register(_A())
    registry.register(_B())

    assert registry.names() == ["alpha", "zebra"]
