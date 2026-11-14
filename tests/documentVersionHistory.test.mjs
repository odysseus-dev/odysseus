// tests/documentVersionHistory.test.mjs
/**
 * Runner for the version history behavior suite.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('version history extracted behavior', () => {
  const suite = fileURLToPath(new URL('./documentVersionHistory.behavior.mjs', import.meta.url));
  const result = spawnSync(process.execPath, ['--experimental-vm-modules', '--test', suite], {
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || '');
    process.stdout.write(result.stdout || '');
  }
  assert.equal(result.status, 0, result.stderr || result.stdout);
});
