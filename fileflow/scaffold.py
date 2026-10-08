"""`fileflow init`: scaffold a project from a built-in template.

Templates are plain strings (no network, no template language). Rules:

* **Never overwrite.** Existing files are skipped and listed unless ``force``.
* Blanks to fill are written as ``<<FILL: what goes here>>`` so ``fileflow check``
  can tell a finished file from a template that was never filled in.
* The harness template is a *construction from public material* (OpenAI's
  "Harness engineering" post, the AGENTS.md convention, Claude Code docs); see
  ``docs/research/harness-engineering.md``. It is not a copy of any paid artefact.
"""

import os

FILL = "<<FILL: "

# --------------------------------------------------------------------------
# minimal
# --------------------------------------------------------------------------
_MINIMAL_CONFIG = '''# fileflow settings, shared by the CLI, `fileflow serve` and the web client.
version = 1

[output]
format = "default"
separators = true
line_numbers = false
max_tokens = 0

[exclude]
patterns = ["node_modules", "dist", "*.log", "__pycache__"]

[ignore]
gitignore = true
hidden = false

[secrets]
mode = "warn"            # warn | exclude | block | off

# Run with:  fileflow --preset analyze --copy
[presets.analyze]
description = "Read-only tour of the codebase"
paths = ["."]
max_tokens = 8000
instruction = "Analyze this codebase without changing anything. Explain the folder structure, the main entry points, and the three highest-priority improvements."
'''

# --------------------------------------------------------------------------
# web-sprint (the "one section at a time" workflow)
# --------------------------------------------------------------------------
_WEB_CONFIG = _MINIMAL_CONFIG.split("# Run with:")[0] + '''# Run with:  fileflow --preset hero --copy
[presets.analyze]
description = "Read-only tour of the project"
paths = ["."]
exclude_patterns = ["screenshots", "design-references"]
max_tokens = 8000
instruction = "Analyze this project without changing anything. Explain the folder structure, main pages, reusable components, current design system, and the next three highest-priority improvements."

[presets.hero]
description = "Hero section only (one section per prompt)"
paths = ["components", "notes/project-notes.txt"]
max_tokens = 12000
instruction = "Improve the hero section ONLY. Use reusable components and responsive CSS. Match spacing and typography from the project notes. Do not touch other sections."

[presets.audit]
description = "Final polish pass against the checklist"
paths = ["components", "notes/testing-checklist.md"]
max_tokens = 12000
instruction = "Check spacing and alignment consistency, smooth section transitions, mobile responsiveness, and standardized buttons/cards/borders. Report anything you changed."
'''

_PROJECT_NOTES = '''# Project notes

Update this file as you go. It is the first thing to read when you come back.

## Purpose
{FILL}what is this site for?>>

## Target users
{FILL}who will use it, and what should they do first?>>

## Required pages / sections
{FILL}hero, about, services, testimonials, call to action, footer ...>>

## Visual direction
Colours: {FILL}primary / secondary / accent - chosen to suit the organisation, not just to look nice>>
Fonts: {FILL}heading / body>>

## Rules
- One section per AI prompt. Never ask for the whole site at once.
- Reference sites are inspiration for structure, spacing and typography - not content or branding.

## Unfinished tasks
- [ ] {FILL}next task>>
'''.replace("{FILL}", FILL)

_TESTING_CHECKLIST = '''# Testing checklist

- [ ] Every button works
- [ ] Navigation works
- [ ] Layout works on mobile
- [ ] Colours are readable (contrast)
- [ ] Transitions are smooth
- [ ] Images and text load correctly
- [ ] No console errors
- [ ] Page is fast enough
'''

# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
_AGENTS_MD = '''# AGENTS.md

This file is a **map**, not a manual. Keep it short (about 100 lines). Put detail in
`docs/` and link to it - anything not linked from here is invisible to the agent.

## Purpose
{FILL}one or two sentences: what this repository is and who it serves>>

## Start here
- Architecture map: [ARCHITECTURE.md](ARCHITECTURE.md)
- Docs index: [docs/index.md](docs/index.md)
- Harness spec (permissions, checks, loop): [docs/HARNESS.md](docs/HARNESS.md)
- Quality scorecard: [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md)
- Design decisions: [docs/design-docs/index.md](docs/design-docs/index.md)
- Plans in flight: `docs/exec-plans/active/`

## Commands
- Run the checks: {FILL}the one command that must pass before you say "done">>
- Run one test: {FILL}command>>

## Operating loop
1. Read the relevant docs above before changing code.
2. For anything bigger than a small fix, write a plan in `docs/exec-plans/active/`.
3. Implement the smallest coherent slice.
4. Run the checks. Read failures as instructions.
5. Request review. Fix or push back with a reason.
6. Stop when the definition of done in `docs/HARNESS.md` holds - or ask.

## Boundaries
Prose here is advisory. What is actually enforced is listed in
[docs/HARNESS.md](docs/HARNESS.md#5-permissions).

## When you find a mistake
Don't add a paragraph here. Add a doc, a check, or a permission, so it cannot happen
again. Then link it from this map.
'''.replace("{FILL}", FILL)

_CLAUDE_MD = '''@AGENTS.md

Claude Code does not read AGENTS.md on its own; the line above imports it, so there is
one source of truth. Add Claude-only notes below this line, not a copy of AGENTS.md.
'''

_SETTINGS_JSON = '''{
  "permissions": {
    "deny": [
      "Bash(rm -rf *)",
      "Bash(git push --force *)",
      "Bash(git push -f *)",
      "Read(./.env)"
    ]
  }
}
'''

_ARCHITECTURE_MD = '''# Architecture

A top-level map: domains, layers, and the direction dependencies may point.

## Domains
{FILL}name each domain and what it owns>>

## Layers and allowed dependencies
{FILL}e.g. Types -> Config -> Repo -> Service -> Runtime -> UI; say what is NOT allowed>>

## Where things live
{FILL}directory -> responsibility>>
'''.replace("{FILL}", FILL)

_HARNESS_MD = '''# Harness Spec

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
{FILL}link the docs; if it only exists in chat, a doc or someone's head, the agent cannot see it>>

## 3. Skills
Playbooks loaded only when a task needs them (`.claude/skills/<name>/SKILL.md`):
{FILL}list the repeatable tasks worth a skill, or "none yet">>

## 4. Memory
What it still knows tomorrow. In files, in the repo: plans in `docs/exec-plans/`, decisions in
`docs/design-docs/`, grades in [docs/QUALITY_SCORE.md](QUALITY_SCORE.md).

## 5. Permissions
**Prose is advisory; enforcement is a fact.** A rule written here asks the agent to behave.
A deny rule in `.claude/settings.json`, a container, or a VM makes it impossible. Write both,
and say which is which.

- Never, without asking me: {FILL}e.g. delete outside the repo, force-push, read secrets, touch production>>
- Ask first: {FILL}e.g. new dependencies, schema changes, anything irreversible>>
- Allowed without asking: {FILL}e.g. edit files in this repo, run the check command>>
- **Enforced by:** {FILL}`.claude/settings.json` deny rules / container / VM - name the mechanism, or write "nothing yet">>

Starter deny rules are in `.claude/settings.json`. They match command *patterns* and can be
bypassed by a differently-written command, so they are a seat belt, not a sandbox. If the
agent runs unattended, run it in a container or VM.

## 6. Tools
What it can reach: files, terminal, MCP connections.
{FILL}list the tools and the data each can touch; remove what the task does not need>>

## 7. Checks
Catch mistakes before I see them.
- Must pass before "done": {FILL}test / lint / type-check command>>
- Structural invariants (mechanical, not opinion): {FILL}e.g. file size limit, dependency direction, no network in unit tests>>
- Failure messages tell the agent how to fix the problem (a failing check is a prompt).
- Reviewer: {FILL}who or what reviews, and what it must ask>>

## 8. The loop
How it plans, acts, looks at the result and decides it is done.
- Done means: {FILL}observable conditions, e.g. checks green, docs updated, plan moved to completed/>>
- Stop and ask me when: {FILL}ambiguity that changes scope, a permission is needed, the same check fails three times>>
- Max attempts before escalating: {FILL}a number, and a time limit>>
'''.replace("{FILL}", FILL)

_DOCS_INDEX = '''# Docs index

The repository is the system of record. If it is not here, the agent cannot see it.

- [Harness spec](HARNESS.md) - permissions, checks, loop
- [Quality scorecard](QUALITY_SCORE.md) - where the gaps are
- [Design decisions](design-docs/index.md)
- Plans: `exec-plans/active/` (in flight) and `exec-plans/completed/`
'''

