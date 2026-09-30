"""Pure mapping between Google events and the display/form models.

- Google event -> DisplayEvent (all-day vs timed, multi-day, timezone, member by colorId).
- NewEventForm -> Google insert payload (colorId, RRULE recurrence, exclusive all-day end).
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from homeplanner.config import Config

from homeplanner.colors import GOOGLE_COLOR_NAMES, GOOGLE_EVENT_COLORS, color_hex  # noqa: F401 (re-exported)

FAMILY_COLOR = "#8d99ae"  # events with no member color

Recurrence = Literal["none", "daily", "weekly", "monthly", "yearly"]


class DisplayEvent(BaseModel):
    """An event as shown on the display. start/end are timezone-aware; for all-day
    events they are local midnights and end is exclusive (like Google)."""

    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None = None
    description: str | None = None
    member: str | None = None
    calendar: str | None = None  # external calendar name (school, soccer...); None = Family
    color: str = FAMILY_COLOR
    editable: bool = False  # Family events can be edited/deleted; external ones can't
    recurring: bool = False
    series_id: str | None = None  # Google recurringEventId for occurrences of a repeating event
    owned: bool = True  # False when the Family calendar is only a guest (organizer is someone else)


class NewEventForm(BaseModel):
    """The add-event form submitted from the display."""

    title: str = Field(min_length=1, max_length=200)
    date: date
    all_day: bool = False
    start_time: time | None = None
    end_time: time | None = None
    member: str | None = None
    location: str | None = None
    description: str | None = None
    recurrence: Recurrence = "none"
    recurrence_until: date | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title is required")
        return v

    @field_validator("member", "location", "description")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _check_times(self) -> "NewEventForm":
        if not self.all_day:
            if self.start_time is None or self.end_time is None:
                raise ValueError("start_time and end_time are required unless all_day")
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be after start_time")
        if self.recurrence_until is not None:
            if self.recurrence == "none":
                raise ValueError("recurrence_until requires a recurrence")
            if self.recurrence_until < self.date:
                raise ValueError("recurrence_until must be on or after date")
        return self


def _member_for_color(config: Config, color_id: str | None) -> str | None:
    for m in config.members:
        if m.color_id == color_id:
            return m.name
    return None


def is_owned(event: dict, config: Config) -> bool:
    """True when the Family calendar organizes the event (so it may change it). When the Family
    calendar was only invited, only the organizer can change details; we may still set our color."""
    organizer = event.get("organizer")
    if not organizer:
        return True
    return bool(organizer.get("self")) or organizer.get("email") == config.calendar_id


def _display_color(config: Config, color_id: str | None) -> str:
    """Wall color: the member's display_color if set, else the Google event color."""
    for m in config.members:
        if m.color_id == color_id and m.display_color:
            return color_hex(m.display_color)
    return GOOGLE_EVENT_COLORS.get(color_id, FAMILY_COLOR)


