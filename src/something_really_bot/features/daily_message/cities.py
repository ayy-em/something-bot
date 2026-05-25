"""City configuration for the daily weather section."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CityConfig:
    """Geographic and display config for one city."""

    name: str
    flag: str
    latitude: float
    longitude: float
    timezone: str


CITIES: tuple[CityConfig, ...] = (
    CityConfig(
        name="Amsterdam",
        flag="\U0001f1f3\U0001f1f1",
        latitude=52.3676,
        longitude=4.9041,
        timezone="Europe/Amsterdam",
    ),
    CityConfig(
        name="Moscow",
        flag="\U0001f1f7\U0001f1fa",
        latitude=55.7558,
        longitude=37.6173,
        timezone="Europe/Moscow",
    ),
)
