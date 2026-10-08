# fileflow — Engineering Contract

> **AI may accelerate implementation, but verification owns the release decision.**

```text
Can generate  ≠  can verify
Can verify    ≠  can deploy
Can deploy    ≠  proven in use
```

fileflow is built with AI assistance (it is part of the product's own spec:
self-bootstrapping). That makes this contract more important, not less: the model
is never the authority — the **tests, receipts, policy and audit trail** are.

## Maturity ladder

| Stage | Meaning | Evidence required |
|---|---|---|
| **Prototype** | It runs on my machine | — |
| **Verified system** | Behaviour is pinned by automated checks across every surface | Gates A–D pass in CI |
| **Production system** | Safe defaults, upgrade path, documented failure behaviour | Gates A–D + versioned contracts + release notes |
| **Usage-proven** | Real people used it for real work and we fixed what broke | Gate E |

**Where fileflow is today:** *verified system* for the local-file case. Not
usage-proven. Any doc or release note must not claim otherwise.

## The five gates

A release (or a roadmap item marked ✅) must pass the gates relevant to it.

### Gate A — Security
- [ ] Server pinned to its root; clients can narrow, never widen (ADR-004) — ✅
- [ ] Symlinks cannot pull files from outside the project into a prompt — ✅
- [x] Host allowlist (DNS rebinding), Origin check on WebSocket/writes, token required for `--allow-remote` — ✅ (R0-3)
- [x] No absolute local paths in served prompt output or preset runs — ✅ (R0-5)
- [x] Secret guard on emitted text (warn / exclude / block / off; value never printed) — ✅ (F8)
- [x] No network access except the explicit `fetch` and `ask` verbs; `fetch` refuses non-public addresses, pins the validated address, re-validates redirects and enforces a hard deadline — ✅ (15 controls mutation-tested)
- [x] The server never calls an LLM and holds no API keys (ADR-007) — ✅ tested: no model endpoint, and `fileflow.ask` is never imported by the server, `prompt`, `check` or `fetch`
- [x] Model calls (`ask`, ADR-010): destination and key never come from a repo config; redirects refused; https except loopback; consent before egress; secrets default to `block` off-machine; key never printed — ✅ each mutation-tested
- [ ] `ask` exercised against a **live** provider — ☐ not done (mock servers only)

### Gate B — Contract
- [x] Config `version` (newer is refused) and `receipt_version` — ✅. Prompt-format notes: ☐
- [ ] Cross-engine **golden corpus** covers every rendering feature **before** it ships (ADR-001) — ✅ for current features
- [x] Config: strict on types, lenient on unknown keys, documented precedence table — ✅
- [x] Errors use the closed taxonomy (`fileflow/errors.py`), each with exit code and next step — ✅ (codes for R2/R4 defined, not yet raised)

### Gate C — Reliability
- [x] CLI failures have a class, an exit code and a "next command" message — ✅ for presets/config/secrets/paths; fetch (R2) pending
- [x] Partial results are explicit (receipt `excluded` / `truncated`), never silent — ✅ (CLI); web receipt: ☐ (R3)
- [ ] Graceful degradation: no clipboard, no network, no git → clear fallback

### Gate D — Verification
- [ ] Unit + integration + **real-subprocess** tests (`tests/test_serve_subprocess.py`) — ✅
- [ ] Property/fuzz parity between Python and JS engines — ✅
- [ ] Packaging test builds a wheel from a **clean copy** and asserts contents — ✅
- [x] Fixtures with *planted* problems (secrets, symlinks, nested ignores, half-filled templates) and known expected findings — ✅
- [x] Each new safety check is **mutation-tested**: break the code, see the test fail — ✅ for R0/R1 (Host, Origin, confinement, placeholder lint, precedence, receipt completeness, secret value leak, block mode)

### Gate E — Usage
Not satisfied by "builds, tests pass, README exists." Requires:
- [ ] ≥ 5 real sprints by ≥ 2 people on real projects
- [~] Every confusing moment, failure and correction logged in `docs/field-notes.md` — started (FN-001..004, one round of self-use; not independent users)
- [ ] Each logged failure either fixed (with a regression test) or consciously accepted in writing

## Epistemic rules for fileflow's own outputs

1. **Package, never fabricate.** fileflow may select and describe context; it may not invent, rewrite or "improve" file content.
2. **Observed ≠ true.** Provenance labels (`observed`, `generated`, `user`) say *where text came from*, not whether it is correct.
3. **Estimates are labelled as estimates.** Token counts are heuristic unless a tokenizer is named.
4. **Say what you don't know.** Receipts carry `verified`, `assumed`, `unknown`, `failed_checks`.
5. **Counts are computed, not asserted.** Totals come from the data; two views of the same data must come from one source.

## Release checklist

1. Run: `python -m pytest -q` · `for t in golden core smoke live; do node fileflow/web/js/$t.test.js; done` · fuzz at 200 cases.
2. Walk the gates above; tick only what you *ran*, not what you believe.
   Include the **stranger walkthrough** (`docs/pilot-kit/README.md` Step 0) on a repo you did not make.
3. State the maturity stage honestly in the release notes.
4. Record the commit hash that was verified.

## Practices learned the hard way

- **Run the product as a stranger before every release.** On a project you did not make, with no flags, write down what it did *not* tell you. A one-hour walkthrough found six gaps in a codebase with 700 passing tests (FN-014), including a 100,000-token default prompt that was 99.9 % one lockfile. Tests prove what you thought to ask; a stranger asks the rest. (`docs/systems-gap-analysis.md` §7.)
- **A green signal must be earned.** Seven separate defects (FN-009…FN-014) were the system reporting success it had not checked: a test that passed with the protection removed, a fetch that "succeeded" on a bot-challenge page, a test double that invented the row the test looked for. When a check cannot fail, it is decoration.
- **Advice must be executed in a test.** If the tool prints "run this flag", a test must parse it out of the message and run it. The first version of the big-file advice suggested a path, which the tool silently ignores.

- **A passing test proves nothing until you've seen it fail.** Mutation-test safety checks. (During R0 a WebSocket Origin test passed for the wrong reason — the Host header was wrong — and only a mutation exposed it.)
- **Beware stale bytecode when mutating by script:** a same-size edit restored within the same second can leave Python running the *mutant*. Disable bytecode writing (`PYTHONDONTWRITEBYTECODE=1`) for mutation runs.
- **Build from a clean copy** in packaging tests; leftover `build/` or `*.egg-info/` changes what a wheel contains.
- **A security test that only passes for the benign variant is not a test.** The `ask` redirect test first used a 307; urllib never follows a POST on 307, so it passed with the protection *removed*. Only 301/302/303 (which urllib turns into a GET and forwards the key on) prove anything. Run the mutation, read which cases fail.
- **`read(n)` waits for n bytes; `read1(n)` returns after one read.** A per-recv socket timeout resets on every byte, so a slow-drip server can hold a naive fetch open indefinitely. Use a hard watchdog for untrusted peers.
- **Add the hostile case to the fuzzer, then fix the code.** Adding non-ASCII to the fuzz alphabet exposed a CLI/web divergence that 100+ ASCII-only cases never could.
