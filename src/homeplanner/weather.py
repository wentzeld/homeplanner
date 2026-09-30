"""Open-Meteo weather. No API key needed; refreshed every 30 min and cached."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime

import httpx
from pydantic import BaseModel

from homeplanner.config import Config

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_INTERVAL_SECONDS = 30 * 60
WEATHER_RETRY_SECONDS = 60  # while failing, e.g. network not up yet after boot

# WMO weather codes -> (description, icon category used by the frontend).
_WMO = {
    0: ("Clear", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "partly-cloudy"),
    3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"),
    57: ("Freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"),
    81: ("Rain showers", "rain"),
    82: ("Violent rain showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunderstorm with hail", "thunder"),
    99: ("Thunderstorm with hail", "thunder"),
}


def describe(code: int) -> tuple[str, str]:
    return _WMO.get(code, ("Unknown", "cloudy"))


class CurrentWeather(BaseModel):
    temperature: float
    description: str
    icon: str


class DailyForecast(BaseModel):
    date: date
    high: float
    low: float
    precipitation_chance: int | None
    description: str
    icon: str


class WeatherReport(BaseModel):
    unit: str  # "C" or "F"
    current: CurrentWeather
    daily: list[DailyForecast]


def parse_open_meteo(data: dict, temperature_unit: str) -> WeatherReport:
    cur = data["current"]
    desc, icon = describe(cur["weather_code"])
    d = data["daily"]
    precip = d.get("precipitation_probability_max") or [None] * len(d["time"])
    daily = []
    for day, code, hi, lo, p in zip(
        d["time"], d["weather_code"], d["temperature_2m_max"], d["temperature_2m_min"], precip
    ):
        day_desc, day_icon = describe(code)
        daily.append(DailyForecast(
            date=date.fromisoformat(day), high=hi, low=lo, precipitation_chance=p,
            description=day_desc, icon=day_icon,
        ))
    return WeatherReport(
        unit="F" if temperature_unit == "fahrenheit" else "C",
        current=CurrentWeather(temperature=cur["temperature_2m"], description=desc, icon=icon),
        daily=daily,
    )


def open_meteo_fetcher(config: Config, client: httpx.AsyncClient) -> Callable[[], Awaitable[dict]]:
    params = {
        "latitude": config.weather.latitude,
        "longitude": config.weather.longitude,
        "current": "temperature_2m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": config.weather.temperature_unit,
        "timezone": config.timezone,
        "forecast_days": 16,
    }

    async def fetch() -> dict:
        resp = await client.get(OPEN_METEO_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    return fetch


class WeatherService:
    def __init__(self, fetch: Callable[[], Awaitable[dict]], config: Config):
        self._fetch = fetch
        self._config = config
        self.report: WeatherReport | None = None
        self.last_updated: datetime | None = None
        self.last_error: str | None = None

    async def refresh(self) -> bool:
        """Fetch new weather. On failure, keep the previous report and record the error."""
        try:
            self.report = parse_open_meteo(await self._fetch(), self._config.weather.temperature_unit)
        except Exception as e:  # network or unexpected payload: keep last report
            log.warning("weather refresh failed: %s", e)
            self.last_error = str(e) or type(e).__name__
            return False
        self.last_updated, self.last_error = datetime.now(self._config.tz), None
        return True

    def next_delay(self) -> float:
        return WEATHER_RETRY_SECONDS if self.last_error else WEATHER_INTERVAL_SECONDS

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.next_delay())
