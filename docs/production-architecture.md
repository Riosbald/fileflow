# fileflow — From Prototype to Live: Product Focus, Architecture and Decisions

> **Role of this document:** the decisions a Principal AI Architect would make to take fileflow from a
> working prototype to a live product, with the reasoning, the evidence, and the conditions under which
> each decision should be revisited.
> **Companion:** [`product-experience.md`](product-experience.md) — frontend, backend API, tool catalogue,
> user journeys, developer flow, integrations. **ADRs 011–023** are in [`architecture.md`](architecture.md).
> **Status:** proposal. Only items tagged **[Built]** exist.
>
> **How to read the tags**
> **[Built]** verified in this repo, with tests · **[Researched]** from outside sources, *mostly secondary
> (blogs, vendor pages)* — verify before betting money on it · **[Assumption]** I am assuming it; correct me ·
> **[Decision]** my choice, with a revisit trigger.

---

## 0. TL;DR — the verdict and the choices

**My analysis of the idea, in one paragraph.** fileflow is not a better file-concatenator. Repomix,
code2prompt, gitingest and files-to-prompt already pack repositories, and Repomix already ships secret
scanning (Secretlint), token counting, Tree-sitter compression and an MCP server **[Researched]**. Competing
on packing is a race to the bottom. What fileflow has that those tools, as their own documentation
describes them, do not is **evidence**: a deterministic, test-pinned engine; a **receipt** that accounts for
every file with a closed set of reasons and a verified/assumed/unknown ledger; **provenance** labels
(observed / generated / user); hardened edges (root confinement, an SSRF-safe `fetch`, a consent-gated,
fail-closed `ask`); and a **harness check** for AGENTS.md-style instruction layers. That is a **context
governance** product: *the way a team proves what it sent to an AI, and keeps its AI-facing repo legible and
safe.* Everything below is built around that, and everything that does not serve it is parked.

| Area | What I would choose | Why (one line) | Revisit when |
|---|---|---|---|
| **Beachhead** | Engineering teams (5–200) at fintechs, agencies and vendors under compliance pressure, starting in Nigeria/West Africa | Real, current pain (NDPA 2023 / GAID 2025 transfer + DPIA duties) that a *receipt* and a *gate* answer directly | ≥3 pilots say the receipt is not what they pay for |
| **Product shape** | Local-first core + CI gate first; a hosted team plane only after the repo-resident stages are used | A server is the most expensive thing to be wrong about | Pilots ask for central audit they can't get from CI artefacts |
| **LLM routing** | Self-hosted **LiteLLM** gateway, BYOK, **sensitivity-based rules** (not cost-based ML routing) | Routing must be explainable and auditable, because the receipt records it | A golden eval proves a cascade saves money without quality loss |
| **Models** | Choose **tiers**, not names; pick the model per tier by **your own eval**; always keep a local open-weight floor | Names and prices churn monthly; the eval and the gate are what last | Every quarter, and on any provider incident |
| **Agent patterns** | Single call → prompt chain → routing → evaluator-optimizer. Orchestrator-workers only for read-only breadth. **No multi-agent swarm.** | Agents cost ~4×, multi-agent ~15× the tokens; parallel *writes* fail | An eval shows a measured gain greater than its cost |
| **Agency ladder** | A0 single-shot **[Built]** → A1 read-only tools → A2 propose-a-patch → A3 sandboxed apply+test | Each rung needs *enforced* permissions, not prose | A rung is only entered after its ADR and gate |
| **Memory** | Files in the repo + a derived, rebuildable index. **No memory vendor now.** | Vendor benchmarks contradict each other; plain files compete; files are diffable and auditable | Temporal questions ("what changed when") become a top user need |
| **RAG** | **Pack-first.** Retrieval only when a corpus exceeds budget: hybrid (BM25 + embeddings) + contextual chunks + rerank, permission-aware, receipt-accounted | Small corpora don't need RAG; fileflow's strength *is* the pack | A real corpus doesn't fit and recall@20 on your eval is below target |
| **Activity pipeline** | JSONL locally → Postgres outbox + `LISTEN/NOTIFY` for teams → NATS JetStream only past a measured threshold; **events carry no file content** | Smallest thing that works; privacy by construction | Sustained event rate or fan-out beyond what Postgres handles |
| **Observability** | OpenTelemetry (OTLP) with `gen_ai.*` **pinned to a version**; self-hosted Grafana stack + Langfuse for LLM traces | OTLP is stable; the GenAI conventions are still "Development" | The GenAI conventions reach a stable release |
| **Hosting** | Control plane in AWS Cape Town (or a Lagos colo) + a **self-host bundle** for regulated buyers; static web on a CDN | No hyperscaler region exists in Nigeria; Cape Town is ~80–120 ms away **[Researched]** | A Nigerian region appears, or a buyer requires in-country data |
| **Packaging** | **Open core:** engine, CLI, web client Apache-2.0 (as today); paid = team plane (policy distribution, audit store, SSO, hosted gateway) | Trust needs the core to be inspectable; money is in the shared plane | Pilot willingness-to-pay says otherwise |

**The one rule above all:** *no stage is built until the one before it is used by real people and has
produced evidence* (Gate E in [`engineering-contract.md`](engineering-contract.md)). fileflow is a
**verified system, not yet a usage-proven one**; everything in this document is about changing that
honestly.

---

## 1. My analysis of the idea

### 1.1 What the product really is
Three layers, in order of defensibility:

