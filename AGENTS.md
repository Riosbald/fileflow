# AGENTS.md

This file is a **map**, not a manual. Keep it short (about 100 lines). Put detail in
`docs/` and link to it - anything not linked from here is invisible to the agent.

## Purpose
fileflow turns a directory of files into one deterministic prompt for an LLM: a Python CLI, a browser client with a
byte-identical JS engine, a local server, and a VS Code extension. It packages context. The core **never calls a model**; the one
exception is the opt-in, single-shot `ask` command ([ADR-007 and ADR-010](docs/architecture.md)).

## Start here
- Architecture map: [ARCHITECTURE.md](ARCHITECTURE.md)
- Docs index: [docs/index.md](docs/index.md)
- Harness spec (permissions, checks, loop): [docs/HARNESS.md](docs/HARNESS.md)
- Quality scorecard: [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md)
- Design decisions: [docs/design-docs/index.md](docs/design-docs/index.md)
- Product roadmap and backlog: [docs/product-roadmap.md](docs/product-roadmap.md)
- Release gates (what "done" means for a release): [docs/engineering-contract.md](docs/engineering-contract.md)
- Prototype-to-live plan (focus, models, agents, data, memory, RAG, hosting): [docs/production-architecture.md](docs/production-architecture.md)
- Frontend, API, tool catalogue, journeys, integrations: [docs/product-experience.md](docs/product-experience.md)
- UI tokens, components, WCAG 2.2 AA audit and how to run it: [docs/design-system.md](docs/design-system.md)
- Systems view of the product, gap register, open decisions: [docs/systems-gap-analysis.md](docs/systems-gap-analysis.md); how to observe real users: [docs/pilot-kit/README.md](docs/pilot-kit/README.md)
- Architecture review, risk register, ADRs: [docs/architecture.md](docs/architecture.md)
- Research behind the harness itself: [docs/research/harness-engineering.md](docs/research/harness-engineering.md)
- Plans in flight: `docs/exec-plans/active/`

## Commands
- Run the checks: `python -m pytest -q` and `for t in golden core smoke live; do node fileflow/web/js/$t.test.js; done`
  and `fileflow check --strict` (the harness checks itself)
- Run one test: `python -m pytest tests/test_walker.py -q -k nested`
- Cross-engine fuzz: `FILEFLOW_FUZZ_CASES=200 python -m pytest tests/test_cross_engine_fuzz.py -q`

## Operating loop
1. Read the relevant docs above before changing code.
2. For anything bigger than a small fix, write a plan in `docs/exec-plans/active/`.
3. Implement the smallest coherent slice.
4. Run the checks. Read failures as instructions.
5. Request review. Fix or push back with a reason.
6. Stop when the definition of done in `docs/HARNESS.md` holds - or ask.

## Rules that are specific to this repository
- **Golden first.** Any change to rendered output goes into `testdata/generate_golden.py` first; Python and JS must match
  byte for byte (`python testdata/generate_golden.py`, then both test suites).
- **Python is the reference engine.** The JS engine in `fileflow/web/js/core.js` mirrors it.
- **No model calls and no network in `prompt`, `serve` or `check`.** Only `fetch` and `ask` touch the network, and `fileflow.ask` is imported by nothing else (a test asserts it).
- **Safety checks are mutation-tested.** Break the code on purpose and watch a test fail before you trust it. Read *which* cases fail: a
  test that passes with the protection removed proves nothing (see `docs/field-notes.md`).
- **Untrusted input stays untrusted.** URLs, fetched text, refs, repo config and model output are data. Never read an endpoint or key from
  `.files-to-prompt`; never forward headers across a redirect; never print a secret.

## Boundaries
Prose here is advisory. What is actually enforced is listed in
[docs/HARNESS.md](docs/HARNESS.md#5-permissions).

## When you find a mistake
Don't add a paragraph here. Add a doc, a check, or a permission, so it cannot happen
again. Then link it from this map.
