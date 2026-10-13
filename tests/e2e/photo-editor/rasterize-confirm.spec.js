const { test, expect } = require('@playwright/test');
const { openBlankEditor, editorState } = require('./helpers.js');

test('paint tools offer rasterization, cancel preserves text, Enter accepts and undo restores it', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="text"]').click();
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(canvas.x + 80, canvas.y + 80);
  const text = page.locator('.ge-direct-text-editor');
  await text.fill('Keep editable');
  await text.press('Control+Enter');
  const retained = (await editorState(page)).layers.find(layer => layer.kind === 'text');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await expect(page.locator('#styled-confirm-ok')).toHaveText('Rasterize');
  await page.keyboard.press('Escape');
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
  expect((await editorState(page)).layers.find(layer => layer.id === retained.id).kind).toBe('text');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await expect(page.locator('#styled-confirm-ok')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
  expect((await editorState(page)).layers.find(layer => layer.id === retained.id).kind).toBe('raster');
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers.find(layer => layer.id === retained.id).kind).toBe('text');
  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  await expect(page.locator('#styled-confirm-ok')).toBeVisible();
  await page.keyboard.press('Escape');
});
