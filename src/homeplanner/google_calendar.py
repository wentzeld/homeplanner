"""Google Calendar client using a service account.

The Family calendar must be shared with the service account's email with
"Make changes to events" (see docs/google-setup.md).
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Protocol

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class EventNotFound(Exception):
    """The event doesn't exist (any more), e.g. it was deleted on a phone in the meantime."""


class NotAllowed(Exception):
    """Google refused the change (HTTP 403), typically because the Family calendar is only a
    guest of the event and only its organizer may change it."""


class CalendarSource(Protocol):
    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        """Expanded (single) events overlapping [start, end), as Google API event dicts."""

    async def insert_event(self, payload: dict) -> dict:
        """Create an event; returns the created Google API event dict."""

    async def get_event(self, event_id: str) -> dict:
        """A single event, occurrence or series. Raises EventNotFound."""

    async def update_event(self, event_id: str, body: dict) -> dict:
        """Replace an event (full resource). Raises EventNotFound."""

    async def delete_event(self, event_id: str) -> None:
        """Delete an event, a single occurrence, or a whole series. Raises EventNotFound."""

    async def patch_event(self, event_id: str, body: dict) -> dict:
        """Change only the given fields (None clears one). Raises EventNotFound / NotAllowed."""


class GoogleCalendar:
    def __init__(self, calendar_id: str, service_account_file: Path):
        self._calendar_id = calendar_id
        self._service_account_file = service_account_file
        self._service = None
        # googleapiclient's HTTP object is not thread-safe; serialize calls.
        self._lock = asyncio.Lock()

    def _get_service(self):
        # Built lazily so a missing/invalid key surfaces as a sync error on the
        # display (via /api/status) instead of crashing the app at startup.
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                str(self._service_account_file), scopes=SCOPES
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _list(self, start: datetime, end: datetime) -> list[dict]:
        events, page_token = [], None
        while True:
            resp = (
                self._get_service()
                .events()
                .list(
                    calendarId=self._calendar_id,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return events

    def _list_raw(self, time_min: datetime) -> list[dict]:
        events, page_token = [], None
        while True:
            resp = self._call(
                self._get_service().events().list(
                    calendarId=self._calendar_id,
                    timeMin=time_min.isoformat(),
                    singleEvents=False,  # repeating series once, plus individually changed occurrences
                    maxResults=2500,
                    pageToken=page_token,
                )
            )
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return events

    def _insert(self, payload: dict) -> dict:
        return self._get_service().events().insert(calendarId=self._calendar_id, body=payload).execute()

    def _call(self, request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (404, 410):
                raise EventNotFound(str(e)) from e
            if e.resp.status == 403:
                raise NotAllowed(str(e)) from e
            raise

    def _get(self, event_id: str) -> dict:
        return self._call(self._get_service().events().get(calendarId=self._calendar_id, eventId=event_id))

    def _update(self, event_id: str, body: dict) -> dict:
        return self._call(
            self._get_service().events().update(calendarId=self._calendar_id, eventId=event_id, body=body)
        )

    def _patch(self, event_id: str, body: dict) -> dict:
        return self._call(
            self._get_service().events().patch(calendarId=self._calendar_id, eventId=event_id, body=body)
        )

    def _delete(self, event_id: str) -> None:
        self._call(self._get_service().events().delete(calendarId=self._calendar_id, eventId=event_id))

    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        async with self._lock:
            return await asyncio.to_thread(self._list, start, end)

    async def insert_event(self, payload: dict) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._insert, payload)

    async def list_raw(self, time_min: datetime) -> list[dict]:
        """Events ending after time_min, without expanding repeats (used by recolor)."""
        async with self._lock:
            return await asyncio.to_thread(self._list_raw, time_min)

    async def get_event(self, event_id: str) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._get, event_id)

    async def update_event(self, event_id: str, body: dict) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._update, event_id, body)

    async def patch_event(self, event_id: str, body: dict) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._patch, event_id, body)

    async def delete_event(self, event_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete, event_id)
