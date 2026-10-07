const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, waitForDraft } = require('./helpers.js');

test('active editor draft reopens after a hard refresh', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 180 }, 'Refresh recovery E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.2, y: 0.3 }, { x: 0.8, y: 0.7 });
  const before = await editorState(page);
  const draftId = await waitForDraft(page);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#gallery-editor-tab')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.gallery-editor')).toBeVisible({ timeout: 20_000 });
  await expect.poll(async () => (await editorState(page)).draftId, { timeout: 20_000 }).toBe(draftId);
  await expect.poll(async () => (await editorState(page)).documentRenderReady, { timeout: 20_000 }).toBe(true);

  const after = await editorState(page);
  expect(after.layers).toEqual(before.layers);
  expect(after.dimensions).toEqual(before.dimensions);
});
