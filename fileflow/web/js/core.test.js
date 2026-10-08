/* Node unit tests for the fileflow web client's core logic. */
const assert = require("assert");
const core = require("./core");

const { collectFiles, buildPrompt, parseGitignore, addLineNumbers, globToRegExp, matches, estimateTokens, applyTokenBudget, buildCliCommand, renderBlock, blockSeparator, estimatePromptChars } = core;

const tree = [
  { name: "README.md", content: "# fileflow\n" },
  { name: ".gitignore", content: "build/\n*.pyc\n# comment\n\n" },
  { name: ".env", content: "SECRET=x\n" },
  { name: ".hidden_dir", children: [{ name: "secret.txt", content: "s\n" }] },
  {
    name: "src",
    children: [
      { name: "app.py", content: "def main():\n    return 1\n" },
      { name: "cached.pyc", content: "\x00\x00" },
      { name: "data.csv", content: "a,b\n" },
    ],
  },
  { name: "build", children: [{ name: "output.txt", content: "x\n" }] },
];

const base = { includeHidden: false, ignoreGitignore: false, lineNumbers: false, separators: true, format: "default", ignorePatterns: [] };

// Default: hidden + gitignored excluded
{
  const files = collectFiles(tree, base);
  const paths = files.map((f) => f.path);
  assert.deepStrictEqual(paths, ["README.md", "src/app.py", "src/data.csv"]);
  console.log("ok default filtering");
}

// includeHidden brings in dotfiles
{
  const files = collectFiles(tree, { ...base, includeHidden: true });
  const paths = files.map((f) => f.path);
  assert.ok(paths.includes(".env"));
  assert.ok(paths.includes(".hidden_dir/secret.txt"));
  console.log("ok include-hidden");
}

// ignoreGitignore brings back cached.pyc and build/output.txt
{
  const files = collectFiles(tree, { ...base, ignoreGitignore: true });
  const paths = files.map((f) => f.path);
  assert.ok(paths.includes("src/cached.pyc"));
  assert.ok(paths.includes("build/output.txt"));
  console.log("ok ignore-gitignore");
}

// ignorePatterns excludes data.csv
{
  const files = collectFiles(tree, { ...base, ignorePatterns: ["*.csv"] });
  const paths = files.map((f) => f.path);
  assert.ok(!paths.includes("src/data.csv"));
  assert.ok(paths.includes("src/app.py"));
  console.log("ok ignore-patterns");
}

// default format with separators (content keeps trailing newline)
{
  const text = buildPrompt([{ path: "src/app.py", content: "x\n" }], base);
  assert.strictEqual(text, "src/app.py\n---\nx\n\n---");
}

// no-separators
{
  const text = buildPrompt([{ path: "a", content: "b\n" }], { ...base, separators: false });
  assert.strictEqual(text, "a\nb\n");
}

// xml
{
  const text = buildPrompt([{ path: "src/app.py", content: "x\n" }], { ...base, format: "xml" });
  assert.ok(text.includes("<documents>"));
  assert.ok(text.includes('<document index="1">'));
  assert.ok(text.includes("<source>src/app.py</source>"));
  assert.ok(text.includes("</documents>"));
}

// json
{
  const text = buildPrompt([{ path: "a", content: "x\n" }], { ...base, format: "json" });
  const data = JSON.parse(text);
  assert.deepStrictEqual(data, [{ path: "a", content: "x\n" }]);
}

// line numbers
{
  const out = addLineNumbers("a\nb\nc\nd\ne\nf\ng\nh\ni\nj");
  const lines = out.split("\n");
  assert.strictEqual(lines[0], " 1  a");
  assert.strictEqual(lines[9], "10  j");
}

// gitignore parsing skips comments + blanks
assert.deepStrictEqual(parseGitignore("build/\n*.pyc\n# c\n\n"), ["build/", "*.pyc"]);

// glob matching
assert.strictEqual(matches("*.py", "app.py"), true);
assert.strictEqual(matches("*.py", "app.txt"), false);
assert.strictEqual(globToRegExp("build/").test("build/"), true);

// token estimation (4 chars/token)
assert.strictEqual(estimateTokens("aaaaaaaa"), 2);
assert.strictEqual(estimateTokens(""), 0);

// token budget: no budget returns everything
{
  const files = [{ path: "a", content: "x".repeat(100) }, { path: "b", content: "y" }];
  const r = applyTokenBudget(files, 0);
  assert.strictEqual(r.files.length, 2);
  assert.strictEqual(r.truncated, 0);
  assert.strictEqual(r.dropped, 0);
}

// token budget: everything fits
{
  const files = [{ path: "a", content: "x".repeat(40) }, { path: "b", content: "y" }];
  const r = applyTokenBudget(files, 100);
  assert.strictEqual(r.files.length, 2);
  assert.strictEqual(r.truncated, 0);
  assert.strictEqual(r.dropped, 0);
}

// token budget: truncates the crossing file, drops the rest
{
  const big = "x".repeat(1000); // ~250 tokens
  const files = [
    { path: "small", content: "a".repeat(40) }, // ~10 tokens
    { path: "big", content: big },              // ~250 tokens
    { path: "after", content: "b".repeat(40) }, // would be dropped
  ];
  const r = applyTokenBudget(files, 20);
  assert.strictEqual(r.kept, 1);
  assert.strictEqual(r.truncated, 1);
  assert.strictEqual(r.dropped, 1);
  assert.strictEqual(r.files.length, 2);
  assert.strictEqual(r.files[0].path, "small");
  assert.strictEqual(r.files[1].path, "big");
  assert.ok(r.files[1]._truncated, "big file is marked truncated");
  assert.ok(r.files[1].content.includes("truncated"), "truncation marker present");
}

