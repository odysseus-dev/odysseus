const { test, expect } = require('@playwright/test');
const { selectTool } = require('./helpers.js');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers');

test('selection boundary animates, moves independently, and supports Quick Mask', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Selection workflow');
  await selectTool(page, 'marquee');
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.55, y: 0.55 });

  const initial = await editorState(page);
  expect(initial.selection?.space).toBe('document');
  expect(initial.selection?.bounds).toBeTruthy();
  const layerBefore = initial.layers.find(layer => layer.id === initial.activeLayerId);

  const overlayHash = () => page.locator('.ge-selection-overlay').evaluate(canvas => {
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3]) hash = ((hash * 33) ^ data[i]) >>> 0;
    return hash;
  });
  const firstOverlay = await overlayHash();
  expect(firstOverlay).not.toBe(0);
  await expect.poll(overlayHash, { timeout: 1200 }).not.toBe(firstOverlay);

  await dragOnCanvas(page, { x: 0.35, y: 0.35 }, { x: 0.45, y: 0.42 });
  const moved = await editorState(page);
  expect(moved.selection.bounds.x).toBeGreaterThan(initial.selection.bounds.x);
  expect(moved.selection.bounds.y).toBeGreaterThan(initial.selection.bounds.y);
  const layerAfterMove = moved.layers.find(layer => layer.id === moved.activeLayerId);
  expect(layerAfterMove.offset).toEqual(layerBefore.offset);
  expect(layerAfterMove.pixelHash).toBe(layerBefore.pixelHash);

  await page.keyboard.press('ArrowRight');
  await expect.poll(async () => (await editorState(page)).selection.bounds.x).toBe(moved.selection.bounds.x + 1);

  await page.keyboard.press('q');
  await expect(page.locator('#ge-quick-mask-bar')).toBeVisible();
  expect((await editorState(page)).quickMaskActive).toBe(true);
  const beforePaint = (await editorState(page)).selection.pixelHash;
  await dragOnCanvas(page, { x: 0.72, y: 0.72 }, { x: 0.78, y: 0.72 });
  await expect.poll(async () => (await editorState(page)).selection.pixelHash).not.toBe(beforePaint);

  await page.locator('.ge-quick-mask-done').click();
  await expect(page.locator('#ge-quick-mask-bar')).toBeHidden();
  expect((await editorState(page)).quickMaskActive).toBe(false);
});

test('marquee supports exact fixed-ratio geometry', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Precise marquee');
  await selectTool(page, 'marquee');

  await page.locator('#ge-marquee-constraint').selectOption('ratio');
  await page.locator('#ge-marquee-width').fill('4');
  await page.locator('#ge-marquee-width').blur();
  await page.locator('#ge-marquee-height').fill('3');
  await page.locator('#ge-marquee-height').blur();
  await dragOnCanvas(page, { x: 0.1, y: 0.1 }, { x: 0.6, y: 0.3 });
  const ratioBounds = (await editorState(page)).selection.bounds;
  expect(ratioBounds.width).toBe(160);
  expect(ratioBounds.height).toBe(120);
  expect(ratioBounds.width / ratioBounds.height).toBeCloseTo(4 / 3, 5);
});

test('transform selection moves, scales, rotates, cancels, and preserves layer pixels', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Transform selection');
  await selectTool(page, 'marquee');
  await dragOnCanvas(page, { x: 0.2, y: 0.25 }, { x: 0.5, y: 0.55 });
  const original = await editorState(page);
  const originalLayer = original.layers.find(layer => layer.id === original.activeLayerId);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="transform"]').click();
  await expect(page.locator('#ge-transform-section')).toBeVisible();
  await expect(page.locator('.ge-transform-popup .ge-adj-title')).toHaveText('Transform Selection');
  await dragOnCanvas(page, { x: 0.35, y: 0.4 }, { x: 0.45, y: 0.47 });
  const moved = await editorState(page);
  expect(moved.selection.bounds.x).toBeGreaterThan(original.selection.bounds.x);
  expect(moved.selection.bounds.y).toBeGreaterThan(original.selection.bounds.y);
  await page.locator('#ge-transform-w').fill('140');
  await page.locator('#ge-transform-w').dispatchEvent('input');
  await page.locator('#ge-transform-rot').fill('25');
  await page.locator('#ge-transform-rot').dispatchEvent('input');
  await page.locator('#ge-transform-apply').click();

  let transformed = await editorState(page);
  const transformedLayer = transformed.layers.find(layer => layer.id === transformed.activeLayerId);
  expect(transformed.selection.pixelHash).not.toBe(original.selection.pixelHash);
  expect(transformed.selection.bounds.width).toBeGreaterThan(original.selection.bounds.width);
  expect(transformedLayer.offset).toEqual(originalLayer.offset);
  expect(transformedLayer.pixelHash).toBe(originalLayer.pixelHash);

  await page.keyboard.press('Control+z');
  await expect.poll(async () => (await editorState(page)).selection.pixelHash).toBe(original.selection.pixelHash);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="transform"]').click();
  await page.locator('#ge-transform-h').fill('40');
  await page.locator('#ge-transform-h').dispatchEvent('input');
  expect((await editorState(page)).selection.pixelHash).not.toBe(original.selection.pixelHash);
  await page.locator('#ge-transform-cancel-btn').click();
  transformed = await editorState(page);
  expect(transformed.selection.pixelHash).toBe(original.selection.pixelHash);
  expect(transformed.redo).toBe(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="transform"]').click();
  await expect(page.locator('#ge-transform-section')).toBeVisible();
  const mobileSection = await page.locator('#ge-transform-section').boundingBox();
  expect(mobileSection.x).toBeGreaterThanOrEqual(0);
  expect(mobileSection.y).toBeGreaterThanOrEqual(0);
  expect(mobileSection.x + mobileSection.width).toBeLessThanOrEqual(390);
  expect(mobileSection.y + mobileSection.height).toBeLessThanOrEqual(844);
  await page.locator('#ge-transform-cancel-btn').click();
  await expect.poll(() => page.evaluate(async () => (await import('/static/js/editor/state.js')).state.transformActive)).toBe(false);
  expect((await editorState(page)).selection.pixelHash).toBe(original.selection.pixelHash);
});

