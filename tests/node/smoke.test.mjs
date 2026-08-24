import test from 'node:test';
import assert from 'node:assert/strict';
import { fileflow } from './helpers.mjs';

test('fileflow --help exits 0 and lists commands', () => {
  const out = fileflow(['--help']);
  assert.match(out, /prompt/);
  assert.match(out, /serve/);
});

test('fileflow prompt --help documents precedence', () => {
  const out = fileflow(['prompt', '--help']);
  assert.match(out, /CLI flags > \.files-to-prompt config > defaults/);
});

test('fileflow --version reports a version', () => {
  assert.match(fileflow(['--version']), /fileflow, version \d+\.\d+\.\d+/);
});
