import tomllib
from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from homeplanner.config import Config, load_config

EXAMPLE = Path(__file__).parent.parent / "config.example.toml"


def example_data() -> dict:
    return tomllib.loads(EXAMPLE.read_text())


def test_example_config_loads():
    config = load_config(EXAMPLE)
    assert config.members[0].name == "Alex"
    assert config.sleep.off == time(22, 0)
    assert config.sleep.on == time(6, 30)
    assert str(config.tz) == "America/Los_Angeles"


def test_config_path_from_env(monkeypatch):
    monkeypatch.setenv("HOMEPLANNER_CONFIG", str(EXAMPLE))
    assert load_config().calendar_id == example_data()["calendar_id"]


@pytest.mark.parametrize("color_id", ["0", "12", "blue", ""])
def test_invalid_member_color(color_id):
    data = example_data()
    data["members"][0]["color_id"] = color_id
    with pytest.raises(ValidationError, match="color_id"):
        Config.model_validate(data)


def test_unknown_timezone():
    data = example_data()
    data["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError, match="timezone"):
        Config.model_validate(data)


def test_duplicate_member_names():
    data = example_data()
    data["members"][1]["name"] = data["members"][0]["name"]
    with pytest.raises(ValidationError, match="unique"):
        Config.model_validate(data)


def test_requires_at_least_one_member():
    data = example_data()
    data["members"] = []
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_invalid_sleep_time():
    data = example_data()
    data["sleep"]["off"] = "25:00"
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_duplicate_member_colors():
    data = example_data()
    data["members"][1]["color_id"] = data["members"][0]["color_id"]
    with pytest.raises(ValidationError, match="color_ids must be unique"):
        Config.model_validate(data)


def test_optional_fields_default():
    data = example_data()
    del data["cache_dir"]
    del data["weather"]["temperature_unit"]
    config = Config.model_validate(data)
    assert config.cache_dir == Path("cache")
    assert config.weather.temperature_unit == "celsius"


def test_key_path_defaults_to_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    data = example_data()
    data.pop("service_account_file", None)
    config = Config.model_validate(data)
    assert config.service_account_file == tmp_path / ".config/homeplanner/service-account.json"


def test_key_path_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    data = example_data()
    data["service_account_file"] = "~/keys/k.json"
    assert Config.model_validate(data).service_account_file == tmp_path / "keys/k.json"


def test_absolute_key_path_unchanged():
    data = example_data()
    data["service_account_file"] = "/home/pi-user/.config/homeplanner/service-account.json"
    assert Config.model_validate(data).service_account_file == Path(
        "/home/pi-user/.config/homeplanner/service-account.json"
    )


@pytest.mark.parametrize("path", ["home/pi-user/key.json", "key.json", "./key.json"])
def test_relative_key_path_rejected(path):
    data = example_data()
    data["service_account_file"] = path
    with pytest.raises(ValidationError, match="absolute path"):
        Config.model_validate(data)