// CLI command builder
{
  const cmd = buildCliCommand(["README.md", "src", "my file.txt"], {
    includeHidden: true,
    ignoreGitignore: false,
    lineNumbers: true,
    separators: false,
    format: "xml",
    ignorePatterns: ["*.md", "__pycache__"],
    maxTokens: 8000,
  });
  assert.strictEqual(
    cmd,
    "fileflow README.md src 'my file.txt' --include-hidden --line-numbers --no-separators --format xml --ignore-patterns '*.md' --ignore-patterns __pycache__ --max-tokens 8000"
  );
}

// CLI command builder: defaults are omitted
{
  const cmd = buildCliCommand(["src"], {
    includeHidden: false,
    ignoreGitignore: false,
    lineNumbers: false,
    separators: true,
    format: "default",
    ignorePatterns: [],
    maxTokens: 0,
  });
  assert.strictEqual(cmd, "fileflow src");
}

// Streaming render helpers must reproduce buildPrompt exactly (A6 guard).
{
  const docs = [
    { path: "a.txt", content: "hello\n" },
    { path: "b/c.py", content: "print(1)\n" },
    { path: "d.md", content: "# hi" },
  ];
  const formats = [
    { format: "default", lineNumbers: false, separators: true },
    { format: "default", lineNumbers: false, separators: false },
    { format: "default", lineNumbers: true, separators: true },
    { format: "xml", lineNumbers: false, separators: true },
  ];
  for (const opts of formats) {
    // estimatePromptChars should match the real rendered length.
    assert.strictEqual(estimatePromptChars(docs, opts), buildPrompt(docs, opts).length);
    // A manual streaming join via renderBlock must equal buildPrompt.
    let streamed = "";
    if (opts.format === "xml") {
      streamed = "<documents>";
      docs.forEach((f, i) => {
        streamed += blockSeparator(opts.format) + renderBlock(f, i + 1, opts);
      });
      streamed += blockSeparator(opts.format) + "</documents>";
    } else {
      docs.forEach((f, i) => {
        streamed += (i === 0 ? "" : blockSeparator(opts.format)) + renderBlock(f, i + 1, opts);
      });
    }
    assert.strictEqual(streamed, buildPrompt(docs, opts), "streaming join must equal buildPrompt");
  }
  console.log("ok streaming helpers reproduce buildPrompt");
}

// --- honest token wording + secret findings (F13 / F8) -------------------
{
  assert.strictEqual(core.formatTokens(11840), "≈ 11,840 tokens (estimate)");
  assert.strictEqual(core.formatTokens(11840, "heuristic"), "≈ 11,840 tokens (estimate)");
  assert.strictEqual(core.formatTokens(11840, "gpt-4o"), "11,840 tokens (gpt-4o)");
  assert.strictEqual(core.formatTokens(undefined), "≈ 0 tokens (estimate)");
  assert.strictEqual(core.describeSecrets([]), "");
  assert.strictEqual(core.describeSecrets(undefined), "");
  assert.strictEqual(
    core.describeSecrets([{ path: "src/creds.py", rules: ["aws-access-key-id"], lines: [1] }]),
    "⚠ possible secrets in 1 file: src/creds.py (aws-access-key-id)"
  );
  const many = ["a", "b", "c", "d"].map((p) => ({ path: p, rules: ["private-key"], lines: [1] }));
  assert.ok(core.describeSecrets(many).endsWith("+1 more"));
  assert.ok(!core.describeSecrets(many).includes("lines"), "only paths and rule names are shown");
  console.log("ok token wording and secret findings");
}

// --- provenance map is prototype-safe: a file called __proto__ / constructor is an ordinary file ----
{
  const files = [
    { path: "__proto__", content: "---\nprovenance: user\n---\nx\n" },
    { path: "constructor", content: "plain\n" },
    { path: "toString", content: "---\nprovenance: observed\nsource_url: https://e.com/\n---\n" },
  ];
  const out = core.buildPrompt(files, { format: "default", separators: true, provenance: core.provenanceMap(files) });
  assert.ok(out.includes("__proto__\n[provenance: user]"), "a file named __proto__ keeps its label");
  assert.ok(out.includes("toString\n[provenance: observed; source: https://e.com/]"));
  assert.ok(!out.includes("constructor\n[provenance"), "an unlabelled file named constructor stays unlabelled");
  console.log("ok provenance map is prototype-safe");
}

// --- line endings: normalised on read (like Python), exotic separators untouched ----------------
{
  const files = core.collectFiles([{ name: "w.txt", content: "a\r\nb\rc\n" }], { ignorePatterns: [], includePatterns: [] });
  assert.strictEqual(files[0].content, "a\nb\nc\n");
  const odd = "one\u000ctwo\u2028three";
  assert.strictEqual(core.addLineNumbers(odd), "1  " + odd);
  console.log("ok line endings normalised, odd separators untouched");
}

console.log("\nAll core logic tests passed.");
