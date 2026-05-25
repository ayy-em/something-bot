"""Weather section: per-city forecast for Amsterdam and Moscow."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

from something_really_bot.features.daily_message.cities import CITIES, CityConfig
from something_really_bot.features.daily_message.markdown import fmt_temp, md
from something_really_bot.features.daily_message.sources.open_meteo import (
    CityWeather,
    fetch_city_weather,
)
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

WeatherFetcher = Callable[[float, float, str], Awaitable[CityWeather]]


class WeatherSection:
    """Renders per-city weather forecasts."""

    name = "weather"

    def __init__(
        self,
        *,
        cities: tuple[CityConfig, ...] = CITIES,
        weather_fetcher: WeatherFetcher | None = None,
    ) -> None:
        self._cities = cities
        self._weather_fetcher = weather_fetcher or fetch_city_weather

    async def render(self, today: date) -> str | None:
        """Fetch weather for all cities and compose MarkdownV2 blocks."""
        results = await asyncio.gather(*(self._safe_fetch(c) for c in self._cities))
        blocks: list[str] = []
        for city, weather in zip(self._cities, results, strict=True):
            if weather is not None:
                blocks.append(self._format_city(city, weather))
        return "\n\n".join(blocks) if blocks else None

    async def _safe_fetch(self, city: CityConfig) -> CityWeather | None:
        try:
            return await self._weather_fetcher(city.latitude, city.longitude, city.timezone)
        except BaseException as exc:
            _logger.warning(
                "daily_message_weather_failed",
                extra={"city": city.name, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    def _format_city(self, city: CityConfig, w: CityWeather) -> str:
        feels_emoji = _feels_like_emoji(w.apparent_temp_max)
        temp_line = (
            f"{md(fmt_temp(w.temp_max))}\\.\\.{md(fmt_temp(w.temp_min))}°C "
            f"\\(feels like {md(fmt_temp(w.apparent_temp_max))}°C {feels_emoji}\\)"
        )
        condition_line = (
            f"{md(w.weather_description)}, "
            f"\U0001f4a8 {md(str(round(w.wind_speed_max)))} km/h {md(w.wind_direction)}, "
            f"\U0001f4a7 {md(str(w.humidity_pct))}%"
        )
        sun_line = f"Sunrise at {md(w.sunrise)}, sunset at {md(w.sunset)}\\."
        return f"{city.flag} *{md(city.name)}*\n{temp_line}\n{condition_line}\n{sun_line}"


def _feels_like_emoji(temp: float) -> str:
    """Pick an emoji based on the feels-like temperature."""
    if temp > 30:
        return "\U0001f321️"
    if temp > 20:
        return "\U0001f60e"
    if temp > 10:
        return "\U0001f324️"
    if temp > 5:
        return "\U0001f32c️"
    return "\U0001f976"
