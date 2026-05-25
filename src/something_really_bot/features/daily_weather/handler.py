"""Daily weather forecast job (#58).

Cloud Scheduler fires ``POST /jobs/daily-weather`` once a day at 05:05 UTC
(= 08:05 MSK / 07:05 CEST). The job fetches weather for Amsterdam and
Moscow, a EUR/RUB exchange rate, a "this day in history" fact, and the
next-reunion countdown, then composes a single MarkdownV2 message sent
silently to ``settings.something_group_chat_id``.

Each data source is fetched independently with graceful per-source
degradation: a failure in one section omits that section only.

The job never raises — failure to send is logged + persisted with
``success=false`` and the HTTP response stays 200 so Cloud Scheduler
does not retry and double-send.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

from something_really_bot.config import Settings
from something_really_bot.features.daily_weather.cities import CITIES, CityConfig
from something_really_bot.features.daily_weather.sources.fx_rates import (
    fetch_eur_rub_rate,
)
from something_really_bot.features.daily_weather.sources.open_meteo import (
    CityWeather,
    fetch_city_weather,
)
from something_really_bot.features.daily_weather.sources.wikipedia_otd import (
    fetch_on_this_day,
)
from something_really_bot.logging import get_logger
from something_really_bot.persistence import ResponseRecord
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)

_MARKDOWN_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"
_MARKDOWN_V2_TABLE = str.maketrans({ch: f"\\{ch}" for ch in _MARKDOWN_V2_SPECIAL})


def _md(text: str) -> str:
    """Escape ``text`` for Telegram MarkdownV2."""
    return text.translate(_MARKDOWN_V2_TABLE)


def _fmt_temp(temp: float) -> str:
    """Format a temperature value with explicit sign prefix."""
    rounded = round(temp)
    return f"+{rounded}" if rounded >= 0 else str(rounded)


WeatherFetcher = Callable[[float, float, str], Awaitable[CityWeather]]
RateFetcher = Callable[[], Awaitable[float]]
OTDFetcher = Callable[[date], Awaitable[str]]
ReunionFetcher = Callable[[], Awaitable[date | None]]


async def _default_reunion_fetcher() -> date | None:
    from something_really_bot.features.daily_weather.reunion import get_reunion_date
    from something_really_bot.persistence.postgres import get_postgres_storage

    storage = get_postgres_storage()
    if storage is None:
        return None
    return await get_reunion_date(storage)


class DailyWeatherJob:
    """Scheduled job: daily weather forecast for Amsterdam and Moscow."""

    def __init__(
        self,
        *,
        name: str = "daily-weather",
        chat_id_override: Callable[[Settings], int | None] | None = None,
        cities: tuple[CityConfig, ...] = CITIES,
        weather_fetcher: WeatherFetcher | None = None,
        rate_fetcher: RateFetcher | None = None,
        otd_fetcher: OTDFetcher | None = None,
        reunion_fetcher: ReunionFetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.name = name
        self._chat_id_override = chat_id_override
        self._cities = cities
        self._weather_fetcher = weather_fetcher or fetch_city_weather
        self._rate_fetcher = rate_fetcher or fetch_eur_rub_rate
        self._otd_fetcher = otd_fetcher or fetch_on_this_day
        self._reunion_fetcher = reunion_fetcher or _default_reunion_fetcher
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, ctx: BotContext) -> None:
        """Execute the daily weather job."""
        if self._chat_id_override is not None:
            chat_id = self._chat_id_override(ctx.settings)
        else:
            chat_id = ctx.settings.something_group_chat_id
        if chat_id is None:
            _logger.error("daily_weather_no_recipient_skipping")
            return

        today = self._now().date()

        weather_results, rate, otd_text, reunion_date = await asyncio.gather(
            self._fetch_all_weather(),
            self._safe_fetch_rate(),
            self._safe_fetch_otd(today),
            self._safe_fetch_reunion(),
        )

        text = self._compose_message(today, weather_results, rate, otd_text, reunion_date)
        await self._send_and_persist(ctx, chat_id, text)

    async def _fetch_all_weather(self) -> list[tuple[CityConfig, CityWeather | None]]:
        """Fetch weather for all cities in parallel."""
        results = await asyncio.gather(*(self._safe_fetch_weather(city) for city in self._cities))
        return list(zip(self._cities, results, strict=True))

    async def _safe_fetch_weather(self, city: CityConfig) -> CityWeather | None:
        try:
            return await self._weather_fetcher(city.latitude, city.longitude, city.timezone)
        except BaseException as exc:
            _logger.warning(
                "daily_weather_weather_failed",
                extra={"city": city.name, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def _safe_fetch_rate(self) -> float | None:
        try:
            return await self._rate_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_weather_rate_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def _safe_fetch_otd(self, today: date) -> str | None:
        try:
            return await self._otd_fetcher(today)
        except BaseException as exc:
            _logger.warning(
                "daily_weather_otd_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def _safe_fetch_reunion(self) -> date | None:
        try:
            return await self._reunion_fetcher()
        except BaseException as exc:
            _logger.warning(
                "daily_weather_reunion_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    def _compose_message(
        self,
        today: date,
        weather_results: list[tuple[CityConfig, CityWeather | None]],
        rate: float | None,
        otd_text: str | None,
        reunion_date: date | None,
    ) -> str:
        header = f"*Today \\({_md(today.isoformat())}\\)*"
        sections: list[str] = [header]

        for city, weather in weather_results:
            section = self._compose_city_section(city, weather)
            if section is not None:
                sections.append(section)

        if reunion_date is not None:
            from something_really_bot.features.daily_weather.reunion import (
                format_reunion_line,
            )

            reunion_line = format_reunion_line(reunion_date, today)
            if reunion_line is not None:
                sections.append(_md(reunion_line))

        if rate is not None:
            rate_str = f"{rate:.2f}".replace(".", ",")
            rate_text = f"Today's exchange rate: €1 = {rate_str} RUB."
            sections.append(f"\U0001f4b6 {_md(rate_text)}")

        if otd_text is not None:
            sections.append(f"\U0001f4dc{_md('This day in history:')}\n{_md(otd_text)}")

        if len(sections) == 1:
            return f"{header}\n\nNo data available today\\."

        return "\n\n".join(sections)

    def _compose_city_section(self, city: CityConfig, weather: CityWeather | None) -> str | None:
        if weather is None:
            return None

        w = weather
        temp_line = (
            f"{_md(_fmt_temp(w.temp_max))}\\.\\.{_md(_fmt_temp(w.temp_min))}°C "
            f"\\(feels like {_md(_fmt_temp(w.apparent_temp_max))}°C\\)"
        )
        condition_line = (
            f"{_md(w.weather_description)}, "
            f"wind {_md(str(round(w.wind_speed_max)))} km/h {_md(w.wind_direction)}, "
            f"{_md(str(w.humidity_pct))}% humidity"
        )
        sun_line = f"Sunrise at {_md(w.sunrise)}, sunset at {_md(w.sunset)}\\."

        return f"{city.flag} {_md(city.name)}\n{temp_line}\n{condition_line}\n{sun_line}"

    async def _send_and_persist(self, ctx: BotContext, chat_id: int, text: str) -> None:
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
                response = await client.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                _logger.warning("daily_weather_send_failed", extra={"error": error})
            else:
                success = True
                message_id = response.get("message_id") if isinstance(response, dict) else None

        if ctx.persistence is not None:
            try:
                ctx.persistence.record_response(
                    ResponseRecord(
                        bot_id=ctx.bot_id,
                        chat_id=chat_id,
                        response_type="scheduled_daily_weather",
                        text=text,
                        sent_at=sent_at,
                        success=success,
                        error=error,
                        message_id=message_id,
                    )
                )
            except Exception:  # noqa: BLE001
                _logger.exception("daily_weather_persist_response_raised")
