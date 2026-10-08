# fileflow — Product Roadmap & Feature Backlog

> **Artifact type:** Product strategy + prioritized, testable backlog
> **Status:** R0 and R1 are built and tested (see §5.1). R2–R4 are proposals. Items marked ✅ are verified by tests.
> **Inputs folded in:** (1) the ADHD-friendly website-sprint workflow, (2) the
> "URL Strategic Analyzer" report on paulgraham.com, (3) the ATS-Analyzer
> architecture critique (evidence ledger / verification gates).
> **Related:** [`architecture.md`](architecture.md) (ADRs 001–009) ·
> [`engineering-contract.md`](engineering-contract.md) (release gates)

---

## 0. TL;DR (read this, skip the rest if you're busy)

**What fileflow is:** the *last mile* between your files and an AI chat/agent. It
selects, shapes, describes and **verifies** the context you paste — nothing more.

**What the three inputs changed**

| Input | What it really is | What fileflow takes from it | What fileflow refuses |
|---|---|---|---|
| ADHD website sprint | A *workflow*: small bounded steps, one section per prompt, resume easily | **Presets + instructions**, `init` templates, one-keystroke handoff, later `next` | Becoming a project manager / site builder |
| Paul Graham report | A *content pipeline*: URL → catalog → prompts → activities → learning path | **Fetch with provenance**, **prompt templates with variables**, playbooks (ordered presets) | Crawling/scoring/clustering sites; calling an LLM to summarise |
| ATS critique | An *engineering contract*: evidence, error taxonomy, verified/assumed/unknown, gates | **Context Receipt**, **`fileflow check`**, **secret guard**, honest "estimate" labels, release gates | Fake precision; "ground truth" language |

**The one idea that ties them together**

> **fileflow packages context. It never fabricates it, its core never calls a model
> (the one exception is the opt-in `ask` command, ADR-010), and it always shows you
> exactly what it packaged and what it left out.**

**Do this next (in order)**

1. ✅ **R0 Trust** — symlink escape, web client in the wheel, shared walker, relative served paths, Host/Origin/token guard, secret guard
2. ✅ **R1 Frame the task** (CLI) — presets, instructions, `{{vars}}` + lint, receipt, error taxonomy, `--copy`, estimate wording
3. ✅ **R2 Start & feed** — `init`, `check`, `fetch` (SSRF-safe, with provenance), provenance labels in prompts, `[include]`, `--diff`/`--staged`/`--since`/`--patch`
3b. ✅ **`ask`** (new, ADR-010) — opt-in single-shot model call, OpenAI-compatible + Anthropic; **never run against a live provider**
4. R3: web preset picker/focus bar/receipt panel, VS Code QuickPick, web secret scan + receipt parity
5. R4: playbooks + `next` — **only if** R1–R3 are used for real (Gate E)

---

## 1. Product definition

### 1.1 Job to be done

> When I'm working with an AI agent on a task that is easy to get lost in, I want
> to hand it a **precise, bounded, verified slice** of my project plus a clear
> instruction — in seconds — so the AI stays on task and I keep momentum.

### 1.2 Who it's for (and who it is not)

| Persona | Situation | Needs most | Primary release |
|---|---|---|---|
| **Sprint builder** (the ADHD-friendly website scenario) | Solo, building/improving a site with an AI agent; loses the thread when distracted | Few decisions, one section per prompt, a known next step | R1, R2, R4 |
| **Reader-to-prompt learner** (the Paul Graham scenario) | Has reading material (essays, notes, docs) and wants to turn it into reflective/analytical prompts | Bring text in with provenance, reusable prompt templates with blanks, ordered steps | R2, R4 |
| **Careful engineer** (the ATS critique) | Pastes real code into third-party tools; must trust what left the machine | Receipts, secret guard, reproducibility, CI check | R0, R1, R2 |

**Not for:** teams needing a hosted multi-user service (ADR-002 non-goal), people who
want fileflow to *do* the AI step, project-management users.

### 1.3 The alignment filter (apply to every future idea)

A feature ships only if it passes **all five**. Fail one → park or reject.

| # | Test | Why |
|---|---|---|
| A1 | **Context-packaging job?** (select / shape / describe / verify what goes into a prompt) | Prevents drift into PM, analyzer, or builder tooling |
| A2 | **Deterministic & file-backed?** Same inputs → same bytes; state lives in plain files | Keeps the golden-corpus contract (ADR-001) and git-friendliness |
| A3 | **Local-first & offline by default?** Network only when the user explicitly asks | Privacy story; Lagos connectivity reality |
| A4 | **Parity-aware?** Either CLI = web byte-for-byte, or the feature is declared CLI-only with a reason | Protects the core promise |
| A5 | **Cuts decisions at the moment of use?** Measurable in fewer steps / seconds | The ADHD-friendly requirement is a *product* requirement, not a skin |

### 1.4 Non-goals (explicit)

