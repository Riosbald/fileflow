import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileflow, makeFixture, cleanup } from './helpers.mjs';

test('config file drives json output; flag overrides it', (t) => {
  const dir = makeFixture();
  t.after(() => cleanup(dir));

  writeFileSync(join(dir, '.files-to-prompt'),
    'format = "json"\n\n[exclude]\npatterns = ["*.md"]\n');

  const jsonOut = JSON.parse(fileflow(['prompt', '.'], { cwd: dir }));
  assert.deepEqual(jsonOut.files.map(f => f.path), ['src/app.py']);
  assert.equal(jsonOut.truncated, false);

  const xmlOut = fileflow(['prompt', '.', '--format', 'xml'], { cwd: dir });
  assert.ok(xmlOut.startsWith('<documents>'));
  assert.ok(!xmlOut.includes('README.md'), 'config exclude still applies');
});

test('exclude patterns merge between config and CLI', (t) => {
  const dir = makeFixture();
  t.after(() => cleanup(dir));

  writeFileSync(join(dir, '.files-to-prompt'), '[exclude]\npatterns = ["*.md"]\n');
  const out = fileflow(['prompt', '.', '-e', '*.py'], { cwd: dir });
  assert.ok(!out.includes('README.md'));
  assert.ok(!out.includes('app.py'));
});
