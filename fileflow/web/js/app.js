/* ============================================================
   fileflow — web client
   Mirrors the fileflow CLI logic (traverse, hidden/gitignore/
   ignore-pattern filtering, default/XML/JSON formatting, line
   numbers, separators) in the browser.
   ============================================================ */

/* ---------- Sample project ---------- */
const SAMPLE_PROJECT = [
  {
    name: "README.md",
    content:
      "# fileflow\n\nTurn a directory of files into a single prompt for LLMs.\n\n```bash\nfileflow src/ | llm -m opus -s 'refactor this'\n```\n",
  },
  {
    name: ".gitignore",
    content: "build/\n*.pyc\n__pycache__/\n.venv/\n",
  },
  {
    name: "fileflow",
    children: [
      {
        name: "__init__.py",
        content: '"""fileflow: turn a directory of files into a single prompt for LLMs."""\n\n__version__ = "0.1.0"\n',
      },
      {
        name: "cli.py",
        content:
          "import os\nfrom fnmatch import fnmatch\n\nimport click\n\n\ndef should_ignore(path, rules):\n    for rule in rules:\n        if fnmatch(os.path.basename(path), rule):\n            return True\n    return False\n\n\n@click.command()\n@click.argument(\"paths\", nargs=-1, type=click.Path(exists=True))\ndef cli(paths):\n    \"\"\"Output every file, each preceded by its path.\"\"\"\n    for path in paths:\n        ...\n",
      },
    ],
  },
  {
    name: "src",
    children: [
      {
        name: "app.py",
        content: 'def main():\n    """Entry point."""\n    return 1\n',
      },
      {
        name: "utils.py",
        content: 'VALUE = 42\n\ndef helper(x):\n    return x * VALUE\n',
      },
    ],
  },
  {
    name: "tests",
    children: [
      {
        name: "test_fileflow.py",
        content:
          "import pytest\nfrom click.testing import CliRunner\n\nfrom fileflow.cli import cli\n\n\ndef test_directory_traversal(tmpdir):\n    runner = CliRunner()\n    result = runner.invoke(cli, [str(tmpdir)])\n    assert result.exit_code == 0\n",
      },
    ],
  },
  {
    name: "assets",
    children: [
      {
        name: "logo.svg",
        content: '<svg viewBox="0 0 24 24" fill="none"><path d="M14 2H6v20h12V8z"/></svg>\n',
      },
    ],
  },
  // Hidden + gitignored entries to exercise the filters.
  { name: ".env", content: "API_KEY=sk-...\n" },
  { name: ".hidden_dir", children: [{ name: "secret.txt", content: "secret\n" }] },
  { name: "build", children: [{ name: "output.txt", content: "ignored build artifact\n" }] },
  { name: "src/__pycache__", children: [{ name: "app.cpython-311.pyc", content: "\x00\x00\x00" }] },
];

/* ---------- Helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------- UI state ---------- */
const state = {
  tree: JSON.parse(JSON.stringify(SAMPLE_PROJECT)),
  selectedPath: null,
  collapsed: new Set(),
  format: "default",
  ignorePatterns: [],
  includePatterns: [],
  serverMode: false,
  serverAvailable: false,
  // Signature of the .files-to-prompt settings currently reflected in the
  // controls; lets a reload tell "the file really changed" from an echo.
  configSig: null,
};

const options = () => ({
  includeHidden: $("#opt-include-hidden").checked,
  ignoreGitignore: $("#opt-ignore-gitignore").checked,
  lineNumbers: $("#opt-line-numbers").checked,
  separators: $("#opt-separators").checked,
  format: state.format,
  ignorePatterns: state.ignorePatterns,
  includePatterns: state.includePatterns,
  maxTokens: parseInt($("#opt-max-tokens").value, 10) || 0,
});

/* ---------- Recipe persistence (localStorage) ---------- */
const RECIPE_KEY = "fileflow-recipe";

function saveRecipe() {
  try {
    localStorage.setItem(
      RECIPE_KEY,
      JSON.stringify({
        format: state.format,
        ignorePatterns: state.ignorePatterns,
        includePatterns: state.includePatterns,
        includeHidden: $("#opt-include-hidden").checked,
        ignoreGitignore: $("#opt-ignore-gitignore").checked,
        lineNumbers: $("#opt-line-numbers").checked,
        separators: $("#opt-separators").checked,
        maxTokens: parseInt($("#opt-max-tokens").value, 10) || 0,
      })
    );
  } catch {
    /* storage may be unavailable (private mode) — ignore */
  }
  // In live-server mode, also persist the recipe to the project's
  // .files-to-prompt config file so the CLI and server share it.
  if (state.serverMode) {
    const cfg = recipeToServerConfig();
    const sig = configSignature(cfg);
    serverSaveConfig(cfg)
      .then(() => {
        // The file now holds exactly this, so the change notification that
        // follows is just our own save echoing back: not a reload.
        state.configSig = sig;
      })
      .catch(() => {});
  }
}

function applyRecipe() {
  let recipe = null;
  try {
    recipe = JSON.parse(localStorage.getItem(RECIPE_KEY) || "null");
  } catch {
    recipe = null;
  }
  if (!recipe) return;
  if (["default", "xml", "json"].includes(recipe.format)) {
    state.format = recipe.format;
    const seg = $("#opt-format");
    $$("button", seg).forEach((b) =>
      b.setAttribute("aria-checked", b.dataset.format === recipe.format ? "true" : "false")
    );
    seg.dataset.active = String(["default", "xml", "json"].indexOf(recipe.format));
  }
  if (Array.isArray(recipe.ignorePatterns)) state.ignorePatterns = recipe.ignorePatterns;
  if (Array.isArray(recipe.includePatterns)) state.includePatterns = recipe.includePatterns;
  const setBool = (id, val) => {
    if (typeof val === "boolean") $("#" + id).checked = val;
  };
  setBool("opt-include-hidden", recipe.includeHidden);
  setBool("opt-ignore-gitignore", recipe.ignoreGitignore);
  setBool("opt-line-numbers", recipe.lineNumbers);
  setBool("opt-separators", recipe.separators);
  if (recipe.maxTokens) $("#opt-max-tokens").value = String(recipe.maxTokens);
  renderPatternChips();
}

