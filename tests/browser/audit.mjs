// Opt-in real-browser audit of the web client (not part of `pytest`).
//
//   cd tests/browser && npm install      # puppeteer-core, axe-core, @sparticuz/chromium
//   fileflow serve --root /tmp/fileflow-demo --port 8090 --no-token &
//   BASE_URL=http://127.0.0.1:8090 node audit.mjs
//
// Use BROWSER_EXE=/path/to/chrome to use a system browser instead of the
// bundled one.  (@sparticuz/chromium targets AWS Lambda: in a plain container
// it also needs its bundled libraries, bin/al2023.tar.br, extracted somewhere
// on LD_LIBRARY_PATH — a system Chrome avoids that.)  Exit code 1 when any check fails.  What this does NOT prove:
// real screen-reader behaviour (NVDA/VoiceOver/TalkBack), real touch devices,
// Safari/Firefox rendering.  axe finds roughly a third of WCAG issues; the
// hand-written checks below cover keyboard, dialog and reflow.
import puppeteer from "puppeteer-core";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const BASE = process.env.BASE_URL || "http://127.0.0.1:8090";

let exe = process.env.BROWSER_EXE;
let args = ["--no-sandbox"];
if (!exe) {
  const { default: chromium } = await import("@sparticuz/chromium");
  exe = await chromium.executablePath();
  args = [...chromium.args, "--no-sandbox"];
}
const browser = await puppeteer.launch({ executablePath: exe, args, headless: "shell" });

const VIEWPORTS = [
  ["small-mobile", 320, 568], ["large-mobile", 430, 932], ["tablet", 768, 1024],
  ["laptop", 1280, 800], ["desktop", 1440, 900], ["large-desktop", 2560, 1440],
];
const failures = [];
const notes = [];
const fail = (area, msg) => failures.push(`${area}: ${msg}`);

async function open(w, h, opts = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, hasTouch: !!opts.touch, isMobile: !!opts.touch });
  if (opts.reducedMotion) await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  if (opts.theme) await page.evaluateOnNewDocument((t) => localStorage.setItem("fileflow-theme", t), opts.theme);
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(BASE, { waitUntil: "networkidle0" });
  page._errors = errors;
  return page;
}

// ---- 1. axe (WCAG 2.2 AA tags), both themes, tree populated -------------
// Run on the settled state (reduced motion => scroll-reveal elements are fully
// opaque).  Otherwise axe samples text mid-fade and reports contrast failures
// that exist only for a few hundred milliseconds.  The end state is checked
// separately below (`.reveal` must end at opacity 1).
for (const theme of ["dark", "light"]) {
  const page = await open(1280, 900, { reducedMotion: true });
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  // let the theme change settle: right after the switch, text and background are
  // computed from different themes for a frame and axe reports a 1.02:1 "failure"
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 150)))));
  await page.evaluate(axeSource);
  const res = await page.evaluate(async () =>
    // eslint-disable-next-line no-undef
    axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
  for (const v of res.violations)
    fail(`axe[${theme}]`, `${v.id} (${v.impact}) x${v.nodes.length}: ${v.nodes[0].target.join(" ")} — ${(v.nodes[0].any[0]?.message || "").replace(/Expected.*/, "").slice(0, 160)}`);
  notes.push(`axe[${theme}]: ${res.violations.length} violations, ${res.passes.length} rules passed, ${res.incomplete.length} need manual review`);
  if (page._errors.length) fail("console", page._errors.join(" | "));
  await page.close();
}

{
  // the animated page must also settle at full opacity once scrolled into view
  const page = await open(1280, 900);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await new Promise((r) => setTimeout(r, 1500));
  const faded = await page.evaluate(() => [...document.querySelectorAll(".reveal")].filter((e) => { const b = e.getBoundingClientRect(); return b.top < innerHeight && b.bottom > 0 && parseFloat(getComputedStyle(e).opacity) < 0.99; }).length);
  if (faded) fail("reveal", `${faded} visible .reveal elements are still translucent after 1.5s`);
  await page.close();
}

