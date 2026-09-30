from fastapi.testclient import TestClient

from homeplanner.config import load_config
from homeplanner.main import create_app
from tests.test_config import EXAMPLE


def client(demo: bool = False) -> TestClient:
    return TestClient(create_app(config=load_config(EXAMPLE), demo=demo))


def test_status():
    with client(demo=True) as c:
        body = c.get("/api/status").json()
        assert body["status"] == "ok"
        assert body["demo"] is True
        assert set(body) == {
            "status", "demo", "last_synced", "calendar_error", "weather_updated", "weather_error",
        }


def test_serves_kiosk_page():
    with client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "<title>HomePlanner</title>" in r.text
