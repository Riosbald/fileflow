"""Named workflow presets (`[presets.<name>]` in ``.files-to-prompt``).

A preset is a saved, bounded task: which paths, what to ask, how big. Running
``fileflow --preset hero`` should need no other flags.

Precedence (one test per row): **CLI flags > preset > root config > defaults**.

Templates are deliberately *not* a language: ``{{name}}`` is replaced by a
string, once, with no logic, no includes and no recursion. An unresolved
``{{name}}`` in the instruction is an error, because the commonest way to waste
a prompt is pasting a half-filled template. File contents are never scanned for
placeholders -- only the instruction text.
"""

import difflib
import os
import re

from .errors import FileflowError
from .server.config import FORMATS

SUPPORTED_VERSION = 1
ASK_PROVIDERS = ("openai", "anthropic")  # kept here so `check` can validate [ask] without importing the model client
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*\}\}")
PROMPT_FIELDS = ("role", "context", "task", "constraints", "format")
_BOOLS = ("separators", "line_numbers", "include_hidden", "ignore_gitignore", "diff", "staged", "patch")


# Every key fileflow reads, per table. Unknown keys are ALLOWED (a newer config must still load on an
# older fileflow), which also means a typo (`formt = "xml"`) silently does nothing. These tables let
# `check` list unknown keys and let a normal run speak up when one is a near-miss of a real key.
CONFIG_KEYS = {
    "": ("version", "output", "exclude", "include", "ignore", "secrets", "presets", "ask", "harness"),
    "output": ("format", "separators", "line_numbers", "max_tokens"),
    "exclude": ("patterns",),
    "include": ("patterns",),
    "ignore": ("hidden", "gitignore"),
    "secrets": ("mode",),
    "ask": ("provider", "model", "max_output_tokens", "base_url", "api_key_env", "api_key"),
    "harness": ("max_lines", "codex_max_bytes"),
}
PRESET_KEYS = ("description", "instruction", "instruction_file", "paths", "exclude_patterns", "include_patterns",
               "max_tokens", "format", "secrets", "vars", "prompt") + _BOOLS


def unknown_keys(cfg):
    """``[{where, key, suggestion}]`` for keys fileflow does not read. ``suggestion`` is the closest
    real key when it is a near-miss (a probable typo), else ``None``."""
    import difflib

    found = []

    def scan(where, table, known):
        if not isinstance(table, dict):
            return
        for key in table:
            if key not in known:
                close = difflib.get_close_matches(str(key), known, n=1, cutoff=0.75)
                found.append({"where": where, "key": str(key), "suggestion": close[0] if close else None})

    scan("top level", cfg, CONFIG_KEYS[""])
    for name, known in CONFIG_KEYS.items():
        if name and isinstance(cfg.get(name), dict):
            scan("[%s]" % name, cfg[name], known)
    presets = cfg.get("presets")
    if isinstance(presets, dict):
        for pname, preset in presets.items():
            scan("[presets.%s]" % pname, preset, PRESET_KEYS)
    return found


