from datetime import date, datetime, time, timedelta

import httpx
import pytest

from homeplanner.config import load_config
from homeplanner.events import GOOGLE_EVENT_COLORS
from homeplanner.external import (
    MAX_CALENDARS,
    MAX_ICS_BYTES,
    RETRY_SECONDS,
    REFRESH_SECONDS,
    CalendarError,
    ExternalCalendar,
    ExternalCalendars,
    NewExternalCalendar,
    calendar_events,
    http_fetcher,
    load_calendar,
    normalize_url,
)
from tests.test_config import EXAMPLE

CONFIG = load_config(EXAMPLE)  # America/Los_Angeles
TZ = CONFIG.tz
TODAY = date(2026, 10, 5)
NOW = datetime.combine(TODAY, time(9), TZ)

SCHOOL_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test School//EN
BEGIN:VEVENT
UID:soccer@club
SUMMARY:Soccer practice
LOCATION:Riverside Park
DTSTART;TZID=America/New_York:20261006T200000
DTEND;TZID=America/New_York:20261006T213000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=America/New_York:20261013T200000
END:VEVENT
BEGIN:VEVENT
UID:soccer@club
RECURRENCE-ID;TZID=America/New_York:20261020T200000
SUMMARY:Soccer practice
STATUS:CANCELLED
DTSTART;TZID=America/New_York:20261020T200000
DTEND;TZID=America/New_York:20261020T213000
END:VEVENT
BEGIN:VEVENT
UID:break@school
SUMMARY:Fall break
DTSTART;VALUE=DATE:20261008
DTEND;VALUE=DATE:20261010
END:VEVENT
BEGIN:VEVENT
UID:picture@school
SUMMARY:Picture day
DTSTART:20261007T090000
END:VEVENT
BEGIN:VEVENT
UID:utc@school
SUMMARY:Late meeting
DTSTART:20261008T020000Z
DTEND:20261008T030000Z
END:VEVENT
END:VCALENDAR
"""

EXT = ExternalCalendar(id="abc", name="Lincoln Elementary", url="https://example.com/s.ics", color_id="10")


def events_in(start: date, days: int):
    cal = load_calendar(SCHOOL_ICS)
    return calendar_events(cal, EXT, TZ, start, start + timedelta(days=days))


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), TZ)


# --- parsing ---------------------------------------------------------------------

def test_timezone_converted_and_repeats_expanded_with_exdate_and_cancellation():
    soccer = sorted((e for e in events_in(TODAY, 31) if e.title == "Soccer practice"), key=lambda e: e.start)
    # Weekly x4 from Oct 6: Oct 6, (Oct 13 excluded), (Oct 20 cancelled), Oct 27.
    assert [e.start for e in soccer] == [at(date(2026, 10, 6), 17), at(date(2026, 10, 27), 17)]
    assert soccer[0].end == at(date(2026, 10, 6), 18, 30)  # 20:00 New York == 17:00 LA
    assert soccer[0].location == "Riverside Park"


def test_all_day_multi_day_event():
    (brk,) = [e for e in events_in(TODAY, 10) if e.title == "Fall break"]
    assert brk.all_day
    assert brk.start == at(date(2026, 10, 8), 0) and brk.end == at(date(2026, 10, 10), 0)


def test_floating_time_without_end_is_local_zero_length():
    (pic,) = [e for e in events_in(TODAY, 10) if e.title == "Picture day"]
    assert pic.start == at(date(2026, 10, 7), 9)
    assert pic.end == pic.start and not pic.all_day


def test_utc_event_lands_on_local_day():
    # 02:00 UTC Oct 8 == 19:00 Oct 7 in Los Angeles
    (ev,) = [e for e in events_in(date(2026, 10, 7), 1) if e.title == "Late meeting"]
    assert ev.start == at(date(2026, 10, 7), 19)
    assert not [e for e in events_in(date(2026, 10, 8), 1) if e.title == "Late meeting"]


def test_zero_length_event_included_on_its_day_only():
    assert [e.title for e in events_in(date(2026, 10, 7), 1) if e.title == "Picture day"] == ["Picture day"]
    assert not [e for e in events_in(date(2026, 10, 8), 1) if e.title == "Picture day"]


def test_events_carry_calendar_name_and_color():
    ev = events_in(TODAY, 10)[0]
    assert ev.calendar == "Lincoln Elementary"
    assert ev.member is None
    assert ev.color == GOOGLE_EVENT_COLORS["10"]
    assert ev.id.startswith("ext-abc-")


def test_window_filters_events():
    assert events_in(date(2026, 12, 1), 5) == []


def test_not_a_calendar():
    for bad in (b"<html>nope</html>", b"", b'{"json": true}'):
        with pytest.raises(CalendarError, match="didn't return a calendar"):
            load_calendar(bad)


# --- links -----------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("webcal://example.com/cal.ics", "https://example.com/cal.ics"),
    ("  https://example.com/c.ics?x=1  ", "https://example.com/c.ics?x=1"),
    ("http://example.com/c.ics", "http://example.com/c.ics"),
    ("WEBCAL://Example.com/c", "https://Example.com/c"),
])
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


@pytest.mark.parametrize("url", ["ftp://example.com/c.ics", "file:///etc/passwd", "example.com/c.ics", "https://"])
def test_bad_links_rejected(url):
    with pytest.raises(CalendarError):
        normalize_url(url)


async def test_fetcher_size_limit_and_http_errors(no_network):
    no_network.get("https://example.com/big.ics").mock(
        return_value=httpx.Response(200, content=b"x" * (MAX_ICS_BYTES + 1)))
    no_network.get("https://example.com/missing.ics").mock(return_value=httpx.Response(404))
    no_network.get("https://example.com/ok.ics").mock(return_value=httpx.Response(200, content=SCHOOL_ICS))
    no_network.get("https://down.example.com/x.ics").mock(side_effect=httpx.ConnectError("DNS failure"))
    async with httpx.AsyncClient() as client:
        fetch = http_fetcher(client)
        with pytest.raises(CalendarError, match="too large"):
            await fetch("https://example.com/big.ics")
        with pytest.raises(CalendarError, match="HTTP 404"):
            await fetch("https://example.com/missing.ics")
        with pytest.raises(CalendarError, match="Couldn't download"):
            await fetch("https://down.example.com/x.ics")
        assert await fetch("https://example.com/ok.ics") == SCHOOL_ICS


# --- service ---------------------------------------------------------------------

class FakeFeeds:
    def __init__(self):
        self.feeds: dict[str, bytes] = {}
        self.fail = False
        self.calls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail:
            raise CalendarError("Couldn't download the link: offline")
        if url not in self.feeds:
            raise CalendarError("The link returned an error (HTTP 404)")
        return self.feeds[url]


def service(tmp_path, feeds: FakeFeeds) -> ExternalCalendars:
    return ExternalCalendars(CONFIG, feeds, list_file=tmp_path / "data/calendars.json",
                             cache_dir=tmp_path / "cache/external", now=lambda: NOW)


def new(name="Lincoln Elementary", url="webcal://example.com/school.ics", color_id="10"):
    return NewExternalCalendar(name=name, url=url, color_id=color_id)


async def test_add_validates_saves_and_counts(tmp_path):
    feeds = FakeFeeds()
    feeds.feeds["https://example.com/school.ics"] = SCHOOL_ICS
    svc = service(tmp_path, feeds)
    cal, upcoming = await svc.add(new())
    assert cal.url == "https://example.com/school.ics"  # webcal normalised
    assert upcoming == 5  # 2 soccer + fall break + picture day + late meeting
    assert (tmp_path / "data/calendars.json").exists()
    assert (tmp_path / f"cache/external/{cal.id}.ics").read_bytes() == SCHOOL_ICS
    assert svc.public_list()[0]["color"] == GOOGLE_EVENT_COLORS["10"]


async def test_add_rejects_bad_feed_and_duplicates(tmp_path):
    feeds = FakeFeeds()
    feeds.feeds["https://example.com/page.html"] = b"<html></html>"
    feeds.feeds["https://example.com/school.ics"] = SCHOOL_ICS
    svc = service(tmp_path, feeds)
    with pytest.raises(CalendarError, match="didn't return a calendar"):
        await svc.add(new(url="https://example.com/page.html"))
    with pytest.raises(CalendarError, match="HTTP 404"):
        await svc.add(new(url="https://example.com/nope.ics"))
    await svc.add(new())
    with pytest.raises(CalendarError, match="already been added"):
        await svc.add(new(name="Again"))
    assert len(svc.calendars) == 1


async def test_limit(tmp_path):
    feeds = FakeFeeds()
    svc = service(tmp_path, feeds)
    for i in range(MAX_CALENDARS):
        feeds.feeds[f"https://example.com/{i}.ics"] = SCHOOL_ICS
        await svc.add(new(name=f"Cal {i}", url=f"https://example.com/{i}.ics"))
    feeds.feeds["https://example.com/extra.ics"] = SCHOOL_ICS
    with pytest.raises(CalendarError, match="up to 10"):
        await svc.add(new(url="https://example.com/extra.ics"))


def test_new_calendar_validation():
    with pytest.raises(ValueError):
        NewExternalCalendar(name="  ", url="https://x/y.ics", color_id="1")
    with pytest.raises(ValueError):
        NewExternalCalendar(name="School", url="https://x/y.ics", color_id="12")


async def test_remove_deletes_cache_and_persists(tmp_path):
    feeds = FakeFeeds()
    feeds.feeds["https://example.com/school.ics"] = SCHOOL_ICS
    svc = service(tmp_path, feeds)
    cal, _ = await svc.add(new())
    assert svc.remove(cal.id)
    assert not svc.remove(cal.id)
    assert not (tmp_path / f"cache/external/{cal.id}.ics").exists()
    reloaded = service(tmp_path, feeds)
    reloaded.load()
    assert reloaded.calendars == []


async def test_offline_boot_uses_cached_copy(tmp_path):
    feeds = FakeFeeds()
    feeds.feeds["https://example.com/school.ics"] = SCHOOL_ICS
    await service(tmp_path, feeds).add(new())

    offline = FakeFeeds()
    offline.fail = True
    svc = service(tmp_path, offline)
    svc.load()
    await svc.refresh_all()
    titles = {e.title for e in svc.events_between(TODAY, 5)}
    assert {"Soccer practice", "Fall break", "Picture day"} <= titles
    assert "offline" in svc.public_list()[0]["error"]
    assert svc.next_delay() == RETRY_SECONDS


async def test_refresh_picks_up_changes_and_clears_error(tmp_path):
    feeds = FakeFeeds()
    url = "https://example.com/school.ics"
    feeds.feeds[url] = SCHOOL_ICS
    svc = service(tmp_path, feeds)
    await svc.add(new())
    feeds.fail = True
    await svc.refresh_all()
    assert svc.next_delay() == RETRY_SECONDS
    feeds.fail = False
    feeds.feeds[url] = SCHOOL_ICS.replace(b"Picture day", b"Photo day")
    await svc.refresh_all()
    assert "Photo day" in {e.title for e in svc.events_between(TODAY, 5)}
    assert svc.public_list()[0]["error"] is None
    assert svc.next_delay() == REFRESH_SECONDS


def test_unreadable_list_file_ignored(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/calendars.json").write_text("garbage")
    svc = service(tmp_path, FakeFeeds())
    svc.load()
    assert svc.calendars == []
