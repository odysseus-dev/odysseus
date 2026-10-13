const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

test('Ctrl-click thumbnail shows pixel selection even after outlines were hidden', async ({ page }) => {
  // Notification polling requires login even in the auth-disabled test server.
  await page.route('**/api/tasks/notification-logs*', route => route.fulfill({ json: { logs: [] } }));
  await openBlankEditor(page, { width: 240, height: 160 }, 'Thumbnail selection');
  const id = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    layer.ctx.fillStyle = '#ff0000';
    layer.ctx.fillRect(30, 25, 60, 40);
    state.wandMaskVisible = false;
    return layer.id;
  });
  await page.locator(`.ge-layer-item[data-layer-id="${id}"] .ge-layer-inline-thumb`).click({ modifiers: ['Control'] });
  await expect.poll(() => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const canvas = state.selectionOverlay;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return state.wandMaskVisible && canvas.style.display !== 'none' && pixels.some((value, index) => index % 4 === 3 && value > 0);
  })).toBe(true);
  await expect.poll(() => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const ctx = state.wandMask.getContext('2d');
    return [ctx.getImageData(40, 35, 1, 1).data[3], ctx.getImageData(0, 0, 1, 1).data[3]];
  })).toEqual([255, 0]);
});

test('selected layers align to the canvas and undo as one operation', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Alignment E2E');
  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-layer-kind="raster"]').click();
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const edit = state.layers.find(layer => layer.name === 'Edit');
    const added = state.layers.find(layer => layer.id === state.activeLayerId);
    edit.canvas.width = 100;
    edit.canvas.height = 80;
    added.canvas.width = 50;
    added.canvas.height = 40;
    state.layerOffsets.set(edit.id, { x: 12, y: 18 });
    state.layerOffsets.set(added.id, { x: 190, y: 150 });
  });
  const editRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  await editRow.click({ modifiers: ['Control'] });
  await expect(page.locator('.ge-layer-item.selected[data-layer-id]')).toHaveCount(2);
  await page.locator('#ge-selected-align').click();
  await page.locator('#ge-layer-align-menu button').filter({ hasText: 'Align center' }).click();

  let current = await editorState(page);
  expect(current.layers.find(layer => layer.name === 'Edit').offset).toEqual({ x: 110, y: 18 });
  expect(current.layers.find(layer => layer.name !== 'Background' && layer.name !== 'Edit').offset)
    .toEqual({ x: 135, y: 150 });

  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.name === 'Edit').offset).toEqual({ x: 12, y: 18 });
  expect(current.layers.find(layer => layer.name !== 'Background' && layer.name !== 'Edit').offset)
    .toEqual({ x: 190, y: 150 });
});

test('layer rename cancels on Escape without committing the draft name', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Rename cancel E2E');
  const row = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  const rowId = await row.getAttribute('data-layer-id');
  const name = row.locator('.ge-layer-name');
  const original = await name.textContent();
  await name.dblclick();
  const stableRow = page.locator(`.ge-layer-item[data-layer-id="${rowId}"]`);
  const input = stableRow.locator('.ge-layer-name-input');
  await input.fill('Temporary name');
  await input.press('Escape');
  await expect(stableRow.locator('.ge-layer-name')).toHaveText(original);
  await expect(stableRow.locator('.ge-layer-name-input')).toHaveCount(0);
});

test('layer rows can be selected with Enter and Space', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Keyboard layer selection E2E');
  const background = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  const edit = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();

  await background.focus();
  await page.keyboard.press('Enter');
  await expect(background).toHaveAttribute('aria-pressed', 'true');
  await expect(edit).toHaveAttribute('aria-pressed', 'false');

  await edit.focus();
  await page.keyboard.press('Space');
  await expect(edit).toHaveAttribute('aria-pressed', 'true');
  await expect(background).toHaveAttribute('aria-pressed', 'false');
});

test('group rows can be selected with the keyboard', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Keyboard group selection E2E');
  await page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first().click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();

  const group = page.locator('.ge-layer-group-row').first();
  await group.focus();
  await page.keyboard.press('Enter');
  await expect(group).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.activeGroupId;
  })).toBe(await group.getAttribute('data-group-id'));
});

test('Delete removes the selected layer and undo restores it', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Keyboard delete E2E');
  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-layer-kind="raster"]').click();
  const addedId = (await editorState(page)).activeLayerId;
  await page.locator(`.ge-layer-item[data-layer-id="${addedId}"]`).click();
  await page.keyboard.press('Backspace');
  expect((await editorState(page)).layers.some(layer => layer.id === addedId)).toBe(false);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.some(layer => layer.id === addedId)).toBe(true);
});