def _bad(message, hint=None):
    return FileflowError("E_CONFIG_SCHEMA", message, hint)


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def check_version(cfg):
    """Refuse configs written for a newer schema instead of misreading them."""
    version = cfg.get("version", SUPPORTED_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _bad("`version` must be a positive integer")
    if version > SUPPORTED_VERSION:
        raise _bad(
            "this .files-to-prompt uses schema version %d; this fileflow understands up to %d"
            % (version, SUPPORTED_VERSION),
            "upgrade fileflow: pip install -U fileflow",
        )


def secrets_mode(cfg):
    """The configured secret-guard mode, or ``None`` if unset."""
    section = cfg.get("secrets", {})
    if not isinstance(section, dict):
        raise _bad("[secrets] must be a table")
    mode = section.get("mode")
    if mode is None:
        return None
    from .secretscan import MODES

    if mode not in MODES:
        raise _bad("[secrets] mode must be one of: %s" % ", ".join(MODES))
    return mode


def validate_settings(cfg):
    """Type-check the global tables (``[output] [exclude] [include] [ignore]``).

    These used to be read leniently: a wrong value (``format = 7``, ``separators = "yes"``) was dropped
    without a word, so the run used defaults and the user believed their setting applied (FN-015).
    """
    def table(name):
        section = cfg.get(name, {})
        if not isinstance(section, dict):
            raise _bad("[%s] must be a table" % name)
        return section

    out = table("output")
    if "format" in out and out["format"] not in FORMATS:
        raise _bad("[output] format must be one of: %s (got %r)" % (", ".join(FORMATS), out["format"]))
    for key in ("separators", "line_numbers"):
        if key in out and not isinstance(out[key], bool):
            raise _bad("[output] %s must be true or false (got %r)" % (key, out[key]))
    if "max_tokens" in out and (isinstance(out["max_tokens"], bool) or not isinstance(out["max_tokens"], int) or out["max_tokens"] < 0):
        raise _bad("[output] max_tokens must be a whole number >= 0 (got %r)" % (out["max_tokens"],))
    # [exclude]/[include] patterns are already type-checked by the config reader (server/config.py).
    ig = table("ignore")
    for key in ("hidden", "gitignore"):
        if key in ig and not isinstance(ig[key], bool):
            raise _bad("[ignore] %s must be true or false (got %r)" % (key, ig[key]))


def ask_section(cfg):
    """The validated ``[ask]`` table (provider / model / max_output_tokens only; unknown keys tolerated).

    ``base_url`` and the key's variable name are deliberately NOT read from config: a cloned repository
    must not be able to point your API key somewhere else. They come from the CLI or your environment.
    """
    section = cfg.get("ask", {})
    if not isinstance(section, dict):
        raise _bad("[ask] must be a table")
    if "provider" in section and section["provider"] not in ASK_PROVIDERS:
        raise _bad("[ask] provider must be one of: %s" % ", ".join(ASK_PROVIDERS))
    if "model" in section and not (isinstance(section["model"], str) and section["model"].strip()):
        raise _bad("[ask] model must be a non-empty string")
    n = section.get("max_output_tokens")
    if "max_output_tokens" in section and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
        raise _bad("[ask] max_output_tokens must be a positive integer")
    return section


def validate_preset(name, p):
    """Type-check one preset table; unknown keys are allowed (forward compatible)."""
    where = "presets.%s" % name
    if not _NAME.match(name):
        raise _bad("%s: preset names may use letters, digits, '-' and '_'" % where)
    if not isinstance(p, dict):
        raise _bad("[%s] must be a table" % where)
    for key in ("description", "instruction", "instruction_file"):
        if key in p and not isinstance(p[key], str):
            raise _bad("%s.%s must be a string" % (where, key))
    for key in ("paths", "exclude_patterns", "include_patterns"):
        if key in p and not _strings(p[key]):
            raise _bad("%s.%s must be an array of strings" % (where, key))
    if "max_tokens" in p and (
        isinstance(p["max_tokens"], bool) or not isinstance(p["max_tokens"], int) or p["max_tokens"] < 0
    ):
        raise _bad("%s.max_tokens must be an integer >= 0" % where)
    if "format" in p and p["format"] not in FORMATS:
        raise _bad("%s.format must be one of: %s" % (where, ", ".join(FORMATS)))
    for key in _BOOLS:
        if key in p and not isinstance(p[key], bool):
            raise _bad("%s.%s must be true or false" % (where, key))
    if "secrets" in p:
        from .secretscan import MODES

        if p["secrets"] not in MODES:
            raise _bad("%s.secrets must be one of: %s" % (where, ", ".join(MODES)))
    if "vars" in p:
        v = p["vars"]
        if not isinstance(v, dict) or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v.values()):
            raise _bad("%s.vars must be a table of strings or numbers" % where)
    if "prompt" in p:
        t = p["prompt"]
        if not isinstance(t, dict) or not all(isinstance(x, str) for x in t.values()):
            raise _bad("%s.prompt must be a table of strings (%s)" % (where, ", ".join(PROMPT_FIELDS)))
    sources = [k for k in ("instruction", "instruction_file", "prompt") if k in p]
    if len(sources) > 1:
        raise _bad(
            "%s sets more than one instruction source (%s)" % (where, ", ".join(sources)),
            "keep one of: instruction, instruction_file, [presets.%s.prompt]" % name,
        )


