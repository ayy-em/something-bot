"""Weather data from the Open-Meteo forecast API.

Fetches daily high/low temperature, apparent (feels-like) temperature,
weather condition (WMO code), wind speed and direction, humidity
(averaged from hourly data), and sunrise/sunset times.
"""

from dataclasses import dataclass

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoError(Exception):
    """Raised when the Open-Meteo API call fails or returns unexpected data."""


@dataclass(frozen=True)
class CityWeather:
    """Daily weather summary for one city."""

    temp_max: float
    temp_min: float
    apparent_temp_max: float
    weather_description: str
    wind_speed_max: float
    wind_direction: str
    humidity_pct: int
    sunrise: str
    sunset: str


WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _degrees_to_cardinal(degrees: float) -> str:
    """Convert wind direction in degrees to a cardinal direction string."""
    directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    idx = round(degrees / 45) % 8
    return directions[idx]


async def fetch_city_weather(
    latitude: float,
    longitude: float,
    timezone: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> CityWeather:
    """Fetch today's weather forecast for a single city.

    Args:
        latitude: City latitude.
        longitude: City longitude.
        timezone: IANA timezone string (e.g. ``Europe/Amsterdam``).
        http_client: Optional pre-built client for connection reuse / testing.

    Returns:
        A populated :class:`CityWeather` with today's forecast.

    Raises:
        OpenMeteoError: API call failed or returned unexpected structure.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "apparent_temperature_max",
                "weather_code",
                "sunrise",
                "sunset",
                "wind_speed_10m_max",
                "wind_direction_10m_dominant",
            ]
        ),
        "hourly": "relative_humidity_2m",
        "timezone": timezone,
        "forecast_days": 1,
    }

    try:
        if http_client is not None:
            resp = await http_client.get(OPEN_METEO_URL, params=params)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise OpenMeteoError(f"API request failed: {exc}") from exc

    try:
        daily = data["daily"]
        hourly = data.get("hourly", {})

        humidity_values = hourly.get("relative_humidity_2m", [])
        avg_humidity = round(sum(humidity_values) / len(humidity_values)) if humidity_values else 0

        sunrise_raw: str = daily["sunrise"][0]
        sunset_raw: str = daily["sunset"][0]
        sunrise_time = sunrise_raw.split("T")[1] if "T" in sunrise_raw else sunrise_raw
        sunset_time = sunset_raw.split("T")[1] if "T" in sunset_raw else sunset_raw

        weather_code = daily["weather_code"][0]

        return CityWeather(
            temp_max=daily["temperature_2m_max"][0],
            temp_min=daily["temperature_2m_min"][0],
            apparent_temp_max=daily["apparent_temperature_max"][0],
            weather_description=WMO_CODES.get(weather_code, f"Code {weather_code}"),
            wind_speed_max=daily["wind_speed_10m_max"][0],
            wind_direction=_degrees_to_cardinal(daily["wind_direction_10m_dominant"][0]),
            humidity_pct=avg_humidity,
            sunrise=sunrise_time,
            sunset=sunset_time,
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenMeteoError(f"Unexpected API response structure: {exc}") from exc
