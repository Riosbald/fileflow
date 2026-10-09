# Harness engineering — research record and what fileflow does with it

> **Purpose:** decide what (if anything) fileflow should build from the
> "Agent = Model + Harness" material, using **primary sources**, and record what
> could *not* be verified.
> **Method:** claims in the pasted summary were checked against the original
> documents on the date below. Nothing here relies on the paid guide.
> **Researched:** 2026-10-02.

## 1. What was verified, and where

| Claim | Status | Primary source |
|---|---|---|
| Treat `AGENTS.md` as a **table of contents (~100 lines)**, not an encyclopedia; knowledge lives in a structured `docs/` directory | ✅ verified | OpenAI, *Harness engineering: leveraging Codex in an agent-first world* (Lopopolo, 2026-02-11) |
| "One big AGENTS.md" failed for four reasons: context is scarce; too much guidance becomes non-guidance; it rots instantly; it is hard to verify mechanically | ✅ verified (same post) | OpenAI post |
| "Dedicated linters and CI jobs validate that the knowledge base is up to date, cross-linked, and structured correctly"; a recurring "doc-gardening" agent opens fix-up PRs | ✅ verified | OpenAI post |
| ~1M lines, ~1,500 PRs, 3 → 7 engineers, 3.5 PRs/engineer/day, "0 lines of manually-written code" | ✅ verified **as OpenAI's own report** (a team describing what it did — not a study) | OpenAI post |
| Codex discovery: one file per directory, root → cwd, closer files override, `AGENTS.override.md`, **stops at 32 KiB** (`project_doc_max_bytes`) | ✅ verified | OpenAI Codex docs (AGENTS.md guide) |
| Claude Code does not read `AGENTS.md` natively; import it from `CLAUDE.md` with `@AGENTS.md`; imports nest at most **4 hops**; `@path` inside code spans is *not* an import | ✅ verified | Claude Code docs — *How Claude remembers your project* |
| `CLAUDE.md` over **200 lines** "may reduce adherence" (startup warning) | ✅ verified | same |
| Permission rules are **enforced by the tool, not the model**: "Instructions in your prompt or `CLAUDE.md` shape what Claude tries to do, but they don't change what Claude Code allows." Evaluation order **deny → ask → allow**, first match wins | ✅ verified | Claude Code docs — *Configure permissions* |
| `Bash(command:rm *)`-style rules are ignored because they'd be bypassable by compound commands; use `Bash(rm *)`, `Read(./path)` | ✅ verified | same |
| `bypassPermissions` should be used "only in isolated environments like containers or VMs" | ✅ verified | same |
| Skill frontmatter: `name` ≤ 64 chars, lowercase/digits/hyphens, no `anthropic`/`claude`; `description` non-empty ≤ 1,024 chars; keep body < 500 lines | ✅ verified | Claude docs — *Skill authoring best practices* |
| Sebastien Guillemot: Claude ran `rm -rf` on his home directory while testing a sandbox script it had written | ✅ verified as his own first-person report (X, 2026-08-26). A secondary report attributes it to one variable name reused for both the test path and the delete path — **treat the mechanism as reported, not confirmed** | his X thread; devby.io (secondary) |
| "350-line file limit", "reviewer agents biased to merge, nothing above P2", "quality score markdown table" | ⚠️ **talk/interview only** (AI Engineer talk, Latent Space) — not in the written post; not independently checked here | — |
| Stripe "Minions" 1,000+ vs ~1,300 PRs/week; Steinberger 6,600 commits/month; Block "Manager Bot" | ⚠️ **aggregator-sourced**; the two Stripe figures disagree and neither cites Stripe. Order of magnitude only | — |
| Ramp case study | ❌ **not recoverable** — an aggregator states the article doesn't mention Ramp | — |
| Gupta's "Harness Spec 1-pager", Notion toolkit, `/harness-assistant`, his ladder steps | ❌ **paywalled / not public** — reconstructed from public sources, never copied | — |
| Addy Osmani: "A decent model with a great harness consistently beats a great model with a bad harness" | ⚠️ a quote with large reach and **no data behind it** | — |

**Genre of the evidence:** practitioner reports and vendor documentation. There is
no controlled comparison anywhere in this set. The documentation facts (limits,
precedence, syntax) are solid; the *effectiveness* claims are plausible but
unmeasured.

## 2. The finding that shapes the design

> **A rule written in prose is a request. A rule in `permissions.deny`, a sandbox,
> or a failing check is a fact.**