// ---- 2. reflow: no horizontal overflow, no clipped controls -------------
for (const [name, w, h] of VIEWPORTS) {
  const page = await open(w, h);
  const r = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const over = [];
    for (const el of document.querySelectorAll("body *")) {
      const b = el.getBoundingClientRect();
      if (!b.width || !b.height) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none" || el.closest("[hidden]")) continue;
      if (b.right > vw + 1 && !el.closest("pre, textarea, .tree, .output, [data-scroll]")) over.push(`${el.tagName.toLowerCase()}.${el.className}`.slice(0, 60));
    }
    return { sw: document.documentElement.scrollWidth, vw, over: [...new Set(over)].slice(0, 5) };
  });
  if (r.sw > r.vw) fail(`reflow[${name} ${w}px]`, `page scrolls horizontally (${r.sw} > ${r.vw})`);
  if (r.over.length) fail(`reflow[${name} ${w}px]`, `elements overflow viewport: ${r.over.join(", ")}`);
  await page.close();
}

// ---- 3. interactive target size (WCAG 2.5.8 min 24px; we aim 44 on touch)
{
  const page = await open(390, 844, { touch: true });
  const small = await page.evaluate(() => {
    const out = [];
    const sel = "button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=treeitem], [role=radio]";
    for (const el of document.querySelectorAll(sel)) {
      const b = el.getBoundingClientRect();
      if (!b.width || el.closest("[hidden]") || getComputedStyle(el).visibility === "hidden") continue;
      if (el.matches("input[type=checkbox]") ) continue; // visually replaced by .switch-track
      if (b.height < 44 || b.width < 44) out.push(`${el.tagName.toLowerCase()}#${el.id || el.className} ${Math.round(b.width)}x${Math.round(b.height)}`);
    }
    return out;
  });
  const tooSmall24 = small.filter((s) => { const [w, h] = s.split(" ").pop().split("x").map(Number); return w < 24 || h < 24; });
  if (tooSmall24.length) fail("target-size AA(24px)", tooSmall24.slice(0, 6).join("; "));
  const under44 = small.filter((s) => { const [w, h] = s.split(" ").pop().split("x").map(Number); return h < 44 && !(w >= 44 && h >= 24 && false); });
  if (under44.length) fail("target-size touch(44px)", `${under44.length} under 44px height, e.g. ${under44.slice(0, 4).join("; ")}`);
  await page.close();
}

