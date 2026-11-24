const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers.js');

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test('AI settings modal remains usable above the mobile layers sheet', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('#ge-ai-settings-btn').click();
  const aiSettings = page.locator('.ge-ai-settings');
  const done = aiSettings.locator('.ge-ai-settings-done');
  await expect(aiSettings).toBeVisible();
  await done.scrollIntoViewIfNeeded();
  await expect(done).toBeVisible();
  expect(await done.evaluate(button => button.scrollWidth <= button.clientWidth)).toBe(true);
  await done.click();
  await expect(aiSettings).toBeHidden();
});