- **The core never calls an LLM.** `prompt`, `serve`, `check`, `fetch` and the receipt hold no keys and make no model calls. The only model call is the opt-in, single-shot `ask` command (ADR-010, decided by the product owner); there is no summarising or scoring inside the core. *Not built, by decision:* agents that act (tools / shell / file writes), memory, MCP management — each needs its own ADR and, for acting agents, an enforced sandbox.
- No site crawler, no "pick the best 20 URLs", no similarity maps or prerequisite
  graphs *computed* by fileflow. Those are a different tool; fileflow consumes their
  **output as files** (a generated report is just a markdown file in `content/`).
- No task database, no accounts, no hosted backend.
- No ATS scoring, résumé rendering, transcription, or PDF generation.

---

## 2. End-to-end user journey

```
 SOURCES ──► SELECT ──► SHAPE ──► VERIFY ──► HAND OFF ──► (user pastes to AI) ──► RE-ENTER
 files        paths,      instruction,  receipt,   copy /        out of scope          next step,
 fetched      presets,    variables,    check,     file /                              what's done
 pages        diff        format        secrets    stdout
```

| Stage | User is thinking… | Pain today | Moment of truth | Feature(s) | Success metric (target) |
|---|---|---|---|---|---|
| **1. Start** | "Where do I even begin?" | Blank folder, no structure | First command produces something useful | `init --template` (F3) | **Time-to-first-prompt ≤ 60 s, ≤ 3 commands** |
| **2. Frame** | "What exactly am I asking?" | Re-typing the same instruction; whole-site prompts | One named preset = paths + instruction + budget | Presets, instructions, variables (F1, F2) | Preset run needs **0 flags** |
| **3. Feed** | "I have a link / notes / screenshots" | Copy-paste from web, lose the source | Content lands in `content/` with its origin recorded | `fetch` + provenance (F6, F7) | Every fetched file has `source_url` + `fetched_at` |
| **4. Verify** | "Is the right stuff in there? Is anything secret?" | Silent skips, symlinks, `.env`, truncation | A receipt answers it in 5 seconds | Receipt, `check`, secret guard (F4, F5, F8) | **0 silent omissions**; secrets blocked before paste |
| **5. Hand off** | "Get it into the chat" | Select-all, scroll, copy | One keystroke | `--copy`, web Copy (F9) | ≤ 1 action |
| **6. Re-enter** | "I got distracted — where was I?" | Lose the thread | One command says what's next | `next`, playbooks (F11) | **Re-entry ≤ 10 s** |

**Design rules that make this ADHD-friendly (testable, not decorative)**

1. **One decision per screen/command.** Defaults must work; flags are for experts.
2. **Never block on a blank page.** Every error says *what happened* and *the next command to run*.
3. **Small, bounded output.** A preset has a token budget; the receipt says when it was hit.
4. **Visible progress, no guilt.** `next` shows the next step, not an overdue list.
5. **No hidden state.** Everything is a file you can read, diff and commit.

---

## 3. Feature catalogue

Scores are **judgement, not measurement** — a deliberate choice (see the ATS
critique on false precision). *Impact* and *Confidence* 1–5 (confidence = how much
real evidence we have), *Effort* S=1 · M=2 · L=3. **Priority = Impact × Confidence ÷ Effort.**

