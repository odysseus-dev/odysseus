const { test, expect } = require('@playwright/test');
const { editorState, openBlankEditor } = require('./helpers.js');

async function currentPng(page) {
  return page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.layers[0].canvas.toDataURL('image/png');
  });
}

async function loadProject(page, project) {
  await page.locator('#ge-save-menu-btn').click();
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-load-project').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: 'recovery.geproj.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(project)),
  });
}

test('mixed-corrupt project recovers valid layers and remains undoable', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Recovery E2E');
  const before = await editorState(page);
  const png = await currentPng(page);
  await loadProject(page, {
    type: 'odysseus-gallery-editor-project',
    v: 5,
    imgWidth: 160,
    imgHeight: 120,
    activeLayerId: 'broken',
    nextLayerId: 3,
    view: {},
    layers: [
      { id: 'good', name: 'Recovered photo', canvasW: 160, canvasH: 120, dataUrl: png, offset: { x: 0, y: 0 }, masks: [] },
      { id: 'broken', name: 'Broken pixels', canvasW: 160, canvasH: 120, dataUrl: 'data:image/png;base64,AAAA', offset: { x: 0, y: 0 }, masks: [] },
    ],
  });
  await expect.poll(async () => (await editorState(page)).layers.map(layer => layer.name)).toEqual(['Recovered photo']);
  await expect(page.locator('#toast')).toContainText('Broken pixels was skipped');
  await page.locator('#ge-undo').click();
  await expect.poll(async () => (await editorState(page)).layers.map(layer => layer.name)).toEqual(before.layers.map(layer => layer.name));
  expect((await editorState(page)).dimensions).toEqual(before.dimensions);
});

test('fully corrupt project leaves the open document unchanged', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Recovery E2E');
  const before = await editorState(page);
  await loadProject(page, {
    type: 'odysseus-gallery-editor-project',
    v: 5,
    imgWidth: 160,
    imgHeight: 120,
    activeLayerId: 'broken',
    view: {},
    layers: [
      { id: 'broken', name: 'Broken pixels', canvasW: 160, canvasH: 120, dataUrl: 'data:image/png;base64,AAAA', offset: { x: 0, y: 0 }, masks: [] },
    ],
  });
  await expect(page.locator('#toast')).toContainText('No recoverable layers could be decoded');
  await expect.poll(async () => await editorState(page)).toEqual(before);
});

test('active editor reopens after a browser refresh', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Refresh recovery');
  await expect.poll(async () => (await editorState(page)).draftId).not.toBeNull();
  await expect(page.locator('#ge-draft-status')).toHaveText('Saved');

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#gallery-modal')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('#gallery-editor-tab')).toHaveClass(/active/);
  await expect(page.locator('.ge-main-canvas')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('#ge-draft-status')).toHaveText('Saved');
  await expect.poll(async () => (await editorState(page)).layers.map(layer => layer.name))
    .toEqual(['Background', 'Edit']);
});

test('active editor reopens after a mobile browser refresh', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openBlankEditor(page, { width: 320, height: 240 }, 'Mobile refresh recovery');
  await expect.poll(async () => (await editorState(page)).draftId).not.toBeNull();
  await expect(page.locator('#ge-draft-status')).toHaveText('Saved');

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#gallery-modal')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('#gallery-editor-tab')).toHaveClass(/active/);
  await expect(page.locator('.ge-main-canvas')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('#ge-draft-status')).toHaveText('Saved');
  await expect.poll(async () => (await editorState(page)).layers.map(layer => layer.name))
    .toEqual(['Background', 'Edit']);
});

test('new project size dialog cancels cleanly with Escape', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#tool-gallery-btn').waitFor({ state: 'attached', timeout: 20_000 });
  await page.locator('#tool-gallery-btn').click();
  await page.locator('#gallery-editor-tab').waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('#gallery-editor-tab').click();
  await page.locator('#gallery-editor-new').click();
  await expect(page.locator('#ge-canvas-size-overlay')).toBeVisible();
  await page.locator('#ge-canvas-prompt-w').press('Escape');
  await expect(page.locator('#ge-canvas-size-overlay')).toBeHidden();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.editorOpen;
  })).toBe(false);
  await expect(page.locator('#gallery-editor-new')).toBeVisible();
});

