"""Tests for :mod:`something_really_bot.features.daily_message`."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.daily_message.composer import DailyMessageComposer
from something_really_bot.features.daily_message.handler import DailyMessageJob
from something_really_bot.features.daily_message.schedule import Schedule, SectionEntry
from something_really_bot.features.daily_message.section import Section
from something_really_bot.features.daily_message.sections.fx_rate import FxRateSection
from something_really_bot.features.daily_message.sections.on_this_day import OnThisDaySection
from something_really_bot.features.daily_message.sections.reunion import ReunionSection
from something_really_bot.features.daily_message.sections.weather import WeatherSection
from something_really_bot.features.daily_message.sections.website_stats import WebsiteStatsSection
from something_really_bot.features.daily_message.sites import SiteConfig
from something_really_bot.features.daily_message.sources.google_analytics import (
    SiteMetrics,
    TopPage,
)
from something_really_bot.features.daily_message.sources.google_search_console import (
    SiteSearchMetrics,
)
from something_really_bot.features.daily_message.sources.open_meteo import CityWeather
from something_really_bot.routing.types import BotContext

GROUP_CHAT_ID = -1001234567890
JM_CHAT_ID = 111222333


def _fixed_now() -> datetime:
    return datetime(2026, 5, 25, 5, 5, tzinfo=UTC)


def _friday_now() -> datetime:
    return datetime(2026, 5, 29, 5, 5, tzinfo=UTC)


# ---- Weather fixtures -------------------------------------------------------

AMSTERDAM_WEATHER = CityWeather(
    temp_max=17.0,
    temp_min=14.0,
    apparent_temp_max=16.0,
    weather_description="Partly cloudy",
    wind_speed_max=12.3,
    wind_direction="W",
    humidity_pct=37,
    sunrise="06:23",
    sunset="20:37",
)

MOSCOW_WEATHER = CityWeather(
    temp_max=22.0,
    temp_min=15.0,
    apparent_temp_max=16.0,
    weather_description="Mainly clear",
    wind_speed_max=8.1,
    wind_direction="S",
    humidity_pct=10,
    sunrise="07:13",
    sunset="19:20",
)

WEATHER_MAP = {
    (52.3676, 4.9041): AMSTERDAM_WEATHER,
    (55.7558, 37.6173): MOSCOW_WEATHER,
}


async def _weather_fetcher(lat: float, lon: float, _tz: str) -> CityWeather:
    return WEATHER_MAP[(lat, lon)]


async def _rate_fetcher() -> float:
    return 89.27


async def _otd_fetcher(_today: date) -> str:
    return "1955 — The Austrian State Treaty is signed."


async def _reunion_fetcher() -> date | None:
    return date(2026, 6, 7)


async def _no_duration_fetcher() -> int | None:
    return None


# ---- Website stats fixtures -------------------------------------------------

SITE_A = SiteConfig(
    label="FinCo",
    domain="fintechcompass.net",
    ga4_property_id="280078425",
    gsc_site_url="sc-domain:fintechcompass.net",
)


def _ga4_fetcher_for(values: dict[str, SiteMetrics | BaseException]):
    async def fetch(property_id: str, _start, _end):
        result = values[property_id]
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch


def _gsc_fetcher_for(values: dict[str, SiteSearchMetrics | BaseException]):
    async def fetch(site_url: str, _start, _end):
        result = values[site_url]
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch


# ---- Test infrastructure -----------------------------------------------------


def _all_days() -> frozenset[int]:
    return frozenset(range(7))


def _everyday_schedule(*section_names: str) -> Schedule:
    return Schedule([SectionEntry(name=n, days=_all_days()) for n in section_names])


def _friday_only_schedule(*section_names: str) -> Schedule:
    return Schedule([SectionEntry(name=n, days=frozenset({4})) for n in section_names])


def _settings(*, chat_id: int | None = GROUP_CHAT_ID, jm_chat_id: int | None = None) -> Settings:
    return Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        irindica_chat_id=None,
        something_group_chat_id=chat_id,
        jm_chat_id=jm_chat_id,
    )


def _ctx(
    *,
    chat_id: int | None = GROUP_CHAT_ID,
    jm_chat_id: int | None = None,
    telegram_client: Any = None,
    persistence: Any = None,
) -> BotContext:
    return BotContext(
        settings=_settings(chat_id=chat_id, jm_chat_id=jm_chat_id),
        telegram_client=telegram_client,
        persistence=persistence,
    )


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


# ---- Default sections helper ------------------------------------------------


def _default_sections() -> list[Section]:
    return [
        WeatherSection(weather_fetcher=_weather_fetcher),
        ReunionSection(
            reunion_fetcher=_reunion_fetcher,
            duration_fetcher=_no_duration_fetcher,
        ),
        FxRateSection(rate_fetcher=_rate_fetcher),
        OnThisDaySection(otd_fetcher=_otd_fetcher),
    ]


# =============================================================================
# Happy path
# =============================================================================


async def test_run_happy_path_full_message() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather", "reunion", "fx_rate", "on_this_day")
    job = DailyMessageJob(
        sections=_default_sections(),
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    assert len(tg.sends) == 1
    sent = tg.sends[0]
    assert sent["chat_id"] == GROUP_CHAT_ID
    assert sent["parse_mode"] == "MarkdownV2"
    assert sent["disable_notification"] is True
    text = sent["text"]

    # Header
    assert "*Today \\(2026\\-05\\-25\\)*" in text
    # Amsterdam section — bold city name, feels-like emoji, wind/humidity emoji
    assert "*Amsterdam*" in text
    assert "\\+17\\.\\.\\+14°C \\(feels like \\+16°C" in text
    assert "Partly cloudy" in text
    assert "\U0001f4a8 12 km/h W" in text
    assert "\U0001f4a7 37%" in text
    assert "06:23" in text
    assert "20:37" in text
    # Moscow section — bold city name
    assert "*Moscow*" in text
    assert "\\+22\\.\\.\\+15°C" in text
    assert "Mainly clear" in text
    assert "13 days" in text
    # Exchange rate — trimmed, no "Today's exchange rate:"
    assert "€1" in text
    assert "89,27" in text
    assert "RUB" in text
    assert "exchange rate" not in text.lower()
    # On this day — bold header
    assert "*This day in history:*" in text
    assert "1955" in text
    assert "Austrian State Treaty" in text

    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.chat_id == GROUP_CHAT_ID
    assert row.response_type == "scheduled_daily_message"
    assert row.success is True
    assert row.message_id == 99


# =============================================================================
# Per-source degradation
# =============================================================================


async def test_run_omits_weather_section_for_failed_city() -> None:
    async def _partial_weather(lat: float, lon: float, _tz: str) -> CityWeather:
        if lat == 52.3676:
            raise RuntimeError("Open-Meteo down")
        return MOSCOW_WEATHER

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_partial_weather),
        ReunionSection(reunion_fetcher=_reunion_fetcher, duration_fetcher=_no_duration_fetcher),
    ]
    schedule = _everyday_schedule("weather", "reunion")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "Amsterdam" not in text
    assert "Moscow" in text


async def test_run_omits_rate_section_on_failure() -> None:
    async def _bad_rate() -> float:
        raise RuntimeError("FX API down")

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        FxRateSection(rate_fetcher=_bad_rate),
    ]
    schedule = _everyday_schedule("weather", "fx_rate")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "RUB" not in text
    assert "Amsterdam" in text


async def test_run_omits_otd_section_on_failure() -> None:
    async def _bad_otd(_today: date) -> str:
        raise RuntimeError("Wikipedia down")

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        OnThisDaySection(otd_fetcher=_bad_otd),
    ]
    schedule = _everyday_schedule("weather", "on_this_day")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "history" not in text.lower()
    assert "Amsterdam" in text


async def test_run_shows_not_yet_known_when_reunion_not_set() -> None:
    async def _no_reunion() -> date | None:
        return None

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        ReunionSection(reunion_fetcher=_no_reunion, duration_fetcher=_no_duration_fetcher),
    ]
    schedule = _everyday_schedule("weather", "reunion")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "not yet known" in text
    assert "Amsterdam" in text


async def test_run_omits_reunion_section_when_past() -> None:
    async def _past_reunion() -> date | None:
        return date(2026, 5, 20)

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        ReunionSection(reunion_fetcher=_past_reunion, duration_fetcher=_no_duration_fetcher),
    ]
    schedule = _everyday_schedule("weather", "reunion")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "reunion" not in text.lower()


async def test_run_sends_no_data_when_all_sources_fail() -> None:
    async def _bad_weather(_lat: float, _lon: float, _tz: str) -> CityWeather:
        raise RuntimeError("down")

    async def _bad_rate() -> float:
        raise RuntimeError("down")

    async def _bad_otd(_today: date) -> str:
        raise RuntimeError("down")

    async def _bad_reunion() -> date | None:
        raise RuntimeError("down")

    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_bad_weather),
        ReunionSection(reunion_fetcher=_bad_reunion, duration_fetcher=_no_duration_fetcher),
        FxRateSection(rate_fetcher=_bad_rate),
        OnThisDaySection(otd_fetcher=_bad_otd),
    ]
    schedule = _everyday_schedule("weather", "reunion", "fx_rate", "on_this_day")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    text = tg.sends[0]["text"]
    assert "not yet known" in text
    assert "Amsterdam" not in text
    assert "RUB" not in text
    assert persistence.responses[0].success is True


# =============================================================================
# Error handling
# =============================================================================


async def test_run_does_nothing_when_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []


async def test_run_persists_failure_when_send_raises_and_does_not_propagate() -> None:
    tg = _FakeTelegramClient(raises=RuntimeError("network down"))
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.success is False
    assert "network down" in row.error


async def test_run_handles_missing_telegram_client() -> None:
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
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
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg, persistence=_BadPersistence()))

    assert len(tg.sends) == 1


# =============================================================================
# Milestone messages
# =============================================================================


async def test_run_shows_milestone_at_seven_days() -> None:
    async def _seven_day_reunion() -> date | None:
        return date(2026, 6, 1)

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(
                reunion_fetcher=_seven_day_reunion, duration_fetcher=_no_duration_fetcher
            )
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "one week" in text


async def test_run_shows_today_milestone() -> None:
    async def _today_reunion() -> date | None:
        return date(2026, 5, 25)

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_today_reunion, duration_fetcher=_no_duration_fetcher)
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "TODAY IS THE DAY" in text


# =============================================================================
# Negative temperatures
# =============================================================================


async def test_negative_temperatures_formatted_correctly() -> None:
    cold_weather = CityWeather(
        temp_max=-5.0,
        temp_min=-12.0,
        apparent_temp_max=-8.0,
        weather_description="Heavy snow",
        wind_speed_max=25.0,
        wind_direction="N",
        humidity_pct=85,
        sunrise="09:00",
        sunset="16:30",
    )

    async def _cold_weather(_lat: float, _lon: float, _tz: str) -> CityWeather:
        return cold_weather

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        sections=[WeatherSection(weather_fetcher=_cold_weather)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "\\-5\\.\\.\\-12°C" in text
    assert "feels like \\-8°C" in text


# =============================================================================
# QA variant (chat_id_override)
# =============================================================================


async def test_qa_variant_sends_to_jm_chat_id() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        name="daily-message-qa",
        chat_id_override=lambda s: s.jm_chat_id,
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(jm_chat_id=JM_CHAT_ID, telegram_client=tg, persistence=persistence))

    assert len(tg.sends) == 1
    assert tg.sends[0]["chat_id"] == JM_CHAT_ID
    assert persistence.responses[0].chat_id == JM_CHAT_ID


async def test_qa_variant_skips_when_jm_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    schedule = _everyday_schedule("weather")
    job = DailyMessageJob(
        name="daily-message-qa",
        chat_id_override=lambda s: s.jm_chat_id,
        sections=[WeatherSection(weather_fetcher=_weather_fetcher)],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(jm_chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []


# =============================================================================
# YAML schedule integration
# =============================================================================


async def test_schedule_from_yaml_loads_default() -> None:
    schedule = Schedule.from_yaml()
    # Sunday = 2026-05-25 (weekday 6)
    sunday_sections = schedule.sections_for_day(date(2026, 5, 25))
    assert "weather" in sunday_sections
    assert "reunion" in sunday_sections
    assert "website_stats" not in sunday_sections

    # Friday = 2026-05-29 (weekday 4)
    friday_sections = schedule.sections_for_day(date(2026, 5, 29))
    assert "weather" in friday_sections
    assert "website_stats" in friday_sections


async def test_section_excluded_by_schedule() -> None:
    """Sections not scheduled for the current day are not rendered."""
    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        FxRateSection(rate_fetcher=_rate_fetcher),
    ]
    # Only weather runs on Sundays (2026-05-25 is a Sunday)
    schedule = Schedule(
        [
            SectionEntry(name="weather", days=_all_days()),
            SectionEntry(name="fx_rate", days=frozenset({4})),  # Friday only
        ]
    )
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_fixed_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "Amsterdam" in text
    assert "RUB" not in text


# =============================================================================
# Website stats section (Friday)
# =============================================================================


async def test_website_stats_section_renders_on_friday() -> None:
    ga4 = _ga4_fetcher_for(
        {
            "280078425": SiteMetrics(
                total_users=1234,
                new_users=412,
                total_users_7d=1234,
                top_pages=(
                    TopPage(page_path="/pricing", views=312),
                    TopPage(page_path="/", views=99),
                ),
            ),
        }
    )

    async def _bad_gsc(_url, _s, _e):
        raise RuntimeError("not configured")

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        WebsiteStatsSection(sites=(SITE_A,), ga4_fetcher=ga4, gsc_fetcher=_bad_gsc),
    ]
    schedule = _everyday_schedule("weather", "website_stats")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_friday_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "Weekly Website Stats" in text
    assert "FinCo" in text
    assert "1,234" in text
    assert "pricing" in text
    assert "Homepage" in text


async def test_website_stats_shows_wow_comparison() -> None:
    call_log: list[tuple[str, date, date]] = []

    async def _tracking_ga4(prop_id: str, start: date, end: date) -> SiteMetrics:
        call_log.append((prop_id, start, end))
        if end == date(2026, 5, 28):
            return SiteMetrics(total_users=1000, new_users=200, total_users_7d=1000, top_pages=())
        return SiteMetrics(total_users=800, new_users=150, total_users_7d=800, top_pages=())

    async def _no_gsc(_url, _s, _e):
        raise RuntimeError("not configured")

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WebsiteStatsSection(sites=(SITE_A,), ga4_fetcher=_tracking_ga4, gsc_fetcher=_no_gsc),
    ]
    schedule = _everyday_schedule("website_stats")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_friday_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "1,000" in text
    assert "+25% WoW" in text


async def test_website_stats_omitted_when_all_fail() -> None:
    async def _bad_ga4(_prop, _s, _e):
        raise RuntimeError("ga4 down")

    async def _bad_gsc(_url, _s, _e):
        raise RuntimeError("gsc down")

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WeatherSection(weather_fetcher=_weather_fetcher),
        WebsiteStatsSection(sites=(SITE_A,), ga4_fetcher=_bad_ga4, gsc_fetcher=_bad_gsc),
    ]
    schedule = _everyday_schedule("weather", "website_stats")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_friday_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "Weekly Website Stats" not in text
    assert "Amsterdam" in text


async def test_website_stats_gsc_renders_with_wow() -> None:
    async def _bad_ga4(_prop, _s, _e):
        raise RuntimeError("ga4 down")

    async def _gsc_with_prev(url: str, start: date, end: date) -> SiteSearchMetrics:
        if end == date(2026, 5, 28):
            return SiteSearchMetrics(clicks=100, impressions=5000)
        return SiteSearchMetrics(clicks=80, impressions=4000)

    tg = _FakeTelegramClient()
    sections: list[Section] = [
        WebsiteStatsSection(sites=(SITE_A,), ga4_fetcher=_bad_ga4, gsc_fetcher=_gsc_with_prev),
    ]
    schedule = _everyday_schedule("website_stats")
    job = DailyMessageJob(sections=sections, schedule=schedule, now=_friday_now)

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "100 clicks" in text
    assert "+25% WoW" in text
    assert "5,000 impressions" in text


# =============================================================================
# Composer unit tests
# =============================================================================


async def test_composer_no_data_message() -> None:
    async def _bad_weather(_lat: float, _lon: float, _tz: str) -> CityWeather:
        raise RuntimeError("down")

    schedule = _everyday_schedule("weather")
    composer = DailyMessageComposer(
        sections=[WeatherSection(weather_fetcher=_bad_weather)],
        schedule=schedule,
    )
    text = await composer.compose(date(2026, 5, 25))
    assert "No data available today\\." in text


async def test_composer_empty_schedule() -> None:
    schedule = Schedule([])
    composer = DailyMessageComposer(sections=_default_sections(), schedule=schedule)
    text = await composer.compose(date(2026, 5, 25))
    assert "No data available today\\." in text


# =============================================================================
# Reunion duration (#60)
# =============================================================================


async def test_run_shows_enjoying_message_during_reunion() -> None:
    """Day 0 of a reunion with duration set shows 'enjoying' message."""

    async def _today_reunion() -> date | None:
        return date(2026, 5, 25)

    async def _duration() -> int | None:
        return 5

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_today_reunion, duration_fetcher=_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "enjoying time together" in text


async def test_run_shows_enjoying_mid_reunion() -> None:
    """Day 2 of a 5-day reunion shows 'enjoying' message."""

    async def _started_reunion() -> date | None:
        return date(2026, 5, 23)

    async def _duration() -> int | None:
        return 5

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_started_reunion, duration_fetcher=_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "enjoying time together" in text


async def test_run_shows_not_yet_known_after_reunion_duration_expires() -> None:
    """After reunion + duration has passed, shows 'not yet known'."""

    async def _past_reunion() -> date | None:
        return date(2026, 5, 20)

    async def _duration() -> int | None:
        return 3

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_past_reunion, duration_fetcher=_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "not yet known" in text


async def test_run_shows_countdown_before_reunion_with_duration() -> None:
    """Before the reunion starts, normal countdown is shown even with duration set."""

    async def _future_reunion() -> date | None:
        return date(2026, 6, 7)

    async def _duration() -> int | None:
        return 5

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_future_reunion, duration_fetcher=_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "13 days" in text
    assert "enjoying" not in text


async def test_run_shows_enjoying_on_last_day_of_reunion() -> None:
    """The last day of reunion (target + duration - 1) still shows 'enjoying'."""

    async def _reunion_start() -> date | None:
        return date(2026, 5, 22)

    async def _duration() -> int | None:
        return 4

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_reunion_start, duration_fetcher=_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "enjoying time together" in text


async def test_run_duration_fetcher_failure_falls_back_to_no_duration() -> None:
    """If duration fetch fails, behave as if no duration is set."""

    async def _future_reunion() -> date | None:
        return date(2026, 6, 7)

    async def _bad_duration() -> int | None:
        raise RuntimeError("PG down")

    tg = _FakeTelegramClient()
    schedule = _everyday_schedule("reunion")
    job = DailyMessageJob(
        sections=[
            ReunionSection(reunion_fetcher=_future_reunion, duration_fetcher=_bad_duration),
        ],
        schedule=schedule,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "13 days" in text