// ---- 4. keyboard: skip link, tree, dialog -------------------------------
{
  const page = await open(1280, 900);
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement?.textContent?.trim().slice(0, 30) + "|" + document.activeElement?.getAttribute("href"));
  if (!/skip/i.test(first)) fail("keyboard", `first Tab stop is not a skip link (got "${first}")`);

  // focus visible on every tab stop (outline or box-shadow present)
  const noRing = [];
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const r = await page.evaluate(() => {
      const el = document.activeElement; if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const sibling = el.matches("input") ? getComputedStyle(el.nextElementSibling || el) : cs;
      const has = (c) => (c.outlineStyle !== "none" && parseFloat(c.outlineWidth) > 0) || (c.boxShadow && c.boxShadow !== "none");
      const wrap = el.closest(".chips-input"); // ring is drawn on the focus-within container
      const wrapOk = wrap ? getComputedStyle(wrap).outlineStyle !== "none" : false;
      return { id: `${el.tagName.toLowerCase()}#${el.id || el.className}`.slice(0, 50), ok: has(cs) || has(sibling) || wrapOk };
    });
    if (r && !r.ok) noRing.push(r.id);
  }
  if (noRing.length) fail("focus-visible", `no visible ring on: ${[...new Set(noRing)].join(", ")}`);

  // tree must be reachable and operable from the keyboard
  const treeStops = await page.evaluate(() => [...document.querySelectorAll("[role=treeitem]")].filter((e) => e.tabIndex >= 0).length);
  if (treeStops !== 1) fail("tree", `expected exactly 1 roving tab stop in the tree, found ${treeStops}`);
  await page.focus("[role=treeitem][tabindex='0']").catch(() => fail("tree", "no focusable treeitem"));
  const rowsInOrder = await page.evaluate(() => [...document.querySelectorAll("[role=treeitem]")].filter((r) => !r.closest("[hidden]")).map((r) => r.dataset.path));
  const focusedPath = () => page.evaluate(() => document.activeElement?.dataset?.path);
  const start = await focusedPath();
  const si = rowsInOrder.indexOf(start);
  await page.keyboard.press("ArrowDown");
  if ((await focusedPath()) !== rowsInOrder[Math.min(si + 1, rowsInOrder.length - 1)]) fail("tree", "ArrowDown does not move focus to the next row");
  await page.keyboard.press("End");
  if ((await focusedPath()) !== rowsInOrder[rowsInOrder.length - 1]) fail("tree", "End does not move focus to the last row");
  await page.keyboard.press("ArrowUp");
  if ((await focusedPath()) !== rowsInOrder[rowsInOrder.length - 2]) fail("tree", "ArrowUp does not move focus to the previous row");
  await page.keyboard.press("Home");
  if ((await focusedPath()) !== rowsInOrder[0]) fail("tree", "Home does not move focus to the first row");
  const hasExpanded = await page.evaluate(() => !!document.querySelector("[role=treeitem][aria-expanded]"));
  if (!hasExpanded) fail("tree", "folders expose no aria-expanded");
  // find a file row and activate it with Enter
  const picked = await page.evaluate(() => {
    const f = document.querySelector("[role=treeitem][data-kind=file]"); if (!f) return false; f.focus(); return f.dataset.path;
  });
  if (picked) {
    await page.keyboard.press("Enter");
    await new Promise((r) => setTimeout(r, 300));
    const sel = await page.evaluate(() => document.querySelector("[role=treeitem][aria-selected=true]")?.dataset.path);
    if (sel !== picked) fail("tree", `Enter did not select the focused file (${picked} → ${sel})`);
    const stillFocused = await page.evaluate(() => document.activeElement?.getAttribute("role"));
    if (stillFocused !== "treeitem") fail("tree", "focus lost from the tree after selecting (re-render drops focus)");
  } else fail("tree", "no file rows found in demo project");

  // dialog: focus in, trap, Escape, restore, background inert
  await page.focus("#add-file-btn");
  await page.keyboard.press("Enter");
  await new Promise((r) => setTimeout(r, 200));
  const inDialog = await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]"));
  if (!inDialog) fail("dialog", "focus did not move into the dialog on open");
  for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
  const trapped = await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]"));
  if (!trapped) fail("dialog", "Tab escapes the dialog (no focus trap)");
  const bgInert = await page.evaluate(() => { const m = document.querySelector("main"); return m ? (m.inert || m.getAttribute("aria-hidden") === "true") : false; });
  if (!bgInert) fail("dialog", "page behind the dialog is not inert");
  await page.keyboard.press("Escape");
  await new Promise((r) => setTimeout(r, 150));
  const closed = await page.evaluate(() => document.querySelector("#add-modal").hidden);
  if (!closed) fail("dialog", "Escape does not close the dialog");
  const restored = await page.evaluate(() => document.activeElement?.id);
  if (restored !== "add-file-btn") fail("dialog", `focus not restored to the opener (got #${restored})`);
  // empty path error must be announced, not silent
  await page.focus("#add-file-btn"); await page.keyboard.press("Enter");
  await new Promise((r) => setTimeout(r, 150));
  await page.keyboard.press("Tab"); await page.keyboard.press("Tab"); await page.keyboard.press("Tab"); await page.keyboard.press("Tab"); await page.keyboard.press("Tab");
  await page.evaluate(() => document.querySelector("#add-form button[type=submit]").click());
  const errState = await page.evaluate(() => { const i = document.querySelector("#new-path"); return { invalid: i.getAttribute("aria-invalid"), msg: !!document.querySelector("#new-path-error:not([hidden])") }; });
  if (errState.invalid !== "true" || !errState.msg) fail("form-errors", "submitting an empty path gives no visible/announced error");
  await page.close();
}

// ---- 5. output region must not re-announce the whole prompt -------------
{
  const page = await open(1280, 900);
  const live = await page.evaluate(() => { const p = document.querySelector("#output-pre"); return p.getAttribute("aria-live") || (p.closest("[aria-live]")?.getAttribute("aria-live")) || null; });
  if (live) fail("screen-reader", `#output-pre is a live region (aria-live=${live}); every keystroke re-announces the whole prompt`);
  const hasStatus = await page.evaluate(() => !!document.querySelector("#output-meta[role=status]"));
  if (!hasStatus) fail("screen-reader", "no concise status message (files / tokens) for regenerated output");
  await page.close();
}

// ---- 6. reduced motion + 200% text zoom ---------------------------------
{
  const page = await open(1280, 900, { reducedMotion: true });
  const anim = await page.evaluate(() => [...document.querySelectorAll("*")].filter((e) => { const c = getComputedStyle(e); return c.animationName !== "none" && parseFloat(c.animationDuration) > 0.05; }).length);
  if (anim) fail("reduced-motion", `${anim} elements still run >50ms animations`);
  await page.close();
  const z = await open(320, 640);
  await z.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const ov = await z.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  if (ov) fail("reflow[320px @200% text]", "horizontal scroll appears when text is enlarged (WCAG 1.4.4/1.4.10)");
  await z.close();
}