test('editor topbar uses uppercase action labels', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Topbar labels');

  await expect(page.locator('#ge-view-menu-btn')).toHaveText(/VIEW/);
  await expect(page.locator('#ge-image-menu-btn')).toHaveText(/IMAGE/);
  await expect(page.locator('#ge-selection-menu-btn')).toHaveText(/SELECT/);
  await expect(page.locator('#ge-filter-menu-btn')).toHaveText(/FILTER/);
  await expect(page.locator('#ge-import-topbar')).toHaveText(/IMPORT/);
  await expect(page.locator('#ge-save-menu-btn')).toHaveText(/SAVE/);
});

test('canvas size anchor keeps the composition centered', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Canvas anchor E2E');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.name === 'Edit');
    state.layerOffsets.set(layer.id, { x: 10, y: 5 });
  });
  await page.locator('#ge-image-menu-btn').click();
  await page.locator('[data-image-action="canvas-size"]').click();
  await expect(page.locator('#ge-canvas-size-overlay')).toBeVisible();
  await page.locator('#ge-canvas-prompt-lock').uncheck();
  await page.locator('#ge-canvas-prompt-w').fill('420');
  await page.locator('#ge-canvas-prompt-h').fill('340');
  await page.locator('.ge-canvas-anchor').nth(4).click();
  await page.locator('#ge-canvas-prompt-ok').click();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.name === 'Edit');
    return { dimensions: [state.imgWidth, state.imgHeight], offset: state.layerOffsets.get(layer.id) };
  })).toEqual({ dimensions: [420, 340], offset: { x: 60, y: 55 } });
});

test('image size supports percentage resampling with locked proportions', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Image size E2E');
  await page.locator('#ge-image-menu-btn').click();
  await page.locator('[data-image-action="image-size"]').click();
  await expect(page.locator('#ge-canvas-size-overlay')).toBeVisible();
  await expect(page.locator('#ge-canvas-prompt-units')).toHaveValue('px');
  await page.locator('#ge-canvas-prompt-units').selectOption('percent');
  await page.locator('#ge-canvas-prompt-w').fill('50');
  await expect(page.locator('#ge-canvas-prompt-h')).toHaveValue('50');
  await page.locator('#ge-canvas-prompt-interpolation').selectOption('medium');
  await page.locator('#ge-canvas-prompt-ok').click();
  await expect.poll(async () => (await editorState(page)).dimensions).toEqual([160, 120]);

  await page.locator('#ge-image-menu-btn').click();
  await page.locator('[data-image-action="image-size"]').click();
  await expect(page.locator('#ge-canvas-prompt-units')).toHaveValue('px');
  await expect(page.locator('#ge-canvas-prompt-w')).toHaveValue('160');
  await expect(page.locator('#ge-canvas-prompt-h')).toHaveValue('120');
  await page.locator('#ge-canvas-prompt-cancel').click();
});

test('canvas size supports percentage bounds with an anchor', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Canvas percentage E2E');
  await page.locator('#ge-image-menu-btn').click();
  await page.locator('[data-image-action="canvas-size"]').click();
  await expect(page.locator('#ge-canvas-size-overlay')).toBeVisible();
  await page.locator('#ge-canvas-prompt-units').selectOption('percent');
  await page.locator('#ge-canvas-prompt-w').fill('125');
  await expect(page.locator('#ge-canvas-prompt-h')).toHaveValue('125');
  await page.locator('.ge-canvas-anchor').nth(4).click();
  await page.locator('#ge-canvas-prompt-ok').click();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.name === 'Edit');
    return { dimensions: [state.imgWidth, state.imgHeight], offset: state.layerOffsets.get(layer.id) };
  })).toEqual({ dimensions: [400, 300], offset: { x: 40, y: 30 } });
});
