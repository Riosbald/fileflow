# Design system and accessibility — web client

*Audited and changed 2026-10-08. Every claim below is tagged **Verified** (a test or real-browser run in this repo shows it), **Measured**, or **Not verified**.*

## 0. In one screen

- The client already had colour, radius, shadow and motion tokens. It was **not** missing a design system; it was missing **accessibility behaviour** and a few tokens.
- A real-browser audit (headless Chromium 153 + axe-core 4.14, WCAG 2.2 AA tags) found **24 failures**. After this pass: **0**, repeated 6 times with no flake. Six of them were real product bugs, not style issues (§2).
- Run it yourself: §7. Fast static guards run in every `pytest` (`tests/test_design_tokens.py`, 58 tests).
- **Not verified:** any real screen reader, any real phone, Safari, Firefox. axe finds roughly a third of WCAG issues; passing it is necessary, not sufficient.

## 1. What was audited, and how

| Area | Method | Tool |
|---|---|---|
| Automated WCAG 2.0/2.1/2.2 A+AA rules | axe-core, both themes, sample project **and** server mode | `tests/browser/audit.mjs` |
| Colour contrast of every token pair | OKLCH → sRGB maths, both themes | `tests/test_design_tokens.py` |
| Keyboard: skip link, tab order, focus ring on 40 stops, tree, dialog, drop zone | scripted key presses | audit |
| Reflow: horizontal overflow at 320 / 430 / 768 / 1280 / 1440 / 2560 px; 320 px at 200 % text | DOM measurement | audit |
| Touch targets (24 px AA floor, 44 px goal) | bounding boxes under touch emulation | audit |
| Reduced motion, scroll-reveal settles at full opacity | media emulation | audit |
| Privacy: no off-origin request, fonts actually load | request log | audit |

## 2. Findings → fixes (before: 24 failures)

| # | Finding | Why it mattered | Fix | Guarded by |
|---|---|---|---|---|
| 1 | **First load showed an empty file tree and blank editor** (`init()` looked for a row before rendering the tree) | First impression of the product; invisible to the Node tests because the DOM shim *fabricated* that row | render first; shim now looks at real rows and clears `innerHTML` like a DOM | smoke test (fails on the old order), audit |
| 2 | Tree rows were `div`s with no `tabindex` — **file selection was mouse-only** | The core action was unusable by keyboard | WAI-ARIA tree: one tab stop, ↑ ↓ Home End → ← Enter Space, `aria-expanded/selected/level` | audit (checks focus *moves to the right row*) |
| 3 | Re-rendering the tree dropped focus (also on **live reload**) | Keyboard user thrown to page top on every file change | one `rebuildTreeDom()` keeps focus + tab stop | audit (`refreshFromServer()` keeps focus) |
| 4 | Dialog: no Escape, no focus trap, no focus restore, page behind not inert | WCAG 2.1.2 / 2.4.3 | all four; bottom sheet on phones | audit |
| 5 | Empty "Add file" submit failed silently | WCAG 3.3.1 | `aria-invalid` + associated text error + focus | audit |
| 6 | `<pre aria-live>` on the prompt | A screen reader would re-read the whole prompt on every edit | removed; short `role="status"` line ("7 files · …") | static test + audit |
| 7 | Drop zone: `role=button` with **no Enter/Space**; in server mode "disabled" only by `pointer-events` | Keyboard users could still trigger it | Enter/Space; `aria-disabled` + out of tab order | audit |
| 8 | Secrets / budget warning rendered in **success green** | Wrong semantic state | `--warning` token | contrast test |
| 9 | `--text-3` failed 4.5:1 in 5 combinations; hidden files used `opacity` (2.7:1) | WCAG 1.4.3 | token lifted; colour + italic instead of opacity | contrast test + axe |
| 10 | **Google Fonts loaded from the network** | Every visit told a third party; broken on restricted networks; contradicts a local-first tool | Inter + JetBrains Mono self-hosted (latin subset, 88 KB, SIL OFL, licences shipped) | static test + audit (no off-origin requests) |
| 11 | No skip link; focus ring missing on several controls; `outline:none` on editor/inputs | WCAG 2.4.1 / 2.4.7 | skip link; one global `:focus-visible` | audit (ring on 40 stops) |
| 12 | Panel header stats overlapped the Copy button; "Add file" wrapped | Layout collision | wrapping header group | screenshot |

