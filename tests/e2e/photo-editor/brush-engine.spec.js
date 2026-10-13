const { test, expect } = require('@playwright/test');
const { editorState, flattenedPixelDigest, openBlankEditor } = require('./helpers.js');

async function canvasPoint(page, xRatio, yRatio) {
  const box = await page.locator('.ge-main-canvas').boundingBox();
  return { x: box.x + box.width * xRatio, y: box.y + box.height * yRatio };
}

test('brush presets and shared stroke controls remain reusable', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await expect(page.locator('#ge-brush-spacing')).toBeVisible();
  await page.locator('#ge-brush-spacing').fill('28');
  await page.locator('#ge-brush-smoothing').fill('52');
  await page.locator('#ge-brush-blend').selectOption('multiply');
  await page.locator('.ge-pressure-option').filter({ hasText: 'Opacity' }).locator('.toggle-slider').click();
  await expect(page.locator('#ge-pressure-opacity')).toBeChecked();

  page.once('dialog', dialog => dialog.accept('My Detail Brush'));
  await page.locator('#ge-brush-preset-save').click();
  await expect(page.locator('#ge-brush-preset option', { hasText: 'My Detail Brush' })).toHaveCount(1);

  await page.locator('#ge-brush-spacing').fill('5');
  await page.locator('#ge-brush-preset').selectOption({ label: 'My Detail Brush' });
  await expect(page.locator('#ge-brush-spacing')).toHaveValue('28');
  await expect(page.locator('#ge-brush-smoothing')).toHaveValue('52');
  await expect(page.locator('#ge-brush-blend')).toHaveValue('multiply');
  await page.locator('#ge-brush-preset-delete').click();
  await expect(page.locator('#ge-brush-preset option', { hasText: 'My Detail Brush' })).toHaveCount(0);
});

test('long sampled strokes stay responsive and undo atomically', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.locator('.ge-size-slider').fill('700');
  await page.locator('#ge-brush-spacing').fill('8');
  await page.locator('#ge-brush-smoothing').fill('65');
  const activeLayerId = (await editorState(page)).activeLayerId;
  const thumb = page.locator(`.ge-layer-item[data-layer-id="${activeLayerId}"] .ge-layer-inline-thumb`);
  const thumbBefore = await thumb.evaluate(canvas => canvas.toDataURL());
  const before = await flattenedPixelDigest(page);
  const start = await canvasPoint(page, 0.08, 0.5);
  const end = await canvasPoint(page, 0.92, 0.5);
  const startedAt = Date.now();
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 240 });
  await page.mouse.up();
  expect(Date.now() - startedAt).toBeLessThan(5000);
  const after = await flattenedPixelDigest(page);
  expect(after).not.toEqual(before);
  await expect(thumb).toHaveAttribute('width', '68');
  expect(await thumb.evaluate(canvas => canvas.toDataURL())).not.toEqual(thumbBefore);
  await page.locator('#ge-undo').click();
  expect(await flattenedPixelDigest(page)).toEqual(before);
  await page.locator('#ge-redo').click();
  expect(await flattenedPixelDigest(page)).toEqual(after);
});

