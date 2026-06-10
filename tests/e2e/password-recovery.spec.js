// Password-recovery flow e2e tests — P5 uiScope:strict gate evidence.
// Covers both the UI affordance on /login and the two reset API endpoints.
//
// Run serially so the beforeAll setup step executes exactly once and the
// page-navigation tests see the app in configured state without a race.
const { test, expect } = require('@playwright/test');

test.describe.configure({ mode: 'serial' });

// Seed admin credentials used only in this spec.
const TEST_ADMIN = { username: 'testadmin', password: 'TestPass123!' };

test.beforeAll(async ({ request }) => {
  // Ensure app is configured before any test runs. Idempotent:
  //   200 = newly created by this call
  //   400 = already configured (from a previous run against the same DB)
  const status = await request.get('/api/auth/status');
  const body = await status.json();
  if (!body.configured) {
    const setup = await request.post('/api/auth/setup', {
      data: TEST_ADMIN,
      headers: { 'Content-Type': 'application/json' },
    });
    if (setup.status() !== 200 && setup.status() !== 400) {
      throw new Error(`Setup failed with status ${setup.status()}: ${await setup.text()}`);
    }
  }
});

test('login page shows Forgot password? link', async ({ page }) => {
  test.setTimeout(15000);
  await page.goto('/login');
  // Wait for the async auth-status fetch inside the page to call setMode('login'),
  // which makes forgotArea visible.
  const forgotLink = page.locator('#forgotLink');
  await expect(forgotLink).toBeVisible();
  await expect(forgotLink).toContainText(/forgot/i);
});

test('clicking Forgot password? shows the reset panel and focuses first input', async ({ page }) => {
  test.setTimeout(15000);
  await page.goto('/login');
  await page.locator('#forgotLink').click();
  await expect(page.locator('#resetPanel')).toBeVisible();
  await expect(page.locator('#authForm')).toBeHidden();
  // A11Y-P5-001: focus must land on the first input in the reset panel.
  await expect(page.locator('#resetUsername')).toBeFocused();
});

test('back-link from reset panel restores focus to login username field', async ({ page }) => {
  test.setTimeout(15000);
  await page.goto('/login');
  await page.locator('#forgotLink').click();
  await expect(page.locator('#resetPanel')).toBeVisible();
  await page.locator('#resetBackLink').click();
  await expect(page.locator('#authForm')).toBeVisible();
  await expect(page.locator('#resetPanel')).toBeHidden();
  // A11Y-P5-002: focus must return to the username field on the login form.
  await expect(page.locator('#username')).toBeFocused();
});

test('reset-request endpoint returns 200 with generic ok message', async ({ request }) => {
  test.setTimeout(15000);
  const response = await request.post('/api/auth/reset-request', {
    data: { username: 'nobody-nonexistent' },
    headers: { 'Content-Type': 'application/json' },
  });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
});

test('reset-confirm endpoint rejects invalid token', async ({ request }) => {
  test.setTimeout(15000);
  const response = await request.post('/api/auth/reset-confirm', {
    data: { token: 'invalid-token-xyz', new_password: 'NewPass123!' },
    headers: { 'Content-Type': 'application/json' },
  });
  expect(response.status()).toBe(400);
});

test('reset panel back-link returns to login form', async ({ page }) => {
  test.setTimeout(15000);
  await page.goto('/login');
  await page.locator('#forgotLink').click();
  await expect(page.locator('#resetPanel')).toBeVisible();
  await page.locator('#resetBackLink').click();
  await expect(page.locator('#authForm')).toBeVisible();
  await expect(page.locator('#resetPanel')).toBeHidden();
});