| Layer | What it is | Defensible? |
|---|---|---|
| **Packing** (select, shape, render) | `prompt`, presets, include/diff, formats | **No.** Commodity; four free tools do it. Keep it excellent and boring. |
| **Evidence** (receipt, provenance, parity, tests) | Closed-reason receipts, verified/assumed/unknown ledger, provenance labels, byte-identical CLI/web contract | **Yes, but only if buyers value proof.** This is the differentiator. |
| **Governance** (policy, gates, audit, harness hygiene) | `check --strict`, secret fail-closed, repo-resident policy, org audit trail | **Yes, and it is where businesses pay.** Not built beyond the repo level. |

### 1.2 Honest strengths and weaknesses
**Strengths [Built]:** a contract tested to byte parity across two engines (golden corpus + fuzz that found
real bugs); security work that was *proven by mutation* (15 SSRF controls, 16 model-call controls, git
hardening); deliberately small blast radius (the model client is imported by one command only).

**Weaknesses:** no external user has used it (Gate E is open); `ask` has never met a live provider; the web
client has never run in a real browser; no CI run on GitHub; Windows untested; the receipt is not yet a
*portable, verifiable* artefact (no signature, no `receipt verify`).

**The strategic risk:** IDE agents (Claude Code, Cursor, Codex, etc.) increasingly fetch their own context,
which shrinks the need for *individuals* to pack files. A tool whose value is "pastes files into a chat"
can be absorbed. A tool whose value is "proves what left the building, enforces policy, and is the
audit record" cannot, because the agent vendors are not the party the auditor trusts to mark their own
homework.

### 1.3 What I would keep, change and stop
- **Keep:** the engine contract, receipts, provenance, `check`, `fetch`, the consent/fail-closed `ask`.
- **Change:** position and roadmap from "prompt packer for individuals" to **"governed AI handoff for teams"**; make the receipt a *signed, verifiable* artefact; ship a CI action before any server.
- **Adopt from the incumbents:** optional **Tree-sitter compression** (Repomix reports ~70% token reduction **[Researched]**) as an explicit, *receipt-visible* lossy mode; an **MCP server** so agents pull context through the governed path.
- **Stop / do not start:** a multi-agent swarm; a hosted general chat product; a memory vendor integration; a vector-DB-first design; an ML auto-router; anything that stores customer file content by default.

---

## 2. Laser focus: the real-life cases

### 2.1 Ranked use cases
| # | Use case | Who feels it | Why it is real | Evidence it works today |
|---|---|---|---|---|
| **UC1** | **Safe AI handoff** — send code/docs to an AI with a secret/PII guard and a receipt proving what left | Dev at a fintech/agency; the engineer's manager; the auditor | NDPA/GAID: recorded basis for transfers, DPIA for cross-border transfer **[Researched]**; every team pastes code into AI tools today | **[Built]** secret guard (4 modes), receipt, consent + fail-closed `ask`; repo-level tests |
| **UC2** | **AI-ready repo gate** — CI fails when AGENTS.md/docs/permissions/skills are broken, stale, oversized or unsafe | Tech lead / platform engineer | OpenAI's own write-up describes linters + CI for the knowledge base **[Researched]**; teams copy "AGENTS.md" and let it rot | **[Built]** `check --strict`; the repo runs it on itself |
| **UC3** | **Reviewable AI change** — `ask --diff --patch` review, answer saved as `provenance: generated`, attached to the PR | Reviewer; the dev | Review is the highest-value, lowest-risk place to use a model: read-only, human decides | **[Built]** the pieces; **not yet** a PR integration |
| UC4 | **Governed knowledge packs** for non-code teams (support, compliance, ops runbooks) | Ops/support leads | Same problem outside code; large market | Parked: needs a different journey and buyer |
| UC5 | Solo "one section at a time" builder (the ADHD workflow) | Individuals | Real, loved, **hard to monetise** | **[Built]**; keep as the *free on-ramp*, not the business |

**[Decision]** Beachhead = **UC1 + UC2 for engineering teams**, with UC3 as the demonstration of value and
UC5 as the free funnel. UC4 is a possible *second* market, only after UC1/UC2 have customers.

### 2.2 Jobs-to-be-done
- *When my team uses AI on our code, I need to **show** what was sent, to whom, under which policy — and to **stop** it when it breaks the policy — so that I can say yes to AI without signing up for an incident.* (buyer: tech lead / security / compliance)
- *When I'm mid-task, I need a bounded, correct bundle in one command, so I don't lose the thread.* (user: developer)

### 2.3 Non-goals (explicit)
Not an IDE; not an agent runtime; not a chat product; not a model host; not a vector database; not a
general RAG platform; not a project manager. (See the alignment filter in [`product-roadmap.md`](product-roadmap.md).)

### 2.4 How we will know (metrics without telemetry-by-default)
- **North star:** *governed AI handoffs per active team per week* = bundles that produced a receipt **and** passed policy.
- **Quality guardrails:** receipt completeness violations (target **0**); secret-scanner false-positive rate on pilot repos (target: low enough that nobody turns it off — measure it); time-to-first-bundle ≤ 60 s; `check` false-positive rate.
- **Business signals:** pilots running `check --strict` in CI for ≥4 weeks; receipts attached to PRs; one auditor/security reviewer saying "this is evidence I accept".
- **Kill / pivot criteria:** after 5–8 pilots, if no team would pay for central audit/policy **and** CI-only usage is free-rider, fall back to a developer-tool + consulting model; if the receipt is not valued, stop investing in the governance plane.
- **How measured:** pilot interviews + opt-in, content-free usage counts (local-first means no default telemetry). No invented market numbers here — they are an output of the pilots.

---

## 3. Target architecture

