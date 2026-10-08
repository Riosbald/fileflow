# Field notes

> Engineering contract, **Gate E (usage)**: every confusing moment, failure and correction from real use goes here,
> and each is either fixed (with a regression test) or consciously accepted in writing.
>
> **Honest status:** the entries below come from the maintainers pointing fileflow at fileflow's own repository -
> one round, no independent users. That finds real bugs (it did, see below) but it is **not** "usage-proven".
> Gate E asks for at least 5 real sprints by at least 2 people; that has not happened.

## Format
`FN-nnn` · date · what happened · why it mattered · resolution · test that keeps it fixed

## Entries

### FN-001 · 2026-10-02 · The secret scanner cried wolf on fileflow's own source
- **What:** `fileflow check` on this repository reported possible secrets in 8 files (`cli.py`, `receipt.py`, `walker.py`, `app.js`, ...).
- **Why it mattered:** every hit was a false positive. A scanner that is wrong on its author's own code gets switched off, which defeats
  the point of having one. The causes: (1) `token` matched as a *substring*, so `tokens`, `tokenizer`, `max_tokens` (LLM tokens - this is a
  prompt tool) looked like credentials; (2) values that were code (`count_tokens(...)`, `_secrets.token_urlsafe(16)`) or constants
  (`SECRET_BLOCKED`) were treated as secret values; (3) a header-only private-key string in a test fixture counted as a leaked key.
- **Resolution:** key names are matched by whole word; a value must not be a call/attribute/expression and must look like a credential
  (digits among letters, or long and mixed-case); a private key needs a key *body*, not just a header.
- **Kept fixed by:** `tests/test_secretscan.py` - the exact offending lines, a table of true positives that must keep firing, and
  `test_scanning_our_own_source_is_quiet`.
- **Accepted limit (written down):** `password=correcthorsebatterystaple` (all lowercase, no digits, under 24 characters) is **not**
  flagged. The scanner favours silence over noise; the receipt lists the scan under "assumed", never "verified".

### FN-002 · 2026-10-02 · The `onboard` preset would have silently cut off its tail
- **What:** the generated `onboard` preset packs `docs/`. In this repo that is about 23,000 tokens (estimate) against a 12,000 budget, so
  the budget would have dropped the end of the pack without the user noticing.
- **Why it mattered:** this is the exact failure the receipt exists to prevent - and `fileflow check` caught it (`C_BUDGET_TRUNCATES`)
  the first time the full harness check ran.
- **Resolution:** the repository's preset now packs the map and the harness docs only (`AGENTS.md`, `ARCHITECTURE.md`, `docs/index.md`,
  `docs/HARNESS.md`, `docs/QUALITY_SCORE.md`, about 2,700 tokens), not the long review documents.
- **Kept fixed by:** `test_budget_truncation_and_placeholders` (the warning), and `test_this_repositorys_own_harness_passes_strict` (the repo
  stays clean; CI runs `fileflow check --strict`).
- **Open question:** should the *template's* `onboard` preset default to `docs/index.md` instead of `docs`? Left as `docs` because small new
  projects fit, and the check warns when they stop fitting.

### FN-003 · 2026-10-02 · A template line counted as an unfilled blank
- **What:** the Harness Spec's own intro sentence quoted the fill marker, so a fully filled-in spec would still have warned.
- **Resolution:** reworded the intro. **Kept fixed by:** `test_a_filled_in_harness_is_clean_even_under_strict`.

### FN-004 · 2026-10-02 · What a structural check cannot tell you (accepted, not fixed)
- `fileflow check` verifies sizes, links, formats and what is enforced versus only described. It cannot tell whether the instructions are
  *good*, whether an agent loads them, or whether it obeys them. The report says so under "assumed" and "unknown" on every run.
- The documented limits it encodes (100-line map guidance, 200-line Claude Code threshold, 32 KiB Codex truncation, 500-line skills, 4-hop
  imports) come from the vendors' documentation as of 2026-10-02 and will drift; they are configurable under `[harness]` in
  `.files-to-prompt` and sourced in `docs/research/harness-engineering.md`.

### FN-005 · 2026-10-02 · `fetch` held open by a slow-drip server (found while writing the adversarial test)
- **What:** a test server sending one byte every 150 ms made `fetch` run 15 s past a 1 s limit.
- **Why it mattered:** `HTTPResponse.read(8192)` waits to fill 8192 bytes, and a per-`recv` socket timeout resets on every byte, so a hostile server can hold a fetch open indefinitely. A *header* drip would have evaded any body-level deadline too.
- **Resolution:** a watchdog thread closes the socket at the deadline (covers connect, headers and body); the body loop uses `read1`. A follow-up race (watchdog firing after completion) was caught by a thread-exception warning and fixed.
- **Kept fixed by:** `test_slow_drip_server_hits_the_overall_deadline`, `test_a_server_that_drips_its_headers_cannot_hold_the_fetch_open`; removing the watchdog fails the header-drip test.