// ---- 7. privacy: nothing leaves the origin; fonts really load ------------
{
  const page = await browser.newPage();
  const foreign = [];
  page.on("request", (r) => { if (!r.url().startsWith(BASE) && !r.url().startsWith("data:") && !r.url().startsWith("blob:")) foreign.push(r.url()); });
  await page.goto(BASE, { waitUntil: "networkidle0" });
  if (foreign.length) fail("privacy", `page contacts third parties: ${[...new Set(foreign)].slice(0, 3).join(", ")}`);
  const fonts = await page.evaluate(async () => { await document.fonts.ready; return { inter: document.fonts.check("16px Inter"), mono: document.fonts.check("16px 'JetBrains Mono'"), loaded: [...document.fonts].filter((f) => f.status === "loaded").length }; });
  if (!fonts.loaded) fail("fonts", "no self-hosted font file was loaded");
  await page.close();
}

// ---- 8. server mode (real project): tree, warning state, keyboard --------
{
  const page = await open(1280, 900, { reducedMotion: true });
  const avail = await page.evaluate(() => !document.querySelector("#server-option")?.hidden);
  if (!avail) { notes.push("server mode: not available at BASE_URL (static host) — skipped"); }
  else {
    await page.evaluate(() => document.querySelector("#opt-server").click());
    await new Promise((r) => setTimeout(r, 1800));
    const st = await page.evaluate(() => ({ rows: document.querySelectorAll("[role=treeitem]").length, meta: document.querySelector("#output-meta").textContent, trimmed: document.querySelector("#output-meta").classList.contains("trimmed"), sel: document.querySelector("[role=treeitem][aria-selected=true]")?.dataset.path }));
    if (st.rows < 3) fail("server-mode", `tree has ${st.rows} rows after enabling server mode`);
    if (!st.sel) fail("server-mode", "no file selected after enabling server mode");
    notes.push(`server mode: ${st.rows} tree rows; meta="${st.meta.slice(0, 110)}"`);
    if (/secret/i.test(st.meta) && !st.trimmed) fail("server-mode", "secret warning shown without the warning state class");
    await page.evaluate(axeSource);
    const res = await page.evaluate(async () => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    for (const v of res.violations) fail("axe[server-mode]", `${v.id} x${v.nodes.length}: ${v.nodes[0].target.join(" ")}`);
    // folder expand/collapse by keyboard in the real tree
    const toggled = await page.evaluate(async () => {
      const dir = document.querySelector("[role=treeitem][data-kind=dir][aria-expanded=true]"); if (!dir) return "no-dir";
      dir.focus(); dir.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
      await new Promise((r) => setTimeout(r, 400));
      const again = document.querySelector(`[data-path="${CSS.escape(dir.dataset.path)}"]`);
      return again.getAttribute("aria-expanded") + "|" + (document.activeElement === again);
    });
    // live reload (watch mode) rebuilds the tree: keyboard focus must survive it
    const survived = await page.evaluate(async () => {
      const f = [...document.querySelectorAll("[role=treeitem][data-kind=file]")].find((r) => !r.closest("[hidden]")); f.focus(); const path = f.dataset.path;
      await refreshFromServer(); // what the file watcher triggers
      await new Promise((r) => setTimeout(r, 300));
      return document.activeElement?.dataset?.path === path;
    });
    if (!survived) fail("server-mode", "live reload drops keyboard focus from the tree");
    if (toggled !== "false|true") fail("server-mode", `ArrowLeft on an open folder should collapse it and keep focus (got ${toggled})`);
  }
  await page.close();
}

// ---- 9. role=button elements honour Enter/Space; disabled ones leave the tab order
{
  const page = await open(1280, 900, { reducedMotion: true });
  const probe = await page.evaluate(() => {
    const z = document.querySelector("#drop-zone"); let clicked = 0;
    document.querySelector("#import-btn").addEventListener("click", (e) => { clicked++; e.stopImmediatePropagation(); }, true);
    z.focus();
    for (const key of ["Enter", " "]) z.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    return clicked;
  });
  if (probe !== 2) fail("drop-zone", `Enter/Space should activate it (activated ${probe}/2 times)`);
  if (await page.evaluate(() => !!document.querySelector("#server-option") && !document.querySelector("#server-option").hidden)) {
    await page.evaluate(() => document.querySelector("#opt-server").click());
    await new Promise((r) => setTimeout(r, 800));
    const d = await page.evaluate(() => { const z = document.querySelector("#drop-zone"); return { aria: z.getAttribute("aria-disabled"), tab: z.tabIndex }; });
    if (d.aria !== "true" || d.tab !== -1) fail("drop-zone", `disabled in server mode must be aria-disabled and out of tab order (got ${JSON.stringify(d)})`);
  }
  await page.close();
}

// ---- 10. feedback: what stayed out, and the dominant-file advice (server mode) ------------
{
  const page = await open(1280, 900, { reducedMotion: true });
  const avail = await page.evaluate(() => !document.querySelector("#server-option")?.hidden);
  if (!avail) { notes.push("feedback: no server at BASE_URL — skipped"); }
  else {
    await page.evaluate(() => document.querySelector("#opt-server").click());
    await new Promise((r) => setTimeout(r, 2000));
    const st = await page.evaluate(() => ({
      advice: document.querySelector("#advice").textContent,
      hasBtn: !!document.querySelector("#advice button"),
      leftHidden: document.querySelector("#left-out").hidden,
      summary: document.querySelector("#left-out-summary").textContent,
    }));
    if (!/package-lock\.json is \d+% of this prompt/.test(st.advice)) fail("feedback", `no dominant-file advice for the demo lockfile (got "${st.advice.slice(0, 80)}")`);
    if (!st.hasBtn) fail("feedback", "advice has no 'Leave it out' button");
    if (st.leftHidden || !/Left out of the prompt \(\d+\)/.test(st.summary)) fail("feedback", `left-out list not shown (${st.summary})`);

    // open the list: untrusted names must render as TEXT, labels in plain words, nothing executes
    await page.evaluate(() => { document.querySelector("#left-out").open = true; });
    const body = await page.evaluate(() => ({
      text: document.querySelector("#left-out-body").textContent,
      imgs: document.querySelectorAll("#left-out-body img").length,
      xss: window.__xss === 1,
      codes: /\b(HIDDEN|GITIGNORED)\b/.test(document.querySelector("#left-out-body").textContent),
    }));
    if (body.imgs || body.xss) fail("feedback[security]", "a file NAME was interpreted as HTML in the left-out list");
    if (!body.text.includes("<img src=x onerror=window.__xss=1>")) fail("feedback", "hostile file name not shown literally as text");
    if (!/hidden \(name starts with a dot\)/.test(body.text) || body.codes) fail("feedback", "reasons must be shown in plain words, not codes");

    // axe on the opened state, both themes
    for (const theme of ["dark", "light"]) {
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 150)))));
      await page.evaluate(axeSource);
      const res = await page.evaluate(async () => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
      for (const v of res.violations) fail(`axe[feedback ${theme}]`, `${v.id} x${v.nodes.length}: ${v.nodes[0].target.join(" ")} — ${(v.nodes[0].any[0]?.message || "").replace(/Expected.*/, "").slice(0, 120)}`);
    }

    // keyboard: summary toggles with Enter; advice button reachable and it works
    await page.evaluate(() => { document.querySelector("#left-out").open = false; document.querySelector("#left-out-summary").focus(); });
    await page.keyboard.press("Enter");
    if (!(await page.evaluate(() => document.querySelector("#left-out").open))) fail("feedback[keyboard]", "Enter on the summary does not open the list");

    // a live region that is rewritten every regeneration makes a screen reader repeat itself
    const rewrites = await page.evaluate(async () => {
      let n = 0; const mo = new MutationObserver((l) => { n += l.length; }); mo.observe(document.querySelector("#advice"), { childList: true, characterData: true, subtree: true });
      await refreshFromServer(); await refreshFromServer(); await new Promise((r) => setTimeout(r, 700)); mo.disconnect(); return n;
    });
    if (rewrites) fail("feedback[screen-reader]", `the advice live region was rewritten ${rewrites}x while nothing changed`);

    // the one-click fix: applies the pattern, advice goes away, the file moves to the left-out list, focus is kept
    await page.evaluate(() => document.querySelector("#advice button").focus());
    await page.keyboard.press("Enter");
    await new Promise((r) => setTimeout(r, 1500));
    const fixed = await page.evaluate(() => ({
      advice: document.querySelector("#advice").textContent,
      chips: [...document.querySelectorAll(".chip")].map((c) => c.textContent.replace("×", "").trim()),
      listed: document.querySelector("#left-out-body").textContent.includes("package-lock.json"),
      reason: /matched an ignore pattern/.test(document.querySelector("#left-out-body").textContent),
      focus: document.activeElement?.id,
      tokens: document.querySelector("#output-meta").textContent,
    }));
    if (fixed.advice) fail("feedback", "advice still shown after 'Leave it out'");
    if (!fixed.chips.some((c) => c.includes("package-lock.json"))) fail("feedback", `pattern chip not added (${fixed.chips})`);
    if (!fixed.listed || !fixed.reason) fail("feedback", "the file did not move to the left-out list as 'matched an ignore pattern'");
    if (fixed.focus !== "output-pre") fail("feedback[focus]", `focus lost after the advice button was replaced (on #${fixed.focus})`);

    // the fix is not just cosmetic: it is written to .files-to-prompt, so the CLI and the team get it too
    const saved = await page.evaluate(async () => (await (await fetch("/api/config")).json()).exclude?.patterns || []);
    if (!saved.includes("package-lock.json")) fail("feedback[persistence]", `'Leave it out' was not saved to .files-to-prompt (exclude.patterns=${JSON.stringify(saved)})`);

    // reversible: removing the chip brings the file (and the advice) back; this also restores the demo's config
    await page.focus("#opt-patterns");
    await page.keyboard.press("Backspace");
    await new Promise((r) => setTimeout(r, 1500));
    const back = await page.evaluate(() => /package-lock\.json is \d+%/.test(document.querySelector("#advice").textContent));
    if (!back) fail("feedback", "removing the chip did not restore the file and the advice");
    const restored = await page.evaluate(async () => (await (await fetch("/api/config")).json()).exclude?.patterns || []);
    if (restored.includes("package-lock.json")) fail("feedback[persistence]", "removing the chip left the pattern in .files-to-prompt");

    // the chip box teaches the rule: a path-shaped pattern becomes its name, with an explanation
    await page.focus("#opt-patterns");
    await page.keyboard.type("dist/");
    await page.keyboard.press("Enter");
    await new Promise((r) => setTimeout(r, 1200));
    const hint = await page.evaluate(() => ({ chips: [...document.querySelectorAll(".chip")].map((c) => c.textContent.replace("×", "").trim()), hint: document.querySelector("#pattern-hint").textContent }));
    if (!hint.chips.includes("dist") || hint.chips.some((c) => c.includes("/"))) fail("patterns", `path-shaped pattern kept as-is (${hint.chips})`);
    if (!/names, not paths/.test(hint.hint)) fail("patterns", "no explanation shown for the corrected pattern");
    await page.focus("#opt-patterns");
    await page.keyboard.press("Backspace");   // restore config
    await new Promise((r) => setTimeout(r, 1200));
  }
  await page.close();
}