### 3.1 Stages (each gated by the one before)
```
 S0  LOCAL CORE (today)      CLI · engine · web client · local server · VS Code ext   ── [Built]
        │  gate: used on real repos by pilot users; live-provider test; Windows; browser E2E
        ▼
 S1  REPO-RESIDENT POLICY    policy-as-code in the repo · signed receipts · CI action
        │                    · `receipt verify` · MCP server (read-only, stdio)         ── Proposed
        │  gate: ≥3 teams run the CI gate for 4+ weeks
        ▼
 S2  TEAM PLANE (hosted)     policy distribution · receipt/audit store · activity feed
        │                    · SSO · gateway (LiteLLM) for `ask`                        ── Proposed
        │  gate: pilots need central audit/budget they cannot get from S1
        ▼
 S3  KNOWLEDGE PLANE         governed index (hybrid RAG) · read-only agent tools (A1)  ── Proposed
                             · propose-a-patch (A2)
```
A3 (sandboxed apply + test) is deliberately **outside** this map until a container/VM boundary exists and
its own ADR is accepted.

### 3.2 The planes (what runs where)
```
 ┌───────────────────────────── USER MACHINE / CI RUNNER (trusted by the user) ─────────────────────────────┐
 │  CLI ── engine (walker → select → secret guard → budget → render)  ── receipt (+ signature, S1)           │
 │   │          ▲ shared by            ▲ byte-identical contract                                            │
 │   │          │                      │                                                                    │
 │  VS Code ext · web client (static / local server) · CI action · MCP server (stdio, read-only)            │
 │   │                                                                                                      │
 │   └── `ask` / `fetch` (the ONLY outbound network commands; consent, no redirects, SSRF policy)           │
 └───────────────────────────────┬──────────────────────────────────────────────┬──────────────────────────┘
                                 │ receipts + events (NO file content)          │ model calls (BYOK or gateway)
                      ┌──────────▼───────────┐                       ┌──────────▼───────────┐
                      │  TEAM PLANE (S2)     │                       │  MODEL PLANE          │
                      │  API · policy · audit │◄── policy lookup ────►│  LiteLLM gateway      │
                      │  Postgres · object    │                       │  local open-weight    │
                      │  store · event bus    │                       │  hosted providers     │
                      └──────────┬───────────┘                       └───────────────────────┘
                                 │ OTLP
                      ┌──────────▼───────────┐
                      │  OBSERVABILITY        │  Grafana/Tempo/Loki/Prometheus · Langfuse
                      └───────────────────────┘
```
**Design invariants (carried from the prototype, non-negotiable):** the core never calls a model and never
needs a network; file content never enters the team plane by default (receipts hold paths, hashes, counts,
reasons); every outbound call is an explicit, consented, logged command; nothing is "advisory" if it can be
made enforced.

### 3.3 Why this shape
- **Local-first** is the trust story *and* the Lagos story (intermittent connectivity, cost, residency): the product works offline and the sensitive data never has to leave.
- **Repo-resident policy first** gets a real pilot running without hosting anything: a policy file in git, a CI action, receipts as build artefacts.
- **A team plane only for what must be shared:** policy distribution, an audit trail across repos, SSO, budgets.

---

## 4. LLM selection and routing

### 4.1 Where a model is actually used (and where it must not be)
| Function | Model? | Tier | Why | Fallback |
|---|---|---|---|---|
| Packing, receipt, `check`, `fetch` extraction | **Never** | — | Determinism is the product | — |
| `ask` (single-shot answer / review) | Yes, user-triggered | T1 default, T2 on request | The one place a model is the point | Next tier; local floor |
| Contextual chunk descriptions (RAG ingest) | Yes, batch | T0/T1 | Cheap, cacheable, offline-tolerant | Skip contextualization (receipt notes it) |
| Optional answer verification (evaluator) | Yes | T1 | Checks an answer against the receipt/tests | Skip; label unverified |
| Eval judging (offline) | Yes | T2 | Quality gate for releases, not user data | Human spot-check |
| Secret/PII detection | **No LLM** | — | Patterns + optional local classifier; an LLM seeing the secret defeats the purpose | Pattern scan |

### 4.2 Tiers, not names **[Decision]**
| Tier | Job | Characteristics | Candidates **[Researched — verify]** |
|---|---|---|---|
| **T0 local** | Contextualization, classification, offline floor, sensitive-by-policy | Open-weight, runs on a dev machine or a Lagos box; free per token | Qwen3.8-27B-class via Ollama/vLLM |
| **T1 default** | Everyday `ask`, review, summaries | Cheap/fast hosted or open-weight API | DeepSeek V4-Flash-class; a small/fast tier from a frontier vendor |
| **T2 frontier** | Hard review, long reasoning, eval judge | Highest quality, highest cost | Frontier families from Anthropic / OpenAI / Google; DeepSeek V4-Pro-class |
| **T3 long-context** | Whole-corpus questions | 1M-class windows | Provider-specific |

Model names, prices and context sizes in this table come from secondary 2026 sources and **will be stale
within weeks**; one source reports a ~100× price spread between the cheapest usable and the most capable
model. That spread is *why* tiers exist; it is not a reason to hard-code a name.

**How a model enters a tier: an eval, not an opinion.** A model is admitted to a tier only when it clears a
**golden-task suite** built from the product's own tasks (review a diff, summarise a module, answer from a
bundle) with a pass threshold, a cost ceiling and a latency ceiling. Pin the exact model **version**; record
`gen_ai.request.model` **and** `gen_ai.response.model` (they differ more often than people expect).

### 4.3 Routing **[Decision]**
**Gateway:** self-hosted **LiteLLM** (MIT, 100+ providers, virtual keys, per-key budgets) **[Researched]**.
OpenRouter is excellent for *prototyping* (one key, hundreds of models) but is hosted-only and cannot meet a
data-control requirement; keep both OpenAI-compatible so switching is a base-URL change.

