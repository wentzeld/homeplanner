"""Google Calendar colors.

- Event colors ("1".."11"): the only colors an individual Google event can have; they sync to
  phones. Family members use these.
- Calendar colors: Google's full 24-color calendar palette (the 11 above plus 13 more). Used for
  other calendars and optional wall-only display colors; keyed by name, e.g. "cherry-blossom".
"""

# Google Calendar UI event colors (what people see on their phones), keyed by colorId.
GOOGLE_EVENT_COLORS = {
    "1": "#7986cb",  # Lavender
    "2": "#33b679",  # Sage
    "3": "#8e24aa",  # Grape
    "4": "#e67c73",  # Flamingo
    "5": "#f6bf26",  # Banana
    "6": "#f4511e",  # Tangerine
    "7": "#039be5",  # Peacock
    "8": "#616161",  # Graphite
    "9": "#3f51b5",  # Blueberry
    "10": "#0b8043",  # Basil
    "11": "#d50000",  # Tomato
}
GOOGLE_COLOR_NAMES = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo", "5": "Banana", "6": "Tangerine",
    "7": "Peacock", "8": "Graphite", "9": "Blueberry", "10": "Basil", "11": "Tomato",
}

# The rest of Google's calendar palette: key -> (name, hex).
EXTENDED_COLORS = {
    "radicchio": ("Radicchio", "#ad1457"),
    "cherry-blossom": ("Cherry blossom", "#d81b60"),
    "pumpkin": ("Pumpkin", "#ef6c00"),
    "mango": ("Mango", "#f09300"),
    "citron": ("Citron", "#e4c441"),
    "avocado": ("Avocado", "#c0ca33"),
    "pistachio": ("Pistachio", "#7cb342"),
    "eucalyptus": ("Eucalyptus", "#009688"),
    "cobalt": ("Cobalt", "#4285f4"),
    "wisteria": ("Wisteria", "#b39ddb"),
    "amethyst": ("Amethyst", "#9e69af"),
    "cocoa": ("Cocoa", "#795548"),
    "birch": ("Birch", "#a79b8e"),
}

# Google's color picker order (rows of six).
_PALETTE_ORDER = [
    "radicchio", "6", "citron", "10", "9", "3",
    "cherry-blossom", "pumpkin", "avocado", "eucalyptus", "1", "cocoa",
    "11", "mango", "pistachio", "7", "wisteria", "8",
    "4", "5", "2", "cobalt", "amethyst", "birch",
]


def is_color(key: str) -> bool:
    return key in GOOGLE_EVENT_COLORS or key in EXTENDED_COLORS


def color_name(key: str) -> str:
    return GOOGLE_COLOR_NAMES[key] if key in GOOGLE_COLOR_NAMES else EXTENDED_COLORS[key][0]


def color_hex(key: str) -> str:
    return GOOGLE_EVENT_COLORS[key] if key in GOOGLE_EVENT_COLORS else EXTENDED_COLORS[key][1]


def palette() -> list[dict]:
    """All 24 colors in Google's order: [{id, name, color}]."""
    return [{"id": k, "name": color_name(k), "color": color_hex(k)} for k in _PALETTE_ORDER]


def event_color_id(name_or_id: str) -> str:
    """'Flamingo', 'flamingo' or '4' -> '4'. Only event colors (the ones Google events can have)."""
    if name_or_id in GOOGLE_EVENT_COLORS:
        return name_or_id
    for cid, name in GOOGLE_COLOR_NAMES.items():
        if name.lower() == name_or_id.strip().lower():
            return cid
    raise ValueError(f"{name_or_id!r} is not a Google event color: {', '.join(GOOGLE_COLOR_NAMES.values())}")