/* ---------- Server-mode helpers (optional `fileflow serve`) ---------- */
const hasFetch = typeof fetch !== "undefined";

function serverQuery() {
  const o = options();
  // No explicit root: the server applies its configured project root
  // (fileflow serve --root), so the client always follows the project.
  const params = new URLSearchParams();
  params.set("format", o.format);
  params.set("budget", String(o.maxTokens));
  params.set("include_hidden", String(o.includeHidden));
  params.set("ignore_gitignore", String(o.ignoreGitignore));
  params.set("line_numbers", String(o.lineNumbers));
  params.set("separators", String(o.separators));
  if (o.ignorePatterns.length) params.set("ignore_patterns", o.ignorePatterns.join(","));
  if (o.includePatterns.length) params.set("include_patterns", o.includePatterns.join(","));
  return params;
}

async function serverAvailable() {
  if (!hasFetch) return false;
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

async function serverFetchPrompt() {
  const res = await fetch("/api/prompt?" + serverQuery().toString());
  if (!res.ok) throw new Error("server prompt failed: " + res.status);
  return res.json();
}

async function serverFetchTree() {
  const q = serverQuery();
  const res = await fetch("/api/tree?" + q.toString());
  if (!res.ok) throw new Error("server tree failed: " + res.status);
  return res.json();
}

async function serverFetchFile(path) {
  const res = await fetch("/api/file?" + new URLSearchParams({ path }).toString());
  if (!res.ok) {
    const err = new Error("server file failed: " + res.status);
    err.status = res.status;
    try {
      err.detail = (await res.json()).detail;
    } catch {
      /* no JSON body */
    }
    throw err;
  }
  return res.json();
}

async function serverFetchConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail || "";
    } catch {
      /* non-JSON error body */
    }
    const err = new Error(detail || "server config failed: " + res.status);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function serverSaveConfig(cfg) {
  const res = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!res.ok) throw new Error("server config save failed: " + res.status);
  return res.json();
}

/** Map the UI recipe (state + controls) onto the .files-to-prompt schema. */
function recipeToServerConfig() {
  return {
    output: {
      format: state.format,
      separators: $("#opt-separators").checked,
      line_numbers: $("#opt-line-numbers").checked,
      max_tokens: parseInt($("#opt-max-tokens").value, 10) || 0,
    },
    ignore: {
      gitignore: $("#opt-ignore-gitignore").checked === false, // stored "ignore gitignore" inverts
      hidden: $("#opt-include-hidden").checked,
    },
    exclude: { patterns: state.ignorePatterns },
    // Sent back as loaded: saving from the UI must never blank a hand-written [include] list.
    include: { patterns: state.includePatterns },
  };
}

/** Apply a loaded .files-to-prompt config to the UI controls. */
function applyServerConfig(cfg) {
  const out = cfg.output || {};
  if (["default", "xml", "json"].includes(out.format)) {
    state.format = out.format;
    const seg = $("#opt-format");
    $$("button", seg).forEach((b) =>
      b.setAttribute("aria-checked", b.dataset.format === out.format ? "true" : "false")
    );
    seg.dataset.active = String(["default", "xml", "json"].indexOf(out.format));
  }
  const setBool = (id, val) => {
    if (typeof val === "boolean") $("#" + id).checked = val;
  };
  setBool("opt-separators", out.separators);
  setBool("opt-line-numbers", out.line_numbers);
  if (Number.isInteger(out.max_tokens) && out.max_tokens >= 0) setTokenBudget(out.max_tokens);
  const ig = cfg.ignore || {};
  setBool("opt-ignore-gitignore", ig.gitignore === false); // .files-to-prompt stores "gitignore=true" => don't ignore
  setBool("opt-include-hidden", ig.hidden);
  const excl = (cfg.exclude && cfg.exclude.patterns) || [];
  if (Array.isArray(excl)) state.ignorePatterns = excl.slice();
  const incl = (cfg.include && cfg.include.patterns) || [];
  state.includePatterns = Array.isArray(incl) ? incl.filter((p) => typeof p === "string") : [];
  renderPatternChips();
}

/** Select a token budget, adding an option for values the preset list lacks. */
function setTokenBudget(n) {
  const sel = $("#opt-max-tokens");
  const v = String(n);
  if (!Array.from(sel.options || []).some((o) => o.value === v)) {
    // A hand-edited .files-to-prompt can ask for e.g. 12000. Without this the
    // <select> would silently fall back to "Off" and disagree with the CLI.
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = `${n.toLocaleString("en-US")} tokens (from .files-to-prompt)`;
    sel.appendChild(opt);
  }
  sel.value = v;
}

/** The settings a .files-to-prompt controls, in a stable form for comparison. */
function configSignature(cfg) {
  const out = cfg.output || {};
  const ig = cfg.ignore || {};
  const ex = cfg.exclude && Array.isArray(cfg.exclude.patterns) ? cfg.exclude.patterns : [];
  const inc = cfg.include && Array.isArray(cfg.include.patterns) ? cfg.include.patterns : [];
  return JSON.stringify([
    out.format,
    out.separators,
    out.line_numbers,
    out.max_tokens || 0,
    ig.gitignore,
    ig.hidden,
    ex,
    inc,
  ]);
}

/**
 * Re-read .files-to-prompt and apply it if its effective settings changed.
 * Returns true if the controls were updated. A file that is currently invalid
 * (HTTP 422, e.g. mid-edit) leaves the user's settings untouched.
 */
async function reloadServerConfig() {
  let cfg;
  try {
    cfg = await serverFetchConfig();
  } catch (err) {
    if (err.status === 422) {
      console.warn("[fileflow] " + err.message);
      showToast(".files-to-prompt has a syntax error — keeping current settings");
    } else {
      showToast("Couldn't reload .files-to-prompt");
    }
    return false;
  }
  const sig = configSignature(cfg);
  if (sig === state.configSig) return false; // our own save echoing back, or a no-op edit
  applyServerConfig(cfg);
  state.configSig = sig;
  showToast(".files-to-prompt reloaded");
  return true;
}