**Routing is rules-first and policy-driven — the opposite of "smart auto-routing":**
1. **Sensitivity decides the *destination* first.** Each bundle carries a classification from the policy and the secret/PII scan (e.g. `public`, `internal`, `restricted`). A `restricted` bundle may only go to T0 local or to a provider on the org's allow-list with **zero-data-retention** terms; `internal` to allow-listed providers; `public` anywhere. This is the differentiating route: *policy-based, not price-based.*
2. **Task decides the *tier*.** A preset declares its tier (`review` → T2, `summarise` → T1, `contextualize` → T0). Explicit, reviewable in git.
3. **Cost and health decide among equals.** Within a tier: fallback chain across providers, always **ending on a local or cheap open-weight floor** so degraded beats failed.
4. **The receipt records the route** (`model_call.provider/model/host/tier/policy_rule`), so the decision is auditable.

**Why not an ML router or cascade now:** an opaque router makes the *receipt* un-explainable and may
route restricted data somewhere unapproved. A **cascade** (cheap model first, escalate on low confidence)
is a real cost win — AT&T and Databricks report 30–56% savings with ~2% measured quality loss **[Researched,
secondary]** — but *only because they built golden evals first*. **Trigger to adopt:** our own eval shows
≥X% savings at ≤Y% quality loss on the `review` and `summarise` tasks.

### 4.4 Controls that ship with the gateway
Per-key and per-team **budgets with hard stops** (a runaway `ask` loop is a cost incident); per-request
**timeouts and max-output caps**; **no automatic retries** on paid calls without an idempotency rule;
prompt-**caching** on (it also makes contextual retrieval affordable); a **kill switch** per provider; PII/secret
guard *before* the gateway (the gateway never sees what the guard blocked).

---

## 5. Agent patterns

### 5.1 The rule **[Decision]**
Use the **simplest pattern that passes the eval**. Anthropic's guidance is to prefer the simplest solution
and only add agency when flexibility outweighs latency, cost and error-compounding **[Researched]**. Reported
costs: agents use ~4× the tokens of a chat, multi-agent systems ~15×; token usage alone explained ~80% of
the performance variance in Anthropic's own research-eval; and multi-agent parallelism helped
**read-heavy** research but hurt **write-heavy** work (a CooperBench figure reported task success roughly
halving, ~50%→25%, when coding was split across two agents) **[Researched, secondary]**. A multi-agent gain
is largely *buying more tokens*.

### 5.2 Pattern decisions
| Pattern | Verdict | Where in fileflow | Guardrails |
|---|---|---|---|
| **Single augmented call** | **Default** | `ask` | token cap, timeout, consent, secret guard **[Built]** |
| **Prompt chaining** | Yes | Review pipeline: *select (deterministic) → pack → review (model) → verify (checks/tests)* | A programmatic gate between steps; each step logged |
| **Routing** | Yes (rules, not LLM) | Preset → tier; sensitivity → destination | Rules in git; receipt records the choice |
| **Parallelization — sectioning/voting** | Yes, narrowly | Review of independent files; N-vote on a *classification* | Read-only; budget cap; n ≤ small constant |
| **Evaluator-optimizer** | Yes, with a **verifiable** signal | Draft answer → check against tests/receipt/schema → revise (≤2 loops) | Stop condition = checks pass **or** max loops; never "model judges model" alone |
| **Plan-and-Execute** | Yes, for *planning only* | Multi-file change **plan** written to `docs/exec-plans/active/`; **a human approves** before anything is applied | The plan is a file in git (it is also the memory) |
| **ReAct (tool loop)** | **A1: read-only, later** | `search`, `read_file` (confined), allow-listed MCP reads | Max iterations, token+time budget, trajectory logged as OTel spans |
| **Orchestrator-workers / supervisor-worker** | **Read-only breadth only** | "Explain this repo", cross-repo research | Worker cap, per-task budget, *no shared writes*, full-trace context sharing |
| **Multi-agent swarm** | **Rejected** | — | Cost ~15×, context fragmentation, conflicting writes; revisit only with an eval showing a net gain |

### 5.3 The agency ladder (what an agent may do)
| Rung | Capability | Permission mechanism (**enforced**, not prose) | Status |
|---|---|---|---|
| **A0** | One prompt → one answer; no tools | consent + fail-closed secrets + no redirects | **[Built]** |
| **A1** | Read-only tools: confined file read, search, allow-listed MCP reads | confinement code + allow-list in policy + per-session scope | Proposed (ADR) |
| **A2** | **Propose** a patch/plan as a file; a human applies it | output is an artefact (diff); no write tool exists | Proposed |
| **A3** | Apply + run tests **inside a container/VM** | OS-level sandbox; network egress policy; resource limits | Proposed; **not before** a real sandbox |
| A4 | Unattended changes to shared systems | — | **Not planned** |

Why enforced: the Claude Code docs state permission rules are enforced by the tool, not the model, and the
public `rm -rf` incident (the agent tested a "safe delete" script that was itself unsafe) is the
cautionary tale **[Researched]**. fileflow's own `check` already flags prose-only permissions.

### 5.4 Promotion gate (workflow → agent, or rung → rung)
A change is promoted only with: (1) an **ADR**; (2) a **golden eval** showing the new pattern beats the
simpler one by a stated margin; (3) a **cost model** (tokens × price × expected volume) and a hard
**budget cap**; (4) **stop conditions** (max iterations, time, spend); (5) **observability** (every step a
span); (6) a **human checkpoint** before any irreversible effect; (7) a **rollback**.

---

## 6. Open-source data → real-time activity pipeline

