/* Cross-engine golden-output contract (Node side).

   Loads the shared testdata/golden-corpus.json and asserts the JS engine's
   renderer produces byte-identical output to the committed expected strings.
   The Python suite (tests/test_golden_contract.py) runs the same corpus
   against the Python engine — so if the two engines drift, one suite fails.
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const core = require("./core");

const corpusPath = path.join(__dirname, "..", "..", "..", "testdata", "golden-corpus.json");
const corpus = JSON.parse(fs.readFileSync(corpusPath, "utf8"));

const documents = corpus.documents;

const scenarios = [
  ["default", { format: "default", lineNumbers: false, separators: true }],
  ["no-separators", { format: "default", lineNumbers: false, separators: false }],
  ["xml", { format: "xml", lineNumbers: false, separators: true }],
  ["json", { format: "json", lineNumbers: false, separators: true }],
  ["line-numbers", { format: "default", lineNumbers: true, separators: true }],
  ["xml-line-numbers", { format: "xml", lineNumbers: true, separators: true }],
  ["json-line-numbers", { format: "json", lineNumbers: true, separators: true }],
  ["line-numbers-no-separators", { format: "default", lineNumbers: true, separators: false }],
];

for (const [name, opts] of scenarios) {
  const actual = core.buildPrompt(documents, opts);
  const expected = corpus.scenarios[name];
  assert.strictEqual(
    actual,
    expected,
    `JS engine diverged from golden in '${name}'`
  );
  console.log(`ok golden: ${name}`);
}

assert.ok(corpus.instruction_scenarios.length >= 8, "instruction scenarios missing from corpus");
for (const sc of corpus.instruction_scenarios) {
  assert.strictEqual(
    core.buildPrompt(documents, sc.opts),
    sc.expected,
    `JS engine diverged from golden in '${sc.name}'`
  );
  console.log(`ok golden: ${sc.name}`);
}

assert.ok(corpus.provenance_scenarios.length >= 7, "provenance scenarios missing from corpus");
for (const sc of corpus.provenance_scenarios) {
  assert.strictEqual(
    core.buildPrompt(documents, sc.opts),
    sc.expected,
    `JS engine diverged from golden in '${sc.name}'`
  );
  console.log(`ok golden: ${sc.name}`);
}

assert.ok(corpus.provenance_parse_cases.length >= 20, "provenance parse cases missing from corpus");
for (const c of corpus.provenance_parse_cases) {
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(core.readProvenance(c.text))),
    c.expected,
    `readProvenance diverged on ${JSON.stringify(c.text)}`
  );
}
console.log(`ok golden: ${corpus.provenance_parse_cases.length} provenance parse cases`);

assert.ok(corpus.include_cases.length >= 40, "include cases missing from corpus");
for (const c of corpus.include_cases) {
  const name = c.rel.slice(c.rel.lastIndexOf("/") + 1);
  assert.strictEqual(core.includeMatch(c.rel, name, c.patterns), c.expected, `includeMatch diverged on ${JSON.stringify(c)}`);
}
console.log(`ok golden: ${corpus.include_cases.length} include glob cases`);

for (const case_ of corpus.gitignore_cases) {
  const scopes = case_.scopes;
  for (const entry of case_.paths) {
    const actual = core.pathIsIgnored(entry.path, entry.isDir, scopes);
    assert.strictEqual(
      actual,
      entry.expected,
      `${case_.name}: pathIsIgnored(${entry.path}) != ${entry.expected}`
    );
  }
  console.log(`ok gitignore: ${case_.name}`);
}

console.log("\nAll golden contract tests passed (JS matches Python reference).");