/** Convert a server tree node ({type, size, children}) to the internal shape. */
function normalizeServerNode(node, base) {
  const rel = base ? base + "/" + node.name : node.name;
  if (node.type === "dir" || node.children) {
    return {
      name: node.name,
      type: "dir",
      children: (node.children || []).map((c) => normalizeServerNode(c, rel)),
    };
  }
  return { name: node.name, type: "file", content: null, size: node.size };
}

/* ---------- Tree helpers ---------- */
function findNode(nodes, parts, parent = null) {
  const [head, ...rest] = parts;
  const node = nodes.find((n) => n.name === head);
  if (!node) return { node: null, parent };
  if (rest.length === 0) return { node, parent };
  return findNode(node.children || [], rest, node);
}

function getSelectedNode() {
  if (!state.selectedPath) return null;
  return findNode(state.tree, state.selectedPath.split("/")).node;
}

/* ---------- Icons ---------- */
const ICON_FILE =
  '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
const ICON_DIR =
  '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
const ICON_CHEVRON =
  '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

/* ---------- Render the file tree ---------- */
async function renderTree() {
  const treeEl = $("#file-tree");
  // Re-rendering replaces every row; remember which one had keyboard focus so
  // the user is not thrown back to the top of the page after each selection.
  const focusPath = treeFocusPath(treeEl);
  if (state.serverMode) {
    try {
      const arr = await serverFetchTree();
      state.tree = arr.map((n) => normalizeServerNode(n, ""));
    } catch {
      state.tree = [];
    }
  }
  rebuildTreeDom(treeEl, focusPath);
}

function treeFocusPath(treeEl) {
  const active = document.activeElement;
  return active && treeEl.contains && treeEl.contains(active) && active.dataset ? active.dataset.path : null;
}

/* The one place that replaces the tree's DOM: keeps the roving tab stop and
   the keyboard focus, whichever path (user action or live reload) got us here. */
function rebuildTreeDom(treeEl, focusPath) {
  treeEl.innerHTML = "";
  buildNodeRows(state.tree, "", treeEl, 1);
  syncRoving(treeEl, focusPath || state.selectedPath);
  if (focusPath) {
    const again = visibleRows(treeEl).find((r) => r.dataset.path === focusPath);
    if (again) again.focus();
  }
}

/* ---------- Tree keyboard model (WAI-ARIA tree pattern) ----------
   One tab stop (roving tabindex); arrows move, Right/Left expand/collapse,
   Home/End jump, Enter/Space activate. */
function visibleRows(treeEl) {
  return $$('[role="treeitem"]', treeEl).filter((r) => !r.closest("[hidden]"));
}

function syncRoving(treeEl, preferPath) {
  const rows = visibleRows(treeEl);
  const target = rows.find((r) => r.dataset.path === preferPath) || rows[0];
  $$('[role="treeitem"]', treeEl).forEach((r) => r.setAttribute("tabindex", r === target ? "0" : "-1"));
}

function focusRow(treeEl, row) {
  if (!row) return;
  $$('[role="treeitem"]', treeEl).forEach((r) => r.setAttribute("tabindex", r === row ? "0" : "-1"));
  row.focus();
}

function onTreeKeydown(e) {
  const treeEl = $("#file-tree");
  const row = e.target.closest ? e.target.closest('[role="treeitem"]') : null;
  if (!row || !treeEl.contains(row)) return;
  const rows = visibleRows(treeEl);
  const i = rows.indexOf(row);
  const isDir = row.dataset.kind === "dir";
  const expanded = isDir && !state.collapsed.has(row.dataset.path);
  let handled = true;
  switch (e.key) {
    case "ArrowDown":
      focusRow(treeEl, rows[Math.min(i + 1, rows.length - 1)]);
      break;
    case "ArrowUp":
      focusRow(treeEl, rows[Math.max(i - 1, 0)]);
      break;
    case "Home":
      focusRow(treeEl, rows[0]);
      break;
    case "End":
      focusRow(treeEl, rows[rows.length - 1]);
      break;
    case "ArrowRight":
      if (isDir && !expanded) onRowClick(row.dataset.path, true);
      else if (isDir) focusRow(treeEl, rows[i + 1]);
      break;
    case "ArrowLeft":
      if (isDir && expanded) onRowClick(row.dataset.path, true);
      else {
        const group = row.parentElement;
        const parent = group && group.classList.contains("tree-children") ? group.previousElementSibling : null;
        focusRow(treeEl, parent);
      }
      break;
    case "Enter":
    case " ":
      onRowClick(row.dataset.path, isDir);
      break;
    default:
      handled = false;
  }
  if (handled) e.preventDefault();
}

function buildNodeRows(nodes, base, container, level = 1) {
  // Folders first, then files — matching the CLI's sorted walk feel.
  const dirs = nodes.filter((n) => n.children !== undefined);
  const files = nodes.filter((n) => n.content !== undefined);
  [...dirs, ...files].forEach((node) => {
    const path = base ? base + "/" + node.name : node.name;
    const isDir = node.children !== undefined;
    const isHidden = node.name.startsWith(".");
    const row = document.createElement("div");
    row.className = "tree-row" + (isDir ? "" : " tree-file");
    if (isHidden) row.classList.add("hidden-node");
    if (path === state.selectedPath) row.classList.add("selected");
    row.dataset.path = path;
    row.dataset.kind = isDir ? "dir" : "file";
    row.setAttribute("role", "treeitem");
    row.setAttribute("tabindex", "-1");
    row.setAttribute("aria-level", String(level));
    if (!isDir) row.setAttribute("aria-selected", path === state.selectedPath ? "true" : "false");
    if (isDir) row.setAttribute("aria-expanded", state.collapsed.has(path) ? "false" : "true");

    if (isDir) {
      const t = document.createElement("span");
      t.className = "tree-toggle";
      t.innerHTML = ICON_CHEVRON;
      row.appendChild(t);
    } else {
      row.appendChild(document.createElement("span"));
    }

    const icon = document.createElement("span");
    icon.className = "tree-icon";
    icon.innerHTML = isDir ? ICON_DIR : ICON_FILE;
    row.appendChild(icon);

    const label = document.createElement("span");
    label.textContent = node.name;
    row.appendChild(label);

    row.addEventListener("click", () => onRowClick(path, isDir));
    container.appendChild(row);

    if (isDir) {
      const childrenWrap = document.createElement("div");
      childrenWrap.className = "tree-children";
      childrenWrap.setAttribute("role", "group");
      const collapsed = state.collapsed.has(path);
      childrenWrap.hidden = collapsed;
      if (collapsed) row.classList.add("collapsed");
      buildNodeRows(node.children || [], path, childrenWrap, level + 1);
      container.appendChild(childrenWrap);
    }
  });
}

