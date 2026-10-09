"""Provenance labels: where a piece of context came from, carried with the file.

Three labels, deliberately few and never upgraded or rewritten by fileflow:

``observed``   fetched from somewhere (or otherwise original source material)
``generated``  produced by a model - lower trust; e.g. a saved analysis or `fileflow ask --save`
``user``       you wrote it

A label records *origin*, not *truth*: "observed" means "this text was at that URL when it was
fetched", not "this is correct". Labels live in plain YAML front matter at the top of a file so
they survive copying, committing and diffing. No database.
"""

import json
import re

LABELS = ("observed", "generated", "user")
_KEY = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*([^\n]*)$")
_WS = " \t\r\n"  # ASCII whitespace only: the JS engine must agree byte for byte
_ATTR_SAFE = re.compile(r"[\x00-\x1f\x7f\"<>&]")


def _unquote(raw):
    raw = raw.strip(_WS)
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        try:
            return json.loads(raw)  # fetch writes JSON-style strings (valid YAML double-quoted scalars)
        except ValueError:
            return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]
    return raw


def parse_frontmatter(text):
    """Minimal ``---`` YAML frontmatter reader: ``(dict, body)`` or ``(None, text)``."""
    lines = text.split("\n")
    if not lines or lines[0].strip(_WS) != "---":
        return None, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip(_WS) == "---"), None)
    if end is None:
        return None, text
    data, key = {}, None
    for line in lines[1:end]:
        m = _KEY.match(line)
        if m:
            key = m.group(1)
            val = m.group(2).strip(_WS)
            data[key] = "" if val in (">", "|", ">-", "|-") else _unquote(val)
        elif key and line.startswith((" ", "\t")) and line.strip(_WS):
            data[key] = (data[key] + " " + line.strip(_WS)).strip(_WS)
    return data, "\n".join(lines[end + 1:])


def read_provenance(text):
    """``{"provenance": label, "source_url"?: str}`` if the text carries a valid label, else ``None``."""
    fm, _ = parse_frontmatter(text)
    if not fm:
        return None
    label = str(fm.get("provenance", "")).strip(_WS).lower()
    if label not in LABELS:
        return None
    out = {"provenance": label}
    url = str(fm.get("source_url", "")).strip(_WS)
    if url:
        out["source_url"] = url
    return out


def attr_value(value):
    """Make ``value`` safe inside an XML attribute / one-line annotation (no quotes, tags, newlines)."""
    return _ATTR_SAFE.sub("", str(value))[:300]


def build_frontmatter(fields):
    """Render ordered ``(key, value)`` pairs as front matter; strings are JSON-quoted (one line, safe)."""
    lines = ["---"]
    for key, value in fields:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append("%s: %s" % (key, value))
        else:
            lines.append("%s: %s" % (key, json.dumps(str(value), ensure_ascii=False) if key not in ("provenance",) else value))
    lines.append("---")
    return "\n".join(lines) + "\n"