## 3. Tokens (all in `fileflow/web/css/styles.css`; no hex values outside the token blocks — enforced)

| Group | Tokens |
|---|---|
| Colour (per theme) | `--bg --bg-elev --panel --panel-2 --border-color --text --text-2 --text-3 --primary --primary-2 --on-primary --accent --code-bg` |
| Semantic state | `--success --danger --warning --info` (+ `.status[data-state=ok|warn|error|info]`) |
| Focus | `--focus` (≥ 3:1 on every surface), `--focus-ring`, `--focus-offset` |
| Spacing | `--space-1…7` = 0.25 / 0.5 / 0.75 / 1 / 1.5 / 2 / 3 rem |
| Type | `--text-xs…xl` (rem only — px font sizes are a failing test); families Inter / JetBrains Mono, self-hosted |
| Radius | `--radius-sm 7 · md 10 · lg 16 · xl 22 · pill` |
| Shadow / elevation | `--shadow-sm|md|lg`; stacking `--z-header 50 · progress 60 · modal 100 · toast 110 · skip 120` |
| Motion | `--dur-fast 150 · base 200 · slow 300 · entrance 500`, `--spring`, `--decel`; `prefers-reduced-motion` collapses all |
| Touch | `--target-min 2.75rem` (44 px) applied under `(pointer: coarse), (max-width: 760px)` |

**Contrast, measured** (both themes, every text pair ≥ 4.5:1; focus ring ≥ 3:1): text 16.6, text-2 7.8–9.3, text-3 now ≥ 4.5 on every surface it is used on.

## 4. Components and states

| Component | States covered | Notes |
|---|---|---|
| Button (`.btn`, `-primary`, `-ghost`, `-sm`, `.icon-button`) | default, hover, active, focus-visible, success (copy) | icon-only buttons carry `aria-label`; 44 px on touch |
| Input / textarea / select / chips | default, focus (ring), `aria-invalid` + `.field-error` | every input has a `<label>` or `aria-label` |
| Switch | on/off, focus | native checkbox under the track (keyboard + SR for free) |
| Segmented control | checked, focus | radiogroup: one tab stop, arrow keys select |
| Tree | selected, hidden-file (colour + italic), collapsed/expanded, focus | roving tabindex |
| Dialog / sheet | open, error | centred ≥ 760 px, bottom sheet below; Esc, trap, inert background, focus restore |
| Toast + status line | `role=status` | toast auto-dismisses; anything that must persist (secret warning) lives in the status line, not the toast |
| Advice banner `.advice` | shown only when one file dominates the prompt (≥ 5,000 tokens and ≥ 50 %) | warning surface, `role=status`, rewritten only when its content changes; one action ("Leave it out") that is saved to `.files-to-prompt` and reversible |
| Left-out list `.left-out` | collapsed `<details>`, plain-word reasons, bounded to 300 rows | file names rendered as text nodes (a markup-named file is in the demo to prove it) |
| Status pill `.status` | info / ok / warn / error | colour is never the only signal — always paired with text |
| Command surface, agent status, tool status | **design only, not built** | no agents or command palette exist (ADR-010 boundary). When A1 exists: *running* = `info` + text, *awaiting approval* = `warn`, *blocked by policy* = `error` with the word "blocked". Reuse `.status`; do not invent a second system. |

Iconography: inline SVG, 2 px stroke, `currentColor`, 14–18 px, always with a text label or `aria-label`; decorative icons `aria-hidden`.

## 5. WCAG 2.2 AA status

