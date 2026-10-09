/* ============================================================
   fileflow — core prompt-generation logic (browser port of the CLI)
   Pure functions, no DOM. Testable in Node via module.exports.
   ============================================================ */

function parseGitignore(content) {
  return String(content)
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
}

function globToRegExp(pattern) {
  let out = "";
  for (const ch of pattern) {
    if (ch === "*") out += ".*";
    else if (ch === "?") out += ".";
    else out += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp("^" + out + "$");
}

function matches(pattern, name) {
  try {
    return globToRegExp(pattern).test(name);
  } catch {
    return false;
  }
}

function matchesAny(rules, name, isDir) {
  for (const r of rules) {
    if (matches(r, name)) return true;
    if (isDir && matches(r, name + "/")) return true;
  }
  return false;
}

/* ---- [include] globs: `*` = any run (incl. "/"), `?` = one character, everything else literal,
   case-sensitive. Mirrors fileflow/walker.py exactly; the `u` flag makes `.`/`[\s\S]` match whole code
   points (as Python does). No `[...]` classes: fnmatch's differ between engines, so neither has them. ---- */
const _globCache = new Map();

function globRegex(pattern) {
  let re = _globCache.get(pattern);
  if (!re) {
    let out = "";
    for (const ch of pattern) {
      out += ch === "*" ? "[\\s\\S]*" : ch === "?" ? "[\\s\\S]" : ch.replace(/[\\^$.*+?()[\]{}|\/]/g, "\\$&");
    }
    re = new RegExp("^" + out + "$", "u");
    _globCache.set(pattern, re);
  }
  return re;
}

function globMatch(pattern, text) {
  return globRegex(pattern).test(text);
}

/** A pattern without "/" matches the file name; one with "/" matches the relative path. */
function includeMatch(rel, name, patterns) {
  return patterns.some((p) => globMatch(p, p.includes("/") ? rel : name));
}

/* ---- Gitignore semantics: negation, anchoring, dir-only ---- */

function normalizeGitignoreRule(pattern) {
  pattern = String(pattern).trim();
  if (!pattern || pattern.startsWith("#")) return null;
  let negated = false;
  if (pattern.startsWith("!")) {
    negated = true;
    pattern = pattern.slice(1).trimStart();
    if (!pattern) return null;
  }
  let dirOnly = false;
  if (pattern.endsWith("/")) {
    dirOnly = true;
    pattern = pattern.replace(/\/+$/, "");
  }
  let anchored = false;
  if (pattern.startsWith("/")) {
    anchored = true;
    pattern = pattern.slice(1);
  }
  if (pattern.includes("**") || pattern.includes("[") || pattern.includes("]")) {
    return null; // unsupported — documented limitation
  }
  return { pattern, dirOnly, negated, anchored };
}

function gitignoreRuleMatches(rel, isDir, rule) {
  if (rule.dirOnly && !isDir) return false;
  let target = rel;
  if (!rule.anchored && !rule.pattern.includes("/")) {
    target = rel.split("/").pop();
  }
  return matches(rule.pattern, target);
}

function scopeMatches(rel, isDir, patterns) {
  let ignored = false;
  for (const pattern of patterns) {
    const rule = normalizeGitignoreRule(pattern);
    if (!rule) continue;
    if (gitignoreRuleMatches(rel, isDir, rule)) ignored = !rule.negated;
  }
  return ignored;
}

/**
 * Return true if `rel` (relative path, '/'-separated) is ignored by any scope.
 * Each scope is `{ dir, patterns }`; rules apply only under `scope.dir` ("" for
 * the root .gitignore), with last-match-wins negation and leading-/ anchoring.
 */
function pathIsIgnored(rel, isDir, scopes) {
  for (const scope of scopes) {
    let relToScope = rel;
    if (scope.dir) {
      if (!rel.startsWith(scope.dir + "/") && rel !== scope.dir) continue;
      relToScope = rel.slice(scope.dir.length + (rel === scope.dir ? 0 : 1));
      if (relToScope === "") continue;
    }
    if (scopeMatches(relToScope, isDir, scope.patterns)) return true;
  }
  return false;
}

/**
 * Collect every file under rootChildren as { path, content }, applying
 * hidden-file, .gitignore and ignore-pattern filtering.
 */
/** CRLF and lone CR become LF, exactly what Python's text-mode read does. */
function normalizeNewlines(text) {
  return String(text).replace(/\r\n?/g, "\n");
}

function collectFiles(rootChildren, opts) {
  // scopes: [{ dir, patterns }] — nested .gitignore files create their own
  // scope so anchoring and negation are relative to the right directory.
  const scopes = [];
  const rootGi = rootChildren.find(
    (n) => n.content !== undefined && n.name === ".gitignore"
  );
  if (rootGi && !opts.ignoreGitignore) {
    scopes.push({ dir: "", patterns: parseGitignore(rootGi.content) });
  }

  const files = [];

  function walk(nodes, base) {
    for (const node of nodes) {
      const rel = base ? base + "/" + node.name : node.name;

      if (node.content !== undefined) {
        // File
        if (!opts.includeHidden && node.name.startsWith(".")) continue;
        if (!opts.ignoreGitignore && pathIsIgnored(rel, false, scopes)) continue;
        if (opts.ignorePatterns.some((p) => matches(p, node.name))) continue;
        // [include]: narrows FILES only (directories are always walked); exclusions above still win.
        if (opts.includePatterns && opts.includePatterns.length && !includeMatch(rel, node.name, opts.includePatterns)) continue;
        files.push({ path: rel, content: normalizeNewlines(node.content) });
      } else {
        // Directory
        if (!opts.includeHidden && node.name.startsWith(".")) continue;
        if (!opts.ignoreGitignore && pathIsIgnored(rel, true, scopes)) continue;
        if (opts.ignorePatterns.some((p) => matches(p, node.name))) continue;
        const gi = (node.children || []).find(
          (c) => c.content !== undefined && c.name === ".gitignore"
        );
        if (gi && !opts.ignoreGitignore) {
          scopes.push({ dir: rel, patterns: parseGitignore(gi.content) });
        }
        walk(node.children || [], rel);
      }
    }
  }

  walk(rootChildren, "");
  return files;
}

function addLineNumbers(content) {
  // Replicate Python's splitlines() semantics exactly: trailing line breaks
  // do not produce a trailing empty line, "" is zero lines, but "\n" is one
  // empty line (rendered as "1  ").
  const text = String(content);
  if (text === "") return "";
  const lines = text.split("\n");
  if (text.endsWith("\n")) lines.pop();
  const pad = String(lines.length).length;
  return lines
    .map((l, i) => String(i + 1).padStart(pad) + "  " + l)
    .join("\n");
}

/** Token estimate — must match the Python engine's heuristic exactly.
 *  Python: `max(1, len(text)//4) if text else 0` (floor, min 1). */
function estimateTokens(text) {
  const t = String(text);
  if (!t) return 0;
  return Math.max(1, Math.floor(codePointLength(t) / 4));
}

/** Length in Unicode code points (Python's len), not UTF-16 units: an emoji is
 *  1 here, but 2 for String.length. Without this the engines estimate different
 *  token counts for the same file and truncate it at different places. */
function codePointLength(text) {
  const pairs = text.match(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g);
  return text.length - (pairs ? pairs.length : 0);
}

/** First `n` code points of `text` (Python's text[:n]); never splits a surrogate pair. */
function sliceCodePoints(text, n) {
  if (codePointLength(text) <= n) return text;
  let i = 0;
  for (let seen = 0; seen < n; seen++) {
    const c = text.charCodeAt(i);
    i += c >= 0xd800 && c <= 0xdbff && i + 1 < text.length ? 2 : 1;
  }
  return text.slice(0, i);
}

/**
 * Trim a list of files to a max-token budget.
 *
 * Files are considered in order. While a file fits the remaining budget it is
 * kept whole. When the first file would exceed the budget, it is truncated to
 * the remaining headroom (keeping a marker line) and every later file is
 * dropped. Returns `{ files, kept, truncated, dropped }`.
 *
 * A maxTokens <= 0 (or falsy) means "no budget" and returns everything.
 */
function applyTokenBudget(files, maxTokens) {
  if (!maxTokens || maxTokens <= 0) {
    return { files, kept: files.length, truncated: 0, dropped: 0 };
  }
  const out = [];
  let remaining = maxTokens;
  let truncated = 0;

  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const tokens = estimateTokens(f.content);
    if (tokens <= remaining) {
      out.push(f);
      remaining -= tokens;
      continue;
    }
    // This file crosses the budget — keep its head, drop everything after.
    // Marker text matches the Python engine byte-for-byte (no locale separators).
    if (remaining > 0 && f.content) {
      const head =
        sliceCodePoints(f.content, remaining * 4) +
        "\n\n# ... truncated to fit the " +
        maxTokens +
        "-token budget (this file was ~" +
        tokens +
        " tokens)";
      out.push({ path: f.path, content: head, _truncated: true });
      truncated += 1;
    }
    const dropped = files.length - i - 1;
    return { files: out, kept: out.length - truncated, truncated, dropped };
  }

  return { files: out, kept: out.length, truncated: 0, dropped: 0 };
}

