"""fileflow command line tool.

Recursively reads the contents of a directory (or a set of files and
directories) and prints them all in a structured format that is easy to feed
to a large language model.

Implemented against the files-to-prompt product specification:
- Directory-to-prompt conversion (recursive by default)
- Multi-path support (files and directories mixed)
- Hidden file control (``--include-hidden``)
- Gitignore integration (``--ignore-gitignore`` to disable)
- Ignore patterns (``--ignore-patterns``)
- Output control: ``--output-file``, ``--format``, ``--no-separators``,
  ``--line-numbers``
"""

import json
import os
import sys
import types
from collections import namedtuple

import click

from . import __version__, clipboard, presets as presets_mod, receipt as receipt_mod, secretscan
from .provenance import attr_value, read_provenance
from .errors import FileflowError
from .walker import (  # noqa: F401  (re-exported: the engine's public names)
    BINARY, NOT_CHANGED, NOT_INCLUDED, SECRET_BLOCKED, SYMLINK_OUTSIDE_ROOT, TOKEN_BUDGET, TOO_LARGE, UNREADABLE,
    Entry, _normalize_gitignore_rule, _gitignore_rule_matches, _scope_matches,
    path_is_ignored, read_gitignore, should_ignore, stays_inside, walk_entries,
)

# Per-file size cap. Default 1 MiB: a file that large is almost never something a person means to
# paste into a model (lockfiles, logs, dumps), reading it whole costs memory, and the prompt
# it would produce is mostly noise. Raise or lift it with FILEFLOW_MAX_FILE_BYTES (bytes;
# 0 = no cap). A skipped file is never silent: it is warned about and listed as TOO_LARGE.
DEFAULT_MAX_FILE_BYTES = 1024 * 1024


def parse_max_file_bytes(raw):
    """``(limit, problem)`` from the environment value. Garbage must not crash the CLI at import
    or silently disable the guard: fall back to the default and say so."""
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_FILE_BYTES, None
    try:
        value = int(raw.strip())
    except ValueError:
        value = -1
    if value < 0:
        return DEFAULT_MAX_FILE_BYTES, (
            "FILEFLOW_MAX_FILE_BYTES=%r is not a whole number of bytes; using the default of %s "
            "(use 0 for no limit)" % (raw, format(DEFAULT_MAX_FILE_BYTES, ","))
        )
    return value, None


_MAX_FILE_BYTES, _MAX_FILE_BYTES_PROBLEM = parse_max_file_bytes(os.environ.get("FILEFLOW_MAX_FILE_BYTES"))


def human_size(n):
    for unit, size in (("MB", 1024 * 1024), ("KB", 1024)):
        if n >= size:
            return "%.1f %s" % (n / size, unit)
    return "%d bytes" % n


def add_line_numbers(content):
    """Prefix each line of ``content`` with its (right-aligned) line number.

    Lines are split on ``\\n`` only. ``str.splitlines()`` would also break on form feed,
    ``\\x1c``-``\\x1e``, ``\\x85``, U+2028 and U+2029 and then re-join with newlines, silently
    rewriting the file; and the JS engine never did that. (Line endings are already normalised
    to ``\\n`` when a file is read.) A trailing newline does not add an empty last line, ``""`` is
    zero lines, and ``"\\n"`` is one empty line.
    """
    if content == "":
        return ""
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    padding = len(str(len(lines)))
    return "\n".join(f"{i + 1:{padding}}  {line}" for i, line in enumerate(lines))


