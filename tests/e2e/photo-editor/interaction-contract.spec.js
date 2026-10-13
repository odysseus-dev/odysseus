const { test, expect } = require('@playwright/test');
const { openBlankEditor, editorState, dragOnCanvas } = require('./helpers');

test.beforeEach(async ({ page }) => {
  // The isolated editor server has no logged-in notification owner.
  await page.route('**/api/tasks/notification-logs*', route => route.fulfill({
    json: { logs: [], notifications: [] },
  }));
});

test('top toolbar stays on one row and its menus remain clickable at narrow widths', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  const tip = page.getByRole('button', { name: 'Got it', exact: true });
  if (await tip.isVisible()) await tip.click();
  for (const width of [1280, 900, 600, 390]) {
    await page.setViewportSize({ width, height: 850 });
    await expect.poll(() => page.locator('.ge-topbar').evaluate(bar => {
      const left = bar.querySelector('.ge-topbar-left').getBoundingClientRect();
      const right = bar.querySelector('.ge-topbar-right').getBoundingClientRect();
      return Math.abs((left.top + left.bottom) / 2 - (right.top + right.bottom) / 2);
    })).toBeLessThan(2);
    await page.locator('#ge-save-menu-btn').scrollIntoViewIfNeeded();
    await expect(page.locator('#ge-save-menu-btn')).toHaveCount(1);
    await expect(page.locator('#ge-draft-status')).toHaveCount(1);
    const status = page.locator('#ge-save-menu-btn > #ge-draft-status');
    await expect(status).toBeVisible();
    await expect(page.locator('#ge-save-menu-btn .ge-save-state-icon:not([hidden])')).toHaveCount(1);
    await expect(status).toBeVisible();
    await expect(page.locator('.ge-topbar-right > #ge-draft-status')).toHaveCount(0);
    await page.locator('#ge-image-menu-btn').click();
    await expect(page.locator('#ge-image-menu')).toBeVisible();
    await page.locator('[data-image-action="rotate-90"]').click();
    await expect(page.locator('#ge-image-menu')).toBeHidden();
    await page.screenshot({ path: `/tmp/editor-toolbar-${width}.png` });
  }
});

test('committed paint refreshes the layer thumbnail and flashes only the edited row', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  const activeId = (await editorState(page)).activeLayerId;
  const row = page.locator(`.ge-layer-item[data-layer-id="${activeId}"]`);
  const before = await row.locator('.ge-layer-inline-thumb').evaluate(canvas => canvas.toDataURL());
  await page.keyboard.press('b');
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.7, y: 0.7 });
  await expect(row).toHaveClass(/ge-layer-action-flash/);
  expect(await row.locator('.ge-layer-inline-thumb').evaluate(canvas => canvas.toDataURL())).not.toBe(before);
  await expect(page.locator('.ge-layer-action-flash')).toHaveCount(1);
  await expect(row).not.toHaveClass(/ge-layer-action-flash/, { timeout: 2000 });
});

test('filter Escape restores pixels and preserves redo history', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.keyboard.press('b');
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.7, y: 0.7 });
  await dragOnCanvas(page, { x: 0.7, y: 0.2 }, { x: 0.2, y: 0.7 });
  await page.keyboard.press('Control+z');
  const before = await editorState(page);
  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="blur-gaussian"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.ge-filter-modal')).toHaveCount(0);
  const after = await editorState(page);
  expect(after.layers).toEqual(before.layers);
  expect(after.undo).toBe(before.undo);
  expect(after.redo).toBe(before.redo);
});

test('focused fields own undo, selection and clipboard without changing layers', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('[data-tool="text"]').click();
  const box = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(box.x + 80, box.y + 80);
  const input = page.locator('.ge-direct-text-editor');
  await input.fill('Editable text');
  await input.press('Control+Enter');
  // A tool's ordinary text input must also retain native clipboard ownership.
  await page.locator('#ge-text-frame-width').focus();
  const before = await editorState(page);
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+j');
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+c');
  await page.evaluate(() => {
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true }));
  });
  const after = await editorState(page);
  expect(after.layers).toEqual(before.layers);
  expect(after.undo).toBe(before.undo);
  expect(after.selection).toEqual(before.selection);
});

test('tool keys never delete a selection and Ctrl J copies only selected pixels', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.fillStyle = '#f00';
    layer.ctx.fillRect(0, 0, layer.canvas.width, layer.canvas.height);
  });
  await page.keyboard.press('m');
  await expect(page.locator('[data-tool="marquee"]')).toHaveClass(/active/);
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.4, y: 0.4 });
  const original = await editorState(page);
  await page.keyboard.press('c');
  await expect(page.locator('[data-tool="crop"]')).toHaveClass(/active/);
  await page.keyboard.press('d');
  expect((await editorState(page)).layers).toEqual(original.layers);
  expect((await editorState(page)).selection).toEqual(original.selection);
  await page.keyboard.press('Control+j');
  expect((await editorState(page)).layers).toHaveLength(original.layers.length + 1);
  const copiedAlpha = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    return [layer.ctx.getImageData(50, 50, 1, 1).data[3], layer.ctx.getImageData(250, 180, 1, 1).data[3]];
  });
  expect(copiedAlpha).toEqual([255, 0]);
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers).toEqual(original.layers);
  await page.keyboard.press('Control+d');
  expect((await editorState(page)).selection).toBeNull();
  await page.keyboard.press('s');
  await expect(page.locator('[data-tool="clone"]')).toHaveClass(/active/);
});

