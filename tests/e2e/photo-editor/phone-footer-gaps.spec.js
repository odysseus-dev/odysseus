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

test('phone footer controls for zoom, setup, and AI settings are accessible via normal clicks', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Mobile footer controls');
  await page.locator('.ge-tour-close').click({ timeout: 500 }).catch(() => {});

  const footer = page.locator('.ge-editor-footer');
  await expect(footer).toBeVisible();

  const setupBtn = page.locator('#ge-setup-btn');
  const aiBtn = page.locator('#ge-ai-settings-btn');
  const zoomIn = page.locator('#ge-zoom-in');
  const zoomOut = page.locator('#ge-zoom-out');
  const zoomFit = page.locator('#ge-zoom-100');

  await expect(setupBtn).toBeVisible();
  await expect(aiBtn).toBeVisible();
  await expect(zoomIn).toBeVisible();
  await expect(zoomOut).toBeVisible();
  await expect(zoomFit).toBeVisible();

  // Test zoom controls via normal clicks
  const zoomLabel = page.locator('.ge-zoom-label');
  const initialZoom = await zoomLabel.textContent();
  await zoomIn.click();
  await expect(zoomLabel).not.toHaveText(initialZoom);
  await zoomFit.click();
  await expect(zoomLabel).toHaveText(/%/);

  // Test Setup modal accessibility via normal click
  await setupBtn.click();
  const preflight = page.locator('.ge-preflight');
  await expect(preflight).toBeVisible();
  await page.locator('.ge-preflight-start').click();
  await expect(preflight).toBeHidden();

  // Test AI Settings modal accessibility via normal click
  await aiBtn.click();
  const aiSettings = page.locator('.ge-ai-settings');
  await expect(aiSettings).toBeVisible();
  await page.locator('.ge-ai-settings-done').click();
  await expect(aiSettings).toBeHidden();
});
