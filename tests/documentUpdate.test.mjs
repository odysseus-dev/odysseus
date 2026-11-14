// tests/documentUpdate.test.mjs
/**
 * Runner for the documentUpdate behavior suite.
 * SourceTextModule needs the vm-modules flag, which this wrapper supplies so
 * `node --test tests/documentUpdate.test.mjs` stays a normal test entry.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('documentUpdate extracted behavior', () => {
  const suite = fileURLToPath(new URL('./documentUpdate.behavior.mjs', import.meta.url));
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  delete env.NODE_TEST_WORKER_ID;
  const result = spawnSync(process.execPath, ['--experimental-vm-modules', '--test', suite], {
    encoding: 'utf8',
    env,
  });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || '');
    process.stdout.write(result.stdout || '');
  }
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout || '', /ℹ pass [1-9]/);
});