def all_presets(cfg):
    """``{name: preset}`` from the config, validated."""
    check_version(cfg)
    table = cfg.get("presets", {})
    if not isinstance(table, dict):
        raise _bad("[presets] must be a table of presets")
    for name, p in table.items():
        validate_preset(name, p)
    return table


def get_preset(cfg, name):
    presets = all_presets(cfg)
    if name not in presets:
        close = difflib.get_close_matches(name, list(presets), n=1)
        avail = ", ".join(sorted(presets)) or "(none defined)"
        hint = "fileflow presets    (available: %s)" % avail
        if close:
            hint += "  -- did you mean '%s'?" % close[0]
        raise FileflowError("E_PRESET_UNKNOWN", "no preset named '%s'" % name, hint)
    return presets[name]


# ---- paths --------------------------------------------------------------


def confined(root, rel, what="path"):
    """Resolve config-supplied ``rel`` under ``root`` or refuse.

    Anything inside a config file may come from a repository you just cloned,
    so it gets the same boundary as the server: symlinks are resolved *before*
    comparing, and ``../`` or absolute paths that leave the project are refused.
    """
    real_root = os.path.realpath(root)
    candidate = rel if os.path.isabs(rel) else os.path.join(real_root, rel)
    real = os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([real_root, real]) == real_root
    except ValueError:
        inside = False
    if not inside:
        raise FileflowError(
            "E_PATH_OUTSIDE_ROOT",
            "%s '%s' resolves outside the project (%s)" % (what, rel, real_root),
            "keep preset paths inside the project, or pass the path on the command line yourself",
        )
    return real


def preset_paths(root, preset, name):
    """Confined, existing paths of a preset, exactly as written in the config."""
    out = []
    for rel in preset.get("paths", []):
        real = confined(root, rel, "preset path")
        if not os.path.exists(real):
            raise FileflowError(
                "E_PRESET_PATH_MISSING",
                "preset '%s': path '%s' does not exist" % (name, rel),
                "create it, or edit paths in [presets.%s] of .files-to-prompt" % name,
            )
        # Keep the path as written (relative stays relative) so a preset run
        # is byte-identical to typing the same paths by hand -- and never
        # puts the machine's absolute directory layout into the prompt.
        out.append(rel)
    return out


# ---- instruction + variables -------------------------------------------


def instruction_from_preset(root, preset):
    """The preset's instruction text with placeholders still in it ("" if none)."""
    if "instruction" in preset:
        return preset["instruction"]
    if "instruction_file" in preset:
        return read_instruction_file(confined(root, preset["instruction_file"], "instruction_file"), preset["instruction_file"])
    if "prompt" in preset:
        t = preset["prompt"]
        return "\n".join("%s: %s" % (k.upper(), t[k].strip()) for k in PROMPT_FIELDS if t.get(k, "").strip())
    return ""


def read_instruction_file(path, shown=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise FileflowError("E_IO", "cannot read instruction file %s: %s" % (shown or path, exc))


def parse_vars(pairs):
    """``["a=1", "b=x=y"]`` -> ``{"a": "1", "b": "x=y"}``."""
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise FileflowError("E_USAGE", "--var expects name=value, got '%s'" % pair, "e.g. --var domain=backend")
        key, _, value = pair.partition("=")
        key = key.strip()
        if not _PLACEHOLDER.fullmatch("{{%s}}" % key):
            raise FileflowError("E_USAGE", "invalid variable name '%s'" % key, "use letters, digits, '_', '-' or '.'")
        out[key] = value
    return out


def placeholders(text):
    """Names of ``{{placeholders}}`` in ``text``, first-seen order, no duplicates."""
    seen = []
    for m in _PLACEHOLDER.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def render_template(text, variables):
    """Replace ``{{name}}`` once, in a single pass. Returns ``(text, unresolved)``.

    Replacement values are inserted verbatim and never re-scanned, so a value
    that happens to contain ``{{x}}`` cannot trigger further substitution.
    """
    unresolved = []

    def sub(m):
        name = m.group(1)
        if name in variables:
            return str(variables[name])
        if name not in unresolved:
            unresolved.append(name)
        return m.group(0)

    return _PLACEHOLDER.sub(sub, text), unresolved
