"""Events the Family calendar was only invited to (organizer is a person): only 'who' can change."""

from datetime import datetime

import httplib2
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from homeplanner.config import load_config
from homeplanner.events import NewEventForm, event_to_form, google_to_display, guest_changes, is_owned
from homeplanner.google_calendar import GoogleCalendar, NotAllowed
from homeplanner.sync import CalendarSync
from tests.fakes import FakeCalendar
from tests.test_config import EXAMPLE
from tests.test_edit_events import timed
from tests.test_weather import SAMPLE

CONFIG = load_config(EXAMPLE)  # Alex=9, Sam=11
PERSON = {"email": "parent@example.com", "displayName": "A parent"}
GUESTS = [{"email": CONFIG.calendar_id, "self": True, "responseStatus": "accepted"}]

GUEST_SERIES = timed("lessons_R20261001T010000", "2026-10-06", "17:00", "18:00", summary="Swim lessons",
                     organizer=PERSON, attendees=GUESTS, recurrence=["RRULE:FREQ=WEEKLY"])
GUEST_OCC = timed("lessons_R20261001T010000_20261013T000000Z", "2026-10-13", "17:00", "18:00",
                  summary="Swim lessons", organizer=PERSON, attendees=GUESTS,
                  recurringEventId="lessons_R20261001T010000",
                  originalStartTime={"dateTime": "2026-10-13T17:00:00-07:00"})
GUEST_SINGLE = timed("party", "2026-10-09", "15:00", "17:00", summary="Birthday party", organizer=PERSON,
                     attendees=GUESTS, location="Park", colorId="9")
OWNED = timed("dentist", "2026-10-07", "15:30", "16:15", summary="Dentist",
              organizer={"email": CONFIG.calendar_id, "self": True})


def form_like(event, **changes) -> NewEventForm:
    current = event_to_form(event, None, CONFIG)
    data = {k: current[k] for k in ("title", "date", "all_day", "start_time", "end_time", "location", "description")}
    data["member"] = current["member"]
    data.update(changes)
    return NewEventForm.model_validate(data)


def make_sync(events):
    cal = FakeCalendar([dict(e) for e in events])
    return CalendarSync(cal, CONFIG, now=lambda: datetime(2026, 10, 5, 9, tzinfo=CONFIG.tz)), cal


# --- detection ------------------------------------------------------------------------

def test_ownership_detection():
    assert not is_owned(GUEST_SINGLE, CONFIG)
    assert is_owned(OWNED, CONFIG)
    assert is_owned({**OWNED, "organizer": {"email": CONFIG.calendar_id}}, CONFIG)  # no "self" flag
    assert is_owned({"id": "x"}, CONFIG)  # no organizer info: treat as ours
    assert google_to_display(GUEST_OCC, CONFIG).owned is False
    assert event_to_form(GUEST_SINGLE, None, CONFIG)["owned"] is False


def test_guest_changes_only_flags_real_changes():
    current = event_to_form(GUEST_SINGLE, None, CONFIG)
    assert guest_changes(current, form_like(GUEST_SINGLE, member="Sam")) == []
    assert guest_changes(current, form_like(GUEST_SINGLE, member=None)) == []
    assert guest_changes(current, form_like(GUEST_SINGLE, title="Party!")) == ["title"]
    assert guest_changes(current, form_like(GUEST_SINGLE, start_time="15:30")) == ["time"]
    assert guest_changes(current, form_like(GUEST_SINGLE, location="Home", description="Bring gift")) == ["location", "notes"]


# --- saving -----------------------------------------------------------------------------

async def test_assign_person_to_guest_occurrence_patches_only_color():
    sync, cal = make_sync([GUEST_SERIES, GUEST_OCC])
    ev = await sync.update_event(GUEST_OCC["id"], form_like(GUEST_OCC, member="Sam"), "this")
    assert cal.patched == [(GUEST_OCC["id"], {"colorId": "11"})]
    assert not getattr(cal, "updated", [])
    assert ev.member == "Sam"


async def test_assign_person_to_whole_guest_series():
    sync, cal = make_sync([GUEST_SERIES, GUEST_OCC])
    await sync.update_event(GUEST_OCC["id"], form_like(GUEST_OCC, member="Alex"), "all")
    assert cal.patched == [("lessons_R20261001T010000", {"colorId": "9"})]


async def test_clearing_person_clears_color():
    sync, cal = make_sync([GUEST_SINGLE])
    ev = await sync.update_event("party", form_like(GUEST_SINGLE, member=None), "this")
    assert cal.patched == [("party", {"colorId": None})]
    assert ev.member is None and "colorId" not in cal.events[0]


async def test_guest_detail_changes_rejected_without_calling_google():
    sync, cal = make_sync([GUEST_SINGLE])
    with pytest.raises(NotAllowed, match="created by someone else"):
        await sync.update_event("party", form_like(GUEST_SINGLE, title="Party!", member="Sam"), "this")
    assert not getattr(cal, "patched", []) and not getattr(cal, "updated", [])


async def test_guest_delete_rejected():
    sync, cal = make_sync([GUEST_SINGLE])
    with pytest.raises(NotAllowed, match="Only they can delete"):
        await sync.delete_event("party", "this")
    assert not getattr(cal, "deleted", [])


async def test_owned_events_still_use_full_update():
    sync, cal = make_sync([OWNED])
    await sync.update_event("dentist", form_like(OWNED, title="Orthodontist", member="Sam"), "this")
    assert cal.updated[0][0] == "dentist" and not getattr(cal, "patched", [])


# --- Google 403 and API messages ------------------------------------------------------------------

def test_google_403_becomes_not_allowed():
    class Request:
        def execute(self):
            raise HttpError(httplib2.Response({"status": 403}), b'{"error": {"message": "Forbidden"}}')

    with pytest.raises(NotAllowed):
        GoogleCalendar("cal", "key.json")._call(Request())


async def weather():
    return SAMPLE


@pytest.fixture
def api(tmp_path):
    from homeplanner.main import create_app

    cal = FakeCalendar([dict(e) for e in (GUEST_SINGLE, OWNED)])
    config = CONFIG.model_copy(update={"cache_dir": tmp_path / "c", "data_dir": tmp_path / "d"})
    with TestClient(create_app(config=config, demo=False, source=cal, weather_fetch=weather)) as c:
        yield c, cal


def test_api_guest_messages(api):
    c, cal = api
    body = form_like(GUEST_SINGLE, member="Sam").model_dump(mode="json")
    assert c.put("/api/events/party", json=body).status_code == 200
    r = c.put("/api/events/party", json={**body, "title": "Party!"})
    assert r.status_code == 403 and "created by someone else" in r.json()["detail"]
    r = c.delete("/api/events/party")
    assert r.status_code == 403 and "Only they can delete" in r.json()["detail"]
    assert c.get("/api/events/party").json()["owned"] is False


def test_api_raw_google_403_is_friendly(api):
    c, cal = api

    async def refuse(event_id, body):
        raise NotAllowed("<HttpError 403 when requesting https://www.googleapis.com/... returned \"Forbidden\">")

    cal.update_event = refuse
    body = form_like(OWNED, title="Orthodontist").model_dump(mode="json")
    r = c.put("/api/events/dentist", json=body)
    assert r.status_code == 403
    assert r.json()["detail"].startswith("Google didn't allow this change") and "HttpError" not in r.json()["detail"]
