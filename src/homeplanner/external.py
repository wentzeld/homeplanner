"""External (public) calendars shown next to the Family calendar: school, sports club, etc.

Each is an iCal link (.ics / webcal://) with a name and a Google color. The backend downloads
them periodically, keeps the last good copy on disk, and expands events (including repeats)
for whatever days the display asks for. External events are view-only.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
from pydantic import BaseModel, Field, TypeAdapter, field_validator

from homeplanner.colors import color_hex, is_color
from homeplanner.config import Config
from homeplanner.events import DisplayEvent, overlaps_day

log = logging.getLogger(__name__)

MAX_CALENDARS = 10
MAX_ICS_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 20
REFRESH_SECONDS = 30 * 60
RETRY_SECONDS = 5 * 60  # while any calendar is failing
UPCOMING_DAYS = 30  # for the "N upcoming events" count shown after adding

Fetch = Callable[[str], Awaitable[bytes]]


class CalendarError(Exception):
    """A problem to show to the user as-is (bad link, not a calendar, limit reached...)."""


class ExternalCalendar(BaseModel):
    id: str
    name: str
    url: str
    color_id: str


class NewExternalCalendar(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=2000)
    color_id: str

    @field_validator("name", "url")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v

    @field_validator("color_id")
    @classmethod
    def _valid_color(cls, v: str) -> str:
        if not is_color(v):
            raise ValueError("color_id must be a Google calendar color")
        return v


_calendars_adapter = TypeAdapter(list[ExternalCalendar])


def normalize_url(url: str) -> str:
    """webcal:// -> https://; only http(s) links with a host are allowed."""
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in ("webcal", "webcals"):
        scheme = "https"
    if scheme not in ("http", "https") or not parts.netloc:
        raise CalendarError("The link must start with https://, http:// or webcal://")
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def load_calendar(data: bytes) -> icalendar.Calendar:
    try:
        cal = icalendar.Calendar.from_ical(data)
    except ValueError as e:
        raise CalendarError("That link didn't return a calendar (.ics / iCal)") from e
    if cal.name != "VCALENDAR":
        raise CalendarError("That link didn't return a calendar (.ics / iCal)")
    return cal


def _to_local(value: date | datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        # Floating times (no timezone) are read as local time.
        return (value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)), False
    return datetime.combine(value, time(), tz), True


def calendar_events(
    cal: icalendar.Calendar, ext: ExternalCalendar, tz: ZoneInfo, start: date, end: date
) -> list[DisplayEvent]:
    """Expand events (incl. repeats) overlapping local days [start, end)."""
    # Pad by a day: the library's date bounds don't know our timezone. Exact filtering below.
    components = recurring_ical_events.of(cal).between(start - timedelta(days=1), end + timedelta(days=1))
    color = color_hex(ext.color_id)
    result = []
    for c in components:
        if str(c.get("STATUS", "")).upper() == "CANCELLED":
            continue
        ev_start, all_day = _to_local(c.decoded("DTSTART"), tz)
        if "DTEND" in c:
            ev_end, _ = _to_local(c.decoded("DTEND"), tz)
        elif "DURATION" in c:
            ev_end = ev_start + c.decoded("DURATION")
        else:
            ev_end = ev_start
        if all_day and ev_end <= ev_start:
            ev_end = ev_start + timedelta(days=1)
        event = DisplayEvent(
            id=f"ext-{ext.id}-{c.get('UID', '')}-{ev_start.isoformat()}",
            title=str(c.get("SUMMARY", "")).strip() or "(No title)",
            start=ev_start,
            end=max(ev_end, ev_start),
            all_day=all_day,
            location=str(c.get("LOCATION", "")).strip() or None,
            description=str(c.get("DESCRIPTION", "")).strip() or None,
            calendar=ext.name,
            color=color,
        )
        # Zero-length timed events still belong to the day they start on.
        days = (end - start).days
        probe = event if event.end > event.start else event.model_copy(
            update={"end": event.start + timedelta(seconds=1)})
        if any(overlaps_day(probe, start + timedelta(days=i), tz) for i in range(days)):
            result.append(event)
    return result


