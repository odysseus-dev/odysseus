const { test, expect } = require('@playwright/test');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

// The previously opt-in node:test now uses only this config's owned app/account.
// Its native JUnit report retains its authored identity; this case is a driver.
test('native email reply regression on the disposable authenticated app', async ({ context, baseURL }, testInfo) => {
  const origin = new URL(baseURL);
  expect(origin.hostname).toBe('127.0.0.1');
  const cookie = (await context.cookies(baseURL)).find(item => item.name === 'odysseus_session');
  expect(cookie?.value).toBeTruthy();
  const report = testInfo.outputPath('native-email-junit.xml');
  const result = spawnSync(process.execPath, ['--test', '--test-reporter=spec', '--test-reporter-destination=stdout',
    '--test-reporter=junit', `--test-reporter-destination=${report}`, 'tests/emailReplyBrowser.test.mjs'], {
    cwd: path.resolve(__dirname, '../../..'), encoding: 'utf8', timeout: 80_000,
    env: { ...process.env, ODYSSEUS_UI_TEST_URL: baseURL, ODYSSEUS_UI_TEST_COOKIE: cookie.value },
  });
  console.log(result.stdout);
  expect(result.error, String(result.error)).toBeUndefined();
  expect(result.status, result.stdout + result.stderr).toBe(0);
});
