"""Tests for :mod:`something_really_bot.features.finco_daily_stats.handler`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.finco_daily_stats.handler import FinCoDailyStatsJob
from something_really_bot.features.finco_daily_stats.sites import SiteConfig
from something_really_bot.features.finco_daily_stats.source.google_analytics import (
    GoogleAnalyticsError,
    SiteMetrics,
    TopPage,
)
from something_really_bot.routing.types import BotContext

GROUP_CHAT_ID = -1001234567890

SITE_A = SiteConfig(label="A", domain="a.example", ga4_property_id="111")
SITE_B = SiteConfig(label="B", domain="b.example", ga4_property_id="222")
TWO_SITES: tuple[SiteConfig, ...] = (SITE_A, SITE_B)


def _settings(*, chat_id: int | None = GROUP_CHAT_ID) -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        irindica_chat_id=None,
        something_group_chat_id=chat_id,
    )


def _ctx(
    *,
    chat_id: int | None = GROUP_CHAT_ID,
    telegram_client: Any = None,
    persistence: Any = None,
) -> BotContext:
    return BotContext(
        settings=_settings(chat_id=chat_id),
        telegram_client=telegram_client,
        persistence=persistence,
    )


@dataclass
class _FakeTelegramClient:
    sends: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None
    message_id: int = 99

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


def _fixed_now() -> datetime:
    # 2026-05-17 09:00 UTC == 11:00 Europe/Amsterdam → "yesterday" is 2026-05-16
    return datetime(2026, 5, 17, 9, 0, tzinfo=UTC)


def _ga4_fetcher_for(values: dict[str, SiteMetrics | BaseException]):
    async def fetch(property_id: str, _start, _end):
        result = values[property_id]
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch


async def test_run_happy_path_two_sites() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for(
        {
            "111": SiteMetrics(
                total_users=1234,
                new_users=412,
                total_users_7d=8765,
                top_pages=(
                    TopPage(page_path="/pricing", views=312),
                    TopPage(page_path="/about", views=220),
                    TopPage(page_path="/", views=99),
                ),
            ),
            "222": SiteMetrics(
                total_users=567,
                new_users=200,
                total_users_7d=4123,
                top_pages=(TopPage(page_path="/home", views=88),),
            ),
        }
    )
    job = FinCoDailyStatsJob(sites=TWO_SITES, ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    assert len(tg.sends) == 1
    sent = tg.sends[0]
    assert sent["chat_id"] == GROUP_CHAT_ID
    text = sent["text"]
    assert "2026-05-16" in text
    assert "A (a.example)" in text
    assert "B (b.example)" in text
    assert "Visitors: 1,234 (412 new), 8,765 last 7 days" in text
    assert "pricing — 312" in text
    assert "/pricing" not in text
    # Root path keeps a slash so the line is not empty.
    assert "/ — 99" in text

    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.chat_id == GROUP_CHAT_ID
    assert row.response_type == "scheduled_finco_daily_stats"
    assert row.success is True
    assert row.message_id == 99


async def test_run_omits_failed_ga4_lines_for_one_site() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for(
        {
            "111": GoogleAnalyticsError("boom"),
            "222": SiteMetrics(total_users=10, new_users=2, total_users_7d=70, top_pages=()),
        }
    )
    job = FinCoDailyStatsJob(sites=TWO_SITES, ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    # Site A's whole section drops out (GA4 was its only source); site B still present.
    assert "A (a.example)" not in text
    assert "B (b.example)" in text
    assert "Visitors: 10" in text


async def test_run_sends_no_data_today_when_all_ga4_calls_fail() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for(
        {
            "111": GoogleAnalyticsError("a"),
            "222": GoogleAnalyticsError("b"),
        }
    )
    job = FinCoDailyStatsJob(sites=TWO_SITES, ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    text = tg.sends[0]["text"]
    assert "No data today." in text
    assert persistence.responses[0].success is True


async def test_run_does_nothing_when_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(0, 0, 0, ())})
    job = FinCoDailyStatsJob(sites=(SITE_A,), ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []


async def test_run_persists_failure_when_send_raises_and_does_not_propagate() -> None:
    tg = _FakeTelegramClient(raises=RuntimeError("network down"))
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(1, 1, 7, ())})
    job = FinCoDailyStatsJob(sites=(SITE_A,), ga4_fetcher=ga4, now=_fixed_now)

    # Must not raise — Cloud Scheduler would retry and double-send.
    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.success is False
    assert row.error is not None
    assert "network down" in row.error


async def test_run_handles_missing_telegram_client() -> None:
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(1, 1, 7, ())})
    job = FinCoDailyStatsJob(sites=(SITE_A,), ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(telegram_client=None, persistence=persistence))

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
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(1, 1, 7, ())})
    job = FinCoDailyStatsJob(sites=(SITE_A,), ga4_fetcher=ga4, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg, persistence=_BadPersistence()))

    assert len(tg.sends) == 1
