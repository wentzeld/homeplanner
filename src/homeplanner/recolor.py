"""One-time recolor of upcoming Family events, e.g. when a family member changes color.

    .venv/bin/python -m homeplanner.recolor Lavender Flamingo [--yes]

Lists every event from now on with the first color (single events, repeating series once, and
individually changed occurrences), then asks before changing anything. Only the color changes.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import datetime

from homeplanner.colors import GOOGLE_COLOR_NAMES, event_color_id
from homeplanner.config import load_config
from homeplanner.google_calendar import EventNotFound, GoogleCalendar


def plan_recolor(events: list[dict], from_id: str) -> list[dict]:
    """The events to change: not cancelled, and tagged with from_id."""
    return [e for e in events if e.get("status") != "cancelled" and e.get("colorId") == from_id]


def describe(event: dict) -> str:
    start = event.get("start", {})
    when = start.get("date") or (start.get("dateTime", "")[:16].replace("T", " "))
    kind = " (repeating series)" if event.get("recurrence") else (
        " (one changed occurrence)" if event.get("recurringEventId") else "")
    return f"{when}  {event.get('summary') or '(No title)'}{kind}"


async def apply_recolor(source, plan: list[dict], to_id: str) -> tuple[int, list[str]]:
    """Change each event's color (get -> set colorId -> full update). Returns (done, problems)."""
    done, problems = 0, []
    for ev in plan:
        try:
            body = await source.get_event(ev["id"])
            body["colorId"] = to_id
            await source.update_event(ev["id"], body)
            done += 1
        except EventNotFound:
            problems.append(f"{describe(ev)}: no longer exists, skipped")
        except Exception as e:
            problems.append(f"{describe(ev)}: {e}")
    return done, problems


async def run(source, from_id: str, to_id: str, now: datetime, yes: bool,
              ask: Callable[[str], str] = input, out=print) -> int:
    plan = plan_recolor(await source.list_raw(now), from_id)
    names = f"{GOOGLE_COLOR_NAMES[from_id]} → {GOOGLE_COLOR_NAMES[to_id]}"
    if not plan:
        out(f"No upcoming {GOOGLE_COLOR_NAMES[from_id]} events. Nothing to do.")
        return 0
    out(f"Upcoming events to recolor ({names}):")
    for ev in plan:
        out(f"  {describe(ev)}")
    if not yes and ask(f"Recolor {len(plan)} events? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Cancelled. Nothing was changed.")
        return 1
    done, problems = await apply_recolor(source, plan, to_id)
    for p in problems:
        out(f"  ! {p}")
    out(f"Recolored {done} of {len(plan)} events.")
    return 0 if not problems else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("from_color", help="current color, e.g. Lavender")
    parser.add_argument("to_color", help="new color, e.g. Flamingo")
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    parser.add_argument("--config", help="path to config.toml (default: HOMEPLANNER_CONFIG or ./config.toml)")
    args = parser.parse_args(argv)
    try:
        from_id, to_id = event_color_id(args.from_color), event_color_id(args.to_color)
    except ValueError as e:
        parser.error(str(e))
    if from_id == to_id:
        parser.error("the two colors are the same")
    config = load_config(args.config)
    source = GoogleCalendar(config.calendar_id, config.service_account_file)
    return asyncio.run(run(source, from_id, to_id, datetime.now(config.tz), args.yes))


if __name__ == "__main__":
    sys.exit(main())
