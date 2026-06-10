// Baseline UI smoke for the opr8 P5 gate. Extended by P5 with the
// password-recovery flow once that lands.
const { test, expect } = require('@playwright/test');

test('login page loads and shows the login form', async ({ page }) => {
  const resp = await page.goto('/login');
  expect(resp, 'navigation response').toBeTruthy();
  expect(resp.status(), '/login HTTP status').toBeLessThan(400);
  await expect(
    page.locator('input[type="password"], #password, [name="password"]').first()
  ).toBeVisible();
});

test('root route is reachable', async ({ page }) => {
  const resp = await page.goto('/');
  expect(resp, 'navigation response').toBeTruthy();
  // Unauthenticated root may redirect to /login — either way it must not 5xx.
  expect(resp.status(), '/ HTTP status').toBeLessThan(500);
});
