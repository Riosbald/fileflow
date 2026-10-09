# Harness Spec

> One page. Every blank marked FILL is a decision you have not made yet - fill it or delete the line.
>
> **Agent = model + harness.** The harness is everything that is not the model: what it
> reads, what it may do, how it is checked, and when it stops. This page is a template
> built from public material (OpenAI's "Harness engineering" post, the AGENTS.md
> convention, the Claude Code and Codex docs); see `docs/research/harness-engineering.md`
> in the fileflow repository for the sources and what could not be verified.
>
> Check this page with `fileflow check`.

## 1. Instructions
What the agent reads every time: [AGENTS.md](../AGENTS.md) - a map of about 100 lines. `CLAUDE.md`
imports it (`@AGENTS.md`) so there is one source of truth.
Maintenance rule: when the agent makes a mistake, add a doc, a check or a permission - not a paragraph.

## 2. Context
What it must know about the product, the users and the domain, and where that lives:
[ARCHITECTURE.md](../ARCHITECTURE.md) (map), [architecture.md](architecture.md) (review, risks, ADRs),
[product-roadmap.md](product-roadmap.md) (what and why), [engineering-contract.md](engineering-contract.md) (release gates).
Nothing the agent needs lives only in chat.

## 3. Skills
Playbooks loaded only when a task needs them (`.claude/skills/<name>/SKILL.md`):
none yet. Candidates once a task repeats three times: "add a rendering feature golden-first", "mutation-test a safety check".

## 4. Memory
What it still knows tomorrow. In files, in the repo: plans in `docs/exec-plans/`, decisions in
`docs/design-docs/`, grades in [docs/QUALITY_SCORE.md](QUALITY_SCORE.md).

## 5. Permissions
**Prose is advisory; enforcement is a fact.** A rule written here asks the agent to behave.
A deny rule in `.claude/settings.json`, a container, or a VM makes it impossible. Write both,
and say which is which.

- Never, without asking me: delete anything outside this repository; force-push or rewrite git history; read or print secrets or `.env`; run `fileflow serve --allow-remote --no-token` on anything but a throwaway demo folder; run `fileflow ask` with a real API key, or `fileflow fetch` against anything but a URL I gave you; publish a package or push to a remote.
- Ask first: adding a dependency; changing a public format (rendered output, receipt, config schema `version`); touching a security boundary (`server/guard.py`, root confinement, the walker's symlink rule); lowering a test's strictness.
- Allowed without asking: edit files in this repo; run `pytest`, the Node suites, the fuzz test and `fileflow check`; create and delete files under `/tmp`.
- **Enforced by:** four deny rules in `.claude/settings.json` (force-push, `rm -rf`, reading `.env`). They are command patterns - a seat belt, not a sandbox - and **there is no container or VM configured for this repo**, so an unattended agent run is still advisory-only beyond those four rules.

Starter deny rules are in `.claude/settings.json`. They match command *patterns* and can be
bypassed by a differently-written command, so they are a seat belt, not a sandbox. If the
agent runs unattended, run it in a container or VM.

## 6. Tools
What it can reach: files, terminal, MCP connections.
Shell, Python 3.11 venv (`.venv`), Node 22, `git`. No MCP servers configured. The test suites need no internet: `fetch` and `ask` are tested against local mock servers on 127.0.0.1 (one test builds a self-signed certificate with `openssl`). Real network use is `pip install` and `fileflow serve` on loopback.

## 7. Checks
Catch mistakes before I see them.
- Must pass before "done": `python -m pytest -q`, the four Node suites (`golden core smoke live`), and `fileflow check --strict`.
- Structural invariants (mechanical, not opinion): Python and JS render byte-identically (golden corpus + fuzz, including CRLF, odd separators, emoji, front matter and include patterns); the base CLI import pulls in no server dependency; every excluded file in a receipt has a reason from the closed set; `AGENTS.md` stays a short map (`fileflow check`).
- Failure messages tell the agent how to fix the problem (a failing check is a prompt).
- Reviewer: a human, after the fact. There is no automated reviewer agent, and the GitHub Actions workflow (`ci/ci.yml`, not yet installed at `.github/workflows/`) has **not yet been run on GitHub** - only its commands have been run locally.

## 8. The loop
How it plans, acts, looks at the result and decides it is done.
- Done means: the checks above are green; behaviour changes are reflected in the README and the relevant doc; any new safety check has been mutation-tested; the roadmap status says only what the tests prove.
- Stop and ask me when: a change would alter a public format without a golden-corpus update; a security boundary would change; a permission is needed that is not listed above; the same check fails three times.
- Max attempts before escalating: three per failing check, then stop and report what was tried.
