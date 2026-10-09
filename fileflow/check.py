"""`fileflow check`: mechanical checks on a project's fileflow config and agent harness.

"AI may accelerate implementation, but verification owns the release decision."
This is the verification step for the repository-resident parts of an agent
harness (instructions, docs links, skills, permissions) and for ``.files-to-prompt``.

Severity policy
---------------
* **error** -- something that definitely breaks: a dangling pointer in
  ``AGENTS.md``/``CLAUDE.md``, a Codex instruction chain over its 32 KiB limit
  (Codex silently stops reading), unparseable settings, a skill the loader will
  reject, a secret sitting in a file that is loaded into every session.
* **warn** -- judgement-shaped guidance: length advice, blanks left in a template,
  permissions that exist only in prose. ``--strict`` promotes warnings to failures.
* **info** -- worth knowing, never fails.

Every threshold cites where it comes from (``docs/research/harness-engineering.md``).
A structural check cannot tell whether instructions are *good*; the report says so.
"""

import json
import os
import re
from collections import namedtuple

from . import presets as presets_mod
from . import secretscan
from .errors import FileflowError
from .provenance import parse_frontmatter  # noqa: F401  (shared front matter reader)
from .walker import walk_entries

Finding = namedtuple("Finding", ["level", "code", "path", "message", "hint"])

# Sources of the numbers below -- keep in sync with docs/research/harness-engineering.md
DEFAULT_MAX_LINES = 100        # OpenAI: AGENTS.md as a ~100-line table of contents (guidance)
CLAUDE_LINE_WARNING = 200      # Claude Code docs: >200 lines "may reduce adherence"
DEFAULT_CODEX_BYTES = 32768    # Codex docs: project_doc_max_bytes default (hard truncation)
SKILL_BODY_LINES = 500         # Anthropic skill authoring guidance
SKILL_NAME = re.compile(r"^[a-z0-9-]{1,64}$")
SKILL_RESERVED = ("anthropic", "claude")
IMPORT_MAX_DEPTH = 4           # Claude Code docs: imports nest at most four hops

FILL_MARKER = "<<FILL:"
_FENCE = re.compile(r"^(\s*)(```|~~~)")
_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_IMPORT = re.compile(r"(?<![\w`@/])@([A-Za-z0-9_.~/\\\-][^\s)`\]>,;]*)")
_HEADING = re.compile(r"^##\s+(\d)\.\s+(.+?)\s*$", re.M)
_XML_TAG = re.compile(r"<[A-Za-z/][^>]*>")

PRUNE = [".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"]

SPEC_NAMES = {1: "Instructions", 2: "Context", 3: "Skills", 4: "Memory",
              5: "Permissions", 6: "Tools", 7: "Checks", 8: "The loop"}
SPEC_CRITICAL = (5, 7, 8)  # permissions, checks, loop: what under-specified prompts lack


class Report(object):
    def __init__(self):
        self.findings = []
        self.verified = []
        self.assumed = []
        self.unknown = []
        self.stats = {}

    def add(self, level, code, path, message, hint=None):
        self.findings.append(Finding(level, code, path, message, hint))

    def count(self, level):
        return sum(1 for f in self.findings if f.level == level)

    def ok(self, strict=False):
        return self.count("error") == 0 and not (strict and self.count("warn"))

    def to_dict(self, strict=False):
        return {
            "ok": self.ok(strict),
            "errors": self.count("error"),
            "warnings": self.count("warn"),
            "findings": [f._asdict() for f in self.findings],
            "ledger": {"verified": self.verified, "assumed": self.assumed, "unknown": self.unknown},
            "stats": self.stats,
        }


# --------------------------------------------------------------------------
# markdown helpers
# --------------------------------------------------------------------------
def mask_code(text):
    """Blank out fenced blocks and inline code spans, keeping line numbers intact."""
    out, in_fence, fence = [], False, None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if m and (not in_fence or m.group(2) == fence):
            in_fence = not in_fence
            fence = m.group(2) if in_fence else None
            out.append("")
            continue
        out.append("" if in_fence else re.sub(r"`[^`\n]*`", lambda mm: " " * len(mm.group(0)), line))
    return "\n".join(out)


