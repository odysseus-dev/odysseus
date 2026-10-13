const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers.js');

test('brush cursor matches the rendered brush diameter and replaces the crosshair', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();

  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 80;
  });

  const canvas = page.locator('.ge-main-canvas');
  const canvasBox = await canvas.boundingBox();
  await page.mouse.move(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);

  const cursor = page.locator('.ge-brush-cursor');
  await expect(cursor).toBeVisible();
  await expect(canvas).toHaveCSS('cursor', 'none');

  const measurements = await page.evaluate(() => {
    const canvasEl = document.querySelector('.ge-main-canvas');
    const cursorEl = document.querySelector('.ge-brush-cursor');
    const canvasRect = canvasEl.getBoundingClientRect();
    const cursorRect = cursorEl.getBoundingClientRect();
    return {
      expectedWidth: 80 * canvasRect.width / canvasEl.width,
      expectedHeight: 80 * canvasRect.height / canvasEl.height,
      cursorWidth: cursorRect.width,
      cursorHeight: cursorRect.height,
      cursorZ: Number(getComputedStyle(cursorEl).zIndex),
      galleryZ: Number(getComputedStyle(document.getElementById('gallery-modal')).zIndex || 0),
    };
  });

  expect(measurements.cursorWidth).toBeCloseTo(measurements.expectedWidth, 1);
  expect(measurements.cursorHeight).toBeCloseTo(measurements.expectedHeight, 1);
  expect(measurements.cursorZ).toBeGreaterThan(measurements.galleryZ);

  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 160;
  });
  await page.mouse.move(canvasBox.x + canvasBox.width / 2 + 1, canvasBox.y + canvasBox.height / 2);
  await expect.poll(async () => (await cursor.boundingBox()).width)
    .toBeCloseTo(measurements.expectedWidth * 2, 1);
});