/** Build the equivalent `fileflow` CLI command for the current state. */
function buildCliCommand(paths, opts) {
  // Quote anything with whitespace or shell glob characters so the shell does
  // not split or expand the argument before fileflow receives it.
  const quote = (s) =>
    /[\s*?[\]{}!]/.test(s) ? "'" + s.replace(/'/g, "'\\''") + "'" : s;
  const parts = ["fileflow"];
  for (const p of paths) parts.push(quote(p));
  if (opts.includeHidden) parts.push("--include-hidden");
  if (opts.ignoreGitignore) parts.push("--ignore-gitignore");
  if (opts.lineNumbers) parts.push("--line-numbers");
  if (opts.separators === false) parts.push("--no-separators");
  if (opts.format && opts.format !== "default") parts.push("--format", opts.format);
  for (const pat of opts.ignorePatterns) parts.push("--ignore-patterns", quote(pat));
  if (opts.maxTokens && opts.maxTokens > 0) parts.push("--max-tokens", String(opts.maxTokens));
  return parts.join(" ");
}

/**
 * Render a single file into its prompt block.
 *
 * This is the single source of truth for block formatting — buildPrompt() and
 * the streaming renderer in the web UI both call it, so they can never drift.
 * `index` is 1-based for XML document tags.
 */
function renderBlock(file, index, opts) {
  const c = opts.lineNumbers ? addLineNumbers(file.content) : file.content;
  const info = provFor(opts, file.path);
  if (opts.format === "xml") {
    let attrs = "";
    if (info) {
      attrs = ` provenance="${info.provenance}"`;
      if (info.source_url) attrs += ` source_url="${attrValue(info.source_url)}"`;
    }
    return `<document index="${index}"${attrs}>\n<source>${file.path}</source>\n<document_content>\n${c}\n</document_content>\n</document>`;
  }
  const head = info ? `${file.path}\n${provNote(info)}` : file.path;
  return opts.separators ? `${head}\n---\n${c}\n---` : `${head}\n${c}`;
}

/** Block separator between documents for a given format. */
function blockSeparator(format) {
  return format === "xml" ? "\n" : "\n\n";
}

/** Estimate the full rendered character count without building the string. */
function estimatePromptChars(files, opts) {
  const extra = normalizeInstruction(opts.instruction).length;
  return estimateFilesChars(files, opts) + (extra ? extra + 64 : 0);
}

function estimateFilesChars(files, opts) {
  if (opts.format === "json") {
    // Rough but cheap: content + keys/indentation overhead per file.
    return files.reduce((sum, f) => sum + f.content.length, 0) + files.length * 40;
  }
  const sepLen = blockSeparator(opts.format).length;
  let total = 0;
  for (let i = 0; i < files.length; i++) {
    total += renderBlock(files[i], i + 1, opts).length;
  }
  if (opts.format === "xml") {
    // buildPrompt: ["<documents>", ...blocks, "</documents>"].join("\n")
    total += "<documents>".length + "</documents>".length;
    total += (files.length + 1) * sepLen;
  } else {
    // buildPrompt: blocks.join("\n\n")
    total += Math.max(0, files.length - 1) * sepLen;
  }
  return total;
}

/** Token count with honest wording: the built-in heuristic is an estimate and
 *  must never be shown as a bare, measurement-looking number. A named real
 *  tokenizer (server mode) is shown with its name instead. */
function formatTokens(count, tokenizer) {
  const n = Number(count || 0).toLocaleString("en-US");
  if (!tokenizer || tokenizer === "heuristic") return `≈ ${n} tokens (estimate)`;
  return `${n} tokens (${tokenizer})`;
}

/** One line describing the server's secret findings ("" when there are none).
 *  Shows file paths and rule names only -- the server never sends the values. */
function describeSecrets(findings) {
  const list = Array.isArray(findings) ? findings : [];
  if (list.length === 0) return "";
  const shown = list.slice(0, 3).map((f) => `${f.path} (${(f.rules || []).join(", ")})`);
  const more = list.length > 3 ? ` +${list.length - 3} more` : "";
  const noun = list.length === 1 ? "file" : "files";
  return `⚠ possible secrets in ${list.length} ${noun}: ${shown.join("; ")}${more}`;
}

/* ---- provenance labels (mirror fileflow/provenance.py exactly) ---------------------------------
   A label lives in YAML front matter at the top of a file: provenance: observed|generated|user,
   optionally source_url. ASCII-only whitespace and [^\n]* matching keep this byte-identical to
   Python (JS `.` stops at \r, Python's does not, and \s differs). Objects have a null prototype so a
   file called "__proto__" cannot poison a lookup. */
const PROVENANCE_LABELS = ["observed", "generated", "user"];

function stripAscii(s) {
  return String(s).replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, "");
}

