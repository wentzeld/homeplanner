from datetime import date, datetime


class FakeCalendar:
    """In-memory CalendarSource. Set fail=True to simulate Google being unreachable."""

    def __init__(self, events: list[dict] | None = None):
        self.events = list(events or [])
        self.fail = False
        self.list_calls: list[tuple[datetime, datetime]] = []
        self.inserted: list[dict] = []

    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        self.list_calls.append((start, end))
        if self.fail:
            raise ConnectionError("offline")
        return list(self.events)

    async def insert_event(self, payload: dict) -> dict:
        if self.fail:
            raise ConnectionError("offline")
        ev = {"id": f"new-{len(self.inserted) + 1}", **payload}
        self.inserted.append(payload)
        self.events.append(ev)
        return ev


    # --- editing (added for edit/delete; the methods above are unchanged) ---

    def _index(self, event_id: str) -> int:
        from homeplanner.google_calendar import EventNotFound

        for i, ev in enumerate(self.events):
            if ev["id"] == event_id:
                return i
        raise EventNotFound(event_id)

    async def get_event(self, event_id: str) -> dict:
        if self.fail:
            raise ConnectionError("offline")
        import copy
        return copy.deepcopy(self.events[self._index(event_id)])

    async def update_event(self, event_id: str, body: dict) -> dict:
        if self.fail:
            raise ConnectionError("offline")
        self.updated = getattr(self, "updated", []) + [(event_id, body)]
        self.events[self._index(event_id)] = {**body, "id": event_id}
        return self.events[self._index(event_id)]

    async def patch_event(self, event_id: str, body: dict) -> dict:
        if self.fail:
            raise ConnectionError("offline")
        self.patched = getattr(self, "patched", []) + [(event_id, body)]
        i = self._index(event_id)
        merged = {**self.events[i], **body}
        self.events[i] = {k: v for k, v in merged.items() if v is not None}
        return self.events[i]

    async def delete_event(self, event_id: str) -> None:
        if self.fail:
            raise ConnectionError("offline")
        self.deleted = getattr(self, "deleted", []) + [event_id]
        del self.events[self._index(event_id)]


def all_day(id: str, day: date, title: str = "Event", **extra) -> dict:
    from datetime import timedelta

    return {"id": id, "summary": title, "start": {"date": day.isoformat()},
            "end": {"date": (day + timedelta(days=1)).isoformat()}, **extra}
