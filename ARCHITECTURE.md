# Architecture

A top-level map: domains, layers, and the direction dependencies may point.

## Domains
- **engine** - `fileflow/walker.py` (one traversal, closed exclusion reasons, include globs), `fileflow/cli.py` (`collect`, budget, render,
  the shared `_assemble`), `fileflow/secretscan.py`, `fileflow/receipt.py`, `fileflow/presets.py`, `fileflow/provenance.py`,
  `fileflow/gitdiff.py`, `fileflow/errors.py`.
- **network edge** - `fileflow/fetch.py` + `fileflow/htmltext.py` (`fetch`: untrusted URLs) and `fileflow/ask.py` (`ask`: one model call).
  These are the only modules that open a socket to a user-chosen address; nothing else imports them.
- **server** - `fileflow/server/` (FastAPI app, request guard, config read/write, watcher). Optional dependency.
- **web client** - `fileflow/web/` (static SPA; `js/core.js` is the engine port, pinned by the golden corpus).
- **harness tooling** - `fileflow/scaffold.py` (`init` templates) and `fileflow/check.py` (`check` lints).

## Layers and allowed dependencies
`walker` imports nothing from fileflow. `cli` (engine) imports `walker`, `secretscan`, `presets`, `receipt`, `clipboard`.
`server` imports the engine, never the reverse. **Not allowed:** the base CLI importing `fastapi` or `fileflow.server.app`
(a test asserts this), `server` or `check` importing `fileflow.ask` or `fileflow.fetch` (asserted too), and any module calling a model or
opening a network socket in the prompt path.

## Where things live
`fileflow/` source - `tests/` pytest (incl. real-subprocess server tests) - `testdata/` golden corpus + generator -
`docs/` knowledge base - `extensions/vscode/` editor integration - `ci/ci.yml` CI workflow (install at `.github/workflows/`, see `ci/README.md`).