| Criterion | Status |
|---|---|
| 1.1.1, 1.3.1, 4.1.2 names/roles/structure | **Verified** (axe 0 violations, 31 rules pass, 2 flagged "needs manual review" and **not yet reviewed by a human**) |
| 1.4.3 / 1.4.11 contrast | **Verified** (token maths + axe, both themes) |
| 1.4.4 / 1.4.10 resize and reflow | **Verified** at 320 px and at 200 % text — no horizontal scroll |
| 2.1.1 / 2.1.2 keyboard, no trap | **Verified** for tree, dialog, drop zone, segmented control, toggles |
| 2.4.1 skip link, 2.4.3 focus order, 2.4.7 focus visible | **Verified** (ring on 40 consecutive tab stops) |
| 2.5.8 target size (24 px) | **Verified**; 44 px goal met on touch/narrow |
| 2.3.3 / reduced motion | **Verified** (no animation > 50 ms under the media query) |
| 3.3.1 error identification | **Verified** for the only validated form (Add file) |
| 4.1.3 status messages | **Verified** structurally (`role=status`); **Not verified** that a real screen reader announces it sensibly |
| Real screen readers (NVDA, VoiceOver, TalkBack), real devices, Safari/Firefox | **Not verified** |
| Cognitive load (the ADHD-first goal) | **Not verified** — no user has been observed using it |

## 6. Responsive and performance

- **Verified:** no horizontal overflow and no clipped control at 320, 430, 768, 1280, 1440, 2560 px.
- **Honest limit:** the CSS is desktop-first (`max-width` queries), not mobile-first. Mobile *is* recomposed (single column, bottom-sheet dialog, stacked header and actions), but a true mobile-first rewrite was not done — it would touch ~1,300 lines for no user-visible gain today.
- **Measured** (desktop, local server, headless Chromium): 199 KB total, uncompressed (HTML 15, CSS 29, JS 66, fonts 88); FCP ≈ 0.56 s; LCP ≈ 0.61 s; CLS 0.014. Mobile-throttled numbers were **discarded** — the throttle did not visibly apply to localhost.
- **No framework.** Next.js guidance (Server Components by default, streaming, `next/image`) applies **only if** the hosted team plane (production-architecture S2) is built. The offline client should stay vanilla: it is 66 KB of JS and must work from a file.
- `fileflow serve` does not gzip; fine on localhost, add `GZipMiddleware` before putting it behind a network.

## 7. Run the audit

```bash
cd tests/browser && npm install          # puppeteer-core, axe-core, @sparticuz/chromium
fileflow serve --root /tmp/fileflow-demo --port 8090 --no-token &    # python scripts/make_demo.py first
BASE_URL=http://127.0.0.1:8090 node audit.mjs            # exit 1 on any failure
BROWSER_EXE=/usr/bin/google-chrome node audit.mjs        # use a system browser instead
```
The bundled Chromium is built for AWS Lambda; in a plain container it needs its libraries extracted (see `tests/browser/audit.mjs` header).

## 8. Mutation evidence (the audit is only trustworthy if it fails when it should)

Seven UI mutations (arrows removed, Escape removed, original init-order bug, light `--text-3` lowered, drop zone never disabled, CDN font restored, global focus ring removed) and nine static ones (lowered tokens, px font, hex colour, magic z-index, Google link, `aria-live`, skip link removed, low-contrast warning) were applied. **Final tally: every mutation is caught by the check written for it.** Three honest corrections along the way:
1. "Tree arrows removed" first **survived** — the test only asked "is focus still on a tree item?", which is true when arrows do nothing. It now asserts focus lands on the *correct row* for ↓ ↑ Home End.
2. The audit flaked ~1 run in 2 (axe read a half-switched theme: dark text on a light background, 1.02:1). Fixed by waiting for the theme to settle; 6/6 clean runs after.
3. The Node DOM shim returned a fabricated tree row and hid bug #1 for the whole project.

## 9. Open items

- The hero fills the first screen, so the tool itself starts below the fold. A product decision (landing vs. workbench), not an accessibility bug — left alone.
- Two axe "needs review" items remain for a human.
- Web preset picker, receipt panel, provenance labels in the offline client (roadmap R3) are not built; build them on these tokens and add their flows to `audit.mjs` first.
- Light/dark follows the system on first load; no explicit high-contrast theme beyond `forced-colors`.
