const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers');

test('layer mask link controls independent movement and survives reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Linked layer mask');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.2, y: 0.35 }, { x: 0.75, y: 0.6 });

  let current = await editorState(page);
  const layerId = current.activeLayerId;
  const layerRow = page.locator(`.ge-layer-item[data-layer-id="${layerId}"]`);
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  const maskRow = page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' });
  await expect(maskRow).toBeVisible();

  current = await editorState(page);
  const originalLayer = current.layers.find(layer => layer.id === layerId);
  expect(originalLayer.masks[0].linked).toBe(true);
  expect(originalLayer.masks[0].offset).toEqual({ x: 0, y: 0 });

  const originalMaskHash = originalLayer.masks[0].pixelHash;
  await maskRow.locator('button[title="Invert mask"]').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].pixelHash)
    .not.toBe(originalMaskHash);
  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].pixelHash)
    .toBe(originalMaskHash);

  await maskRow.locator('details.ge-mask-properties > summary').click();
  await maskRow.locator('input.ge-mask-density').fill('50');
  await maskRow.locator('input.ge-mask-density').dispatchEvent('change');
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].density).toBe(0.5);
  await maskRow.locator('input.ge-mask-feather').fill('12');
  await maskRow.locator('input.ge-mask-feather').dispatchEvent('change');
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].feather).toBe(12);
  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].density).toBe(1);
  expect(current.layers.find(layer => layer.id === layerId).masks[0].feather).toBe(0);

  await maskRow.locator('button[title="Inspect mask"]').click();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.maskInspectMode;
  })).toBe(true);
  const inspectedPixel = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return [...state.mainCtx.getImageData(Math.floor(state.imgWidth / 2), Math.floor(state.imgHeight / 2), 1, 1).data];
  });
  expect(inspectedPixel[0]).toBeGreaterThan(200);
  expect(inspectedPixel[1]).toBeGreaterThan(200);
  expect(inspectedPixel[2]).toBeGreaterThan(200);
  await page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' })
    .locator('button[title="Inspect mask"]').click();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.maskInspectMode;
  })).toBe(false);

  await maskRow.locator('.ge-mask-link-btn').click();
  await expect(maskRow.locator('.ge-mask-link-btn')).toHaveAttribute('aria-pressed', 'false');
  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await dragOnCanvas(page, { x: 0.45, y: 0.45 }, { x: 0.55, y: 0.5 });

  current = await editorState(page);
  let layer = current.layers.find(item => item.id === layerId);
  expect(layer.offset).toEqual(originalLayer.offset);
  expect(layer.pixelHash).toBe(originalLayer.pixelHash);
  expect(layer.masks[0].linked).toBe(false);
  expect(layer.masks[0].offset.x).toBeGreaterThan(0);
  expect(layer.masks[0].offset.y).toBeGreaterThan(0);
  const fixedDocumentMask = {
    x: layer.offset.x + layer.masks[0].offset.x,
    y: layer.offset.y + layer.masks[0].offset.y,
  };

  await layerRow.click();
  await dragOnCanvas(page, { x: 0.4, y: 0.4 }, { x: 0.5, y: 0.45 });
  current = await editorState(page);
  layer = current.layers.find(item => item.id === layerId);
  expect(layer.offset.x).toBeGreaterThan(originalLayer.offset.x);
  expect(layer.offset.y).toBeGreaterThan(originalLayer.offset.y);
  expect({
    x: layer.offset.x + layer.masks[0].offset.x,
    y: layer.offset.y + layer.masks[0].offset.y,
  }).toEqual(fixedDocumentMask);

  const beforeTransform = layer;
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-w').fill('280');
  await page.locator('#ge-transform-w').dispatchEvent('input');
  await page.locator('#ge-transform-apply').click();
  current = await editorState(page);
  layer = current.layers.find(item => item.id === layerId);
  expect(layer.size[0]).toBe(280);
  expect({
    x: layer.offset.x + layer.masks[0].offset.x,
    y: layer.offset.y + layer.masks[0].offset.y,
  }).toEqual(fixedDocumentMask);
  await page.locator('#ge-undo').click();
  await expect.poll(async () => {
    const state = await editorState(page);
    return state.layers.find(item => item.id === layerId).size[0];
  }).toBe(beforeTransform.size[0]);

  await maskRow.locator('.ge-mask-link-btn').click();
  current = await editorState(page);
  const relinked = current.layers.find(item => item.id === layerId);
  expect(relinked.masks[0].linked).toBe(true);
  const linkedRelativeOffset = { ...relinked.masks[0].offset };
  await layerRow.click();
  await dragOnCanvas(page, { x: 0.35, y: 0.35 }, { x: 0.45, y: 0.35 });
  current = await editorState(page);
  layer = current.layers.find(item => item.id === layerId);
  expect(layer.masks[0].offset).toEqual(linkedRelativeOffset);

  await page.locator('#ge-undo').click();
  await expect.poll(async () => {
    const state = await editorState(page);
    return state.layers.find(item => item.id === layerId).offset.x;
  }).toBe(relinked.offset.x);

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(item => item.id === layerId)).toEqual(
    beforeReopen.layers.find(item => item.id === layerId),
  );
  const maskRowAfterReopen = page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' });
  await maskRowAfterReopen.locator('details.ge-mask-properties > summary').click();
  await maskRowAfterReopen.locator('button[title="Bake this mask into the layer and remove the mask"]').click();
  await expect.poll(async () => (await editorState(page)).layers.find(item => item.id === layerId).masks).toHaveLength(0);
  await page.locator('#ge-undo').click();
  await expect.poll(async () => (await editorState(page)).layers.find(item => item.id === layerId).masks).toHaveLength(1);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
