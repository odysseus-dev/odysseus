import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('behavior runner requires executed passing tests and complete successful output', t => {
  t.mock.method(process.stderr, 'write', () => true);
  const directory = mkdtempSync(join(tmpdir(), 'document-behavior-runner-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const run = source => {
    const path = join(directory, 'suite.mjs');
    writeFileSync(path, source);
    return () => runBehaviorSuite(path);
  };
  assert.throws(run('// no registered tests'), /did not execute/);
  assert.throws(run("import test from 'node:test'; test.skip('skipped', () => {});"), /did not execute/);
  assert.throws(run("import test from 'node:test'; test('failed', () => { throw new Error('regression'); });"), /Suite process failed/);
  assert.throws(run("import test from 'node:test'; test('truncated', () => {}); process.exit(0);"), /did not execute/);
  assert.throws(run("process.stdout.write('x'.repeat(2 * 1024 * 1024));"), /Suite process failed/);
  assert.doesNotThrow(run("import test from 'node:test'; test('one real test', () => {});"));
});