function onRowClick(path, isDir) {
  if (isDir) {
    if (state.collapsed.has(path)) state.collapsed.delete(path);
    else state.collapsed.add(path);
    renderTree();
    return;
  }
  selectFile(path);
}

async function selectFile(path) {
  state.selectedPath = path;
  await renderTree();
  const node = getSelectedNode();
  $("#editor-file").textContent = path;
  $("#empty-editor").hidden = true;
  const editor = $("#editor");
  editor.hidden = false;
  if (state.serverMode) {
    // Real file on the server: show contents, read-only.
    editor.readOnly = true;
    editor.title = "Real file on server — read-only";
    try {
      const data = await serverFetchFile(path);
      editor.value = data.content;
    } catch (err) {
      // Say why instead of showing an empty file: a blank editor reads as "this file is empty".
      editor.value = err && err.detail ? "— not shown: " + err.detail : "";
    }
    $("#delete-file-btn").disabled = true;
  } else {
    editor.readOnly = false;
    editor.title = "";
    editor.value = node ? node.content : "";
    $("#delete-file-btn").disabled = false;
  }
}

function clearSelection() {
  state.selectedPath = null;
  $("#empty-editor").hidden = false;
  $("#editor").hidden = true;
  $("#editor-file").textContent = "—";
  $("#delete-file-btn").disabled = true;
}

/* ---------- Output generation ---------- */

/* ---------- Feedback: what stayed out of the prompt, and one-file-dominates advice ---------- */
function clearFeedback() {
  const advice = $("#advice");
  advice.textContent = "";
  advice.dataset.key = "";
  $("#left-out").hidden = true;
}

// Paths are untrusted file names: text nodes only, never innerHTML.
function renderLeftOut(m) {
  const box = $("#left-out");
  const total = m.left_out_total || 0;
  if (!total) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $("#left-out-summary").textContent = `Left out of the prompt (${total})`;
  const body = $("#left-out-body");
  body.textContent = "";
  const groups = new Map();
  for (const e of m.left_out || []) {
    if (!groups.has(e.reason)) groups.set(e.reason, { label: e.label, items: [] });
    groups.get(e.reason).items.push(e);
  }
  for (const [reason, g] of groups) {
    const h = document.createElement("h3");
    h.textContent = `${(m.left_out_counts || {})[reason] || g.items.length} · ${g.label}`;
    body.appendChild(h);
    const ul = document.createElement("ul");
    for (const e of g.items) {
      const li = document.createElement("li");
      li.textContent = e.path + (e.partial ? "  (cut short)" : "");
      ul.appendChild(li);
    }
    body.appendChild(ul);
  }
  const hidden = total - (m.left_out || []).length;
  if (hidden > 0) {
    const p = document.createElement("p");
    p.className = "more";
    p.textContent = `…and ${hidden.toLocaleString()} more not listed here (the counts above are complete).`;
    body.appendChild(p);
  }
}

function addIgnorePattern(pattern) {
  if (!state.ignorePatterns.includes(pattern)) {
    state.ignorePatterns.push(pattern);
    renderPatternChips();
    saveRecipe();
  }
  generateOutput();
}

function renderAdvice(m) {
  const host = $("#advice");
  const d = m.dominant;
  const key = d ? `${d.path}|${d.percent}|${d.tokens}|${d.shared}` : "";
  if (host.dataset.key === key) return; // unchanged: do not make a screen reader say it again
  host.dataset.key = key;
  host.textContent = "";
  if (!d) return;
  const p = document.createElement("p");
  p.textContent =
    `${d.path} is ${d.percent}% of this prompt: about ${d.tokens.toLocaleString()} of ` +
    `${d.total.toLocaleString()} tokens${m.tokenizer === "heuristic" ? " (estimate)" : ""}. ` +
    "A lockfile, log or data dump like this usually drowns the files that matter.";
  if (d.shared) {
    const note = document.createElement("span");
    note.className = "advice-note";
    note.textContent =
      `Ignore patterns match by name, so ${d.shared} other file${d.shared === 1 ? "" : "s"} ` +
      `called ${d.name} would also be left out.`;
    p.appendChild(note);
  }
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-sm";
  btn.textContent = "Leave it out";
  btn.addEventListener("click", () => {
    addIgnorePattern(d.pattern);
    showToast(`Leaving out ${d.name}`);
    $("#output-pre").focus(); // this button is about to be replaced; keep focus somewhere sensible
  });
  host.appendChild(p);
  host.appendChild(btn);
}

/* Ignore patterns are compared with file and folder NAMES, never paths (same rule in the CLI and
   this page). A pattern containing "/" can never match, so say so and use the name. */
function normalizePatternInput(raw) {
  if (!raw.includes("/")) return { pattern: raw, note: "" };
  const name = raw.replace(/\/+$/, "").split("/").pop() || raw;
  return {
    pattern: name,
    note: `Patterns match file and folder names, not paths — added "${name}" (applies in every folder).`,
  };
}

function setPatternHint(text) {
  let hint = $("#pattern-hint");
  if (!hint) return;
  hint.textContent = text;
}

let genTimer = null;

