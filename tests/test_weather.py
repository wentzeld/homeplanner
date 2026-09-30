from datetime import date

import httpx
import pytest

from homeplanner.config import load_config
from homeplanner.demo import demo_weather_fetcher
from homeplanner.weather import (
    OPEN_METEO_URL,
    WeatherService,
    describe,
    open_meteo_fetcher,
    parse_open_meteo,
)
from tests.test_config import EXAMPLE

CONFIG = load_config(EXAMPLE)

SAMPLE = {
    "current": {"time": "2026-10-05T09:00", "temperature_2m": 55.4, "weather_code": 61},
    "daily": {
        "time": ["2026-10-05", "2026-10-06"],
        "weather_code": [61, 0],
        "temperature_2m_max": [58.1, 63.0],
        "temperature_2m_min": [48.2, 50.5],
        "precipitation_probability_max": [80, 5],
    },
}


def test_parse_open_meteo():
    r = parse_open_meteo(SAMPLE, "fahrenheit")
    assert r.unit == "F"
    assert r.current.temperature == 55.4
    assert (r.current.description, r.current.icon) == ("Light rain", "rain")
    assert [d.date for d in r.daily] == [date(2026, 10, 5), date(2026, 10, 6)]
    assert r.daily[1].high == 63.0 and r.daily[1].low == 50.5
    assert r.daily[0].precipitation_chance == 80
    assert r.daily[1].icon == "clear"


def test_parse_without_precipitation():
    data = {**SAMPLE, "daily": {k: v for k, v in SAMPLE["daily"].items()
                                if k != "precipitation_probability_max"}}
    r = parse_open_meteo(data, "celsius")
    assert r.unit == "C"
    assert r.daily[0].precipitation_chance is None


def test_unknown_code():
    assert describe(1234) == ("Unknown", "cloudy")


async def test_open_meteo_fetcher_sends_config(no_network):
    route = no_network.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=SAMPLE))
    async with httpx.AsyncClient() as client:
        data = await open_meteo_fetcher(CONFIG, client)()
    assert data == SAMPLE
    params = route.calls.last.request.url.params
    assert params["latitude"] == "47.61" and params["longitude"] == "-122.33"
    assert params["temperature_unit"] == "fahrenheit"
    assert params["timezone"] == "America/Los_Angeles"


async def test_service_keeps_last_report_on_error(no_network):
    route = no_network.get(OPEN_METEO_URL).mock(return_value=httpx.Response(200, json=SAMPLE))
    async with httpx.AsyncClient() as client:
        svc = WeatherService(open_meteo_fetcher(CONFIG, client), CONFIG)
        assert await svc.refresh()
        first = svc.report
        route.mock(return_value=httpx.Response(500))
        assert not await svc.refresh()
    assert svc.report == first
    assert "500" in svc.last_error


@pytest.mark.parametrize("unit", ["celsius", "fahrenheit"])
async def test_demo_weather_parses(unit):
    cfg = CONFIG.model_copy(update={"weather": CONFIG.weather.model_copy(update={"temperature_unit": unit})})
    svc = WeatherService(demo_weather_fetcher(cfg, date(2026, 10, 5)), cfg)
    assert await svc.refresh()
    assert len(svc.report.daily) == 16 and svc.report.daily[0].date == date(2026, 10, 5)


async def test_weather_retries_quickly_while_failing(no_network):
    from homeplanner.weather import WEATHER_INTERVAL_SECONDS, WEATHER_RETRY_SECONDS

    route = no_network.get(OPEN_METEO_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        svc = WeatherService(open_meteo_fetcher(CONFIG, client), CONFIG)
        await svc.refresh()
        assert svc.next_delay() == WEATHER_RETRY_SECONDS == 60
        route.mock(return_value=httpx.Response(200, json=SAMPLE))
        await svc.refresh()
        assert svc.next_delay() == WEATHER_INTERVAL_SECONDS == 30 * 60
