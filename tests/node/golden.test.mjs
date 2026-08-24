import test from 'node:test';
import assert from 'node:assert/strict';
import { fileflow, makeFixture, cleanup } from './helpers.mjs';

// Golden outputs: byte-exact expectations for each format against the
// standard two-file fixture (src/app.py + README.md).

const GOLDEN = {
  default: [
    'README.md',
    '---',
    '# readme',
    '',
    '---',
    '',
    'src/app.py',
    '---',
    "print('hi')",
    '',
    '---',
  ].join('\n') + '\n',

  markdown: [
    'README.md',
    '```markdown',
    '# readme',
    '',
    '```',
    '',
    'src/app.py',
    '```python',
    "print('hi')",
    '',
    '```',
  ].join('\n') + '\n',

  xml: [
    '<documents>',
    '<document index="1">',
    '<source>README.md</source>',
    '<document_contents>',
    '# readme',
    '',
    '</document_contents>',
    '</document>',
    '<document index="2">',
    '<source>src/app.py</source>',
    '<document_contents>',
    "print('hi')",
    '',
    '</document_contents>',
    '</document>',
    '</documents>',
  ].join('\n') + '\n',
};

for (const [format, expected] of Object.entries(GOLDEN)) {
  test(`golden output: ${format}`, (t) => {
    const dir = makeFixture();
    t.after(() => cleanup(dir));
    const out = fileflow(['prompt', '.', '--format', format], { cwd: dir });
    assert.equal(out, expected);
  });
}

test('golden output: json (structural)', (t) => {
  const dir = makeFixture();
  t.after(() => cleanup(dir));
  const data = JSON.parse(fileflow(['prompt', '.', '--format', 'json'], { cwd: dir }));
  assert.deepEqual(data, {
    files: [
      { path: 'README.md', content: '# readme\n', tokens: 2 },
      { path: 'src/app.py', content: "print('hi')\n", tokens: 3 },
    ],
    total_tokens: data.total_tokens,
    truncated: false,
  });
});