def _parse_google_time(value: dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    if "date" in value:
        return datetime.combine(date.fromisoformat(value["date"]), time(), tz), True
    return datetime.fromisoformat(value["dateTime"]).astimezone(tz), False


def google_to_display(event: dict, config: Config) -> DisplayEvent | None:
    """Map a Google Calendar API event to a DisplayEvent. Returns None for cancelled events."""
    if event.get("status") == "cancelled":
        return None
    tz = config.tz
    start, all_day = _parse_google_time(event["start"], tz)
    end, _ = _parse_google_time(event["end"], tz)
    color_id = event.get("colorId")
    return DisplayEvent(
        id=event["id"],
        title=event.get("summary") or "(No title)",
        start=start,
        end=end,
        all_day=all_day,
        location=event.get("location"),
        description=event.get("description"),
        member=_member_for_color(config, color_id),
        color=_display_color(config, color_id),
        editable=True,
        recurring=bool(event.get("recurringEventId") or event.get("recurrence")),
        series_id=event.get("recurringEventId"),
        owned=is_owned(event, config),
    )


def _rrule(form: NewEventForm, tz: ZoneInfo) -> str:
    rule = f"RRULE:FREQ={form.recurrence.upper()}"
    if form.recurrence_until is not None:
        if form.all_day:
            rule += f";UNTIL={form.recurrence_until:%Y%m%d}"
        else:
            # RFC 5545: with a zoned DTSTART, UNTIL must be UTC. Use end of the local day.
            local_end = datetime.combine(form.recurrence_until, time(23, 59, 59), tz)
            rule += f";UNTIL={local_end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
    return rule


def form_to_google(form: NewEventForm, config: Config) -> dict:
    """Build a Google Calendar API insert payload from the add-event form.

    Raises ValueError if form.member is not a configured family member.
    """
    payload: dict = {"summary": form.title}
    if form.all_day:
        payload["start"] = {"date": form.date.isoformat()}
        payload["end"] = {"date": (form.date + timedelta(days=1)).isoformat()}
    else:
        for key, t in (("start", form.start_time), ("end", form.end_time)):
            payload[key] = {
                "dateTime": datetime.combine(form.date, t).isoformat(),
                "timeZone": config.timezone,
            }
    if form.member is not None:
        member = next((m for m in config.members if m.name == form.member), None)
        if member is None:
            raise ValueError(f"unknown member {form.member!r}")
        payload["colorId"] = member.color_id
    if form.location:
        payload["location"] = form.location
    if form.description:
        payload["description"] = form.description
    if form.recurrence != "none":
        payload["recurrence"] = [_rrule(form, config.tz)]
    return payload


def overlaps_day(event: DisplayEvent, day: date, tz: ZoneInfo) -> bool:
    day_start = datetime.combine(day, time(), tz)
    day_end = datetime.combine(day + timedelta(days=1), time(), tz)
    return event.start < day_end and event.end > day_start


def group_by_day(events: list[DisplayEvent], start: date, days: int, tz: ZoneInfo) -> list[dict]:
    """Group events into consecutive days; multi-day events appear on every day they cover.
    Within a day, all-day events come first, then by start time."""
    result = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_events = sorted(
            (e for e in events if overlaps_day(e, day, tz)),
            key=lambda e: (not e.all_day, e.start, e.title),
        )
        result.append({"date": day, "events": day_events})
    return result


# --- Editing existing events ----------------------------------------------------------

_SIMPLE_FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
# Fields the form owns; everything else on the Google resource is kept as-is on update.
_FORM_FIELDS = ("summary", "start", "end", "colorId", "location", "description")


def _parse_until(value: str, tz: ZoneInfo) -> date:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(tz).date()
    return datetime.strptime(value[:8], "%Y%m%d").date()


def parse_simple_rrule(recurrence: list[str], start: date, tz: ZoneInfo) -> tuple[str, date | None] | None:
    """(freq, until) for repeat rules the form can express, ("none", None) for no repeat,
    or None for a custom rule (every 2 weeks, 2nd Tuesday, COUNT...) that must be kept as-is.
    Rules Google's own app writes for plain repeats (e.g. weekly with BYDAY=<start day>) count as simple."""
    rrules = [r for r in recurrence if r.upper().startswith("RRULE:")]
    if not rrules:
        return ("none", None)
    if len(rrules) > 1:
        return None
    parts = dict(p.split("=", 1) for p in rrules[0][6:].upper().split(";") if "=" in p)
    freq = parts.pop("FREQ", None)
    if freq not in _SIMPLE_FREQS:
        return None
    parts.pop("WKST", None)
    if parts.pop("INTERVAL", "1") != "1":
        return None
    if freq == "WEEKLY" and parts.get("BYDAY") == _WEEKDAYS[start.weekday()]:
        parts.pop("BYDAY")
    if freq == "MONTHLY" and parts.get("BYMONTHDAY") == str(start.day):
        parts.pop("BYMONTHDAY")
    until = parts.pop("UNTIL", None)
    if parts:  # COUNT, BYDAY lists, BYSETPOS...
        return None
    return (freq.lower(), _parse_until(until, tz) if until else None)


def _local_start(event: dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    return _parse_google_time(event["start"], tz)


def occurrence_date(event: dict, tz: ZoneInfo) -> date:
    """Local date where the series put this event (originalStartTime), even if it was moved since."""
    return _parse_google_time(event.get("originalStartTime") or event["start"], tz)[0].date()


def all_day_span(event: dict) -> int:
    """Number of days an all-day event covers (1 for a normal all-day event)."""
    if "date" not in event.get("start", {}):
        return 1
    days = (date.fromisoformat(event["end"]["date"]) - date.fromisoformat(event["start"]["date"])).days
    return max(days, 1)


def event_to_form(event: dict, series: dict | None, config: Config) -> dict:
    """Pre-filled edit-form data for a Google event (an occurrence's repeat comes from its series)."""
    tz = config.tz
    start, all_day = _local_start(event, tz)
    end, _ = _parse_google_time(event["end"], tz)
    rule_source = series if series is not None else event
    rule = parse_simple_rrule(rule_source.get("recurrence", []), _local_start(rule_source, tz)[0].date(), tz)
    multi_day_timed = not all_day and end.date() > start.date() and end != datetime.combine(
        start.date() + timedelta(days=1), time(), tz)
    return {
        "id": event["id"],
        "title": event.get("summary") or "",
        "date": start.date(),
        "all_day": all_day,
        "start_time": None if all_day else start.time().replace(second=0, microsecond=0),
        "end_time": None if all_day else end.time().replace(second=0, microsecond=0),
        "member": _member_for_color(config, event.get("colorId")),
        "location": event.get("location"),
        "description": event.get("description"),
        "recurrence": rule[0] if rule else "none",
        "recurrence_until": rule[1] if rule else None,
        "recurring": bool(event.get("recurringEventId") or event.get("recurrence")),
        "custom_repeat": rule is None,
        "span_days": all_day_span(event),
        "owned": is_owned(event, config),
        # The form has one date; events crossing midnight can only be deleted here.
        "form_editable": not multi_day_timed,
    }


def apply_form(
    resource: dict,
    form: NewEventForm,
    config: Config,
    *,
    keep_recurrence: bool,
    span_days: int = 1,
) -> dict:
    """The Google resource updated with the form. Cleared fields (member, location, notes) are
    removed. keep_recurrence leaves the existing repeat rule untouched (occurrences, custom rules);
    otherwise the rule is replaced, keeping any EXDATE/RDATE lines (deleted/extra occurrences)."""
    new = form_to_google(form, config)
    if form.all_day and span_days > 1:
        new["end"] = {"date": (form.date + timedelta(days=span_days)).isoformat()}
    body = {k: v for k, v in resource.items() if k not in _FORM_FIELDS}
    if keep_recurrence:
        new.pop("recurrence", None)
    else:
        extra = [r for r in resource.get("recurrence", []) if not r.upper().startswith("RRULE:")]
        body.pop("recurrence", None)
        if "recurrence" in new:
            new["recurrence"] = new["recurrence"] + extra
    body.update(new)
    return body


def guest_changes(current: dict, form: NewEventForm) -> list[str]:
    """Fields (other than who / repeat) that the form changes compared with event_to_form data.
    For guest events these can't be saved; only the organizer may change them."""
    def minutes(t):
        return None if t is None else (t.hour, t.minute)

    changed = []
    if form.title.strip() != (current["title"] or "").strip():
        changed.append("title")
    if form.date != current["date"] or form.all_day != current["all_day"]:
        changed.append("date")
    if not form.all_day and (minutes(form.start_time) != minutes(current["start_time"])
                             or minutes(form.end_time) != minutes(current["end_time"])):
        changed.append("time")
    if (form.location or None) != (current["location"] or None):
        changed.append("location")
    if (form.description or None) != (current["description"] or None):
        changed.append("notes")
    return changed
