/* Minimal DOM shim so app.js can run under Node without a browser.
 *
 * Shared by smoke.test.js (static mode) and live.test.js (server mode). It is
 * deliberately tiny: just enough surface for app.js to initialise, render and
 * react to events. Tests observe behaviour through the elements in `byId`.
 */

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      contains: (c) => this.classList._s.has(c),
    };
    this.style = {};
    this._value = "";
    this._text = "";
    this._hidden = false;
    this.listeners = {};
    this.parentNode = null;
    this.disabled = false;
    this.checked = false;
  }
  get hidden() { return this._hidden; }
  set hidden(v) { this._hidden = v; }
  get value() { return this._value; }
  set value(v) { this._value = v; }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
  get innerHTML() { return this._html || ""; }
  set innerHTML(v) {
    this._html = String(v);
    this.children.length = 0; // like a real DOM: assigning innerHTML replaces the children
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] ?? null; }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  insertBefore(c) { this.children.push(c); return c; }
  remove() {
    if (this.parentNode) {
      const i = this.parentNode.children.indexOf(this);
      if (i >= 0) this.parentNode.children.splice(i, 1);
    }
  }
  querySelector(sel) {
    // Not a parser: if innerHTML mentions <tag ...>, hand back a stub for it.
    if (/^[a-z]+$/i.test(sel) && new RegExp("<" + sel + "[\\s>]", "i").test(this._html || "")) {
      this._stubs = this._stubs || {};
      return (this._stubs[sel] = this._stubs[sel] || new El(sel));
    }
    return null;
  }
  querySelectorAll() { return []; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  /** Test helper: run every listener registered for `type`; resolves when all settle. */
  async fire(type) { await Promise.all((this.listeners[type] || []).map((fn) => fn({ target: this }))); }
  focus() {}
  select() {}
  click() {}
}

const ELEMENTS = [
  ["opt-include-hidden", "input"], ["opt-ignore-gitignore", "input"],
  ["opt-line-numbers", "input"], ["opt-separators", "input"],
  ["opt-format", "div"], ["opt-patterns", "input"], ["patterns-wrap", "div"],
  ["opt-max-tokens", "select"], ["file-tree", "div"], ["editor", "textarea"],
  ["editor-file", "span"], ["empty-editor", "p"], ["delete-file-btn", "button"],
  ["add-file-btn", "button"], ["import-btn", "button"], ["import-input", "input"],
  ["drop-zone", "div"], ["add-modal", "div"], ["modal-close", "button"],
  ["modal-cancel", "button"], ["add-form", "form"], ["new-path", "input"],
  ["new-content", "textarea"], ["output-pre", "pre"], ["output-empty", "div"],
  ["output-meta", "span"], ["trim-note", "div"], ["copy-btn", "button"],
  ["cli-btn", "button"], ["download-btn", "button"],
  ["theme-toggle", "button"], ["load-sample", "button"], ["toast", "div"],
  ["server-option", "label"], ["opt-server", "input"],
];

/**
 * Install DOM globals and return `{ byId, readyCbs }`.
 * `window` is returned too so tests can attach e.g. `WebSocket`.
 */
function installDom() {
  const byId = {};
  const mk = (id, tag) => {
    const e = new El(tag);
    if (id) { e.id = id; byId[id] = e; }
    return e;
  };
  ELEMENTS.forEach(([id, tag]) => mk(id, tag));

  byId["opt-separators"].checked = true;
  byId["opt-max-tokens"].value = "8000";

  const readyCbs = [];
  global.document = {
    documentElement: { dataset: {} },
    body: mk(null, "body"),
    getElementById: (id) => byId[id] || mk(id),
    createElement: (tag) => mk(null, tag),
    addEventListener: (type, cb) => { if (type === "DOMContentLoaded") readyCbs.push(cb); },
    querySelector: (sel) => {
      if (sel === "#file-tree .tree-file") {
        // Honest lookup: only rows app.js really rendered. (This used to return a
        // fabricated row, which hid an init-order bug that left the tree empty.)
        const walk = (el) => {
          for (const c of el.children || []) {
            if (String(c.className || "").split(/\s+/).includes("tree-file")) return c;
            const hit = walk(c);
            if (hit) return hit;
          }
          return null;
        };
        return walk(byId["file-tree"] || mk("file-tree"));
      }
      if (sel.startsWith("#")) return byId[sel.slice(1)] || mk(sel.slice(1));
      return null;
    },
    querySelectorAll: () => [],
    execCommand: () => true,
  };
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.window = { matchMedia: () => ({ matches: false }) };
  global.navigator = { clipboard: { writeText: async () => {} } };
  return { byId, readyCbs, window: global.window };
}

module.exports = { El, installDom };