### FN-006 · 2026-10-02 · Two CLI/web divergences that predated this work, found by widening the fuzzer
- **What:** adding `\r`, form feed, U+2028, NEL, FS and emoji to the fuzz alphabet made 25 of 400 cases diverge. (1) Python's `str.splitlines()` also breaks on those separators and re-joins with `\n`, so `--line-numbers` silently **rewrote** files; JS never did. (2) The web engine kept CRLF while Python's text-mode read converted it.
- **Resolution:** line numbering splits on `\n` only; line endings are normalised to `\n` on read in both engines. Documented as policy.
- **Kept fixed by:** golden document with exotic separators, `test_line_numbers_split_on_newline_only...`, JS core test, fuzz at 600 cases.
- **Lesson:** ASCII-only fuzzing missed this for months. Add the hostile input to the generator *first*.

### FN-007 · 2026-10-02 · Saving from the web UI would have wiped a hand-written `[include]` list
- **What:** the config writer rebuilt known sections from the payload; the UI never sent `include`, so a save blanked it. Harmless while `[include]` was "reserved", live data loss the moment it worked.
- **Resolution:** a section the payload omits is preserved from the file; the client now round-trips `include`.
- **Kept fixed by:** `test_ui_save_without_an_include_section_preserves_the_include_list`.

### FN-008 · 2026-10-02 · `--base-url ""` silently chose the default host
- **What:** an empty flag (typically an unset shell variable) fell through to the provider default, so the *destination of your files* was decided by a typo.
- **Resolution:** an explicitly empty `--base-url` is `E_MODEL_CONFIG`; an empty *environment* variable is still plain "unset".
- **Kept fixed by:** `test_an_empty_base_url_is_an_error_not_a_silent_default`.

### FN-009 · 2026-10-02 · A security test that passed with the protection removed
- **What:** the first `ask` redirect test used HTTP 307. urllib never follows a POST on 307, so the test passed with redirects fully enabled. The real hazard is 301/302/303, where urllib turns the POST into a GET and **forwards the API key**.
- **How it was found:** mutation testing — one survivor out of sixteen.
- **Resolution / kept fixed by:** the test now covers 301/302/303/307/308 and a spy server; with the protection removed, 301/302/303 fail.
- **Also found the same way:** the IPv4-mapped-IPv6 rule was invisible on this Python (the stdlib already rejects it; 3.13 changed that), and a file named `__proto__` could silently lose its provenance label in JS. Each now has a test that fails without the code.

### FN-010 · 2026-10-02 · First real-world `fetch`: a bot challenge saved as "content", and noisy extraction
- **What:** against real sites, `pypi.org` served a "Client Challenge" interstitial. `fetch` saved its 212 characters as `provenance: observed` with a success message (it cleared the 200-character minimum by 12). `github.com` extraction included 100 lines of repo navigation, with every file name twice.
- **Why it mattered:** a user would believe they had the page. The label said "observed", which is true (that text *was* at that URL) and useless.
- **Resolution:** challenge/interstitial pages are refused (`E_FETCH_EXTRACT`, nothing written); `<article>` is preferred over `<main>`; short immediate duplicates collapse; pages under 600 characters get a warning. GitHub's page went from 216 lines to 66 (README only).
- **Kept fixed by:** tests built from the exact PyPI text, plus six mutations.
- **Accepted limit:** the challenge detector is a phrase/title list, so it will miss new wording. Sites that need a real browser will not work with `fetch`.

### FN-011 · 2026-10-02 · What the sandbox could not tell us
- Most hosts (`example.com`, Wikipedia, paulgraham.com, docs.python.org, OpenAI/Claude docs) are **not reachable from this sandbox** (identical failure from `curl`; egress appears allow-listed: `pypi.org` and `github.com` worked). So real-world `fetch` was exercised on **two sites**, not a broad sample.
- `ask` has **never talked to a live provider**: there is no API key here and the egress list would likely not allow it. Everything is mock-tested.
- `fetch` ignores `HTTP(S)_PROXY`; a user behind a mandatory proxy cannot use it.