def links_in(text):
    """``[(line_no, target)]`` for relative Markdown links outside code."""
    found = []
    for n, line in enumerate(mask_code(text).split("\n"), start=1):
        for m in _LINK.finditer(line):
            target = m.group(1).strip()
            low = target.lower()
            if not target or low.startswith(("http:", "https:", "mailto:", "#", "tel:")) or "://" in low:
                continue
            found.append((n, target.split("#", 1)[0].split("?", 1)[0]))
    return [(n, t) for n, t in found if t]


def imports_in(text):
    """``[(line_no, path)]`` for Claude-style ``@path`` imports outside code spans."""
    found = []
    for n, line in enumerate(mask_code(text).split("\n"), start=1):
        for m in _IMPORT.finditer(line):
            p = m.group(1).rstrip(".:;,!?")
            if "/" in p or re.search(r"\.\w{1,6}$", p):
                found.append((n, p.replace("\\ ", " ")))
    return found


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _within(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _files(root):
    """Relative POSIX paths of every selected file (hidden included, gitignore respected)."""
    out = []
    for e in walk_entries([root], include_hidden=True, ignore_gitignore=False, ignore_patterns=PRUNE):
        if not e.is_dir and e.reason is None:
            out.append(os.path.relpath(e.path, root).replace(os.sep, "/"))
    return sorted(out)


def check_config(root, report):
    """.files-to-prompt: parse, schema, presets, paths, budget fit."""
    from .server.config import ConfigError, config_path, read_config

    if not os.path.isfile(config_path(root)):
        report.verified.append("no .files-to-prompt (nothing to validate there)")
        return {}
    gi = os.path.join(root, ".gitignore")
    if os.path.isfile(gi):
        from .walker import path_is_ignored, read_gitignore

        try:
            ignored = path_is_ignored(config_path(root), False, [(root, read_gitignore(root))])
        except (OSError, UnicodeDecodeError):
            ignored = False
        if ignored:
            report.add("warn", "C_CONFIG_IGNORED", ".files-to-prompt",
                       "this file is ignored by .gitignore, so your presets and settings are never shared or versioned",
                       "remove the .files-to-prompt line from .gitignore and commit the file")
    try:
        cfg = read_config(root, strict=True)
    except ConfigError as exc:
        code = "C_CONFIG_PARSE" if "not valid TOML" in str(exc) else "C_CONFIG_SCHEMA"
        report.add("error", code, ".files-to-prompt", str(exc), "fix the file, then re-run `fileflow check`")
        return {}
    # Report EVERY schema problem, not just the first one found.
    table, failed = {}, False
    for step in (lambda: presets_mod.secrets_mode(cfg), lambda: presets_mod.validate_settings(cfg)):
        try:
            step()
        except FileflowError as exc:
            report.add("error", "C_CONFIG_SCHEMA", ".files-to-prompt", exc.message, exc.hint)
            failed = True
    try:
        table = presets_mod.all_presets(cfg)
    except FileflowError as exc:
        report.add("error", "C_CONFIG_SCHEMA", ".files-to-prompt", exc.message, exc.hint)
        failed = True
    if failed:
        return cfg
    report.verified.append(".files-to-prompt parsed; %d preset(s) type-checked" % len(table))
    for u in presets_mod.unknown_keys(cfg):
        report.add("warn", "C_CONFIG_UNKNOWN_KEY", u["where"],
                   "'%s' is not a setting fileflow reads%s" % (u["key"], " (did you mean '%s'?)" % u["suggestion"] if u["suggestion"] else ""),
                   "unknown keys are ignored on purpose (so newer configs still load), which means a typo silently does nothing")
    if "ask" in cfg:
        try:
            presets_mod.ask_section(cfg)
        except FileflowError as exc:
            report.add("error", "C_CONFIG_SCHEMA", ".files-to-prompt", exc.message, exc.hint)
        ask = cfg["ask"] if isinstance(cfg["ask"], dict) else {}
        for key in ("base_url", "api_key_env"):
            if key in ask:
                report.add("warn", "C_ASK_IGNORED_KEY", "[ask]",
                           "%s is not read from .files-to-prompt (a cloned repo must not be able to redirect your API key)" % key,
                           "use --base-url / $OPENAI_BASE_URL / $ANTHROPIC_BASE_URL, and --api-key-env")
        if "api_key" in ask:
            report.add("error", "C_ASK_KEY_IN_CONFIG", "[ask]", "an API key must never be stored in .files-to-prompt (it is committed and shared)",
                       "remove it, rotate the key, and export it as an environment variable instead")

    from .cli import collect, count_tokens, config_to_options

    base = config_to_options(cfg)
    for name in sorted(table):
        p = table[name]
        where = "presets.%s" % name
        paths = []
        try:
            for rel in p.get("paths", []):
                real = presets_mod.confined(root, rel, "preset path")
                if not os.path.exists(real):
                    report.add("error", "C_PRESET_PATH", where, "path '%s' does not exist" % rel,
                               "create it or edit paths in [%s]" % where)
                else:
                    paths.append(os.path.join(root, rel))
        except FileflowError as exc:
            report.add("error", "C_PRESET_PATH", where, exc.message, exc.hint)
            continue
        text = ""
        try:
            text = presets_mod.instruction_from_preset(root, p)
        except FileflowError as exc:
            report.add("error", "C_PRESET_INSTRUCTION", where, exc.message, exc.hint)
        needed = [n for n in presets_mod.placeholders(text)
                  if n not in {str(k) for k in p.get("vars", {})}]
        if needed:
            report.add("info", "C_PLACEHOLDERS", where,
                       "needs --var for: " + ", ".join(needed))
        budget = p.get("max_tokens", base.get("max_tokens", 0))
        if budget and paths:
            try:
                col = collect(
                    paths,
                    include_hidden=p.get("include_hidden", base.get("include_hidden", False)),
                    ignore_gitignore=p.get("ignore_gitignore", base.get("ignore_gitignore", False)),
                    ignore_patterns=base.get("ignore_patterns", []) + p.get("exclude_patterns", []),
                    include_patterns=p.get("include_patterns", base.get("include_patterns", [])),
                    secrets="off", quiet=True)
                total = sum(count_tokens(c) for _, c in col.documents)
                if total > budget:
                    report.add("warn", "C_BUDGET_TRUNCATES", where,
                               "about %s tokens (estimate) of content vs a %s budget: the tail will be cut"
                               % (format(total, ","), format(budget, ",")),
                               "raise max_tokens, or narrow paths / add exclude_patterns")
            except Exception as exc:  # a check must never crash the run
                report.add("info", "C_BUDGET_UNKNOWN", where, "could not estimate size: %s" % exc)
    return cfg


def check_project_hygiene(root, cfg, report):
    """Out-of-project symlinks and secrets in what a default run would emit."""
    from .cli import collect, config_to_options

    try:
        opts = config_to_options(cfg) if cfg else {}
    except FileflowError:
        opts = {}  # a schema error is already reported by check_config; still scan with defaults
    col = collect(
        [root],
        include_hidden=opts.get("include_hidden", False),
        ignore_gitignore=opts.get("ignore_gitignore", False),
        ignore_patterns=opts.get("ignore_patterns", []) + PRUNE,
        secrets="warn", quiet=True)
    for e in col.excluded:
        if e.reason == "SYMLINK_OUTSIDE_ROOT":
            rel = os.path.relpath(e.path, root).replace(os.sep, "/")
            report.add("warn", "C_SYMLINK_OUTSIDE", rel, "symlink leaves the project; it is never included in a prompt")
    for path, found in sorted(col.secret_findings.items()):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        rules = ", ".join(sorted({f.rule for f in found}))
        lines = ", ".join(str(n) for n in sorted({f.line for f in found})[:5])
        report.add("warn", "C_SECRET", rel, "possible secret (%s) at line %s -- value not shown" % (rules, lines),
                   "remove it, or use --secrets exclude/block when generating prompts")
    report.verified.append("%d file(s) scanned for secrets and out-of-project symlinks" % len(col.documents))
    report.assumed.append("secret scan is pattern-based: finding nothing is not proof that nothing is there")


def _harness_present(files):
    return any(f in ("AGENTS.md", "CLAUDE.md", "docs/HARNESS.md", "HARNESS.md") or f.startswith(".claude/")
               or f.endswith("/AGENTS.md") or f.endswith("/CLAUDE.md") for f in files)


def check_harness(root, cfg, files, report):
    hcfg = cfg.get("harness", {}) if isinstance(cfg.get("harness", {}), dict) else {}
    max_lines = hcfg.get("max_lines", DEFAULT_MAX_LINES)
    codex_bytes = hcfg.get("codex_max_bytes", DEFAULT_CODEX_BYTES)
    if not (isinstance(max_lines, int) and not isinstance(max_lines, bool) and max_lines > 0):
        report.add("error", "C_CONFIG_SCHEMA", ".files-to-prompt", "[harness] max_lines must be a positive integer")
        max_lines = DEFAULT_MAX_LINES
    if not (isinstance(codex_bytes, int) and not isinstance(codex_bytes, bool) and codex_bytes > 0):
        report.add("error", "C_CONFIG_SCHEMA", ".files-to-prompt", "[harness] codex_max_bytes must be a positive integer")
        codex_bytes = DEFAULT_CODEX_BYTES

    text_of = {f: _read(os.path.join(root, *f.split("/"))) for f in files
               if f.endswith(".md") or f.endswith("/SKILL.md")}
    agents = [f for f in files if os.path.basename(f) in ("AGENTS.md", "AGENTS.override.md")]
    claudes = [f for f in files if os.path.basename(f) == "CLAUDE.md"]
    instruction_files = agents + claudes + [f for f in files if f in ("docs/HARNESS.md", "HARNESS.md")]

    # --- H1: size -------------------------------------------------------------
    for f in agents + claudes:
        t = text_of.get(f)
        if t is None:
            continue
        n = len(t.splitlines())
        if n > CLAUDE_LINE_WARNING:
            report.add("warn", "H_VERY_LONG", f,
                       "%d lines: over Claude Code's documented 200-line warning threshold" % n,
                       "move detail into docs/ and link to it (a map, not a manual)")
        elif n > max_lines:
            report.add("warn", "H_LONG", f,
                       "%d lines: over the %d-line map guideline (OpenAI: ~100 lines, pointers to docs/)" % (n, max_lines),
                       "move detail into docs/ and link to it")
    report.verified.append("%d instruction file(s) measured" % len(agents + claudes))

    # --- H2: Codex chain (hard truncation) ---------------------------------------
    by_dir = {}
    for f in agents:
        d = os.path.dirname(f)
        cur = by_dir.get(d)
        if cur is None or os.path.basename(f) == "AGENTS.override.md":
            by_dir[d] = f  # Codex reads one file per directory; the override wins
    for d, f in sorted(by_dir.items()):
        chain, parts = 0, []
        cur = ""
        for seg in ([""] + ([p for p in d.split("/") if p] if d else [])):
            cur = os.path.join(cur, seg) if seg else ""
            member = by_dir.get(cur.replace(os.sep, "/"))
            if member:
                size = os.path.getsize(os.path.join(root, *member.split("/")))
                chain += size
                parts.append(member)
        if chain >= codex_bytes:
            report.add("error", "H_CODEX_BYTES", f,
                       "instruction chain %s is %s bytes; Codex stops reading at %s bytes, so the end is silently dropped"
                       % (" + ".join(parts), format(chain, ","), format(codex_bytes, ",")),
                       "shorten these files or split instructions across nested directories")

    # --- H3: links and imports -------------------------------------------------------
    checked = 0
    kb = sorted(set(instruction_files) | {f for f in files if f.startswith("docs/") and f.endswith(".md")})
    for f in kb:
        t = text_of.get(f)
        if t is None:
            continue
        base = os.path.dirname(os.path.join(root, *f.split("/")))
        critical = f in instruction_files
        for line, target in links_in(t):
            checked += 1
            full = os.path.realpath(os.path.join(base, target))
            if not _within(os.path.realpath(root), full):
                report.add("warn", "H_LINK_OUTSIDE", "%s:%d" % (f, line), "link '%s' leaves the repository (an agent cannot see it)" % target)
            elif not os.path.exists(full):
                report.add("error" if critical else "warn", "H_LINK_DANGLING", "%s:%d" % (f, line),
                           "link '%s' points at nothing" % target,
                           "fix the path or remove the link: a map that points nowhere sends the agent wandering")
    report.stats["links_checked"] = checked
    if checked:
        report.verified.append("%d relative link(s) resolved in %d file(s)" % (checked, len(kb)))

    def walk_imports(f, depth, stack):
        t = text_of.get(f)
        if t is None:
            return
        base = os.path.dirname(os.path.join(root, *f.split("/")))
        for line, p in imports_in(t):
            full = os.path.realpath(os.path.expanduser(p) if p.startswith("~") else os.path.join(base, p))
            if not _within(os.path.realpath(root), full):
                continue  # outside the repo: can't judge
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not os.path.exists(full):
                report.add("warn", "H_IMPORT_MISSING", "%s:%d" % (f, line), "@%s does not exist" % p,
                           "wrap it in backticks if you meant to mention the path without importing it")
                continue
            if rel in stack:
                report.add("warn", "H_IMPORT_CYCLE", "%s:%d" % (f, line), "@%s imports back into %s" % (p, rel))
                continue
            if depth + 1 > IMPORT_MAX_DEPTH:
                report.add("warn", "H_IMPORT_DEPTH", "%s:%d" % (f, line),
                           "@%s is more than %d hops deep; Claude Code stops there" % (p, IMPORT_MAX_DEPTH))
                continue
            if rel not in text_of:
                text_of[rel] = _read(full)
            walk_imports(rel, depth + 1, stack + [rel])

    for f in claudes:
        walk_imports(f, 0, [f])

    # --- H4: CLAUDE.md vs AGENTS.md drift ---------------------------------------------
    if "AGENTS.md" in files and "CLAUDE.md" in files:
        c = text_of.get("CLAUDE.md") or ""
        a = text_of.get("AGENTS.md") or ""
        imports_agents = any(os.path.basename(p) == "AGENTS.md" for _, p in imports_in(c))
        if not imports_agents:
            same = " ".join(c.split()) == " ".join(a.split())
            report.add("warn", "H_DRIFT", "CLAUDE.md",
                       "AGENTS.md and CLAUDE.md are %s: two sources of truth will drift"
                       % ("identical copies" if same else "different files, and CLAUDE.md does not import AGENTS.md"),
                       "make CLAUDE.md start with `@AGENTS.md` (Claude Code does not read AGENTS.md natively)")

    # --- H5: unfilled template blanks + the Harness Spec ---------------------------------
    for f in agents:
        n = (text_of.get(f) or "").count(FILL_MARKER)
        if n:
            report.add("warn", "H_UNFILLED", f, "%d blank(s) still marked FILL" % n, "fill them or delete the lines")
    spec = next((f for f in ("docs/HARNESS.md", "HARNESS.md") if f in files), None)
    if spec:
        t = text_of.get(spec) or ""
        heads = {int(m.group(1)): m.start() for m in _HEADING.finditer(t)}
        order = sorted(heads.items(), key=lambda kv: kv[1])
        missing = [n for n in SPEC_NAMES if n not in heads]
        if missing:
            report.add("warn", "H_SPEC_MISSING_SECTION", spec,
                       "missing section(s): " + ", ".join("%d. %s" % (n, SPEC_NAMES[n]) for n in missing),
                       "the 8 components are: " + ", ".join(SPEC_NAMES[n] for n in sorted(SPEC_NAMES)))
        sections = {}
        for i, (n, start) in enumerate(order):
            end = order[i + 1][1] if i + 1 < len(order) else len(t)
            sections[n] = t[start:end]
        unfilled = {n: s.count(FILL_MARKER) for n, s in sections.items() if FILL_MARKER in s}
        crit = [n for n in SPEC_CRITICAL if n in unfilled]
        if crit:
            report.add("warn", "H_SPEC_CRITICAL_UNFILLED", spec,
                       "still blank: " + ", ".join("%d. %s" % (n, SPEC_NAMES[n]) for n in crit)
                       + " -- permissions, checks and the stop condition are what under-specified prompts lack",
                       "write explicit clauses (never / ask first / enforced by; commands that must pass; done / stop-and-ask / max attempts)")
        rest = sum(c for n, c in unfilled.items() if n not in crit)
        if rest:
            report.add("warn", "H_SPEC_UNFILLED", spec, "%d other blank(s) still marked FILL" % rest)
        report.verified.append("Harness Spec sections present: %d of 8" % (8 - len(missing)))
    else:
        report.add("info", "H_NO_SPEC", None, "no docs/HARNESS.md (the 8-component spec)",
                   "scaffold one with: fileflow init --template harness")

    # --- H6: permissions: enforced vs advisory ----------------------------------------------
    settings_path = os.path.join(root, ".claude", "settings.json")
    deny = []
    if ".claude/settings.json" in files:
        raw = _read(settings_path)
        try:
            data = json.loads(raw) if raw is not None else None
        except ValueError as exc:
            data = None
            report.add("error", "H_SETTINGS_INVALID", ".claude/settings.json", "not valid JSON: %s" % exc)
        if data is not None:
            perms = data.get("permissions", {}) if isinstance(data, dict) else None
            if not isinstance(perms, dict):
                if perms is not None or not isinstance(data, dict):
                    report.add("error", "H_SETTINGS_INVALID", ".claude/settings.json", "`permissions` must be an object")
                perms = {}
            bad = False
            for kind in ("allow", "ask", "deny"):
                rules = perms.get(kind, [])
                if not (isinstance(rules, list) and all(isinstance(r, str) for r in rules)):
                    report.add("error", "H_SETTINGS_INVALID", ".claude/settings.json",
                               "permissions.%s must be a list of strings" % kind)
                    bad = True
                    continue
                for r in rules:
                    if re.match(r"^(Bash|PowerShell)\(command:", r) or re.match(r"^(Read|Edit|Write)\(file_path:", r):
                        report.add("warn", "H_SETTINGS_RULE_IGNORED", ".claude/settings.json",
                                   "rule '%s' is ignored by Claude Code (bypassable by compound commands)" % r,
                                   "use the specifier form: Bash(rm *), Read(./path)")
                if kind == "deny" and not bad:
                    deny = rules
            if perms.get("defaultMode") == "bypassPermissions":
                report.add("warn", "H_BYPASS_DEFAULT", ".claude/settings.json",
                           "defaultMode is bypassPermissions: no prompts. The docs advise this only inside containers or VMs",
                           "use it only in an isolated environment, and say so in docs/HARNESS.md")
            report.verified.append(".claude/settings.json parsed; %d deny rule(s)" % len(deny))
    if spec and not deny and not any(f.startswith(".devcontainer/") or f == "Dockerfile" for f in files):
        report.add("warn", "H_PERMS_PROSE_ONLY", spec,
                   "permissions are described in prose only: no deny rules and no container config found",
                   "prose is advisory (Claude Code docs: rules are enforced by the tool, not the model). "
                   "Add permissions.deny rules or run the agent in a container/VM")
    report.assumed.append("a container/VM config file existing does not prove the agent runs inside it")

    # --- H7: skills ---------------------------------------------------------------------------
    skills = [f for f in files if re.match(r"^\.claude/skills/[^/]+/SKILL\.md$", f)]
    for f in skills:
        t = text_of.get(f)
        if t is None:
            report.add("error", "H_SKILL_INVALID", f, "unreadable (not UTF-8 text)")
            continue
        fm, body = parse_frontmatter(t)
        if fm is None:
            report.add("error", "H_SKILL_INVALID", f, "missing YAML frontmatter (--- ... ---): the skill will not load")
            continue
        name = fm.get("name", "")
        desc = fm.get("description", "")
        if not name:
            report.add("warn", "H_SKILL_NAME", f, "no `name` (the Agent Skills spec requires it; Claude Code falls back to the directory name)")
        elif not SKILL_NAME.match(name) or any(w in name for w in SKILL_RESERVED) or _XML_TAG.search(name):
            report.add("error", "H_SKILL_INVALID", f,
                       "name '%s' must be <=64 chars of lowercase letters, digits and hyphens, with no 'claude'/'anthropic'" % name)
        if not desc.strip():
            report.add("error", "H_SKILL_INVALID", f, "`description` is empty: the skill is never selected without it")
        elif len(desc) > 1024 or _XML_TAG.search(desc):
            report.add("error", "H_SKILL_INVALID", f, "`description` must be <=1024 characters with no XML tags")
        if len(body.splitlines()) > SKILL_BODY_LINES:
            report.add("warn", "H_SKILL_LONG", f, "%d lines: Anthropic advises keeping SKILL.md under %d" % (len(body.splitlines()), SKILL_BODY_LINES),
                       "split detail into files the skill links to")
    if skills:
        report.verified.append("%d skill(s) validated against the documented frontmatter rules" % len(skills))

    # --- H8: secrets in files loaded into every session ----------------------------------------
    for f in instruction_files + skills:
        t = text_of.get(f)
        if t:
            found = secretscan.scan(t)
            if found:
                report.add("error", "H_SECRET_IN_INSTRUCTIONS", f,
                           "possible secret (%s) in a file that is loaded into agent context -- value not shown"
                           % ", ".join(sorted({x.rule for x in found})))
    report.assumed.append("size/link/format checks cannot tell whether the instructions are good")
    report.unknown.append("what your agent actually loads: depends on its version, settings and flags")
    report.unknown.append("whether the agent follows the instructions")


def run_checks(root, strict=False):
    root = os.path.realpath(root)
    report = Report()
    cfg = check_config(root, report)
    files = _files(root)
    report.stats["files"] = len(files)
    check_project_hygiene(root, cfg, report)
    if _harness_present(files):
        check_harness(root, cfg, files, report)
    else:
        report.verified.append("no agent-harness files found (AGENTS.md / CLAUDE.md / docs/HARNESS.md / .claude); harness checks skipped")
    return report


def format_report(report, strict=False):
    lines = []
    status = "ok" if report.ok(strict) else "FAILED"
    lines.append("fileflow check: %s (%d error(s), %d warning(s))" % (status, report.count("error"), report.count("warn")))
    order = {"error": 0, "warn": 1, "info": 2}
    for f in sorted(report.findings, key=lambda x: (order[x.level], x.path or "", x.code)):
        tag = {"error": "ERROR", "warn": "WARN ", "info": "info "}[f.level]
        where = ("%s: " % f.path) if f.path else ""
        lines.append("  %s %-24s %s%s" % (tag, f.code, where, f.message))
        if f.hint and f.level != "info":
            lines.append("        next: %s" % f.hint)
    for title, items in (("verified", report.verified), ("assumed", report.assumed), ("unknown", report.unknown)):
        for item in dict.fromkeys(items):
            lines.append("  %-9s %s" % (title, item))
    return "\n".join(lines)
