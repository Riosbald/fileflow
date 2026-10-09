"""One traversal for everything: prompt, receipt, tree, project counts.

Before this module the prompt engine, ``/api/tree`` and ``/api/project`` each
re-implemented directory walking, and the tree/project views only read the
*root* ``.gitignore`` -- so the live tree listed (and counted) files the prompt
silently omitted. :func:`walk_entries` is now the single source of truth: it
yields every entry it meets together with the **reason** it was excluded (or
``None`` if it is selected), so callers can build a prompt, a tree, a count or a
receipt from the *same* decisions.

Exclusion reasons are a closed vocabulary (:data:`REASONS`).
"""

import functools
import os
import re
from collections import namedtuple
from fnmatch import fnmatch

# Closed vocabulary of reasons an entry can be left out. The receipt (and its
# tests) reject anything not listed here.
HIDDEN = "HIDDEN"
GITIGNORED = "GITIGNORED"
PATTERN = "PATTERN"
SYMLINK_OUTSIDE_ROOT = "SYMLINK_OUTSIDE_ROOT"
SYMLINK_DIR = "SYMLINK_DIR"  # symlinked directory inside the project: not followed
BINARY = "BINARY"
TOO_LARGE = "TOO_LARGE"
UNREADABLE = "UNREADABLE"
SECRET_BLOCKED = "SECRET_BLOCKED"
TOKEN_BUDGET = "TOKEN_BUDGET"
NOT_INCLUDED = "NOT_INCLUDED"  # an [include] list is active and this file matched none of it
NOT_CHANGED = "NOT_CHANGED"    # --diff/--staged/--since is active and this file did not change

REASONS = (
    HIDDEN, GITIGNORED, PATTERN, SYMLINK_OUTSIDE_ROOT, SYMLINK_DIR,
    BINARY, TOO_LARGE, UNREADABLE, SECRET_BLOCKED, TOKEN_BUDGET, NOT_INCLUDED, NOT_CHANGED,
)

# ``reason`` is None when the entry is selected for the prompt.
Entry = namedtuple("Entry", ["path", "is_dir", "reason"])


def _normalize_gitignore_rule(pattern):
    """Parse a single gitignore pattern into a structured rule.

    Supports the common git semantics: leading ``!`` negation, trailing ``/``
    directory-only, and leading ``/`` anchoring to the .gitignore's directory.
    Returns ``None`` for blank/comment lines and for unsupported constructs
    (``**`` and ``[...]`` character classes), which are deliberately skipped
    and documented as a limitation.
    """
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return None
    negated = False
    if pattern.startswith("!"):
        negated = True
        pattern = pattern[1:].lstrip()
        if not pattern:
            return None
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern.rstrip("/")
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern.lstrip("/")
    if "**" in pattern or "[" in pattern or "]" in pattern:
        return None  # unsupported — documented limitation
    return {
        "pattern": pattern,
        "dir_only": dir_only,
        "negated": negated,
        "anchored": anchored,
    }


def _gitignore_rule_matches(relpath, is_dir, rule):
    """Return True if ``relpath`` (relative, '/'-separated) matches ``rule``.

    A non-anchored pattern without a slash matches against the basename; a
    pattern containing a slash (or anchored) matches against the full path.
    Glob matching uses ``fnmatch`` (``*`` and ``?``).
    """
    if rule["dir_only"] and not is_dir:
        return False
    target = relpath
    if not rule["anchored"] and "/" not in rule["pattern"]:
        target = os.path.basename(relpath)
    return fnmatch(target, rule["pattern"])


def _scope_matches(relpath, is_dir, patterns):
    """Apply last-match-wins over ``patterns`` for one gitignore scope."""
    ignored = False
    for pattern in patterns:
        rule = _normalize_gitignore_rule(pattern)
        if rule is None:
            continue
        if _gitignore_rule_matches(relpath, is_dir, rule):
            ignored = not rule["negated"]
    return ignored


def path_is_ignored(path, is_dir, scopes):
    """Return True if ``path`` is ignored by any gitignore ``scope``.

    ``scopes`` is a list of ``(gitignore_dir, patterns)``. Each scope's rules
    apply only to paths under its own directory, using last-match-wins with
    ``!`` negation and leading-``/`` anchoring.
    """
    abs_path = os.path.abspath(path)
    for gdir, patterns in scopes:
        abs_gdir = os.path.abspath(gdir)
        try:
            rel = os.path.relpath(abs_path, abs_gdir)
        except ValueError:
            continue
        if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
            continue
        rel = rel.replace(os.sep, "/")
        if _scope_matches(rel, is_dir, patterns):
            return True
    return False


