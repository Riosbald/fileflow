"""Core engine: file collection and prompt rendering.

This module is dependency-light (stdlib + pathspec) and imported by both
the CLI and the server.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from pathspec import GitIgnoreSpec

FORMATS = ("default", "markdown", "xml", "json")

#: The single place where option defaults live.  The precedence chain is
#: CLI flags > .files-to-prompt config > these defaults.
DEFAULT_OPTIONS: dict = {
    "format": "default",
    "separators": False,
    "line_numbers": False,
    "max_tokens": None,
    "include_hidden": False,
    "ignore_gitignore": False,
    "exclude": (),
}


@dataclass(frozen=True)
class RenderOptions:
    format: str = "default"
    separators: bool = False
    line_numbers: bool = False
    max_tokens: int | None = None
    include_hidden: bool = False
    ignore_gitignore: bool = False
    exclude: tuple[str, ...] = ()

    def merged(self, overrides: dict) -> "RenderOptions":
        """Return a copy with non-None overrides applied.

        ``exclude`` patterns are *merged* (union, order-preserving), not
        replaced, so config patterns and CLI patterns are additive.
        """
        clean = {}
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "exclude":
                seen = list(self.exclude)
                for pat in value:
                    if pat not in seen:
                        seen.append(pat)
                clean["exclude"] = tuple(seen)
            else:
                clean[key] = value
        opts = replace(self, **clean)
        if opts.format not in FORMATS:
            raise ValueError(f"unknown format {opts.format!r}; expected one of {FORMATS}")
        return opts


@dataclass(frozen=True)
class FileEntry:
    path: str  # display path, using forward slashes
    content: str


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _matches_exclude(rel: str, name: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def _load_gitignore(directory: Path) -> GitIgnoreSpec | None:
    gi = directory / ".gitignore"
    if gi.is_file():
        try:
            lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        spec = GitIgnoreSpec.from_lines(lines)
        if spec.patterns:
            return spec
    return None


def collect_files(paths: list[Path | str], options: RenderOptions) -> list[FileEntry]:
    """Collect readable text files under the given paths.

    Honors hidden-file filtering, ``.gitignore`` files (per directory,
    unless ``ignore_gitignore``), and ``exclude`` fnmatch patterns tested
    against both the relative path and the basename.  Binary files (any
    file that does not decode as UTF-8) are silently skipped.
    """
    entries: list[FileEntry] = []
    for raw in paths:
        base = Path(raw)
        if base.is_file():
            content = _read_text(base)
            if content is not None:
                display = base.as_posix()
                if not _matches_exclude(display, base.name, options.exclude):
                    entries.append(FileEntry(display, content))
            continue
        if not base.is_dir():
            continue
        entries.extend(_walk_dir(base, options))
    entries.sort(key=lambda e: e.path)
    return entries


def _walk_dir(base: Path, options: RenderOptions) -> list[FileEntry]:
    collected: list[FileEntry] = []
    # (dir, spec) pairs; a file is ignored if any ancestor's spec matches
    # its path relative to that ancestor.
    specs: dict[str, GitIgnoreSpec] = {}

    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        if not options.ignore_gitignore:
            spec = _load_gitignore(current)
            if spec is not None:
                specs[str(current)] = spec

        def ignored(target: Path, is_dir: bool) -> bool:
            if options.ignore_gitignore:
                return False
            for root_str, spec in specs.items():
                root = Path(root_str)
                try:
                    rel = target.relative_to(root).as_posix()
                except ValueError:
                    continue
                if is_dir:
                    rel += "/"
                if spec.match_file(rel):
                    return True
            return False

        keep_dirs = []
        for d in sorted(dirnames):
            if d == ".git":
                continue
            if _is_hidden(d) and not options.include_hidden:
                continue
            child = current / d
            rel_display = (Path(base) / child.relative_to(base)).as_posix()
            if _matches_exclude(rel_display, d, options.exclude):
                continue
            if ignored(child, is_dir=True):
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for name in sorted(filenames):
            if _is_hidden(name) and not options.include_hidden:
                continue
            child = current / name
            display = (Path(base) / child.relative_to(base)).as_posix()
            if _matches_exclude(display, name, options.exclude):
                continue
            if ignored(child, is_dir=False):
                continue
            content = _read_text(child)
            if content is None:
                continue
            collected.append(FileEntry(display, content))
    return collected


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary


def add_line_numbers(content: str) -> str:
    lines = content.splitlines()
    if not lines:
        return content
    width = len(str(len(lines)))
    numbered = [f"{i + 1:>{width}}  {line}" for i, line in enumerate(lines)]
    out = "\n".join(numbered)
    if content.endswith("\n"):
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "jsx",
    ".tsx": "tsx", ".json": "json", ".md": "markdown", ".html": "html",
    ".css": "css", ".sh": "bash", ".rb": "ruby", ".go": "go", ".rs": "rust",
    ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".toml": "toml",
    ".yml": "yaml", ".yaml": "yaml", ".sql": "sql", ".txt": "",
}


def _lang_for(path: str) -> str:
    return _LANG_BY_EXT.get(Path(path).suffix.lower(), "")


def _chunk_default(entry: FileEntry, options: RenderOptions) -> str:
    if options.separators:
        rule = "=" * 48
        return f"{rule}\nFILE: {entry.path}\n{rule}\n{entry.content}"
    return f"{entry.path}\n---\n{entry.content}\n---"


def _chunk_markdown(entry: FileEntry, options: RenderOptions) -> str:
    fence = "```"
    while fence in entry.content:
        fence += "`"
    lang = _lang_for(entry.path)
    return f"{entry.path}\n{fence}{lang}\n{entry.content}\n{fence}"


def _chunk_xml(entry: FileEntry, index: int) -> str:
    return (
        f'<document index="{index}">\n'
        f"<source>{entry.path}</source>\n"
        f"<document_contents>\n{entry.content}\n</document_contents>\n"
        f"</document>"
    )


def render(entries: list[FileEntry], options: RenderOptions) -> str:
    """Render collected files to a single prompt string."""
    if options.format not in FORMATS:
        raise ValueError(f"unknown format {options.format!r}; expected one of {FORMATS}")

    if options.line_numbers:
        entries = [FileEntry(e.path, add_line_numbers(e.content)) for e in entries]

    budget = options.max_tokens
    kept: list[FileEntry] = []
    used = 0
    truncated = False
    for entry in entries:
        cost = estimate_tokens(entry.content) + estimate_tokens(entry.path) + 4
        if budget is not None and kept and used + cost > budget:
            truncated = True
            break
        if budget is not None and not kept and cost > budget:
            truncated = True
            break
        kept.append(entry)
        used += cost

    omitted = len(entries) - len(kept)

    if options.format == "json":
        payload = {
            "files": [
                {
                    "path": e.path,
                    "content": e.content,
                    "tokens": estimate_tokens(e.content),
                }
                for e in kept
            ],
            "total_tokens": used,
            "truncated": truncated,
        }
        return json.dumps(payload, indent=2)

    if options.format == "xml":
        body = "\n".join(_chunk_xml(e, i + 1) for i, e in enumerate(kept))
        out = f"<documents>\n{body}\n</documents>" if kept else "<documents>\n</documents>"
        if truncated:
            out += (
                f"\n<!-- fileflow: truncated at {budget} tokens, "
                f"{omitted} file(s) omitted -->"
            )
        return out

    if options.format == "markdown":
        out = "\n\n".join(_chunk_markdown(e, options) for e in kept)
    else:  # default
        out = "\n\n".join(_chunk_default(e, options) for e in kept)

    if truncated:
        notice = (
            f"[fileflow] truncated: token budget {budget} reached, "
            f"{omitted} file(s) omitted"
        )
        out = f"{out}\n\n{notice}" if out else notice
    return out


def build_prompt(paths: list[Path | str], options: RenderOptions) -> str:
    """Convenience: collect + render in one step."""
    return render(collect_files(paths, options), options)
