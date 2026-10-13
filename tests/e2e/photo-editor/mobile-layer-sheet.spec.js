const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const { editorState, openBlankEditor, waitForDraft } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test('mobile tool and layer sheets do not overlap mask controls', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Mobile layer sheet');
  await page.locator('.ge-tour-close').click({ timeout: 500 }).catch(() => {});

  const controls = page.locator('.ge-controls');
  const layerSheet = page.locator('.ge-right-panel');
  await expect(controls).toHaveClass(/dismissed/);
  await expect(layerSheet).not.toHaveClass(/minimized/);

  await page.locator('.ge-layers-title').click();
  await expect(layerSheet).toHaveClass(/expanded/);

  const layerId = (await editorState(page)).activeLayerId;
  const layerRow = page.locator(`.ge-layer-item[data-layer-id="${layerId}"]`);
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();

  const maskRow = page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' });
  const maskName = maskRow.locator('.ge-layer-name');
  const maskThumb = maskRow.locator('.ge-mask-inline-thumb');
  await expect(maskRow).toBeVisible();
  await expect(maskName).toContainText('Layer Mask');
  await expect(maskThumb).toBeVisible();
  const rowBox = await maskRow.boundingBox();
  const nameBox = await maskName.boundingBox();
  const linkBox = await maskRow.locator('.ge-mask-link-btn').boundingBox();
  expect(rowBox).toBeTruthy();
  expect(nameBox.width).toBeGreaterThan(80);
  expect(rowBox.x).toBeGreaterThanOrEqual(0);
  expect(rowBox.x + rowBox.width).toBeLessThanOrEqual(390);
  expect(nameBox.x).toBeGreaterThanOrEqual(0);
  expect(nameBox.x + nameBox.width).toBeLessThanOrEqual(linkBox.x);
  expect(await page.locator('.ge-layers-list').evaluate(list => list.scrollLeft)).toBe(0);
  await maskRow.locator('.ge-mask-link-btn').click();
  await expect(maskRow.locator('.ge-mask-link-btn')).toHaveAttribute('aria-pressed', 'false');

  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await expect(controls).not.toHaveClass(/dismissed/);
  await expect(layerSheet).toHaveClass(/minimized/);
  const controlsBox = await controls.boundingBox();
  const sheetBox = await layerSheet.boundingBox();
  expect(controlsBox.y).toBeLessThan(sheetBox.y + sheetBox.height);

  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  await expect(controls).toHaveClass(/dismissed/);
});

test('mobile group rows keep their preview and touch controls inside the viewport', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Mobile group preview');
  await page.locator('.ge-layers-title').click();
  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  await backgroundRow.click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();

  const groupRow = page.locator('.ge-layer-group-row').first();
  await expect(groupRow).toBeVisible();
  await expect(groupRow.locator('.ge-group-inline-thumb')).toBeVisible();
  const rowBox = await groupRow.boundingBox();
  const thumbBox = await groupRow.locator('.ge-group-inline-thumb').boundingBox();
  const toggleBox = await groupRow.locator('.ge-layer-group-toggle').boundingBox();
  const visibilityBox = await groupRow.locator('.ge-layer-vis').boundingBox();
  expect(rowBox).toBeTruthy();
  for (const box of [thumbBox, toggleBox, visibilityBox]) {
    expect(box).toBeTruthy();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
  }
});

