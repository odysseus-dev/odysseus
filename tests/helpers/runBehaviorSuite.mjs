import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

/**
 * Runs a behavior suite ESM module in an isolated node process with VM modules enabled.
 * Portable across Node versions and reporter defaults (spec vs tap).
 * Ensures failures propagate correctly and empty or unexecuted suites fail.
 */
export function runBehaviorSuite(suiteUrlOrPath) {
  const suitePath = typeof suiteUrlOrPath === 'string' && !suiteUrlOrPath.startsWith('file:')
    ? suiteUrlOrPath
    : fileURLToPath(suiteUrlOrPath);

  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  delete env.NODE_TEST_WORKER_ID;

  const result = spawnSync(
    process.execPath,
    ['--experimental-vm-modules', '--test', '--test-reporter=spec', suitePath],
    { encoding: 'utf8', env }
  );

  const output = (result.stdout || '') + (result.stderr || '');
  if (result.status !== 0) {
    process.stderr.write(output);
  }

  assert.equal(result.status, 0, `Suite process failed with exit code ${result.status}:\n${output}`);

  // Portable match for passing tests in both spec reporter and TAP reporter
  const passMatch = output.match(/(?:ℹ|#)\s+pass\s+([1-9]\d*)/i);
  assert.ok(
    passMatch && parseInt(passMatch[1], 10) > 0,
    `Suite did not execute any passing tests:\n${output}`
  );

  // Ensure no failing tests occurred
  const failMatch = output.match(/(?:ℹ|#)\s+fail\s+([1-9]\d*)/i);
  assert.ok(
    !failMatch,
    `Suite reported test failures:\n${output}`
  );
}
