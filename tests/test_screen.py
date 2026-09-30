from datetime import datetime, time, timedelta

import pytest

from homeplanner.config import Sleep
from homeplanner.screen import ScreenScheduler, WlrRandrScreen, is_sleep_time

SLEEP = Sleep(off=time(22, 0), on=time(6, 30), wake_minutes=5)


@pytest.mark.parametrize("now, asleep", [
    (time(21, 59), False),
    (time(22, 0), True),
    (time(23, 30), True),
    (time(0, 0), True),
    (time(6, 29), True),
    (time(6, 30), False),
    (time(12, 0), False),
])
def test_overnight_window(now, asleep):
    assert is_sleep_time(now, time(22, 0), time(6, 30)) is asleep


@pytest.mark.parametrize("now, asleep", [
    (time(0, 59), False),
    (time(1, 0), True),
    (time(5, 59), True),
    (time(6, 0), False),
    (time(23, 0), False),
])
def test_same_day_window(now, asleep):
    assert is_sleep_time(now, time(1, 0), time(6, 0)) is asleep


def test_equal_times_never_sleep():
    assert not is_sleep_time(time(3, 0), time(22, 0), time(22, 0))


class FakeScreen:
    def __init__(self):
        self.calls: list[bool] = []
        self.fail = False

    async def set_power(self, on: bool) -> None:
        if self.fail:
            raise RuntimeError("no display")
        self.calls.append(on)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def scheduler(start: datetime):
    screen, clock = FakeScreen(), Clock(start)
    return ScreenScheduler(screen, SLEEP, now=clock), screen, clock


async def test_first_tick_always_applies_and_later_ticks_only_on_change():
    sched, screen, clock = scheduler(datetime(2026, 10, 5, 12, 0))
    await sched.tick()
    await sched.tick()
    assert screen.calls == [True]
    clock.t = datetime(2026, 10, 5, 22, 0)
    await sched.tick()
    await sched.tick()
    assert screen.calls == [True, False]
    clock.t = datetime(2026, 10, 6, 6, 30)
    await sched.tick()
    assert screen.calls == [True, False, True]


async def test_wake_during_sleep_then_back_off():
    sched, screen, clock = scheduler(datetime(2026, 10, 5, 23, 0))
    await sched.tick()
    assert screen.calls == [False]
    await sched.wake()
    assert screen.calls == [False, True]
    clock.t += timedelta(minutes=4, seconds=59)
    await sched.tick()
    assert screen.calls == [False, True]
    clock.t += timedelta(seconds=1)
    await sched.tick()
    assert screen.calls == [False, True, False]


async def test_repeated_input_extends_wake():
    sched, screen, clock = scheduler(datetime(2026, 10, 5, 23, 0))
    await sched.wake()
    clock.t += timedelta(minutes=4)
    await sched.wake()
    clock.t += timedelta(minutes=4)
    await sched.tick()
    assert screen.calls == [True]  # still on: 8 minutes after first input, 4 after the last


async def test_wake_during_day_is_harmless():
    sched, screen, clock = scheduler(datetime(2026, 10, 5, 12, 0))
    await sched.tick()
    await sched.wake()
    assert screen.calls == [True]


async def test_failure_is_retried_next_tick():
    sched, screen, clock = scheduler(datetime(2026, 10, 5, 23, 0))
    screen.fail = True
    await sched.tick()
    assert sched.is_on is None
    screen.fail = False
    await sched.tick()
    assert screen.calls == [False] and sched.is_on is False


def test_wlr_randr_command():
    s = WlrRandrScreen("HDMI-A-1")
    assert s.command(True) == ["wlr-randr", "--output", "HDMI-A-1", "--on"]
    assert s.command(False) == ["wlr-randr", "--output", "HDMI-A-1", "--off"]


async def test_wlr_randr_reports_failure(monkeypatch):
    s = WlrRandrScreen("HDMI-A-1")
    monkeypatch.setattr(s, "command", lambda on: ["sh", "-c", "echo boom >&2; exit 3"])
    with pytest.raises(RuntimeError, match="boom"):
        await s.set_power(False)
