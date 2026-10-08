"""Path-resolution and binding safety for server mode.

Threat model: ``fileflow serve`` exposes part of the local filesystem over
HTTP. Two boundaries keep that exposure bounded:

1. **The served root** (``fileflow serve --root X``). Every request is pinned to
   X. A client may *narrow* the scope with a ``root`` query parameter (a
   sub-directory), but it can never widen it. See :func:`confine_root`.
2. **Paths within a root** (``/api/file?path=...``). Resolved with ``realpath``
   and verified to stay inside the root, which blocks ``..`` traversal and
   in-tree symlinks that point elsewhere. See :func:`safe_join`.

Both checks resolve symlinks *before* comparing, and compare on path-component
boundaries (so ``/srv/proj-evil`` is not "inside" ``/srv/proj``).
"""

import os


class PathOutsideRootError(ValueError):
    """Raised when a requested path escapes the configured project root."""


def resolve_project_root(root):
    """Return the canonical (realpath) of ``root``, refusing non-directories."""
    root = os.path.realpath(os.path.abspath(root or "."))
    if not os.path.isdir(root):
        raise ValueError("Project root is not a directory: %s" % root)
    return root


def is_within(root, path):
    """True if ``path`` is ``root`` itself or lies beneath it.

    Both arguments must already be canonical (``realpath``). The comparison is
    component-wise via :func:`os.path.commonpath`, so a sibling that merely
    shares a name prefix (``/srv/proj-evil`` vs ``/srv/proj``) is correctly
    rejected, and a root of ``/`` behaves sensibly.
    """
    root_n = os.path.normcase(root)
    path_n = os.path.normcase(path)
    try:
        return os.path.commonpath([root_n, path_n]) == root_n
    except ValueError:  # e.g. different drives on Windows
        return False


def safe_join(root, rel_path):
    """Join ``rel_path`` to ``root``, rejecting any traversal outside ``root``.

    ``rel_path`` may be absolute; it is still confined to ``root``. Returns the
    canonical, verified path or raises :class:`PathOutsideRootError`.
    """
    root = os.path.realpath(root)
    if rel_path:
        joined = os.path.realpath(os.path.join(root, rel_path))
    else:
        joined = root
    if not is_within(root, joined):
        raise PathOutsideRootError("Path escapes project root: %s" % rel_path)
    return joined


def confine_root(jail, requested=None):
    """Resolve a client-supplied ``root`` and require it to stay inside ``jail``.

    ``jail`` is the directory the server was started with. ``requested`` is the
    untrusted ``root`` query parameter:

    * empty / ``None`` -> the jail itself;
    * relative -> interpreted relative to the jail (never the process CWD);
    * absolute -> accepted only if, after resolving symlinks, it is the jail or
      one of its descendants.

    Raises :class:`PathOutsideRootError` if the result would leave the jail, or
    :class:`ValueError` for an unusable value (not a directory, embedded NUL).
    The containment check happens *before* any existence check, so the error
    for an outside path is identical whether or not it exists (no oracle for
    probing the rest of the filesystem).
    """
    jail = os.path.realpath(jail)
    if not requested:
        return jail
    candidate = requested if os.path.isabs(requested) else os.path.join(jail, requested)
    resolved = os.path.realpath(candidate)
    if not is_within(jail, resolved):
        raise PathOutsideRootError("root is outside the served project: %s" % requested)
    if not os.path.isdir(resolved):
        raise ValueError("Project root is not a directory: %s" % requested)
    return resolved


def is_allowed_host(host, allow_remote):
    """Enforce the localhost-only binding guarantee.

    Binds to 127.0.0.1 (and ::1) by default; serving on 0.0.0.0 requires the
    explicit ``--allow-remote`` flag because it exposes the local filesystem
    to the network.
    """
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    if host in ("0.0.0.0", "::"):
        return allow_remote
    return allow_remote  # any explicit host is only honoured with --allow-remote