function unquoteValue(raw) {
  raw = stripAscii(raw);
  if (raw.length >= 2 && raw[0] === '"' && raw[raw.length - 1] === '"') {
    try {
      return JSON.parse(raw);
    } catch (e) {
      return raw.slice(1, -1);
    }
  }
  if (raw.length >= 2 && raw[0] === "'" && raw[raw.length - 1] === "'") return raw.slice(1, -1);
  return raw;
}

function parseFrontmatter(text) {
  const lines = String(text).split("\n");
  if (!lines.length || stripAscii(lines[0]) !== "---") return null;
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    if (stripAscii(lines[i]) === "---") {
      end = i;
      break;
    }
  }
  if (end === -1) return null;
  const data = Object.create(null);
  let key = null;
  for (const line of lines.slice(1, end)) {
    const m = /^([A-Za-z0-9_-]+):[ \t]*([^\n]*)$/.exec(line);
    if (m) {
      key = m[1];
      const val = stripAscii(m[2]);
      data[key] = [">", "|", ">-", "|-"].includes(val) ? "" : unquoteValue(val);
    } else if (key && (line.startsWith(" ") || line.startsWith("\t")) && stripAscii(line)) {
      data[key] = stripAscii(data[key] + " " + stripAscii(line));
    }
  }
  return data;
}

