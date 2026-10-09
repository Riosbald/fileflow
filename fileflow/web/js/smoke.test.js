/* Smoke test: app.js initialises, renders the tree and generates output under a
 * DOM shim, in static (no server) mode. Server mode is covered by live.test.js. */
const assert = require("assert");
const { installDom } = require("./dom_shim.js");

const { byId, readyCbs } = installDom();

require("./core.js");
require("./app.js");

// Drive init now that app.js has registered its DOMContentLoaded callback.
assert.strictEqual(readyCbs.length, 1, "init registered");
readyCbs[0]();

setTimeout(() => {
  const pre = byId["output-pre"];
  const meta = byId["output-meta"].textContent;
  assert.ok(pre.textContent.length > 0, "output produced");
  // Regression: init() used to look for a file row BEFORE rendering the tree, so a
  // new visitor saw an empty sidebar and a blank editor while the output was full.
  // (The shim used to fabricate that row and hid the bug; it now looks at real rows.)
  const tree = byId["file-tree"];
  assert.ok(tree.children.length > 0, "file tree has rows after init");
  assert.notStrictEqual(byId["editor-file"].textContent, "—", "a file is selected after init");
  assert.ok(/files/.test(meta), "meta has file count: " + meta);
  console.log("meta:", meta);
  console.log("output head:", pre.textContent.split("\n").slice(0, 4).join(" | "));
  console.log("\nSmoke test passed — app.js initialized, rendered the tree and generated output without errors.");
  process.exit(0);
}, 400);