### 6.1 What "data" and "activity" mean here
**Inputs (open and internal):** file-change events (the existing watcher); VCS events (git hooks; GitHub/GitLab
webhooks); fetched web docs (`fetch`, `llms.txt`-style reference files); dependency/security data
(OSV advisories, SPDX licences) as *signals attached to bundles*; CI results; model-call telemetry; human
feedback (answer accepted/rejected).
**Activity:** an append-only stream of *what happened*, never of *what was in the files*.

### 6.2 Pipeline
```
 PRODUCERS                      LOG                  FAN-OUT                 CONSUMERS
 CLI / local server ──┐
 CI action ───────────┤  event (CloudEvents-shaped,    S1: local JSONL        activity feed (SSE / WebSocket)
 VS Code ext ─────────┼─►  OTel trace id, no content) ─► S2: Postgres outbox ─► audit export (JSONL/OTLP → SIEM)
 webhooks (GitHub…) ──┤                                  + LISTEN/NOTIFY      alerts (Slack/Teams/webhook)
 `ask`/`fetch` ───────┘                                  S3?: NATS JetStream  metrics (Prometheus) · evals feed
```
**[Decision]** Smallest thing that works, with explicit upgrade triggers:
1. **S1 local:** one JSONL file per project (`.fileflow/events.jsonl`, git-ignored). The existing WebSocket watcher already pushes "changed" frames; the activity feed is the same mechanism with richer events.
2. **S2 team:** **Postgres** — an `events` table (append-only, monthly partitions) written transactionally with the receipt (**outbox pattern**) and fanned out with `LISTEN/NOTIFY` to SSE/WebSocket gateways. One datastore to run, back up and secure.
3. **S3 only on evidence:** **NATS JetStream** (single binary, persistence, replay, simple to operate) **if** sustained event rate or consumer fan-out exceeds what Postgres handles *as measured*. **Kafka/Redpanda are rejected for now:** the operating cost is wrong for a small team and Nigerian infrastructure realities; revisit at multi-tenant scale.

### 6.3 Event envelope and catalogue
```json
{ "specversion": "1.0", "id": "01J...", "type": "fileflow.ask.sent",
  "source": "cli://team-a/payments-api", "time": "2026-10-08T09:30:00Z",
  "subject": "bundle:9f2c1ab04e7d",
  "traceparent": "00-...", "actor": "u_184", "org": "o_7", "project": "p_31",
  "data": { "files_sent": 11, "tokens_est": 11840, "tier": "T2", "route": "provider-b/model-x",
            "destination_host": "api.example.com", "policy_rule": "internal->allow-listed",
            "secrets": {"findings": 0, "mode": "block"}, "receipt_sha256_12": "3c91d0aa72be" } }
```
| Event type | Emitted when | Never contains |
|---|---|---|
| `bundle.built` / `bundle.blocked` | a bundle is produced / stopped by policy | file content, secret values |
| `receipt.created` | a receipt is written | content (paths+hashes only; path-hashing option) |
| `check.passed` / `check.failed` | `check` runs (CI or local) | file content |
| `fetch.saved` / `fetch.blocked` | a page is saved / refused (SSRF, challenge) | page text |
| `ask.sent` / `ask.answered` / `ask.failed` | model call lifecycle | prompt, answer |
| `answer.saved` / `answer.reviewed` | a `generated` file is saved / a human accepts or rejects it | answer text |
| `policy.violated` / `policy.changed` | a rule blocks / the policy file changes | — |
| `index.updated` (S3) | the retrieval index changes | chunks |

**Properties:** at-least-once delivery with an idempotency key (the event `id`); per-project ordering by
sequence; schema versioned (`specversion` + `dataschema`); replayable; retention per org (default 13 months,
configurable); **right-to-erasure** by actor id (hash-chain preserved, payload redacted). Opt-in only for any
cross-org analytics.

### 6.4 Ingesting open-source data
`fetch` already produces provenance-labelled Markdown. The pipeline adds: **reference packs**
(`docs/references/*` from vendors' `llms.txt`, as OpenAI's own repository does **[Researched]**), **OSV**
advisories matched against lockfiles to annotate bundles ("this bundle contains a dependency with an open
advisory"), and **SPDX/licence** detection for `fetch`ed text ("check permissions before republishing" becomes
a structured field). Every ingested item keeps `source_url`, `fetched_at`, hash and `observed`.

---

## 7. Agent memory architecture

### 7.1 Principle
> **Memory is files the next session can read, plus a derived index that can be rebuilt.** If it can't be
> diffed in git, shown to the user, and deleted, it is not memory fileflow should keep.

### 7.2 Layers
| Layer | What | Where | Lifetime | Trust label | Write path |
|---|---|---|---|---|---|
| **Working** | The bundle for this call | the context window | one call | per-file provenance | the engine |
| **Run record** | Receipt + event of this call | `.fileflow/receipts/`, events log | retention policy | `user` / system | automatic, no content |
| **Project memory** | Plans, decisions, quality scores, field notes, conventions | `docs/exec-plans/`, `docs/design-docs/`, `docs/QUALITY_SCORE.md`, `AGENTS.md` | the repo's life | `user` (or `generated` until a human accepts) | human or **proposed patch a human merges** |
| **Preferences** | Per-user defaults (tier, format, budget) | `~/.config/fileflow/` | the user's life | `user` | the user |
| **Team memory** | Shared presets, policy, templates | the repo (S1) / team plane (S2) | the team's life | `user` | PR review |
| **Retrieval index** | Chunks + embeddings + BM25 | `.fileflow/index/` or Postgres | rebuildable at any time | derived — **never a source of truth** | the indexer |

