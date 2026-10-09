"""Context Receipt: an evidence ledger for one bundle.

After a bundle is built you can see, in a few seconds, what went in, what did
not and why, and what is verified versus merely assumed.

Guarantees (each has a test):

* **Complete.** Every file the walker met appears exactly once across
  ``included``, ``truncated`` and ``excluded`` (excluded *directories* appear
  once, as ``dir/``, and are not descended into).
* **Closed vocabulary.** Every ``reason`` is in :data:`fileflow.walker.REASONS`.
* **Deterministic.** Sorted, no timestamps, no absolute paths -- the receipt can
  be committed and diffed.
* **Honest.** The ledger separates ``verified`` from ``assumed`` and ``unknown``.
  Token counts are estimates unless a real tokenizer was named.

fileflow never calls a model, so what the model *does* with the bundle is always
listed under ``unknown``.
"""

import hashlib
import os
import shlex

from . import __version__
from .walker import REASONS, TOKEN_BUDGET

RECEIPT_VERSION = 1


def _shown(path, root):
    """Project-relative POSIX path; the path as given if it is outside the project."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path.replace(os.sep, "/")
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        return os.path.abspath(path).replace(os.sep, "/")
    return rel.replace(os.sep, "/")


def _sha12(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_receipt(
    collection,
    budget,
    root,
    count_tokens,
    tokenizer="heuristic",
    preset=None,
    max_tokens=0,
    secrets_mode="off",
    instruction=None,
    unresolved=(),
    config_loaded=False,
    paths_verified=False,
    provenance=None,
    changes=None,
):
    """Build the receipt dict.

    ``collection`` is a :class:`fileflow.cli.Collection`, ``budget`` the
    :class:`fileflow.cli.BudgetResult` applied to ``collection.documents`` and
    ``count_tokens(text) -> int`` the counter used for the estimates.
    """
    provenance = provenance if provenance is not None else getattr(collection, "provenance", {})
    kept_whole = {p: c for p, c in budget.documents}
    truncated_paths = {p for p, _, _ in budget.truncated}

    included, truncated, excluded = [], [], []
    for path, content in collection.documents:
        shown = _shown(path, root)
        if path in truncated_paths:
            kept, original = next((k, o) for p, k, o in budget.truncated if p == path)
            entry = {"path": shown, "reason": TOKEN_BUDGET, "kept_tokens_est": kept, "original_tokens_est": original}
            if provenance.get(path):
                entry["provenance"] = provenance[path]["provenance"]
            truncated.append(entry)
        elif path in kept_whole:
            entry = {
                "path": shown,
                "bytes": len(content.encode("utf-8")),
                "tokens_est": count_tokens(content),
                "sha256_12": _sha12(content),
            }
            label = provenance.get(path)
            if label:
                entry["provenance"] = label["provenance"]
                if label.get("source_url"):
                    entry["source_url"] = label["source_url"]
            included.append(entry)
        else:  # dropped because an earlier file used the budget up
            excluded.append({"path": shown, "reason": TOKEN_BUDGET})

    for entry in collection.excluded:
        label = _shown(entry.path, root) + ("/" if entry.is_dir else "")
        excluded.append({"path": label, "reason": entry.reason})

    for item in included + truncated + excluded:
        reason = item.get("reason")
        assert reason is None or reason in REASONS, "unknown reason code: %r" % reason

    included.sort(key=lambda i: i["path"])
    truncated.sort(key=lambda i: i["path"])
    excluded.sort(key=lambda i: (i["path"], i["reason"]))

    # What the model will actually see: whole files + the truncated heads.
    sent = sum(count_tokens(c) for _, c in budget.documents)
    candidates = len(included) + len(truncated) + sum(1 for e in excluded if not e["path"].endswith("/"))

    verified, assumed, unknown, failed = [], [], [], []
    if config_loaded:
        verified.append(".files-to-prompt parsed and validated")
    if paths_verified:
        verified.append("all preset paths exist and are inside the project")
    read_ok = len(collection.documents)
    unreadable = [e for e in excluded if e["reason"] == "UNREADABLE"]
    verified.append("%d of %d candidate files read" % (read_ok, read_ok + len(unreadable) + sum(
        1 for e in excluded if e["reason"] in ("BINARY", "TOO_LARGE"))))
    if instruction is not None:
        if unresolved:
            failed.append("unresolved placeholders left in the instruction: " + ", ".join(unresolved))
        else:
            verified.append("no unresolved placeholders in the instruction")

    if tokenizer == "heuristic":
        assumed.append("token counts are a ~4 characters/token heuristic, not a real tokenizer (it can be well off, especially for code and non-English text)")
    if secrets_mode != "off":
        assumed.append("secret scan is pattern-based: finding nothing is not proof that nothing is there")
    unknown.append("the target model's real context window")
    unknown.append("whether the model will follow the instruction")

    counts = {"observed": 0, "generated": 0, "user": 0, "unlabelled": 0}
    for path, _ in budget.documents:
        label = provenance.get(path)
        counts[label["provenance"] if label else "unlabelled"] += 1
    if any(counts[k] for k in ("observed", "generated", "user")):
        assumed.append("provenance labels record origin, not truth: 'observed' means fetched from that source, not that it is correct")
    for path, found in sorted(collection.secret_findings.items()):
        rules = ", ".join(sorted({f.rule for f in found}))
        failed.append("possible secret in %s (%s) at line %s" % (
            _shown(path, root), rules, ", ".join(str(n) for n in sorted({f.line for f in found})[:5])))
    for e in unreadable:
        failed.append("could not read %s" % e["path"])

    if changes:
        verified.append("selection limited to changed files (%s%s)" % (
            changes["mode"], ", base " + changes["base"] if changes.get("base") else ""))
    out = {
        "receipt_version": RECEIPT_VERSION,
        "fileflow": __version__,
        "preset": preset,
        "root": ".",
        "instruction": {"present": instruction is not None, "chars": len(instruction or "")},
        "included": included,
        "truncated": truncated,
        "excluded": excluded,
        "ledger": {"verified": verified, "assumed": assumed, "unknown": unknown, "failed_checks": failed},
        "totals": {
            "candidates": candidates,
            "files_sent": len(included) + len(truncated),
            "bytes": sum(i["bytes"] for i in included),
            "tokens_est": sent,
            "tokenizer": tokenizer,
            "budget": max_tokens or 0,
            "provenance": counts,
        },
    }
    if changes:
        out["changes"] = changes
    return out


# Plain words for the closed reason vocabulary. One place, used by the terminal line, the
# web panel (served in /api/prompt) and tests. A new reason code without words fails a test.
REASON_WORDS = {
    "HIDDEN": "hidden (name starts with a dot)",
    "GITIGNORED": "ignored by .gitignore",
    "PATTERN": "matched an ignore pattern",
    "SYMLINK_OUTSIDE_ROOT": "symlink that points outside the project",
    "SYMLINK_DIR": "symlinked folder (not followed)",
    "BINARY": "not text (binary, or not UTF-8)",
    "TOO_LARGE": "over the file size limit",
    "UNREADABLE": "could not be read",
    "SECRET_BLOCKED": "held back: possible secret",
    "TOKEN_BUDGET": "over the token budget",
    "NOT_INCLUDED": "not matched by --include",
    "NOT_CHANGED": "unchanged (diff mode)",
}

# A single file this large AND this dominant is almost always noise (a lockfile, a log, a
# data dump), costs real money and drowns the files that matter. Rare on purpose: a
# warning that fires every run gets ignored.
DOMINANT_MIN_TOKENS = 5000
DOMINANT_SHARE = 0.5


def dominant_file(items):
    """``items`` is ``[(path, tokens)]``. Return ``(path, tokens, total)`` or ``None``."""
    items = [(p, t) for p, t in items if isinstance(t, int)]
    total = sum(t for _, t in items)
    if not items or total < DOMINANT_MIN_TOKENS:
        return None
    path, tokens = max(items, key=lambda i: (i[1], i[0]))
    if tokens >= DOMINANT_MIN_TOKENS and tokens / total >= DOMINANT_SHARE:
        return path, tokens, total
    return None


def _glob_escape(name):
    """Make a file name safe as a pattern: `[id].tsx` must match itself, not a character class."""
    return "".join("[%s]" % c if c in "*?[" else c for c in name)


def dominant_info(items):
    """Structured form of the dominant-file finding (the web client builds its own wording).

    ``items`` is ``[(path, tokens)]``. Returns ``None`` or a dict with the file's path, name,
    the ``--ignore-patterns`` value that removes it (names only, glob-escaped), its tokens, the
    total, a rounded percent, and ``shared``: OTHER files with the same name that would go too.
    """
    hit = dominant_file(items)
    if not hit:
        return None
    path, tokens, total = hit
    name = path.rsplit("/", 1)[-1]
    shared = sum(1 for p, _ in items if p != path and p.rsplit("/", 1)[-1] == name)
    return {"path": path, "name": name, "pattern": _glob_escape(name), "tokens": tokens, "total": total,
            "percent": round(100 * tokens / total), "shared": shared}


def dominant_advice(info, tokenizer="heuristic"):
    """CLI wording. ``--ignore-patterns`` compares NAMES, never paths, so the advice is the name."""
    est = " (estimate)" if tokenizer == "heuristic" else ""
    text = (
        "%s is %d%% of this prompt: about %s of %s tokens%s. If you do not want the model to read it, "
        "leave it out: --ignore-patterns %s"
        % (info["path"], info["percent"], format(info["tokens"], ","), format(info["total"], ","), est,
           shlex.quote(info["pattern"]))
    )
    if info["shared"]:
        text += " (this matches by name, so %d other file%s called %s would also be left out)" % (
            info["shared"], "" if info["shared"] == 1 else "s", info["name"])
    return text


def advisories(receipt):
    """Short, rare, actionable warnings derived from a receipt (empty when nothing stands out)."""
    info = dominant_info([(i["path"], i["tokens_est"]) for i in receipt["included"]])
    return [dominant_advice(info, receipt["totals"]["tokenizer"])] if info else []


def empty_notice(receipt, change_mode=None):
    """When nothing at all will be sent, say so (and why). A wrong folder or an over-eager ignore rule
    otherwise produces a blank prompt that gets pasted into a model."""
    if receipt["totals"]["files_sent"]:
        return None
    if change_mode:
        return "nothing to send: no changed files were found for the selected diff mode"
    counts = {}
    for e in receipt["excluded"]:
        counts[e["reason"]] = counts.get(e["reason"], 0) + 1
    if not counts:
        return "nothing to send: no files were found here. Are you in the right folder?"
    parts = ", ".join("%d %s" % (counts[k], REASON_WORDS[k].split(" (")[0]) for k in sorted(counts))
    tips = []
    if "HIDDEN" in counts:
        tips.append("--include-hidden")
    if "GITIGNORED" in counts:
        tips.append("--ignore-gitignore")
    return "nothing to send: every file was left out (%s)%s" % (
        parts, ". Try " + " or ".join(tips) + ", or check you are in the right folder" if tips else ". Check you are in the right folder")


def one_line(receipt):
    """What a person at a terminal needs after every run: what went in, what stayed out and why."""
    t = receipt["totals"]
    est = " (estimate)" if t["tokenizer"] == "heuristic" else ""
    text = "fileflow: %d file%s, about %s tokens%s" % (
        t["files_sent"], "" if t["files_sent"] == 1 else "s", format(t["tokens_est"], ","), est)
    counts = {}
    for e in receipt["excluded"]:
        counts[e["reason"]] = counts.get(e["reason"], 0) + 1
    if counts:
        parts = ["%d %s" % (counts[k], REASON_WORDS[k].split(" (")[0]) for k in sorted(counts)]
        text += "; left out %d (%s)" % (sum(counts.values()), ", ".join(parts))
    return text + ". Details: --receipt"


def summarize(receipt):
    """A short human summary (for stderr). Never prints file contents."""
    t = receipt["totals"]
    est = "" if t["tokenizer"] != "heuristic" else " (estimate)"
    lines = ["Context receipt%s" % (" - preset '%s'" % receipt["preset"] if receipt["preset"] else "")]
    budget = " of %s budget" % format(t["budget"], ",") if t["budget"] else ""
    lines.append("  sent       %d files, about %s tokens%s%s" % (
        t["files_sent"], format(t["tokens_est"], ","), est, budget))
    prov = t.get("provenance", {})
    labelled = ["%s %d" % (k, prov[k]) for k in ("observed", "generated", "user") if prov.get(k)]
    if labelled:
        lines.append("  provenance " + ", ".join(labelled) + (", unlabelled %d" % prov["unlabelled"] if prov.get("unlabelled") else ""))
    if receipt["truncated"]:
        lines.append("  truncated  " + ", ".join(i["path"] for i in receipt["truncated"]) + "  (TOKEN_BUDGET)")
    if receipt["excluded"]:
        counts = {}
        for e in receipt["excluded"]:
            counts[e["reason"]] = counts.get(e["reason"], 0) + 1
        lines.append("  left out   " + ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts)))
    ledger = receipt["ledger"]
    if ledger["failed_checks"]:
        for f in ledger["failed_checks"]:
            lines.append("  CHECK      " + f)
    lines.append("  assumed    " + "; ".join(ledger["assumed"]) if ledger["assumed"] else "  assumed    nothing")
    lines.append("  unknown    " + "; ".join(ledger["unknown"]))
    return "\n".join(lines)
