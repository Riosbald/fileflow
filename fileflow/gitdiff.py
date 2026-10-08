"""`--diff` / `--staged` / `--since`: restrict a prompt to what changed, optionally with the patch.

This runs ``git`` inside the user's repository, so it is written defensively:

* **No shell**, fixed argument lists, ``-C <root>``, no pager, no colour, no prompts.
* Per-invocation code-execution hooks are switched off on the command line (which beats
  repository config): ``core.fsmonitor`` (a configured command that git would run), external diff
  drivers (``--no-ext-diff``) and textconv filters (``--no-textconv``).
* A user-supplied ref (``--since``) is **validated and resolved to a full commit SHA first**; only
  the SHA reaches ``git diff``. A ref like ``--output=/tmp/x`` is therefore refused rather than
  interpreted as an option.

What this cannot do: make git safe inside a repository whose ``.git/config`` an attacker
controls (clean/smudge filters and other config-defined commands can still run). Git's own
``safe.directory`` ownership check still applies. Don't run ``--diff`` in a checkout you don't trust.
"""

import os
import re
import shutil
import subprocess

from .errors import FileflowError

MAX_PATCH_BYTES = 2 * 1024 * 1024
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@^~{}\-]*$")
# Overrides that win over repository config for this one invocation.
_SAFE = ["--no-pager", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "color.ui=never"]
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat", "LC_ALL": "C"}


def _git(root, *args, check=True):
    exe = shutil.which("git")
    if not exe:
        raise FileflowError("E_IO", "git is not installed or not on PATH", "install git, or drop --diff/--staged/--since")
    env = dict(os.environ)
    env.update(_ENV)
    proc = subprocess.run([exe] + _SAFE + ["-C", root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, timeout=60)
    if check and proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FileflowError("E_IO", "git failed: %s" % (msg[-1] if msg else "exit %d" % proc.returncode),
                            "run it from inside a git repository you own")
    return proc


def repo_root(cwd):
    proc = _git(cwd, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        raise FileflowError("E_IO", "not a git repository: %s" % cwd,
                            "--diff, --staged and --since need git history; run from inside a repository or pass files explicitly")
    return os.path.realpath(proc.stdout.decode("utf-8", "replace").strip())


def resolve_ref(root, ref):
    """Validate ``ref`` and return the full commit SHA it names. Never lets ``ref`` act as an option."""
    if not ref or not _REF.match(ref) or ".." in ref or ref.endswith(".lock"):
        raise FileflowError("E_USAGE", "'%s' is not a usable git ref" % ref, "use a branch, tag, SHA or HEAD~N")
    proc = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    sha = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not re.match(r"^[0-9a-f]{40,64}$", sha):
        raise FileflowError("E_USAGE", "git does not know a commit called '%s'" % ref, "check the name with: git log --oneline")
    return sha


def _names(proc):
    return [n.decode("utf-8", "surrogateescape") for n in proc.stdout.split(b"\0") if n]


def has_head(root):
    return _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False).returncode == 0


def changed_files(root, mode="head", since=None):
    """Absolute real paths of files that changed (deleted files excluded; they have nothing to read).

    ``mode``: ``"head"`` = working tree vs HEAD plus untracked-not-ignored files; ``"staged"`` = the index
    vs HEAD; ``"since"`` = working tree vs the commit ``since`` (plus untracked).
    """
    rels = set()
    common = ["--name-only", "-z", "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-filter=d"]
    if mode == "staged":
        if has_head(root):
            rels.update(_names(_git(root, "diff", "--cached", *common, "--")))
        else:  # no commits yet: everything staged is new
            rels.update(_names(_git(root, "ls-files", "-z", "--cached")))
    else:
        if mode == "since":
            rels.update(_names(_git(root, "diff", *common, resolve_ref(root, since), "--")))
        elif has_head(root):
            rels.update(_names(_git(root, "diff", *common, "HEAD", "--")))
        else:
            rels.update(_names(_git(root, "ls-files", "-z", "--cached")))
        rels.update(_names(_git(root, "ls-files", "-z", "--others", "--exclude-standard")))
    out = set()
    for rel in rels:
        full = os.path.realpath(os.path.join(root, rel))
        if os.path.isfile(full):
            out.add(full)
    return out


def patch_text(root, mode="head", since=None, limit=MAX_PATCH_BYTES):
    """The unified diff for the same change set (tracked files only), size-capped. ``""`` if empty."""
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"]
    if mode == "staged":
        args.append("--cached")
        if has_head(root):
            args.append("HEAD")
    elif mode == "since":
        args.append(resolve_ref(root, since))
    elif has_head(root):
        args.append("HEAD")
    proc = _git(root, *(args + ["--"]))
    data = proc.stdout
    text = data[:limit].decode("utf-8", "replace")
    if len(data) > limit:
        text += "\n# ... patch truncated at %d bytes (it was %d)\n" % (limit, len(data))
    return text.replace("\r\n", "\n").replace("\r", "\n")
