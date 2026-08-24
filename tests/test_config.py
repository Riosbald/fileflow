from pathlib import Path

from fileflow.config import (
    CONFIG_FILENAME,
    config_path,
    config_to_options,
    read_config,
    write_config,
)


def test_read_missing_config_returns_empty(tmp_path: Path):
    assert read_config(tmp_path) == {}


def test_round_trip_scalars(tmp_path: Path):
    data = {
        "format": "json",
        "separators": True,
        "line_numbers": False,
        "max_tokens": 8000,
        "include_hidden": False,
        "ignore_gitignore": True,
    }
    write_config(tmp_path, data)
    assert read_config(tmp_path) == data


def test_round_trip_multiple_exclude_patterns_toml_array(tmp_path: Path):
    """Regression: patterns must be a proper TOML array, not repeated
    scalar lines (which is invalid TOML and dropped all but the last)."""
    write_config(tmp_path, {"format": "xml", "exclude": {"patterns": ["*.log", "node_modules/*", "dist/*"]}})
    text = config_path(tmp_path).read_text()
    assert 'patterns = ["*.log", "node_modules/*", "dist/*"]' in text
    parsed = read_config(tmp_path)
    assert parsed["exclude"]["patterns"] == ["*.log", "node_modules/*", "dist/*"]


def test_write_accepts_plain_exclude_list(tmp_path: Path):
    write_config(tmp_path, {"exclude": ["*.log", "*.tmp"]})
    assert read_config(tmp_path)["exclude"]["patterns"] == ["*.log", "*.tmp"]


def test_pattern_quoting(tmp_path: Path):
    tricky = 'weird "quoted" \\ pattern'
    write_config(tmp_path, {"exclude": {"patterns": [tricky]}})
    assert read_config(tmp_path)["exclude"]["patterns"] == [tricky]


def test_config_to_options_maps_keys():
    data = {
        "format": "markdown",
        "line_numbers": True,
        "max_tokens": 100,
        "exclude": {"patterns": ["*.log"]},
        "unknown_key": "ignored",
    }
    out = config_to_options(data)
    assert out == {
        "format": "markdown",
        "line_numbers": True,
        "max_tokens": 100,
        "exclude": ("*.log",),
    }


def test_config_to_options_tolerates_scalar_pattern():
    assert config_to_options({"exclude": {"patterns": "*.log"}})["exclude"] == ("*.log",)


def test_config_to_options_empty():
    assert config_to_options({}) == {}


def test_config_filename_constant(tmp_path: Path):
    write_config(tmp_path, {"format": "xml"})
    assert (tmp_path / CONFIG_FILENAME).is_file()
