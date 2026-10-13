const { test, expect } = require('@playwright/test');
const { editorState, flattenedPixelDigest, openBlankEditor } = require('./helpers.js');

async function seedVisibleLayers(page) {
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const background = state.layers.find(layer => layer.name === 'Background');
    const edit = state.layers.find(layer => layer.name === 'Edit');
    background.ctx.fillStyle = '#26354f';
    background.ctx.fillRect(0, 0, background.canvas.width, background.canvas.height);
    edit.ctx.fillStyle = '#cf5b4a';
    edit.ctx.fillRect(48, 32, edit.canvas.width - 96, edit.canvas.height - 64);
    window.galleryEditorComposite?.();
  });
}

test('Merge all preserves retained effects and adjustment output', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Merge fidelity E2E');
  await seedVisibleLayers(page);

  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-color-overlay"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[type="color"]').fill('#f0c04a');
  await page.locator('.ge-filter-row input[data-key="opacity"]').fill('35');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();

  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-adjustment-type="exposure"]').click();
  await expect(page.locator('.ge-adj-popup')).toBeVisible();
  await page.locator('.ge-adj-row input[data-key="exposure"]').fill('45');
  await page.locator('.ge-adj-row input[data-key="exposure"]').dispatchEvent('input');
  await page.locator('[data-adj-action="ok"]').click();

  const before = await flattenedPixelDigest(page);
  const beforeState = await editorState(page);
  expect(beforeState.layers.some(layer => layer.effects.length)).toBe(true);
  expect(beforeState.layers.some(layer => layer.kind === 'adjustment')).toBe(true);

  await page.locator('#ge-merge-all').click();
  await expect.poll(async () => (await editorState(page)).layers.length).toBe(1);
  const afterState = await editorState(page);
  expect(afterState.layers[0].effects).toHaveLength(0);
  expect(afterState.layers).toEqual([
    expect.objectContaining({ kind: 'raster', effects: [] }),
  ]);
  expect(await flattenedPixelDigest(page)).toEqual(before);
});