function generateOutput() {
  const pre = $("#output-pre");
  const empty = $("#output-empty");
  pre.classList.add("skeleton");
  pre.textContent = "";
  empty.hidden = true;

  clearTimeout(genTimer);
  genTimer = setTimeout(async () => {
    if (state.serverMode) {
      try {
        const data = await serverFetchPrompt();
        pre.textContent = data.text;
        pre.classList.remove("skeleton");
        empty.hidden = true;
        const m = data.meta || {};
        const secretsNote = describeSecrets(m.secrets);
        $("#output-meta").textContent =
          `${m.files ?? 0} files · ${m.chars ?? 0} chars · ` +
          `${formatTokens(m.tokens, m.tokenizer)} · ${m.lines ?? 0} lines` +
          (secretsNote ? ` · ${secretsNote}` : "");
        $("#output-meta").classList.toggle("trimmed", Boolean(secretsNote));
        $("#trim-note").hidden = true;
        renderLeftOut(m);
        renderAdvice(m);
      } catch (err) {
        pre.classList.remove("skeleton");
        pre.textContent = "";
        empty.hidden = false;
        $("#output-meta").textContent = "server unavailable";
        clearFeedback();
      }
      return;
    }

    clearFeedback(); // the offline sample has no walk to report on
    const opts = options();
    const collected = collectFiles(state.tree, opts);
    if (collected.length === 0) {
      pre.classList.remove("skeleton");
      pre.textContent = "";
      empty.hidden = false;
      $("#trim-note").hidden = true;
      $("#output-meta").textContent = "0 files";
      $("#output-meta").classList.remove("trimmed");
      return;
    }

    const { files, truncated, dropped } = applyTokenBudget(collected, opts.maxTokens);
    const { displayed, omittedDisplay } = applyDisplayLimit(files, opts);
    const text = await streamPrompt(pre, displayed, opts);
    pre.classList.remove("skeleton");
    empty.hidden = true;

    const chars = text.length;
    const lines = text.split("\n").length;
    const tokens = Math.round(chars / 4);
    let meta = `${displayed.length} files · ${chars.toLocaleString()} chars · ${formatTokens(tokens)} · ${lines} lines`;
    if (omittedDisplay > 0) meta += ` · ${omittedDisplay} hidden in preview`;
    const trimmedAny = truncated + dropped > 0 || omittedDisplay > 0;
    if (trimmedAny) {
      $("#output-meta").classList.add("trimmed");
      const notes = [];
      if (truncated + dropped > 0) {
        notes.push(
          `Token budget ${opts.maxTokens.toLocaleString()}: ` +
            `${truncated} truncated, ${dropped} dropped.`
        );
      }
      if (omittedDisplay > 0) {
        notes.push(
          `${omittedDisplay} more files hidden from preview to keep the UI fast. ` +
            `Run "fileflow serve" to work with the full project.`
        );
      }
      $("#trim-note").textContent = notes.join(" ");
      $("#trim-note").hidden = false;
    } else {
      $("#output-meta").classList.remove("trimmed");
      $("#trim-note").hidden = true;
    }
    $("#output-meta").textContent = meta;
  }, 260);
}

/* Keep the preview render responsive and bounded (A6): stream the output into
   the DOM in chunks and cap how much we display at once. */
const DISPLAY_MAX_FILES = 1000;
const DISPLAY_MAX_CHARS = 3_000_000;
const STREAM_CHUNK = 60;

function applyDisplayLimit(files, opts) {
  let displayed = files;
  let omitted = 0;
  if (files.length > DISPLAY_MAX_FILES || estimatePromptChars(files, opts) > DISPLAY_MAX_CHARS) {
    const kept = [];
    let budget = DISPLAY_MAX_CHARS;
    for (const f of files) {
      if (kept.length >= DISPLAY_MAX_FILES) break;
      if (kept.length > 0 && budget - f.content.length < 0) break;
      kept.push(f);
      budget -= f.content.length;
    }
    omitted = files.length - kept.length;
    displayed = kept;
  }
  return { displayed, omittedDisplay: omitted };
}

async function streamPrompt(pre, files, opts) {
  pre.textContent = "";
  if (opts.format === "json") {
    // JSON is a single atomic document; stream it as one assignment.
    const text = buildPrompt(files, opts);
    pre.textContent = text;
    return text;
  }
  const sep = blockSeparator(opts.format);
  if (opts.format === "xml") {
    pre.textContent = "<documents>";
    for (let i = 0; i < files.length; i++) {
      pre.textContent += sep + renderBlock(files[i], i + 1, opts);
      if ((i + 1) % STREAM_CHUNK === 0) await new Promise((r) => setTimeout(r, 0));
    }
    pre.textContent += sep + "</documents>";
    return pre.textContent;
  }
  for (let i = 0; i < files.length; i++) {
    pre.textContent += (i === 0 ? "" : sep) + renderBlock(files[i], i + 1, opts);
    if ((i + 1) % STREAM_CHUNK === 0) await new Promise((r) => setTimeout(r, 0));
  }
  return pre.textContent;
}

/* ---------- Options wiring ---------- */
function bindOptions() {
  const regen = () => {
    generateOutput();
    saveRecipe();
  };
  ["opt-include-hidden", "opt-ignore-gitignore", "opt-line-numbers", "opt-separators"].forEach(
    (id) => {
      $("#" + id).addEventListener("change", regen);
    }
  );

  $("#opt-max-tokens").addEventListener("change", regen);

  const seg = $("#opt-format");
  $$("button", seg).forEach((btn) => {
    btn.addEventListener("click", () => {
      state.format = btn.dataset.format;
      $$("button", seg).forEach((b) =>
        b.setAttribute("aria-checked", b === btn ? "true" : "false")
      );
      const idx = ["default", "xml", "json"].indexOf(state.format);
      seg.dataset.active = String(idx);
      generateOutput();
      saveRecipe();
    });
  });
  seg.dataset.active = "0";

  // Radio-group keyboard model: one tab stop (the checked option), arrows move
  // and select. A MutationObserver keeps the tab stop right however the
  // selection changed (click, saved recipe, server config).
  const buttons = $$("button", seg);
  const syncTabStop = () =>
    buttons.forEach((b) => b.setAttribute("tabindex", b.getAttribute("aria-checked") === "true" ? "0" : "-1"));
  syncTabStop();
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(syncTabStop).observe(seg, { subtree: true, attributes: true, attributeFilter: ["aria-checked"] });
  }
  seg.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const cur = buttons.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const next = buttons[(Math.max(cur, 0) + step + buttons.length) % buttons.length];
    next.click();
    next.focus();
  });
}

