"""Change detection for ``/api/watch`` (live reload in server mode).

A *snapshot* is a pair of short digests:

``files``
    Every file that can influence the prompt: all non-ignored files (hidden
    files included, so ``.gitignore`` and ``.files-to-prompt`` edits count),
    identified by relative path, size and nanosecond mtime.
``config``
    The *content* of ``.files-to-prompt``, tracked separately and read even if
    the project's own ``.gitignore`` ignores that file. Lets the web client
    distinguish "files changed, re-render" from "the config changed, reload the
    controls first".

Directories that are never prompt material and can be enormous
(``node_modules``, ``.git``, virtualenvs, caches) are never descended into, and
anything matched by ``.gitignore`` (root and nested, same rules as the prompt
engine) is skipped, so polling cost tracks the size of the *project*, not of
its dependency folders.
"""

import hashlib
import os
from collections import namedtuple

from ..cli import path_is_ignored, read_gitignore
from .config import config_path

# Never descended into, regardless of .gitignore.
PRUNE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

_CONFIG_READ_LIMIT = 1 << 20  # a config file larger than 1 MiB is not a config

Snapshot = namedtuple("Snapshot", ["files", "config"])


def _digest(data=b""):
    return hashlib.blake2b(data, digest_size=10)


def _gitignore_rules(directory):
    """Rules from ``directory/.gitignore``; unreadable files count as empty."""
    try:
        return read_gitignore(directory)
    except (OSError, UnicodeDecodeError):
        return []


def files_digest(root):
    """Digest of every prompt-relevant file under ``root``."""
    h = _digest()
    scopes = [(root, _gitignore_rules(root))]
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath != root:
            nested = _gitignore_rules(dirpath)
            if nested:
                scopes.append((dirpath, nested))
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in PRUNE_DIRS and not path_is_ignored(os.path.join(dirpath, d), True, scopes)
        )
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if path_is_ignored(full, False, scopes):
                continue
            try:
                st = os.stat(full)
            except OSError:  # vanished mid-walk, or a dangling symlink
                continue
            rel = os.path.relpath(full, root)
            h.update(("%s\0%d\0%d\n" % (rel, st.st_size, st.st_mtime_ns)).encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def config_digest(root):
    """Digest of ``.files-to-prompt``'s content, or ``"absent"`` if there is none."""
    try:
        with open(config_path(root), "rb") as fh:
            return _digest(fh.read(_CONFIG_READ_LIMIT)).hexdigest()
    except OSError:
        return "absent"


def snapshot(root):
    """Return the current :class:`Snapshot` of ``root``."""
    return Snapshot(files=files_digest(root), config=config_digest(root))


def digest_of(snap):
    """One short string identifying a snapshot (the ``hash`` field of a frame)."""
    return _digest((snap.files + ":" + snap.config).encode("ascii")).hexdigest()