def should_ignore(path, gitignore_rules):
    """Backward-compatible wrapper: ``path``'s parent is the gitignore scope."""
    return path_is_ignored(
        path, os.path.isdir(path), [(os.path.dirname(path), list(gitignore_rules))]
    )


def read_gitignore(path):
    """Parse the ``.gitignore`` file inside ``path`` into a list of rules.

    Blank lines and comment lines (starting with ``#``) are skipped. Returns
    an empty list if there is no ``.gitignore`` file in ``path``.
    """
    gitignore_path = os.path.join(path, ".gitignore")
    if os.path.isfile(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    return []


def _gitignore_or_empty(directory):
    try:
        return read_gitignore(directory)
    except (OSError, UnicodeDecodeError):
        return []


def stays_inside(base_real, path):
    """True if ``path`` (after resolving symlinks) is within ``base_real``.

    A symlink named ``notes.txt`` that points at ``~/.ssh/id_rsa`` must not be
    pasted into a prompt just because it lives in the project. Non-symlinks
    and symlinks that resolve *inside* the walked directory are fine.
    """
    if not os.path.islink(path):
        return True
    real = os.path.realpath(path)
    try:
        return os.path.commonpath([base_real, real]) == base_real
    except ValueError:  # different drives on Windows
        return False


def _classify(path, name, is_dir, base_real, scopes, include_hidden, patterns):
    """Why ``name`` is excluded, or ``None``. Order: hidden, gitignore, pattern, symlink."""
    if not include_hidden and name.startswith("."):
        return HIDDEN
    if scopes and path_is_ignored(path, is_dir, scopes):
        return GITIGNORED
    if patterns and any(fnmatch(name, p) for p in patterns):
        return PATTERN
    if os.path.islink(path):
        if not stays_inside(base_real, path):
            return SYMLINK_OUTSIDE_ROOT
        if is_dir:
            return SYMLINK_DIR
    return None


# ---- include globs --------------------------------------------------------------------------------
# Deliberately tiny and identical in both engines: `*` = any run of characters (including `/`),
# `?` = exactly one character, everything else literal (no `[...]` classes), case-sensitive.
# fnmatch is NOT used: its character classes and case rules differ from the JS engine's.

@functools.lru_cache(maxsize=256)
def _glob_regex(pattern):
    out = []
    for ch in pattern:
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return re.compile("^" + "".join(out) + "$", re.DOTALL)


def glob_match(pattern, text):
    return _glob_regex(pattern).match(text) is not None


def include_match(rel, name, patterns):
    """True if any pattern matches: a pattern without ``/`` matches the file name, one with ``/`` the relative path."""
    return any(glob_match(p, rel if "/" in p else name) for p in patterns)


def walk_entries(paths, include_hidden=False, ignore_gitignore=False, ignore_patterns=None, include_patterns=None):
    """Yield :class:`Entry` for everything under ``paths``.

    * A path that is a file is yielded once, selected (the user named it).
    * A directory is walked; for every directory level the directory entries
      come first (sorted), then the file entries (sorted). Excluded
      directories are reported once and **not descended into**.
    * Selected files appear in exactly the order the prompt uses.
    * Nested ``.gitignore`` files get their own scope (anchoring and negation
      are relative to the directory that holds them).
    * ``include_patterns`` (when non-empty) narrows *files found by walking a directory* to
      those matching at least one pattern (:func:`include_match`, relative to that directory);
      the rest get reason ``NOT_INCLUDED``. Exclusions still win, directories are always
      walked, and a file named explicitly is never filtered.
    """
    patterns = list(ignore_patterns or [])
    include = list(include_patterns or [])
    scopes = []
    for path in paths:
        if os.path.isfile(path):
            yield Entry(path, False, None)
            continue
        if not os.path.isdir(path):
            continue
        base_real = os.path.realpath(path)
        if not ignore_gitignore:
            scopes.append((os.path.abspath(path), _gitignore_or_empty(path)))
        for root, dirs, files in os.walk(path):
            if not ignore_gitignore and root != path:
                nested = _gitignore_or_empty(root)
                if nested:
                    scopes.append((os.path.abspath(root), nested))
            keep = []
            for d in sorted(dirs):
                full = os.path.join(root, d)
                reason = _classify(full, d, True, base_real, scopes, include_hidden, patterns)
                yield Entry(full, True, reason)
                if reason is None:
                    keep.append(d)
            dirs[:] = keep
            for f in sorted(files):
                full = os.path.join(root, f)
                reason = _classify(full, f, False, base_real, scopes, include_hidden, patterns)
                if reason is None and include:
                    rel = os.path.relpath(full, path).replace(os.sep, "/")
                    if not include_match(rel, f, include):
                        reason = NOT_INCLUDED
                yield Entry(full, False, reason)