_QUALITY = '''# Quality scorecard

Grade each area A-D and note the gap. Revisit when something changes - stale grades are worse than none.

| Area | Grade | Gap / next step |
|---|---|---|
| {FILL}domain or layer>> | {FILL}A-D>> | {FILL}what is missing>> |
'''.replace("{FILL}", FILL)

_DESIGN_INDEX = '''# Design decisions

One short file per decision: context, decision, alternatives, consequences.

{FILL}link each decision here>>
'''.replace("{FILL}", FILL)

_HARNESS_CONFIG = _MINIMAL_CONFIG.split("# Run with:")[0] + '''[harness]
max_lines = 100          # AGENTS.md: guidance (OpenAI's "map, not manual"); warns above this
codex_max_bytes = 32768  # Codex stops reading instructions at this size; errors above it

# Run with:  fileflow --preset onboard --copy
[presets.onboard]
description = "Hand an agent the map and the docs (no source code)"
paths = ["AGENTS.md", "ARCHITECTURE.md", "docs"]
max_tokens = 12000
instruction = "You are joining this repository. Read the instructions and docs below. Summarise the operating loop, the permissions and the checks in your own words, and list anything that is missing or contradictory. Do not change anything."

[presets.harness-review]
description = "Review the harness itself against the 8 components"
paths = ["AGENTS.md", "CLAUDE.md", "docs/HARNESS.md", ".claude"]
max_tokens = 12000
instruction = "Review this agent harness against eight components: instructions, context, skills, memory, permissions, tools, checks, loop. For each, say whether it is present, what is only advisory (prose) versus enforced (deny rules, sandbox, failing checks), and what is missing. Give the three highest-value fixes."
'''

TEMPLATES = {
    "minimal": {
        "description": "Just a .files-to-prompt with one 'analyze' preset",
        "files": {".files-to-prompt": _MINIMAL_CONFIG},
        "next": "fileflow --preset analyze --copy",
    },
    "web-sprint": {
        "description": "Folders + notes + checklist + presets for building a site one section at a time",
        "files": {
            ".files-to-prompt": _WEB_CONFIG,
            "notes/project-notes.txt": _PROJECT_NOTES,
            "notes/testing-checklist.md": _TESTING_CHECKLIST,
            "design-references/.gitkeep": "",
            "content/.gitkeep": "",
            "components/.gitkeep": "",
            "screenshots/.gitkeep": "",
        },
        "next": "edit notes/project-notes.txt, then: fileflow --preset analyze --copy",
    },
    "harness": {
        "description": "AGENTS.md map + docs skeleton + Harness Spec + starter permissions",
        "files": {
            ".files-to-prompt": _HARNESS_CONFIG,
            "AGENTS.md": _AGENTS_MD,
            "CLAUDE.md": _CLAUDE_MD,
            ".claude/settings.json": _SETTINGS_JSON,
            "ARCHITECTURE.md": _ARCHITECTURE_MD,
            "docs/index.md": _DOCS_INDEX,
            "docs/HARNESS.md": _HARNESS_MD,
            "docs/QUALITY_SCORE.md": _QUALITY,
            "docs/design-docs/index.md": _DESIGN_INDEX,
            "docs/exec-plans/active/.gitkeep": "",
            "docs/exec-plans/completed/.gitkeep": "",
        },
        "next": "fill the <<FILL: ...>> blanks in docs/HARNESS.md, then run: fileflow check",
    },
}

# Files only written for a specific tool; skipped with --no-claude.
CLAUDE_ONLY = {"CLAUDE.md", ".claude/settings.json"}


def init_project(dest, template, force=False, claude=True):
    """Write ``template`` into ``dest``. Returns ``(created, skipped, overwritten)`` (relative paths)."""
    spec = TEMPLATES[template]
    created, skipped, overwritten = [], [], []
    for rel, content in spec["files"].items():
        if not claude and rel in CLAUDE_ONLY:
            continue
        path = os.path.join(dest, *rel.split("/"))
        existed = os.path.exists(path)
        if existed and not force:
            skipped.append(rel)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        (overwritten if existed else created).append(rel)
    return created, skipped, overwritten
