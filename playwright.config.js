// Playwright config for opr8 UI-evidence (P5 uiScope:strict).
// The opr8 evidence producer (scripts/opr8-playwright-evidence.mjs) spawns the
// dev server via .opr8/config.json -> playwright.devServerCommand, polls health,
// then runs `npx playwright test --reporter=json` against PLAYWRIGHT_BASE_URL.
const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:7000',
    headless: true,
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
