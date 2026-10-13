const { test, expect } = require('@playwright/test');
const { editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

async function sampleComposite(page) {
  return page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const ctx = state.documentCompositeCanvas.getContext('2d');
    return {
      left: [...ctx.getImageData(40, 50, 1, 1).data],
      right: [...ctx.getImageData(160, 50, 1, 1).data],
    };
  });
}

test('clipping mask uses base alpha and survives undo, redo, and server reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 200, height: 100 }, 'Clipping E2E');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const base = state.layers.find(layer => layer.name === 'Background');
    const color = state.layers.find(layer => layer.name === 'Edit');
    for (const layer of [base, color]) layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    base.ctx.fillStyle = '#ff0000';
    base.ctx.fillRect(0, 0, 100, 100);
    color.ctx.fillStyle = '#0000ff';
    color.ctx.fillRect(0, 0, 200, 100);
  });

  const editRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  await page.locator('#ge-layer-tools .ge-layer-clip-btn').click();
  await expect(editRow).toHaveClass(/clipped/);
  await expect(editRow.locator('.ge-layer-clipped-marker')).toBeVisible();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').clipped).toBe(true);
  let pixels = await sampleComposite(page);
  expect(pixels.left).toEqual([0, 0, 255, 255]);
  expect(pixels.right[3]).toBe(0);

  await page.locator('#ge-undo').click();
  expect((await sampleComposite(page)).right).toEqual([0, 0, 255, 255]);
  await page.locator('#ge-redo').click();
  expect((await sampleComposite(page)).right[3]).toBe(0);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').clipped).toBe(true);
  expect((await sampleComposite(page)).right[3]).toBe(0);
  await expect(page.locator('.ge-layer-item.clipped .ge-layer-clipped-marker')).toBeVisible();

  await page.locator('#ge-layer-tools button[title="Merge down into layer below"]').click();
  expect((await editorState(page)).layers).toHaveLength(1);
  expect((await sampleComposite(page)).right[3]).toBe(0);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').clipped).toBe(true);

  await page.locator('#ge-layer-tools .ge-layer-clip-btn').click();
  expect((await sampleComposite(page)).right).toEqual([0, 0, 255, 255]);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