test('mobile touch mask editing supports undo and persists through reload', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  await openBlankEditor(page, { width: 320, height: 240 }, 'Mobile mask persistence');
  await page.locator('.ge-layers-title').click();
  await expect(page.locator('.ge-right-panel')).toHaveClass(/expanded/);

  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  const maskRow = page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' });
  await expect(maskRow).toBeVisible();
  const before = (await editorState(page)).layers.find(layer => layer.masks.length).masks[0].pixelHash;

  // A new layer mask is fully white (revealed), so Brush would paint white
  // onto white. Eraser makes the first touch edit observable by hiding pixels.
  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  // Collapse the bottom-sheet controls so the full canvas is available to the
  // touch gesture, matching the canvas-first mobile editing mode.
  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
  const cdp = await page.context().newCDPSession(page);
  const visibleCanvasPoint = await page.evaluate(() => {
    const canvas = document.querySelector('.ge-main-canvas');
    const rect = canvas?.getBoundingClientRect();
    if (!canvas || !rect) return null;
    for (let y = rect.top + 8; y < rect.bottom - 8; y += 8) {
      for (let x = rect.left + 8; x < rect.right - 8; x += 8) {
        if (document.elementFromPoint(x, y) === canvas) return { x, y };
      }
    }
    return null;
  });
  expect(visibleCanvasPoint).toBeTruthy();
  const x1 = visibleCanvasPoint.x;
  const x2 = Math.min(canvasBox.x + canvasBox.width - 8, x1 + canvasBox.width * 0.35);
  const y = visibleCanvasPoint.y;
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: x1, y, id: 1, radiusX: 6, radiusY: 6 }],
  });
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x: x2, y, id: 1, radiusX: 6, radiusY: 6 }],
  });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

  const painted = await editorState(page);
  const paintedLayer = painted.layers.find(layer => layer.masks.length);
  expect(paintedLayer.masks[0].pixelHash).not.toBe(before);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.masks.length).masks[0].pixelHash)
    .toBe(before);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers.find(layer => layer.masks.length).masks[0].pixelHash)
    .toBe(paintedLayer.masks[0].pixelHash);

  const draftId = await waitForDraft(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('.ge-main-canvas')).toBeVisible({ timeout: 20_000 });
  await expect.poll(async () => {
    const state = await editorState(page);
    return state.layers.find(layer => layer.masks.length)?.masks[0]?.pixelHash;
  }).toBe(paintedLayer.masks[0].pixelHash);
  await page.request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('mobile export dialog stays usable and downloads the requested PNG', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Mobile export');
  await page.locator('#ge-save-menu-btn').click();
  await page.locator('#ge-download').click();

  const dialog = page.locator('.ge-export-dialog');
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox).toBeTruthy();
  expect(dialogBox.x).toBeGreaterThanOrEqual(0);
  expect(dialogBox.y).toBeGreaterThanOrEqual(0);
  expect(dialogBox.x + dialogBox.width).toBeLessThanOrEqual(390);
  expect(dialogBox.y + dialogBox.height).toBeLessThanOrEqual(844);

  await page.locator('#ge-export-width').fill('160');
  await page.locator('#ge-export-filename').fill('mobile-export');
  const downloadPromise = page.waitForEvent('download');
  await dialog.locator('button[type="submit"]').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('mobile-export.png');
  const bytes = fs.readFileSync(await download.path());
  expect(bytes.subarray(1, 4).toString('ascii')).toBe('PNG');
  expect(bytes.readUInt32BE(16)).toBe(160);
  expect(bytes.readUInt32BE(20)).toBe(120);
});

test('mobile touch moves an unlinked layer mask without moving its layer', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  await openBlankEditor(page, { width: 320, height: 240 }, 'Mobile mask movement');
  await page.locator('.ge-layers-title').click();
  await expect(page.locator('.ge-right-panel')).toHaveClass(/expanded/);
  const layerId = (await editorState(page)).activeLayerId;
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  const maskRow = page.locator('.ge-mask-sub-item').filter({ hasText: 'Layer Mask' });
  await expect(maskRow).toBeVisible();
  await maskRow.locator('.ge-mask-link-btn').click();
  await page.locator('.ge-tool-btn[data-tool="move"]').click();
  await page.locator('.ge-tool-btn[data-tool="move"]').click();

  const point = await page.evaluate(() => {
    const target = document.querySelector('.ge-main-canvas');
    const rect = target?.getBoundingClientRect();
    if (!target || !rect) return null;
    for (let y = rect.top + 8; y < rect.bottom - 8; y += 8) {
      for (let x = rect.left + 8; x < rect.right - 8; x += 8) {
        if (document.elementFromPoint(x, y) === target) return { x, y };
      }
    }
    return null;
  });
  expect(point).toBeTruthy();
  const before = await editorState(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: point.x, y: point.y, id: 1, radiusX: 6, radiusY: 6 }],
  });
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x: point.x + 24, y: point.y + 16, id: 1, radiusX: 6, radiusY: 6 }],
  });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

  const moved = await editorState(page);
  const beforeLayer = before.layers.find(layer => layer.id === layerId);
  const movedLayer = moved.layers.find(layer => layer.id === layerId);
  expect(movedLayer.offset).toEqual(beforeLayer.offset);
  expect(movedLayer.masks[0].offset.x).not.toBe(beforeLayer.masks[0].offset.x);
  expect(movedLayer.masks[0].offset.y).not.toBe(beforeLayer.masks[0].offset.y);
  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.id === layerId).masks[0].offset)
    .toEqual(beforeLayer.masks[0].offset);
});