test('Shift Alt intersects a marquee and reopening does not duplicate shortcuts', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.evaluate(async () => {
    const editor = await import('/static/js/galleryEditor.js');
    await editor.openEditor(null, null, { w: 320, h: 240 }, 'Reopened');
  });
  await page.keyboard.press('m');
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.6, y: 0.6 });
  await page.keyboard.down('Shift');
  await page.keyboard.down('Alt');
  await dragOnCanvas(page, { x: 0.4, y: 0.4 }, { x: 0.9, y: 0.9 });
  await page.keyboard.up('Alt');
  await page.keyboard.up('Shift');
  const selection = (await editorState(page)).selection.bounds;
  expect(selection.x).toBeGreaterThanOrEqual(127);
  expect(selection.width).toBeLessThanOrEqual(65);
  const before = await editorState(page);
  await page.keyboard.press('Control+j');
  expect((await editorState(page)).layers.length).toBe(before.layers.length + 1);
});

test('cut removes selected pixels without creating a layer and undo restores them', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.fillStyle = '#f00';
    layer.ctx.fillRect(0, 0, 320, 240);
  });
  await page.keyboard.press('m');
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.4, y: 0.4 });
  const before = await editorState(page);
  await page.keyboard.press('Control+x');
  await expect.poll(async () => (await editorState(page)).selection).toBeNull();
  const cut = await editorState(page);
  expect(cut.layers.length).toBe(before.layers.length);
  expect(cut.layers.find(layer => layer.id === before.activeLayerId).pixelHash)
    .not.toBe(before.layers.find(layer => layer.id === before.activeLayerId).pixelHash);
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers).toEqual(before.layers);
  expect((await editorState(page)).selection).toEqual(before.selection);
});

test('selection delete and fill edit an offset mask, preserving parent pixels', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.fillStyle = '#f00';
    layer.ctx.fillRect(0, 0, 320, 240);
    const canvas = document.createElement('canvas');
    canvas.width = 200; canvas.height = 160;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 200, 160);
    layer.masks = [{ id: 'test-mask', name: 'Mask', mode: 'layer', space: 'layer',
      canvas, ctx, offset: { x: 10, y: 10 }, visible: true, linked: true }];
    layer.activeMaskId = 'test-mask';
    state.layerOffsets.set(layer.id, { x: 20, y: 10 });
    state.color = '#ffffff';
  });
  await page.keyboard.press('m');
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.4, y: 0.4 });
  const before = await editorState(page);
  const parentBefore = before.layers.find(layer => layer.id === before.activeLayerId);
  await page.keyboard.press('Delete');
  await expect.poll(async () => (await editorState(page)).selection).toBeNull();
  const erased = (await editorState(page)).layers.find(layer => layer.id === before.activeLayerId);
  expect(erased.pixelHash).toBe(parentBefore.pixelHash);
  expect(erased.masks[0].pixelHash).not.toBe(parentBefore.masks[0].pixelHash);
  await page.locator('#ge-image-menu-btn').click();
  await page.locator('[data-image-action="fill"]').click();
  await expect.poll(async () => (await editorState(page)).layers.find(layer => layer.id === before.activeLayerId).masks[0].pixelHash)
    .toBe(parentBefore.masks[0].pixelHash);
  expect((await editorState(page)).layers.find(layer => layer.id === before.activeLayerId).pixelHash).toBe(parentBefore.pixelHash);
  await page.keyboard.press('Control+c');
  const clipboard = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { pixel: [...state.internalClipboard.getContext('2d').getImageData(50, 50, 1, 1).data], offset: state.internalClipboardOffset };
  });
  expect(clipboard).toEqual({ pixel: [255, 255, 255, 255], offset: { x: 30, y: 20 } });
  await page.evaluate(() => document.body.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true })));
  const pasted = await editorState(page);
  expect(pasted.layers).toHaveLength(before.layers.length + 1);
  expect(pasted.layers.find(layer => layer.id === pasted.activeLayerId).offset).toEqual({ x: 30, y: 20 });
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers).toHaveLength(before.layers.length);
});

test('focus loss releases a held brush and does not resume it on return', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.keyboard.press('b');
  const before = await editorState(page);
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.move(canvas.x + 50, canvas.y + 50);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 90, canvas.y + 70, { steps: 5 });
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  expect(await page.evaluate(async () => (await import('/static/js/editor/state.js')).state.drawing)).toBe(false);
  const released = await editorState(page);
  await page.mouse.move(canvas.x + 150, canvas.y + 100, { steps: 5 });
  await page.mouse.up();
  expect((await editorState(page)).layers).toEqual(released.layers);
  expect((await editorState(page)).undo).toBe(before.undo + 1);
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers).toEqual(before.layers);
});

test('switching away from a held brush ends one stroke and desktop reselect keeps controls open', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 });
  await page.keyboard.press('b');
  await page.keyboard.press('b');
  await expect(page.locator('.ge-controls')).not.toHaveClass(/dismissed/);
  const before = await editorState(page);
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.move(canvas.x + 50, canvas.y + 50);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 90, canvas.y + 70, { steps: 5 });
  await page.keyboard.press('v');
  expect(await page.evaluate(async () => (await import('/static/js/editor/state.js')).state.drawing)).toBe(false);
  await page.mouse.up();
  expect((await editorState(page)).undo).toBe(before.undo + 1);
  await page.keyboard.press('Control+z');
  expect((await editorState(page)).layers).toEqual(before.layers);
});
