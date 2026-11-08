const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers.js');

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test('setup checklist remains usable above the mobile layers sheet', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('#ge-setup-btn').click();
  const checklist = page.locator('.ge-preflight');
  const start = checklist.locator('[data-preflight-start]');
  await expect(checklist).toBeVisible();
  await start.scrollIntoViewIfNeeded();
  await expect(start).toHaveCSS('white-space', 'nowrap');
  expect(await start.evaluate(button => button.scrollWidth <= button.clientWidth)).toBe(true);
  await start.click();
  await expect(checklist).toBeHidden();
});