### FN-012 · 2026-10-08 · The repo's own `.files-to-prompt` was git-ignored, so it was never committed (and a sandbox reset deleted it)
- **What:** `fileflow check` printed "no `.files-to-prompt`" on this repository. `.gitignore` line 16 listed `.files-to-prompt` (left over from the first prototype), so the file with this repo's presets and `[harness]` limits was never tracked; when the sandbox was reset it vanished, taking the `onboard` fix from FN-002 with it.
- **Why it mattered:** the whole point of that file is that a *team* shares it. Ignoring it makes presets and policy private to one machine, silently. Every doc that said "the repo's presets" was describing something git did not have. This is exactly the failure the governance product claims to catch.
- **Resolution:** restored from the harness template with the FN-002 narrowing; removed the `.gitignore` line; **`check` now warns `C_CONFIG_IGNORED`** when the config is ignored by `.gitignore` (so it fails `--strict`).
- **Kept fixed by:** `test_a_gitignored_config_is_flagged...`, `test_a_tracked_config_is_not_flagged`, and `test_this_repository_tracks_its_own_config` (asserts the file exists and is not ignored).
- **Lesson:** dogfooding found a *process* bug that no unit test of the feature could: the file the feature reads was never in version control. Verify that the thing you depend on is actually committed.

### FN-013 · 2026-10-08 · The first real-browser run: 24 accessibility failures, one blank first screen, and three tests that lied

- **What:** until today no browser had ever opened the UI (sandbox had none). A headless Chromium was obtained from an npm package (`@sparticuz/chromium`; its shared libraries had to be extracted by hand) and axe-core + scripted keyboard/reflow checks were run. 24 failures. Full table: `docs/design-system.md` §2. The ones that mattered:
  - **New visitors saw an empty file tree and a blank editor** (`init()` looked for a row before rendering).
  - **File selection was mouse-only** (tree rows had no `tabindex`); the dialog had no Escape/trap/focus-restore; the secrets warning was green; a Google Fonts request left the machine on every page load.
- **Why the existing tests missed it:** `smoke.test.js` printed "rendered the tree" but only asserted the output; and `dom_shim.js` *returned a fabricated tree row* for `#file-tree .tree-file` and did not clear `innerHTML`. A test double that cannot fail is not a test.
- **Three of my own checks were wrong, found by mutation testing** (rule: read *which* case fails):
  1. "tree arrows removed" **survived** — the assertion ("focus is still on a tree item") is true when arrows do nothing. Now asserts focus lands on the correct row.
  2. The audit flaked about 1 run in 2 — axe sampled a half-switched theme (dark text on light background, "1.02:1"). Fixed by waiting for the theme change; 6/6 clean afterwards.
  3. The live-reload focus test picked a file inside a collapsed folder (cannot take focus) and "failed" a correct app.
- **Resolution / kept fixed by:** `tests/browser/audit.mjs` (opt-in), `tests/test_design_tokens.py` (58 static tests, runs in `pytest`), a stricter smoke test and a more honest DOM shim. 7 UI + 9 static mutations, each caught by its own check.
- **Accepted limits:** no real screen reader, device, Safari or Firefox was used; axe covers a fraction of WCAG; two axe items are "needs manual review".
- **Lesson:** the biggest finding was not an accessibility rule but "the first screen is empty". Open the product in a real browser *before* writing a design system for it.

### FN-014 · 2026-10-08 · A stranger's first run: 100,000 tokens, six silent omissions, and a password the scanner could not see

- **What:** I ran `fileflow .` on a realistic messy project (git repo, `node_modules`, a `.env`, a 400 KB lockfile, a PNG) as a first-time user. It sent **≈100,044 tokens, 99.9 % of it `package-lock.json`**; said nothing about the six files it left out (`.env`, `.git/`, `node_modules/`…); and printed one warning, `UnicodeDecodeError`, about the PNG, while the receipt calls the same event `BINARY`. With `--include-hidden`, the scanner flagged the Stripe key in `.env` but **not** `DATABASE_URL=postgres://admin:<password>@host/prod` in the same file. Separately, `--ignore-patterns docs/x.csv` is silently a no-op (patterns match names, never paths), and my first draft of the fix advised exactly that.
- **Why it mattered:** the receipt already held a reason for every omitted file; the evidence existed and was withheld. Omission is the dangerous failure of a context tool because nobody notices an absence. The scanner's rule is "high confidence only" — and URL-embedded credentials are the commonest high-confidence shape.
- **Resolution:** `url-credentials` rule (+ npm, GitLab, SendGrid shapes; placeholders, `${VAR}`, `%s`, weak defaults, ports, SSH remotes and Sentry DSNs are deliberately *not* findings); a one-line terminal summary printed **after** the output and **only** when stderr is a TTY; a rare `Heads-up:` when one file is ≥ 5,000 tokens and ≥ 50 % of the prompt (CLI, `ask` before consent, web with a one-click fix saved to `.files-to-prompt`); one vocabulary with plain words for every reason code; a warning for path-shaped ignore patterns; a web "Left out of the prompt" list.
- **Kept fixed by:** 26 + 16 + 9 tests, a real-pty ordering test, a browser audit extension (XSS on a markup-named file, live-region rewrites, persistence of the fix, reflow at 320 px and 200 % text). 8/8 non-equivalent scanner mutations, 14/14 CLI mutations and 7/7 browser mutations caught by the check written for them.
- **Mistakes of my own, caught by the same method:** (1) the advice I first wrote did not work — a test that *executes* the advice found it; (2) a mutation of the "save" path survived because nothing checked that the fix is written to `.files-to-prompt`; (3) my "moved the summary before the output" mutation actually deleted the lines, so it proved nothing about ordering until a real move was tried.
- **Accepted limits / open:** (the missing default file-size cap was fixed in FN-015); known-noise files are warned about, not excluded by default; token-only userinfo (`https://<token>@host`) is not flagged. Decisions in `docs/systems-gap-analysis.md` §6.
- **Lesson:** seven earlier defects shared one shape — a green signal that was never earned. The cheapest detector is a stranger's first run, and it found more in an hour than another test file would have.

