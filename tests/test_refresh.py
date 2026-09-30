import asyncio

from fastapi.testclient import TestClient

from homeplanner.config import load_config
from homeplanner.main import create_app
from tests.fakes import FakeCalendar
from tests.test_config import EXAMPLE
from tests.test_weather import SAMPLE

CONFIG = load_config(EXAMPLE)


def test_refresh_updates_family_calendar_external_and_weather(tmp_path):
    calls = {"weather": 0}

    async def weather():
        calls["weather"] += 1
        return SAMPLE

    cal = FakeCalendar()
    config = CONFIG.model_copy(update={"cache_dir": tmp_path / "cache", "data_dir": tmp_path / "data"})
    with TestClient(create_app(config=config, demo=False, source=cal, weather_fetch=weather)) as c:
        before_lists, before_weather = len(cal.list_calls), calls["weather"]
        r = c.post("/api/refresh")
        assert r.status_code == 200 and r.json()["error"] is None and r.json()["last_synced"]
        assert len(cal.list_calls) == before_lists + 1
        assert calls["weather"] == before_weather + 1


def test_concurrent_refreshes_share_one_run(tmp_path):
    class SlowCalendar(FakeCalendar):
        async def list_events(self, start, end):
            await asyncio.sleep(0.2)
            return await super().list_events(start, end)

    async def weather():
        return SAMPLE

    cal = SlowCalendar()
    config = CONFIG.model_copy(update={"cache_dir": tmp_path / "cache", "data_dir": tmp_path / "data"})
    app = create_app(config=config, demo=False, source=cal, weather_fetch=weather)
    with TestClient(app) as c:
        import time
        time.sleep(0.3)  # let the start-up sync finish
        before = len(cal.list_calls)

        async def burst():
            import httpx
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return await asyncio.gather(*(client.post("/api/refresh") for _ in range(5)))

        responses = c.portal.call(burst)
        assert all(r.status_code == 200 for r in responses)
        assert len(cal.list_calls) == before + 1
