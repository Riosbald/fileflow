# fileflow

Turn a directory of files into a single prompt that you can feed to a large
language model.

`fileflow` walks a local file tree and prints every file in a structured,
deterministic format — each file preceded by its path and separated by `---`
delimiters. Pipe the result straight into any LLM CLI (such as
[`llm`](https://github.com/simonw/llm)) to give the model the full context of
a project instead of a few hand-pasted snippets.

## Features

- Recursively convert a directory into a prompt
- Pass any number of files and directories in a single command
- Respect `.gitignore` rules automatically
- Exclude hidden files by default
- Exclude additional glob patterns with `--ignore-patterns`
- Output to a file, or as `default`, `xml`, or `json`
- Add line numbers with `--line-numbers`
- A browser-based web client (`fileflow/web/`) with the same engine

## Installation

```bash
pip install -e .
```

This installs the `fileflow` command line tool and its single dependency,
`click`. The tool supports Python 3.8 and later.

## Quick start

```bash
fileflow path/to/directory
```

For example, given this project structure:

```text
project/
├── README.md
├── src/
│   ├── app.py
│   └── utils.py
└── tests/
    └── test_app.py
```

Running:

```bash
fileflow project/src/
```

produces:

```text
project/src/app.py
---
def main():
    return 1

---
project/src/utils.py
---
VALUE = 42

---
```

Each file is preceded by its path, followed by a `---` separator, then the
file contents, then a closing `---` separator.

## Pipe to an LLM

```bash
fileflow src/ | llm -m opus --system 'refactor this code'

# With conversation continuity
fileflow src/ | llm -m opus -s 'add logging'
llm -c 'also add error handling'
```

## Options

### `--include-hidden`

Include files and directories that start with a dot. By default these are
excluded.

```bash
fileflow path/ --include-hidden
```

### `--ignore-gitignore`

Ignore `.gitignore` files and include every file, even those that a
`.gitignore` would normally exclude.

```bash
fileflow path/ --ignore-gitignore
```

By default `.gitignore` rules are applied automatically, both from the root
of each directory you pass and from nested `.gitignore` files found while
walking. Supported semantics: comment and blank lines, `*` and `?` glob
patterns, trailing `/` (directory-only), leading `/` (anchored to the
`.gitignore`'s directory), and `!` negation (last match wins). Unsupported
constructs (`**` and `[...]` character classes) are ignored. Rules apply to
the whole subtree — a `.gitignore` inside a subdirectory anchors to that
subdirectory.

### `--ignore-patterns`

Exclude files matching additional glob patterns. May be repeated.

```bash
fileflow src/ --ignore-patterns '*.md' '*.txt'
fileflow src/ --ignore-patterns '*.pyc' --ignore-patterns '__pycache__'
```

Patterns are applied to file and directory **names** with `fnmatch`, never to
paths: `a.txt` removes that name in every folder, while `sub/a.txt` or
`node_modules/` can never match. fileflow warns when you write one (and says which
name to use). Directory matches prune the whole subtree.

### `--output-file`

Write the output to a file instead of stdout.

```bash
fileflow src/ --output-file context.txt
```

### `--format`

Select the output format. One of `default`, `xml`, or `json`.

```bash
fileflow src/ --format xml
fileflow src/ --format json
```

XML output wraps each document in `<document index="1">` tags:

```text
<documents>
<document index="1">
<source>src/app.py</source>
<document_content>
def main():
    return 1
</document_content>
</document>
</documents>
```

JSON output is a list of objects with `path` and `content` keys:

```json
[
  {
    "path": "src/app.py",
    "content": "def main():\n    return 1\n"
  }
]
```

### `--no-separators`

Omit the `---` separator lines in the default output.

```bash
fileflow src/ --no-separators
```

### `--line-numbers`

Prefix each line of every file with its line number.

```bash
fileflow src/ --line-numbers
```

### `--max-tokens`

Trim the output to an approximate token budget. Files are kept whole while
they fit; the first file that would exceed the budget is truncated and
everything after it is dropped. `0` (the default) disables trimming.

```bash
fileflow src/ --max-tokens 8000
```

### `--tokenizer`

Select how tokens are counted for `--max-tokens`. `heuristic` (the default) is
a cheap offline estimate (~4 characters per token). Any other value is
interpreted as a `tiktoken` model or encoding name (e.g. `cl100k_base`), which
requires the optional `tiktoken` dependency.

```bash
pip install tiktoken
fileflow src/ --max-tokens 8000 --tokenizer cl100k_base
```

### Size limit

Files over **1 MiB** are skipped by default. A lockfile, log or data dump that large is almost
never what you mean to paste into a model, reading it whole costs memory, and it would make the
prompt mostly noise. A skipped file is never silent: it is warned about with its real size and how to
include it, and it appears as `TOO_LARGE` in the receipt and in the web client's "left out" list.

```bash
FILEFLOW_MAX_FILE_BYTES=0 fileflow src/         # no limit
FILEFLOW_MAX_FILE_BYTES=5242880 fileflow src/   # a different limit: 5 MiB
```

A value that is not a whole number of bytes (`10MB`) is reported and the default stays in force.
*(Behaviour change: earlier builds had no default limit.)*

### Multiple paths

Pass any number of files and directories. Files are included directly,
directories are traversed recursively, and mixed paths are processed in order.

```bash
fileflow README.md src/ tests/
```

Invalid paths raise a `click.BadParameter` error.

## Config file (`.files-to-prompt`)

A `.files-to-prompt` TOML file in the current directory supplies defaults,
shared by the CLI, `fileflow serve`, and the web client. Precedence is
**CLI flags > preset > config file > defaults**, so any explicit flag overrides it.

```toml
[output]
format = "default"        # default | xml | json
separators = true
line_numbers = false
max_tokens = 8000

[include]
patterns = []             # only include files matching these (e.g. ["*.py", "docs/*"]); empty = no filter

[exclude]
patterns = ["*.min.js", "__pycache__"]

[ignore]
gitignore = true          # respect .gitignore
hidden = false            # include hidden files
```

The web client (in live-server mode) reads and writes this file from its
options, so the recipe carries across the CLI and the browser.

- **Edits are picked up live.** With "Live server" on, saving `.files-to-prompt`
  in your editor reloads the controls in the open browser tab (you'll see a
  *".files-to-prompt reloaded"* toast) and the prompt is regenerated with the new
  settings. The file's own changes made by the UI do not trigger a reload.
- **A broken file never wipes your settings.** If the file is invalid TOML (for
  example mid-edit), the web client keeps your current settings and says so; the
  CLI prints a warning to stderr and carries on with its flags/defaults. Fix the
  file and the live reload picks it up.
- **Saving from the web UI keeps your presets.** The UI only edits
  `[output]`, `[include]`, `[exclude]` and `[ignore]`; `version`, `[secrets]`,
  `[presets.*]` and any keys it doesn't know are carried over untouched. Comments
  are **not** preserved (the file is re-generated), so if you keep hand-written
  comments, edit it in your editor rather than toggling options in the browser.
- `max_tokens` can be any non-negative integer (the UI adds an entry for values
  outside its presets); `0` means no budget.

## Presets, instructions & variables

A **preset** is a saved, bounded task: which paths, what to ask, how big. Define
it once in `.files-to-prompt`, run it with no other flags.

```toml
version = 1

[presets.hero]
description = "Hero section only"
paths = ["components/hero", "notes/project-notes.txt"]
max_tokens = 12000
format = "xml"
instruction = "Improve the hero section only. Match the spacing in design-references."

[presets.frontier-audit]
description = "Reflective prompt with blanks"
paths = ["notes"]
[presets.frontier-audit.prompt]            # rendered as ROLE:/CONTEXT:/TASK:/... lines
role        = "Act as a mentor who has seen hundreds of exceptional performers."
context     = "I am a {{role}} working in {{domain}} for {{years}} years."
task        = "Identify the gap between what I do now and great work in my field."
constraints = "No generic advice. Be specific to my domain."
[presets.frontier-audit.vars]
years = "3"                                  # default; override with --var
```

```bash
fileflow presets                                   # list them
fileflow --preset hero                             # run one (same as -p hero)
fileflow -p hero --format json                     # flags still win over the preset
fileflow -p frontier-audit --var domain="backend engineering" --var role=developer
fileflow notes/ --instruction "Summarise the open questions."   # no preset needed
fileflow notes/ --instruction-file prompts/review.md
```

- **Precedence** (highest first): command-line flags, the preset, the root config,
  built-in defaults. `exclude_patterns` are additive. CLI paths replace a preset's
  `paths`.
- **Where the instruction goes.** Always *outside* the files section: default
  format → a `# Task` section then `# Files`; XML → `<task_instructions>` before
  `<documents>`; JSON → an object `{"instructions": ..., "documents": [...]}`
  (without an instruction JSON stays a bare array). The web client renders the
  same bytes (pinned by the golden corpus).
- **Variables are plain text substitution**: `{{name}}` is replaced once; there is
  no logic, no includes, and a value is never re-scanned. A `{{name}}` still in the
  instruction is an **error** (`E_PLACEHOLDER_UNRESOLVED`, exit 2) because pasting a
  half-filled template is the commonest wasted prompt; `--allow-unresolved` keeps
  them as written. File *contents* are never scanned for placeholders.
- **Paths in a preset are confined to the project.** Anything in a config file may
  come from a repository you just cloned, so `../x`, absolute paths outside the
  project, and symlinks that lead out are refused (`E_PATH_OUTSIDE_ROOT`, exit 3).
  `--instruction-file` on the command line is *your* choice and may point anywhere;
  a preset's `instruction_file` may not.
- Set exactly one of `instruction`, `instruction_file` or a `[presets.<n>.prompt]`
  table. Unknown keys are tolerated (forward compatible); wrong types are errors.
  A config with a newer `version` than this fileflow understands is refused.
- On Python < 3.11 the config is parsed with `tomli` (installed automatically).

## Secret guard

fileflow scans the **exact text it is about to emit** for high-confidence
credentials: private-key headers, AWS/GitHub/Slack/Stripe/Google/OpenAI-style
keys, and `SECRET=`/`TOKEN=`/`PASSWORD=`-style assignments whose value is not an
obvious placeholder (`changeme`, `your_key_here`, `${VAR}`).

| `--secrets` | Behaviour |
|---|---|
| `warn` (default) | keep the file, report `path: rule (line N)` on stderr |
| `exclude` | leave files with findings out of the prompt, report them |
| `block` | fail closed: **exit 3, emit nothing** (`E_SECRET_FOUND`) |
| `off` | don't scan |

Set it per run (`--secrets block`), per preset (`secrets = "block"`) or for the
project (`[secrets] mode = "block"`). **A finding never prints the secret value.**
This *reduces* risk; it does not prove absence — the receipt lists the scan under
"assumed", never "verified". `/api/prompt` takes the same `secrets` parameter and
returns the findings (paths and rule names only) in `meta.secrets`; the web client
shows them next to the token count.

## Typos in `.files-to-prompt`

Unknown keys are ignored on purpose (a newer config must still load on an older fileflow), which
means a typo such as `formt = "xml"` silently does nothing. So: a normal run prints a warning when
an unknown key is a near-miss of a real one (`did you mean 'format'?`), and `fileflow check` lists
**every** unknown key (`C_CONFIG_UNKNOWN_KEY`, a warning, so `--strict` fails on it). When nothing at
all would be sent (wrong folder, everything ignored) fileflow says so and names the flag that helps.

A `.files-to-prompt` that cannot be read (bad TOML) or whose settings have the wrong type
(`format = 7`, `separators = "yes"`) is an **error**: exit 2, nothing sent, with the key and the
line. It used to warn and carry on, which silently dropped settings such as
`[secrets] mode = "block"`. `fileflow check` lists every problem, not just the first. (`fileflow serve`
stays tolerant on purpose: its UI is how you repair the file.)

## What fileflow tells you after every run

At a terminal, one line follows the output (nothing is added when you pipe or redirect,
so scripts see the same bytes as before):

```text
fileflow: 5 files, about 100,044 tokens (estimate); left out 6 (1 not text, 2 ignored by .gitignore, 3 hidden). Details: --receipt
```

If a single file is at least 5,000 tokens **and** at least half of the prompt, you also get
a `Heads-up:` that names it and the exact flag that removes it (it never changes what is
sent). `ask` shows the same heads-up *before* the consent question. In `fileflow serve`,
the page lists what was left out and why, and offers the same one-click "Leave it out".

## Context receipt

```bash
fileflow -p hero --receipt                  # human summary on stderr
fileflow -p hero --receipt-file receipt.json
fileflow -p hero --copy --receipt           # clipboard + receipt, nothing noisy on stdout
```

```text
Context receipt - preset 'analyze'
  sent       7 files, about 146 tokens (estimate) of 8,000 budget
  left out   GITIGNORED 3, HIDDEN 3, PATTERN 2
  assumed    token counts are a ~4 characters/token heuristic, not a real tokenizer (it can be well off, especially for code and non-English text); secret scan is pattern-based: finding nothing is not proof that nothing is there
  unknown    the target model's real context window; whether the model will follow the instruction
```

The JSON receipt lists every file the walker met **exactly once** as `included`,
`truncated` or `excluded` (an excluded directory appears once as `dir/`), each
excluded entry with a reason from a closed set: `HIDDEN, GITIGNORED, PATTERN,
SYMLINK_OUTSIDE_ROOT, SYMLINK_DIR, BINARY, TOO_LARGE, UNREADABLE, SECRET_BLOCKED,
TOKEN_BUDGET`. Its `ledger` separates `verified`, `assumed`, `unknown` and
`failed_checks`. It is deterministic (sorted, no timestamps, no absolute paths), so
it can be committed and diffed. What a model does with a bundle is always listed as
unknown, because fileflow only *packages* context; the opt-in `fileflow ask` command can send a bundle to a model, and
its receipt then also records the call (still listing the correctness of the answer as unknown).

`--copy` uses whichever of `pbcopy`, `clip`, `wl-copy`, `xclip`, `xsel` is
installed; if none is, it prints to stdout instead.

**Errors** have a class, an exit code and a next step: `Error [E_PRESET_UNKNOWN]:
no preset named 'her'` / `Next: fileflow presets (did you mean 'hero'?)`. Exit
codes: `1` I/O, `2` usage or config, `3` security refusal, `4` network.

> Not yet: the web client has no preset picker or receipt panel (planned, R3),
> and its static (offline) mode does not scan for secrets.

## Fetch a page into your project (`fetch`)

```bash
fileflow fetch https://example.com/essay --into content/essays
```

Saves the page as readable Markdown with its **origin recorded** in front matter (`source_url`, `fetched_at`, `sha256_12`,
`provenance: observed`). Later prompts then say where each piece of text came from. Only `fetch` and `ask` talk to the network;
neither is part of `fileflow serve`, and `prompt` never touches it.

**Fetched pages are untrusted data.** A page can hide instructions aimed at a model. `fetch` removes the usual hiding places
(`display:none` / `hidden` / `aria-hidden` elements, HTML comments, zero-width and bidi characters, the Unicode "tag" block) and reports
how many it removed, but visible text can still contain instructions, so treat it as data, never as commands. Check permissions before
republishing anything you fetch.

It is built to be safe to point at a URL you don't trust:

| Defence | What it does |
|---|---|
| Address policy | the hostname is resolved by fileflow and **every** address must be globally routable: loopback, private, link-local (`169.254.169.254`), CGNAT, multicast, reserved, IPv4-mapped IPv6, NAT64, 6to4 and Teredo are refused. `http://2130706433/`, `0x7f.1`, `[::1]` are caught because the *resolved* address is checked, not the spelling. `--allow-private` lifts only this rule, for your own intranet on purpose. |
| No DNS rebinding | the socket connects to the address that was validated, not to the name again; HTTPS still verifies the certificate against the original hostname. |
| Redirects | followed by hand, at most 3, each hop re-validated; `https`→`http` downgrades refused. |
| URL shape | `http`/`https` only (no `file:`, `ftp:`, `data:`), no `user:pass@`, no control characters. |
| Resource caps | 5 MiB (`--max-bytes`), 20 s overall (`--timeout`, enforced by a watchdog so a server that drips bytes can't hold it open), text content types only, no compressed responses. |
| No ambient authority | no cookies, no credentials, no `Referer`; it ignores `HTTP(S)_PROXY` (it connects directly). |

`--into` and `--name` are confined to the project, and an existing file is never overwritten without `--force`.

Extraction prefers `<article>` over `<main>` over the whole page, collapses the doubled link lines sites render, **refuses anti-bot / JavaScript
challenge pages** (`E_FETCH_EXTRACT`; nothing is written) and warns when a page yields under 600 characters. A site that needs a real browser
will not work with `fetch`: save its text yourself and add a front matter label.

## Only what matters: `--include`, `--diff`, `--staged`, `--since`, `--patch`

```bash
fileflow . --include '*.py' --include 'docs/*'     # only matching files (or [include] patterns = [...] in config)
fileflow --diff                                    # only files changed since HEAD (+ untracked, not ignored)
fileflow --staged                                  # only what is staged
fileflow --since main --patch                      # changed since main, plus the unified diff as one extra document
```

* **`--include`** uses `*` (any run of characters, including `/`) and `?` only, case-sensitive; a pattern without `/` matches the file name,
  one with `/` matches the path from the directory you passed. Exclusions (hidden, `.gitignore`, `--ignore-patterns`) still win; files you
  name explicitly are never filtered; directories are always walked. An include list **replaces** rather than adds
  (CLI > preset `include_patterns` > `[include]` in config). The web client reads and preserves `[include]` too.
* **`--diff` / `--staged` / `--since REF` / `--patch`** restrict the selection to changed files (deleted files have nothing to read). In a
  preset: `diff = true`, `staged = true`, `patch = true`. The receipt marks everything else `NOT_CHANGED` and records the mode.
* The **patch** is scanned for secrets like any other text. A removed line can still leak one, so `--secrets block` stops on it.
* **git is run defensively**: no shell, no pager, and `core.fsmonitor`, external diff drivers and textconv are disabled on the command line;
  `--since` is validated and resolved to a full SHA before it reaches `git diff`, so a ref like `--output=/tmp/x` is refused. This cannot make
  git safe inside a repository whose `.git/config` an attacker controls (config-defined filters can still run); don't run `--diff` in a
  checkout you don't trust.

## Ask a model (`ask`) — opt-in, single-shot

```bash
export OPENAI_API_KEY=...   # or ANTHROPIC_API_KEY
fileflow ask --provider openai    --model <name> --preset review --diff --save notes/review.md
fileflow ask --provider anthropic --model <name> src/ --instruction "Find bugs"
fileflow ask --provider openai --base-url http://localhost:11434/v1 --model <name> README.md --instruction "Summarise"   # local model
```

`ask` builds **exactly the bundle `prompt` would print** (presets, include, diff, instruction, receipt, secret guard, budget) and sends it to
the endpoint you choose, then prints the answer. It is **prompt in, answer out**: no tools, no file writes, no shell, no memory, no loop.
It is a separate, opt-in command; the rest of fileflow stays deterministic and key-free, and the server has no model endpoint.

* **Providers.** `openai` = any OpenAI-compatible `/chat/completions` endpoint (OpenAI, hosted look-alikes, and local servers such as
  Ollama, llama.cpp, vLLM); `anthropic` = the Messages API. There is deliberately **no default model** (names change): pass `--model`,
  or set `$FILEFLOW_MODEL` or `model` in `[ask]`. `[ask]` in `.files-to-prompt` may set `provider`, `model`, `max_output_tokens`.
* **Where your files go is your decision, never a repository's.** The base URL comes from `--base-url` or `$OPENAI_BASE_URL` /
  `$ANTHROPIC_BASE_URL`, and the key's variable name from `--api-key-env`; **neither is read from `.files-to-prompt`**, so a cloned repo
  can't point your key at its own server (`check` warns if they appear there, and errors on a stored `api_key`). `https` is required
  except for localhost; `user:pass@` URLs and an empty `--base-url` are refused.
* **Consent.** Sending to a non-local host prints what will leave (files, estimated tokens, host) and asks; when not interactive it
  refuses unless you pass `--yes`. Local models need no consent.
* **Secrets fail closed.** For a non-local destination the secret guard defaults to `block`, and **only the command line** can relax it
  (`--secrets exclude|warn|off`): `.files-to-prompt` and presets cannot.
* **Your key** is read from the environment, sent only in the provider's documented header, never printed, and scrubbed from errors.
  Redirects are refused (Python's default client would forward your key to the redirect target). Responses are size-capped.
* **Answers are generated text.** `--save PATH` writes Markdown labelled `provenance: generated` (provider, model, prompt hash, token usage),
  so a later `prompt` reports it as lower-trust. `--receipt` adds a `model_call` record and lists *whether the answer is correct* (and, for
  a remote host, what the provider does with your data) under **unknown**. Token usage is as reported by the provider.
* Errors are classified: `E_MODEL_NOKEY` / `_CONFIG` / `_CONSENT` (exit 2), `E_MODEL_AUTH` / `_RATE` / `_NETWORK` / `_TIMEOUT` / `_RESPONSE` (exit 4).
  fileflow never retries (a retry costs money).

> **Not verified against a live provider.** Both adapters follow the providers' documented request/response formats and are tested against
> local mock servers only. Treat your first real call as a test, with a throwaway key and a small bundle.

## Agent harness: `init` and `check`

An AI coding agent is **a model plus a harness**: everything that is not the model - what it reads, what it may do, how its work is
checked, when it stops. fileflow does not run agents (`ask` is a single prompt-in/answer-out call, not an agent loop); it can **scaffold** the repository-resident parts of a
harness and **verify** them mechanically. The reasoning, the sources, and what could *not* be verified are in
[`docs/research/harness-engineering.md`](docs/research/harness-engineering.md).

```bash
fileflow init --list                      # minimal | web-sprint | harness
fileflow init --template harness          # AGENTS.md map, docs/, Harness Spec, starter permissions
fileflow check                            # lint config + harness; exit 2 on errors
fileflow check --strict                   # warnings fail too (use in CI)
fileflow check --json                     # machine-readable report
```

`init` never overwrites an existing file (use `--force`) and ends by printing exactly one next step. Blanks you must decide are written
`<<FILL: ...>>`; `check` finds the ones you left. The **harness** template creates:

| File | What it is |
|---|---|
| `AGENTS.md` | a ~100-line **map** with pointers into `docs/` (OpenAI's "table of contents, not encyclopedia") |
| `CLAUDE.md` | one line, `@AGENTS.md`, because Claude Code does not read AGENTS.md natively |
| `docs/HARNESS.md` | the **Harness Spec**: the 8 components (instructions, context, skills, memory, permissions, tools, checks, loop) as one fill-in page, with explicit *never / ask first / enforced by*, *must pass*, and *done / stop-and-ask / max attempts* clauses |
| `.claude/settings.json` | a conservative starter `permissions.deny` list in Claude Code's documented syntax |
| `ARCHITECTURE.md`, `docs/...` | the knowledge-base skeleton (index, quality scorecard, design decisions, exec plans) |
| `.files-to-prompt` | presets `onboard` (hand an agent the map) and `harness-review` |

**Advisory versus enforced.** A permission written in prose is a request; a deny rule, a container or a failing check is a fact (Claude
Code's docs: rules are enforced by the tool, not the model). The starter deny rules match command *patterns* and can be bypassed by a
differently written command - a seat belt, not a sandbox. For unattended runs use a container or VM. `check` warns when permissions
exist only in prose.

**What `check` verifies** (errors are reserved for things that definitely break; guidance is a warning; every limit cites its source and
can be tuned under `[harness]` in `.files-to-prompt`):

| Code | Level | Check | Source of the number |
|---|---|---|---|
| `H_LINK_DANGLING` | error in `AGENTS.md`/`CLAUDE.md`/spec, warn in `docs/` | relative links resolve | OpenAI: linters validate the KB is "cross-linked" |
| `H_CODEX_BYTES` | **error** | root-to-directory `AGENTS.md` chain under 32 KiB (one file per directory, `AGENTS.override.md` wins) | Codex truncates silently at `project_doc_max_bytes` |
| `H_LONG` / `H_VERY_LONG` | warn | over 100 lines (map guidance) / over 200 lines | OpenAI ~100-line map; Claude Code's 200-line warning |
| `H_IMPORT_*` | warn | `@path` imports exist, no cycles, at most 4 hops | Claude Code docs |
| `H_DRIFT` | warn | `CLAUDE.md` imports `AGENTS.md` instead of copying it | two sources of truth drift |
| `H_SPEC_*`, `H_UNFILLED` | warn | all 8 sections present; permissions, checks and the loop are filled in | - |
| `H_PERMS_PROSE_ONLY`, `H_BYPASS_DEFAULT` | warn | permissions only in prose; `bypassPermissions` as default | Claude Code docs |
| `H_SETTINGS_INVALID`, `H_SETTINGS_RULE_IGNORED` | error / warn | settings parse; rule forms Claude Code ignores | Claude Code docs |
| `H_SKILL_*` | error / warn | skill `name` (<=64, lowercase/hyphens, no "claude"), `description` (<=1,024), body under 500 lines | Anthropic skill docs |
| `H_SECRET_IN_INSTRUCTIONS` | **error** | a secret in a file loaded into every session | - |
| `C_*` | error / warn / info | config parses, preset paths exist and stay inside the project, symlinks, secrets, preset budget vs content size, **`.files-to-prompt` not git-ignored** (`C_CONFIG_IGNORED`: an ignored config is shared with nobody), `[ask]` keys that must not live in config | - |

A structural check cannot tell whether instructions are *good*; the report says so (`assumed`/`unknown`) on every run. fileflow holds
itself to this: CI runs `fileflow check --strict` on this repository, and [`docs/field-notes.md`](docs/field-notes.md) records what that
found.

## Combined example

```bash
fileflow README.md src/ tests/ \
  --ignore-patterns '*.md' \
  --ignore-gitignore \
  --format default \
  | llm -m opus -s 'generate comprehensive API documentation'
```

## Command reference

```bash
fileflow --help
```

```text
Usage: fileflow [OPTIONS] [COMMAND] [ARGS]...

  fileflow — turn files into a single LLM prompt.

      fileflow path/to/directory
      fileflow README.md src/ tests/
      fileflow serve            # optional local web backend

Commands:
  prompt   Takes one or more paths to files or directories and outputs every
           file, recursively, each preceded by its path and --- separators.
  presets  List the presets defined in .files-to-prompt.
  init     Scaffold a project: config + presets, notes, or an agent harness.
  check    Verify .files-to-prompt and the agent harness.
  fetch    Save web pages as Markdown with provenance (SSRF-safe).
  ask      Send the bundle to a model and print the answer (opt-in).
  serve    Start a local web server for the fileflow web client.

Run `fileflow prompt --help` or `fileflow serve --help` for options.
Leading options go to `prompt`, so `fileflow --preset hero` works with no path.
```

## Local server mode

The web client can run against a real project directory instead of an imported
snapshot. `fileflow serve` starts a local FastAPI server (bound to `127.0.0.1`
by default — files never leave your machine) that serves the web client plus a
read-mostly `/api` backed by the real filesystem.

```bash
pip install -e ".[server]"
fileflow serve --root /path/to/project --port 8090
# open http://localhost:8090, then toggle "Live server" in the sidebar
```

The `/api` endpoints: `health`, `project`, `tree`, `file`, `prompt`, `config`
(GET/PUT for `.files-to-prompt`), and `watch` (WebSocket change notifications).
`/api/prompt` accepts a `tokenizer` parameter (heuristic or tiktoken) and trims
to the `budget` using the same engine logic as the CLI.

**What the server will and won't expose.** `--root` *pins* the server to that
directory. Every endpoint (and the watch socket) accepts an optional `root` query
parameter, but it can only *narrow* the scope to a sub-directory; anything that
resolves outside `--root` — an absolute path, `..`, a symlink, a sibling that
merely shares a name prefix — is refused with HTTP 403 (the WebSocket handshake
is rejected). `/api/file` additionally confines `path` to the root and never
serves anything inside `.git/`. Serving on a non-localhost host requires the
explicit `--allow-remote` flag.

**Network hardening.**
- *DNS rebinding:* on a local bind the server only accepts `Host` values
  `localhost`, `127.0.0.1`, `::1` (and the bound host); anything else is `400`.
  Add names with `--allowed-host NAME` (repeatable).
- *Cross-site WebSockets / writes:* a browser `Origin` must match the `Host` (or an
  allowed host) for the watch socket and for every non-GET request, otherwise `403`.
- *Authentication:* `--allow-remote` now **requires a token**. One is generated and
  printed (`Open: http://host:port/?token=…`) unless you choose it with
  `--token`/`FILEFLOW_TOKEN`. The first visit swaps `?token=` for an HttpOnly
  cookie; scripts can send `Authorization: Bearer <token>`. Only `/api/health` is
  open. `--no-token` serves **without** authentication (loud warning; use it only
  for a folder you are happy to share). With `--allow-remote`, `Host` is not
  restricted unless you pass `--allowed-host`.
- `/api/prompt` paths are relative to the served root (no absolute local paths in
  what you paste), symlinks that leave the project are skipped, and the tree,
  project count and prompt all come from one shared walker so they always agree
  (including nested `.gitignore` files).

**Live reload:** with "Live server" on, the web client connects to `/api/watch`
and refreshes the tree + prompt automatically when files change on disk — a
true watch mode (the server pushes change notifications over WebSocket).

- *What counts as a change:* every file that can affect the prompt, including
  hidden files, `.gitignore` files and `.files-to-prompt` itself. Anything
  matched by `.gitignore`, and `.git`, `node_modules`, virtualenvs and caches,
  is never scanned, so polling cost follows your project's size, not its
  dependencies'. (If you switch "respect .gitignore" off in the UI, changes to
  ignored files don't live-refresh.)
- *Protocol:* once connected the server sends a `ready` frame carrying a digest of
  the current state, then `{changed, config_changed, hash}` frames when something
  changes. `config_changed` is true when the *content* of `.files-to-prompt`
  changed, which is what makes the client re-read it before refreshing. After a
  dropped connection the client compares the new `ready` digest with the last one
  it saw and re-syncs only if the project changed in the meantime.
- Scanning runs off the server's event loop, and a watcher stops as soon as its
  client disconnects, so `Ctrl+C` stops the server promptly even with a tab open.

## VS Code extension

A starter extension in [`extensions/vscode/`](extensions/vscode) wraps the CLI:
run `fileflow` on the workspace or a selected folder and view/copy the prompt.
See its [README](extensions/vscode/README.md).

## Web client

`fileflow` ships a self-contained browser app in [`fileflow/web/`](fileflow/web) that runs the
same prompt-generation engine entirely client-side. Build a virtual file tree
in the browser, toggle every option, and see the prompt rendered live as
`default`, `xml` or `json`.

- **Import real files** — drag and drop files or a folder onto the sidebar, or
  use the Import button (File System Access API, with a `<input webkitdirectory>`
  fallback). Hidden and gitignored entries are preserved.
- **Live output** — re-renders on every change with copy, download, and a
  "CLI cmd" button that copies the equivalent `fileflow …` shell command.
- **Token budget** — set a max-token cap; oversized files are truncated and
  later files dropped, with a note shown in the output.
- **Recipe persistence** — your options (format, patterns, budget, toggles)
  are saved to `localStorage` and restored on the next load.
- **Bounded rendering** — large previews stream in chunks (keeps the UI
  responsive) and are capped by default; oversized trees show a note pointing
  you to `fileflow serve`.
- **Live server mode** — when the client is served by `fileflow serve`, a
  "Live server" toggle appears; it switches the tree and output to the real
  project on disk (server-applied gitignore, read-only file previews).

```bash
# Static (offline) mode — no backend needed:
cd web
python3 -m http.server 8090
# open http://localhost:8090
```

The engine lives in `fileflow/web/js/core.js` (pure, unit-tested in Node with
`node js/core.test.js` and `node js/smoke.test.js`) and the UI in
`fileflow/web/js/app.js`.

## Cross-engine contract

The CLI (Python) and the web client (JS) implement the same engine. A shared
[`testdata/golden-corpus.json`](testdata/golden-corpus.json) pins the exact
rendered output for every format/option combination; both
[`tests/test_golden_contract.py`](tests/test_golden_contract.py) (pytest) and
[`fileflow/web/js/golden.test.js`](fileflow/web/js/golden.test.js) (Node) assert byte-identical
output, so the two engines cannot silently drift. A property-based
[`tests/test_cross_engine_fuzz.py`](tests/test_cross_engine_fuzz.py) fuzzes the
full pipeline (filtering + token budget + all three formatters) over random
trees and options, comparing both engines byte-for-byte.

```bash
python -m pytest tests/test_golden_contract.py   # Python side
node fileflow/web/js/golden.test.js                       # JS side

# Regenerate the corpus (Python engine is the reference):
python3 testdata/generate_golden.py
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) — an architecture review of
the current system, the target v1.0 design, ADRs, and the `fileflow serve`
backend spec.

## Development

Run the Python test suite (includes the cross-engine golden contract and the
server tests):

```bash
pip install -e ".[test,server]"
pytest
```

The server tests are skipped automatically if `fastapi` is not installed. There
are two layers: `tests/test_server.py` drives the ASGI app in-process, and
`tests/test_serve_subprocess.py` launches the real `fileflow serve` command on a
free port and exercises it over actual HTTP and WebSocket connections (including
attempts to escape `--root` and a shutdown-with-a-client-connected check).

The browser logic that can't run in pytest is covered by Node tests under a small
DOM shim (`fileflow/web/js/dom_shim.js`): `smoke.test.js` (static mode) and
`live.test.js` (live-server mode, with a fake `fetch`/`WebSocket`):

```bash
for t in golden core smoke live; do node fileflow/web/js/$t.test.js; done
```

Continuous integration (`ci/ci.yml`, to be installed at `.github/workflows/ci.yml`: see `ci/README.md`) runs the Python suite across
Python 3.8/3.11/3.12, the Node golden/core/smoke/live suites, and a dedicated
cross-engine parity job enforcing ADR-001.

The tests use `pytest` and `click.testing.CliRunner` with temporary
directories for filesystem isolation. They cover directory traversal, hidden
files, `.gitignore` behaviour, ignore patterns, multiple paths, output
formats, and error conditions.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Roadmap & engineering contract

- [`docs/product-roadmap.md`](docs/product-roadmap.md) — what we are building next (presets, instructions, context receipts, `init`, `fetch`, `check`, playbooks), the user journey, the alignment filter and the prioritised backlog.
- [`docs/production-architecture.md`](docs/production-architecture.md) — the prototype-to-live plan: product focus, LLM routing, agent patterns, activity pipeline, memory, RAG, hosting and observability, with ADRs 011–024 in `docs/architecture.md`.
- [`docs/product-experience.md`](docs/product-experience.md) — frontend, backend API, tool catalogue, user journeys, developer flow and integrations.
- [`docs/design-system.md`](docs/design-system.md) — design tokens, components, the WCAG 2.2 AA browser audit (24 failures → 0), and what is still not verified.
- [`docs/engineering-contract.md`](docs/engineering-contract.md) — the release gates: *AI may accelerate implementation, but verification owns the release decision.*
- [`docs/architecture.md`](docs/architecture.md) — architecture review, risk register and ADRs 001–009.
