const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
const {
  compareExportPixels,
  encodedImagePixelDigest,
  editorState,
  flattenedPixelDigest,
  openBlankEditor,
  openExportDialog,
  reopenDraft,
  waitForDraft,
} = require('./helpers.js');

async function seedGradient(page) {
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    const gradient = layer.ctx.createLinearGradient(0, 0, layer.canvas.width, layer.canvas.height);
    gradient.addColorStop(0, '#d43f67');
    gradient.addColorStop(0.5, '#4aa884');
    gradient.addColorStop(1, '#3868d4');
    layer.ctx.fillStyle = gradient;
    layer.ctx.fillRect(0, 0, layer.canvas.width, layer.canvas.height);
    window.galleryEditorComposite?.();
  });
}

async function downloadFormat(page, format, filename) {
  await openExportDialog(page);
  await page.locator(`[data-format="${format}"]`).click();
  await page.locator('#ge-export-filename').fill(filename);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('.ge-export-dialog button[type="submit"]').click();
  const download = await downloadPromise;
  return {
    name: download.suggestedFilename(),
    bytes: fs.readFileSync(await download.path()),
  };
}

async function addAdjustment(page, type) {
  await page.locator('#ge-add-layer').click();
  await expect(page.locator('.ge-add-layer-menu')).toBeVisible();
  await page.locator(`.ge-add-layer-menu [data-adjustment-type="${type}"]`).click();
  await expect(page.locator('.ge-adj-popup')).toBeVisible();
}

async function exportCurrentPng(page) {
  const dataUrl = await page.evaluate(async () => {
    const editor = await import('/static/js/galleryEditor.js');
    return editor.exportPNG();
  });
  return Buffer.from(dataUrl.split(',', 2)[1], 'base64');
}

test('every retained adjustment family exports the visible composite exactly', async ({ page }) => {
  await openBlankEditor(page, { width: 128, height: 96 }, 'Adjustment export matrix');
  await seedGradient(page);

  const cases = [
    ['brightness-contrast', 'brightness', '35'],
    ['exposure', 'exposure', '35'],
    ['white-balance', 'temperature', '30'],
    ['hue-saturation', 'hue', '30'],
    ['vibrance', 'vibrance', '45'],
    ['black-white', 'red', '60'],
    ['shadows-highlights', 'shadows', '35'],
    ['levels', 'inBlack', '20'],
    ['color-balance', 'shadows-r', '35'],
    ['selective-color', 'cyan', '30'],
  ];

  for (const [type, key, value] of cases) {
    await addAdjustment(page, type);
    const control = page.locator(`.ge-adj-row input[data-key="${key}"]`);
    await control.fill(value);
    await control.dispatchEvent('input');
    await page.locator('[data-adj-action="ok"]').click();
    const visibleDigest = await flattenedPixelDigest(page);
    expect(await encodedImagePixelDigest(page, await exportCurrentPng(page))).toEqual(visibleDigest);
  }

  await addAdjustment(page, 'curves');
  const curve = page.locator('.ge-curves-canvas');
  const curveBox = await curve.boundingBox();
  await page.mouse.click(curveBox.x + curveBox.width * 0.5, curveBox.y + curveBox.height * 0.28);
  await page.locator('[data-adj-action="ok"]').click();
  const curveDigest = await flattenedPixelDigest(page);
  expect(await encodedImagePixelDigest(page, await exportCurrentPng(page))).toEqual(curveDigest);

  await addAdjustment(page, 'gradient-map');
  await page.locator('[data-gradient-key="shadows"]').fill('#102030');
  await page.locator('[data-gradient-key="shadows"]').dispatchEvent('input');
  await page.locator('[data-adj-action="ok"]').click();
  const gradientDigest = await flattenedPixelDigest(page);
  expect(await encodedImagePixelDigest(page, await exportCurrentPng(page))).toEqual(gradientDigest);
});

