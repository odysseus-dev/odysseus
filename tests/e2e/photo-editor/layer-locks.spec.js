const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

test('independent layer locks enforce operations and survive undo and reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 360, height: 260 }, 'Layer locks E2E');
  const editRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  const lockButton = editRow.locator('.ge-layer-lock-btn');
  const opacityRow = editRow.locator('.ge-layer-opacity-row');
  const lockBox = await lockButton.boundingBox();
  const opacityBox = await opacityRow.boundingBox();
  expect(lockBox).toBeTruthy();
  expect(opacityBox).toBeTruthy();
  expect(lockBox.y + lockBox.height).toBeLessThanOrEqual(opacityBox.y + 1);

  const toggleLock = async type => {
    await lockButton.click();
    const menu = page.locator('#ge-layer-lock-menu');
    await expect(menu).toBeVisible();
    await menu.locator(`[data-lock-type="${type}"]`).click();
  };

  await toggleLock('position');
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').locks.position).toBe(true);
  await expect(lockButton).toHaveClass(/active/);

  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await dragOnCanvas(page, { x: 0.35, y: 0.35 }, { x: 0.55, y: 0.50 });
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').offset).toEqual({ x: 0, y: 0 });

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').locks.position).toBe(false);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').locks.position).toBe(true);
  await toggleLock('position');

  await toggleLock('pixels');
  const emptyHash = (await editorState(page)).layers.find(layer => layer.name === 'Edit').pixelHash;
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.42, y: 0.42 }, { x: 0.58, y: 0.50 });
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').pixelHash).toBe(emptyHash);
  await toggleLock('pixels');

  await toggleLock('transparency');
  await dragOnCanvas(page, { x: 0.42, y: 0.42 }, { x: 0.58, y: 0.50 });
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').pixelHash).toBe(emptyHash);
  await toggleLock('transparency');

  await dragOnCanvas(page, { x: 0.42, y: 0.42 }, { x: 0.58, y: 0.50 });
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').pixelHash).not.toBe(emptyHash);

  await toggleLock('pixels');
  await toggleLock('transparency');
  await toggleLock('position');
  const beforeReopen = await editorState(page);
  const expectedLocks = beforeReopen.layers.find(layer => layer.name === 'Edit').locks;
  expect(expectedLocks).toEqual({ pixels: true, transparency: true, position: true });

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(layer => layer.name === 'Edit').locks).toEqual(expectedLocks);
  await expect(page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first().locator('.ge-layer-lock-btn')).toHaveClass(/active/);

  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