| ID | Feature | Source input | Impact | Conf. | Effort | Priority | Release |
|---|---|---|---|---|---|---|---|
| R0-1 | Symlink confinement in the prompt walker | security review | 5 | 5 | S | 25 | ✅ done |
| R0-2 | Web client ships in the wheel | packaging review | 4 | 5 | S | 20 | ✅ done |
| R0-3 | `Host` allowlist + optional token for `--allow-remote` | security review / ATS Gate A | 5 | 4 | M | 10 | ✅ done |
| R0-4 | One shared walker (tree/project/prompt/watch agree on nested `.gitignore`) | verified bug | 4 | 5 | M | 10 | ✅ done |
| R0-5 | Relative paths in served prompts (no absolute local paths) | verified bug | 3 | 5 | S | 15 | ✅ done |
| **F8** | **Secret guard** (`warn`/`block`) | ATS Gate A | 5 | 4 | M | 10 | ✅ done (CLI) |
| **F1** | **Named presets** `[presets.<name>]` + `--preset` | ADHD plan | 5 | 4 | M | 10 | ✅ done (CLI) |
| **F2** | **Instructions** `--instruction/-file`, **prompt templates with `{{vars}}`**, unresolved-placeholder lint | ADHD plan + PG prompt library | 5 | 5 | M | 12.5 | ✅ done (CLI) |
| **F4** | **Context Receipt** (evidence ledger for a bundle) | ATS critique | 5 | 4 | M | 10 | ✅ done (CLI) |
| F12 | **Error taxonomy + exit codes** | ATS critique | 4 | 4 | S | 16 | ✅ done (CLI) |
| F13 | Honest **estimate** labelling for tokens | ATS "false precision" | 3 | 5 | S | 15 | ✅ done (CLI) |
| F9 | `--copy` one-keystroke handoff (CLI) | ADHD plan | 4 | 4 | S | 16 | ✅ done (CLI) |
| **F3** | **`fileflow init --template`** (`minimal`, `web-sprint`, `harness` built; `learn` not) | ADHD plan + harness research | 4 | 4 | M | 8 | ✅ done (3 of 4 templates) |
| **F6** | **`fileflow fetch <url>`** → clean markdown in `content/` | PG report + ADHD step 7 | 4 | 3 | M | 6 | ✅ done |
| **F7** | **Provenance labels** (`observed` / `generated` / `user`) carried into the prompt | ATS evidence-vs-claim | 4 | 4 | M | 8 | ✅ done (Python + JS parity) |
| **F5** | **`fileflow check`** (lint config, presets, secrets, symlinks, budget **+ harness lints**) — CI-friendly | ATS gates + harness research | 4 | 4 | M | 8 | ✅ done (provenance checks wait for F7) |
| **F20** | **Harness kit**: Harness Spec template, `AGENTS.md` map, advisory-vs-enforced permissions, instruction-layer lints | harness-engineering research (`docs/research/harness-engineering.md`) | 4 | 3 | M | 6 | ✅ done (instruction layer only) |
| F14 | `[include].patterns` | spec roadmap | 3 | 4 | S | 12 | ✅ done (Python + JS parity) |
| F15 | `--diff` / `--staged` / `--since` / `--patch` (changed files only) | ADHD plan (polish pass) | 4 | 3 | M | 6 | ✅ done |
| **F21** | **`fileflow ask`**: opt-in single-shot model call (OpenAI-compatible + Anthropic), consent, secrets-block default, answers saved as `generated` | product owner request (ADR-010) | 4 | 2 | L | 2.7 | ✅ done — mock-tested only |
| F16 | Web: preset picker, focus bar, receipt panel, `/api/presets`, `/api/receipt` | ADHD + ATS | 4 | 3 | M | 6 | R3 |
| F17 | VS Code: *fileflow: Run Preset…* QuickPick | spec roadmap | 3 | 3 | S | 9 | R3 |
| F18 | Tokenizer choice in the web UI | open P2 | 2 | 4 | S | 8 | R3 |
| **F11** | **Playbooks** (ordered presets with `time_box`, `success`, `requires`) + **`fileflow next`** | PG activity cards + prerequisite graph + ADHD order | 4 | 2 | L | 2.7 | R4 — *gated* |
| F19 | Preset packs (`web-sprint`, `learn-from-essays`, `code-review`) | PG prompt library | 3 | 3 | M | 4.5 | R4 |

> **Why F11 ranks low and is gated:** the *idea* is the most distinctive, but our
> confidence is 2/5 — no real user has run a sprint with it. Per Gate E it earns its
> build only after R1–R3 show actual use. The honest roadmap says so out loud.

---

## 4. Feature specs & acceptance criteria

Each criterion is written to become a test. "Gate" refers to
[`engineering-contract.md`](engineering-contract.md).

### F1 — Named presets
**Story:** *As a sprint builder, I run `fileflow --preset hero` and get exactly the
hero-section bundle, without remembering flags.*

```toml
version = 1                      # config schema version (Gate B)

[output]  format = "xml"  separators = true  line_numbers = true  max_tokens = 16000
[ignore]  gitignore = true  hidden = false
[exclude] patterns = ["node_modules", "dist", "*.log", "__pycache__"]
[secrets] mode = "block"         # warn | block | off   (F8)

[presets.analyze]
description = "Read-only tour of the codebase"
paths = ["."]
exclude_patterns = ["tests", "docs"]
max_tokens = 8000
instruction = "Analyze this codebase without changing anything. Explain the folder structure, entry points, reusable components, and the top 3 improvements."

[presets.hero]
description = "Hero section only"
paths = ["components/hero", "notes/project-notes.txt", "design-references"]
max_tokens = 12000
instruction_file = "notes/prompts/hero.md"

[presets.audit]
description = "Polish pass on what changed"
paths = ["components", "notes/testing-checklist.md"]
diff = true                      # F15
instruction = "Check spacing, section transitions, mobile responsiveness and button/card tokens. Report every change you made."
```

**Precedence:** `CLI flags > preset > root config > built-in defaults` (one table in
the README, one test per row).