// ---- 11. reflow with the new elements visible ----------------------------------------------
for (const [name, w, h, zoom] of [["320px", 320, 640, false], ["320px @200% text", 320, 640, true], ["430px", 430, 932, false]]) {
  const page = await open(w, h, { reducedMotion: true });
  const avail = await page.evaluate(() => !document.querySelector("#server-option")?.hidden);
  if (!avail) { await page.close(); continue; }
  await page.evaluate(() => document.querySelector("#opt-server").click());
  await new Promise((r) => setTimeout(r, 1800));
  await page.evaluate((z) => { document.querySelector("#left-out").open = true; if (z) document.documentElement.style.fontSize = "200%"; }, zoom);
  await new Promise((r) => setTimeout(r, 300));
  const ov = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth }));
  if (ov.sw > ov.vw) fail(`reflow-feedback[${name}]`, `page scrolls horizontally with advice + left-out list visible (${ov.sw} > ${ov.vw})`);
  const small = await page.evaluate(() => [...document.querySelectorAll("#advice button, #left-out summary")].filter((e) => { const b = e.getBoundingClientRect(); return b.height < 44 || b.width < 24; }).map((e) => e.id || e.tagName));
  if (w < 500 && small.length) fail(`target-size-feedback[${name}]`, `under 44px: ${small.join(", ")}`);
  await page.close();
}

await browser.close();
for (const n of notes) console.log("note:", n);
if (failures.length) { console.log(`\nFAIL (${failures.length})`); for (const f of failures) console.log(" ✗", f); process.exit(1); }
console.log("\nPASS: all browser checks");