def estimate_tokens(text):
    """Rough token estimate (~4 characters per token)."""
    return max(1, len(text) // 4) if text else 0


def count_tokens(text, tokenizer="heuristic"):
    """Count tokens in ``text`` using the named tokenizer.

    ``"heuristic"`` (default) is a cheap offline estimate (~4 chars/token).
    Any other value requires the optional ``tiktoken`` dependency and is
    interpreted as a model or encoding name. Raises ``click.UsageError`` when
    the tokenizer cannot be loaded.
    """
    if tokenizer == "heuristic":
        return estimate_tokens(text)
    try:
        import tiktoken
    except ImportError:
        raise click.UsageError(
            f"--tokenizer '{tokenizer}' requires tiktoken. "
            "Install with: pip install tiktoken"
        )
    try:
        enc = tiktoken.encoding_for_model(tokenizer)
    except Exception:
        try:
            enc = tiktoken.get_encoding(tokenizer)
        except Exception:
            raise click.UsageError(f"Unknown tokenizer or model: {tokenizer}")
    return len(enc.encode(text))


BudgetResult = namedtuple("BudgetResult", ["documents", "truncated", "dropped"])


def apply_token_budget_detailed(documents, max_tokens, tokenizer="heuristic"):
    """Like :func:`apply_token_budget` but also says *what* the budget did.

    Returns ``BudgetResult(documents, truncated, dropped)`` where ``truncated``
    is a list of ``(path, kept_tokens, original_tokens)`` (at most one entry:
    the file that crossed the budget) and ``dropped`` the paths after it that
    were left out. The receipt is built from this, so nothing the budget removes
    is ever silent.
    """
    if not max_tokens or max_tokens <= 0:
        return BudgetResult(list(documents), [], [])
    out, truncated, dropped = [], [], []
    remaining = max_tokens
    for index, (path, content) in enumerate(documents):
        tokens = count_tokens(content, tokenizer)
        if tokens <= remaining:
            out.append((path, content))
            remaining -= tokens
            continue
        if remaining > 0 and content:
            head = content[: remaining * 4]
            head += (
                "\n\n# ... truncated to fit the %s-token budget "
                "(this file was ~%s tokens)" % (max_tokens, tokens)
            )
            out.append((path, head))
            truncated.append((path, remaining, tokens))
        else:
            dropped.append(path)
        dropped.extend(p for p, _ in documents[index + 1 :])
        break
    return BudgetResult(out, truncated, dropped)


def apply_token_budget(documents, max_tokens, tokenizer="heuristic"):
    """Trim ``documents`` (list of ``(path, content)``) to a token budget.

    Files are considered in order. While a file fits the remaining budget it
    is kept whole; the first file that would exceed the budget is truncated to
    the remaining headroom (with a marker appended) and every later file is
    dropped. A ``max_tokens`` <= 0 disables trimming and returns the input
    unchanged.
    """
    if not max_tokens or max_tokens <= 0:
        return documents
    return apply_token_budget_detailed(documents, max_tokens, tokenizer).documents


Read = namedtuple("Read", ["content", "reason", "message"])


def read_text(path):
    """Read ``path`` as UTF-8 without printing anything.

    Returns ``Read(content, reason, message)``: on success ``reason`` is
    ``None``; otherwise ``content`` is ``None`` and ``reason`` is one of
    ``TOO_LARGE`` / ``BINARY`` / ``UNREADABLE`` with a human-readable
    ``message``.
    """
    try:
        if _MAX_FILE_BYTES > 0 and os.path.getsize(path) > _MAX_FILE_BYTES:
            return Read(
                None,
                TOO_LARGE,
                f"Skipping {path}: {human_size(os.path.getsize(path))}, over the {human_size(_MAX_FILE_BYTES)} "
                "size limit (TOO_LARGE). To include it: FILEFLOW_MAX_FILE_BYTES=0 (no limit) "
                "or a larger number of bytes",
            )
    except OSError:
        pass
    try:
        # newline=None (the default, spelled out): "\\r\\n" and lone "\\r" are read as "\\n". The JS
        # engine normalises identically, so a CRLF file renders the same in the CLI and the web client.
        with open(path, "r", encoding="utf-8", newline=None) as f:
            return Read(f.read(), None, None)
    except UnicodeDecodeError:
        return Read(None, BINARY, f"Skipping {path}: not UTF-8 text (BINARY)")
    except OSError as ex:
        return Read(None, UNREADABLE, f"Could not read file {path}: {ex}")


def _stderr_is_tty():
    """True when a person is reading stderr (a seam so tests can simulate a terminal)."""
    return sys.stderr is not None and sys.stderr.isatty()


def _heads_up(rec):
    """Rare, actionable advisories (e.g. one file is most of the prompt). Always on stderr."""
    for line in receipt_mod.advisories(rec):
        click.echo(click.style("Heads-up: " + line, fg="yellow"), err=True)


def _warn(message):
    click.echo(click.style("Warning: " + message, fg="red"), err=True)


def read_file_contents(path):
    """Read a text file as UTF-8.

    Returns ``(path, content)`` on success or ``None`` if the file could not
    be read (for example it is binary). A warning is printed to stderr so
    binary files are handled gracefully rather than crashing the tool.
    """
    result = read_text(path)
    if result.reason is not None:
        _warn(result.message)
        return None
    return path, result.content


class Collection:
    """Everything one walk decided: what is in, and what is out and why.

    ``documents`` is the list of ``(path, content)`` that will be rendered.
    ``excluded`` is a list of :class:`fileflow.walker.Entry` (with ``reason``)
    for everything met but not used -- excluded directories appear once, as a
    directory. ``secret_findings`` maps path -> list of
    :class:`fileflow.secretscan.Finding`.
    """

    def __init__(self):
        self.documents = []
        self.excluded = []
        self.secret_findings = {}
        self.provenance = {}  # path -> {"provenance": label, "source_url"?: str}


def collect(
    paths,
    include_hidden=False,
    ignore_gitignore=False,
    ignore_patterns=None,
    secrets="off",
    quiet=False,
    include_patterns=None,
    only=None,
):
    """Walk ``paths`` and read every selected file, recording every decision.

    ``only`` (a set of real paths, or ``None``) keeps just those files; everything else selected by
    the walk is recorded as ``NOT_CHANGED`` (used by ``--diff`` / ``--staged`` / ``--since``).

    ``secrets`` is ``"off"``, ``"warn"`` (report findings, keep the file),
    ``"exclude"`` (leave files with findings out, reason ``SECRET_BLOCKED``) or
    ``"block"`` (same collection as ``exclude``; the CLI then fails closed).
    """
    if secrets not in secretscan.MODES:
        raise click.UsageError("secrets mode must be one of: %s" % ", ".join(secretscan.MODES))
    col = Collection()
    for entry in walk_entries(paths, include_hidden, ignore_gitignore, ignore_patterns, include_patterns):
        if entry.reason is not None:
            col.excluded.append(entry)
            if entry.reason == SYMLINK_OUTSIDE_ROOT and not quiet:
                _warn(f"Skipping {entry.path}: symlink points outside the project")
            continue
        if entry.is_dir:
            continue
        if only is not None and os.path.realpath(entry.path) not in only:
            col.excluded.append(Entry(entry.path, False, NOT_CHANGED))
            continue
        result = read_text(entry.path)
        if result.reason is not None:
            col.excluded.append(Entry(entry.path, False, result.reason))
            if not quiet:
                _warn(result.message)
            continue
        if secrets != "off":
            findings = secretscan.scan(result.content)
            if findings:
                col.secret_findings[entry.path] = findings
                rules = ", ".join(sorted({f.rule for f in findings}))
                where = ", ".join("line %d" % n for n in sorted({f.line for f in findings})[:5])
                if secrets in ("exclude", "block"):
                    col.excluded.append(Entry(entry.path, False, SECRET_BLOCKED))
                    if not quiet:
                        if secrets == "exclude":
                            _warn(f"Left out {entry.path}: possible secret ({rules}; {where}). Value not shown.")
                    continue
                if not quiet:
                    _warn(f"{entry.path}: possible secret ({rules}; {where}). Value not shown.")
        label = read_provenance(result.content) if result.content.startswith("---") else None
        if label:
            col.provenance[entry.path] = label
        col.documents.append((entry.path, result.content))
    return col


def iter_documents(
    paths, include_hidden=False, ignore_gitignore=False, ignore_patterns=None, secrets="off"
):
    """Yield ``(path, content)`` for every readable file under ``paths``.

    ``paths`` is an iterable of files and/or directories. Files are included
    directly; directories are walked recursively. Hidden files are skipped
    unless ``include_hidden`` is True, gitignore rules are applied unless
    ``ignore_gitignore`` is True, and glob patterns in ``ignore_patterns``
    are always applied.

    Symlinked files that resolve outside a walked directory are skipped with
    a warning; symlinked directories are never descended. A file passed
    explicitly is the user's own choice and is always read.
    """
    for doc in collect(
        paths, include_hidden, ignore_gitignore, ignore_patterns, secrets=secrets
    ).documents:
        yield doc


def _prov_note(info):
    """``[provenance: observed; source: URL]`` (values sanitised: no newlines, quotes or markup)."""
    note = "provenance: " + info["provenance"]
    if info.get("source_url"):
        note += "; source: " + attr_value(info["source_url"])
    return "[" + note + "]"


def _format_default(path, content, line_numbers, separators, info=None):
    if line_numbers:
        content = add_line_numbers(content)
    head = f"{path}\n{_prov_note(info)}" if info else path
    if separators:
        return f"{head}\n---\n{content}\n---"
    return f"{head}\n{content}"


def _format_xml(path, content, line_numbers, index, info=None):
    if line_numbers:
        content = add_line_numbers(content)
    attrs = ""
    if info:
        attrs = f' provenance="{info["provenance"]}"'
        if info.get("source_url"):
            attrs += f' source_url="{attr_value(info["source_url"])}"'
    return (
        f'<document index="{index}"{attrs}>\n'
        f"<source>{path}</source>\n"
        "<document_content>\n"
        f"{content}\n"
        "</document_content>\n"
        "</document>"
    )


PATCH_PATH = "git-diff.patch"  # the --patch document (not a file on disk)
_ASCII_WS = " \t\r\n"


def normalize_instruction(instruction):
    """Trim ASCII whitespace only; ``""`` means "no instruction".

    Only ASCII whitespace is stripped so Python and the JS engine (whose
    ``trim`` differs on exotic Unicode spaces) can never disagree.
    """
    return (instruction or "").strip(_ASCII_WS)


def render_documents(
    documents,
    format="default",
    line_numbers=False,
    separators=True,
    instruction=None,
    provenance=None,
):
    """Render a list of ``(path, content)`` documents into a prompt string.

    This is the pure formatting stage of :func:`generate_prompt`, factored out
    so both the CLI and the web client can be compared byte-for-byte on the
    same (path, content) corpus. ``format`` may be ``"default"``, ``"xml"`` or
    ``"json"``.

    A non-blank ``instruction`` is rendered *outside* the files section:

    * default: ``# Task`` section, then a ``# Files`` section;
    * xml: ``<task_instructions>`` block before ``<documents>``;
    * json: an object ``{"instructions": ..., "documents": [...]}`` (without an
      instruction the output stays a bare array, exactly as before).

    ``provenance`` maps ``path -> {"provenance": label, "source_url"?: str}``. A document
    with an entry is annotated (default: a ``[provenance: ...]`` line under the path; xml:
    attributes; json: keys); a document without one renders exactly as before.
    """
    instruction = normalize_instruction(instruction)
    provenance = provenance or {}

    if format == "json":
        data = []
        for path, content in documents:
            if line_numbers:
                content = add_line_numbers(content)
            entry = {"path": path, "content": content}
            info = provenance.get(path)
            if info:
                entry["provenance"] = info["provenance"]
                if info.get("source_url"):
                    entry["source_url"] = attr_value(info["source_url"])
            data.append(entry)
        if instruction:
            data = {"instructions": instruction, "documents": data}
        return json.dumps(data, indent=2, ensure_ascii=False)

    blocks = []
    if format == "xml":
        blocks.append("<documents>")
        for index, (path, content) in enumerate(documents, start=1):
            blocks.append(_format_xml(path, content, line_numbers, index, provenance.get(path)))
        blocks.append("</documents>")
        body = "\n".join(blocks)
        if instruction:
            body = "<task_instructions>\n%s\n</task_instructions>\n%s" % (instruction, body)
        return body

    # default
    for path, content in documents:
        blocks.append(_format_default(path, content, line_numbers, separators, provenance.get(path)))
    body = "\n\n".join(blocks)
    if instruction:
        parts = ["# Task\n\n" + instruction]
        if body:
            parts.append("# Files\n\n" + body)
        body = "\n\n".join(parts)
    return body


def generate_prompt(
    paths,
    include_hidden=False,
    ignore_gitignore=False,
    ignore_patterns=None,
    format="default",
    line_numbers=False,
    separators=True,
    max_tokens=0,
    tokenizer="heuristic",
    instruction=None,
):
    """Build the complete prompt text for the given ``paths``.

    This is the core algorithm described in the product specification:

    * validate paths (caller is responsible for raising BadParameter)
    * collect gitignore rules from the root of each directory
    * walk directories, filtering hidden files, gitignore rules and
      ``--ignore-patterns``
    * read every collected file and render it in the requested format.

    ``format`` may be ``"default"``, ``"xml"`` or ``"json"``. ``max_tokens``
    (when > 0) trims the collected files to a token budget. Returns the
    prompt as a single string.
    """
    documents = list(iter_documents(paths, include_hidden, ignore_gitignore, ignore_patterns))
    documents = apply_token_budget(documents, max_tokens, tokenizer=tokenizer)
    return render_documents(
        documents, format=format, line_numbers=line_numbers, separators=separators, instruction=instruction
    )


def load_config(base, required=False):
    """Read ``base/.files-to-prompt``. Returns ``(cfg, loaded)``.

    A config fileflow cannot read is an **error, never a warning** (FN-015). The file carries safety
    settings (``[secrets] mode = "block"``, ignore rules); warning and carrying on would send the
    prompt with exactly those settings silently dropped. This matches how a config from a *newer*
    fileflow is already refused (``check_version``). ``required`` is kept for callers; it no longer
    changes the outcome. The web server stays tolerant on purpose (its UI is how you fix the file).
    """
    from .server.config import ConfigError, config_path, read_config

    if not os.path.isfile(config_path(base)):
        return {}, False
    try:
        cfg = read_config(base, strict=True)
    except ConfigError as exc:
        text = str(exc)
        code = "E_CONFIG_PARSE" if "not valid TOML" in text else "E_CONFIG_SCHEMA"
        raise FileflowError(
            code,
            text,
            "nothing was sent: your settings (secrets mode, ignore rules) cannot be applied while this file is broken. "
            "Run `fileflow check` to see the problem, fix .files-to-prompt, then retry",
        )
    presets_mod.check_version(cfg)  # a config from a newer fileflow is refused, not misread
    return cfg, True


def config_to_options(cfg):
    """Map a loaded config to option values (keys the CLI/engine understand)."""
    presets_mod.validate_settings(cfg)  # a wrong-typed setting is an error, not a silent default
    out = {}
    o = cfg.get("output", {})
    if o.get("format") in ("default", "xml", "json"):
        out["format"] = o["format"]
    if isinstance(o.get("separators"), bool):
        out["separators"] = o["separators"]
    if isinstance(o.get("line_numbers"), bool):
        out["line_numbers"] = o["line_numbers"]
    if o.get("max_tokens"):
        out["max_tokens"] = int(o["max_tokens"])
    ig = cfg.get("ignore", {})
    if isinstance(ig.get("hidden"), bool):
        out["include_hidden"] = ig["hidden"]
    if isinstance(ig.get("gitignore"), bool):
        # The config stores "respect .gitignore"; --ignore-gitignore inverts it.
        out["ignore_gitignore"] = not ig["gitignore"]
    excl = cfg.get("exclude", {}).get("patterns") or []
    if excl:
        out["ignore_patterns"] = list(excl)
    incl = cfg.get("include", {}).get("patterns") or []
    if incl:
        out["include_patterns"] = list(incl)
    mode = presets_mod.secrets_mode(cfg)
    if mode:
        out["secrets"] = mode
    return out


def preset_to_options(preset):
    """The subset of a preset that overrides root-config options."""
    out = {}
    for key in ("format", "separators", "line_numbers", "max_tokens", "include_hidden", "secrets", "include_patterns", "diff", "staged", "patch"):
        if key in preset:
            out[key] = preset[key]
    if "ignore_gitignore" in preset:
        out["ignore_gitignore"] = preset["ignore_gitignore"]
    return out


def read_project_config(cwd=None):
    """Map a project's ``.files-to-prompt`` (from ``cwd``) to CLI overrides.

    Precedence is applied by the caller: explicit CLI flags win over these
    config-derived values, which win over tool defaults. Returns ``{}`` when
    there is no (usable) config file.
    """
    cfg, loaded = load_config(cwd or os.getcwd())
    return config_to_options(cfg) if loaded else {}


class _DefaultGroup(click.Group):
    """A Click group that falls back to a default subcommand.

    ``fileflow path/to/dir`` should behave like ``fileflow prompt path/to/dir``
    while still allowing ``fileflow serve``. When the first argument is not a
    registered command name, the arguments are handed to the default command.
    """

    default_cmd = "prompt"
    # Options that belong to the group itself; any other leading option is a
    # `prompt` option (so `fileflow --preset hero` works with no path at all).
    _group_options = ("-h", "--help", "--version")

    def parse_args(self, ctx, args):
        if args and args[0].startswith("-") and args[0] not in self._group_options:
            args = [self.default_cmd] + list(args)
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            # Not a known subcommand → route everything to the default command.
            cmd = self.get_command(ctx, self.default_cmd)
            if cmd is None:
                raise
            return self.default_cmd, cmd, args


@click.group(
    cls=_DefaultGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=__version__, prog_name="fileflow")
def cli():
    """
    fileflow — turn files into a single LLM prompt.

    \b
        fileflow path/to/directory
        fileflow README.md src/ tests/
        fileflow serve            # optional local web backend

    Use `fileflow prompt --help` for prompt options, or `fileflow serve --help`
    for the local server.
    """


def _layer(key, cli_value, preset_opts, cfg_opts, default):
    """CLI flag > preset > root config > built-in default."""
    if cli_value is not None:
        return cli_value
    if key in preset_opts:
        return preset_opts[key]
    return cfg_opts.get(key, default)


def _assemble(paths, preset_name, instruction, instruction_file, variables, allow_unresolved, include_hidden,
              ignore_gitignore, ignore_patterns, include_patterns, diff_flag, staged_flag, since_ref, patch_flag,
              output_format, no_separators, line_numbers, max_tokens, tokenizer, secrets_mode,
              secrets_default="warn", secrets_cli_only=False, preloaded=None):
    """Everything `prompt` and `ask` share: config layers, preset, instruction, selection, secrets, budget, render.

    Returns a namespace with the rendered prompt (``out``) and the pieces the receipt needs.
    """
    if _MAX_FILE_BYTES_PROBLEM:
        _warn(_MAX_FILE_BYTES_PROBLEM)
    root = os.getcwd()
    cfg, loaded = preloaded if preloaded is not None else load_config(root, required=bool(preset_name))
    cfg_opts = config_to_options(cfg) if loaded else {}
    if loaded:
        # Only near-misses of a real key: a truly new key (from a newer fileflow) stays quiet.
        for u in presets_mod.unknown_keys(cfg):
            if u["suggestion"]:
                _warn("%s: '%s' is not a setting fileflow reads (did you mean '%s'?). It is being ignored."
                      % (u["where"], u["key"], u["suggestion"]))

    preset = None
    preset_opts = {}
    if preset_name:
        preset = presets_mod.get_preset(cfg, preset_name)
        preset_opts = preset_to_options(preset)

    # Paths: the command line wins; otherwise the preset's; otherwise it's an error.
    if paths:
        paths = list(paths)
    elif preset is not None:
        paths = presets_mod.preset_paths(root, preset, preset_name) or ["."]
    elif diff_flag or staged_flag or since_ref or patch_flag:
        paths = ["."]  # "what changed" with no path means: in this directory
    else:
        raise click.UsageError("At least one file or directory path is required.")

    include_hidden = _layer("include_hidden", include_hidden, preset_opts, cfg_opts, False)
    ignore_gitignore = _layer("ignore_gitignore", ignore_gitignore, preset_opts, cfg_opts, False)
    line_numbers = _layer("line_numbers", line_numbers, preset_opts, cfg_opts, False)
    output_format = _layer("format", output_format, preset_opts, cfg_opts, "default")
    max_tokens = _layer("max_tokens", max_tokens, preset_opts, cfg_opts, 0)
    if secrets_cli_only:
        # Sending to a third party: only the command line may choose a mode. A repository's config or
        # presets cannot weaken the default (block) for data that is about to leave the machine.
        secrets_mode = secrets_mode or secrets_default
    else:
        secrets_mode = _layer("secrets", secrets_mode, preset_opts, cfg_opts, secrets_default)
    if no_separators:
        separators = False
    else:
        separators = _layer("separators", None, preset_opts, cfg_opts, True)
    # Exclude patterns are additive: flags + root config + the preset's own.
    ignore_patterns = (
        list(ignore_patterns)
        + cfg_opts.get("ignore_patterns", [])
        + (preset.get("exclude_patterns", []) if preset else [])
    )
    # A pattern containing "/" is compared with NAMES, which never contain one: it can never match,
    # and the user would get a prompt that silently still has the file. Say so, with the fix.
    for pat in ignore_patterns:
        if "/" in pat:
            name = pat.rstrip("/").rsplit("/", 1)[-1] or pat
            _warn(
                "ignore pattern %r matches nothing: patterns are compared with file and folder NAMES, "
                "not paths. Use %r (it applies in every folder)." % (pat, name)
            )

    # Instruction: --instruction / --instruction-file beat the preset's.
    if instruction is not None and instruction_file:
        raise FileflowError("E_USAGE", "use either --instruction or --instruction-file, not both")
    text, source = None, "none"
    if instruction is not None:
        text, source = instruction, "cli"
    elif instruction_file:
        text, source = presets_mod.read_instruction_file(instruction_file), "cli-file"
    elif preset is not None:
        found = presets_mod.instruction_from_preset(root, preset)
        if found.strip():
            text, source = found, "preset"

    unresolved = []
    if text is not None:
        merged = {k: str(v) for k, v in (preset.get("vars", {}) if preset else {}).items()}
        cli_vars = presets_mod.parse_vars(variables)
        merged.update(cli_vars)
        used = presets_mod.placeholders(text)
        for name in cli_vars:
            if name not in used:
                click.echo(click.style(
                    "Warning: variable '%s' is not used by the instruction" % name, fg="yellow"), err=True)
        text, unresolved = presets_mod.render_template(text, merged)
        if unresolved and not allow_unresolved:
            names = ", ".join("{{%s}}" % n for n in unresolved)
            raise FileflowError(
                "E_PLACEHOLDER_UNRESOLVED",
                "the instruction still has unfilled placeholders: %s" % names,
                "fileflow ... " + " ".join("--var %s=VALUE" % n for n in unresolved)
                + "   (or --allow-unresolved to keep them as written)",
            )
    elif variables:
        click.echo(click.style("Warning: --var given but there is no instruction to fill", fg="yellow"), err=True)

    # [include] REPLACES rather than adds (a narrower list from a higher layer wins): CLI > preset > root config.
    include_patterns = list(include_patterns) if include_patterns else list(
        preset_opts.get("include_patterns", cfg_opts.get("include_patterns", [])))
    # --diff / --staged / --since / --patch: restrict to what changed (CLI > preset). See fileflow/gitdiff.py
    staged = _layer("staged", staged_flag, preset_opts, cfg_opts, False)
    with_patch = _layer("patch", patch_flag, preset_opts, cfg_opts, False)
    want_diff = _layer("diff", diff_flag, preset_opts, cfg_opts, False)
    if since_ref and staged:
        raise FileflowError("E_USAGE", "--staged and --since cannot be combined", "pick one: staged changes, or changes since a commit")
    change_mode = "staged" if staged else "since" if since_ref else "head" if (want_diff or with_patch) else None
    only, git_root, base = None, None, None
    if change_mode:
        from . import gitdiff

        git_root = gitdiff.repo_root(root)
        base = gitdiff.resolve_ref(git_root, since_ref)[:12] if change_mode == "since" else None
        only = gitdiff.changed_files(git_root, change_mode, since_ref)
    col = collect(
        paths,
        include_hidden=include_hidden,
        ignore_gitignore=ignore_gitignore,
        ignore_patterns=ignore_patterns,
        secrets=secrets_mode,
        include_patterns=include_patterns,
        only=only,
    )
    if change_mode and not any(d for d in col.documents):
        click.echo(click.style("Warning: no changed files matched (%s)" % {
            "head": "since HEAD", "staged": "staged", "since": "since " + str(since_ref)}[change_mode], fg="yellow"), err=True)
    if with_patch:
        from . import gitdiff

        patch = gitdiff.patch_text(git_root, change_mode, since_ref)
        if patch.strip():
            found = secretscan.scan(patch) if secrets_mode != "off" else []
            if found:
                rules = ", ".join(sorted({f.rule for f in found}))
                if secrets_mode == "block":
                    raise FileflowError("E_SECRET_FOUND", "possible secret (%s) in the patch; nothing was emitted (values are never shown)" % rules,
                                        "a removed line can still leak a secret; rotate it, or use --secrets exclude to drop the patch")
                if secrets_mode == "exclude":
                    _warn("Left out the patch: possible secret (%s). Value not shown." % rules)
                    patch = ""
                else:
                    col.secret_findings[PATCH_PATH] = found
                    _warn("the patch contains a possible secret (%s). Value not shown." % rules)
            if patch.strip():
                col.documents.append((PATCH_PATH, patch))
    if include_patterns and not col.documents:
        click.echo(click.style("Warning: no files matched the include patterns: %s" % ", ".join(include_patterns), fg="yellow"), err=True)
    if secrets_mode == "block" and col.secret_findings:
        lines = []
        for path, found in sorted(col.secret_findings.items()):
            rules = ", ".join(sorted({f.rule for f in found}))
            lines.append("  %s: %s (line %s)" % (path, rules, ", ".join(str(n) for n in sorted({f.line for f in found})[:5])))
        raise FileflowError(
            "E_SECRET_FOUND",
            "possible secrets found; nothing was emitted (values are never shown):\n" + "\n".join(lines),
            "remove the secret, add the path to [exclude] patterns, or use --secrets exclude / --secrets warn",
        )

    budget = apply_token_budget_detailed(col.documents, max_tokens, tokenizer=tokenizer)
    out = render_documents(
        budget.documents,
        format=output_format,
        line_numbers=line_numbers,
        separators=separators,
        instruction=text,
        provenance=col.provenance,
    )

    return types.SimpleNamespace(
        root=root, cfg=cfg, loaded=loaded, preset=preset, preset_name=preset_name, text=text, unresolved=unresolved,
        col=col, budget=budget, out=out, secrets_mode=secrets_mode, max_tokens=max_tokens, tokenizer=tokenizer,
        change_mode=change_mode, base=base, paths=paths,
    )


def _make_receipt(A, tokenizer):
    shown = A.text.strip(_ASCII_WS) if A.text is not None else None
    return receipt_mod.build_receipt(
        A.col,
        A.budget,
        A.root,
        lambda t: count_tokens(t, tokenizer),
        tokenizer=tokenizer,
        preset=A.preset_name,
        max_tokens=A.max_tokens,
        secrets_mode=A.secrets_mode,
        instruction=shown if shown else None,
        unresolved=A.unresolved,
        config_loaded=A.loaded,
        paths_verified=bool(A.preset and A.preset.get("paths")),
        changes={"mode": A.change_mode, "base": A.base} if A.change_mode else None,
    )


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option(
    "--preset",
    "-p",
    "preset_name",
    help="Run a named preset from .files-to-prompt ([presets.<name>]). "
    "List them with `fileflow presets`.",
)
@click.option("--instruction", help="Task text to put before the files (rendered outside them).")
@click.option(
    "--instruction-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Read the task text from this file.",
)
@click.option(
    "--var",
    "variables",
    multiple=True,
    metavar="NAME=VALUE",
    help="Fill a {{NAME}} placeholder in the instruction. Repeatable.",
)
@click.option(
    "--allow-unresolved",
    is_flag=True,
    help="Keep unfilled {{placeholders}} as written instead of failing.",
)
@click.option(
    "--include-hidden",
    is_flag=True,
    default=None,
    help="Include files and folders starting with .",
)
@click.option(
    "--ignore-gitignore",
    is_flag=True,
    default=None,
    help="Ignore .gitignore files and include all files",
)
@click.option(
    "--ignore-patterns",
    multiple=True,
    help="Glob patterns to exclude, matched against file and folder NAMES (not paths), "
    "e.g. '*.md' or '__pycache__'. Repeatable.",
)
@click.option(
    "--include",
    "include_patterns",
    multiple=True,
    metavar="PATTERN",
    help="Only include files matching a pattern, e.g. '*.py' or 'src/*'. Repeatable. "
    "`*` and `?` only; a pattern with a / matches the path from the directory you pass. "
    "Exclusions still apply, and files you name explicitly are always included.",
)
@click.option("--diff", "diff_flag", is_flag=True, default=None,
              help="Only files changed since HEAD (tracked changes plus untracked files that aren't ignored).")
@click.option("--staged", "staged_flag", is_flag=True, default=None, help="Only files staged for commit.")
@click.option("--since", "since_ref", metavar="REF", default=None,
              help="Only files changed since this branch/tag/commit (resolved to a full SHA first).")
@click.option("--patch", "patch_flag", is_flag=True, default=None,
              help="Also include the unified diff (tracked changes) as one more document. Implies --diff.")
@click.option(
    "--output-file",
    type=click.Path(dir_okay=False, writable=True),
    help="Write the output to this file instead of stdout",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["default", "xml", "json"]),
    default=None,
    help="Output format (default: default; overrides .files-to-prompt)",
)
@click.option(
    "--no-separators",
    is_flag=True,
    default=None,
    help="Omit the --- separator lines in the default output",
)
@click.option(
    "--line-numbers",
    is_flag=True,
    default=None,
    help="Prefix each line of code with its line number",
)
@click.option(
    "max_tokens",
    "--max-tokens",
    type=int,
    default=None,
    help="Trim the output to an approximate token budget (0 disables)",
)
@click.option(
    "--tokenizer",
    default="heuristic",
    show_default=True,
    help="Tokenizer for --max-tokens: 'heuristic' (offline) or a tiktoken model/encoding",
)
@click.option(
    "--secrets",
    "secrets_mode",
    type=click.Choice(list(secretscan.MODES)),
    default=None,
    help="Secret guard: warn (default) | exclude (leave the file out) | block "
    "(fail with exit 3, emit nothing) | off. Findings never show the value.",
)
@click.option("--receipt", "show_receipt", is_flag=True, help="Print a context receipt (what went in/out and why) to stderr.")
@click.option(
    "--receipt-file",
    type=click.Path(dir_okay=False, writable=True),
    help="Write the receipt as JSON to this file.",
)
@click.option("--copy", "copy_to_clipboard", is_flag=True, help="Copy the prompt to the clipboard instead of printing it.")
def prompt(
    paths,
    preset_name,
    instruction,
    instruction_file,
    variables,
    allow_unresolved,
    include_hidden,
    ignore_gitignore,
    ignore_patterns,
    include_patterns,
    diff_flag,
    staged_flag,
    since_ref,
    patch_flag,
    output_file,
    output_format,
    no_separators,
    line_numbers,
    max_tokens,
    tokenizer,
    secrets_mode,
    show_receipt,
    receipt_file,
    copy_to_clipboard,
):
    """
    Takes one or more paths to files or directories and outputs every file,
    recursively, each one preceded with its path and separated by --- lines.

    \b
        path/to/file.py
        ---
        Contents of file.py goes here
        ---
        path/to/file2.py
        ---
        ...

    Use the --format flag to switch between the default, XML and JSON output
    formats. Invalid paths raise an error.

    \b
    Settings come from, in order of priority:
        command-line flags  >  --preset  >  .files-to-prompt  >  defaults
    """
    A = _assemble(
        paths, preset_name, instruction, instruction_file, variables, allow_unresolved, include_hidden,
        ignore_gitignore, ignore_patterns, include_patterns, diff_flag, staged_flag, since_ref, patch_flag,
        output_format, no_separators, line_numbers, max_tokens, tokenizer, secrets_mode,
    )
    root, loaded, preset, text, unresolved = A.root, A.loaded, A.preset, A.text, A.unresolved
    col, budget, out = A.col, A.budget, A.out
    secrets_mode, max_tokens, change_mode, base = A.secrets_mode, A.max_tokens, A.change_mode, A.base

    # Built every run: the same evidence feeds --receipt, the advisory and the terminal line.
    rec = _make_receipt(A, tokenizer)
    nothing = receipt_mod.empty_notice(rec, A.change_mode)
    if nothing:
        _warn(nothing)
    if receipt_file:
        with open(receipt_file, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    if show_receipt:
        click.echo(receipt_mod.summarize(rec), err=True)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as fp:
            fp.write(out)
            if out and not out.endswith("\n"):
                fp.write("\n")
    printed = False
    if copy_to_clipboard:
        tool = clipboard.copy(out)
        if tool:
            tokens = count_tokens(out, tokenizer)
            label = "" if tokenizer != "heuristic" else " (estimate)"
            click.echo(
                "Copied to clipboard via %s: %s characters, about %s tokens%s"
                % (tool, format(len(out), ","), format(tokens, ","), label),
                err=True,
            )
            printed = True
        else:
            click.echo(click.style(
                "Warning: no clipboard tool found (tried pbcopy, clip, wl-copy, xclip, xsel); "
                "printing to stdout instead.", fg="yellow"), err=True)
    if not output_file and not printed:
        click.echo(out)
    # After the output, where the eye lands: a person at a terminal learns what went in, what
    # stayed out and why. Not printed when piped (the prompt is the product) or with --receipt.
    _heads_up(rec)
    if _stderr_is_tty() and not show_receipt:
        click.echo(receipt_mod.one_line(rec), err=True)


_ASK_PARAMS = [
    click.Option(["--provider"], type=click.Choice(list(presets_mod.ASK_PROVIDERS)), default=None,
                 help="openai = any OpenAI-compatible endpoint (incl. local servers); anthropic = Claude. "
                 "Also settable as `provider` in [ask]."),
    click.Option(["--model"], default=None,
                 help="Model name (no default: names change). Also $FILEFLOW_MODEL or `model` in [ask]."),
    click.Option(["--base-url"], default=None, metavar="URL",
                 help="API endpoint. Default: the provider's, or $OPENAI_BASE_URL / $ANTHROPIC_BASE_URL. "
                 "Never read from .files-to-prompt. https required, except localhost."),
    click.Option(["--api-key-env"], default=None, metavar="NAME",
                 help="Environment variable holding the key (default OPENAI_API_KEY / ANTHROPIC_API_KEY)."),
    click.Option(["--system"], default=None, help="Optional system prompt."),
    click.Option(["--max-output-tokens"], type=int, default=None, help="Cap on the answer length (default 4096)."),
    click.Option(["--timeout"], type=float, default=120.0, show_default=True, help="Overall seconds allowed for the call."),
    click.Option(["--yes", "assume_yes"], is_flag=True,
                 help="Skip the consent question when sending to a non-local host (needed when not interactive)."),
    click.Option(["--save"], type=click.Path(dir_okay=False), default=None,
                 help="Save the answer here (inside the project) as Markdown labelled provenance: generated."),
    click.Option(["--force"], is_flag=True, help="With --save: overwrite an existing file."),
]


def _interactive():
    """True when a person can answer a question (a seam so tests can simulate a terminal)."""
    return sys.stdin is not None and sys.stdin.isatty()


def _ask_callback(**kw):
    from . import ask as ask_mod
    import datetime

    root = os.getcwd()
    cfg, loaded = load_config(root, required=bool(kw["preset_name"]))
    section = presets_mod.ask_section(cfg) if loaded else {}
    provider = kw["provider"] or section.get("provider")
    if not provider:
        raise FileflowError("E_MODEL_CONFIG", "no provider chosen", "pass --provider openai|anthropic (or set provider in [ask])")
    model = kw["model"] or os.environ.get("FILEFLOW_MODEL") or section.get("model")
    if not model:
        raise FileflowError("E_MODEL_CONFIG", "no model chosen", "pass --model NAME (or set $FILEFLOW_MODEL, or model in [ask]); there is deliberately no default")
    base_url = ask_mod.resolve_base_url(provider, kw["base_url"], os.environ)
    key = ask_mod.resolve_key(provider, base_url, kw["api_key_env"], os.environ)  # fail before reading any file
    host = ask_mod.host_of(base_url)
    local = ask_mod.is_loopback_host(host)
    max_out = kw["max_output_tokens"] or section.get("max_output_tokens") or ask_mod.DEFAULT_MAX_OUTPUT
    tokenizer = kw["tokenizer"]

    A = _assemble(
        kw["paths"], kw["preset_name"], kw["instruction"], kw["instruction_file"], kw["variables"], kw["allow_unresolved"],
        kw["include_hidden"], kw["ignore_gitignore"], kw["ignore_patterns"], kw["include_patterns"], kw["diff_flag"],
        kw["staged_flag"], kw["since_ref"], kw["patch_flag"], kw["output_format"], kw["no_separators"], kw["line_numbers"],
        kw["max_tokens"], tokenizer, kw["secrets_mode"],
        # Data about to leave the machine defaults to fail-closed, and only the command line may relax it.
        secrets_default="warn" if local else "block", secrets_cli_only=not local, preloaded=(cfg, loaded),
    )
    if A.text is None or not A.text.strip():
        raise FileflowError("E_USAGE", "ask needs an instruction to send alongside the files",
                            "add --instruction \"...\", --instruction-file, or --preset <name>")

    tokens = count_tokens(A.out, tokenizer)
    est = " (estimate)" if tokenizer == "heuristic" else ""
    sent_files = len(A.budget.documents)
    summary = "%d file(s), about %s tokens%s, to %s (%s, model %s); secret guard: %s" % (
        sent_files, format(tokens, ","), est, host, provider, model, A.secrets_mode)
    if local:
        click.echo("Sending to a local model: " + summary, err=True)
    else:
        click.echo("About to SEND your project content to a third party: " + summary, err=True)
    # Before the decision, not after: one file that is most of a paid prompt is worth a pause.
    _heads_up(_make_receipt(A, tokenizer))
    if not local:
        if not kw["assume_yes"]:
            if _interactive():
                if not click.confirm("Send it?", default=False, err=True):
                    raise FileflowError("E_MODEL_CONSENT", "not sent: you declined", "nothing left your machine")
            else:
                raise FileflowError("E_MODEL_CONSENT", "not sent: this would send your files to %s and there is no one to confirm" % host,
                                    "re-run with --yes if that is what you want; nothing left your machine")

    result = ask_mod.call_model(provider, base_url, model, A.out, key, system=kw["system"],
                                max_output_tokens=max_out, timeout=kw["timeout"])
    click.echo(result.text)

    usage = []
    if isinstance(result.input_tokens, int):
        usage.append("in %s" % format(result.input_tokens, ","))
    if isinstance(result.output_tokens, int):
        usage.append("out %s" % format(result.output_tokens, ","))
    click.echo("answer from %s via %s%s%s" % (result.model or model, host,
               " (tokens as reported by the provider: %s)" % ", ".join(usage) if usage else "",
               "; stop: %s" % result.stop_reason if result.stop_reason else ""), err=True)
    if result.stop_reason in ("max_tokens", "length"):
        click.echo(click.style("Warning: the answer was cut off at the output limit; raise --max-output-tokens.", fg="yellow"), err=True)
    click.echo("Note: a model's answer is generated text, not verified fact. Check it before relying on it.", err=True)

    if kw["show_receipt"] or kw["receipt_file"]:
        rec = _make_receipt(A, tokenizer)
        rec["model_call"] = {
            "provider": provider, "model": result.model or model, "host": host, "local": local,
            "input_tokens": result.input_tokens if isinstance(result.input_tokens, int) else None,
            "output_tokens": result.output_tokens if isinstance(result.output_tokens, int) else None,
            "stop_reason": result.stop_reason, "prompt_sha256_12": ask_mod.sha12(A.out), "answer_sha256_12": ask_mod.sha12(result.text),
        }
        rec["ledger"]["verified"].append("the model call returned a well-formed response")
        rec["ledger"]["assumed"].append("token usage in model_call is as reported by the provider")
        rec["ledger"]["unknown"].append("whether the answer is correct")
        if not local:
            rec["ledger"]["unknown"].append("what %s does with the data you sent (retention, training): see its terms" % host)
        if kw["receipt_file"]:
            with open(kw["receipt_file"], "w", encoding="utf-8") as fp:
                fp.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        if kw["show_receipt"]:
            click.echo(receipt_mod.summarize(rec), err=True)

    if kw["save"]:
        from .provenance import build_frontmatter

        target = presets_mod.confined(root, kw["save"], "--save path")
        shown = os.path.relpath(target, root).replace(os.sep, "/")
        if os.path.exists(target) and not kw["force"]:
            raise FileflowError("E_IO", "%s already exists (the answer above was NOT saved)" % shown, "use --force to replace it, or pick another path")
        fields = [("provenance", "generated"), ("generated_at", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("provider", provider), ("model", result.model or model), ("prompt_sha256_12", ask_mod.sha12(A.out))]
        if isinstance(result.input_tokens, int):
            fields.append(("input_tokens", result.input_tokens))
        if isinstance(result.output_tokens, int):
            fields.append(("output_tokens", result.output_tokens))
        if result.stop_reason:
            fields.append(("stop_reason", result.stop_reason))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(build_frontmatter(fields) + "\n" + result.text.rstrip("\n") + "\n")
        click.echo("saved %s  (provenance: generated)" % shown, err=True)


ask_cmd = click.Command(
    "ask",
    callback=_ask_callback,
    params=[p for p in prompt.params if p.name not in ("output_file", "copy_to_clipboard")] + _ASK_PARAMS,
    help="""
    Send the bundle (files + instruction) to a model and print its answer. Opt-in; single-shot.

    \b
        fileflow ask --provider openai --model <name> --preset review --diff
        fileflow ask --provider anthropic --model <name> src/ --instruction "Find bugs"
        fileflow ask --provider openai --base-url http://localhost:11434/v1 --model <name> README.md --instruction "Summarise"

    This sends your files to the endpoint you choose. There are no tools, no file writes and no
    shell: prompt in, answer out. Non-local hosts need your consent (--yes when not interactive)
    and the secret guard defaults to `block`. Your key is read from the environment and never
    printed. The base URL comes from the command line or your environment, never from .files-to-prompt.
    """,
)
cli.add_command(ask_cmd)


@cli.command("presets")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def presets_cmd(as_json):
    """List the presets defined in .files-to-prompt (name, budget, what they ask)."""
    root = os.getcwd()
    cfg, loaded = load_config(root, required=True) if os.path.isfile(
        os.path.join(root, ".files-to-prompt")) else ({}, False)
    table = presets_mod.all_presets(cfg) if loaded else {}
    if as_json:
        click.echo(json.dumps(table, indent=2, ensure_ascii=False))
        return
    if not table:
        click.echo("No presets defined. Add a [presets.<name>] table to .files-to-prompt "
                   "(or run `fileflow init` once it is available).")
        return
    width = max(len(n) for n in table)
    for name in sorted(table):
        p = table[name]
        bits = []
        if p.get("max_tokens"):
            bits.append("budget %s" % format(p["max_tokens"], ","))
        if p.get("paths"):
            bits.append(", ".join(p["paths"]))
        click.echo("%s  %s%s" % (name.ljust(width), p.get("description", "(no description)"),
                                 ("   [" + "; ".join(bits) + "]") if bits else ""))
    click.echo("\nRun one with: fileflow --preset <name>", err=True)


@cli.command("init")
@click.option(
    "--template",
    "-t",
    type=click.Choice(["minimal", "web-sprint", "harness"]),
    default="minimal",
    show_default=True,
    help="What to scaffold. `fileflow init --list` describes them.",
)
@click.option("--list", "list_templates", is_flag=True, help="Describe the templates and exit.")
@click.option("--dir", "dest", default=".", type=click.Path(file_okay=False), help="Where to write (created if missing).")
@click.option("--force", is_flag=True, help="Overwrite files that already exist (default: skip them).")
@click.option("--no-claude", is_flag=True, help="harness: skip CLAUDE.md and .claude/settings.json.")
def init_cmd(template, list_templates, dest, force, no_claude):
    """
    Scaffold a project: config + presets, notes, or an agent harness.

    Existing files are never overwritten unless you pass --force. Blanks you
    must fill are marked <<FILL: ...>>; `fileflow check` finds the ones left.
    """
    from .scaffold import TEMPLATES, init_project

    if list_templates:
        for name, spec in TEMPLATES.items():
            click.echo("%-11s %s" % (name, spec["description"]))
        return
    created, skipped, overwritten = init_project(dest, template, force=force, claude=not no_claude)
    for rel in created:
        click.echo("  created      " + rel)
    for rel in overwritten:
        click.echo("  overwritten  " + rel)
    for rel in skipped:
        click.echo("  skipped      %s  (already exists; use --force to replace)" % rel)
    if not (created or overwritten):
        click.echo("Nothing to do: every file already exists.")
        return
    prefix = "" if os.path.abspath(dest) == os.path.abspath(".") else "cd %s && " % dest
    click.echo("\nNext: %s%s" % (prefix, TEMPLATES[template]["next"]))


@cli.command("fetch")
@click.argument("urls", nargs=-1, required=True)
@click.option("--into", default="content", show_default=True, help="Directory (inside the project) to save into.")
@click.option("--name", help="File name stem (default: derived from the URL). Only with a single URL.")
@click.option("--force", is_flag=True, help="Overwrite a file that already exists.")
@click.option("--allow-private", is_flag=True,
              help="Allow addresses on your own machine/network (default: refused, to protect local services and cloud metadata).")
@click.option("--max-bytes", default=5 * 1024 * 1024, show_default=True, type=int, help="Stop if a response is larger than this.")
@click.option("--timeout", default=20.0, show_default=True, type=float, help="Overall seconds allowed per URL.")
def fetch_cmd(urls, into, name, force, allow_private, max_bytes, timeout):
    """
    Save web pages as readable Markdown files, with their origin recorded.

    \b
        fileflow fetch https://example.com/essay --into content/essays

    With `fileflow ask`, this is one of only two commands that talk to the network, and neither
    is part of `fileflow serve`. Each file starts with front matter -- source_url, fetched_at,
    provenance: observed -- so later prompts can say where the text came from.
    Fetched text is untrusted data: it can contain instructions aimed at a model. Check
    permissions before republishing it.
    """
    import datetime

    from .fetch import fetch_url, page_to_document, slug_for

    if name and len(urls) > 1:
        raise FileflowError("E_USAGE", "--name works with a single URL", "fetch the URLs one at a time, or omit --name")
    if name and (os.sep in name or "/" in name or name in (".", "..") or name.startswith(".")):
        raise FileflowError("E_USAGE", "--name must be a plain file name (no directories)", "use --into to choose the directory")
    root = os.getcwd()
    dest_dir = presets_mod.confined(root, into, "--into directory")
    if allow_private:
        click.echo(click.style("Note: --allow-private lets fetch reach addresses on your own machine/network.", fg="yellow"), err=True)
    for url in urls:
        got = fetch_url(url, max_bytes=max_bytes, timeout=timeout, allow_private=allow_private)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        text, info = page_to_document(got, url, now)
        stem = name or slug_for(got.url)
        stem = stem[:-3] if stem.endswith(".md") else stem
        target = os.path.join(dest_dir, stem + ".md")
        shown = os.path.relpath(target, root).replace(os.sep, "/")
        if os.path.exists(target) and not force:
            raise FileflowError("E_IO", "%s already exists" % shown, "use --force to replace it, or --name to pick another name")
        os.makedirs(dest_dir, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        extra = []
        if info["invisible_removed"]:
            extra.append("%d invisible character(s) removed" % info["invisible_removed"])
        if info["hidden_elements"]:
            extra.append("%d hidden element(s) removed" % info["hidden_elements"])
        click.echo("saved %s  (%s characters, provenance: observed%s)" % (
            shown, format(info["chars"], ","), ("; " + "; ".join(extra)) if extra else ""))
        if info["short"]:
            click.echo(click.style("Warning: only %s characters of text were found; check the file before relying on it." % format(info["chars"], ","),
                                   fg="yellow"), err=True)
    click.echo("Note: fetched text is untrusted data, not instructions; check permissions before republishing.", err=True)
    click.echo("Next: fileflow %s --instruction \"...\" --receipt" % shown)


@cli.command("check")
@click.option("--root", default=".", type=click.Path(exists=True, file_okay=False), help="Project directory to check.")
@click.option("--strict", is_flag=True, help="Treat warnings as failures (for CI).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable report.")
@click.pass_context
def check_cmd(ctx, root, strict, as_json):
    """
    Verify .files-to-prompt and the agent harness (AGENTS.md, docs links, skills, permissions).

    Exit 0 when there are no errors (and no warnings with --strict), else 2.
    Errors are reserved for things that definitely break; guidance is a warning.
    """
    from .check import format_report, run_checks

    report = run_checks(root, strict=strict)
    if as_json:
        click.echo(json.dumps(report.to_dict(strict), indent=2, ensure_ascii=False))
    else:
        click.echo(format_report(report, strict))
    if not report.ok(strict):
        ctx.exit(2)


@cli.command()
@click.option(
    "--root",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Project directory to serve. The server is pinned to it: clients can "
    "narrow the scope to a sub-directory but never reach outside it.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", default=8090, show_default=True, help="Bind port")
@click.option(
    "--allow-remote",
    is_flag=True,
    help="Allow binding to non-localhost (exposes local files to the network). "
    "A random access token is generated unless you pass --token or --no-token.",
)
@click.option(
    "--allowed-host",
    "allowed_hosts",
    multiple=True,
    help="Extra Host header value to accept (repeatable). Localhost is always "
    "accepted; with --allow-remote any host is accepted unless this is given.",
)
@click.option(
    "--token",
    envvar="FILEFLOW_TOKEN",
    default=None,
    help="Require this access token (Authorization: Bearer, cookie or ?token=).",
)
@click.option(
    "--no-token",
    is_flag=True,
    help="With --allow-remote: serve without authentication (anyone who can "
    "reach the port can read the project). Demo folders only.",
)
def serve(root, host, port, allow_remote, allowed_hosts, token, no_token):
    """
    Start a local web server for the fileflow web client.

    Serves the bundled web client and a read-mostly /api backed by the real
    filesystem (tree, prompt, config, watch). Binds to 127.0.0.1 by default —
    files never leave the machine. Requires the 'server' extra:
    `pip install -e ".[server]"`.
    """
    from .server import guard, security
    from .server.app import build_app
    import uvicorn

    if not security.is_allowed_host(host, allow_remote):
        raise click.UsageError(
            f"Refusing to bind to {host}. Local files are exposed to the network; "
            "pass --allow-remote explicitly if you understand the risk."
        )

    if no_token and token:
        raise click.UsageError("--token and --no-token are mutually exclusive.")
    generated = False
    if allow_remote:
        if no_token:
            click.echo(
                click.style(
                    "WARNING: serving WITHOUT authentication. Anyone who can reach "
                    f"{host}:{port} can read every file under the project root.",
                    fg="red",
                ),
                err=True,
            )
            token = None
        elif not token:
            import secrets as _secrets

            token = _secrets.token_urlsafe(16)
            generated = True
        hosts = list(allowed_hosts) or None  # remote: any Host unless pinned
    else:
        hosts = sorted(set(guard.LOCAL_HOSTS) | {host.lower()} | {h.lower() for h in allowed_hosts})

    root = os.path.abspath(root)
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if not os.path.isdir(web_dir):
        click.echo(
            click.style(
                "Warning: bundled web client not found; serving the API only.", fg="yellow"
            ),
            err=True,
        )
        web_dir = None

    app = build_app(
        static_dir=web_dir, project_root=root, allowed_hosts=hosts, token=token, enforce_origin=True
    )
    click.echo(f"fileflow serve → http://{host}:{port}  (root: {root})")
    if token:
        shown = "generated" if generated else "required"
        click.echo(f"Access token ({shown}): {token}")
        click.echo(f"Open: http://{host}:{port}/?token={token}")
    click.echo("Press Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    cli()


if __name__ == "__main__":
    main()
