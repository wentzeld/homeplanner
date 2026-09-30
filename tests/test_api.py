from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from homeplanner.config import load_config
from homeplanner.events import GOOGLE_EVENT_COLORS
from homeplanner.main import create_app
from tests.fakes import FakeCalendar, all_day
from tests.test_config import EXAMPLE
from tests.test_weather import SAMPLE

CONFIG = load_config(EXAMPLE)


def today() -> date:
    return datetime.now(CONFIG.tz).date()


async def sample_weather() -> dict:
    return SAMPLE


@pytest.fixture
def cal():
    return FakeCalendar([
        all_day("a", today(), title="Today thing", colorId="9"),
        all_day("b", today() + timedelta(days=7), title="Next week"),
    ])


@pytest.fixture
def client(cal, tmp_path):
    config = CONFIG.model_copy(update={"cache_dir": tmp_path})
    app = create_app(config=config, demo=False, source=cal, weather_fetch=sample_weather)
    with TestClient(app) as c:
        yield c


def test_config_endpoint(client):
    body = client.get("/api/config").json()
    assert body["timezone"] == "America/Los_Angeles"
    assert body["today"] == today().isoformat()
    assert body["members"] == [
        {"name": "Alex", "color": GOOGLE_EVENT_COLORS["9"]},
        {"name": "Sam", "color": GOOGLE_EVENT_COLORS["11"]},
    ]


def test_events_default_is_five_days_from_today(client):
    body = client.get("/api/events").json()
    assert body["start"] == today().isoformat()
    assert len(body["days"]) == 5
    first = body["days"][0]
    assert first["date"] == today().isoformat()
    assert [(e["title"], e["member"], e["all_day"]) for e in first["events"]] == [
        ("Today thing", "Alex", True)
    ]
    assert body["error"] is None and body["last_synced"] is not None


def test_events_next_week(client):
    start = today() + timedelta(days=5)
    body = client.get("/api/events", params={"start": start.isoformat(), "days": 5}).json()
    titles = [e["title"] for d in body["days"] for e in d["events"]]
    assert titles == ["Next week"]


@pytest.mark.parametrize("days", [0, 32])
def test_events_days_bounds(client, days):
    assert client.get("/api/events", params={"days": days}).status_code == 422


def test_add_event(client, cal):
    r = client.post("/api/events", json={
        "title": "Soccer", "date": today().isoformat(), "start_time": "17:00", "end_time": "18:00",
        "member": "Sam", "recurrence": "weekly",
    })
    assert r.status_code == 201
    assert r.json()["title"] == "Soccer" and r.json()["member"] == "Sam"
    assert cal.inserted[0]["colorId"] == "11"
    assert cal.inserted[0]["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    titles = [e["title"] for e in client.get("/api/events").json()["days"][0]["events"]]
    assert "Soccer" in titles  # visible immediately


def test_add_event_validation_error(client, cal):
    r = client.post("/api/events", json={"title": "", "date": today().isoformat(), "all_day": True})
    assert r.status_code == 422
    assert cal.inserted == []


def test_add_event_unknown_member(client, cal):
    r = client.post("/api/events", json={
        "title": "x", "date": today().isoformat(), "all_day": True, "member": "Nobody"})
    assert r.status_code == 422
    assert "unknown member" in r.json()["detail"]
    assert cal.inserted == []


def test_add_event_google_down(client, cal):
    cal.fail = True
    r = client.post("/api/events", json={"title": "x", "date": today().isoformat(), "all_day": True})
    assert r.status_code == 502
    assert "Could not save" in r.json()["detail"]


def test_weather_endpoint(client):
    body = client.get("/api/weather").json()
    assert body["report"]["unit"] == "F"
    assert body["report"]["current"]["icon"] == "rain"
    assert body["error"] is None


def test_status_unaffected_by_insert_failure(client, cal):
    cal.fail = True
    client.post("/api/events", json={"title": "x", "date": today().isoformat(), "all_day": True})
    status = client.get("/api/status").json()
    assert status["status"] == "ok"
    assert status["calendar_error"] is None  # insert failure doesn't clobber sync status
    assert status["last_synced"] is not None


def test_offline_boot_serves_disk_cache(tmp_path):
    config = CONFIG.model_copy(update={"cache_dir": tmp_path})
    online = FakeCalendar([all_day("a", today(), title="Cached")])
    with TestClient(create_app(config=config, demo=False, source=online, weather_fetch=sample_weather)):
        pass
    offline = FakeCalendar()
    offline.fail = True
    with TestClient(create_app(config=config, demo=False, source=offline, weather_fetch=sample_weather)) as c:
        body = c.get("/api/events").json()
        assert [e["title"] for e in body["days"][0]["events"]] == ["Cached"]
        assert body["error"] == "offline"
        assert c.get("/api/status").json()["calendar_error"] == "offline"


def test_demo_mode_end_to_end():
    with TestClient(create_app(config=CONFIG, demo=True)) as c:
        body = c.get("/api/events").json()
        assert sum(len(d["events"]) for d in body["days"]) > 0
        r = c.post("/api/events", json={"title": "Demo add", "date": today().isoformat(),
                                        "start_time": "12:00", "end_time": "13:00"})
        assert r.status_code == 201
        titles = [e["title"] for e in c.get("/api/events").json()["days"][0]["events"]]
        assert "Demo add" in titles
        assert c.get("/api/weather").json()["report"] is not None


def test_events_report_error_kind(client, tmp_path):
    assert client.get("/api/events").json()["error_kind"] is None

    config = CONFIG.model_copy(update={"cache_dir": tmp_path / "offline"})
    offline = FakeCalendar()
    offline.fail = True  # raises ConnectionError, like DNS not ready at boot
    with TestClient(create_app(config=config, demo=False, source=offline, weather_fetch=sample_weather)) as c:
        body = c.get("/api/events").json()
        assert body["error_kind"] == "network"
        assert body["last_synced"] is None