def http_fetcher(client: httpx.AsyncClient) -> Fetch:
    async def fetch(url: str) -> bytes:
        try:
            async with client.stream("GET", url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise CalendarError(f"The link returned an error (HTTP {resp.status_code})")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_ICS_BYTES:
                        raise CalendarError("That calendar is too large (over 5 MB)")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as e:
            raise CalendarError(f"Couldn't download the link: {e}") from e

    return fetch


class ExternalCalendars:
    def __init__(
        self,
        config: Config,
        fetch: Fetch,
        list_file: Path | None = None,
        cache_dir: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._config = config
        self._fetch = fetch
        self._list_file = list_file  # None: keep in memory only (demo mode)
        self._cache_dir = cache_dir
        self._now = now or (lambda: datetime.now(config.tz))
        self.calendars: list[ExternalCalendar] = []
        self.status: dict[str, dict] = {}  # id -> {"last_updated": datetime|None, "error": str|None}
        self._parsed: dict[str, icalendar.Calendar] = {}

    # --- persistence ---------------------------------------------------------------

    def load(self) -> None:
        if self._list_file is not None and self._list_file.exists():
            try:
                self.calendars = _calendars_adapter.validate_json(self._list_file.read_bytes())
            except (OSError, ValueError) as e:
                log.warning("ignoring unreadable calendar list %s: %s", self._list_file, e)
        for cal in self.calendars:
            self.status.setdefault(cal.id, {"last_updated": None, "error": None})
            path = self._ics_path(cal.id)
            if path is not None and path.exists():
                try:
                    self._parsed[cal.id] = load_calendar(path.read_bytes())
                except (OSError, CalendarError) as e:
                    log.warning("ignoring cached calendar %s: %s", path, e)

    def _save_list(self) -> None:
        if self._list_file is None:
            return
        self._list_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._list_file.with_suffix(".tmp")
        tmp.write_bytes(_calendars_adapter.dump_json(self.calendars, indent=2))
        tmp.replace(self._list_file)

    def _ics_path(self, cal_id: str) -> Path | None:
        return None if self._cache_dir is None else self._cache_dir / f"{cal_id}.ics"

    def _save_ics(self, cal_id: str, data: bytes) -> None:
        path = self._ics_path(cal_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError as e:
            log.warning("could not cache calendar %s: %s", path, e)

    # --- management ----------------------------------------------------------------

    async def add(self, new: NewExternalCalendar) -> tuple[ExternalCalendar, int]:
        """Validate the link by downloading it, then save. Returns the calendar and how many
        events it has in the next UPCOMING_DAYS days. Raises CalendarError for user errors."""
        if len(self.calendars) >= MAX_CALENDARS:
            raise CalendarError(f"You can add up to {MAX_CALENDARS} calendars. Remove one first.")
        url = normalize_url(new.url)
        if any(c.url == url for c in self.calendars):
            raise CalendarError("That calendar has already been added")
        data = await self._fetch(url)
        parsed = load_calendar(data)
        cal = ExternalCalendar(id=uuid.uuid4().hex[:12], name=new.name, url=url, color_id=new.color_id)
        self._parsed[cal.id] = parsed
        self._save_ics(cal.id, data)
        self.status[cal.id] = {"last_updated": self._now(), "error": None}
        self.calendars.append(cal)
        self._save_list()
        today = self._now().date()
        upcoming = calendar_events(parsed, cal, self._config.tz, today, today + timedelta(days=UPCOMING_DAYS))
        return cal, len(upcoming)

    def remove(self, cal_id: str) -> bool:
        before = len(self.calendars)
        self.calendars = [c for c in self.calendars if c.id != cal_id]
        if len(self.calendars) == before:
            return False
        self._parsed.pop(cal_id, None)
        self.status.pop(cal_id, None)
        path = self._ics_path(cal_id)
        if path is not None:
            path.unlink(missing_ok=True)
        self._save_list()
        return True

    # --- syncing -------------------------------------------------------------------

    async def refresh_all(self) -> None:
        for cal in list(self.calendars):
            try:
                data = await self._fetch(cal.url)
                parsed = load_calendar(data)
            except Exception as e:  # keep the last good copy
                log.warning("external calendar %r refresh failed: %s", cal.name, e)
                if cal.id in self.status:
                    self.status[cal.id]["error"] = str(e) or type(e).__name__
                continue
            if cal.id not in self.status:  # removed while downloading
                continue
            self._parsed[cal.id] = parsed
            self._save_ics(cal.id, data)
            self.status[cal.id] = {"last_updated": self._now(), "error": None}

    def next_delay(self) -> float:
        failing = any(s.get("error") for s in self.status.values())
        return RETRY_SECONDS if failing else REFRESH_SECONDS

    async def run(self) -> None:
        while True:
            await self.refresh_all()
            await asyncio.sleep(self.next_delay())

    # --- reading -------------------------------------------------------------------

    def events_between(self, start: date, days: int) -> list[DisplayEvent]:
        end = start + timedelta(days=days)
        events = []
        for cal in self.calendars:
            parsed = self._parsed.get(cal.id)
            if parsed is None:
                continue
            try:
                events.extend(calendar_events(parsed, cal, self._config.tz, start, end))
            except Exception as e:  # a malformed feed must not break the whole display
                log.warning("could not read events from %r: %s", cal.name, e)
        return events

    def public_list(self) -> list[dict]:
        return [
            {
                **cal.model_dump(),
                "color": color_hex(cal.color_id),
                "last_updated": self.status.get(cal.id, {}).get("last_updated"),
                "error": self.status.get(cal.id, {}).get("error"),
            }
            for cal in self.calendars
        ]