### 7.3 Rules
1. **Provenance on everything.** `observed` / `generated` / `user` ride in front matter; generated memory is **quarantined from the instruction role** (a model's earlier answer is data, not a command — a prompt-injection path otherwise).
2. **No hidden memory.** Anything the model "remembers" is a file or an index entry the user can list (`fileflow memory ls` — Proposed), read, edit and delete.
3. **Writes need a human, at first.** A model may *propose* a memory change as a patch; a person merges it. (Matches A2.)
4. **Staleness is a check.** `check` gains doc-freshness rules (doc-gardening, as in OpenAI's description **[Researched]**): stale plans, orphaned decisions, contradicted conventions.
5. **Forgetting is a feature.** TTL on `generated` items; export and erase by actor (NDPA data-subject rights).
6. **Poisoning:** content from `fetch` or from earlier answers cannot silently promote itself into instructions.

### 7.4 Why no memory vendor (Mem0 / Zep / Letta) **[Decision]**
Their benchmarks are **vendor-reported and contradict each other** (one vendor reports ~94 on LongMemEval for
its own system while other comparisons put the same system near 49; temporal-graph systems report ~64);
Letta reports ~74 on LoCoMo with **plain file storage** — i.e. *how you organise what you store matters more
than the database* **[Researched, indicative only]**. For fileflow's use (a project, its decisions, its plans)
files + git + a derived index are sufficient, auditable and offline. **Trigger to revisit:** users routinely
need *temporal* questions ("what did we decide about X last quarter and what changed") that a flat index
fails on **in our own eval**; then evaluate a temporal graph (Graphiti-style) behind the same file contract.

---

## 8. RAG architecture

### 8.1 Pack first
**fileflow's core already is retrieval:** a deterministic, budgeted selection of the *whole* relevant set. For
a corpus that fits the budget, **packing beats RAG** (no retrieval failure; perfect recall; a complete
receipt). RAG is only for corpora that **don't fit**; and small corpora "do not need this much machinery"
**[Researched]**. So RAG is S3, optional, and it must *extend* the receipt, not bypass it.

### 8.2 Pipeline (when needed)
```
 INGEST            CHUNK                CONTEXTUALIZE        INDEX                 RETRIEVE → PACK
 same walker,  →  structure-aware:  →  T0/T1 writes 50–100 → SQLite FTS5 (BM25)  →  hybrid top-150
 confinement,      Markdown by          tokens of situating    + vector (sqlite-vec    → rerank → top-20
 ignore, secret    heading; code by     context per chunk      local / pgvector team)   → ACL filter
 guard, provenance symbol (Tree-sitter) (prompt-cached)        incremental by hash      → pack with budget
                   300–800 tokens                                                       → receipt lists chunks
```
- **Hybrid + contextual + rerank** is the design because it is the best-evidenced stack: Anthropic's benchmark (top-20 failure rate **5.7% → 3.7%** contextual embeddings, **→ 2.9%** adding contextual BM25, **→ 1.9%** adding reranking from a top-150 candidate pool; datasets were codebases, fiction, arXiv and science papers) **[Researched, vendor-internal]**. **Treat these as a hypothesis for our corpora, not a result** — we run our own eval before enabling anything.
- **Embeddings:** one hosted high-quality option (Voyage/Gemini-class were best in that test) and one **local open-weight** option for `restricted` data. Multilingual quality (Yoruba, Hausa, Nigerian Pidgin) is **unverified** — evaluate before promising it.
- **Store:** SQLite (FTS5 + a vector extension) locally; **Postgres + pgvector** for the team plane (one datastore). A dedicated vector DB is rejected until measured scale demands it.
- **Permission-aware retrieval is mandatory for business use:** every chunk carries the ACL of its source; the filter runs **before** ranking and before the prompt is built; a user can never retrieve what they could not open.
- **Receipt integration:** each retrieved chunk is an `included` item (`path#L12-48`, hash, score, retriever); everything considered but cut is `excluded` with a new closed reason (`NOT_RETRIEVED`, `BELOW_RERANK_CUTOFF`). The completeness property ("every candidate exactly once") is kept — it becomes *the* RAG audit trail.
- **Freshness:** incremental by content hash; deletes propagate; a stale index is a `check` warning.
- **Safety:** retrieved text is labelled by provenance; it never enters the instruction role.

### 8.3 Evaluation (the gate for turning RAG on)
Golden question set per pilot corpus (questions with known source passages) → **recall@20**, **MRR**,
**answer faithfulness** (does the answer follow only from retrieved text), **latency**, **cost per query**.
Release gate: no regression; target set per corpus from the first baseline. Re-run on every embedding-model,
chunker, reranker or contextualizer change. Contextualization cost is a one-time ingest cost; with prompt
caching the vendor-quoted figure is ~$1.02 per million document tokens **[Researched, secondary]** — measure ours.

---

## 9. Deployment, infrastructure and observability

### 9.1 Distribution (S0–S1)
`pipx install fileflow` / `uv tool install fileflow` (PyPI, **trusted publishing**, signed with sigstore),
a Homebrew tap and winget package, the VS Code extension on the Marketplace, a **GitHub Action**
(`fileflow/check@v1`), and a container image for CI. The web client ships **inside the wheel** (**[Built]**)
and works as a static page, so a team can host it on any static host. **[Assumption]** Windows must be
supported before pilots in enterprises (it is untested today).

### 9.2 Team plane (S2) topology **[Decision]**
| Piece | Choice | Why |
|---|---|---|
| API | FastAPI (same language as the engine) in containers | one stack; the engine is a library |
| Datastore | **Managed PostgreSQL** (+ pgvector later) | events, receipts, policy, index in one place |
| Object store | S3-compatible (receipts bundles, exports) | cheap, standard |
| Queue/jobs | Postgres-backed job queue (no extra system) | operate one datastore |
| Gateway | **LiteLLM** (self-hosted) | zero markup, budgets, data control |
| Auth | **OIDC** (Okta / Entra / Google) + service tokens; SCIM later | enterprise buyers require SSO |
| Edge | CDN for the static web client | lowest latency, no origin load |
| Secrets | cloud secret manager; per-tenant encryption keys | tenancy + residency |

### 9.3 Region and residency (Nigeria/Africa) **[Researched]**
- **No hyperscaler operates a region in Nigeria** (AWS: nearest is Cape Town; Azure and GCP: Johannesburg). Reported latency from Lagos: **~80–120 ms** to Cape Town, **~95–140 ms** to Johannesburg. Lagos has edge/CDN presence and carrier-neutral colocation (Rack Centre, Equinix/ex-MainOne Lekki, OADC); new hyperscale campuses (e.g. Kasi Cloud) are under construction, with **grid reliability/diesel cost** the stated risk.
- **[Decision]** Control plane in **AWS af-south-1 (Cape Town)**; static web on a CDN with Lagos presence. For buyers that require in-country data, ship a **self-host bundle** (Docker Compose first, Helm later) deployable into a Lagos colo. Because the team plane holds **no file content** (receipts: paths, hashes, counts), the residency footprint is small *by design* — that is a selling point.
- **Offline/low-bandwidth is a product requirement, not a nicety:** the core works with no network; the web client works offline (static mode); `ask` is the only thing that needs a link, and a local model removes even that.

### 9.4 Observability
**Standard:** **OpenTelemetry** end to end. OTLP and the tracing model are stable; the `gen_ai.*` semantic
conventions are **still "Development" with no 1.0 and were moved to their own repository in June 2026**, so:
adopt the *shape*, **pin the convention version**, and wrap attribute names in one module so churn is a
one-file change **[Researched]**.

| Signal | What we record | Never |
|---|---|---|
| **Traces** | `invoke_agent` (the command) → `chat` (model call) / `execute_tool` (fetch, read) spans; `gen_ai.request.model` **and** `.response.model`; input/output tokens; **cache read/creation tokens** (otherwise cost dashboards lie); finish reason | prompt/answer text (opt-in, redacted, sampled) |
| **Metrics** | ask latency/p95, tokens, **cost per org/tier**, secret-block rate, `check` failure rate, fetch refusals (SSRF), receipt-completeness violations (must be 0), consent declines, fallback rate, cache-hit rate | per-user content |
| **Logs** | structured JSON, correlation = trace id | content, secrets, keys |
| **Evals in prod** | sampled, opt-in, judged offline; feeds the release gate | raw prompts by default |

**Backends:** self-hosted **Grafana + Tempo + Loki + Prometheus** (open, portable) and **Langfuse** (OTLP
ingest) for LLM-specific traces and eval datasets. **SLOs (proposed, to be tuned on pilot data):** `check` p95
< 5 s on a 5k-file repo; bundle build p95 < 2 s; team-plane API 99.5% monthly; **zero** silent omissions.
**Alerts:** budget burn, secret-guard bypass attempts, provider error spikes, a receipt that fails
verification. **Runbooks** for provider outage (fallback chain → local floor), key leak (rotate + audit
query by key id), cost incident (kill switch).

### 9.5 CI/CD and release
Gates (extend today's): unit + integration + real-subprocess tests **[Built]**; golden + fuzz parity
**[Built]**; **mutation checks on safety code** (manual today → scripted in CI); **eval regression** for any
model-dependent feature; SAST + dependency audit + **SBOM**; wheel built from a clean copy **[Built]**;
signed artefacts; staged rollout behind feature flags; DB migrations reversible. **Release = a human tick
against [`engineering-contract.md`](engineering-contract.md)'s gates**, with the verified commit hash.

---

## 10. Security, privacy and compliance (cross-cutting)

- **Threat model, extended** (the prototype's list + new): hostile repo config (cannot redirect keys **[Built]**), SSRF through `fetch` **[Built]**, key exfiltration via redirects **[Built]**, prompt injection through fetched/generated/retrieved text (labelled, never instruction-role), malicious MCP servers (**[Researched]**: a July 2026 analysis reportedly found ~70% of internet-exposed MCP servers returned their tool list to anonymous callers — treat every MCP endpoint as hostile; remote servers only with OAuth 2.1), cross-tenant leakage (per-tenant keys + row-level security), supply chain (signed releases, pinned dependencies, SBOM, trusted publishing).
- **Secrets:** pattern scanner stays the default (**[Built]**, tuned by dogfooding); add **custom rules** per org and an optional **local** classifier — *never* an external LLM for secret detection.
- **NDPA 2023 / GAID 2025 posture [Researched — get Nigerian counsel; this is not legal advice]:** the Act requires a **recorded basis and adequacy assessment for each transfer of personal data out of Nigeria**, and the GAID makes **cross-border transfer a high-risk activity requiring a DPIA filed with the NDPC**; breach notification is **72 hours**; organisations above the "major importance" thresholds must **register and appoint a DPO**; reported penalties reach the greater of a fixed sum or a percentage of annual gross revenue. *Implications by design:* (1) the receipt is the **transfer record** a controller needs; (2) `ask` to a non-local host is a cross-border transfer when the bundle holds personal data — hence consent, fail-closed secrets, a **PII classifier gate** (S1), and a *local-only* route for `restricted`; (3) the hosted plane stores **no content**; (4) offer a **DPIA template** and DPA terms in the commercial pack; (5) erasure and export by actor.
- **Honesty requirements** (already product rules): estimates labelled as estimates; `unknown` always listed; a label records origin, not truth.

---

## 11. Roadmap to live

Phases are **evidence gates, not dates.** Each lists what is built, what must be true to proceed, and what
is explicitly *not* built yet.

| Phase | Theme | Build | Exit gate (must be true) | Do **not** build yet |
|---|---|---|---|---|
| **P0** | **Make it real** | Live provider test of `ask` (throwaway key); Windows CI; real-browser E2E (Playwright); GitHub CI run; publish to PyPI (trusted publishing); scripted mutation checks | Green CI on 3 OSes; a live OpenAI-compatible + Anthropic round-trip; browser E2E for the main flow | Anything hosted |
| **P1** | **Pilots** | 5–8 pilot teams; `fileflow/check` GitHub Action; PR comment with receipt + `check` result; field notes | ≥3 teams run the gate ≥4 weeks; false-positive rates known; Gate E entries ≥10 from ≥2 people | Team plane, RAG, agents |
| **P2** | **Evidence you can hand to an auditor** | **Signed receipts** (+ `receipt verify`, `receipt diff`); policy-as-code file; PII classifier gate; MCP server (stdio, read-only, confined); DPIA template | One security/compliance reviewer accepts a receipt as evidence; policy file in ≥3 repos | Hosted store |
| **P3** | **Team plane** | OIDC, policy distribution, receipt/audit store, activity feed, LiteLLM gateway with budgets, self-host bundle | Pilots *need* central audit/budgets; one paying design partner | Retrieval, agent tools |
| **P4** | **Knowledge plane** | Governed index (hybrid RAG), permission-aware retrieval, evals; Tree-sitter compression as an explicit lossy mode | Corpora that don't fit exist at pilots; recall@20 target met on their golden sets | Writes |
| **P5** | **Read-only agency (A1) → propose-a-patch (A2)** | Confined read tools, allow-listed MCP reads, patch artefacts, eval + budget gates | Own eval shows net gain over single-shot; ADR accepted | A3/A4 |

**Always-on work:** mutation-test every safety change; add the hostile input to the fuzzer first; keep the
field notes; keep the research record honest about what is secondary.

---

## 12. What could go wrong

1. **The receipt isn't valued.** Buyers may want blocking, not evidence. → Pilots decide; the CI gate and secret guard still stand alone; kill criterion in §2.4.
2. **Absorbed by agent vendors.** Their tools gain secret scanning and audit. → Compete where they cannot: *independent* evidence, org policy, self-hosting, local-first.
3. **Scope creep into an AI platform** (RAG + agents + memory + hosting). → The stage gates and "do not build yet" column; the alignment filter.
4. **Governance plane becomes a liability.** A hosted store of receipts is a target. → No content by design; per-tenant encryption; path-hashing option; self-host.
5. **False comfort from `check` and the secret scanner.** → "assumed/unknown" ledger on every report; accepted limits written down (FN-001).
6. **Provider/format churn breaks `ask`** (adapters written from docs, mock-tested only). → P0 live tests; contract tests against recorded fixtures; fallbacks.
7. **Model-quality regressions from silent provider updates.** → Pin versions; record response model; eval gate; canary.
8. **Standards churn** (OTel GenAI is "Development"; MCP spec revised on 2026-07-28). → Isolate in adapters; pin versions; one-file changes.
9. **NDPA mis-reading.** → Counsel review before any compliance claim in marketing; DPIA template is a template, not advice.
10. **Lagos infrastructure risk** (power, latency, FX billing). → Offline-first core; Cape Town control plane; self-host bundle; local-currency payment options (Paystack/Flutterwave are the obvious candidates — **verify before committing**).

---

## 13. Scorecard, assumptions, and what I need from you

**5-Lens score for the *refocused* product (governed AI handoff for teams)**

| Lens | /5 | Notes |
|---|---|---|
| Leverage | 4 | Receipt + policy benefit every surface and every provider |
| Rarity | 4 | Independent, deterministic, locally-verifiable evidence is uncommon; packing is not |
| Compounding | 4 | Receipts → policy → audit → (later) governed retrieval |
| Transferability | 3 | Pattern transfers to other agent tooling; product is code/knowledge-centric |
| Urgency | 4 | Regulatory (NDPA/GAID) and AI-adoption pressure are current |
| **Total** | **19/25** | **Proceed to P0–P2 now; hold P3+ until pilot evidence.** |

**Assumptions I made [Assumption] — please correct:**
1. The first customers are **engineering teams**, not individuals or non-code teams.
2. A **small team (2–4 engineers)** builds this; hence one datastore, no Kafka, no vector DB.
3. Buyers care enough about *auditability* to pay; **not yet validated**.
4. Pilots can be found in Lagos/West Africa fintech and agency circles.
5. Windows and macOS support are needed before enterprise pilots.

**Decisions I need from you:**
1. **Confirm the beachhead** (UC1 + UC2, engineering teams) or choose another.
2. **Open core** (Apache-2.0 core, paid team plane) — or a different model?
3. **Hosting posture:** Cape Town + self-host bundle, or Lagos-only from day one?
4. **Pilot access:** who are the first 3–5 teams, and may we run the CI action on their repos?
5. **A budget and a throwaway key** for live provider tests and golden-eval runs (P0).

**Evidence ledger.** *Verified in this repo:* the engine contract and its parity tests, receipts, provenance,
`fetch`/`ask`/`check`/`--diff` and their mutation-tested safety controls, 683 tests at the time of writing.
*From outside sources and mostly secondary (verify):* competitor capabilities; model names/prices/context
sizes; routing-platform comparisons and cascade savings (AT&T/Databricks); the multi-agent token and
CooperBench figures; RAG benchmark numbers (Anthropic-internal); memory benchmarks (vendor-reported,
contradictory); OTel GenAI status; MCP revisions and the ~70% exposed-server finding; NDPA/GAID details;
cloud-region and latency figures. *Not verified at all:* any live provider call, Windows, a real browser,
market size, willingness to pay.
