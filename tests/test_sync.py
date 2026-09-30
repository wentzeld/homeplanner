from datetime import date, datetime, time, timedelta

import httplib2
import pytest
from google.auth.exceptions import TransportError

from homeplanner.config import load_config
from homeplanner.sync import DAYS_AHEAD, DAYS_BACK, CalendarSync
from tests.fakes import FakeCalendar, all_day
from tests.test_config import EXAMPLE

CONFIG = load_config(EXAMPLE)
TODAY = date(2026, 10, 5)
NOW = datetime.combine(TODAY, time(9, 0), CONFIG.tz)


def make_sync(cal: FakeCalendar, cache_file=None) -> CalendarSync:
    return CalendarSync(cal, CONFIG, cache_file=cache_file, now=lambda: NOW)


async def test_refresh_fetches_window_and_maps_events():
    cal = FakeCalendar([all_day("a", TODAY), {**all_day("x", TODAY), "status": "cancelled"}])
    sync = make_sync(cal)
    assert await sync.refresh()
    start, end = cal.list_calls[0]
    assert start == datetime.combine(TODAY - timedelta(days=DAYS_BACK), time(), CONFIG.tz)
    assert end == datetime.combine(TODAY + timedelta(days=DAYS_AHEAD), time(), CONFIG.tz)
    assert [e.id for e in sync.events] == ["a"]
    assert sync.last_synced == NOW and sync.last_error is None


async def test_failed_refresh_keeps_previous_events():
    cal = FakeCalendar([all_day("a", TODAY)])
    sync = make_sync(cal)
    await sync.refresh()
    cal.fail = True
    assert not await sync.refresh()
    assert [e.id for e in sync.events] == ["a"]
    assert sync.last_error == "offline"
    assert sync.last_synced == NOW
    cal.fail = False
    await sync.refresh()
    assert sync.last_error is None


async def test_events_between_served_from_cache_inside_window():
    cal = FakeCalendar([all_day("a", TODAY), all_day("b", TODAY + timedelta(days=10))])
    sync = make_sync(cal)
    await sync.refresh()
    calls = len(cal.list_calls)
    events = await sync.events_between(TODAY, 5)
    assert [e.id for e in events] == ["a"]
    assert len(cal.list_calls) == calls  # no extra fetch


async def test_events_between_fetches_on_demand_outside_window():
    far = TODAY + timedelta(days=DAYS_AHEAD + 10)
    cal = FakeCalendar([all_day("far", far)])
    sync = make_sync(cal)
    await sync.refresh()
    events = await sync.events_between(far, 5)
    assert [e.id for e in events] == ["far"]
    start, end = cal.list_calls[-1]
    assert start.date() == far and end.date() == far + timedelta(days=5)


async def test_events_between_falls_back_to_cache_when_offline():
    cal = FakeCalendar([all_day("a", TODAY)])
    sync = make_sync(cal)
    await sync.refresh()
    sync.window = None  # e.g. only loaded from disk cache
    cal.fail = True
    assert [e.id for e in await sync.events_between(TODAY, 5)] == ["a"]


async def test_cache_round_trip(tmp_path):
    cache = tmp_path / "sub" / "events.json"
    cal = FakeCalendar([all_day("a", TODAY, colorId="9")])
    await make_sync(cal, cache).refresh()
    assert cache.exists()

    # Offline boot: new sync loads the cache and can serve it.
    offline = FakeCalendar()
    offline.fail = True
    restored = make_sync(offline, cache)
    restored.load_cache()
    events = await restored.events_between(TODAY, 5)
    assert [(e.id, e.member) for e in events] == [("a", "Alex")]


def test_unreadable_cache_is_ignored(tmp_path):
    cache = tmp_path / "events.json"
    cache.write_text("not json")
    sync = make_sync(FakeCalendar(), cache)
    sync.load_cache()
    assert sync.events == []


async def test_add_event_inserts_and_resyncs():
    cal = FakeCalendar()
    sync = make_sync(cal)
    created = await sync.add_event(all_day("ignored", TODAY, title="Dinner", colorId="11"))
    assert created.title == "Dinner" and created.member == "Sam"
    assert [e.title for e in sync.events] == ["Dinner"]


async def test_retries_quickly_while_failing():
    from homeplanner.sync import SYNC_INTERVAL_SECONDS, SYNC_RETRY_SECONDS

    cal = FakeCalendar()
    sync = make_sync(cal)
    await sync.refresh()
    assert sync.next_delay() == SYNC_INTERVAL_SECONDS == 120
    cal.fail = True
    await sync.refresh()
    assert sync.next_delay() == SYNC_RETRY_SECONDS == 15
    cal.fail = False
    await sync.refresh()
    assert sync.next_delay() == SYNC_INTERVAL_SECONDS


def _raising(exc):
    class Failing(FakeCalendar):
        async def list_events(self, start, end):
            raise exc
    return Failing()


@pytest.mark.parametrize("exc, kind", [
    (ConnectionError("offline"), "network"),
    (TimeoutError("timed out"), "network"),
    (httplib2.ServerNotFoundError("Unable to find the server at oauth2.googleapis.com"), "network"),
    (TransportError("wrapped httplib2 error"), "network"),
    (RuntimeError("403 calendar not shared"), "other"),
    (ValueError("bad key file"), "other"),
])
async def test_error_kind(exc, kind):
    sync = make_sync(_raising(exc))
    await sync.refresh()
    assert sync.last_error_kind == kind
    assert sync.next_delay() == 15


async def test_error_kind_cleared_after_success():
    cal = FakeCalendar()
    cal.fail = True
    sync = make_sync(cal)
    await sync.refresh()
    assert sync.last_error_kind == "network"
    cal.fail = False
    await sync.refresh()
    assert sync.last_error_kind is None
