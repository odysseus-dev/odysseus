const { test, expect } = require('@playwright/test');
const { openBlankEditor, selectTool } = require('./helpers.js');

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test('minimizing layers restores fit view and keeps an explicit zoom', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  const canvas = page.locator('.ge-main-canvas');
  const zoom = () => page.evaluate(async () => (await import('/static/js/editor/state.js')).state.zoom);
  await page.locator('.ge-layers-title').click();
  await expect(page.locator('.ge-right-panel')).toHaveClass(/expanded/);
  await selectTool(page, 'eraser');
  await selectTool(page, 'eraser');
  await expect(page.locator('.ge-right-panel')).toHaveClass(/minimized/);
  await expect.poll(async () => (await canvas.boundingBox()).width).toBeGreaterThan(200);

  await page.locator('.ge-layers-grab').click();
  await expect(page.locator('.ge-right-panel')).not.toHaveClass(/minimized/);
  await page.locator('#ge-zoom-in').evaluate(button => button.click());
  await expect.poll(() => page.evaluate(async () => (await import('/static/js/editor/state.js')).state.zoomViewMode)).toBe('scale');
  const manualZoom = await zoom();
  await page.locator('.ge-layers-title').click();
  await selectTool(page, 'eraser');
  await selectTool(page, 'eraser');
  await expect(page.locator('.ge-right-panel')).toHaveClass(/minimized/);
  await expect.poll(zoom).toBe(manualZoom);
});

test('minimizing layers keeps a pinch zoom', async ({ page, context, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP multi-touch injection');
  await openBlankEditor(page, { width: 320, height: 240 });
  const state = () => page.evaluate(async () => {
    const { state: editor } = await import('/static/js/editor/state.js');
    return { zoom: editor.zoom, mode: editor.zoomViewMode };
  });
  const fitted = await state();
  const box = await page.locator('.ge-main-canvas').boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const cdp = await context.newCDPSession(page);
  const fingers = spread => [{ x: cx - spread, y: cy, id: 1 }, { x: cx + spread, y: cy, id: 2 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: fingers(20) });
  for (const spread of [30, 40, 50]) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: fingers(spread) });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  const pinched = await state();
  expect(pinched.mode).toBe('scale');
  expect(pinched.zoom).toBeGreaterThan(fitted.zoom * 2);

  await page.locator('.ge-layers-title').click();
  await expect(page.locator('.ge-right-panel')).toHaveClass(/expanded/);
  await selectTool(page, 'eraser');
  await selectTool(page, 'eraser');
  await expect(page.locator('.ge-right-panel')).toHaveClass(/minimized/);
  await expect.poll(async () => (await state()).zoom).toBe(pinched.zoom);
});
