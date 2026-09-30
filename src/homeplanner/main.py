"""FastAPI app: serves the kiosk page and the JSON API.

Run: uvicorn homeplanner.main:app --host 127.0.0.1 --port 8000
Set HOMEPLANNER_DEMO=1 to use fake data instead of Google (see demo.py).
Set HOMEPLANNER_SCREEN_CONTROL=1 (done by the Pi's systemd unit) to let the app turn the
HDMI output off/on per the sleep schedule; otherwise screen changes are only logged.
Set HOMEPLANNER_REMOTE=1 (done by the Pi's systemd unit, together with --host 0.0.0.0) to allow
other devices on the home network in, behind a PIN (see auth.py).
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from homeplanner.auth import COOKIE_NAME, SESSION_SECONDS, AuthStore, LockedOut, is_display, make_guard
from homeplanner.colors import color_hex, palette
from homeplanner.config import Config, load_config
from homeplanner.demo import DemoCalendar, demo_weather_fetcher
from homeplanner.events import (
    FAMILY_COLOR,
    DisplayEvent,
    NewEventForm,
    form_to_google,
    group_by_day,
)
from homeplanner.external import CalendarError, ExternalCalendars, Fetch, NewExternalCalendar, http_fetcher
from homeplanner.google_calendar import CalendarSource, EventNotFound, GoogleCalendar, NotAllowed
from homeplanner.screen import NullScreen, ScreenScheduler, WlrRandrScreen, watch_input
from homeplanner.sync import CalendarSync
from homeplanner.weather import WeatherService, open_meteo_fetcher

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_DAYS = 31
EXTERNAL_PREFIX = "ext-"
GONE = "This event no longer exists. It may have been deleted on another device."
FORBIDDEN = ("Google didn't allow this change. The event was probably created by someone else, "
             "and only they can change it.")


def _not_allowed_detail(e: NotAllowed) -> str:
    # Our own checks carry a friendly message; raw Google 403s start with "<HttpError".
    text = str(e)
    return FORBIDDEN if not text or text.startswith("<HttpError") else text


class PinForm(BaseModel):
    pin: str


async def _watch_input_safely(on_input) -> None:
    try:
        await watch_input(on_input)
    except ImportError:
        log.warning("evdev not installed; key presses won't wake the screen (pip install .[pi])")
    except Exception:
        log.exception("input watcher stopped; key presses won't wake the screen")


def create_app(
    config: Config | None = None,
    demo: bool | None = None,
    source: CalendarSource | None = None,
    weather_fetch: Callable[[], Awaitable[dict]] | None = None,
    ics_fetch: Fetch | None = None,
    remote: bool | None = None,
) -> FastAPI:
    """source/weather_fetch/ics_fetch override the Google, Open-Meteo and iCal downloads
    (used by tests). remote=True lets other devices in behind a PIN (HOMEPLANNER_REMOTE=1)."""
    if demo is None:
        demo = os.environ.get("HOMEPLANNER_DEMO") == "1"
    if remote is None:
        remote = os.environ.get("HOMEPLANNER_REMOTE") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = config or load_config()
        app.state.config = cfg
        app.state.demo = demo

        async with httpx.AsyncClient() as client:
            if demo:
                cal = source or DemoCalendar(cfg)
                fetch = weather_fetch or demo_weather_fetcher(cfg)
                cache_file = None  # never mix fake events into the real cache
            else:
                cal = source or GoogleCalendar(cfg.calendar_id, cfg.service_account_file)
                fetch = weather_fetch or open_meteo_fetcher(cfg, client)
                cache_file = cfg.cache_dir / "events.json"

            app.state.sync = CalendarSync(cal, cfg, cache_file=cache_file)
            app.state.sync.load_cache()
            app.state.weather = WeatherService(fetch, cfg)

            # Demo mode keeps external calendars and the PIN in memory only.
            app.state.external = ExternalCalendars(
                cfg,
                ics_fetch or http_fetcher(client),
                list_file=None if demo else cfg.data_dir / "calendars.json",
                cache_dir=None if demo else cfg.cache_dir / "external",
            )
            app.state.external.load()
            app.state.auth = AuthStore(None if demo else cfg.data_dir / "auth.json")
            app.state.auth.load()

            screen_control = os.environ.get("HOMEPLANNER_SCREEN_CONTROL") == "1"
            controller = WlrRandrScreen(cfg.sleep.output) if screen_control else NullScreen()
            app.state.screen = ScreenScheduler(controller, cfg.sleep, now=lambda: datetime.now(cfg.tz))

            tasks = [
                asyncio.create_task(app.state.sync.run()),
                asyncio.create_task(app.state.weather.run()),
                asyncio.create_task(app.state.screen.run()),
                asyncio.create_task(app.state.external.run()),
            ]
            if screen_control:
                tasks.append(asyncio.create_task(_watch_input_safely(app.state.screen.wake)))
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await t

    app = FastAPI(title="HomePlanner", lifespan=lifespan)
    app.state.remote = remote
    if remote:
        app.middleware("http")(make_guard(lambda: app.state.auth))

    app.state.refresh_task = None

    async def refresh_everything() -> None:
        """Family calendar, other calendars and weather at once; concurrent callers share one run."""
        task = app.state.refresh_task
        if task is None or task.done():
            async def run():
                await asyncio.gather(
                    app.state.sync.refresh(), app.state.external.refresh_all(), app.state.weather.refresh()
                )
            task = app.state.refresh_task = asyncio.create_task(run())
        await asyncio.shield(task)

    def require_family_event(event_id: str) -> None:
        if event_id.startswith(EXTERNAL_PREFIX):
            raise HTTPException(status_code=403, detail="Events from other calendars are read-only")

    def require_display(request: Request) -> None:
        if not is_display(request, remote):
            raise HTTPException(status_code=403, detail="This can only be changed on the wall display")

    @app.get("/api/status")
    async def status() -> dict:
        sync: CalendarSync = app.state.sync
        weather: WeatherService = app.state.weather
        return {
            "status": "ok",
            "demo": app.state.demo,
            "last_synced": sync.last_synced,
            "calendar_error": sync.last_error,
            "weather_updated": weather.last_updated,
            "weather_error": weather.last_error,
        }

    @app.get("/api/config")
    async def get_config(request: Request) -> dict:
        cfg: Config = app.state.config
        return {
            "timezone": cfg.timezone,
            "today": app.state.sync.today(),
            "family_color": FAMILY_COLOR,
            "members": [
                {"name": m.name, "color": color_hex(m.display_color or m.color_id)} for m in cfg.members
            ],
            "calendars": [{"name": c["name"], "color": c["color"]} for c in app.state.external.public_list()],
            "colors": palette(),  # Google's 24 calendar colors, for other calendars
            "remote": {
                "mode": remote,
                "enabled": remote and app.state.auth.remote_enabled,
                "is_display": is_display(request, remote),
            },
        }

    @app.get("/api/events")
    async def get_events(
        start: date | None = None, days: int = Query(default=5, ge=1, le=MAX_DAYS)
    ) -> dict:
        sync: CalendarSync = app.state.sync
        start = start or sync.today()
        events = await sync.events_between(start, days) + app.state.external.events_between(start, days)
        return {
            "start": start,
            "days": group_by_day(events, start, days, app.state.config.tz),
            "last_synced": sync.last_synced,
            "error": sync.last_error,
            "error_kind": sync.last_error_kind,
        }

    @app.post("/api/events", status_code=201)
    async def add_event(form: NewEventForm) -> DisplayEvent:
        cfg: Config = app.state.config
        sync: CalendarSync = app.state.sync
        try:
            payload = form_to_google(form, cfg)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        try:
            return await sync.add_event(payload)
        except Exception as e:
            log.warning("add_event failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Could not save to Google Calendar: {e}") from e

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: str) -> dict:
        require_family_event(event_id)
        try:
            return await app.state.sync.event_details(event_id)
        except EventNotFound as e:
            raise HTTPException(status_code=404, detail=GONE) from e

    @app.put("/api/events/{event_id}")
    async def update_event(
        event_id: str, form: NewEventForm, scope: Literal["this", "all"] = "this"
    ) -> DisplayEvent:
        require_family_event(event_id)
        try:
            return await app.state.sync.update_event(event_id, form, scope)
        except EventNotFound as e:
            raise HTTPException(status_code=404, detail=GONE) from e
        except NotAllowed as e:
            raise HTTPException(status_code=403, detail=_not_allowed_detail(e)) from e
        except ValueError as e:  # unknown member
            raise HTTPException(status_code=422, detail=str(e)) from e
        except Exception as e:
            log.warning("update_event failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Could not save to Google Calendar: {e}") from e

    @app.delete("/api/events/{event_id}", status_code=204)
    async def delete_event(event_id: str, scope: Literal["this", "all"] = "this") -> Response:
        require_family_event(event_id)
        try:
            await app.state.sync.delete_event(event_id, scope)
        except EventNotFound as e:
            raise HTTPException(status_code=404, detail=GONE) from e
        except NotAllowed as e:
            raise HTTPException(status_code=403, detail=_not_allowed_detail(e)) from e
        except Exception as e:
            log.warning("delete_event failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Could not delete from Google Calendar: {e}") from e
        return Response(status_code=204)

    @app.post("/api/refresh")
    async def refresh() -> dict:
        await refresh_everything()
        sync: CalendarSync = app.state.sync
        return {"last_synced": sync.last_synced, "error": sync.last_error, "error_kind": sync.last_error_kind}

    @app.get("/api/weather")
    async def get_weather() -> dict:
        weather: WeatherService = app.state.weather
        return {
            "report": weather.report,
            "updated": weather.last_updated,
            "error": weather.last_error,
        }

    # --- External calendars ------------------------------------------------------

    @app.get("/api/calendars")
    async def list_calendars() -> list[dict]:
        return app.state.external.public_list()

    @app.post("/api/calendars", status_code=201)
    async def add_calendar(new: NewExternalCalendar) -> dict:
        try:
            cal, upcoming = await app.state.external.add(new)
        except CalendarError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"calendar": cal, "upcoming_events": upcoming}

    @app.delete("/api/calendars/{cal_id}", status_code=204)
    async def remove_calendar(cal_id: str) -> Response:
        if not app.state.external.remove(cal_id):
            raise HTTPException(status_code=404, detail="No such calendar")
        return Response(status_code=204)

    # --- Remote access (PIN) -------------------------------------------------------

    @app.post("/api/login")
    async def login(form: PinForm, response: Response) -> dict:
        auth: AuthStore = app.state.auth
        try:
            ok = auth.check_pin(form.pin)
        except LockedOut as e:
            minutes = max(1, round(e.seconds_left / 60))
            raise HTTPException(status_code=429, detail=f"Too many wrong PINs. Try again in {minutes} minutes.") from e
        if not ok:
            left = auth.attempts_left
            raise HTTPException(status_code=401, detail=f"Wrong PIN ({left} {'try' if left == 1 else 'tries'} left)")
        response.set_cookie(COOKIE_NAME, auth.issue_token(), max_age=SESSION_SECONDS,
                            httponly=True, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/auth/state")
    async def auth_state(request: Request) -> dict:
        return {
            "mode": remote,
            "enabled": remote and app.state.auth.remote_enabled,
            "is_display": is_display(request, remote),
        }

    @app.post("/api/auth/pin")
    async def set_pin(form: PinForm, request: Request) -> dict:
        require_display(request)
        try:
            app.state.auth.set_pin(form.pin)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"ok": True}

    @app.post("/api/auth/disable")
    async def disable_remote(request: Request) -> dict:
        require_display(request)
        app.state.auth.disable()
        return {"ok": True}

    @app.post("/api/auth/signout-all")
    async def sign_out_all(request: Request) -> dict:
        require_display(request)
        app.state.auth.sign_out_all()
        return {"ok": True}

    @app.get("/settings", include_in_schema=False)
    async def settings_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "settings.html")

    @app.get("/login", include_in_schema=False)
    async def login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html")

    # Mounted last so /api routes take precedence.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
