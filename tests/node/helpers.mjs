import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export function fileflow(args, opts = {}) {
  return execFileSync('python3', ['-m', 'fileflow', ...args], {
    encoding: 'utf-8',
    ...opts,
  });
}

export function makeFixture() {
  const dir = mkdtempSync(join(tmpdir(), 'fileflow-node-'));
  mkdirSync(join(dir, 'src'));
  writeFileSync(join(dir, 'src', 'app.py'), "print('hi')\n");
  writeFileSync(join(dir, 'README.md'), '# readme\n');
  return dir;
}

export function cleanup(dir) {
  rmSync(dir, { recursive: true, force: true });
}
