from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from homeplanner.auth import COOKIE_NAME, MAX_FAILURES
from homeplanner.config import load_config
from homeplanner.external import CalendarError
from tests.fakes import FakeCalendar
from tests.test_config import EXAMPLE
from tests.test_weather import SAMPLE

CONFIG = load_config(EXAMPLE)
DISPLAY = ("127.0.0.1", 50000)
PHONE = ("192.168.1.60", 50000)


def today():
    return datetime.now(CONFIG.tz).date()


def ics_for(day) -> bytes:
    d = day.strftime("%Y%m%d")
    nxt = (day + timedelta(days=1)).strftime("%Y%m%d")
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:game@club
SUMMARY:Soccer game
DTSTART;VALUE=DATE:{d}
DTEND;VALUE=DATE:{nxt}
END:VEVENT
END:VCALENDAR
""".encode()


class Feeds:
    def __init__(self):
        self.feeds = {"https://club.example.com/soccer.ics": ics_for(today())}

    async def __call__(self, url):
        if url not in self.feeds:
            raise CalendarError("The link returned an error (HTTP 404)")
        return self.feeds[url]


async def sample_weather():
    return SAMPLE


@pytest.fixture
def make_app(tmp_path):
    from homeplanner.main import create_app

    def make(remote=False):
        config = CONFIG.model_copy(update={"cache_dir": tmp_path / "cache", "data_dir": tmp_path / "data"})
        return create_app(config=config, demo=False, source=FakeCalendar(), weather_fetch=sample_weather,
                          ics_fetch=Feeds(), remote=remote)
    return make


def add_soccer(client):
    return client.post("/api/calendars", json={
        "name": "Soccer club", "url": "webcal://club.example.com/soccer.ics", "color_id": "6"})


# --- calendars -------------------------------------------------------------------

def test_add_list_show_and_remove(make_app):
    with TestClient(make_app()) as c:
        r = add_soccer(c)
        assert r.status_code == 201
        assert r.json()["upcoming_events"] == 1
        cal_id = r.json()["calendar"]["id"]

        (listed,) = c.get("/api/calendars").json()
        assert listed["name"] == "Soccer club" and listed["url"] == "https://club.example.com/soccer.ics"

        (ev,) = c.get("/api/events").json()["days"][0]["events"]
        assert ev["title"] == "Soccer game" and ev["calendar"] == "Soccer club" and ev["member"] is None
        assert ev["color"] == "#f4511e"  # Tangerine
        assert {"name": "Soccer club", "color": "#f4511e"} in c.get("/api/config").json()["calendars"]

        assert c.delete(f"/api/calendars/{cal_id}").status_code == 204
        assert c.delete(f"/api/calendars/{cal_id}").status_code == 404
        assert c.get("/api/events").json()["days"][0]["events"] == []


def test_add_errors_are_shown(make_app):
    with TestClient(make_app()) as c:
        r = c.post("/api/calendars", json={"name": "X", "url": "https://club.example.com/nope.ics", "color_id": "6"})
        assert r.status_code == 422 and "HTTP 404" in r.json()["detail"]
        r = c.post("/api/calendars", json={"name": "X", "url": "ftp://x/y", "color_id": "6"})
        assert r.status_code == 422 and "https://" in r.json()["detail"]
        r = c.post("/api/calendars", json={"name": "X", "url": "https://x/y", "color_id": "99"})
        assert r.status_code == 422


def test_calendars_persist_across_restart(make_app):
    with TestClient(make_app()) as c:
        add_soccer(c)
    with TestClient(make_app()) as c:
        assert [x["name"] for x in c.get("/api/calendars").json()] == ["Soccer club"]


def test_json_body_required(make_app):
    # A cross-site form can't send JSON; plain-text bodies must be rejected.
    with TestClient(make_app()) as c:
        r = c.post("/api/calendars", content='{"name":"X","url":"https://x/y","color_id":"6"}',
                   headers={"Content-Type": "text/plain"})
        assert r.status_code == 422


# --- remote access -----------------------------------------------------------------

def test_without_remote_mode_everything_is_the_display(make_app):
    with TestClient(make_app(remote=False), client=PHONE) as c:
        assert c.get("/api/events").status_code == 200
        assert c.get("/api/auth/state").json() == {"mode": False, "enabled": False, "is_display": True}


def test_display_never_needs_pin(make_app):
    with TestClient(make_app(remote=True), client=DISPLAY) as c:
        assert c.get("/api/events").status_code == 200
        assert c.get("/").status_code == 200
        assert c.get("/api/auth/state").json()["is_display"] is True


def test_other_devices_blocked_until_pin_set(make_app):
    app = make_app(remote=True)
    with TestClient(app, client=PHONE) as phone:
        r = phone.get("/", follow_redirects=False)
        assert r.status_code == 403 and "Remote access is off" in r.text
        assert phone.get("/api/events").status_code == 403
        assert phone.get("/styles.css").status_code == 200  # for the "off" page


def test_sign_in_flow(make_app):
    app = make_app(remote=True)
    with TestClient(app, client=DISPLAY) as display, TestClient(app, client=PHONE) as phone:
        assert display.post("/api/auth/pin", json={"pin": "246810"}).status_code == 200

        r = phone.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert phone.get("/api/events").status_code == 401
        assert phone.get("/login").status_code == 200

        r = phone.post("/api/login", json={"pin": "111111"})
        assert r.status_code == 401 and "tries left" in r.json()["detail"]

        r = phone.post("/api/login", json={"pin": "246810"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert f"{COOKIE_NAME}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie

        # Signed in: everything works, including adding events and calendars.
        assert phone.get("/").status_code == 200
        assert phone.get("/api/events").status_code == 200
        assert add_soccer(phone).status_code == 201
        assert phone.post("/api/events", json={"title": "Pizza", "date": str(today()), "all_day": True}).status_code == 201

        # ...but PIN management stays on the display.
        assert phone.post("/api/auth/pin", json={"pin": "999999"}).status_code == 403
        assert phone.post("/api/auth/disable").status_code == 403
        assert phone.post("/api/auth/signout-all").status_code == 403

        phone.post("/api/logout")
        assert phone.get("/api/events").status_code == 401


def test_lockout_via_api(make_app):
    app = make_app(remote=True)
    with TestClient(app, client=DISPLAY) as display, TestClient(app, client=PHONE) as phone:
        display.post("/api/auth/pin", json={"pin": "246810"})
        for _ in range(MAX_FAILURES - 1):
            assert phone.post("/api/login", json={"pin": "000000"}).status_code == 401
        r = phone.post("/api/login", json={"pin": "000000"})
        assert r.status_code == 429 and "Try again in 15 minutes" in r.json()["detail"]
        assert phone.post("/api/login", json={"pin": "246810"}).status_code == 429


def test_display_can_sign_everyone_out_and_disable(make_app):
    app = make_app(remote=True)
    with TestClient(app, client=DISPLAY) as display, TestClient(app, client=PHONE) as phone:
        display.post("/api/auth/pin", json={"pin": "246810"})
        phone.post("/api/login", json={"pin": "246810"})
        assert phone.get("/api/events").status_code == 200
        display.post("/api/auth/signout-all")
        assert phone.get("/api/events").status_code == 401
        phone.post("/api/login", json={"pin": "246810"})
        display.post("/api/auth/disable")
        assert phone.get("/api/events").status_code == 403


def test_bad_pin_format_rejected(make_app):
    with TestClient(make_app(remote=True), client=DISPLAY) as display:
        r = display.post("/api/auth/pin", json={"pin": "1234"})
        assert r.status_code == 422 and "6 to 8 digits" in r.json()["detail"]
