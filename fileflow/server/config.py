"""Read/write the project's `.files-to-prompt` configuration file.

The file is TOML and is optional. Precedence when running: CLI flags > config
file > defaults. The web client persists its recipe via this file (and to
localStorage when run in pure static mode).

Reading is *lenient* by default (a malformed file degrades to defaults so the
CLI keeps working) and *strict* on request (``strict=True`` raises
:class:`ConfigError`), which the server uses so the web client can tell "the
file says X" apart from "the file is currently broken" while someone is
mid-edit.
"""

import json
import os
import re

DEFAULTS = {
    "include": {"patterns": []},
    "exclude": {"patterns": []},
    "output": {
        "format": "default",
        "separators": True,
        "line_numbers": False,
        "max_tokens": 0,
    },
    "ignore": {"gitignore": True, "hidden": False},
}

CONFIG_FILENAME = ".files-to-prompt"
FORMATS = ("default", "xml", "json")


class ConfigError(ValueError):
    """``.files-to-prompt`` exists but is unreadable or has the wrong shape."""


def config_path(root):
    return os.path.join(root, CONFIG_FILENAME)


def read_config(root, strict=False):
    """Return the merged config (defaults overlaid with the on-disk file).

    With ``strict=False`` (default) any problem with the file yields the
    defaults. With ``strict=True`` a parse error or wrongly-shaped section
    raises :class:`ConfigError` instead.

    TOML parsing uses ``tomllib`` (Python 3.11+) and a small built-in subset
    parser on older Pythons (see :func:`_naive_toml`).
    """
    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    path = config_path(root)
    if not os.path.isfile(path):
        return merged
    try:
        data = _load_toml(path)
    except Exception as exc:  # parse error, bad encoding, unreadable file
        if strict:
            raise ConfigError("%s is not valid TOML: %s" % (CONFIG_FILENAME, exc))
        return merged  # a malformed config file degrades to defaults
    for key, value in data.items():
        if key in merged:
            if isinstance(value, dict):
                merged[key].update(value)
            elif strict:
                raise ConfigError("[%s] must be a table" % key)
            # lenient: ignore a wrongly-shaped known section, keep defaults
        else:
            merged[key] = value  # unknown keys are preserved (forward compat)
    for section in ("include", "exclude"):
        pats = merged[section].get("patterns", [])
        if not (isinstance(pats, list) and all(isinstance(p, str) for p in pats)):
            if strict:
                raise ConfigError("[%s] patterns must be an array of strings" % section)
            merged[section]["patterns"] = []
    return merged


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _q(value):
    """Render ``value`` as a TOML basic string (all special characters escaped)."""
    out = []
    for ch in str(value):
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


def _toml_array(values):
    return "[%s]" % ", ".join(_q(v) for v in values)


def _bool(section, key, value):
    if not isinstance(value, bool):
        raise ValueError("%s.%s must be true or false" % (section, key))
    return str(value).lower()


def _patterns(section, config):
    pats = (config.get(section) or {}).get("patterns", [])
    if not (isinstance(pats, list) and all(isinstance(p, str) for p in pats)):
        raise ValueError("%s.patterns must be an array of strings" % section)
    return pats


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
KNOWN_SECTIONS = ("output", "include", "exclude", "ignore")


def _key(name):
    return name if _BARE_KEY.match(name) else _q(name)


def _scalar(value, where):
    """Render a TOML scalar or array of scalars; refuse anything else."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("%s: cannot write a non-finite number" % where)
        return repr(value)
    if isinstance(value, str):
        return _q(value)
    if isinstance(value, list):
        return "[%s]" % ", ".join(_scalar(v, where) for v in value)
    raise ValueError("cannot preserve %s: unsupported value type %s" % (where, type(value).__name__))


def _emit_table(path, table, lines):
    """Append ``[path]`` (scalars first, then nested tables) to ``lines``."""
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    if path and (scalars or not tables):
        lines.append("")
        lines.append("[%s]" % ".".join(_key(p) for p in path))
    for k, v in scalars:
        lines.append("%s = %s" % (_key(k), _scalar(v, ".".join(path + [k]))))
    for k, v in tables:
        _emit_table(path + [k], v, lines)


def _existing(root):
    """The on-disk file as a dict ({} if absent or broken: a broken file has nothing safe to preserve)."""
    path = config_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        return _load_toml(path)
    except Exception:
        return {}


def _existing_extras(root):
    """Top-level keys of the on-disk file the web client does not manage."""
    return {k: v for k, v in _existing(root).items() if k not in KNOWN_SECTIONS}


def write_config(root, config):
    """Persist ``config`` to ``root/.files-to-prompt`` as valid TOML.

    Raises :class:`ValueError` if ``config`` has the wrong shape (so a bad API
    payload can never produce a file the reader would then reject).

    Anything the payload does not mention is **preserved** from the existing
    file: the web client only edits output/include/exclude/ignore, and saving
    must never delete presets, ``version``, ``[secrets]`` or unknown keys.
    (Comments are not preserved.)
    """
    existing = _existing(root)
    # A section the payload does not mention keeps what the file already says (a client that edits
    # only some settings must never blank the rest, e.g. a hand-written [include] list).
    config = dict(config)
    for name in KNOWN_SECTIONS:
        if name not in config and isinstance(existing.get(name), dict):
            config[name] = existing[name]
    extras = _existing_extras(root)
    extras.update({k: v for k, v in config.items() if k not in KNOWN_SECTIONS})
    # Writing is all-or-nothing: render everything before touching the file.
    extra_lines = []
    top_scalars = {k: v for k, v in extras.items() if not isinstance(v, dict)}
    extra_tables = {k: v for k, v in extras.items() if isinstance(v, dict)}
    for k, v in top_scalars.items():
        extra_lines.append("%s = %s" % (_key(k), _scalar(v, k)))
    tail = []
    for k, v in extra_tables.items():
        _emit_table([k], v, tail)

    out = config.get("output") or {}
    fmt = out.get("format", "default")
    if fmt not in FORMATS:
        raise ValueError("output.format must be one of: %s" % ", ".join(FORMATS))
    try:
        max_tokens = int(out.get("max_tokens", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("output.max_tokens must be an integer")
    if max_tokens < 0:
        raise ValueError("output.max_tokens must be >= 0")
    ig = config.get("ignore") or {}

    lines = extra_lines + ([""] if extra_lines else []) + [
        "[output]",
        "format = %s" % _q(fmt),
        "separators = %s" % _bool("output", "separators", out.get("separators", True)),
        "line_numbers = %s" % _bool("output", "line_numbers", out.get("line_numbers", False)),
        "max_tokens = %d" % max_tokens,
        "",
        "[include]",
        "patterns = %s" % _toml_array(_patterns("include", config)),
        "",
        "[exclude]",
        "patterns = %s" % _toml_array(_patterns("exclude", config)),
        "",
        "[ignore]",
        "gitignore = %s" % _bool("ignore", "gitignore", ig.get("gitignore", True)),
        "hidden = %s" % _bool("ignore", "hidden", ig.get("hidden", False)),
    ] + tail
    text = "\n".join(lines) + "\n"
    with open(config_path(root), "w", encoding="utf-8") as f:
        f.write(text)
    return text


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _load_toml(path):
    """Parse ``path`` with ``tomllib`` if available, else the subset parser."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # same parser, backported (a declared dependency)
        except ImportError:
            return _naive_toml(path)
    with open(path, "rb") as f:
        return tomllib.load(f)


_UNESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(body):
    """Decode the escape sequences of a TOML basic string body."""
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1 : i + 2]
        if nxt in _UNESCAPES:
            out.append(_UNESCAPES[nxt])
            i += 2
        elif nxt == "u" and len(body) >= i + 6:
            out.append(chr(int(body[i + 2 : i + 6], 16)))
            i += 6
        else:
            raise ValueError("invalid escape sequence: \\%s" % nxt)
    return "".join(out)


def _strip_comment(line):
    """Drop a trailing ``# comment`` that is not inside a quoted string."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "#":
            return line[:i]
        i += 1
    return line


def _bracket_depth(text):
    """Net ``[`` minus ``]`` count outside quoted strings."""
    depth = 0
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return depth


def _split_items(body):
    """Split an array body on top-level commas (commas inside quotes ignored)."""
    items, cur, quote, i = [], [], None, 0
    while i < len(body):
        ch = body[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(body):
                cur.append(body[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    items.append("".join(cur))
    return [s.strip() for s in items if s.strip()]


def _parse_value(text):
    text = text.strip()
    if text.startswith(('"""', "'''")):
        raise ValueError("multi-line strings need Python 3.11+ or `pip install tomli`")
    if text.startswith("{"):
        raise ValueError("inline tables need Python 3.11+ or `pip install tomli`")
    if text.startswith("["):
        if not text.endswith("]"):
            raise ValueError("unterminated array")
        return [_parse_value(item) for item in _split_items(text[1:-1])]
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unescape(text[1:-1])
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    raise ValueError("unsupported value: %s" % text)


def _naive_toml(path):
    """Small TOML subset parser for Python < 3.11.

    Supports ``[section]`` tables, ``key = value`` with strings (basic and
    literal), integers, floats, booleans, and (multi-line) arrays of those,
    plus ``#`` comments. That covers every key ``.files-to-prompt`` uses. It
    raises :class:`ValueError` on anything it does not understand, so callers
    can report a broken file rather than silently misreading it.
    """
    result = {}
    section = None
    pending_key, pending = None, []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = _strip_comment(raw).strip()
            if pending_key is not None:  # inside a multi-line array
                pending.append(line)
                joined = " ".join(pending)
                if _bracket_depth(joined) <= 0:
                    _store(result, section, pending_key, _parse_value(joined))
                    pending_key, pending = None, []
                continue
            if not line:
                continue
            if line.startswith("[") and line.endswith("]") and "=" not in line:
                if line.startswith("[["):
                    raise ValueError("arrays of tables are not supported: %s" % line)
                section = _split_dotted(line[1:-1])
                _table(result, section)
                continue
            if "=" not in line:
                raise ValueError("expected key = value: %s" % line)
            key, _, value = line.partition("=")
            key = key.strip().strip('"')
            value = value.strip()
            if value.startswith("[") and _bracket_depth(value) > 0:
                pending_key, pending = key, [value]
                continue
            _store(result, section, key, _parse_value(value))
    if pending_key is not None:
        raise ValueError("unterminated array for key %r" % pending_key)
    return result


def _split_dotted(name):
    """``presets.hero.vars`` / ``presets."my key"`` -> list of key parts."""
    parts, cur, quote = [], [], None
    for ch in name.strip():
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ('"', "'"):
            quote = ch
        elif ch == ".":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    if quote or not all(parts):
        raise ValueError("invalid table name: [%s]" % name)
    return parts


def _table(result, parts):
    """Return (creating as needed) the nested table at ``parts``."""
    node = result
    for part in parts:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ValueError("%s is both a value and a table" % part)
    return node


def _store(result, section, key, value):
    _table(result, section or [])[key] = value
