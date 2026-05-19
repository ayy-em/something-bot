"""Tests for :mod:`something_really_bot.features.daily_digest.handler`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.daily_digest.handler import DailyDigestJob
from something_really_bot.features.daily_digest.sites import SiteConfig
from something_really_bot.features.daily_digest.source.google_analytics import (
    GoogleAnalyticsError,
    SiteMetrics,
    TopPage,
)
from something_really_bot.features.daily_digest.source.google_search_console import (
    GoogleSearchConsoleError,
    SiteSearchMetrics,
)
from something_really_bot.routing.types import BotContext
from something_really_bot.services.job_history import JobTallyRow

GROUP_CHAT_ID = -1001234567890

SITE_A = SiteConfig(
    label="A",
    domain="a.example",
    ga4_property_id="111",
    gsc_site_url="sc-domain:a.example",
)
SITE_B = SiteConfig(
    label="B",
    domain="b.example",
    ga4_property_id="222",
    gsc_site_url="sc-domain:b.example",
)
TWO_SITES: tuple[SiteConfig, ...] = (SITE_A, SITE_B)


def _gsc_unavailable():
    """Default fixture: GSC fails for every site, dropping its line."""

    async def fetch(_site_url, _start, _end):
        raise GoogleSearchConsoleError("not configured in test")

    return fetch


def _gsc_fetcher_for(values: dict[str, SiteSearchMetrics | BaseException]):
    async def fetch(site_url: str, _start, _end):
        result = values[site_url]
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch


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
    job_history_logger: Any = None,
) -> BotContext:
    return BotContext(
        settings=_settings(chat_id=chat_id),
        telegram_client=telegram_client,
        persistence=persistence,
        job_history_logger=job_history_logger,
    )


@dataclass
class _FakeJobHistoryLogger:
    """Stub that returns a canned tally (or raises) when queried."""

    tally: list[Any] = field(default_factory=list)
    raises: BaseException | None = None

    async def fetch_recent_summary(self, *, bot_id: str, window: Any) -> list[Any]:
        if self.raises is not None:
            raise self.raises
        return list(self.tally)


@dataclass
class _FakeTelegramClient:
    sends: list[dict[str, Any]] = field(default_factory=list)
    raises: BaseException | None = None
    message_id: int = 99

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        if self.raises is not None:
            raise self.raises
        self.sends.append({"chat_id": chat_id, "text": text, **kwargs})
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
    job = DailyDigestJob(
        sites=TWO_SITES, ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    assert len(tg.sends) == 1
    sent = tg.sends[0]
    assert sent["chat_id"] == GROUP_CHAT_ID
    # Digest goes out silently with MarkdownV2 so bold + emoji render.
    assert sent["parse_mode"] == "MarkdownV2"
    assert sent["disable_notification"] is True
    text = sent["text"]
    # Date is bolded in MarkdownV2 with dashes escaped.
    assert "*2026\\-05\\-16*" in text
    assert "A \\(a\\.example\\)" in text
    assert "B \\(b\\.example\\)" in text
    assert "\U0001f440 Visitors: 1,234 \\(412 new\\), 8,765 last 7 days" in text
    assert "\U0001f51d Top pages:" in text
    assert "pricing — 312" in text
    assert "/pricing" not in text
    # Root path renders as "Homepage" so the line is not empty.
    assert "Homepage — 99" in text

    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.chat_id == GROUP_CHAT_ID
    assert row.response_type == "scheduled_daily_digest"
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
    job = DailyDigestJob(
        sites=TWO_SITES, ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    # Site A's whole section drops out (GA4 was its only source); site B still present.
    assert "A \\(a\\.example\\)" not in text
    assert "B \\(b\\.example\\)" in text
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
    job = DailyDigestJob(
        sites=TWO_SITES, ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    text = tg.sends[0]["text"]
    assert "No data today\\." in text
    assert persistence.responses[0].success is True


async def test_run_does_nothing_when_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(0, 0, 0, ())})
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []


async def test_run_persists_failure_when_send_raises_and_does_not_propagate() -> None:
    tg = _FakeTelegramClient(raises=RuntimeError("network down"))
    persistence = _RecordingPersistence()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(1, 1, 7, ())})
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

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
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

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
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, persistence=_BadPersistence()))

    assert len(tg.sends) == 1


async def test_tally_section_omitted_when_history_empty() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(10, 5, 70, ())})
    history = _FakeJobHistoryLogger(tally=[])
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, job_history_logger=history))

    text = tg.sends[0]["text"]
    assert "Bot Jobs Triggered" not in text


async def test_tally_section_renders_and_sorts_by_total() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(10, 5, 70, ())})
    history = _FakeJobHistoryLogger(
        tally=[
            JobTallyRow(job_name="video_downloader", succeeded=3, failed=1),
            JobTallyRow(job_name="voice_transcription", succeeded=2, failed=0),
            JobTallyRow(job_name="daily-digest", succeeded=1, failed=0),
        ]
    )
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, job_history_logger=history))

    text = tg.sends[0]["text"]
    assert "Bot Jobs Triggered \\(Last 24hrs\\)" in text
    # video_downloader (total 4) comes before voice_transcription (total 2),
    # which comes before daily-digest (total 1). Underscores and dashes are
    # MarkdownV2 specials, so they're backslash-escaped in the rendered text.
    vd_pos = text.index("video\\_downloader")
    vt_pos = text.index("voice\\_transcription")
    dd_pos = text.index("daily\\-digest")
    assert vd_pos < vt_pos < dd_pos
    # Plain success count is shown without the "ok" suffix; failures still get
    # called out on the right when non-zero.
    assert "3, 1 failed" in text
    assert " ok" not in text
    # The other two jobs both have failed=0 so only the success count appears.
    voice_line_end = text.index("\n", vt_pos)
    assert text[vt_pos:voice_line_end].rstrip().endswith("2")


async def test_tally_section_omitted_when_postgres_fails() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": SiteMetrics(10, 5, 70, ())})
    history = _FakeJobHistoryLogger(raises=RuntimeError("pg down"))
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    # Digest still sends, just without the tally section.
    await job.run(_ctx(telegram_client=tg, job_history_logger=history))

    text = tg.sends[0]["text"]
    assert "Bot Jobs Triggered" not in text
    assert "A \\(a\\.example\\)" in text


async def test_tally_only_digest_when_all_sites_fail_but_jobs_ran() -> None:
    """Edge case: GA4 dead but bot did things. The "No data today." fallback
    should *not* fire — the tally counts as data."""
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": GoogleAnalyticsError("ga4 down")})
    history = _FakeJobHistoryLogger(
        tally=[JobTallyRow(job_name="video_downloader", succeeded=2, failed=0)]
    )
    job = DailyDigestJob(
        sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_gsc_unavailable(), now=_fixed_now
    )

    await job.run(_ctx(telegram_client=tg, job_history_logger=history))

    text = tg.sends[0]["text"]
    assert "No data today\\." not in text
    assert "Bot Jobs Triggered \\(Last 24hrs\\)" in text
    assert "video\\_downloader" in text


# --------------------------------------------------------------------------- #
# GSC integration (#51)
# --------------------------------------------------------------------------- #


async def test_run_renders_gsc_line_alongside_ga4() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for(
        {"111": SiteMetrics(total_users=10, new_users=2, total_users_7d=70, top_pages=())}
    )
    gsc = _gsc_fetcher_for({"sc-domain:a.example": SiteSearchMetrics(clicks=45, impressions=1234)})
    job = DailyDigestJob(sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=gsc, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "\U0001f440 Visitors: 10 \\(2 new\\), 70 last 7 days" in text
    assert "\U0001f50d Search: 45 clicks, 1,234 impressions" in text


async def test_run_drops_gsc_line_when_only_gsc_fails() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for(
        {"111": SiteMetrics(total_users=10, new_users=2, total_users_7d=70, top_pages=())}
    )
    gsc = _gsc_fetcher_for({"sc-domain:a.example": GoogleSearchConsoleError("403")})
    job = DailyDigestJob(sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=gsc, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    # GA4 still renders; GSC line is silently absent.
    assert "Visitors: 10" in text
    assert "Search:" not in text
    assert "A \\(a\\.example\\)" in text


async def test_run_keeps_site_section_when_only_ga4_fails_but_gsc_succeeds() -> None:
    """Per-source degradation: GSC alone is enough to keep the site."""
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": GoogleAnalyticsError("ga4 down")})
    gsc = _gsc_fetcher_for({"sc-domain:a.example": SiteSearchMetrics(clicks=99, impressions=2222)})
    job = DailyDigestJob(sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=gsc, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "A \\(a\\.example\\)" in text
    assert "Visitors:" not in text
    assert "\U0001f50d Search: 99 clicks, 2,222 impressions" in text


async def test_run_drops_whole_site_when_both_sources_fail() -> None:
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for({"111": GoogleAnalyticsError("ga4 down")})
    gsc = _gsc_fetcher_for({"sc-domain:a.example": GoogleSearchConsoleError("gsc down")})
    job = DailyDigestJob(sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=gsc, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "A \\(a\\.example\\)" not in text
    assert "No data today\\." in text


async def test_run_skips_gsc_when_site_has_no_gsc_site_url() -> None:
    """Sites with ``gsc_site_url=None`` shouldn't trigger the GSC fetcher at all."""
    site_without_gsc = SiteConfig(label="C", domain="c.example", ga4_property_id="333")
    tg = _FakeTelegramClient()
    ga4 = _ga4_fetcher_for(
        {"333": SiteMetrics(total_users=5, new_users=1, total_users_7d=20, top_pages=())}
    )

    async def _gsc_explodes(_site_url, _start, _end):
        raise AssertionError("GSC fetcher must not be called when gsc_site_url is None")

    job = DailyDigestJob(
        sites=(site_without_gsc,),
        ga4_fetcher=ga4,
        gsc_fetcher=_gsc_explodes,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "C \\(c\\.example\\)" in text
    assert "Search:" not in text