/** {provenance, source_url?} when the text carries a valid label, else null. */
function readProvenance(text) {
  if (!String(text).startsWith("---")) return null;
  const fm = parseFrontmatter(text);
  if (!fm) return null;
  const label = stripAscii(fm.provenance === undefined ? "" : fm.provenance).toLowerCase();
  if (!PROVENANCE_LABELS.includes(label)) return null;
  const out = { provenance: label };
  const url = stripAscii(fm.source_url === undefined ? "" : fm.source_url);
  if (url) out.source_url = url;
  return out;
}

/** path -> label for every file that carries one (a null-prototype map). */
function provenanceMap(files) {
  const map = Object.create(null);
  for (const f of files) {
    const info = readProvenance(f.content);
    if (info) map[f.path] = info;
  }
  return map;
}

/** Make a value safe for an XML attribute / one-line note: no control chars, quotes, tags or &. */
function attrValue(value) {
  return sliceCodePoints(String(value).replace(/[\u0000-\u001f\u007f"<>&]/g, ""), 300);
}

function provFor(opts, path) {
  const map = opts && opts.provenance;
  return map && Object.prototype.hasOwnProperty.call(map, path) ? map[path] : null;
}

function provNote(info) {
  let note = "provenance: " + info.provenance;
  if (info.source_url) note += "; source: " + attrValue(info.source_url);
  return "[" + note + "]";
}

/** Trim ASCII whitespace only ("" = no instruction). Mirrors Python's
 *  normalize_instruction so the engines can't disagree on exotic Unicode spaces
 *  (JS String.trim strips more than Python's str.strip(" \t\r\n") does). */
function normalizeInstruction(text) {
  return String(text == null ? "" : text).replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, "");
}

/** Render documents; a non-blank opts.instruction is placed OUTSIDE the files:
 *  default -> "# Task" then "# Files"; xml -> <task_instructions> before
 *  <documents>; json -> {instructions, documents} (a bare array without one). */
function buildPrompt(files, opts) {
  const instruction = normalizeInstruction(opts.instruction);

  if (opts.format === "json") {
    const docs = files.map((f) => {
      const entry = { path: f.path, content: opts.lineNumbers ? addLineNumbers(f.content) : f.content };
      const info = provFor(opts, f.path);
      if (info) {
        entry.provenance = info.provenance;
        if (info.source_url) entry.source_url = attrValue(info.source_url);
      }
      return entry;
    });
    return JSON.stringify(instruction ? { instructions: instruction, documents: docs } : docs, null, 2);
  }

  if (opts.format === "xml") {
    const blocks = ["<documents>"];
    files.forEach((f, i) => blocks.push(renderBlock(f, i + 1, opts)));
    blocks.push("</documents>");
    const body = blocks.join("\n");
    return instruction ? `<task_instructions>\n${instruction}\n</task_instructions>\n${body}` : body;
  }

  // default
  const body = files.map((f) => renderBlock(f, 0, opts)).join("\n\n");
  if (!instruction) return body;
  const parts = [`# Task\n\n${instruction}`];
  if (body) parts.push(`# Files\n\n${body}`);
  return parts.join("\n\n");
}

/* Expose on globalThis (browser classic script shares globals anyway) so
   app.js and Node test harnesses can both reach the functions. */
const api = {
  parseGitignore,
  globToRegExp,
  matches,
  matchesAny,
  normalizeGitignoreRule,
  pathIsIgnored,
  collectFiles,
  addLineNumbers,
  estimateTokens,
  applyTokenBudget,
  buildCliCommand,
  renderBlock,
  blockSeparator,
  estimatePromptChars,
  globMatch,
  includeMatch,
  normalizeNewlines,
  normalizeInstruction,
  readProvenance,
  provenanceMap,
  attrValue,
  formatTokens,
  describeSecrets,
  codePointLength,
  sliceCodePoints,
  buildPrompt,
};

if (typeof globalThis !== "undefined") Object.assign(globalThis, api);

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
}
