import pytest
from pydantic import ValidationError

from homeplanner.colors import (
    EXTENDED_COLORS,
    GOOGLE_EVENT_COLORS,
    color_hex,
    color_name,
    event_color_id,
    is_color,
    palette,
)
from homeplanner.config import Config
from homeplanner.events import google_to_display
from homeplanner.external import NewExternalCalendar
from tests.test_config import example_data


def test_palette_has_googles_24_colors_once_each():
    p = palette()
    assert len(p) == 24 == len({c["id"] for c in p})
    assert {c["id"] for c in p} == set(GOOGLE_EVENT_COLORS) | set(EXTENDED_COLORS)
    assert {"id": "cherry-blossom", "name": "Cherry blossom", "color": "#d81b60"} in p
    assert {"id": "4", "name": "Flamingo", "color": "#e67c73"} in p


def test_color_lookups():
    assert color_hex("11") == "#d50000" and color_name("11") == "Tomato"
    assert color_hex("cobalt") == "#4285f4" and color_name("cobalt") == "Cobalt"
    assert is_color("7") and is_color("birch") and not is_color("pink") and not is_color("12")


@pytest.mark.parametrize("value, expected", [("Flamingo", "4"), ("flamingo", "4"), ("4", "4"), (" Tomato ", "11")])
def test_event_color_id(value, expected):
    assert event_color_id(value) == expected


@pytest.mark.parametrize("value", ["Cherry blossom", "pink", "12", ""])
def test_event_color_id_rejects_non_event_colors(value):
    with pytest.raises(ValueError):
        event_color_id(value)


def test_external_calendars_accept_full_palette_and_old_ids():
    assert NewExternalCalendar(name="School", url="https://x/y.ics", color_id="cherry-blossom").color_id == "cherry-blossom"
    assert NewExternalCalendar(name="School", url="https://x/y.ics", color_id="10").color_id == "10"
    with pytest.raises(ValidationError):
        NewExternalCalendar(name="School", url="https://x/y.ics", color_id="pink")


@pytest.mark.parametrize("value, stored", [
    ("cherry-blossom", "cherry-blossom"), ("Cherry blossom", "cherry-blossom"), ("flamingo", "4"), ("4", "4"),
])
def test_member_display_color(value, stored):
    data = example_data()
    data["members"][0]["display_color"] = value
    assert Config.model_validate(data).members[0].display_color == stored


def test_member_display_color_rejected():
    data = example_data()
    data["members"][0]["display_color"] = "hot pink"
    with pytest.raises(ValidationError, match="display_color"):
        Config.model_validate(data)


def test_display_color_used_on_wall_but_google_color_id_unchanged():
    data = example_data()  # Alex = color_id 9
    data["members"][0]["display_color"] = "cherry-blossom"
    config = Config.model_validate(data)
    ev = google_to_display({"id": "a", "summary": "Ballet", "colorId": "9",
                            "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}}, config)
    assert ev.member == "Alex" and ev.color == "#d81b60"
