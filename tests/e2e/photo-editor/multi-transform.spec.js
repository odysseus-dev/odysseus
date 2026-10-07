const { test, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const { editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

function selectionBounds(layers) {
  const left = Math.min(...layers.map(layer => layer.offset.x));
  const top = Math.min(...layers.map(layer => layer.offset.y));
  const right = Math.max(...layers.map(layer => layer.offset.x + layer.size[0]));
  const bottom = Math.max(...layers.map(layer => layer.offset.y + layer.size[1]));
  return { left, top, right, bottom, width: right - left, height: bottom - top, centerX: (left + right) / 2, centerY: (top + bottom) / 2 };
}

test('shared transform preserves relative layout, masks, retained text, undo, cancel, and reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 400, height: 300 }, 'Multi-transform E2E');
  const editRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  await editRow.click();
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();

  await page.locator('.ge-tool-btn[data-tool="text"]').click();
  const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(canvasBox.x + canvasBox.width * 0.35, canvasBox.y + canvasBox.height * 0.25);
  await page.locator('#ge-text-content').fill('Transform together');
  await page.locator('#ge-text-size').fill('30');
  await page.locator('#ge-text-size').press('Enter');

  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  await editRow.click();
  await page.locator('#ge-layer-tools .ge-layer-clip-btn').click();
  await editRow.click();
  await backgroundRow.click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(1);

  await page.locator('#ge-select-all-layers').click();
  const initial = await editorState(page);
  expect(initial.selectedLayerIds).toHaveLength(3);
  const bounds = selectionBounds(initial.layers);

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('.ge-transform-popup .ge-adj-title')).toHaveText('Transform 3 layers');
  expect(Number(await page.locator('#ge-transform-w').inputValue())).toBe(bounds.width);
  expect(Number(await page.locator('#ge-transform-h').inputValue())).toBe(bounds.height);
  await page.locator('#ge-transform-w').fill(String(bounds.width * 2));
  expect(Number(await page.locator('#ge-transform-h').inputValue())).toBe(bounds.height * 2);
  await page.locator('#ge-transform-apply').click();

  const transformed = await editorState(page);
  const initialByName = new Map(initial.layers.map(layer => [layer.name, layer]));
  for (const layer of transformed.layers.filter(layer => layer.kind === 'raster')) {
    const before = initialByName.get(layer.name);
    expect(layer.size).toEqual([before.size[0] * 2, before.size[1] * 2]);
    const expectedX = Math.round(bounds.centerX + (before.offset.x + before.size[0] / 2 - bounds.centerX) * 2 - layer.size[0] / 2);
    const expectedY = Math.round(bounds.centerY + (before.offset.y + before.size[1] / 2 - bounds.centerY) * 2 - layer.size[1] / 2);
    expect(layer.offset).toEqual({ x: expectedX, y: expectedY });
  }
  const transformedEdit = transformed.layers.find(layer => layer.name === 'Edit');
  expect(transformedEdit.clipped).toBe(true);
  expect(transformed.groups).toEqual(initial.groups);
  expect(transformed.layers.map(layer => layer.id)).toEqual(initial.layers.map(layer => layer.id));
  expect(transformedEdit.masks[0].size).toEqual(transformedEdit.size);
  const beforeText = initial.layers.find(layer => layer.kind === 'text');
  const afterText = transformed.layers.find(layer => layer.kind === 'text');
  expect(afterText.text.content).toBe('Transform together');
  expect(afterText.text.transform.scaleX).toBeCloseTo(beforeText.text.transform.scaleX * 2, 5);
  expect(afterText.text.transform.scaleY).toBeCloseTo(beforeText.text.transform.scaleY * 2, 5);

  await page.locator('#ge-undo').click();
  const undone = await editorState(page);
  expect(undone.layers).toEqual(initial.layers);
  expect(undone.selectedLayerIds).toEqual(initial.selectedLayerIds);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers).toEqual(transformed.layers);

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  const secondWidth = await page.locator('#ge-transform-w').inputValue();
  await page.locator('#ge-transform-rot-90').click();
  await expect(page.locator('#ge-transform-rot')).toHaveValue('90');
  await expect.poll(async () => {
    const edit = (await editorState(page)).layers.find(layer => layer.name === 'Edit');
    return edit.size;
  }).toEqual([transformedEdit.size[1], transformedEdit.size[0]]);
  await page.locator('#ge-transform-flip-h').click();
  await expect(page.locator('#ge-transform-w')).toHaveValue(`-${secondWidth}`);
  await page.locator('#ge-transform-cancel-btn').click();
  const cancelled = await editorState(page);
  expect(cancelled.layers).toEqual(transformed.layers);
  expect(cancelled.groups).toEqual(transformed.groups);
  expect(cancelled.redo).toBe(0);

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-rot-90').click();
  await expect(page.locator('#ge-transform-rot')).toHaveValue('90');
  await page.locator('#ge-transform-flip-h').click();
  await page.locator('#ge-transform-apply').click();
  const finalTransform = await editorState(page);
  const finalText = finalTransform.layers.find(layer => layer.kind === 'text');
  expect(finalText.text.content).toBe('Transform together');
  expect(finalText.text.transform.rotation).toBeCloseTo(afterText.text.transform.rotation + 90, 5);
  expect(finalText.text.transform.flipH).toBe(!afterText.text.transform.flipH);
  expect(finalTransform.groups).toEqual(transformed.groups);
  expect(finalTransform.layers.find(layer => layer.name === 'Edit').clipped).toBe(true);

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#ge-save-menu-btn').click();
  await page.locator('#ge-save-project').click();
  const download = await downloadPromise;
  const projectBuffer = await fs.readFile(await download.path());
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers).toEqual(transformed.layers);
  await page.locator('#ge-save-menu-btn').click();
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-load-project').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: 'transform-roundtrip.geproj.json',
    mimeType: 'application/json',
    buffer: projectBuffer,
  });
  await expect.poll(async () => (await editorState(page)).layers).toEqual(finalTransform.layers);
  expect((await editorState(page)).groups).toEqual(finalTransform.groups);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  expect((await editorState(page)).layers).toEqual(finalTransform.layers);
  expect((await editorState(page)).groups).toEqual(finalTransform.groups);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
