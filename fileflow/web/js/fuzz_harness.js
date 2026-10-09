/* Cross-engine fuzz harness (JS side).

Reads a JSON file with `{ cases: [{ tree, opts }] }` and, for each case, runs
the full JS pipeline — collectFiles → (sort by path) → applyTokenBudget →
buildPrompt — printing the resulting prompt text as JSON. The Python test
(tests/test_cross_engine_fuzz.py) materializes the same tree on disk, runs the
equivalent Python pipeline with paths sorted identically, and asserts the two
outputs are byte-identical.

Usage: node fileflow/web/js/fuzz_harness.js <cases.json>
*/

const fs = require("fs");
const core = require("./core");

const file = process.argv[2];
if (!file) {
  console.error("usage: node fuzz_harness.js <cases.json>");
  process.exit(2);
}

const data = JSON.parse(fs.readFileSync(file, "utf8"));

const results = data.cases.map(({ tree, opts }) => {
  const collected = core.collectFiles(tree, opts);
  // Sort by path so walk order does not affect the comparison (both engines
  // sort identically before rendering in the fuzz).
  collected.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  // Labels are read from the content BEFORE budget truncation (the Python side does the same).
  const provenance = core.provenanceMap(collected);
  const { files } = core.applyTokenBudget(collected, opts.maxTokens || 0);
  const text = core.buildPrompt(files, Object.assign({}, opts, { provenance }));
  return { text };
});

process.stdout.write(JSON.stringify(results));
