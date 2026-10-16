const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

async function gaps(page) {
  return page.evaluate(() => {
    const rect = selector => document.querySelector(selector).getBoundingClientRect();
    const canvas = rect('.ge-main-canvas');
    const footer = rect('.ge-editor-footer');
    const sheet = rect('.ge-right-panel');
    return {
      canvasToFooter: Math.abs(canvas.bottom - footer.top),
      footerToSheet: Math.abs(footer.bottom - sheet.top),
      footerLeft: footer.left,
      footerRight: footer.right,
      sheetTop: sheet.top,
    };
  });
}

test('mobile footer docks flush between the canvas and the layers sheet', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Mobile footer dock');
  await page.locator('.ge-tour-close').click({ timeout: 500 }).catch(() => {});

  await expect(page.locator('.ge-editor-footer')).toHaveClass(/ge-footer-floating/);
  await expect(page.locator('.ge-layers-grab')).toBeHidden();

  // Wait until the dock has settled: canvas bottom == footer top == sheet top.
  await expect.poll(async () => {
    const g = await gaps(page);
    return g.canvasToFooter <= 2 && g.footerToSheet <= 2;
  }, { timeout: 10_000 }).toBe(true);

  const g = await gaps(page);
  expect(g.canvasToFooter).toBeLessThanOrEqual(2);
  expect(g.footerToSheet).toBeLessThanOrEqual(2);
  expect(g.footerLeft).toBeLessThanOrEqual(1);
  expect(Math.abs(g.footerRight - 390)).toBeLessThanOrEqual(1);
});