function renderPatternChips() {
  const wrap = $("#patterns-wrap");
  $$(".chip", wrap).forEach((c) => c.remove());
  state.ignorePatterns.forEach((p) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML =
      escapeHtml(p) +
      `<button type="button" aria-label="Remove ${escapeHtml(p)}">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.ignorePatterns = state.ignorePatterns.filter((x) => x !== p);
      renderPatternChips();
      generateOutput();
      saveRecipe();
    });
    wrap.insertBefore(chip, $("#opt-patterns"));
  });
}

function bindPatterns() {
  const input = $("#opt-patterns");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      e.preventDefault();
      const norm = normalizePatternInput(input.value.trim());
      const val = norm.pattern;
      setPatternHint(norm.note);
      if (norm.note) showToast(norm.note);
      if (!state.ignorePatterns.includes(val)) {
        state.ignorePatterns.push(val);
        renderPatternChips();
        generateOutput();
        saveRecipe();
      }
      input.value = "";
    }
    if (e.key === "Backspace" && input.value === "" && state.ignorePatterns.length) {
      state.ignorePatterns.pop();
      renderPatternChips();
      generateOutput();
      saveRecipe();
    }
  });
}

/* ---------- Editor ---------- */
function bindEditor() {
  const editor = $("#editor");
  editor.addEventListener("input", () => {
    const node = getSelectedNode();
    if (node) {
      node.content = editor.value;
      scheduleQuietRegen();
    }
  });
}

let quietTimer = null;
function scheduleQuietRegen() {
  clearTimeout(quietTimer);
  quietTimer = setTimeout(generateOutput, 350);
}

/* ---------- Add / delete ---------- */
/* Dialog behaviour (WCAG 2.4.3 / 2.1.2): focus moves in, Tab stays inside,
   Escape closes, the page behind is inert, focus returns to the opener. */
let modalOpener = null;
const BACKGROUND = ["header.site-header", "main", "footer.site-footer"];

function setBackgroundInert(on) {
  BACKGROUND.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.inert = on;
  });
}

function clearPathError() {
  const input = $("#new-path");
  input.removeAttribute("aria-invalid");
  $("#new-path-error").hidden = true;
}

function openModal() {
  const modal = $("#add-modal");
  modalOpener = document.activeElement;
  modal.hidden = false;
  setBackgroundInert(true);
  $("#new-path").value = "";
  $("#new-content").value = "";
  clearPathError();
  $("#new-path").focus();
}

function closeModal() {
  $("#add-modal").hidden = true;
  setBackgroundInert(false);
  if (modalOpener && modalOpener.isConnected && modalOpener.focus) modalOpener.focus();
  modalOpener = null;
}

function onModalKeydown(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    closeModal();
    return;
  }
  if (e.key !== "Tab") return;
  const items = $$("button, input, textarea, select, a[href]", $("#add-modal .modal")).filter(
    (el) => !el.disabled && !el.hidden
  );
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

function addFileToTree(path, content) {
  const parts = path.split("/").filter(Boolean);
  if (!parts.length) return;
  const name = parts.pop();
  let nodes = state.tree;
  for (const part of parts) {
    let dir = nodes.find((n) => n.children !== undefined && n.name === part);
    if (!dir) {
      dir = { name: part, children: [] };
      nodes.push(dir);
    }
    nodes = dir.children;
  }
  if (nodes.some((n) => n.name === name)) {
    const { node } = findNode(state.tree, [...parts, name]);
    if (node) node.content = content;
  } else {
    nodes.push({ name, content });
  }
}

function bindAddDelete() {
  $("#add-file-btn").addEventListener("click", openModal);
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#add-modal").addEventListener("click", (e) => {
    if (e.target.id === "add-modal") closeModal();
  });
  $("#add-modal").addEventListener("keydown", onModalKeydown);
  $("#new-path").addEventListener("input", clearPathError);
  $("#add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const path = $("#new-path").value.trim();
    if (!path) {
      // Say what is wrong, in text, next to the field, and put focus there.
      $("#new-path").setAttribute("aria-invalid", "true");
      $("#new-path-error").hidden = false;
      $("#new-path").focus();
      return;
    }
    addFileToTree(path, $("#new-content").value);
    closeModal();
    renderTree();
    state.collapsed.clear();
    selectFile(path);
    generateOutput();
    showToast("Added " + path);
  });

  const del = $("#delete-file-btn");
  del.disabled = true;
  del.addEventListener("click", () => {
    if (!state.selectedPath) return;
    const deletedPath = state.selectedPath;
    const parts = deletedPath.split("/");
    const name = parts.pop();
    // parts now holds the path of the parent directory.
    const chain = findNode(state.tree, parts);
    const list = chain.parent ? chain.parent.children : state.tree;
    const idx = list.findIndex((n) => n.name === name);
    if (idx >= 0) list.splice(idx, 1);
    renderTree();
    clearSelection();
    generateOutput();
    showToast("Deleted " + deletedPath);
  });
}

/* ---------- Copy / download ---------- */
function bindOutputActions() {
  const copy = $("#copy-btn");
  copy.addEventListener("click", async () => {
    const text = $("#output-pre").textContent;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    copy.classList.add("success");
    $(".btn-label", copy).textContent = "Copied";
    setTimeout(() => {
      copy.classList.remove("success");
      $(".btn-label", copy).textContent = "Copy";
    }, 1600);
    showToast("Prompt copied to clipboard");
  });

  $("#cli-btn").addEventListener("click", async () => {
    // Rebuild the paths list from the current tree's top-level nodes.
    const paths = state.tree
      .filter((n) => !n.name.startsWith(".") || options().includeHidden)
      .map((n) => n.name);
    const cmd = buildCliCommand(paths, options());
    try {
      await navigator.clipboard.writeText(cmd);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = cmd;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    showToast("CLI command copied");
  });

  $("#download-btn").addEventListener("click", () => {
    const text = $("#output-pre").textContent;
    if (!text) return;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "fileflow-prompt.txt";
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

/* ---------- Theme ---------- */
function initTheme() {
  const html = document.documentElement;
  const saved = localStorage.getItem("fileflow-theme");
  if (saved) {
    html.dataset.theme = saved;
  } else {
    html.dataset.theme = window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }
  $("#theme-toggle").addEventListener("click", () => {
    const next = html.dataset.theme === "dark" ? "light" : "dark";
    html.dataset.theme = next;
    localStorage.setItem("fileflow-theme", next);
  });
}

/* ---------- Toast ---------- */
let toastTimer = null;
function showToast(msg) {
  const toast = $("#toast");
  toast.textContent = msg;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

/* ---------- Real-file import (drag & drop / folder picker) ---------- */
function addImportedFiles(fileObjs) {
  if (!fileObjs || fileObjs.length === 0) {
    showToast("Nothing to import");
    return;
  }
  fileObjs.forEach((f) => addFileToTree(f.path, f.content));
  state.collapsed.clear();
  renderTree();
  generateOutput();
  showToast(`Imported ${fileObjs.length} file${fileObjs.length === 1 ? "" : "s"}`);
}

function isBinaryText(text) {
  return String(text).includes("\u0000");
}

/** Recursively read a webkitGetAsEntry entry tree into {path, content}[]. */
function readEntryPromise(entry, base) {
  return new Promise((resolve) => {
    const out = [];
    const rel = base ? base + entry.name : entry.name;
    if (entry.isFile) {
      entry.file(
        (file) => {
          file
            .text()
            .then((text) => {
              if (!isBinaryText(text)) out.push({ path: rel, content: text });
              resolve(out);
            })
            .catch(() => resolve(out));
        },
        () => resolve(out)
      );
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const gather = () => {
        reader.readEntries(
          async (entries) => {
            if (entries.length === 0) return resolve(out);
            const results = await Promise.all(
              entries.map((e) => readEntryPromise(e, rel + "/"))
            );
            for (const r of results) out.push(...r);
            gather();
          },
          () => resolve(out)
        );
      };
      gather();
    } else {
      resolve(out);
    }
  });
}

/** Recursively read a File System Access API directory handle. */
async function readDirHandle(handle, base) {
  const out = [];
  for await (const entry of handle.values()) {
    const rel = base + entry.name;
    if (entry.kind === "file") {
      const file = await entry.getFile();
      const text = await file.text();
      if (!isBinaryText(text)) out.push({ path: rel, content: text });
    } else if (entry.kind === "directory") {
      const sub = await readDirHandle(entry, rel + "/");
      out.push(...sub);
    }
  }
  return out;
}

async function readWebkitInput(fileList) {
  const results = await Promise.all(
    [...fileList].map(async (f) => {
      if (!f.webkitRelativePath) return null;
      const text = await f.text();
      if (isBinaryText(text)) return null;
      return { path: f.webkitRelativePath, content: text };
    })
  );
  return results.filter(Boolean);
}

async function readPlainFiles(fileList) {
  const results = await Promise.all(
    [...fileList].map(async (f) => {
      const text = await f.text();
      if (isBinaryText(text)) return null;
      return { path: f.webkitRelativePath || f.name, content: text };
    })
  );
  return results.filter(Boolean);
}

function bindImport() {
  const importBtn = $("#import-btn");
  const zone = $("#drop-zone");
  const input = $("#import-input");

  importBtn.addEventListener("click", async () => {
    if (window.showDirectoryPicker) {
      try {
        const handle = await window.showDirectoryPicker({ mode: "read" });
        const files = await readDirHandle(handle, "");
        addImportedFiles(files);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        input.click(); // fallback
      }
    } else {
      input.click();
    }
  });

  input.addEventListener("change", async () => {
    const files = await readWebkitInput(input.files);
    input.value = "";
    addImportedFiles(files);
  });

  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("drag-over");
    })
  );
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    if (zone.getAttribute("aria-disabled") === "true") return; // server mode: the project is the source
    const items = e.dataTransfer && e.dataTransfer.items;
    if (items && items.length && items[0].webkitGetAsEntry) {
      const entries = [...items]
        .map((i) => i.webkitGetAsEntry())
        .filter(Boolean);
      Promise.all(entries.map((ent) => readEntryPromise(ent, ""))).then(
        (groups) => addImportedFiles(groups.flat())
      );
    } else if (e.dataTransfer && e.dataTransfer.files.length) {
      readPlainFiles(e.dataTransfer.files).then(addImportedFiles);
    }
  });

  // Clicking the zone opens the same picker as the Import button.
  zone.addEventListener("click", () => {
    if (zone.getAttribute("aria-disabled") !== "true") importBtn.click();
  });
  // role="button" promises Enter and Space.
  zone.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && zone.getAttribute("aria-disabled") !== "true") {
      e.preventDefault();
      importBtn.click();
    }
  });
}

/* ---------- Server-mode UI ---------- */
function setServerModeUI(on) {
  ["add-file-btn", "import-btn"].forEach((id) => {
    const el = $("#" + id);
    if (el) el.disabled = on;
  });
  const zone = $("#drop-zone");
  if (zone) {
    // Disabled for everyone, not just mouse users: out of the tab order,
    // announced as disabled, ignored by click/Enter.
    zone.setAttribute("aria-disabled", on ? "true" : "false");
    zone.setAttribute("tabindex", on ? "-1" : "0");
  }
}

/* ---------- Live reload via /api/watch (watch mode) ---------- */
const watch = { ws: null, timer: null, attempts: 0, enabled: false, hasConnected: false, lastHash: null };

function watchUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/watch?poll=1.5`;
}

function scheduleWatchReconnect() {
  if (!watch.enabled) return;
  clearTimeout(watch.timer);
  // Exponential-ish backoff, capped at 5s.
  const delay = Math.min(5000, 500 * Math.pow(2, watch.attempts));
  watch.timer = setTimeout(connectWatch, delay);
}

let refreshChain = Promise.resolve();

/**
 * Re-sync with the server. Calls are serialised so a slow earlier refresh can
 * never land after (and overwrite) a newer one. With `reloadConfig`, the
 * project's .files-to-prompt is re-read first, because the tree and prompt
 * requests are built from the controls it drives.
 */
function refreshFromServer(opts = {}) {
  refreshChain = refreshChain
    .then(() => doRefreshFromServer(opts))
    .catch((err) => console.error("[fileflow] live refresh failed:", err));
  return refreshChain;
}

async function doRefreshFromServer({ reloadConfig = false } = {}) {
  if (!state.serverMode) return;
  if (reloadConfig) await reloadServerConfig();
  // Rebuild the tree (preserving selection if the path still exists) and
  // regenerate the prompt in one go.
  const sel = state.selectedPath;
  let tree;
  try {
    tree = (await serverFetchTree()).map((n) => normalizeServerNode(n, ""));
  } catch {
    return;
  }
  if (!state.serverMode) return; // switched back to the sample while we waited
  state.tree = tree;
  const parts = sel ? sel.split("/") : [];
  const stillThere = parts.length ? !!findNode(state.tree, parts).node : false;
  buildNodeRowsPreservingSelection();
  if (!stillThere) {
    clearSelection();
  } else {
    // The open file may be the one that changed: refresh the read-only editor.
    try {
      const data = await serverFetchFile(sel);
      const editor = $("#editor");
      if (editor.value !== data.content) editor.value = data.content;
    } catch {
      /* vanished between the two requests; the next refresh clears it */
    }
  }
  generateOutput();
}

function buildNodeRowsPreservingSelection() {
  const treeEl = $("#file-tree");
  rebuildTreeDom(treeEl, treeFocusPath(treeEl));
}

function connectWatch() {
  if (!state.serverMode || !window.WebSocket) return;
  watch.enabled = true;
  try {
    const ws = new WebSocket(watchUrl());
    watch.ws = ws;
    ws.onopen = () => {
      watch.attempts = 0;
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return; // ignore malformed frames
      }
      if (!msg) return;
      if (msg.ready) {
        // Sent once the server has its baseline. On the first connection the UI
        // was just loaded, so there is nothing to catch up on. After a
        // reconnect, a different hash means the project changed while we were
        // disconnected (laptop sleep, server restart): re-sync now.
        const missed = watch.hasConnected && msg.hash !== watch.lastHash;
        watch.hasConnected = true;
        watch.lastHash = msg.hash;
        if (missed) refreshFromServer({ reloadConfig: true });
        return;
      }
      if (msg.changed) {
        watch.lastHash = msg.hash;
        refreshFromServer({ reloadConfig: !!msg.config_changed });
      }
    };
    ws.onclose = () => {
      watch.ws = null;
      if (watch.enabled) {
        watch.attempts += 1;
        scheduleWatchReconnect();
      }
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    };
  } catch {
    scheduleWatchReconnect();
  }
}