test('Ctrl/Cmd+J duplicates the active layer through the layer panel path', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Keyboard duplicate E2E');
  const before = await editorState(page);
  const activeId = before.activeLayerId;
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+j' : 'Control+j');
  const after = await editorState(page);
  expect(after.layers).toHaveLength(before.layers.length + 1);
  expect(after.activeLayerId).not.toBe(activeId);
  expect(after.layers.find(layer => layer.id === after.activeLayerId).name).toContain('copy');
});

test('move tool can auto-select the topmost visible layer under the pointer', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Auto-select E2E');
  await page.locator('.ge-tool-btn[data-tool="shape"]').click();
  await dragOnCanvas(page, { x: 0.25, y: 0.25 }, { x: 0.75, y: 0.75 });
  const shape = (await editorState(page)).layers.find(layer => layer.kind === 'shape');
  expect(shape).toBeTruthy();

  await page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first().click();
  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await expect(page.locator('.ge-auto-select-option')).toBeVisible();
  await page.locator('.ge-auto-select-option').click();
  await expect(page.locator('#ge-auto-select-layer')).toBeChecked();
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('.ge-auto-select-option')).toBeHidden();
  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await expect(page.locator('.ge-auto-select-option')).toBeVisible();
  const box = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect.poll(async () => (await editorState(page)).activeLayerId).toBe(shape.id);
  expect((await editorState(page)).selectedLayerIds).toEqual([shape.id]);
});

test('multi-selected layers move together and support history-backed bulk actions', async ({ page }) => {
  await openBlankEditor(page);
  const editRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Edit' }).first();
  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();

  await backgroundRow.click({ modifiers: ['Control'] });
  await expect(page.locator('.ge-layer-item.selected[data-layer-id]')).toHaveCount(2);
  expect((await editorState(page)).selectedLayerIds).toHaveLength(2);
  await expect(page.locator('#ge-layer-selection-bar')).toBeVisible();

  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await dragOnCanvas(page, { x: 0.42, y: 0.42 }, { x: 0.52, y: 0.50 });
  let current = await editorState(page);
  const edit = current.layers.find(layer => layer.name === 'Edit');
  const background = current.layers.find(layer => layer.name === 'Background');
  expect(edit.offset).toEqual(background.offset);
  expect(edit.offset).not.toEqual({ x: 0, y: 0 });

  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.name === 'Edit').offset).toEqual({ x: 0, y: 0 });
  expect(current.layers.find(layer => layer.name === 'Background').offset).toEqual({ x: 0, y: 0 });
  expect(current.selectedLayerIds).toHaveLength(2);
  await page.locator('#ge-redo').click();

  await page.locator('#ge-selected-visibility').click();
  current = await editorState(page);
  expect(current.layers.every(layer => layer.visible === false)).toBe(true);
  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers.every(layer => layer.visible !== false)).toBe(true);

  await page.locator('#ge-selected-lock').click();
  current = await editorState(page);
  expect(current.layers.every(layer => layer.locked)).toBe(true);
  await page.locator('#ge-undo').click();

  await editRow.click();
  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-layer-kind="raster"]').click();
  const addedId = (await editorState(page)).activeLayerId;
  await editRow.click({ modifiers: ['Control'] });
  await expect(page.locator('.ge-layer-item.selected[data-layer-id]')).toHaveCount(2);
  await page.locator('#ge-selected-delete').click();
  current = await editorState(page);
  expect(current.layers).toHaveLength(1);
  expect(current.layers[0].name).toBe('Background');
  expect(current.layers.some(layer => layer.id === addedId)).toBe(false);

  await page.locator('#ge-undo').click();
  current = await editorState(page);
  expect(current.layers).toHaveLength(3);
  expect(current.selectedLayerIds).toHaveLength(2);
});