test('eyedropper and retouch tools use the editor stroke lifecycle', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.locator('.ge-color-picker').first().evaluate((input) => {
    input.value = '#33aa55';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const sample = await canvasPoint(page, 0.3, 0.3);
  await page.mouse.click(sample.x, sample.y);

  await page.locator('.ge-tool-btn[data-tool="eyedropper"]').click();
  await expect(page.locator('#ge-eyedropper-section')).toBeVisible();
  await page.locator('#ge-eyedropper-sample').selectOption('composite');
  await page.mouse.move(sample.x, sample.y);
  await expect(page.locator('#ge-eyedropper-live-value')).toHaveText('#33aa55');
  await expect(page.locator('#ge-eyedropper-live-rgb')).toHaveText('RGB 51 170 85');
  await expect(page.locator('#ge-eyedropper-live-hsl')).toHaveText('HSL 137 54% 43%');
  await expect(page.locator('#ge-eyedropper-live-swatch')).not.toHaveClass(/empty/);
  await expect(page.locator('#ge-eyedropper-loupe')).toBeVisible();
  expect((await page.locator('#ge-eyedropper-loupe').evaluate(canvas => canvas.getContext('2d').getImageData(42, 42, 1, 1).data[3]))).toBe(255);
  await page.mouse.click(sample.x, sample.y);
  await expect(page.locator('.ge-color-picker').first()).toHaveValue('#33aa55');

  for (const tool of ['dodge', 'burn']) {
    const before = await flattenedPixelDigest(page);
    await page.locator(`.ge-tool-btn[data-tool="${tool}"]`).click();
    const from = await canvasPoint(page, 0.35, tool === 'dodge' ? 0.45 : 0.6);
    const to = await canvasPoint(page, 0.65, tool === 'dodge' ? 0.45 : 0.6);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 20 });
    await page.mouse.up();
    expect((await editorState(page)).layers.length).toBeGreaterThan(0);
    await page.locator('#ge-undo').click();
    expect(await flattenedPixelDigest(page)).toEqual(before);
  }

  const blemish = await canvasPoint(page, 0.72, 0.3);
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.locator('.ge-color-picker').first().evaluate((input) => {
    input.value = '#111111';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.mouse.click(blemish.x, blemish.y);
  await page.locator('.ge-tool-btn[data-tool="heal"]').click();
  const beforeHeal = await flattenedPixelDigest(page);
  const healTarget = blemish;
  await page.mouse.move(healTarget.x, healTarget.y);
  await page.mouse.down();
  await page.mouse.move(healTarget.x + 50, healTarget.y, { steps: 12 });
  await page.mouse.up();
  expect(await flattenedPixelDigest(page)).not.toEqual(beforeHeal);
  await page.locator('#ge-undo').click();
  expect(await flattenedPixelDigest(page)).toEqual(beforeHeal);
});

test('healing brush can use an optional sampled source', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Sampled healing E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.locator('.ge-size-slider').fill('420');
  await page.locator('.ge-color-picker').first().evaluate((input) => {
    input.value = '#d24b62';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const source = await canvasPoint(page, 0.28, 0.5);
  await page.mouse.click(source.x, source.y);

  await page.locator('.ge-tool-btn[data-tool="heal"]').click();
  await page.keyboard.down('Alt');
  await page.mouse.click(source.x, source.y);
  await page.keyboard.up('Alt');
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.cloneSourceX !== null && state.cloneSourceY !== null;
  })).toBe(true);
  const target = await canvasPoint(page, 0.72, 0.5);
  await page.mouse.click(target.x, target.y);

  const result = await page.evaluate(async ({ xRatio, yRatio }) => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    const x = Math.round(layer.canvas.width * xRatio);
    const y = Math.round(layer.canvas.height * yRatio);
    const point = layer.ctx.getImageData(x, y, 1, 1).data;
    return { hasSource: !!state.cloneSourceSnapshot, pixel: [...point] };
  }, { xRatio: 0.72, yRatio: 0.5 });
  expect(result.hasSource).toBe(true);
  expect(result.pixel[0]).toBeGreaterThan(80);
  expect(result.pixel[3]).toBeGreaterThan(0);
  await expect(page.locator('#ge-clone-source-label')).toHaveText('Active layer sampled');
  await page.locator('#ge-clone-source-clear').click();
  await expect(page.locator('#ge-clone-source-label')).toHaveText('No source selected');
});

test('smudge carries nearby pixels along a stroke and undoes atomically', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Smudge E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.locator('.ge-size-slider').fill('700');
  await page.locator('.ge-color-picker').first().evaluate((input) => {
    input.value = '#d24b62';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const source = await canvasPoint(page, 0.28, 0.5);
  const target = await canvasPoint(page, 0.55, 0.5);
  await page.mouse.click(source.x, source.y);

  const before = await page.evaluate(() => {
    const canvas = document.querySelector('.ge-main-canvas');
    const x = Math.round(canvas.width * 0.55);
    const y = Math.round(canvas.height * 0.5);
    return [...canvas.getContext('2d').getImageData(x, y, 1, 1).data];
  });
  await page.locator('.ge-tool-btn[data-tool="smudge"]').click();
  await expect(page.locator('#ge-smudge-strength-row')).toBeVisible();
  await page.mouse.move(source.x, source.y);
  await page.mouse.down();
  await page.mouse.move(target.x, target.y, { steps: 20 });
  await page.mouse.up();
  const after = await page.evaluate(() => {
    const canvas = document.querySelector('.ge-main-canvas');
    const x = Math.round(canvas.width * 0.55);
    const y = Math.round(canvas.height * 0.5);
    const ctx = canvas.getContext('2d');
    return [...ctx.getImageData(x, y, 1, 1).data];
  });
  expect(before[0]).toBeGreaterThan(220);
  expect(before[3]).toBe(255);
  expect(after[0]).toBeLessThan(220);
  expect(after[2]).toBeLessThan(220);
  expect(after[3]).toBeGreaterThan(0);
  await page.locator('#ge-undo').click();
  expect(await page.evaluate(() => {
    const canvas = document.querySelector('.ge-main-canvas');
    const x = Math.round(canvas.width * 0.55);
    const y = Math.round(canvas.height * 0.5);
    return canvas.getContext('2d').getImageData(x, y, 1, 1).data[3];
  })).toBe(255);
});