function disconnectWatch() {
  watch.enabled = false;
  clearTimeout(watch.timer);
  if (watch.ws) {
    try {
      watch.ws.onclose = null;
      watch.ws.close();
    } catch {
      /* ignore */
    }
    watch.ws = null;
  }
  watch.attempts = 0;
  watch.hasConnected = false;
  watch.lastHash = null;
}

function resetToSample() {
  state.tree = JSON.parse(JSON.stringify(SAMPLE_PROJECT));
  state.collapsed.clear();
  state.ignorePatterns = [];
  $("#opt-patterns").value = "";
  renderPatternChips();
  renderTree();
  clearSelection();
  generateOutput();
  showToast("Sample project loaded");
}

/* ---------- Init ---------- */
async function init() {
  initTheme();
  bindOptions();
  bindPatterns();
  applyRecipe();
  renderPatternChips();
  bindEditor();
  bindAddDelete();
  bindOutputActions();
  bindImport();

  $("#load-sample").addEventListener("click", resetToSample);

  // Optional live-server mode: probe /api/health, enable the toggle if found.
  const available = await serverAvailable();
  if (available) {
    state.serverAvailable = true;
    const row = $("#server-option");
    if (row) row.hidden = false;
  }

  $("#opt-server").addEventListener("change", async () => {
    state.serverMode = $("#opt-server").checked;
    setServerModeUI(state.serverMode);
    if (state.serverMode) {
      // Load the project's .files-to-prompt config into the controls.
      let note = "";
      try {
        const cfg = await serverFetchConfig();
        applyServerConfig(cfg);
        state.configSig = configSignature(cfg);
      } catch (err) {
        // Keep the current recipe if the config can't be read; say why if the
        // file is merely broken (the live reload will pick up the fix).
        state.configSig = null;
        if (err.status === 422) note = ".files-to-prompt has a syntax error — using current settings";
      }
      await renderTree();
      const first = $("#file-tree .tree-file");
      if (first) selectFile(first.dataset.path);
      else clearSelection();
      generateOutput();
      connectWatch();
      showToast(
        "Connected to fileflow server (real project) — live reload on" + (note ? " · " + note : "")
      );
    } else {
      disconnectWatch();
      state.configSig = null;
      resetToSample();
    }
  });

  // Start with a file selected so the editor feels alive. The tree has to be
  // rendered first: looking for a row in an empty tree left new visitors with a
  // blank sidebar and editor (found in the first real-browser run).
  await renderTree();
  const first = $("#file-tree .tree-file");
  if (first) selectFile(first.dataset.path);
  $("#file-tree").addEventListener("keydown", onTreeKeydown);

  generateOutput();
}

document.addEventListener("DOMContentLoaded", init);
