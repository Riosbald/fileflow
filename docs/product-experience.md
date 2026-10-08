# fileflow — Product Experience: Frontend, Backend, Tools, Journeys, Developer Flow, Integrations

> **Companion to** [`production-architecture.md`](production-architecture.md) (strategy, models, agents, data,
> memory, RAG, deployment) — read its §0 first. **Status tags:** **[Built]** exists and is tested ·
> **Proposed** designed here, not built. Nothing proposed is promised for a date; phases are evidence gates
> (see that document's §11).

---

## 1. Experience principles (testable, not decorative)

| # | Principle | How we test it |
|---|---|---|
| 1 | **One decision per step.** Defaults work; flags are for experts. | A first-time user reaches a correct bundle in ≤ 3 commands / ≤ 60 s |
| 2 | **Say what happened, why, and the next command.** | Every error has a class, an exit code and a `Next:` line **[Built]** |
| 3 | **Show the evidence, don't ask for trust.** | Every bundle can show its receipt; totals are computed, never asserted **[Built]** |
| 4 | **Safe by default, explicit to relax.** | Off-machine sends default to fail-closed secrets and need consent **[Built]**; only the command line may relax |
| 5 | **No hidden state.** | Everything is a file you can read, diff, commit and delete **[Built]** for config/presets/answers |
| 6 | **Resume in seconds.** | "Where was I?" is one command (`next`/`status`) — gated on evidence (see roadmap F11) |
| 7 | **Works offline and on a thin link.** | Core and web client function with no network; no CDN-only assets **[Built]** for the core |
| 8 | **Estimates are labelled estimates.** | "≈ N tokens (estimate)" everywhere **[Built]** |
| 9 | **Keyboard-first, accessible.** | WCAG 2.2 AA target; every action reachable without a mouse; reduced-motion respected |

---

## 2. Frontend

### 2.1 Surfaces
| Surface | Role | Status |
|---|---|---|
| **CLI** | The primary interface: scripts, CI, power users | **[Built]** |
| **Web client** (static or served) | Visual bundle building, preview, config; zero-install demo | **[Built]** core; picker/receipt/ask/activity **Proposed** |
| **VS Code extension** | Generate / copy / watch from the editor | **[Built]** (3 commands, no tests); preset QuickPick + receipt view **Proposed** |
| **CI action + PR comment** | The gate and the evidence in the place reviewers already look | **Proposed (P1)** |
| **MCP server** (stdio, read-only) | Lets coding agents pull context through the governed path | **Proposed (P2)** |
| **Team console** (S2) | Policy, audit trail, activity feed, budgets | **Proposed (P3)** |

### 2.2 Web client — what exists and what is added
**[Built]** sidebar (project tree, drag-and-drop import, add file), options (hidden files, `.gitignore`,
line numbers, separators, format, ignore chips, token budget, *Live server* toggle), editor panel, output
panel (meta line with estimate wording and secret findings, copy, copy-as-CLI, download), light/dark theme,
live reload from `.files-to-prompt`, config round-trip that preserves presets and `[include]`.

**Proposed screens**
| Screen | Purpose | Key elements |
|---|---|---|
| **Workspace** (extend) | Build one bundle | **Preset picker** + **Focus bar** (current preset · budget used · Copy) · include/diff chips · receipt drawer |
| **Receipt drawer** | Answer "what went in / out / why" in 5 s | counts by reason; click a reason to see files; verified/assumed/unknown ledger; provenance chips; **Verify signature** (P2) |
| **Ask panel** | The one outbound action, made safe and legible | destination (host, provider, model, tier); **what will leave** (files, tokens ≈); secret-guard status; **consent** button; answer viewer; **Save as generated** |
| **Check dashboard** | Harness health | findings grouped by severity; fix hints; link to the file:line; trend over runs (S2) |
| **Policy** (S2) | Org rules | allow-listed destinations, classification rules, required modes; diff view of changes; dry-run evaluate |
| **Activity** (S2) | Real-time feed | event stream with filters (project, actor, type); never shows content |
| **Settings** | Per-user defaults | tier, format, budget, theme, privacy toggles |

```
┌ fileflow ─────────────────────────────────────────────────────────── ◐ theme  ─┐
│ PROJECT          │ PRESET [ hero ▾ ]   budget ▓▓▓▓░░ 8.2k/12k ≈   [ Copy ] [Ask]│
│ ▸ components     ├───────────────────────────────────────────────────────────────┤
│   ▸ hero         │  # Task                                                       │
│     Hero.jsx  ✓  │  Improve the hero section only …                              │
│     hero.css  ✓  │  # Files                                                      │
│ ▸ notes          │  components/hero/Hero.jsx [provenance: user] …                │
│   project-notes ✓│                                                               │
│ OPTIONS          ├─ RECEIPT ▾ ───────────────────────────────────────────────────┤
│ ☐ hidden  ☑ gitignore │ sent 3 files · ≈ 612 tokens (estimate)                    │
│ include: *.jsx ✕ │ left out  GITIGNORED 3 · HIDDEN 3 · PATTERN 2   (click to see) │
│                  │ ⚠ possible secret in notes/aws.txt (aws-access-key-id) — value │
│                  │   not shown      assumed: heuristic tokens · unknown: model…  │
└──────────────────┴───────────────────────────────────────────────────────────────┘
```

**Consent dialog (Ask) — wording is part of the safety feature**
```
Send to a THIRD PARTY?
  3 files · ≈ 612 tokens (estimate)  →  api.example.com   (provider-b · model-x · tier T2)
  Secret guard: block — 0 findings      Policy rule: internal → allow-listed
  Nothing leaves until you press Send.            [ Cancel ]   [ Send ]
```

### 2.3 States every screen must handle
Empty (no files / no presets → show the `init` next step) · Loading (skeleton, never a blank) · Offline
(static mode banner; Ask disabled with the reason) · Error (class + next step, never a stack trace) ·
Partial (budget truncated, secrets excluded → shown, not hidden) · Large (virtualised tree/list; streaming
preview; caps already built) · Blocked-by-policy (show the rule and who can change it).

### 2.4 Quality bars
Accessibility (WCAG 2.2 AA, focus order, ARIA on the tree, contrast tokens, reduced motion) · performance
(first render < 1 s on a mid-range phone over a throttled link; no framework runtime required for the
static client) · i18n-ready strings (English first) · **no third-party calls from the page** (privacy and
offline) · every state covered by the DOM-shim tests, plus **real-browser E2E (P0)**.

### 2.5 CLI experience conventions
`--json` on every command; colour only on a TTY; exit codes by class (1 I/O, 2 usage/config, 3 security
refusal, 4 network/provider) **[Built]**; one `Next:` line on success and failure **[Built]**; destructive or
outbound actions need an explicit flag or a prompt **[Built]**; help text shows an example, not just flags.

---

## 3. Backend

### 3.1 Local server [Built]
FastAPI, pinned to its root; Host/Origin guard + token for remote; `/api/{health,project,tree,file,prompt,
config,watch}`; no `fetch`/`ask` endpoints (asserted by tests); static client mounted last.

### 3.2 Team plane (S2) — Proposed
**API (versioned `/v1`, JSON, RFC 9457 `application/problem+json` errors that reuse the `E_*` taxonomy)**
| Endpoint | Purpose | Auth scope |
|---|---|---|
| `POST /v1/receipts` | Ingest a (signed) receipt; idempotent on its hash | `ingest` |
| `GET /v1/receipts` · `GET /v1/receipts/{id}` | Query / fetch; paths and hashes only | `read` |
| `POST /v1/receipts/{id}/verify` | Re-verify signature and chain | `read` |
| `POST /v1/events` (batch) · `GET /v1/events/stream` (SSE) | Ingest activity / live feed | `ingest` / `read` |
| `GET/PUT /v1/policy` (ETag, versioned) · `POST /v1/policy/evaluate` | Policy as data + dry-run | `read` / `admin` |
| `POST /v1/ask` | Gateway call with policy + budget enforcement and consent token | `ask` |
| `GET /v1/routes` | Effective tier/route table | `read` |
| `GET /v1/audit/export` | JSONL/OTLP export for a SIEM | `audit` |
| `POST /v1/index/jobs` · `GET /v1/search` (S3) | Build/query the governed index | `index` / `read` |
| `/v1/admin/*` | orgs, projects, members, service tokens, virtual keys | `admin` |

**Cross-cutting:** OIDC bearer for humans, **scoped service tokens** for CI (project + verb); `Idempotency-Key`
on writes; per-token rate limits; request-size caps; optimistic concurrency (ETag) on policy; pagination by
cursor; audit log of every admin action.

**Data model (Postgres, row-level security by `org_id`)**
```
orgs ─< projects ─< receipts(id, sha256, project_id, actor, created_at, json[encrypted], signature, chain_prev)
  │         │   └─< events(id, type, project_id, actor, time, data jsonb, trace_id)  [append-only, monthly partitions]
  │         └─< policies(version, project_id, body, created_by, created_at)           [immutable versions]
  ├─< members(user, role)        ├─< service_tokens(scope, project_id, expires)
  └─< model_routes(tier, rule, provider, model, pinned_version, budget)   └─< eval_runs(suite, model, score, cost, at)
```
Receipts hold **paths, hashes, counts, reasons — never content**; the JSON is encrypted per tenant; an
org-level option hashes paths for stricter privacy. Retention and erasure per org.

**Workers:** a Postgres-backed job queue for index builds, exports and eval runs; **idempotent** jobs;
dead-letter table; metrics per queue.

**Signed receipts (P2, designed here):** receipt v2 adds `model_call.{tier,route,policy_rule}`, `signature`,
and an optional `chain_prev` hash chain. Signing: **Ed25519** with a project key (public key committed in
`.fileflow/trusted-keys`), or **sigstore keyless** in CI (OIDC identity). `fileflow receipt verify` works
**offline** — the auditor needs no account.

**Policy as code (P2, designed here)** — a file in the repo, reviewed like code:
```toml
# .fileflow/policy.toml  (Proposed)
version = 1
[classify]                      # path globs -> classification
restricted = ["payments/**", "**/*.pem", "customers/**"]
internal   = ["**/*"]
[destinations]                  # where each class may go
restricted = ["local"]          # local models only
internal   = ["api.provider-a.example", "api.provider-b.example"]   # allow-listed, ZDR terms
[require]
secrets = "block"               # for any non-local destination
consent = true
check   = "strict"              # `check --strict` must pass before `ask`
max_tokens = 24000
```
`fileflow policy explain` shows *which rule* allowed or blocked a send; the receipt records it.

### 3.3 Error and exit semantics
One taxonomy across CLI, server and gateway: config/usage (2), security refusal (3), network/provider (4),
I/O (1). HTTP mapping: 400/401/403/409/422/429/502/504 each tied to a code; **every error carries a
`next` hint**. No stack traces cross a boundary.

---

## 4. Tool catalogue — functions and usability

### 4.1 Commands
| Command | What it does | Key options | Output / exit | Status | Usability note |
|---|---|---|---|---|---|
| `fileflow [PATHS]` (`prompt`) | Build the bundle | `--preset/-p` · `--instruction[-file]` · `--var` · `--include` · `--diff/--staged/--since/--patch` · `--secrets` · `--max-tokens` · `--format` · `--receipt[-file]` · `--copy` | prompt on stdout; receipt on stderr; exit by class | **[Built]** | `fileflow --preset hero --copy` needs no other flag |
| `fileflow presets` | List saved tasks | `--json` | name · description · budget | **[Built]** | The menu: what can I run right now? |
| `fileflow init` | Scaffold | `--template minimal\|web-sprint\|harness` · `--force` · `--no-claude` · `--list` | created/skipped list + **one** next step | **[Built]** | Never overwrites; ends with exactly one command |
| `fileflow check` | Verify config + harness | `--strict` · `--json` · `--root` | findings + verified/assumed/unknown; exit 0/2 | **[Built]** | CI-friendly; errors only for what truly breaks |
| `fileflow fetch URL…` | Save pages with provenance | `--into` · `--name` · `--allow-private` · `--max-bytes` · `--timeout` | Markdown file + front matter | **[Built]** | Refuses private targets & challenge pages; says why |
| `fileflow ask` | One model call, opt-in | `--provider` · `--model` · `--base-url` · `--api-key-env` · `--system` · `--save` · `--yes` · all `prompt` options | answer on stdout; usage + receipt on stderr | **[Built]** (mock-tested) | Shows destination and consent before anything leaves |
| `fileflow serve` | Local web client + API | `--root` · `--host` · `--port` · `--allow-remote` · `--token` · `--allowed-host` | URL (+ token) | **[Built]** | Local by default; token required when remote |
| `fileflow receipt verify\|diff\|sign` | Prove / compare / sign receipts | `--key` · `--json` | ok / tamper report | Proposed (P2) | The auditor's one command |
| `fileflow policy check\|explain` | Evaluate a send against policy | `--dry-run` | allow/deny + the rule | Proposed (P2) | Makes "why was this blocked?" answerable |
| `fileflow mcp serve` | Read-only MCP server (stdio) | `--root` | MCP tools: `pack`, `check`, `list_presets`, `get_receipt` | Proposed (P2) | Governed context for agent hosts |
| `fileflow index build\|search\|status` | Governed retrieval (S3) | `--rebuild` · `--top` | ranked chunks with receipt entries | Proposed (P4) | Only for corpora that don't fit |
| `fileflow memory ls\|show\|rm` | See and delete what is remembered | `--label generated` | list | Proposed (P4) | "No hidden memory" made literal |
| `fileflow doctor` | Environment + setup diagnosis | `--json` | what's missing and how to fix | Proposed (P1) | Cuts support load (git? clipboard? keys? write access?) |
| `fileflow next` / `status` | Where was I? | — | the single next step | Proposed (gated; roadmap F11) | Re-entry in seconds after a distraction |

### 4.2 Function inventory by outcome
| Outcome the user wants | Functions that deliver it |
|---|---|
| "Give the AI exactly this slice" | paths · presets · `--include` · `--diff/--staged/--since` · token budget · compression mode (P4) |
| "Tell it what to do" | `--instruction` · structured prompt tables · `{{vars}}` + unresolved lint |
| "Don't leak anything" | secret guard (4 modes) · off-machine default `block` · PII gate (P2) · local-only routes (P2) |
| "Prove what happened" | receipt (closed reasons, ledger) · provenance labels · signatures (P2) · audit export (P3) |
| "Keep my AI-facing repo healthy" | `check` (links, size, skills, permissions, drift) · harness template · CI action (P1) |
| "Bring knowledge in safely" | `fetch` (SSRF-safe, hidden-text removal) · reference packs · provenance |
| "Get an answer" | `ask` (single-shot, consented) · `--save` as `generated` |
| "Do it from where I work" | VS Code · web client · MCP server · PR comment |

---

## 5. User journeys

> For each: **trigger → steps → what the system does → success → failure and recovery.**

### 5.1 Solo builder ("one section at a time") — the free on-ramp
| Stage | User does | System does | Success |
|---|---|---|---|
| Discover | `pipx install fileflow` | — | installed < 1 min |
| First bundle | `fileflow init --template web-sprint` → `fileflow --preset analyze --copy` | scaffolds; one **Next:** line; copies the bundle; prints "≈ N tokens (estimate)" | **first prompt ≤ 60 s** |
| Frame | edits `notes/project-notes.txt`; runs `--preset hero` | bounded bundle with the instruction first | one section per prompt |
| Feed | `fileflow fetch <url>` | saves labelled Markdown; refuses a bot-challenge page and says so | content has an origin |
| Verify | `--receipt` | shows what was left out and why | no silent omissions |
| Return tomorrow | opens the project | (gated) `fileflow next` | re-entry ≤ 10 s |
**Failure/recovery:** unresolved `{{var}}` → exit 2 with the exact `--var` to add; secret found → warned with file and rule, never the value.

### 5.2 Team engineer — the governed handoff (UC1/UC3)
| Stage | User does | System does | Success |
|---|---|---|---|
| Need AI help | `fileflow ask --preset review --diff --patch --save notes/review.md` | builds bundle; **secrets default to block** off-machine; shows destination + consent | review text saved as `provenance: generated` |
| Blocked | secret in the diff | exit 3; names file and rule; suggests rotate/`--secrets exclude` | nothing left the machine |
| Policy | `restricted` path included | policy says local-only → routes to T0 or refuses with the rule | decision is explained |
| Share | opens the PR | CI action posts receipt summary + `check` result | reviewer sees *what the AI saw* |
**Failure/recovery:** provider error → classified (auth/rate/network/timeout/response) with a next step; fallback tier if policy allows; never an automatic paid retry.

### 5.3 Tech lead / platform engineer — the gate (UC2)
Adds `fileflow/check@v1` to CI → first run lists warnings (blanks, prose-only permissions, oversize
AGENTS.md) → fixes the three that matter → flips to `--strict` → the repo's AI-facing layer is
**continuously verified**. Success: gate green for 4+ weeks; findings trend down; no one disabled the
scanner. Recovery: a false positive is a bug report that becomes a regression test (the FN-001 pattern).

### 5.4 Security / compliance officer — the evidence (UC1)
| Stage | Action | System |
|---|---|---|
| Set policy | reviews `.fileflow/policy.toml` in a PR | versioned, diffable, `policy explain` |
| Audit | `fileflow receipt verify` on a sample; or S2 audit export | offline verification; paths/hashes only, no content |
| Incident | "what did we send last Tuesday?" | query receipts + events by actor/time; destination hosts listed |
| Compliance | needs a transfer record / DPIA input | receipts are the transfer log; template DPIA supplied |
Success: **one reviewer accepts a receipt as evidence** (the P2 exit gate). Recovery: tampered or unsigned receipt → verification fails loudly.

### 5.5 Integrator / developer — extend it
Reads `AGENTS.md` → clones → `pytest` + Node suites green → adds a **preset pack / template / secret rule /
provider adapter / event sink** → golden-first if it changes rendering → mutation-tests any safety code →
opens a PR; CI enforces gates. Success: a new provider adapter passes the **adapter contract tests** with a
recorded-fixture suite and a live smoke test behind a flag.

---

## 6. Developer functions and flow

### 6.1 The loop (as in the repo's own harness)
```
 read AGENTS.md → plan (docs/exec-plans/active/) → golden-first if rendering changes
   → implement smallest slice → run checks (pytest · node suites · fuzz · check --strict)
   → MUTATE the safety code, watch a test fail → update docs/ADR/field-notes → PR
```
**Done means:** all checks green; docs match behaviour; any new safety check mutation-tested; roadmap status
claims only what tests prove. **Stop and ask** when a public format, a security boundary or a permission
changes.

### 6.2 Extension points
| Point | Contract | Safety rule | Status |
|---|---|---|---|
| **Preset packs / templates** | `[presets.*]` schema (versioned) | paths confined; no code execution | **[Built]** schema; pack distribution Proposed |
| **Custom secret rules** | `[secrets.rules]` regex + name + severity | tested against a precision corpus; value never printed | Proposed (P2) |
| **Provider adapters** | `build_request(...)` / `parse_response(...)` / key + URL policy | redirects refused; https except loopback; recorded-fixture contract tests | Partly **[Built]** (2 adapters); registry Proposed |
| **Event sinks** | consume the event envelope (JSONL / OTLP / webhook) | no content in events | Proposed (P3) |
| **Receipt consumers** | JSON Schema for `receipt_version` | forward-compatible, closed reason enum | **[Built]** v1; schema file + v2 Proposed |
| **Checks** | `check` rule = function returning findings with level/code/hint | errors only for what definitely breaks; cites its source | **[Built]** internal; plugin API Proposed |
| **Python API** | `from fileflow import collect, render_documents, build_receipt` | stable subset documented; semver | Importable today; stability promise Proposed |
| **MCP tools** | `pack`, `check`, `list_presets`, `get_receipt` (read-only, confined, stdio) | local only by default; remote needs OAuth 2.1; host-mediated sends are marked `destination: host-mediated` in the receipt | Proposed (P2) |

### 6.3 Contracts that must never silently change
Rendered output (golden corpus, byte-equal in Python and JS) · receipt schema and reason enum · config
`version` · error codes and exit codes · event envelope. Changing any of them is a versioned change with a
migration note — **golden-first**.

### 6.4 Release flow
`main` protected → CI (matrix: 3 OSes × Python 3.8/3.11/3.12 + Node) → gates → tag → trusted-publishing to
PyPI + sigstore signature + SBOM + changelog stating the **maturity stage honestly** → staged rollout of any
hosted component behind feature flags → rollback plan. A human ticks the release checklist against the
engineering contract and records the verified commit.

---

## 7. Integrations and activities

| System | Direction | What crosses | Auth | Activities emitted | Status |
|---|---|---|---|---|---|
| **GitHub / GitLab** (Action, checks, PR comment) | out | `check` result, receipt summary (hashes/paths) | repo token (least scope) | `check.*`, `receipt.created` | Proposed (P1) |
| **GitHub/GitLab webhooks** | in | push / PR events | HMAC secret | triggers `check` / refresh | Proposed (P3) |
| **VS Code / JetBrains** | local | commands, receipts | none (local) | `bundle.built` | VS Code **[Built]** (minimal); JetBrains parked |
| **MCP clients** (Claude Code, Cursor, Codex…) | local, via `mcp serve` | packed bundle + receipt | stdio (host permissions) | `bundle.built` (host-mediated) | Proposed (P2) |
| **Agent instruction files** (AGENTS.md, CLAUDE.md) | files | the map and docs | n/a | `check.*` | **[Built]** (scaffold + check) |
| **Model providers** (OpenAI-compatible, Anthropic, local Ollama/vLLM) | out | the bundle (consented) | key from env; never config | `ask.*` | **[Built]** (mock-tested) |
| **LiteLLM gateway** | out (S2) | model calls with budgets | virtual keys | `ask.*`, cost | Proposed (P3) |
| **Identity** (Okta, Entra ID, Google; SCIM later) | in | identity assertions | OIDC | `admin.*` | Proposed (P3) |
| **SIEM / log stack** (Splunk, ELK, Grafana/Loki) | out | audit events / OTLP | token / mTLS | all | Proposed (P3) |
| **Chat** (Slack, Teams) | out | alerts (policy violated, budget burn) | webhook | `policy.violated` | Proposed (P3) |
| **Issue trackers** (Jira, Linear) | out | link receipt id to a ticket | OAuth | — | Parked |
| **Docs/KB** (Notion, Confluence, wikis) | in (via `fetch`/export) | pages as labelled Markdown | export or token | `fetch.saved` | `fetch` **[Built]**; connectors parked |
| **Dependency intel** (OSV, SPDX) | in | advisories, licences | none (public) | annotations on bundles | Proposed (P3) |
| **Payments** (Paystack / Flutterwave / Stripe) | out (S2) | billing | provider keys | — | Proposed — **verify provider fit before committing** |
| **Object store** (S3-compatible) | out | exports | IAM | — | Proposed (P3) |

**Activity principle:** an integration that *receives* data gets receipts, hashes and counts — **not
content** — unless the user explicitly sends content to a model they chose.

---

## 8. Acceptance scenarios (what "done" means for P1–P2)

```gherkin
Scenario: governed handoff succeeds
  Given a repo with a policy that allows provider-a for "internal" files
  When I run `fileflow ask --preset review --diff --patch --yes`
  Then exactly the changed files and their patch are sent
  And the answer is saved as provenance: generated
  And the receipt lists every considered file exactly once with a closed reason
  And the receipt records destination, tier and the policy rule

Scenario: a secret blocks the send
  Given a changed file containing a credential
  When I run the same command
  Then exit code is 3, nothing is sent, and no secret value appears in any output

Scenario: restricted data never goes off-machine
  Given a file classified "restricted"
  When the bundle would be sent to a non-local host
  Then the send is refused with the rule that forbids it, or routed to a local model if configured

Scenario: the gate protects the AI-facing layer
  Given a pull request that adds a dangling link to AGENTS.md
  When CI runs `fileflow check --strict`
  Then the job fails with the file:line and a next step

Scenario: a receipt is evidence
  Given a signed receipt from last week
  When an auditor runs `fileflow receipt verify` offline
  Then it is accepted, and a modified copy is rejected

Scenario: works with the network down
  Given no network
  When I build a bundle and run `check`
  Then both succeed; `ask` explains it needs a link (or uses a local model)
```
