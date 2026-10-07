const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers');

async function openRefine(page) {
  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="refine"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
}

async function drawLasso(page, points) {
  const box = await page.locator('.ge-main-canvas').boundingBox();
  const point = ([x, y]) => ({ x: box.x + box.width * x, y: box.y + box.height * y });
  const first = point(points[0]);
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  for (const item of points.slice(1)) {
    const next = point(item);
    await page.mouse.move(next.x, next.y, { steps: 3 });
  }
  await page.mouse.move(first.x, first.y, { steps: 3 });
  await page.mouse.up();
}

test('selection clipboard copy and paste creates an undoable independent layer', async ({ page, request }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Selection clipboard');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.18, y: 0.24 }, { x: 0.72, y: 0.68 });
  await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
  await dragOnCanvas(page, { x: 0.25, y: 0.25 }, { x: 0.65, y: 0.65 });

  const before = await editorState(page);
  await page.keyboard.press('Control+c');
  await page.evaluate(() => {
    window.dispatchEvent(new Event('paste', { bubbles: true, cancelable: true }));
  });
  await expect(page.locator('.ge-layer-item').filter({ hasText: 'Pasted Selection' })).toBeVisible();
  const pasted = await editorState(page);
  expect(pasted.layers).toHaveLength(before.layers.length + 1);
  const pastedLayer = pasted.layers.at(-1);
  expect(pasted.activeLayerId).toBe(pastedLayer.id);
  expect(pastedLayer.kind).toBe('placed');
  expect(pastedLayer.placed.sourceSize).toEqual(pastedLayer.size);

  await page.locator('#ge-undo').click();
  await expect.poll(async () => (await editorState(page)).layers.length).toBe(before.layers.length);
  await page.locator('#ge-redo').click();
  await expect.poll(async () => (await editorState(page)).layers.length).toBe(before.layers.length + 1);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  const reopenedLayer = reopened.layers.at(-1);
  expect(reopenedLayer.kind).toBe('placed');
  expect(reopenedLayer.placed.sourceSize).toEqual(pastedLayer.size);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('cut selection is one undoable move from source to new layer', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Selection cut');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.18, y: 0.24 }, { x: 0.72, y: 0.68 });
  await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
  await dragOnCanvas(page, { x: 0.25, y: 0.25 }, { x: 0.65, y: 0.65 });

  const before = await editorState(page);
  const sourceSignature = () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.name === 'Edit');
    const data = layer.canvas.getContext('2d').getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    let hash = 2166136261;
    for (const value of data) {
      hash ^= value;
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  });
  const beforeSource = await sourceSignature();
  await page.keyboard.press('Control+x');
  await expect(page.locator('.ge-layer-item').filter({ hasText: 'Wand copy' })).toBeVisible();
  const cut = await editorState(page);
  expect(cut.layers).toHaveLength(before.layers.length + 1);
  expect(await sourceSignature()).not.toBe(beforeSource);

  await page.locator('#ge-undo').click();
  await expect.poll(async () => (await editorState(page)).layers.length).toBe(before.layers.length);
  expect(await sourceSignature()).toBe(beforeSource);
});

test('completed lasso copy preserves the source layer coordinate space', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Offset selection copy');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.layerOffsets.set(state.activeLayerId, { x: 32, y: 24 });
    window.galleryEditorComposite?.();
  });
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.28, y: 0.3 }, { x: 0.62, y: 0.58 });
  await page.locator('.ge-tool-btn[data-tool="lasso"]').click();
  await drawLasso(page, [[0.26, 0.26], [0.68, 0.26], [0.68, 0.64], [0.26, 0.64]]);

  const before = await editorState(page);
  const source = before.layers.find(layer => layer.id === before.activeLayerId);
  await page.locator('#ge-lasso-copy').click();
  await expect(page.locator('.ge-layer-item').filter({ hasText: 'Wand copy' })).toBeVisible();
  const copied = await editorState(page);
  const copy = copied.layers.find(layer => layer.name === 'Wand copy');
  expect(copy.size).toEqual(source.size);
  expect(copy.offset).toEqual(source.offset);
});

test('selection refine previews safely and layer masks round-trip to selection', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Selection refinement');
  await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
  await dragOnCanvas(page, { x: 0.25, y: 0.25 }, { x: 0.55, y: 0.55 });
  const original = (await editorState(page)).selection;

  await openRefine(page);
  const expand = page.locator('.ge-filter-modal input[data-key="expand"]');
  await expand.fill('12');
  await expand.dispatchEvent('input');
  await expect.poll(async () => (await editorState(page)).selection.bounds.width)
    .toBeGreaterThan(original.bounds.width);
  await page.locator('.ge-filter-modal [data-action="cancel"]').click();
  expect((await editorState(page)).selection.pixelHash).toBe(original.pixelHash);

  await openRefine(page);
  const appliedExpand = page.locator('.ge-filter-modal input[data-key="expand"]');
  await appliedExpand.fill('12');
  await appliedExpand.dispatchEvent('input');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  const refined = (await editorState(page)).selection;
  expect(refined.bounds.width).toBeGreaterThan(original.bounds.width);
  expect(refined.pixelHash).not.toBe(original.pixelHash);

  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  let current = await editorState(page);
  const active = current.layers.find(layer => layer.id === current.activeLayerId);
  expect(active.masks).toHaveLength(1);
  expect(active.masks[0].mode).toBe('layer');
  expect(active.masks[0].pixelHash).toBe(refined.pixelHash);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="deselect"]').click();
  expect((await editorState(page)).selection).toBeNull();
  await page.getByRole('button', { name: 'Load mask as selection' }).click();
  current = await editorState(page);
  expect(current.selection.pixelHash).toBe(refined.pixelHash);
});

test('lasso uses the shared replace, add, subtract, and intersect modes', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Lasso combine modes');
  await page.locator('.ge-tool-btn[data-tool="lasso"]').click();
  await drawLasso(page, [[0.1, 0.2], [0.35, 0.2], [0.35, 0.55], [0.1, 0.55]]);
  const first = (await editorState(page)).selection;
  expect(first.source).toBe('lasso');

  await page.locator('#ge-lasso-section [data-wand-mode="add"]').click();
  await drawLasso(page, [[0.55, 0.2], [0.85, 0.2], [0.85, 0.55], [0.55, 0.55]]);
  const added = (await editorState(page)).selection;
  expect(added.bounds.width).toBeGreaterThan(first.bounds.width);

  await page.locator('#ge-lasso-section [data-wand-mode="subtract"]').click();
  await drawLasso(page, [[0.05, 0.15], [0.4, 0.15], [0.4, 0.6], [0.05, 0.6]]);
  const subtracted = (await editorState(page)).selection;
  expect(subtracted.bounds.x).toBeGreaterThan(first.bounds.x);

  await page.locator('#ge-lasso-section [data-wand-mode="intersect"]').click();
  await drawLasso(page, [[0.65, 0.25], [0.78, 0.25], [0.78, 0.48], [0.65, 0.48]]);
  const intersected = (await editorState(page)).selection;
  expect(intersected.bounds.width).toBeLessThan(subtracted.bounds.width);
});
