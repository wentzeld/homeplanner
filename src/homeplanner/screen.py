"""Screen sleep.

- is_sleep_time(now, off, on): pure check, handles windows crossing midnight.
- WlrRandrScreen: wlr-randr --output <name> --on/--off (Wayland on Raspberry Pi OS).
- ScreenScheduler: applies the schedule every 30s; wake() keeps the screen on for wake_minutes.
- watch_input(): any key/mouse input (via evdev) calls wake(). Linux only.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Protocol

from homeplanner.config import Sleep

log = logging.getLogger(__name__)

TICK_SECONDS = 30
INPUT_RESCAN_SECONDS = 30


def is_sleep_time(now: time, off: time, on: time) -> bool:
    """True if `now` is inside the [off, on) sleep window. off == on means never sleep."""
    if off == on:
        return False
    if off < on:  # e.g. 01:00-06:00
        return off <= now < on
    return now >= off or now < on  # crosses midnight, e.g. 22:00-06:30


class ScreenController(Protocol):
    async def set_power(self, on: bool) -> None: ...


class WlrRandrScreen:
    def __init__(self, output: str):
        self.output = output

    def command(self, on: bool) -> list[str]:
        return ["wlr-randr", "--output", self.output, "--on" if on else "--off"]

    async def set_power(self, on: bool) -> None:
        proc = await asyncio.create_subprocess_exec(
            *self.command(on), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"wlr-randr failed ({proc.returncode}): {stderr.decode().strip()}")


class NullScreen:
    """Used off the Pi (development, tests): only logs."""

    async def set_power(self, on: bool) -> None:
        log.info("screen %s (no-op)", "on" if on else "off")


class ScreenScheduler:
    def __init__(self, controller: ScreenController, sleep: Sleep, now: Callable[[], datetime]):
        self._controller = controller
        self._sleep = sleep
        self._now = now
        self.is_on: bool | None = None  # unknown until first apply
        self.awake_until: datetime | None = None

    def should_be_on(self) -> bool:
        now = self._now()
        if self.awake_until is not None and now < self.awake_until:
            return True
        return not is_sleep_time(now.time(), self._sleep.off, self._sleep.on)

    async def tick(self) -> None:
        desired = self.should_be_on()
        if desired == self.is_on:
            return
        try:
            await self._controller.set_power(desired)
        except Exception as e:  # leave is_on unchanged so the next tick retries
            log.warning("could not turn screen %s: %s", "on" if desired else "off", e)
            return
        self.is_on = desired

    async def wake(self) -> None:
        """Called on user input: keep the screen on for wake_minutes from now."""
        self.awake_until = self._now() + timedelta(minutes=self._sleep.wake_minutes)
        await self.tick()

    async def run(self, interval: float = TICK_SECONDS) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(interval)


async def watch_input(on_input: Callable[[], object], rescan: float = INPUT_RESCAN_SECONDS) -> None:
    """Call on_input() (sync or async) for key/button/mouse-move events on any input device.
    Rescans periodically so a keyboard/mouse plugged in later is picked up.
    Requires the `evdev` package and membership of the `input` group."""
    import evdev  # Linux only; optional dependency (pip install .[pi])

    watched: dict[str, asyncio.Task] = {}

    async def read(device: "evdev.InputDevice") -> None:
        try:
            async for event in device.async_read_loop():
                if event.type in (evdev.ecodes.EV_KEY, evdev.ecodes.EV_REL):
                    result = on_input()
                    if asyncio.iscoroutine(result):
                        await result
        except OSError as e:  # device unplugged
            log.info("stopped watching %s: %s", device.path, e)
        finally:
            watched.pop(device.path, None)

    try:
        while True:
            for path in evdev.list_devices():
                if path in watched:
                    continue
                try:
                    device = evdev.InputDevice(path)
                except OSError as e:
                    log.warning("cannot open %s (is the user in the 'input' group?): %s", path, e)
                    continue
                caps = device.capabilities()
                if evdev.ecodes.EV_KEY in caps or evdev.ecodes.EV_REL in caps:
                    log.info("watching input device %s (%s)", device.path, device.name)
                    watched[path] = asyncio.create_task(read(device))
                else:
                    device.close()
            await asyncio.sleep(rescan)
    finally:
        for task in list(watched.values()):
            task.cancel()
