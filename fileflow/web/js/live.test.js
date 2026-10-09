/* Live-server mode: how app.js reacts to /api/watch frames.
 *
 * Runs the real app.js under the DOM shim with a fake `fetch` (backed by a
 * mutable in-memory "server") and a fake `WebSocket`, so every scenario below
 * is observable through the request log and the controls, no browser needed.
 *
 *   A  a .files-to-prompt edit on disk reloads the controls, and the very next
 *      tree/prompt requests use the new settings
 *   B  an ordinary file change refreshes tree + prompt but leaves config alone
 *   C  the echo of the UI's own save is recognised (no reload, no toast)
 *   D  a half-edited, invalid config keeps the user's settings (and recovers)
 *   E  after a dropped socket, reconnecting re-syncs only if something changed
 *   F  a late frame after leaving live mode is ignored
 */
const assert = require("assert");
const { installDom } = require("./dom_shim.js");

const { byId, readyCbs, window } = installDom();

/* ---------- fake server ---------- */
const BASE_CONFIG = {
  include: { patterns: [] },
  exclude: { patterns: [] },
  output: { format: "default", separators: true, line_numbers: false, max_tokens: 8000 },
  ignore: { gitignore: true, hidden: false },
};
const server = {
  config: JSON.parse(JSON.stringify(BASE_CONFIG)),
  configStatus: 200,
  configDetail: "",
  tree: [
    { name: "README.md", type: "file", size: 6 },
    { name: "src", type: "dir", children: [{ name: "main.py", type: "file", size: 12 }] },
  ],
  files: { "README.md": "hello\n" },
};
const calls = []; // { method, kind, url }

function fakeFetch(url, init = {}) {
  const method = init.method || "GET";
  const u = new URL(url, "http://fileflow.test");
  const kind = u.pathname.replace("/api/", "");
  calls.push({ method, kind, url: u.pathname + u.search });
  const reply = (status, body) =>
    Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
  switch (u.pathname) {
    case "/api/health":
      return reply(200, { status: "ok" });
    case "/api/config":
      if (method === "PUT") {
        server.config = { ...JSON.parse(init.body), include: { patterns: [] } };
        return reply(200, { ok: true });
      }
      if (server.configStatus !== 200) return reply(server.configStatus, { detail: server.configDetail });
      return reply(200, server.config);
    case "/api/tree":
      return reply(200, server.tree);
    case "/api/file":
      if (u.searchParams.get("path") === "huge.log")
        return reply(413, { detail: "huge.log is 3.0 MB, over the 1.0 MB size limit, so it is not shown or sent" });
      return reply(200, { path: u.searchParams.get("path"), content: server.files[u.searchParams.get("path")] || "" });
    case "/api/prompt":
      return reply(200, {
        text: "PROMPT " + u.search,
        meta: { files: 1, chars: 10, tokens: 3, lines: 1, tokenizer: "heuristic" },
      });
    default:
      return reply(404, { detail: "not found" });
  }
}

/* ---------- fake WebSocket ---------- */
class FakeWS {
  constructor(url) {
    this.url = url;
    FakeWS.instances.push(this);
    setTimeout(() => this.onopen && this.onopen(), 0);
  }
  close() { if (this.onclose) this.onclose(); }
  emit(frame) { this.onmessage({ data: JSON.stringify(frame) }); }
}
FakeWS.instances = [];

const define = (name, value) =>
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
define("fetch", fakeFetch); // must exist before app.js loads (it feature-detects fetch)
define("WebSocket", FakeWS);
define("location", { protocol: "https:", host: "preview.example" });
window.WebSocket = FakeWS;

