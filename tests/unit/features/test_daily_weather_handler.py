"""Tests for :mod:`something_really_bot.features.daily_weather.handler`."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.daily_weather.handler import DailyWeatherJob
from something_really_bot.features.daily_weather.sources.open_meteo import CityWeather
from something_really_bot.routing.types import BotContext

GROUP_CHAT_ID = -1001234567890


def _fixed_now() -> datetime:
    return datetime(2026, 5, 25, 5, 5, tzinfo=UTC)


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


JM_CHAT_ID = 111222333


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


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_run_happy_path_full_message() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
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
    # Amsterdam section
    assert "Amsterdam" in text
    assert "\\+17\\.\\.\\+14°C \\(feels like \\+16°C\\)" in text
    assert "Partly cloudy" in text
    assert "wind 12 km/h W" in text
    assert "37% humidity" in text
    assert "06:23" in text
    assert "20:37" in text
    # Moscow section
    assert "Moscow" in text
    assert "\\+22\\.\\.\\+15°C" in text
    assert "Mainly clear" in text
    # Reunion
    assert "reunion" in text.lower()
    assert "13 days" in text
    # Exchange rate
    assert "89,27" in text
    assert "RUB" in text
    # On this day
    assert "1955" in text
    assert "Austrian State Treaty" in text

    assert len(persistence.responses) == 1
    row = persistence.responses[0]
    assert row.chat_id == GROUP_CHAT_ID
    assert row.response_type == "scheduled_daily_weather"
    assert row.success is True
    assert row.message_id == 99


# --------------------------------------------------------------------------- #
# Per-source degradation
# --------------------------------------------------------------------------- #


async def test_run_omits_weather_section_for_failed_city() -> None:
    async def _partial_weather(lat: float, lon: float, _tz: str) -> CityWeather:
        if lat == 52.3676:
            raise RuntimeError("Open-Meteo down")
        return MOSCOW_WEATHER

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_partial_weather,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "Amsterdam" not in text
    assert "Moscow" in text


async def test_run_omits_rate_section_on_failure() -> None:
    async def _bad_rate() -> float:
        raise RuntimeError("FX API down")

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_bad_rate,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "RUB" not in text
    assert "Amsterdam" in text


async def test_run_omits_otd_section_on_failure() -> None:
    async def _bad_otd(_today: date) -> str:
        raise RuntimeError("Wikipedia down")

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_bad_otd,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "history" not in text.lower()
    assert "Amsterdam" in text


async def test_run_shows_not_yet_known_when_reunion_not_set() -> None:
    async def _no_reunion() -> date | None:
        return None

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_no_reunion,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "not yet known" in text
    assert "Amsterdam" in text


async def test_run_omits_reunion_section_when_past() -> None:
    async def _past_reunion() -> date | None:
        return date(2026, 5, 20)

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_past_reunion,
        now=_fixed_now,
    )

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
    job = DailyWeatherJob(
        weather_fetcher=_bad_weather,
        rate_fetcher=_bad_rate,
        otd_fetcher=_bad_otd,
        reunion_fetcher=_bad_reunion,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg, persistence=persistence))

    text = tg.sends[0]["text"]
    assert "not yet known" in text
    assert "Amsterdam" not in text
    assert "RUB" not in text
    assert persistence.responses[0].success is True


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


async def test_run_does_nothing_when_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []


async def test_run_persists_failure_when_send_raises_and_does_not_propagate() -> None:
    tg = _FakeTelegramClient(raises=RuntimeError("network down"))
    persistence = _RecordingPersistence()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
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
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
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
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg, persistence=_BadPersistence()))

    assert len(tg.sends) == 1


# --------------------------------------------------------------------------- #
# Milestone messages
# --------------------------------------------------------------------------- #


async def test_run_shows_milestone_at_seven_days() -> None:
    async def _seven_day_reunion() -> date | None:
        return date(2026, 6, 1)

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_seven_day_reunion,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "7 days" in text
    assert "One week to go" in text


async def test_run_shows_today_milestone() -> None:
    async def _today_reunion() -> date | None:
        return date(2026, 5, 25)

    tg = _FakeTelegramClient()
    job = DailyWeatherJob(
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_today_reunion,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "TODAY IS THE DAY" in text


# --------------------------------------------------------------------------- #
# Negative temperatures
# --------------------------------------------------------------------------- #


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
    job = DailyWeatherJob(
        weather_fetcher=_cold_weather,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(telegram_client=tg))

    text = tg.sends[0]["text"]
    assert "\\-5\\.\\.\\-12°C" in text
    assert "feels like \\-8°C" in text


# --------------------------------------------------------------------------- #
# QA variant (chat_id_override)
# --------------------------------------------------------------------------- #


async def test_qa_variant_sends_to_jm_chat_id() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = DailyWeatherJob(
        name="daily-weather-qa",
        chat_id_override=lambda s: s.jm_chat_id,
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(jm_chat_id=JM_CHAT_ID, telegram_client=tg, persistence=persistence))

    assert len(tg.sends) == 1
    assert tg.sends[0]["chat_id"] == JM_CHAT_ID
    assert persistence.responses[0].chat_id == JM_CHAT_ID


async def test_qa_variant_skips_when_jm_chat_id_missing() -> None:
    tg = _FakeTelegramClient()
    persistence = _RecordingPersistence()
    job = DailyWeatherJob(
        name="daily-weather-qa",
        chat_id_override=lambda s: s.jm_chat_id,
        weather_fetcher=_weather_fetcher,
        rate_fetcher=_rate_fetcher,
        otd_fetcher=_otd_fetcher,
        reunion_fetcher=_reunion_fetcher,
        now=_fixed_now,
    )

    await job.run(_ctx(jm_chat_id=None, telegram_client=tg, persistence=persistence))

    assert tg.sends == []
    assert persistence.responses == []