### FN-015 · 2026-10-08 · The novice-mistakes battery, a default size limit, and what a stranger still cannot do

- **What:** after FN-014 I ran the mistakes a newcomer makes. Most were handled well (clear message, a `Next:` step, exit 2). Four were not: (N1) a broken `.files-to-prompt` prints a warning and then **sends the prompt anyway with exit 0**, silently dropping safety settings such as `secrets.mode = "block"`; (N2) a typo'd key (`formt = "xml"`) did nothing and `fileflow check` said **ok, 0 warnings**; (N3) an empty folder printed a blank line and exit 0; (N4) `fetch notaurl` said `got 'none'`. Separately, `/api/file` read any file whole into memory, and `FILEFLOW_MAX_FILE_BYTES=10MB` crashed every command at import with a traceback.
- **Why it mattered:** each is the FN-014 shape again: the tool accepted an instruction and reported nothing. N3 produces a blank prompt that gets pasted into a model; the unbounded read is a self-inflicted denial of service from one click in the UI.
- **Resolution:** default per-file limit of 1 MiB (decision A: warn with real size and the exact remedy, `TOO_LARGE` in the receipt and web list, `/api/file` returns 413 with the reason, the editor shows the reason instead of looking empty, a bad env value falls back and says so); unknown config keys (`check` lists all, runs warn only for near-misses, so a key from a newer fileflow stays quiet); an empty-result notice that names `--include-hidden` / `--ignore-gitignore` when they apply; plain fetch wording. **N1 is left for the owner** (below).
- **Kept fixed by:** `tests/test_size_limit.py` (23, real default tested in clean subprocesses), `tests/test_config_hygiene.py` (16), `tests/test_stranger_walkthrough.py` (18; the walkthrough as a standing test incl. 10 novice mistakes that must never produce a traceback or a Python exception name), a live-mode test for the editor message. Mutation results: size limit 10/10 after fixing one survivor (the message's "real size" was untested because my test file was almost exactly the limit, so size and limit both read "1.0 MB"); config hygiene 13/13; walkthrough 4/5 against re-introduced old behaviours (the fifth, bare-name fetch wording, is covered by the hygiene tests instead).
- **Mistakes of my own:** a gitignored-folder test assumed nothing would be sent with `--include-hidden`, but `.gitignore` itself is a hidden file and is sent; a convoluted `A or B` assertion that could not fail; a test of the preset-key schema that was close to a tautology (deleted rather than kept as decoration).
- **N1 resolved (decision D, applied):** a config that cannot be parsed now stops the run (`E_CONFIG_PARSE`, exit 2, "nothing was sent"), matching how a config from a *newer* fileflow was already refused. Writing the test for it exposed a second, wider hole: **wrong-typed settings were silently dropped** (`[output] format = 7` ran with defaults; `check` said nothing about it). `validate_settings` now type-checks the global tables at run time and in `check`, and `check` reports every schema problem instead of stopping at the first. `fileflow serve` stays tolerant by design. Tests: a broken config holding `secrets.mode = "block"` cannot leak a secret; 12 wrong-value cases; the web UI's own output passes. Mutations: 10/10 caught after fixing two of my own errors (a no-op mutation I wrote, and a duplicate `patterns` check that the config reader already makes unreachable, which I deleted).
- **Measured (one sandbox, fast network, not representative of a Lagos connection):** fresh venv + install of the wheel ≈ 0.7 s (+3 s venv, +5 s for the `[server]` extra); first run on the messy project 92 ms. Install speed is not the barrier — **getting the package is: nothing is published.**

## Not yet observed (so not claimed)
- Whether the harness template helps an agent do better work.
- Whether `ask` works against OpenAI, Anthropic or a local Ollama in practice (see FN-011). There is no measurement of that here, and the public sources for the idea
  are practitioner reports, not controlled studies.
- Behaviour with a real screen reader, on a real phone, or in Safari/Firefox (FN-013 used one Chromium build).
- Behaviour on Windows, on monorepos with deeply nested `AGENTS.md`, or with Codex/Claude Code versions other than the documentation read.
