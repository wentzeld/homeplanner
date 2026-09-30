"""Fake calendar and weather data for running the UI without Google credentials
(HOMEPLANNER_DEMO=1). Events are generated relative to today and adding works in memory."""

import itertools
from datetime import date, datetime, time, timedelta

from homeplanner.config import Config


class DemoCalendar:
    def __init__(self, config: Config, today: date | None = None):
        self._config = config
        self._ids = itertools.count(1)
        self._events = self._sample_events(today or datetime.now(config.tz).date())

    def _timed(self, title: str, day: date, start: time, end: time, member_idx: int | None, **extra) -> dict:
        ev = {
            "id": f"demo-{next(self._ids)}",
            "summary": title,
            "start": {"dateTime": datetime.combine(day, start, self._config.tz).isoformat()},
            "end": {"dateTime": datetime.combine(day, end, self._config.tz).isoformat()},
            **extra,
        }
        return self._with_member(ev, member_idx)

    def _all_day(self, title: str, day: date, days: int, member_idx: int | None) -> dict:
        ev = {
            "id": f"demo-{next(self._ids)}",
            "summary": title,
            "start": {"date": day.isoformat()},
            "end": {"date": (day + timedelta(days=days)).isoformat()},
        }
        return self._with_member(ev, member_idx)

    def _with_member(self, ev: dict, member_idx: int | None) -> dict:
        members = self._config.members
        if member_idx is not None:
            ev["colorId"] = members[member_idx % len(members)].color_id
        return ev

    def _sample_events(self, today: date) -> list[dict]:
        d = lambda n: today + timedelta(days=n)  # noqa: E731
        return [
            self._timed("School drop-off", d(0), time(8, 0), time(8, 30), 0),
            self._timed("Dentist", d(0), time(15, 30), time(16, 15), 1, location="Main St Dental"),
            self._timed("Family dinner", d(0), time(18, 30), time(20, 0), None),
            self._timed("Soccer practice", d(1), time(17, 0), time(18, 30), 1, location="Riverside Park"),
            self._all_day("Grandma visiting", d(2), 3, None),
            self._timed("Piano lesson", d(3), time(16, 0), time(17, 0), 0),
            self._timed("Date night", d(4), time(19, 0), time(22, 0), None),
            self._all_day("Camping trip", d(8), 2, 1),
            self._timed("Parent-teacher conference", d(9), time(18, 0), time(19, 0), 0),
        ]

    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        def bounds(ev: dict) -> tuple[datetime, datetime]:
            s, e = ev["start"], ev["end"]
            if "date" in s:
                tz = self._config.tz
                return (datetime.combine(date.fromisoformat(s["date"]), time(), tz),
                        datetime.combine(date.fromisoformat(e["date"]), time(), tz))
            return datetime.fromisoformat(s["dateTime"]), datetime.fromisoformat(e["dateTime"])

        return [ev for ev in self._events if bounds(ev)[0] < end and bounds(ev)[1] > start]

    def _find(self, event_id: str) -> int:
        from homeplanner.google_calendar import EventNotFound

        for i, ev in enumerate(self._events):
            if ev["id"] == event_id:
                return i
        raise EventNotFound(event_id)

    async def get_event(self, event_id: str) -> dict:
        return dict(self._events[self._find(event_id)])

    async def update_event(self, event_id: str, body: dict) -> dict:
        ev = self._with_zone({**body, "id": event_id})
        self._events[self._find(event_id)] = ev
        return ev

    async def delete_event(self, event_id: str) -> None:
        del self._events[self._find(event_id)]

    async def patch_event(self, event_id: str, body: dict) -> dict:
        i = self._find(event_id)
        ev = {**self._events[i], **body}
        self._events[i] = {k: v for k, v in ev.items() if v is not None}
        return self._events[i]

    def _with_zone(self, ev: dict) -> dict:
        # The form sends local times plus a timeZone; store them as offset times like Google returns.
        for key in ("start", "end"):
            if "dateTime" in ev[key] and datetime.fromisoformat(ev[key]["dateTime"]).tzinfo is None:
                naive = datetime.fromisoformat(ev[key]["dateTime"])
                ev[key] = {"dateTime": naive.replace(tzinfo=self._config.tz).isoformat()}
        return ev

    async def insert_event(self, payload: dict) -> dict:
        # Recurrence isn't expanded in demo mode; only the first occurrence is shown.
        ev = self._with_zone({"id": f"demo-{next(self._ids)}", **payload})
        self._events.append(ev)
        return ev


def demo_weather_fetcher(config: Config, today: date | None = None):
    today = today or datetime.now(config.tz).date()
    codes = [0, 2, 61, 3, 80, 1, 71, 95, 45, 2, 0, 63, 3, 1, 2, 0]
    f = config.weather.temperature_unit == "fahrenheit"
    to_unit = (lambda c: round(c * 9 / 5 + 32)) if f else (lambda c: c)

    async def fetch() -> dict:
        return {
            "current": {"temperature_2m": to_unit(14), "weather_code": 2},
            "daily": {
                "time": [(today + timedelta(days=i)).isoformat() for i in range(16)],
                "weather_code": codes,
                "temperature_2m_max": [to_unit(16 + i % 4) for i in range(16)],
                "temperature_2m_min": [to_unit(7 + i % 3) for i in range(16)],
                "precipitation_probability_max": [(i * 13) % 100 for i in range(16)],
            },
        }

    return fetch
