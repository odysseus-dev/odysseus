const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, selectTool } = require('./helpers.js');

test('paint tools require confirmation before rasterizing text layers, preserving text on cancel', async ({ page }) => {
  await openBlankEditor(page);
  await selectTool(page, 'text');
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(canvas.x + 80, canvas.y + 80);
  const text = page.locator('.ge-direct-text-editor');
  await text.fill('Keep editable');
  await text.press('Control+Enter');
  const retained = (await editorState(page)).layers.find(layer => layer.kind === 'text');
  expect(retained).toBeDefined();
  const kind = async () => (await editorState(page)).layers.find(layer => layer.id === retained.id)?.kind;

  // Tool selection alone does not prompt and does not rasterize
  for (const tool of ['brush', 'eraser']) {
    await selectTool(page, tool);
    await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
    expect(await kind()).toBe('text');
  }

  // Attempting to draw triggers confirmation
  await selectTool(page, 'brush');
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.25, y: 0.2 });
  await expect(page.locator('#styled-confirm-overlay')).toBeVisible();

  // Cancelling keeps text editable with no changes
  await page.locator('#styled-confirm-cancel').click();
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
  expect(await kind()).toBe('text');

  // Second stroke prompt confirmed -> rasterizes layer
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.25, y: 0.2 });
  await expect(page.locator('#styled-confirm-overlay')).toBeVisible();
  await page.locator('#styled-confirm-ok').click();
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
  expect(await kind()).toBe('raster');

  // Subsequent strokes on the raster layer draw immediately without prompting
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.35, y: 0.3 });
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();

  // Undo restores editable text
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+z');
  expect(await kind()).toBe('text');
});