**Acceptance**
- `fileflow --preset hero` ≡ the same bundle as spelling out its paths/flags by hand (byte-equal test).
- Unknown preset → exit code 2, message lists available presets and the closest match.
- Preset paths are **confined to the project root** (same boundary as ADR-004); `..` or absolute-outside paths are rejected with `E_PATH_OUTSIDE_ROOT`.
- `fileflow presets` lists name · description · budget, one line each.
- Config is **lenient** for unknown keys (forward-compatible) but **strict** for wrong types (reuses today's `ConfigError`).

### F2 — Instructions, templates and variables
**Story:** *I keep my best prompts as files; I fill in the blanks once; the tool tells me if I forgot one.*

- `--instruction "<text>"` / `--instruction-file <path>` (file must be inside the root).
- **Structured prompt template** (adapted from the prompt library pattern — Role / Context / Task / Constraints / Format):
  ```toml
  [presets.frontier-audit.prompt]
  role        = "Act as a mentor who has observed hundreds of exceptional performers."
  context     = "I am a {{role}} working in {{domain}} for {{years}} years."
  task        = "Identify the gap between what I do now and genuinely great work in my field."
  constraints = "No generic advice. Be specific to my domain."
  format      = "(1) three common failure patterns, (2) my likely bottleneck, (3) the one thing to do first"
  [presets.frontier-audit.vars]
  years = "3"                      # default; others supplied with --var
  ```
- `fileflow --preset frontier-audit --var domain="backend engineering" --var role=developer`
- **Unresolved-placeholder lint:** any remaining `{{x}}` (or `[your …]`-style bracket blanks if `strict_blanks = true`) → exit 2 with `E_PLACEHOLDER_UNRESOLVED` and the list of names. `--allow-unresolved` overrides. *This is the single cheapest guard against pasting a half-filled template into a chat.*
- **Rendering:** default format → `# Task` section first; XML → `<task_instructions>…</task_instructions>` before the documents; JSON → top-level `"instructions"` key. **Instructions are never inside the files section.**

**Acceptance:** golden corpus gains instruction cases for all three formats, asserted byte-equal in Python **and** JS (A4); variable substitution is pure string replace (no eval, no conditionals — templates can't execute anything).

### F4 — Context Receipt (the evidence ledger, scaled to fileflow)
**Story:** *After a bundle, I can see in five seconds what went in, what didn't, and why.*

```json
{
  "receipt_version": 1,
  "fileflow": "0.3.0",
  "generated_by": "cli",
  "preset": "hero",
  "root": ".",
  "included": [
    {"path": "components/hero/Hero.jsx", "bytes": 2210, "tokens_est": 540, "sha256_12": "9f2c1ab04e7d"}
  ],
  "excluded": [
    {"path": "notes/.env",            "reason": "HIDDEN"},
    {"path": "assets/logo.png",       "reason": "BINARY"},
    {"path": "notes/link.txt",        "reason": "SYMLINK_OUTSIDE_ROOT"},
    {"path": "dist/app.js",           "reason": "GITIGNORED"}
  ],
  "truncated": [{"path": "components/hero/hero.css", "reason": "TOKEN_BUDGET", "kept_tokens_est": 800}],
  "ledger": {
    "verified": ["config parsed", "all preset paths exist inside root", "42 of 42 candidate files read", "no unresolved placeholders"],
    "assumed":  ["token counts are a ~4 chars/token heuristic, not a real tokenizer"],
    "unknown":  ["target model's real context window", "whether the model will honour the instruction"],
    "failed_checks": []
  },
  "totals": {"files": 11, "tokens_est": 11840, "budget": 12000}
}
```

**Acceptance**
- Reason codes are a **closed enum** (`HIDDEN, GITIGNORED, PATTERN, SYMLINK_OUTSIDE_ROOT, SYMLINK_DIR, BINARY, TOO_LARGE, UNREADABLE, SECRET_BLOCKED, TOKEN_BUDGET`); a test fails if the engine emits a code not in the enum.
- **Every candidate file appears exactly once** in `included ∪ excluded ∪ truncated` (tested on a deliberately messy project: gitignored, hidden, binary, ignored dir, budget-truncated) — this is what "0 silent omissions" means.
- Receipt is **deterministic** (sorted, no timestamps by default) so it can be committed and diffed.
- `--receipt` prints a human summary to **stderr** (so stdout stays a clean prompt); `--receipt-file receipt.json` writes JSON; `--embed-receipt` appends a short manifest *inside* the prompt (opt-in; useful for agents).
- CLI and web produce the **same receipt JSON** for the golden corpus (A4).

### F8 — Secret guard
- Scans the **exact text about to be emitted**, not just filenames. High-confidence patterns only (private-key headers, `AKIA…` AWS ids, GitHub/Slack/Stripe-style tokens, `.env`-style `KEY=value` where the key looks sensitive).
- `warn` (default for CLI) prints findings with `path:line` and **never prints the secret value**; `block` exits 3 with `E_SECRET_FOUND`; `off` is explicit.
- Honest limit, stated in the README: pattern scanning reduces risk, it does not prove absence. The receipt lists it under **assumed**, not verified.

### F12 — Error taxonomy & exit codes (don't collapse everything into "failed")

| Code | Class | Examples | Exit |
|---|---|---|---|
| `E_CONFIG_PARSE` | config | invalid TOML | 2 |
| `E_CONFIG_SCHEMA` | config | wrong type, unknown preset field type | 2 |
| `E_PRESET_UNKNOWN` / `E_PRESET_CYCLE` | preset | typo; `requires` loop (R4) | 2 |
| `E_PLACEHOLDER_UNRESOLVED` | template | missing `--var` | 2 |
| `E_PATH_OUTSIDE_ROOT` | security | `../`, outside symlink as preset path | 3 |
| `E_SECRET_FOUND` | security | block mode hit | 3 |
| `E_FETCH_NETWORK` / `E_FETCH_TIMEOUT` / `E_FETCH_BLOCKED` | fetch | DNS, timeout, private-IP refusal | 4 |
| `E_FETCH_EXTRACT` | fetch | page yielded no usable text | 4 |
| `E_IO` | system | unreadable root | 1 |

Every error prints: **what happened · why · the next command to try.**

### F3 — `fileflow init`
`fileflow init [--template minimal|web-sprint|learn] [--dir .] [--force]`

- `minimal` → `.files-to-prompt` only (config + one `analyze` preset).
- `web-sprint` → folders `design-references/ content/ components/ screenshots/ notes/`, `notes/project-notes.txt` (purpose, users, pages, colours, open tasks), `notes/testing-checklist.md`, and presets `analyze · structure · hero · section · audit`.
- `learn` → `content/`, `notes/`, presets for reflective prompts (frontier-audit style) with variables.
- **Never overwrites** existing files without `--force`; prints a created/skipped list.
- Ends by printing exactly **one** next command (`fileflow --preset analyze --copy`).

**Acceptance:** idempotent (second run creates nothing); generated config passes `fileflow check`; templates live as package data (tested via the wheel test).

### F6 + F7 — `fileflow fetch` with provenance
**Story:** *I paste a link; I get a clean local file that remembers where it came from; later prompts tell the AI how much to trust it.*

```
fileflow fetch https://example.com/essay --into content/essays
```
writes `content/essays/essay.md`:
```markdown
---
source_url: https://example.com/essay
fetched_at: 2026-10-01T12:00:00Z
sha256_12: 3c91d0aa72be
provenance: observed        # observed | generated | user
license_note: check permissions before republishing
---
# Essay title
…extracted text…
```
- **Explicit, offline-by-default (A3):** only `fetch` touches the network; `prompt`/`serve` never do.
- **SSRF-safe:** refuses loopback/private/link-local addresses and non-http(s) schemes; follows ≤ 3 redirects re-checking each hop; size and time caps. **Not exposed through `fileflow serve`** (a server that fetches arbitrary URLs for remote clients is an SSRF endpoint).
- Extraction uses the standard library by default (`html.parser`), with an optional extra for better readability. If extraction yields little text → `E_FETCH_EXTRACT` rather than saving junk.
- **Provenance in prompts (F7):** the walker reads the front matter; XML output gets `<document path="…" provenance="observed" source_url="…">`. Labelling vocabulary is deliberate: **observed** (fetched/original material), **generated** (AI output, e.g. a saved analyzer report — lower trust), **user** (you wrote it). fileflow never upgrades or rewrites a label.
- The receipt shows provenance per file, and `check` warns when a preset mixes `generated` content in without saying so.

### F5 — `fileflow check`
The "verification owns the release decision" command, for humans and CI. Exit 0/≠0.

Checks: config parses · presets resolve · every path exists and is inside root · no unresolved placeholders · `requires` graph acyclic (R4) · symlinks leaving root · secrets · budget vs content size · fetched files have provenance · `generated` content flagged. Output is the **Verified / Assumed / Unknown** ledger (same shape as the receipt).

### F9 / F13 — handoff and honesty
- `--copy` uses the OS clipboard tool if present (`pbcopy`, `wl-copy`/`xclip`, `clip`); otherwise prints a clear one-line fallback. No new dependency.
- UI/CLI wording: **"≈ 11,840 tokens (estimate)"**; exact only when `--tokenizer` is set. Never show a bare number that implies measurement.

### F15 — `--diff` / `--staged`
Only files changed vs `HEAD` (or staged) are included; a non-git folder gives `E_IO`-class guidance, not a stack trace. Receipt marks each file `changed`.

### F16 / F17 — Surfaces (R3)
Web: preset dropdown (from `GET /api/presets`), a **focus bar** (current preset · budget used · "Copy"), receipt drawer. VS Code: QuickPick of presets → run → copy. Both are **thin**: they call the engine; no logic of their own (ADR-001).

### F11 — Playbooks & `next` *(gated, R4)*
A playbook is an ordered list of presets with the *activity card* fields from the PG report, expressed as plain TOML:

```toml
[playbooks.website-sprint]
steps = ["analyze", "structure", "references", "hero", "audit"]

[presets.hero]
requires   = ["structure"]
time_box   = "90m"
success    = "Hero matches reference spacing on desktop and mobile"
frequency  = "once"
```
- `fileflow next` prints the **single** next step: preset, time box, success check, and the command to run.
- Progress is a small committed file (`.fileflow/progress.toml`) — plain text, hand-editable, optional. **No database.**
- `requires` is validated as a DAG (`E_PRESET_CYCLE`).
- **Entry condition (Gate E):** ≥ 5 real sprints completed using R1–R3 by at least 2 people, with their corrections recorded in `docs/field-notes.md`.

---

## 5. Releases

> **Update (2026-10-02):** F3, F5 and the new F20 (harness kit) were built ahead of the rest of R2 because they share one scaffold/check
> mechanism and the harness research gave them a verified basis. **Later the same day the rest of R2 (F6, F7, F14, F15) and the model-call layer (F21) were built.**
> See [`research/harness-engineering.md`](research/harness-engineering.md) and [`field-notes.md`](field-notes.md).

### 5.1 Build status (verified, with the gaps stated)

**R0 · Trust — complete**

| Item | Evidence |
|---|---|
| R0-1 symlink confinement | `tests/test_symlink_confinement.py`; walker skips out-of-project symlinks (CLI, server, tree) |
| R0-2 web client in wheel | `tests/test_packaging.py` builds from a clean copy; mutation-checked |
| R0-3 Host allowlist, Origin check, token | `tests/test_guard.py` (24) + `tests/test_serve_auth_subprocess.py` (real server); Host and Origin checks mutation-tested |
| R0-4 one shared walker | `fileflow/walker.py`; `tests/test_walker.py` — prompt, tree and project count agree on nested `.gitignore` |
| R0-5 relative served paths | `tests/test_server_hygiene.py` — no absolute path in any format |
| F8 secret guard | `fileflow/secretscan.py`; precision table in `tests/test_presets_cli.py`; value never printed (mutation-tested) |

**R1 · Frame the task — complete for the CLI**

| Item | Evidence |
|---|---|
| F1 presets | `tests/test_presets_cli.py`: one test per precedence row, paths confined (incl. symlink), unknown-preset suggestions |
| F2 instructions + vars + lint | hand-written render oracle (`test_instruction_render.py`) **and** golden corpus asserted byte-equal in Python **and** JS |
| F4 receipt | completeness (every file exactly once), closed reasons, deterministic, no absolute paths, verified/assumed/unknown ledger |
| F9 `--copy` | tested with a stub clipboard and the no-clipboard fallback |
| F12 error taxonomy | `fileflow/errors.py`; every code has an exit code; each error prints a next step |
| F13 estimate wording | CLI summary/clipboard line and web UI (`formatTokens`) |


**R2 · Start & feed — complete (plus `ask`)**

| Item | Evidence |
|---|---|
| F6 `fetch` | `tests/test_fetch.py` (132): address policy table (28 addresses incl. IPv4-mapped/NAT64/6to4/Teredo), numeric-IP tricks, pinned connection with no second DNS lookup, per-hop redirect validation, HTTPS verified against the original hostname (real self-signed server), caps, slow-body and slow-header drip, extraction (hidden text, invisible Unicode), CLI confinement. **15 controls mutation-tested.** |
| F7 provenance | hand-written render oracle + golden corpus (7 render scenarios, 24 parse cases) asserted byte-equal in Python **and** JS; fuzz generates random front matter |
| F14 `[include]` | `tests/test_include.py`; glob semantics pinned by 40+ golden cases; fuzz generates random include patterns; the web client preserves `[include]` on save |
| F15 `--diff` etc. | `tests/test_gitdiff.py`: hostile `.git/config` (fsmonitor, external diff, textconv) proven not to execute — each protection fails a test when removed; option injection through `--since` |
| F21 `ask` | `tests/test_ask.py` (83) against mock providers; **16 safety mutations**, one of which first *survived* (see below) |

**Deliberate differences / honest gaps for R2**

1. **`ask` has never talked to a live provider.** Request/response shapes follow the providers' documented formats (OpenAI chat completions; Anthropic Messages — the latter confirmed through consistent secondary sources because the official page was too long to reach the request section). First real call = a test.
2. **Provenance labels are carried by Python, the server and the web client's engine, but the static (offline) web client does not yet call `provenanceMap` on imported files**; only the engine function exists there. The server-mode client shows the labels in the prompt text.
3. **`fetch` ignores `HTTP(S)_PROXY`** (it must connect to the address it validated). Corporate-proxy users can't use it as is.
4. **`--diff` cannot make git safe inside an attacker-controlled `.git/config`** (config-defined filters can still run).
5. **The `ask` deadline covers reading the body; the connect/header phase is bounded only by the socket timeout** (acceptable for an endpoint you chose; `fetch`, whose URL is untrusted, uses a hard watchdog instead).
6. **`ask` has no tools, memory, loop or MCP** — by decision (ADR-010). Code review is `ask --diff --patch` with a review preset.

**Bugs found by building R2 (all fixed, each with a regression test)**

- **Slow-drip DoS in `fetch`**: `HTTPResponse.read(8192)` blocks until 8192 bytes and a per-recv timeout resets on every byte, so one byte every 150 ms held a fetch open for 15 s past a 1 s limit; a header drip would have evaded any body-level check. → hard watchdog + `read1`.
- **Watchdog race**: the watchdog could fire after the response finished and crash on a cleared socket. → own socket reference + finished flag.
- **CLI/web divergences that predate this work** (found by adding `\r`, form feed, U+2028, NEL, FS and emoji to the fuzz alphabet): Python's `str.splitlines()` also breaks on those separators and re-joined with newlines, so `--line-numbers` silently *rewrote* files; JS didn't. → line numbering splits on `\n` only; line endings are normalised to `\n` on read in both engines.
- **UI save would have blanked `[include]`** (the writer rebuilt it from a payload that never contained it). → omitted sections are preserved; the client now sends it.
- **`--base-url ""` silently used the default host** (an unset shell variable would have decided where your files go). → refused.
- **Redirect test passed for the wrong reason** (a 307 is never followed on a POST, so the test passed with redirects fully enabled). → 301/302/303 added; verified to fail without the protection.
- **Mutation survivors that were really test gaps**: the IPv4-mapped rule was invisible on this Python (stdlib already rejects it; 3.13 changed that) and a file named `__proto__` could lose its label — each now has a test that fails without the code.

**Deliberate differences from the spec above (decided while building)**

1. **Secret guard has four modes, not three.** `block` fails closed (exit 3, nothing emitted) exactly as specified; a separate `exclude` mode leaves the offending file out and carries on. `SECRET_BLOCKED` is the receipt reason for `exclude`.
2. **Receipt has no `provenance` field and no `--embed-receipt` yet.** Provenance arrives with F7 (R2); `--embed-receipt` was dropped from R1 rather than shipped half-done.
3. **Web parity for the receipt is not built** (the A4 "same JSON in the web client" criterion moves to R3 with the receipt panel). Instruction rendering **is** at byte parity.
4. **Unresolved-placeholder lint covers `{{name}}` only.** The `strict_blanks` option for `[your role]`-style blanks was not built.
5. **Excluded directories appear once as `dir/`** in the receipt (not once per file inside); "every file exactly once" is checked against what the walker actually met.
6. **Error codes defined but not yet raised:** `E_PRESET_CYCLE` (needs R4 `requires`) and the four `E_FETCH_*` codes (R2).

**Bugs found by building this (all fixed, each with a regression test)**

- `fileflow --preset hero` failed: Click parsed leading options as *group* options before routing to `prompt` (so `fileflow --format xml dir` had never worked either).
- Preset paths were rendered absolute, leaking the local directory layout into the prompt.
- JSON output escaped non-ASCII in Python but not in JS, and token estimates/truncation counted astral characters (emoji) differently — **93 of 200 fuzz cases diverged** once non-ASCII was added to the fuzz alphabet.
- The web UI's config save would have deleted presets; the writer now preserves everything it doesn't manage.
- A readiness probe in my own test passed for the wrong reason (WebSocket test client sends `Host: testserver`) — found by mutation-testing the Origin check.

### 5.2 Release plan

| Release | Theme | Contents | "Done" means | Maturity |
|---|---|---|---|---|
| **R0 · v0.2.1** | **Trust** ✅ | R0-1 R0-2 R0-3 R0-4 R0-5 F8 | A stranger can run `serve --allow-remote` and `prompt` on a repo without leaking anything we know about | verified system |
| **R1 · v0.3** | **Frame the task** ✅ (CLI) | F1 F2 F4 F9 F12 F13 | Preset → bundle → receipt → clipboard in one command; golden corpus covers instructions + receipt in Python **and** JS | verified system |
| **R2 · v0.4** | **Start & feed** ✅ | F3 F5 F6 F7 F14 F15 (+ F21 `ask`) | New folder → first prompt in ≤ 60 s; fetched content carries provenance end-to-end | verified system |
| **R3 · v0.5** | **Focus surfaces** | F16 F17 F18 | Web and VS Code can run a preset without the CLI; parity tests green | verified system |
| **R4 · v0.6** | **Resume** | F11 F19 | `next` used for real by ≥ 2 people | **usage-proven** *(only then)* |

Honest status today: fileflow is a **verified system for the local-file case**
(260+ Python tests, 4 Node suites, cross-engine golden + fuzz parity) but **not usage-proven** —
no external user has run it end-to-end yet. R4 is where that changes.

---

## 6. Measuring success without telemetry

fileflow is local-first, so **no usage telemetry**. Evidence comes from observation:

| Metric | How | Target |
|---|---|---|
| Time-to-first-prompt | Stopwatch in a 3-person usability run, fresh folder | ≤ 60 s |
| Re-entry time | "Close the laptop, return tomorrow, find the next step" | ≤ 10 s |
| Silent omissions | Property test (included ∪ excluded ∪ truncated = candidates) | 0 |
| Secret leaks in golden fixtures | Fixture repo with planted secrets | 0 emitted |
| Parity | Golden + fuzz, Python vs JS | 0 divergences |
| Real corrections | `docs/field-notes.md` — every confusing moment logged with the fix | ≥ 10 entries before R4 |

---

## 7. What could go wrong (adversarial)

1. **Scope creep into an AI product.** Playbooks, fetch and templates all *smell* like an agent framework. → The alignment filter (§1.3) and ADR-007 (*never calls an LLM*) are the brake; review every new verb against them.
2. **`fetch` becomes an SSRF/exfiltration hole.** → CLI-only, private-IP refusal, redirect re-validation, size/time caps, not exposed in `serve`; a test with `http://127.0.0.1`, `http://169.254.169.254`, and a redirect to each.
3. **Template variables become a mini-language.** → Pure `{{name}}` replacement; no logic, no includes. Anything more is rejected in review.
4. **Receipt gives false comfort.** A receipt proves *what was packaged*, not that the AI will behave. → The ledger has an explicit **unknown** list; wording avoids "safe"/"guaranteed".
5. **Secret scan misses or mis-flags.** → High-confidence patterns only, documented limits, listed as *assumed*; `block` is opt-in so false positives can't silently break flows.
6. **Provenance labels get treated as truth.** `observed` means "fetched from here", not "correct". → Wording in docs and prompt attributes; the PG report example below shows why.
7. **Parity debt explodes.** Every new rendering feature doubles work (Python + JS). → Golden corpus first, implementation second; CLI-only features must declare why (A4).
8. **ADHD-friendly becomes a gimmick.** → It's defined as testable rules (§2) and metrics (§6), not as emoji or tone.
9. **Gated F11 is quietly built anyway.** → Gate E is a written entry condition; the backlog shows confidence 2/5 on purpose.

---

## 8. Appendix — what the Paul Graham report teaches us about fileflow itself

The report is a good *example input* and a good *warning*. Reading it as an
artifact (not as advice) shows exactly why the Receipt/`check` ideas matter:

| Observation in the report | Why it matters to fileflow |
|---|---|
| States **"20 articles analyzed"**, but only **19 distinct IDs** exist (A1–A4, B1–B5, C1–C3, D1–D4, E1–E3); **B3 appears at rank 6 and again at rank 20** ("see above") | A packaged context should *count what it contains*. Receipt totals are computed, not asserted. |
| Executive summary's "Top 5" order differs from the ranked table (summary: Great Work, Superlinear, Hard Work, Think, Bus Ticket; table: A1, B1, A2, B2, A3) | Two views of the same data drifted. → single source of truth + `check`. |
| Scores like "15/15", "14/15" are unexplained | False precision. fileflow labels estimates as estimates (F13). |
| "Evidence" lines per essay are model-written summaries | They are **generated**, not **observed**. If saved to `content/`, provenance must say so (F7). |
| "High-priority prompts" cover 6 of the 9 items marked HIGH | A completeness claim nothing verified. `check`'s "every playbook step has a preset" is the same idea. |
| Prompt templates contain `[your role/stage]`, `[X years]` blanks | Exactly the half-filled-template failure F2's lint prevents. |

**How this report would flow through fileflow (end to end):**

```bash
fileflow init --template learn
fileflow fetch https://paulgraham.com/greatwork.html --into content/essays   # provenance: observed
# save the analyzer's report as content/reports/pg-report.md with: provenance: generated
fileflow --preset frontier-audit --var domain="backend engineering" --var role=developer --receipt --copy
#   receipt: 2 files · 1 observed · 1 generated · 0 secrets · ≈6,100 tokens (estimate)
fileflow next        # (R4) → "Step 2/6: Hard Work Inventory — 45m — success: deep work ≥ 30%"
```

Note what fileflow did **not** do: choose the essays, score them, summarise them,
or build the learning path. Those stay in the analyzer; fileflow makes their output
safe and cheap to use.

---

## 9. 5-Lens score (for the expanded roadmap)

| Lens | /5 | Notes |
|---|---|---|
| Leverage | 5 | Presets + receipt benefit every surface (CLI, web, IDE) and every future workflow. |
| Rarity | 4 | "Verified, provenance-aware context packager" is uncommon; plain concatenators don't offer receipts. |
| Compounding | 5 | Each release feeds the next: presets → receipt → check → playbooks. |
| Transferability | 4 | Evidence-ledger + gates pattern transfers to the ATS Analyzer and other agentic tools. |
| Urgency | 3 | Trust fixes (R0) are urgent; the rest is not competitor-driven. |
| **Total** | **21/25** | **Build R0→R3 now; gate R4 on real usage.** |

### Assumptions (explicit — correct me if wrong)
1. The primary user is a solo builder pasting into a chat/agent UI, not an autonomous agent with API access.
2. Python 3.8+ CLI stays the source of truth; the JS engine mirrors it under the golden contract.
3. Clipboard/network features may be unavailable (headless, offline) and must degrade gracefully.
4. "ADHD-friendly" is a design constraint serving everyone; it does not imply a medical claim.

### Decisions still open
- Should `generated` provenance content be **excluded by default** from `block`-mode presets? (Proposed: no — label, don't hide.)
- Receipt on **stdout vs stderr** when `--output-file` is used. (Proposed: stderr always.)
- Preset packs: bundled only, or installable from a path/URL? (Proposed: bundled first.)

---

## 10. Reuse prompt — implementing R1 with this repo

```
### TASK: Implement fileflow R1 (presets, instructions, receipt)
Context: docs/product-roadmap.md §4 (F1, F2, F4, F12, F13), ADR-006/007.
Requirements:
1. Extend fileflow/server/config.py for `version`, [presets.*], [secrets]; strict types, lenient unknown keys.
2. cli.py: --preset, --instruction, --instruction-file, --var, --receipt, --receipt-file, --copy.
3. Precedence: CLI flags > preset > root config > defaults (one test per row).
4. Render instructions as `# Task` / <task_instructions> / "instructions" in default/xml/json.
5. Receipt: closed reason-code enum; included ∪ excluded ∪ truncated == candidates (property test).
6. Golden corpus FIRST (Python + JS byte-equal), then implementation.
7. Error taxonomy with exit codes; every error says what/why/next command.
Gate: docs/engineering-contract.md must pass before merge.
```