The Claude Code docs say it outright, and the Guillemot incident is the cautionary
tale (a "safe delete" script that was itself the unsafe part). So a harness
template that only *tells* an agent "never run `rm -rf`" gives false comfort.
Anything fileflow generates must therefore (a) separate **advisory** from
**enforced**, and (b) have a mechanical check that notices when a harness is
advisory-only.

This is the same principle as the receipt: *declare what backs a claim.*

## 3. The 8 components → what fileflow can honestly do

fileflow is a **context packager whose core never calls a model** (ADR-007; an opt-in single-shot `ask` command was added later by ADR-010 and does not change this: it is one prompt-in/answer-out call, not an agent loop). It cannot be
the harness. It can (1) **scaffold** the repository-resident parts of one, (2)
**verify** those parts mechanically, and (3) **pack** them into a prompt.

| # | Component | Lives in | fileflow's role | Built? |
|---|---|---|---|---|
| 1 | Instructions | `AGENTS.md` (+ `CLAUDE.md` importing it) | scaffold a ≤100-line map; **check** size, dangling links, import depth/cycles, Codex 32 KiB chain, `AGENTS.md`/`CLAUDE.md` drift | ✅ |
| 2 | Context | `docs/`, `ARCHITECTURE.md` | scaffold the knowledge-base skeleton; **check** cross-links; **pack** it (`--preset onboard`) | ✅ |
| 3 | Skills | `.claude/skills/*/SKILL.md` | **check** frontmatter (`name`/`description` rules) and body length | ✅ |
| 4 | Memory | `docs/exec-plans/`, `docs/design-docs/` | scaffold the folders; nothing to run — memory is "files the next session can read" | ✅ (scaffold) |
| 5 | Permissions | `.claude/settings.json`, container/VM | scaffold a **conservative deny list** in the documented syntax; **check** that settings parse, that unsupported rule forms aren't used, and warn when permissions exist **only in prose** or `bypassPermissions` is the default | ✅ |
| 6 | Tools | MCP config, scripts | out of scope — fileflow doesn't manage tool wiring | ❌ by design |
| 7 | Checks / evals | CI, linters, tests | `fileflow check` *is* one such check for the instruction layer; the spec has a place to name the project's own | ✅ (layer 1 only) |
| 8 | The loop | `AGENTS.md` operating loop + `docs/HARNESS.md` | scaffold explicit **done / stop-and-ask / max-iterations** clauses; **check** they were filled in, not left as blanks | ✅ (the clauses; not the loop itself) |

**Not building (and why):** an agent runner, a reviewer-agent orchestrator, an eval
framework, memory storage, MCP management. Each fails the alignment filter (A1:
not a context-packaging job) or ADR-007 (no model calls).

## 4. Decisions

1. **`fileflow init --template harness`** scaffolds a *map-style* harness (not a manual) plus the **Harness Spec** one-pager (`docs/HARNESS.md`) — a construction from the public material above, labelled as such.
2. **`fileflow check`** lints the instruction layer using only verified limits. Errors are reserved for things that **definitely break** (dangling pointers in `AGENTS.md`/`CLAUDE.md`, a Codex instruction chain over 32 KiB which Codex silently truncates, malformed settings/skills). Everything judgement-shaped (length guidance, unfilled spec sections, prose-only permissions) is a **warning**; `--strict` promotes warnings.
3. **Sizes cite their source.** 100 lines = OpenAI's *guidance* (warn); 200 lines = Claude Code's documented threshold (warn); 32 KiB = Codex's hard truncation (error); 500-line skills = Anthropic's guidance (warn). Configurable under `[harness]`.
4. **A structural check cannot tell whether instructions are *good*.** The check output says so under "assumed"; what an agent actually loads depends on tool versions and settings ("unknown").
5. **Dogfood it.** fileflow's own repository gets the harness, filled in by hand, and the first findings go in `docs/field-notes.md` (engineering-contract Gate E). One maintainer is not "usage-proven" — the field notes say so.

## 5. Sources

- OpenAI — Harness engineering: leveraging Codex in an agent-first world: <https://openai.com/index/harness-engineering/>
- OpenAI Codex — Custom instructions with AGENTS.md: <https://developers.openai.com/codex/guides/agents-md>
- Claude Code — How Claude remembers your project: <https://code.claude.com/docs/en/memory>
- Claude Code — Configure permissions: <https://code.claude.com/docs/en/permissions>
- Claude — Skill authoring best practices: <https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices>
- AGENTS.md open format: <https://agents.md/>
- Sebastien Guillemot, X, 2026-08-26: <https://x.com/SebastienGllmt/status/2092634841863123047>
- Hashimoto's definition and AWS write-up, Lopopolo's AI Engineer talk and the Latent Space episode: referenced in the pasted material; **not re-fetched**.