test('named selections support reselect, load, delete, and server-draft reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Saved selections');
  await selectTool(page, 'marquee');
  await dragOnCanvas(page, { x: 0.15, y: 0.2 }, { x: 0.5, y: 0.6 });
  const original = (await editorState(page)).selection;

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('#ge-selection-name').fill('Subject');
  await page.locator('[data-selection-action="save"]').click();
  let current = await editorState(page);
  expect(current.savedSelections).toHaveLength(1);
  expect(current.savedSelections[0].name).toBe('Subject');
  expect(current.savedSelections[0].pixelHash).toBe(original.pixelHash);

  await page.locator('[data-selection-action="deselect"]').click();
  current = await editorState(page);
  expect(current.selection).toBeNull();
  expect(current.lastSelection.pixelHash).toBe(original.pixelHash);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="reselect"]').click();
  expect((await editorState(page)).selection.pixelHash).toBe(original.pixelHash);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('[data-selection-action="deselect"]').click();
  await dragOnCanvas(page, { x: 0.62, y: 0.15 }, { x: 0.88, y: 0.4 });
  expect((await editorState(page)).selection.pixelHash).not.toBe(original.pixelHash);
  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('.ge-saved-selection-load', { hasText: 'Subject' }).click();
  expect((await editorState(page)).selection.pixelHash).toBe(original.pixelHash);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.savedSelections).toHaveLength(1);
  expect(current.savedSelections[0].pixelHash).toBe(original.pixelHash);
  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('.ge-saved-selection-load', { hasText: 'Subject' }).click();
  expect((await editorState(page)).selection.pixelHash).toBe(original.pixelHash);

  await page.locator('#ge-selection-menu-btn').click();
  await page.locator('.ge-saved-selection-delete').click();
  expect((await editorState(page)).savedSelections).toHaveLength(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#ge-selection-menu')).toBeHidden();
  await page.waitForTimeout(250);
  await page.locator('#ge-selection-menu-btn').click();
  await expect(page.locator('#ge-selection-menu')).toBeVisible();
  const mobileMenu = await page.locator('#ge-selection-menu').boundingBox();
  expect(mobileMenu.x).toBeGreaterThanOrEqual(0);
  expect(mobileMenu.x + mobileMenu.width).toBeLessThanOrEqual(390);
});

test('marquee released over the right panel commits a clamped selection', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Marquee overshoot');
  await selectTool(page, 'marquee');
  const setup = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const rect = state.mainCanvas.getBoundingClientRect();
    const panel = document.querySelector('.ge-right-panel');
    const panelBox = panel ? panel.getBoundingClientRect() : null;
    return {
      document: { width: state.imgWidth, height: state.imgHeight },
      start: { x: rect.left + rect.width * 0.2, y: rect.top + rect.height * 0.2 },
      panelPoint: panelBox
        ? { x: panelBox.left + panelBox.width / 2, y: rect.top + rect.height * 0.6 }
        : null,
    };
  });
  expect(setup.panelPoint, 'right panel must be visible on desktop').toBeTruthy();

  await page.mouse.move(setup.start.x, setup.start.y);
  await page.mouse.down();
  // Drag straight onto the right panel. The window mousemove guard must
  // keep the raw rect from extending off-canvas, and the release must
  // commit a clamped selection rather than discard it.
  await page.mouse.move(setup.panelPoint.x, setup.panelPoint.y, { steps: 12 });
  await page.mouse.up();

  const result = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    if (!state.wandMask) return { active: false };
    const data = state.wandMask.getContext('2d')
      .getImageData(0, 0, state.wandMask.width, state.wandMask.height).data;
    let left = state.wandMask.width, top = state.wandMask.height, right = -1, bottom = -1;
    for (let y = 0; y < state.wandMask.height; y += 1) {
      for (let x = 0; x < state.wandMask.width; x += 1) {
        if (data[(y * state.wandMask.width + x) * 4 + 3] < 1) continue;
        left = Math.min(left, x); top = Math.min(top, y);
        right = Math.max(right, x); bottom = Math.max(bottom, y);
      }
    }
    return { active: true, left, top, right, bottom, width: state.wandMask.width, height: state.wandMask.height };
  });
  expect(result.active).toBe(true);
  expect(result.right).toBeGreaterThanOrEqual(result.left);
  expect(result.bottom).toBeGreaterThanOrEqual(result.top);
  // Clamped: never beyond the document edge on any side.
  expect(result.left).toBeGreaterThanOrEqual(0);
  expect(result.top).toBeGreaterThanOrEqual(0);
  expect(result.right).toBeLessThan(result.width);
  expect(result.bottom).toBeLessThan(result.height);
  expect(result.right - result.left).toBeGreaterThan(0);
  expect(result.bottom - result.top).toBeGreaterThan(0);
});
