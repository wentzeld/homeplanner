from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from homeplanner.config import load_config
from homeplanner.events import (
    FAMILY_COLOR,
    GOOGLE_EVENT_COLORS,
    DisplayEvent,
    NewEventForm,
    form_to_google,
    google_to_display,
    group_by_day,
)
from tests.test_config import EXAMPLE

CONFIG = load_config(EXAMPLE)  # America/Los_Angeles; Alex=9, Sam=11
TZ = CONFIG.tz
DAY = date(2026, 10, 5)


# --- Google -> display -------------------------------------------------------

def test_timed_event_converted_to_local_timezone():
    ev = google_to_display({
        "id": "a", "summary": "Soccer", "colorId": "11",
        "start": {"dateTime": "2026-10-05T17:00:00Z"},
        "end": {"dateTime": "2026-10-05T18:30:00Z"},
        "location": "Park",
    }, CONFIG)
    assert ev.start == datetime(2026, 10, 5, 10, 0, tzinfo=TZ)
    assert ev.start.utcoffset() == timedelta(hours=-7)
    assert ev.end == datetime(2026, 10, 5, 11, 30, tzinfo=TZ)
    assert not ev.all_day
    assert ev.member == "Sam"
    assert ev.color == GOOGLE_EVENT_COLORS["11"]
    assert ev.location == "Park"


def test_all_day_event():
    ev = google_to_display({
        "id": "b", "summary": "Trip",
        "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-08"},
    }, CONFIG)
    assert ev.all_day
    assert ev.start == datetime(2026, 10, 5, tzinfo=TZ)
    assert ev.end == datetime(2026, 10, 8, tzinfo=TZ)


def test_no_color_is_family_event():
    ev = google_to_display({"id": "c", "summary": "Dinner",
                            "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}}, CONFIG)
    assert ev.member is None
    assert ev.color == FAMILY_COLOR


def test_color_without_member_keeps_google_color():
    ev = google_to_display({"id": "d", "summary": "x", "colorId": "3",
                            "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}}, CONFIG)
    assert ev.member is None
    assert ev.color == GOOGLE_EVENT_COLORS["3"]


def test_missing_title_and_cancelled():
    base = {"id": "e", "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}}
    assert google_to_display(base, CONFIG).title == "(No title)"
    assert google_to_display({**base, "status": "cancelled"}, CONFIG) is None


# --- Form validation ---------------------------------------------------------

def form(**kw) -> NewEventForm:
    data = {"title": "Soccer", "date": DAY, "start_time": "17:00", "end_time": "18:30"}
    data.update(kw)
    return NewEventForm.model_validate(data)


@pytest.mark.parametrize("kw", [
    {"title": "   "},
    {"start_time": None},
    {"end_time": None},
    {"end_time": "17:00"},
    {"end_time": "16:00"},
    {"recurrence_until": "2026-12-01"},  # without recurrence
    {"recurrence": "weekly", "recurrence_until": "2026-10-01"},  # before date
    {"recurrence": "hourly"},
])
def test_invalid_forms(kw):
    with pytest.raises(ValidationError):
        form(**kw)


def test_all_day_needs_no_times():
    f = form(all_day=True, start_time=None, end_time=None)
    assert f.all_day


def test_blank_optional_fields_become_none():
    f = form(title="  Soccer ", member="", location="  ", description="")
    assert f.title == "Soccer"
    assert f.member is None and f.location is None and f.description is None


# --- Form -> Google payload --------------------------------------------------

def test_timed_payload():
    p = form_to_google(form(member="Alex", location="Park", description="Bring water"), CONFIG)
    assert p == {
        "summary": "Soccer",
        "start": {"dateTime": "2026-10-05T17:00:00", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-10-05T18:30:00", "timeZone": "America/Los_Angeles"},
        "colorId": "9",
        "location": "Park",
        "description": "Bring water",
    }


def test_all_day_payload_has_exclusive_end():
    p = form_to_google(form(all_day=True, start_time=None, end_time=None), CONFIG)
    assert p["start"] == {"date": "2026-10-05"}
    assert p["end"] == {"date": "2026-10-06"}
    assert "colorId" not in p and "recurrence" not in p


def test_unknown_member_rejected():
    with pytest.raises(ValueError, match="unknown member"):
        form_to_google(form(member="Nobody"), CONFIG)


@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly", "yearly"])
def test_recurrence_without_end(freq):
    p = form_to_google(form(recurrence=freq), CONFIG)
    assert p["recurrence"] == [f"RRULE:FREQ={freq.upper()}"]


def test_timed_recurrence_until_is_utc_end_of_local_day():
    p = form_to_google(form(recurrence="weekly", recurrence_until="2026-12-14"), CONFIG)
    # 2026-12-14 23:59:59 PST (UTC-8) == 2026-12-15 07:59:59 UTC
    assert p["recurrence"] == ["RRULE:FREQ=WEEKLY;UNTIL=20261215T075959Z"]


def test_all_day_recurrence_until_is_date():
    p = form_to_google(form(all_day=True, start_time=None, end_time=None,
                            recurrence="yearly", recurrence_until="2030-10-05"), CONFIG)
    assert p["recurrence"] == ["RRULE:FREQ=YEARLY;UNTIL=20301005"]


# --- Grouping ----------------------------------------------------------------

def ev(id, start, end, all_day=False, title=None) -> DisplayEvent:
    return DisplayEvent(id=id, title=title or id, start=start, end=end, all_day=all_day)


def at(d: date, h: int = 0, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), TZ)


def test_group_by_day_orders_all_day_first_then_start():
    d1 = DAY + timedelta(days=1)
    events = [
        ev("late", at(DAY, 18), at(DAY, 19)),
        ev("early", at(DAY, 8), at(DAY, 9)),
        ev("trip", at(DAY), at(DAY + timedelta(days=2)), all_day=True),
    ]
    days = group_by_day(events, DAY, 3, TZ)
    assert [d["date"] for d in days] == [DAY, d1, DAY + timedelta(days=2)]
    assert [e.id for e in days[0]["events"]] == ["trip", "early", "late"]
    assert [e.id for e in days[1]["events"]] == ["trip"]  # multi-day spans into day 2
    assert days[2]["events"] == []  # exclusive end


def test_event_ending_at_midnight_not_on_next_day():
    events = [ev("party", at(DAY, 20), at(DAY + timedelta(days=1)))]
    days = group_by_day(events, DAY, 2, TZ)
    assert len(days[0]["events"]) == 1 and days[1]["events"] == []


def test_overnight_event_on_both_days():
    events = [ev("flight", at(DAY, 23), at(DAY + timedelta(days=1), 2))]
    days = group_by_day(events, DAY, 2, TZ)
    assert len(days[0]["events"]) == 1 and len(days[1]["events"]) == 1


def test_utc_event_grouped_by_local_day():
    # 2026-10-06 02:00 UTC is 2026-10-05 19:00 in Los Angeles.
    utc = datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)
    days = group_by_day([ev("x", utc, utc + timedelta(hours=1))], DAY, 2, TZ)
    assert len(days[0]["events"]) == 1 and days[1]["events"] == []
