const { test, expect } = require('@playwright/test');
const { openBlankEditor, selectTool } = require('./helpers.js');

// Editors opened wider than 700px move the tool controls into the options
// strip. At tablet widths (701-820px) that strip must stay visible, or every
// tool control is stranded in a hidden container.
for (const width of [768, 820]) {
  test(`tool controls stay reachable at a ${width}px tablet width`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    await openBlankEditor(page, { width: 320, height: 240 }, `Tablet ${width}`);
    const inViewport = locator => locator.evaluate(el => {
      const box = el.getBoundingClientRect();
      return box.width > 0 && box.left >= 0 && box.right <= innerWidth + 1 && box.bottom <= innerHeight + 1;
    });

    await selectTool(page, 'brush');
    await expect(page.locator('#ge-options-bar')).toBeVisible();
    await expect(page.locator('#ge-options-bar .ge-size-slider')).toBeVisible();
    expect(await inViewport(page.locator('#ge-options-bar .ge-size-slider'))).toBe(true);

    await selectTool(page, 'transform');
    await expect(page.locator('#ge-transform-w')).toBeVisible();
    await page.locator('#ge-transform-w').fill('200');
    await page.locator('#ge-transform-w').dispatchEvent('input');
    await expect.poll(() => page.evaluate(async () => (await import('/static/js/editor/state.js')).state.transformPendingW)).toBe(200);
    await page.locator('#ge-transform-cancel-btn').click();
  });
}

test('responsive transition across 700px breakpoint preserves dock/sheet layout and state', async ({ page }) => {
  // Desktop -> Phone transition
  await page.setViewportSize({ width: 1024, height: 768 });
  await openBlankEditor(page, { width: 320, height: 240 }, 'Responsive transitions');
  await expect(page.locator('.ge-right-panel')).toHaveClass(/ge-right-panel--dock/);
  await expect(page.locator('.ge-right-panel > .ge-dock')).toBeVisible();

  // Resize across 700px to phone
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.ge-right-panel')).not.toHaveClass(/ge-right-panel--dock/);
  await expect(page.locator('.ge-right-panel > .ge-dock')).toBeHidden();

  // Resize back to desktop
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.locator('.ge-right-panel')).toHaveClass(/ge-right-panel--dock/);
  await expect(page.locator('.ge-right-panel > .ge-dock')).toBeVisible();
});
