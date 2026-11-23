// Playwright config for the Notes regression specs in this directory.
// They run against an instance that is already up (see README.md and
// run_notes_qa.py); this config never starts a server itself.
const os = require('node:os');
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

for (const name of ['NOTES_QA_ORIGIN', 'NOTES_ORIGIN']) {
  const value = process.env[name] || '';
  if (/:7860\b/.test(value)) throw new Error(`${name} points at :7860; use a scratch instance`);
}

const browsers = (process.env.NOTES_QA_BROWSERS || 'chromium').split(',').map(name => name.trim()).filter(Boolean);

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: /\.spec\.mjs$/,
  // Outside the tree: `test-results/` is not ignored by .gitignore.
  outputDir: process.env.NOTES_QA_OUTPUT_DIR || path.join(os.tmpdir(), 'odysseus-notes-qa-results'),
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  use: {
    headless: true,
    viewport: { width: 1440, height: 1000 },
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: browsers.map(name => ({ name, use: { browserName: name } })),
});
