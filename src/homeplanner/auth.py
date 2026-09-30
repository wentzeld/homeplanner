"""PIN-protected access from other devices on the home network.

- The wall display (connections from the Pi itself) never needs a PIN.
- Other devices are blocked until a PIN is set on the display; then they sign in once and
  stay signed in for 30 days (signed, HttpOnly, SameSite=Strict cookie).
- 5 wrong PINs lock sign-in for 15 minutes (for everyone, so it can't be dodged by switching devices).
- Only active when the app runs with HOMEPLANNER_REMOTE=1 (set by the Pi's systemd unit).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

COOKIE_NAME = "hp_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
PIN_PATTERN = re.compile(r"^\d{6,8}$")
LOOPBACK = {"127.0.0.1", "::1"}
# Reachable without signing in (the PIN page itself).
OPEN_PATHS = {"/login", "/login.js", "/styles.css", "/api/login"}

REMOTE_OFF_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>HomePlanner</title>
<link rel="stylesheet" href="/styles.css"></head><body class="page"><main class="card narrow">
<h1>Remote access is off</h1>
<p>To use HomePlanner from this device, set a PIN on the wall display:
press <b>S</b> (Settings) and choose <b>Set PIN</b>.</p></main></body></html>"""


class LockedOut(Exception):
    def __init__(self, seconds_left: int):
        super().__init__(f"locked for {seconds_left}s")
        self.seconds_left = seconds_left


def _hash_pin(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


class AuthStore:
    def __init__(self, path: Path | None, now: Callable[[], float] = time.time):
        self._path = path  # None: in memory only (demo mode)
        self._now = now
        self._state: dict = {}
        self._failures = 0
        self._locked_until = 0.0

    def load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            self._state = json.loads(self._path.read_text())
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable auth file %s: %s", self._path, e)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._state, f)
        tmp.replace(self._path)

    # --- PIN ---------------------------------------------------------------------

    @property
    def remote_enabled(self) -> bool:
        return bool(self._state.get("pin_hash"))

    def set_pin(self, pin: str) -> None:
        """Set or change the PIN. Signs out every device."""
        if not PIN_PATTERN.match(pin):
            raise ValueError("The PIN must be 6 to 8 digits")
        salt = os.urandom(16)
        self._state["pin_salt"] = salt.hex()
        self._state["pin_hash"] = _hash_pin(pin, salt).hex()
        self._state["secret"] = secrets.token_hex(32)
        self._failures, self._locked_until = 0, 0.0
        self._save()

    def disable(self) -> None:
        """Turn off remote access: forget the PIN and sign out every device."""
        self._state = {"secret": secrets.token_hex(32)}
        self._save()

    def sign_out_all(self) -> None:
        self._state["secret"] = secrets.token_hex(32)
        self._save()

    def check_pin(self, pin: str) -> bool:
        """True if correct. Raises LockedOut after too many wrong attempts."""
        now = self._now()
        if now < self._locked_until:
            raise LockedOut(int(self._locked_until - now) + 1)
        if not self.remote_enabled:
            return False
        expected = bytes.fromhex(self._state["pin_hash"])
        actual = _hash_pin(pin, bytes.fromhex(self._state["pin_salt"]))
        if hmac.compare_digest(expected, actual):
            self._failures = 0
            return True
        self._failures += 1
        if self._failures >= MAX_FAILURES:
            self._failures = 0
            self._locked_until = now + LOCKOUT_SECONDS
            raise LockedOut(LOCKOUT_SECONDS)
        return False

    @property
    def attempts_left(self) -> int:
        return MAX_FAILURES - self._failures

    # --- sessions ------------------------------------------------------------------

    def _sign(self, payload: str) -> str:
        return hmac.new(bytes.fromhex(self._state["secret"]), payload.encode(), hashlib.sha256).hexdigest()

    def issue_token(self) -> str:
        expires = int(self._now()) + SESSION_SECONDS
        return f"{expires}.{self._sign(str(expires))}"

    def verify_token(self, token: str | None) -> bool:
        if not token or not self.remote_enabled or "secret" not in self._state:
            return False
        expires, _, signature = token.partition(".")
        if not expires.isdigit() or int(expires) < self._now():
            return False
        return hmac.compare_digest(signature, self._sign(expires))


def is_display(request: Request, remote_mode: bool) -> bool:
    """The wall display itself. Without remote mode the app only listens on localhost,
    so every client is the display."""
    if not remote_mode:
        return True
    return request.client is not None and request.client.host in LOOPBACK


def make_guard(get_store: Callable[[], AuthStore]):
    """HTTP middleware for remote mode: display passes; other devices need a PIN session."""

    async def guard(request: Request, call_next):
        if is_display(request, remote_mode=True):
            return await call_next(request)
        store = get_store()
        path = request.url.path
        wants_api = path.startswith("/api/")
        if not store.remote_enabled:
            if path == "/styles.css":  # used by the "remote access is off" page
                return await call_next(request)
            if wants_api:
                return JSONResponse({"detail": "Remote access is off. Set a PIN on the display."}, status_code=403)
            return HTMLResponse(REMOTE_OFF_PAGE, status_code=403)
        if path in OPEN_PATHS or store.verify_token(request.cookies.get(COOKIE_NAME)):
            return await call_next(request)
        if wants_api:
            return JSONResponse({"detail": "Please sign in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    return guard
