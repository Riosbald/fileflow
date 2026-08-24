"""Read/write the ``.files-to-prompt`` project config (TOML).

Shared by the CLI (via a deferred import so the CLI stays free of any
server dependencies) and the server.  Only stdlib is used here.

Config shape::

    format = "json"
    separators = true
    line_numbers = true
    max_tokens = 8000
    include_hidden = false
    ignore_gitignore = false

    [exclude]
    patterns = ["*.log", "node_modules/*"]
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

CONFIG_FILENAME = ".files-to-prompt"

_SCALAR_KEYS = (
    "format",
    "separators",
    "line_numbers",
    "max_tokens",
    "include_hidden",
    "ignore_gitignore",
)


def config_path(root: Path | str) -> Path:
    return Path(root) / CONFIG_FILENAME


def read_config(root: Path | str) -> dict:
    """Parse the config file at *root*. Returns {} when absent."""
    path = config_path(root)
    if not path.is_file():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def config_to_options(data: dict) -> dict:
    """Map a parsed config dict onto render-option overrides."""
    out: dict = {}
    for key in _SCALAR_KEYS:
        if key in data:
            out[key] = data[key]
    exclude = data.get("exclude", {})
    patterns = exclude.get("patterns", []) if isinstance(exclude, dict) else []
    if isinstance(patterns, str):  # tolerate a lone scalar
        patterns = [patterns]
    if patterns:
        out["exclude"] = tuple(str(p) for p in patterns)
    return out


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)  # valid TOML basic string
    raise TypeError(f"unsupported config value: {value!r}")


def write_config(root: Path | str, data: dict) -> Path:
    """Serialize *data* to the config file.

    ``exclude.patterns`` is emitted as a proper TOML array so multiple
    patterns round-trip correctly.
    """
    lines: list[str] = []
    for key in _SCALAR_KEYS:
        if key in data and data[key] is not None:
            lines.append(f"{key} = {_toml_value(data[key])}")

    patterns = None
    exclude = data.get("exclude")
    if isinstance(exclude, dict):
        patterns = exclude.get("patterns")
    elif isinstance(exclude, (list, tuple)):
        patterns = list(exclude)
    if patterns:
        if isinstance(patterns, str):
            patterns = [patterns]
        array = ", ".join(json.dumps(str(p)) for p in patterns)
        lines.append("")
        lines.append("[exclude]")
        lines.append(f"patterns = [{array}]")

    path = config_path(root)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
