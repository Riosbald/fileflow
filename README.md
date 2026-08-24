# fileflow

Flatten a codebase into a single LLM-ready prompt — with a CLI, an HTTP
server, and a web client that all share one source of truth: the
`.files-to-prompt` config file at your project root.

## Install

```bash
pip install -e .            # CLI only (click + pathspec)
pip install -e '.[server]'  # + fileflow serve (fastapi + uvicorn)
pip install -e '.[dev]'     # + test deps (pytest + httpx)
```

## CLI

```bash
fileflow prompt .                        # default format
fileflow prompt src/ README.md           # any mix of files/dirs
fileflow prompt . --format markdown      # default | markdown | xml | json
fileflow prompt . -n --separators        # line numbers, heavy separators
fileflow prompt . --max-tokens 8000      # approximate token budget
fileflow prompt . -e '*.log' -e 'dist/*' # exclude patterns (repeatable)
fileflow prompt . --include-hidden --ignore-gitignore
fileflow prompt . -o prompt.txt          # write to a file
```

Binary files are skipped automatically; `.gitignore` files (including
nested ones) are honored unless `--ignore-gitignore`.

## `.files-to-prompt` config

Drop a TOML file at the project root and every interface — CLI, server,
web client — picks it up:

```toml
format = "json"
separators = false
line_numbers = true
max_tokens = 8000
include_hidden = false
ignore_gitignore = false

[exclude]
patterns = ["*.log", "node_modules/*"]
```

**Precedence: CLI flags > config file > defaults.** Exclude patterns
are additive: config patterns and `-e` flags are merged.

## Server + web client

```bash
fileflow serve --port 8090 --root .
```

* `GET  /` — web client
* `GET  /api/tree` — file listing (config-filtered) with token estimates
* `GET  /api/config` / `PUT /api/config` — read/persist `.files-to-prompt`
* `POST /api/render` — render the root; body `{"overrides": {...}}` beats the config
* `GET  /api/events` — SSE stream emitting `config-changed` whenever
  `.files-to-prompt` changes on disk

**Live refresh:** every render re-reads the config from disk, and the
web client subscribes to `/api/events` — edit `.files-to-prompt` by
hand (or from the CLI, or another client) and the page re-applies the
config and re-renders automatically.

## Tests

```bash
python3 -m pytest          # unit + fuzz + subprocess E2E integration
npm test                   # Node golden/core/smoke tests against the CLI
```
