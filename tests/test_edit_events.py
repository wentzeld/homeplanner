from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from homeplanner.config import load_config
from homeplanner.events import (
    NewEventForm,
    all_day_span,
    apply_form,
    event_to_form,
    google_to_display,
    parse_simple_rrule,
)
from homeplanner.google_calendar import EventNotFound
from homeplanner.sync import CalendarSync
from tests.fakes import FakeCalendar
from tests.test_config import EXAMPLE
from tests.test_weather import SAMPLE

CONFIG = load_config(EXAMPLE)  # Los Angeles; Alex=9, Sam=11
TZ = CONFIG.tz
LA = "America/Los_Angeles"


def timed(id, day: str, start: str, end: str, **extra) -> dict:
    return {"id": id, "summary": extra.pop("summary", id), "etag": '"1"', "iCalUID": f"{id}@google.com",
            "start": {"dateTime": f"{day}T{start}:00-07:00", "timeZone": LA},
            "end": {"dateTime": f"{day}T{end}:00-07:00", "timeZone": LA}, **extra}


# Weekly soccer on Tuesdays, as Google's own app writes it, plus two expanded occurrences.
SERIES = timed("soccer", "2026-10-06", "17:00", "18:30", summary="Soccer", colorId="11",
               location="Park", recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU", "EXDATE;TZID=America/Los_Angeles:20261020T170000"])
OCC1 = timed("soccer_20261013T000000Z", "2026-10-13", "17:00", "18:30", summary="Soccer", colorId="11",
             location="Park", recurringEventId="soccer",
             originalStartTime={"dateTime": "2026-10-13T17:00:00-07:00", "timeZone": LA})
OCC2 = timed("soccer_20261027T000000Z", "2026-10-27", "17:00", "18:30", summary="Soccer", colorId="11",
             location="Park", recurringEventId="soccer",
             originalStartTime={"dateTime": "2026-10-27T17:00:00-07:00", "timeZone": LA})
DENTIST = timed("dentist", "2026-10-07", "15:30", "16:15", summary="Dentist", colorId="9",
                location="Main St", description="Bring card")
TRIP = {"id": "trip", "summary": "Grandma visiting", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-11"}}


def form(**kw) -> NewEventForm:
    data = {"title": "Soccer", "date": "2026-10-13", "start_time": "17:00", "end_time": "18:30", "member": "Sam",
            "location": "Park"}
    data.update(kw)
    return NewEventForm.model_validate(data)


# --- repeat rules ----------------------------------------------------------------------

@pytest.mark.parametrize("rules, start, expected", [
    ([], date(2026, 10, 6), ("none", None)),
    (["RRULE:FREQ=WEEKLY"], date(2026, 10, 6), ("weekly", None)),
    (["RRULE:FREQ=WEEKLY;BYDAY=TU"], date(2026, 10, 6), ("weekly", None)),  # Oct 6 2026 is a Tuesday
    (["RRULE:FREQ=WEEKLY;WKST=SU;BYDAY=TU"], date(2026, 10, 6), ("weekly", None)),
    (["RRULE:FREQ=MONTHLY;BYMONTHDAY=6"], date(2026, 10, 6), ("monthly", None)),
    (["RRULE:FREQ=DAILY;UNTIL=20261215"], date(2026, 10, 6), ("daily", date(2026, 12, 15))),
    (["RRULE:FREQ=WEEKLY;UNTIL=20261215T075959Z"], date(2026, 10, 6), ("weekly", date(2026, 12, 14))),
    (["RRULE:FREQ=YEARLY", "EXDATE:20271006"], date(2026, 10, 6), ("yearly", None)),
])
def test_simple_rules(rules, start, expected):
    assert parse_simple_rrule(rules, start, TZ) == expected


@pytest.mark.parametrize("rules", [
    ["RRULE:FREQ=WEEKLY;INTERVAL=2"],
    ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH"],
    ["RRULE:FREQ=WEEKLY;BYDAY=WE"],  # not the start day
    ["RRULE:FREQ=MONTHLY;BYDAY=2TU"],
    ["RRULE:FREQ=DAILY;COUNT=5"],
    ["RRULE:FREQ=HOURLY"],
    ["RRULE:FREQ=DAILY", "RRULE:FREQ=WEEKLY"],
])
def test_custom_rules(rules):
    assert parse_simple_rrule(rules, date(2026, 10, 6), TZ) is None


# --- form pre-fill -----------------------------------------------------------------------

def test_event_to_form_single():
    f = event_to_form(DENTIST, None, CONFIG)
    assert (f["title"], f["date"], f["start_time"], f["end_time"]) == ("Dentist", date(2026, 10, 7), time(15, 30), time(16, 15))
    assert (f["member"], f["location"], f["description"]) == ("Alex", "Main St", "Bring card")
    assert f["recurrence"] == "none" and not f["recurring"] and not f["custom_repeat"] and f["form_editable"]


def test_event_to_form_occurrence_takes_repeat_from_series():
    f = event_to_form(OCC1, SERIES, CONFIG)
    assert f["recurring"] and f["recurrence"] == "weekly" and f["date"] == date(2026, 10, 13)
    assert f["member"] == "Sam"


def test_event_to_form_multi_day_all_day_and_overnight():
    assert event_to_form(TRIP, None, CONFIG)["span_days"] == 3
    overnight = timed("party", "2026-10-07", "20:00", "23:00")
    overnight["end"] = {"dateTime": "2026-10-08T02:00:00-07:00"}
    assert event_to_form(overnight, None, CONFIG)["form_editable"] is False
    to_midnight = timed("late", "2026-10-07", "20:00", "23:00")
    to_midnight["end"] = {"dateTime": "2026-10-08T00:00:00-07:00"}
    assert event_to_form(to_midnight, None, CONFIG)["form_editable"] is True


# --- applying the form -------------------------------------------------------------------

def test_apply_form_keeps_other_fields_and_clears_emptied_ones():
    body = apply_form(DENTIST, form(title="Dentist", date="2026-10-07", start_time="16:00", end_time="17:00",
                                    member=None, location="", description=None), CONFIG, keep_recurrence=False)
    assert body["summary"] == "Dentist"
    assert body["start"] == {"dateTime": "2026-10-07T16:00:00", "timeZone": LA}
    assert "colorId" not in body and "location" not in body and "description" not in body
    assert body["iCalUID"] == DENTIST["iCalUID"] and body["etag"] == DENTIST["etag"]


def test_apply_form_replaces_rule_but_keeps_exdates():
    body = apply_form(SERIES, form(date="2026-10-06", recurrence="daily"), CONFIG, keep_recurrence=False)
    assert body["recurrence"] == ["RRULE:FREQ=DAILY", SERIES["recurrence"][1]]


def test_apply_form_removing_repeat_makes_single_event():
    body = apply_form(SERIES, form(date="2026-10-06"), CONFIG, keep_recurrence=False)
    assert "recurrence" not in body


def test_apply_form_keep_recurrence():
    body = apply_form(SERIES, form(date="2026-10-06", recurrence="daily"), CONFIG, keep_recurrence=True)
    assert body["recurrence"] == SERIES["recurrence"]


def test_apply_form_keeps_multi_day_length():
    body = apply_form(TRIP, form(title="Grandma", date="2026-10-09", all_day=True, start_time=None, end_time=None,
                                 member=None, location=None), CONFIG, keep_recurrence=False, span_days=all_day_span(TRIP))
    assert body["start"] == {"date": "2026-10-09"} and body["end"] == {"date": "2026-10-12"}


# --- sync: scope logic ---------------------------------------------------------------------

def make_sync(events):
    cal = FakeCalendar([dict(e) for e in events])
    return CalendarSync(cal, CONFIG, now=lambda: datetime(2026, 10, 5, 9, tzinfo=TZ)), cal


async def test_edit_single_event():
    sync, cal = make_sync([DENTIST])
    ev = await sync.update_event("dentist", form(title="Orthodontist", date="2026-10-07", start_time="15:30",
                                                 end_time="16:15", member="Alex", location="Main St"), "this")
    assert ev.title == "Orthodontist" and ev.member == "Alex"
    assert [e.title for e in sync.events] == ["Orthodontist"]  # re-synced


async def test_edit_this_occurrence_only():
    sync, cal = make_sync([SERIES, OCC1, OCC2])
    await sync.update_event(OCC1["id"], form(title="Soccer (moved)", start_time="18:00", end_time="19:00"), "this")
    ((updated_id, body),) = cal.updated
    assert updated_id == OCC1["id"]
    assert body["recurringEventId"] == "soccer" and "recurrence" not in body
    assert body["start"]["dateTime"] == "2026-10-13T18:00:00"
    assert await cal.get_event("soccer") == SERIES  # series untouched


async def test_edit_all_events_updates_series_and_shifts_date():
    sync, cal = make_sync([SERIES, OCC1, OCC2])
    # Clicked the Oct 13 occurrence and moved it to Wednesday Oct 14 at 18:00 for all events.
    await sync.update_event(OCC1["id"], form(date="2026-10-14", start_time="18:00", end_time="19:30",
                                             recurrence="weekly"), "all")
    ((updated_id, body),) = cal.updated
    assert updated_id == "soccer"
    assert body["start"]["dateTime"] == "2026-10-07T18:00:00"  # series start Oct 6 + 1 day
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY", SERIES["recurrence"][1]]


async def test_edit_all_keeps_custom_rule():
    custom = {**SERIES, "recurrence": ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"]}
    sync, cal = make_sync([custom, OCC1])
    await sync.update_event(OCC1["id"], form(title="Soccer!", recurrence="daily"), "all")
    ((_, body),) = cal.updated
    assert body["summary"] == "Soccer!" and body["recurrence"] == custom["recurrence"]


async def test_delete_this_and_all():
    sync, cal = make_sync([SERIES, OCC1, OCC2])
    await sync.delete_event(OCC1["id"], "this")
    await sync.delete_event(OCC2["id"], "all")
    assert cal.deleted == [OCC1["id"], "soccer"]


async def test_missing_event_raises():
    sync, _ = make_sync([])
    with pytest.raises(EventNotFound):
        await sync.update_event("gone", form(), "this")
    with pytest.raises(EventNotFound):
        await sync.delete_event("gone", "all")


def test_family_events_editable_and_marked_recurring():
    ev = google_to_display(OCC1, CONFIG)
    assert ev.editable and ev.recurring and ev.series_id == "soccer"
    assert not google_to_display(DENTIST, CONFIG).recurring


# --- API -------------------------------------------------------------------------------------

async def sample_weather():
    return SAMPLE


@pytest.fixture
def api(tmp_path):
    from homeplanner.main import create_app

    cal = FakeCalendar([dict(e) for e in (SERIES, OCC1, OCC2, DENTIST)])
    config = CONFIG.model_copy(update={"cache_dir": tmp_path / "cache", "data_dir": tmp_path / "data"})
    with TestClient(create_app(config=config, demo=False, source=cal, weather_fetch=sample_weather)) as c:
        yield c, cal


def test_api_details(api):
    c, _ = api
    body = c.get(f"/api/events/{OCC1['id']}").json()
    assert body["recurring"] and body["recurrence"] == "weekly" and body["member"] == "Sam"
    assert c.get("/api/events/nope").status_code == 404


def test_api_edit_and_delete(api):
    c, cal = api
    payload = {"title": "Dentist", "date": "2026-10-07", "start_time": "10:00", "end_time": "11:00", "member": "Sam"}
    r = c.put("/api/events/dentist", json=payload)
    assert r.status_code == 200 and r.json()["member"] == "Sam"
    assert c.put(f"/api/events/{OCC1['id']}?scope=all", json={**payload, "date": "2026-10-13"}).status_code == 200
    assert cal.updated[-1][0] == "soccer"
    assert c.delete("/api/events/dentist").status_code == 204
    r = c.delete("/api/events/dentist")
    assert r.status_code == 404 and "no longer exists" in r.json()["detail"]
    assert c.put("/api/events/x?scope=some", json=payload).status_code == 422


def test_api_external_events_read_only(api):
    c, _ = api
    assert c.put("/api/events/ext-abc-uid-2026", json={"title": "x", "date": "2026-10-07", "all_day": True}).status_code == 403
    assert c.delete("/api/events/ext-abc-uid-2026").status_code == 403


def test_api_edit_unknown_member_and_google_down(api):
    c, cal = api
    payload = {"title": "Dentist", "date": "2026-10-07", "all_day": True, "member": "Nobody"}
    r = c.put("/api/events/dentist", json=payload)
    assert r.status_code == 422 and "unknown member" in r.json()["detail"]
    cal.fail = True
    r = c.delete("/api/events/dentist")
    assert r.status_code == 502 and "Could not delete" in r.json()["detail"]
