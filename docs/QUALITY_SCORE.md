# Quality scorecard

Grade each area A-D and note the gap. Revisit when something changes - stale grades are worse than none.

**Rubric.** A = tested end to end *and* the safety-relevant checks were mutation-tested. B = tested, not mutation-tested, or
very new. C = partly tested. D = no automated tests. These are the maintainer's grades, not an independent audit.

| Area | Grade | Gap / next step |
|---|---|---|
| Engine: walker, collect, render | A | Python/JS byte parity pinned by golden corpus and fuzz (non-ASCII and emoji included) |
| Presets, instructions, variables | A | Precedence, confinement and placeholder lint are tested and mutation-tested |
| Context receipt | A | Completeness and closed reasons tested and mutation-tested; **web client has no receipt** |
| `fetch` (SSRF-safe download, extraction) | A | 15 controls mutation-tested, incl. real HTTPS verification; **proxy variables unsupported; never run against the open internet in CI** |
| `ask` (opt-in model call) | C | Safety rules mutation-tested against mock providers; **no live provider call has ever been made**, Anthropic request format confirmed via secondary sources |
| `--diff` / git integration | B | Hostile-config protections proven by marker files; cannot defend against config-defined clean/smudge filters |
| `[include]`, provenance labels | A | Golden corpus + fuzz parity (Python = JS); static web client does not yet label imported files |
| Secret guard | B | Pattern-based; precision fixed after the first dogfooding run; only that one round of real-world tuning |
| Server guard (Host, Origin, token) | A | Unit and real-subprocess tests, mutation-tested; **token cookie behaviour inside an iframe is unverified** |
| `init` / `check` (harness tooling) | B | New; planted-problem tests and mutation checks; one dogfooding round |
| Web client | C | Logic tested through a DOM shim; **no real-browser test has ever run** |
| VS Code extension | D | No automated tests |
| Packaging | B | Wheel contents tested from a clean copy; not yet published or installed from an index |
| CI | C | Workflow exists; **never run on GitHub** |
