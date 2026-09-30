"""Background calendar sync.

Polls on an interval over [today-7d, today+35d], keeps an in-memory cache,
and persists it to JSON so the display has data after an offline boot.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httplib2
from google.auth.exceptions import TransportError
from pydantic import TypeAdapter

from homeplanner.config import Config
from homeplanner.events import (
    DisplayEvent,
    NewEventForm,
    all_day_span,
    apply_form,
    event_to_form,
    google_to_display,
    guest_changes,
    is_owned,
    occurrence_date,
    overlaps_day,
    parse_simple_rrule,
)
from homeplanner.google_calendar import CalendarSource, NotAllowed

log = logging.getLogger(__name__)

DAYS_BACK = 7
DAYS_AHEAD = 35
SYNC_INTERVAL_SECONDS = 120
GUEST_EVENT_MESSAGE = (
    "This event was created by someone else. Only they can change its title, time or place; "
    "you can change who it's for."
)
SYNC_RETRY_SECONDS = 15  # while failing, e.g. network not up yet after boot

# DNS/connection failures (typically: network not ready yet), as opposed to
# configuration problems such as a bad key or an unshared calendar.
_NETWORK_ERRORS = (OSError, httplib2.HttpLib2Error, TransportError)


def is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, _NETWORK_ERRORS)

_events_adapter = TypeAdapter(list[DisplayEvent])


class CalendarSync:
    def __init__(
        self,
        source: CalendarSource,
        config: Config,
        cache_file: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._source = source
        self._config = config
        self._cache_file = cache_file
        self._now = now or (lambda: datetime.now(config.tz))
        self.events: list[DisplayEvent] = []
        self.window: tuple[date, date] | None = None  # [first day, last day) covered by cache
        self.last_synced: datetime | None = None
        self.last_error: str | None = None
        self.last_error_kind: str | None = None  # "network" | "other" | None

    def today(self) -> date:
        return self._now().date()

    def _bounds(self, first: date, last: date) -> tuple[datetime, datetime]:
        tz = self._config.tz
        return datetime.combine(first, time(), tz), datetime.combine(last, time(), tz)

    async def _fetch(self, first: date, last: date) -> list[DisplayEvent]:
        raw = await self._source.list_events(*self._bounds(first, last))
        return [e for e in (google_to_display(r, self._config) for r in raw) if e is not None]

    async def refresh(self) -> bool:
        """Fetch the sync window. On failure, keep the previous cache and record the error."""
        today = self.today()
        first, last = today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_AHEAD)
        try:
            events = await self._fetch(first, last)
        except Exception as e:  # network, auth, API errors: keep showing cached data
            log.warning("calendar sync failed: %s", e)
            self.last_error = str(e) or type(e).__name__
            self.last_error_kind = "network" if is_network_error(e) else "other"
            return False
        self.events, self.window = events, (first, last)
        self.last_synced, self.last_error, self.last_error_kind = self._now(), None, None
        self._save_cache()
        return True

    async def add_event(self, payload: dict) -> DisplayEvent:
        """Create an event, then re-sync so it shows up immediately. Insert errors propagate."""
        created = await self._source.insert_event(payload)
        await self.refresh()
        return google_to_display(created, self._config)

    async def event_details(self, event_id: str) -> dict:
        """Edit-form data for an event. Raises EventNotFound."""
        event = await self._source.get_event(event_id)
        series = None
        if event.get("recurringEventId"):
            series = await self._source.get_event(event["recurringEventId"])
        return event_to_form(event, series, self._config)

    async def update_event(self, event_id: str, form: NewEventForm, scope: str) -> DisplayEvent:
        """scope "this": only this event/occurrence; "all": the whole series it belongs to.
        Re-syncs afterwards. Raises EventNotFound."""
        tz = self._config.tz
        event = await self._source.get_event(event_id)
        series_id = event.get("recurringEventId")
        series = await self._source.get_event(series_id) if series_id and scope == "all" else None
        target_id, target = (series_id, series) if series is not None else (event_id, event)
        if not is_owned(target, self._config):
            # The Family calendar is only a guest: Google lets it change just its own color.
            if guest_changes(event_to_form(event, None, self._config), form):
                raise NotAllowed(GUEST_EVENT_MESSAGE)
            updated = await self._source.patch_event(target_id, {"colorId": self._member_color(form.member)})
            await self.refresh()
            return google_to_display(updated, self._config)
        if series is not None:
            series_date = occurrence_date(series, tz)
            # Moving the clicked occurrence by N days moves the whole series by N days.
            shifted = form.model_copy(update={"date": series_date + (form.date - occurrence_date(event, tz))})
            custom = parse_simple_rrule(series.get("recurrence", []), series_date, tz) is None
            body = apply_form(series, shifted, self._config, keep_recurrence=custom,
                              span_days=all_day_span(series))
            updated = await self._source.update_event(series_id, body)
        else:
            if series_id:  # a single occurrence: its repeat belongs to the series
                keep = True
            else:
                start = occurrence_date(event, tz)
                keep = bool(event.get("recurrence")) and parse_simple_rrule(event["recurrence"], start, tz) is None
            body = apply_form(event, form, self._config, keep_recurrence=keep, span_days=all_day_span(event))
            updated = await self._source.update_event(event_id, body)
        await self.refresh()
        return google_to_display(updated, self._config)

    def _member_color(self, member: str | None) -> str | None:
        if member is None:
            return None
        for m in self._config.members:
            if m.name == member:
                return m.color_id
        raise ValueError(f"unknown member {member!r}")

    async def delete_event(self, event_id: str, scope: str) -> None:
        """scope "this": only this event/occurrence; "all": the whole series.
        Raises EventNotFound, or NotAllowed for events the Family calendar was only invited to."""
        event = await self._source.get_event(event_id)
        if not is_owned(event, self._config):
            raise NotAllowed("This event was created by someone else. Only they can delete it.")
        if scope == "all":
            event_id = event.get("recurringEventId") or event_id
        await self._source.delete_event(event_id)
        await self.refresh()

    async def events_between(self, start: date, days: int) -> list[DisplayEvent]:
        """Events overlapping [start, start+days). Served from cache when covered;
        otherwise fetched on demand, falling back to whatever the cache has."""
        end = start + timedelta(days=days)
        if self.window is None or start < self.window[0] or end > self.window[1]:
            try:
                return await self._fetch(start, end)
            except Exception as e:
                log.warning("on-demand fetch failed: %s", e)
        tz = self._config.tz
        return [
            e for e in self.events
            if any(overlaps_day(e, start + timedelta(days=i), tz) for i in range(days))
        ]

    def _save_cache(self) -> None:
        if self._cache_file is None:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_file.with_suffix(".tmp")
            tmp.write_bytes(_events_adapter.dump_json(self.events))
            tmp.replace(self._cache_file)
        except OSError as e:
            log.warning("could not write cache %s: %s", self._cache_file, e)

    def load_cache(self) -> None:
        """Load events saved by a previous run. The window stays unset, so the cache is
        used as a fallback but a fresh sync is still attempted."""
        if self._cache_file is None or not self._cache_file.exists():
            return
        try:
            self.events = _events_adapter.validate_json(self._cache_file.read_bytes())
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable cache %s: %s", self._cache_file, e)

    def next_delay(self) -> float:
        return SYNC_RETRY_SECONDS if self.last_error else SYNC_INTERVAL_SECONDS

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.next_delay())