/* ---------- helpers ---------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond, what, timeout = 3000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeout)
      throw new Error(`timed out waiting for: ${what}\n  requests so far: ${calls.map((c) => c.method + " " + c.url).join("\n                   ")}\n  toast: ${JSON.stringify(byId["toast"].textContent)}`);
    await sleep(10);
  }
}
const sawPrompt = () => calls.some((c) => c.kind === "prompt");
const idx = (kind) => calls.findIndex((c) => c.kind === kind);
const lastPrompt = () => [...calls].reverse().find((c) => c.kind === "prompt").url;
const quiet = () => { calls.length = 0; byId["toast"].textContent = ""; };
const toast = () => byId["toast"].textContent;
const select = byId["opt-max-tokens"];

async function main() {
  require("./core.js");
  require("./app.js");
  await readyCbs[0](); // init()

  // Server detected -> the toggle becomes available.
  assert.strictEqual(byId["server-option"].hidden, false, "Live-server toggle shown when /api/health answers");

  // Enter live mode.
  byId["opt-server"].checked = true;
  await byId["opt-server"].fire("change");
  await waitFor(sawPrompt, "initial prompt");
  assert.strictEqual(FakeWS.instances.length, 1, "one watch socket opened");
  assert.strictEqual(FakeWS.instances[0].url, "wss://preview.example/api/watch?poll=1.5", "wss:// behind https");
  assert.strictEqual(select.value, "8000", "initial config applied");
  const ws1 = FakeWS.instances[0];

  // First `ready` right after connecting: the UI was just loaded, nothing to catch up on.
  quiet();
  ws1.emit({ ready: true, hash: "h1", changed: false, config_changed: false });
  await sleep(350);
  assert.strictEqual(calls.length, 0, "initial ready frame triggers no refresh");
  console.log("ok  first ready frame is a no-op");

  // A) .files-to-prompt edited on disk.
  server.config = {
    ...BASE_CONFIG,
    output: { format: "xml", separators: true, line_numbers: true, max_tokens: 12000 },
    exclude: { patterns: ["tests"] },
  };
  quiet();
  ws1.emit({ changed: true, config_changed: true, hash: "h2" });
  await waitFor(sawPrompt, "prompt after config edit");
  assert.ok(idx("config") >= 0 && idx("config") < idx("tree") && idx("tree") < idx("prompt"),
    "config is re-read BEFORE the tree/prompt requests that depend on it: " + calls.map((c) => c.kind));
  assert.strictEqual(byId["opt-line-numbers"].checked, true, "line numbers applied");
  assert.strictEqual(select.value, "12000", "custom token budget applied");
  assert.ok(select.children.some((o) => o.value === "12000"), "an option was added for the custom budget");
  const p = lastPrompt();
  for (const part of ["format=xml", "line_numbers=true", "ignore_patterns=tests", "budget=12000"])
    assert.ok(p.includes(part), `next prompt request uses the reloaded settings (${part}): ${p}`);
  assert.ok(calls.some((c) => c.kind === "file"), "the open file is re-read so the editor is not stale");
  assert.strictEqual(toast(), ".files-to-prompt reloaded");
  console.log("ok  A: config edit on disk reloads controls, then refreshes with them");

  // B) an ordinary file changed.
  quiet();
  ws1.emit({ changed: true, config_changed: false, hash: "h3" });
  await waitFor(sawPrompt, "prompt after file change");
  assert.ok(!calls.some((c) => c.kind === "config"), "config is not re-read for an ordinary file change");
  assert.ok(calls.some((c) => c.kind === "tree"), "tree refreshed");
  assert.strictEqual(toast(), "", "no toast for ordinary changes");
  assert.strictEqual(byId["opt-line-numbers"].checked, true, "controls untouched");
  console.log("ok  B: ordinary change refreshes tree+prompt, leaves config alone");

  // C) the UI saves; the file watcher then reports that very write.
  byId["opt-line-numbers"].checked = false;
  await byId["opt-line-numbers"].fire("change");
  await waitFor(() => calls.some((c) => c.method === "PUT"), "PUT /api/config");
  await sleep(30); // let the save's .then() record what the file now holds
  quiet();
  ws1.emit({ changed: true, config_changed: true, hash: "h4" });
  await waitFor(sawPrompt, "prompt after echo");
  assert.ok(calls.some((c) => c.kind === "config" && c.method === "GET"), "config was re-read");
  assert.strictEqual(toast(), "", "own save echoing back is not announced as a reload");
  assert.strictEqual(byId["opt-line-numbers"].checked, false, "and does not revert the control");
  console.log("ok  C: echo of the UI's own save is ignored");

  // D) the file is invalid right now (mid-edit): keep the user's settings.
  server.configStatus = 422;
  server.configDetail = ".files-to-prompt is not valid TOML: Expected '=' (at line 2)";
  quiet();
  ws1.emit({ changed: true, config_changed: true, hash: "h5" });
  await waitFor(sawPrompt, "prompt despite invalid config");
  assert.ok(/syntax error/.test(toast()), "user is told the file is broken: " + toast());
  assert.strictEqual(byId["opt-line-numbers"].checked, false, "settings kept");
  assert.strictEqual(select.value, "12000", "settings kept");
  assert.ok(calls.some((c) => c.kind === "tree"), "files still refresh while the config is broken");
  // ...then the user fixes the file.
  server.configStatus = 200;
  server.config = { ...BASE_CONFIG, output: { ...BASE_CONFIG.output, line_numbers: true, max_tokens: 0 } };
  quiet();
  ws1.emit({ changed: true, config_changed: true, hash: "h6" });
  await waitFor(sawPrompt, "prompt after the fix");
  assert.strictEqual(toast(), ".files-to-prompt reloaded");
  assert.strictEqual(byId["opt-line-numbers"].checked, true, "fixed file applied");
  assert.strictEqual(select.value, "0", "max_tokens = 0 now means Off (it used to be ignored)");
  console.log("ok  D: invalid config keeps settings, and recovers once fixed");

  // E) socket drops and reconnects.
  ws1.close();
  await waitFor(() => FakeWS.instances.length === 2, "reconnect #1", 4000);
  const ws2 = FakeWS.instances[1];
  quiet();
  ws2.emit({ ready: true, hash: "h6", changed: false, config_changed: false }); // same as last seen
  await sleep(350);
  assert.strictEqual(calls.length, 0, "reconnect with an unchanged hash does not refresh");
  ws2.close();
  await waitFor(() => FakeWS.instances.length === 3, "reconnect #2", 4000);
  const ws3 = FakeWS.instances[2];
  quiet();
  ws3.emit({ ready: true, hash: "h7", changed: false, config_changed: false }); // changed while away
  await waitFor(sawPrompt, "catch-up refresh");
  assert.ok(calls.some((c) => c.kind === "config") && calls.some((c) => c.kind === "tree"),
    "reconnect with a different hash re-syncs config + tree + prompt");
  console.log("ok  E: reconnect re-syncs only when the project changed while disconnected");

  // G) a file the server refuses (over the size limit): the editor must say why, not look empty.
  server.tree.push({ name: "huge.log", type: "file", size: 3000000 });
  quiet();
  ws3.emit({ changed: true, config_changed: false, hash: "h-huge" });
  await waitFor(sawPrompt, "refresh after the tree changed");
  const hugeRow = byId["file-tree"].children.find((c) => c.dataset && c.dataset.path === "huge.log");
  assert.ok(hugeRow, "the oversized file is still listed in the tree");
  await hugeRow.fire("click");
  await sleep(300);
  assert.ok(/^— not shown: huge\.log is 3\.0 MB, over the 1\.0 MB size limit/.test(byId["editor"].value),
    "the editor explains why instead of showing an empty file: " + JSON.stringify(byId["editor"].value));
  console.log("ok  G: a refused file shows its reason in the editor");

  // F) leave live mode; a late frame must not repaint the sample project.
  byId["opt-server"].checked = false;
  await byId["opt-server"].fire("change");
  quiet();
  ws3.emit({ changed: true, config_changed: true, hash: "late" });
  await sleep(300);
  assert.strictEqual(calls.filter((c) => c.kind !== "health").length, 0, "no server requests after leaving live mode");
  const before = FakeWS.instances.length;
  await sleep(1200); // longer than the first reconnect back-off
  assert.strictEqual(FakeWS.instances.length, before, "no reconnect after leaving live mode");
  console.log("ok  F: late frames and reconnects are ignored after leaving live mode");

  console.log("\nLive-mode tests passed.");
  process.exit(0);
}

setTimeout(() => { console.error("live.test.js timed out"); process.exit(1); }, 30000).unref();
main().catch((err) => { console.error(err); process.exit(1); });