test('composited correction exports preserve pixels and format metadata', async ({ page }) => {
  await openBlankEditor(page, { width: 160, height: 120 }, 'Export fidelity');
  await seedGradient(page);

  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-adjustment-type="exposure"]').click();
  await page.locator('.ge-adj-row input[data-key="exposure"]').fill('35');
  await page.locator('.ge-adj-row input[data-key="exposure"]').dispatchEvent('input');
  await page.locator('[data-adj-action="ok"]').click();
  const renderedPixels = await flattenedPixelDigest(page);

  const png = await downloadFormat(page, 'png', 'correction');
  expect(png.name).toBe('correction.png');
  expect(png.bytes.subarray(1, 4).toString('ascii')).toBe('PNG');
  expect(png.bytes.readUInt32BE(16)).toBe(160);
  expect(png.bytes.readUInt32BE(20)).toBe(120);
  expect(await encodedImagePixelDigest(page, png.bytes)).toEqual(renderedPixels);

  const jpeg = await downloadFormat(page, 'jpeg', 'correction-jpeg');
  expect(jpeg.name).toBe('correction-jpeg.jpg');
  expect(jpeg.bytes[0]).toBe(0xff);
  expect(jpeg.bytes[1]).toBe(0xd8);

  const webp = await downloadFormat(page, 'webp', 'correction-webp');
  expect(webp.name).toBe('correction-webp.webp');
  expect(webp.bytes.subarray(0, 4).toString('ascii')).toBe('RIFF');
  expect(webp.bytes.subarray(8, 12).toString('ascii')).toBe('WEBP');

  const comparison = await compareExportPixels(page, png.bytes, { width: 160, height: 120 });
  expect(comparison.meanAbsoluteError).toBe(0);
  expect(comparison.maximumError).toBe(0);

  const exposure = (await editorState(page)).layers.find(layer => layer.kind === 'adjustment');
  const exposureRow = page.locator(`.ge-layer-item[data-layer-id="${exposure.id}"]`);
  await exposureRow.locator('.ge-layer-opacity').fill('62');
  await exposureRow.locator('.ge-layer-opacity').dispatchEvent('input');
  const opacityState = await editorState(page);
  expect(opacityState.layers.find(layer => layer.id === exposure.id).opacity).toBeCloseTo(.62, 2);
  const opacityPixels = await flattenedPixelDigest(page);

  const opacityPng = await downloadFormat(page, 'png', 'correction-opacity');
  expect(await encodedImagePixelDigest(page, opacityPng.bytes)).toEqual(opacityPixels);
  expect(await compareExportPixels(page, opacityPng.bytes, { width: 160, height: 120 })).toEqual({
    meanAbsoluteError: 0,
    maximumError: 0,
    changedPixelRatio: 0,
  });

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(layer => layer.id === exposure.id).opacity).toBeCloseTo(.62, 2);
  expect(await flattenedPixelDigest(page)).toEqual(opacityPixels);
});

test('export preview clears stale matte pixels when transparency changes', async ({ page }) => {
  await openBlankEditor(page, { width: 160, height: 120 }, 'Export preview');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    for (const layer of state.layers) layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.fillStyle = '#2671c8';
    layer.ctx.fillRect(40, 30, 80, 60);
    window.galleryEditorComposite?.();
  });

  await openExportDialog(page);
  const previewPixel = () => page.locator('.ge-export-preview').evaluate(canvas => (
    [...canvas.getContext('2d').getImageData(0, 0, 1, 1).data]
  ));
  expect(await previewPixel()).toEqual([0, 0, 0, 0]);

  await page.locator('#ge-export-transparency').uncheck();
  await page.locator('#ge-export-matte').click();
  await page.locator('.cp-hex').fill('#d94747');
  await page.locator('.cp-hex').press('Enter');
  expect(await previewPixel()).toEqual([217, 71, 71, 255]);

  await page.locator('#ge-export-transparency').check();
  expect(await previewPixel()).toEqual([0, 0, 0, 0]);
  await page.locator('.ge-export-close').click();
});

test('closing export returns focus to the launch control', async ({ page }) => {
  await openBlankEditor(page, { width: 160, height: 120 }, 'Export focus');
  const launch = page.locator('#ge-save-menu-btn');
  await launch.focus();
  await launch.click();
  await page.locator('#ge-download').click();
  await expect(page.locator('.ge-export-dialog')).toBeVisible();
  await page.locator('.ge-export-close').click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('ge-save-menu-btn');
});