test('layer groups composite, lock, collapse, undo, and survive server reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 480, height: 320 }, 'Grouped E2E');
  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  await backgroundRow.click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(1);
  await expect(page.locator('.ge-layer-group-row .ge-group-inline-thumb')).toHaveCount(1);
  let current = await editorState(page);
  expect(current.groups).toHaveLength(1);
  expect(current.groups[0].layerIds).toHaveLength(2);

  const groupName = page.locator('.ge-layer-group-name');
  await groupName.dblclick();
  await page.locator('.ge-layer-group-row input.ge-layer-name-input').fill('Hero group');
  await page.locator('.ge-layer-group-row input.ge-layer-name-input').press('Enter');
  await page.locator('.ge-layer-group-row .ge-layer-opacity').fill('55');
  await page.locator('.ge-layer-group-toggle').click();
  await expect(page.locator('.ge-layer-item.grouped')).toHaveCount(0);
  current = await editorState(page);
  expect(current.groups[0]).toMatchObject({ name: 'Hero group', opacity: 0.55, collapsed: true });

  await page.locator('.ge-layer-group-row .ge-layer-lock-btn').click();
  const beforeLockNudge = await editorState(page);
  await page.keyboard.press('ArrowRight');
  expect((await editorState(page)).layers.map(layer => layer.offset)).toEqual(beforeLockNudge.layers.map(layer => layer.offset));
  await page.locator('.ge-layer-group-row .ge-layer-lock-btn').click();

  await page.locator('.ge-layer-group-row').click();
  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await dragOnCanvas(page, { x: 0.40, y: 0.40 }, { x: 0.52, y: 0.48 });
  current = await editorState(page);
  expect(current.layers[0].offset).toEqual(current.layers[1].offset);
  expect(current.layers[0].offset).not.toEqual({ x: 0, y: 0 });

  await page.locator('.ge-layer-group-row .ge-layer-vis').click();
  current = await editorState(page);
  expect(current.groups[0].visible).toBe(false);
  expect(current.layers.every(layer => layer.visible)).toBe(true);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).groups[0].visible).toBe(true);

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.groups).toEqual(beforeReopen.groups);
  expect(reopened.layers.map(layer => layer.offset)).toEqual(beforeReopen.layers.map(layer => layer.offset));
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(1);
  await expect(page.locator('.ge-layer-group-row .ge-group-inline-thumb')).toHaveCount(1);
  await expect(page.locator('.ge-layer-item.grouped')).toHaveCount(0);

  await page.locator('.ge-layer-group-row').click();
  await page.locator('#ge-layer-tools button[title="Ungroup layers"]').click();
  expect((await editorState(page)).groups).toHaveLength(0);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).groups).toHaveLength(1);

  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('nested groups preserve hierarchy, ancestor locks, collapse, and server reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 420, height: 300 }, 'Nested groups E2E');
  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  await backgroundRow.click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  let current = await editorState(page);
  const innerId = current.groups[0].id;

  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-layer-kind="raster"]').click();
  current = await editorState(page);
  const looseId = current.activeLayerId;
  await page.locator(`.ge-layer-group-row[data-group-id="${innerId}"]`).click();
  await page.locator(`.ge-layer-item[data-layer-id="${looseId}"]`).click({ modifiers: ['Control'] });
  await expect(page.locator('.ge-layer-item.selected[data-layer-id]')).toHaveCount(3);
  await page.locator('#ge-group-selected').click();

  current = await editorState(page);
  expect(current.groups).toHaveLength(2);
  const inner = current.groups.find(group => group.id === innerId);
  const outer = current.groups.find(group => group.id !== innerId);
  expect(inner.parentId).toBe(outer.id);
  expect(outer.layerIds).toEqual([looseId]);
  expect(inner.layerIds).toHaveLength(2);
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(2);
  await expect(page.locator('.ge-layer-group-row .ge-group-inline-thumb')).toHaveCount(2);
  await expect(page.locator(`.ge-layer-group-row[data-group-id="${inner.id}"]`)).toHaveCSS('--group-depth', '1');

  const outerRow = page.locator(`.ge-layer-group-row[data-group-id="${outer.id}"]`);
  await outerRow.locator('.ge-layer-group-toggle').click();
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(1);
  await expect(page.locator('.ge-layer-item.grouped')).toHaveCount(0);
  await outerRow.locator('.ge-layer-group-toggle').click();
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(2);

  await outerRow.locator('.ge-layer-lock-btn').click();
  await page.locator(`.ge-layer-item[data-layer-id="${inner.layerIds[0]}"]`).click();
  const beforeNudge = await editorState(page);
  await page.keyboard.press('ArrowRight');
  expect((await editorState(page)).layers.map(layer => layer.offset)).toEqual(beforeNudge.layers.map(layer => layer.offset));
  await outerRow.locator('.ge-layer-lock-btn').click();

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.groups).toEqual(beforeReopen.groups);
  await expect(page.locator('.ge-layer-group-row')).toHaveCount(2);

  await page.locator(`.ge-layer-group-row[data-group-id="${outer.id}"]`).click();
  await page.locator('#ge-layer-tools button[title="Ungroup layers"]').click();
  current = await editorState(page);
  expect(current.groups).toHaveLength(1);
  expect(current.groups[0]).toMatchObject({ id: inner.id, parentId: null });
  expect(current.groups[0].layerIds).toHaveLength(2);
  await expect(page.locator('.ge-layer-item.grouped')).toHaveCount(2);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).groups).toEqual(beforeReopen.groups);

  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
