from datetime import datetime

import pytest

from homeplanner.recolor import main, plan_recolor, run
from tests.fakes import FakeCalendar

NOW = datetime(2026, 10, 5, 9)

EVENTS = [
    {"id": "single", "summary": "Ballet", "colorId": "1", "start": {"dateTime": "2026-10-07T16:00:00-07:00"}},
    {"id": "series", "summary": "Piano", "colorId": "1", "recurrence": ["RRULE:FREQ=WEEKLY"],
     "start": {"dateTime": "2026-09-01T16:00:00-07:00"}},
    {"id": "series_20261020", "summary": "Piano (moved)", "colorId": "1", "recurringEventId": "series",
     "start": {"dateTime": "2026-10-21T16:00:00-07:00"}},
    {"id": "other", "summary": "Soccer", "colorId": "11", "start": {"date": "2026-10-08"}},
    {"id": "nocolor", "summary": "Dinner", "start": {"date": "2026-10-08"}},
    {"id": "gone", "summary": "Old", "colorId": "1", "status": "cancelled", "start": {"date": "2026-10-09"}},
]


class RawCalendar(FakeCalendar):
    async def list_raw(self, time_min):
        self.raw_since = time_min
        return [dict(e) for e in self.events]


def test_plan_selects_only_matching_live_events():
    assert [e["id"] for e in plan_recolor(EVENTS, "1")] == ["single", "series", "series_20261020"]


async def test_confirm_no_changes_nothing():
    cal = RawCalendar([dict(e) for e in EVENTS])
    lines = []
    code = await run(cal, "1", "4", NOW, yes=False, ask=lambda _: "n", out=lines.append)
    assert code == 1 and "Nothing was changed" in lines[-1]
    assert not getattr(cal, "updated", [])
    assert "(repeating series)" in "\n".join(lines) and "(one changed occurrence)" in "\n".join(lines)


async def test_confirm_yes_recolors_and_keeps_everything_else():
    cal = RawCalendar([dict(e) for e in EVENTS])
    lines = []
    code = await run(cal, "1", "4", NOW, yes=False, ask=lambda _: "y", out=lines.append)
    assert code == 0 and lines[-1] == "Recolored 3 of 3 events."
    assert cal.raw_since == NOW
    changed = {eid: body for eid, body in cal.updated}
    assert set(changed) == {"single", "series", "series_20261020"}
    assert all(b["colorId"] == "4" for b in changed.values())
    assert changed["series"]["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    assert changed["single"]["summary"] == "Ballet"


async def test_nothing_to_do():
    lines = []
    assert await run(RawCalendar([]), "1", "4", NOW, yes=True, out=lines.append) == 0
    assert "Nothing to do" in lines[0]


async def test_missing_event_reported_not_fatal():
    class Vanishing(RawCalendar):
        async def get_event(self, event_id):
            if event_id == "single":
                from homeplanner.google_calendar import EventNotFound
                raise EventNotFound(event_id)
            return await super().get_event(event_id)

    lines = []
    code = await run(Vanishing([dict(e) for e in EVENTS]), "1", "4", NOW, yes=True, out=lines.append)
    assert code == 2 and lines[-1] == "Recolored 2 of 3 events."
    assert any("no longer exists" in line for line in lines)


@pytest.mark.parametrize("argv", [["Pink", "Flamingo"], ["Lavender", "Lavender"]])
def test_cli_rejects_bad_colors(argv, capsys):
    with pytest.raises(SystemExit):
        main(argv)
    assert "error" in capsys.readouterr().err
