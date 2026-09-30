"""Load and validate the HomePlanner TOML config."""

import os
import tomllib
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from homeplanner.colors import EXTENDED_COLORS, GOOGLE_COLOR_NAMES, event_color_id

DEFAULT_CONFIG_PATH = "config.toml"
# Also hard-coded in deploy/setup-from-sdcard.sh (runs before the app is installed).
DEFAULT_KEY_PATH = "~/.config/homeplanner/service-account.json"
GOOGLE_COLOR_IDS = {str(i) for i in range(1, 12)}


class Member(BaseModel):
    name: str = Field(min_length=1)
    color_id: str
    # Optional wall-only color from Google's full calendar palette (e.g. "cherry-blossom").
    # Phones keep showing color_id, since Google events only support the 11 event colors.
    display_color: str | None = None

    @field_validator("display_color")
    @classmethod
    def _valid_display_color(cls, v: str | None) -> str | None:
        if v is None:
            return None
        key = v.strip().lower().replace(" ", "-")
        if key in EXTENDED_COLORS:
            return key
        try:
            return event_color_id(v)  # "flamingo" / "Flamingo" / "4"
        except ValueError:
            names = ", ".join(list(EXTENDED_COLORS) + [n.lower() for n in GOOGLE_COLOR_NAMES.values()])
            raise ValueError(f"display_color must be one of: {names}") from None

    @field_validator("color_id")
    @classmethod
    def _valid_color(cls, v: str) -> str:
        if v not in GOOGLE_COLOR_IDS:
            raise ValueError("color_id must be a Google event color '1'..'11'")
        return v


class Weather(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    temperature_unit: Literal["celsius", "fahrenheit"] = "celsius"


class Sleep(BaseModel):
    off: time
    on: time
    wake_minutes: int = Field(default=5, ge=1)
    output: str = "HDMI-A-1"


class Config(BaseModel):
    calendar_id: str = Field(min_length=1)
    timezone: str
    # Optional: defaults to the running user's home folder, so no username is needed.
    service_account_file: Path = Field(default=Path(DEFAULT_KEY_PATH), validate_default=True)
    members: list[Member] = Field(min_length=1)
    weather: Weather
    sleep: Sleep
    # Last synced events are saved here so the display has data after an offline boot.
    cache_dir: Path = Path("cache")
    # App data that must survive (external calendar list, remote-access PIN). Not a cache.
    data_dir: Path = Path("data")

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    @field_validator("service_account_file")
    @classmethod
    def _absolute_key_path(cls, v: Path) -> Path:
        v = v.expanduser()
        if not v.is_absolute():
            raise ValueError(
                f"service_account_file must be an absolute path (starting with / or ~/), got {str(v)!r}"
            )
        return v

    @field_validator("members")
    @classmethod
    def _unique_members(cls, v: list[Member]) -> list[Member]:
        names = [m.name for m in v]
        if len(names) != len(set(names)):
            raise ValueError("member names must be unique")
        colors = [m.color_id for m in v]
        if len(colors) != len(set(colors)):
            raise ValueError("member color_ids must be unique (color identifies the person)")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("HOMEPLANNER_CONFIG", DEFAULT_CONFIG_PATH))
    with path.open("rb") as f:
        return Config.model_validate(tomllib.load(f))
