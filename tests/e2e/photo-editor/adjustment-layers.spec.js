const { test, expect } = require('@playwright/test');
const {
  dragOnCanvas,
  encodedImagePixelDigest,
  editorState,
  flattenedPixelDigest,
  openBlankEditor,
  reopenDraft,
  waitForDraft,
} = require('./helpers.js');

async function addAdjustment(page, type) {
  await page.locator('#ge-add-layer').click();
  const menu = page.locator('.ge-add-layer-menu');
  await expect(menu).toBeVisible();
  await menu.locator(`[data-adjustment-type="${type}"]`).click();
  await expect(page.locator('.ge-adj-popup')).toBeVisible();
}

test('Levels is a retained stack layer with clipping, masks, history, and persistence', async ({ page }) => {
  await openBlankEditor(page, { width: 360, height: 260 }, 'Adjustment layers E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.18, y: 0.25 }, { x: 0.78, y: 0.7 });
  const before = await flattenedPixelDigest(page);

  await addAdjustment(page, 'levels');
  await page.locator('.ge-adj-channel-select').selectOption('red');
  await page.locator('.ge-adj-row input[data-key="outWhite"]').fill('128');
  await page.locator('.ge-adj-row input[data-key="outWhite"]').dispatchEvent('input');
  await page.locator('[data-adj-action="ok"]').click();

  let current = await editorState(page);
  const levels = current.layers.find(layer => layer.kind === 'adjustment');
  expect(levels.adjustment.type).toBe('levels');
  expect(levels.adjustment.params.channels.red.outWhite).toBe(128);
  expect(await flattenedPixelDigest(page)).not.toEqual(before);

  const row = page.locator(`.ge-layer-item[data-layer-id="${levels.id}"]`);
  await page.locator('#ge-layer-tools .ge-layer-clip-btn').click();
  expect((await editorState(page)).layers.find(layer => layer.id === levels.id).clipped).toBe(true);
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === levels.id).masks).toHaveLength(1);

  await row.locator('.ge-layer-vis').click();
  expect(await flattenedPixelDigest(page)).toEqual(before);
  await row.locator('.ge-layer-vis').click();
  await row.locator('.ge-layer-opacity').fill('45');
  await row.locator('.ge-layer-opacity').dispatchEvent('input');
  expect((await editorState(page)).layers.find(layer => layer.id === levels.id).opacity).toBeCloseTo(.45, 2);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.id === levels.id).opacity).toBe(1);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers.find(layer => layer.id === levels.id).opacity).toBeCloseTo(.45, 2);

  const draftId = await waitForDraft(page);
  const expected = await editorState(page);
  const expectedPixels = await flattenedPixelDigest(page);
  const exportedPng = await page.evaluate(async () => {
    const editor = await import('/static/js/galleryEditor.js');
    return editor.exportPNG();
  });
  expect(await encodedImagePixelDigest(
    page,
    Buffer.from(exportedPng.split(',', 2)[1], 'base64'),
  )).toEqual(expectedPixels);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === levels.id)).toEqual(expected.layers.find(layer => layer.id === levels.id));
  expect(await flattenedPixelDigest(page)).toEqual(expectedPixels);
});

test('Curves supports editable RGB and channel points with reset and cancel', async ({ page }) => {
  await openBlankEditor(page, { width: 360, height: 260 }, 'Curves E2E');
  await addAdjustment(page, 'curves');
  const curve = page.locator('.ge-curves-canvas');
  const box = await curve.boundingBox();
  await page.mouse.click(box.x + box.width * .5, box.y + box.height * .28);
  await page.locator('.ge-adj-channel-select').selectOption('blue');
  const blueBox = await curve.boundingBox();
  await page.mouse.click(blueBox.x + blueBox.width * .4, blueBox.y + blueBox.height * .68);
  await page.locator('[data-adj-action="ok"]').click();

  let current = await editorState(page);
  const curves = current.layers.find(layer => layer.kind === 'adjustment');
  expect(curves.adjustment.type).toBe('curves');
  expect(curves.adjustment.params.points.rgb).toHaveLength(3);
  expect(curves.adjustment.params.points.blue).toHaveLength(3);

  const row = page.locator(`.ge-layer-item[data-layer-id="${curves.id}"]`);
  await page.locator('#ge-layer-tools .ge-layer-fx-btn').click();
  await page.locator('[data-adj-action="reset"]').click();
  await page.locator('[data-adj-action="cancel"]').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === curves.id).adjustment.params.points.rgb).toHaveLength(3);
});

test('color adjustment controls retain neutral reusable parameters', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Color adjustments E2E');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    const gradient = layer.ctx.createLinearGradient(0, 0, layer.canvas.width, layer.canvas.height);
    gradient.addColorStop(0, '#b8324f');
    gradient.addColorStop(.5, '#56a56d');
    gradient.addColorStop(1, '#315fc4');
    layer.ctx.fillStyle = gradient;
    layer.ctx.fillRect(0, 0, layer.canvas.width, layer.canvas.height);
    window.galleryEditorComposite?.();
  });

  const cases = [
    { type: 'exposure', key: 'exposure', value: '100', check: p => p.exposure === 1 },
    { type: 'white-balance', key: 'temperature', value: '70', check: p => p.temperature === 70 },
    { type: 'hue-saturation', key: 'lightness', value: '18', check: p => p.lightness === 18 },
    { type: 'vibrance', key: 'vibrance', value: '55', check: p => p.vibrance === 55 },
    { type: 'black-white', key: 'red', value: '72', compare: true, check: p => p.red === 72 && p.green === 59 && p.blue === 11 },
    { type: 'shadows-highlights', key: 'shadows', value: '48', check: p => p.shadows === 48 },
    { type: 'color-balance', key: 'shadows-r', value: '45', check: p => p.shadows.r === 45 },
    { type: 'selective-color', key: 'cyan', value: '35', check: p => p.ranges.reds.cyan === 35 },
  ];
  let priorDigest = await flattenedPixelDigest(page);
  for (const item of cases) {
    await addAdjustment(page, item.type);
    const control = page.locator(`.ge-adj-row input[data-key="${item.key}"]`);
    await control.fill(item.value);
    await control.dispatchEvent('input');
    await page.locator('[data-adj-action="ok"]').click();
    const current = await editorState(page);
    const added = current.layers.filter(layer => layer.kind === 'adjustment').at(-1);
    expect(added.adjustment.type).toBe(item.type);
    expect(item.check(added.adjustment.params)).toBe(true);
    const nextDigest = await flattenedPixelDigest(page);
    expect(nextDigest).not.toBe(priorDigest);
    if (item.compare) {
      await page.locator('#ge-layer-tools .ge-layer-fx-btn').click();
      await expect(page.locator('.ge-adj-popup')).toBeVisible();
      await page.locator('[data-adj-action="compare"]').click();
      expect(await flattenedPixelDigest(page)).toEqual(priorDigest);
      await page.locator('[data-adj-action="compare"]').click();
      expect(await flattenedPixelDigest(page)).toEqual(nextDigest);
      await page.locator('[data-adj-action="cancel"]').click();
    }
    priorDigest = nextDigest;
  }

  await addAdjustment(page, 'gradient-map');
  await page.locator('[data-gradient-key="shadows"]').fill('#123456');
  await page.locator('[data-gradient-key="highlights"]').fill('#f0d080');
  await page.locator('[data-gradient-reverse]').check();
  await page.locator('[data-adj-action="ok"]').click();
  const current = await editorState(page);
  const gradient = current.layers.filter(layer => layer.kind === 'adjustment').at(-1);
  expect(gradient.adjustment).toEqual({
    type: 'gradient-map',
    params: { shadows: '#123456', highlights: '#f0d080', midpoint: 50, reverse: true },
  });
  const expectedPixels = await flattenedPixelDigest(page);
  const draftId = await waitForDraft(page);
  const expectedState = await editorState(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.filter(layer => layer.kind === 'adjustment')).toEqual(
    expectedState.layers.filter(layer => layer.kind === 'adjustment'),
  );
  expect(await flattenedPixelDigest(page)).toEqual(expectedPixels);
});

test('adjustment presets apply through the retained popup', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Adjustment presets E2E');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.fillStyle = '#5577aa';
    layer.ctx.fillRect(0, 0, layer.canvas.width, layer.canvas.height);
    window.galleryEditorComposite?.();
  });
  const before = await flattenedPixelDigest(page);
  await addAdjustment(page, 'exposure');
  await page.locator('.ge-adj-preset-select').selectOption('Lift Exposure');
  await page.locator('[data-adj-action="ok"]').click();
  const current = await editorState(page);
  const exposure = current.layers.find(layer => layer.kind === 'adjustment');
  expect(exposure.adjustment.params.exposure).toBe(0.45);
  expect(await flattenedPixelDigest(page)).not.toEqual(before);
});

test('adjustment popup keeps controls inside a narrow phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await openBlankEditor(page, { width: 240, height: 180 }, 'Mobile adjustment E2E');
  await addAdjustment(page, 'exposure');

  const popup = await page.locator('.ge-adj-popup').boundingBox();
  expect(popup).not.toBeNull();
  expect(popup.x).toBeGreaterThanOrEqual(0);
  expect(popup.x + popup.width).toBeLessThanOrEqual(320);
  for (const row of await page.locator('.ge-adj-row').all()) {
    const box = await row.boundingBox();
    expect(box).not.toBeNull();
    expect(box.x).toBeGreaterThanOrEqual(popup.x);
    expect(box.x + box.width).toBeLessThanOrEqual(popup.x + popup.width);
  }
  await expect(page.locator('.ge-adj-foot [data-adj-action="cancel"]')).toBeVisible();
  await expect(page.locator('.ge-adj-foot [data-adj-action="ok"]')).toBeVisible();
});

test('gradient map controls remain usable on a narrow phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await openBlankEditor(page, { width: 240, height: 180 }, 'Mobile gradient map E2E');
  await addAdjustment(page, 'gradient-map');

  const popup = await page.locator('.ge-adj-popup').boundingBox();
  const colors = page.locator('.ge-gradient-color-row');
  const colorBox = await colors.boundingBox();
  expect(popup).not.toBeNull();
  expect(colorBox).not.toBeNull();
  expect(colorBox.x).toBeGreaterThanOrEqual(popup.x);
  expect(colorBox.x + colorBox.width).toBeLessThanOrEqual(popup.x + popup.width);
  expect(await page.locator('.ge-gradient-color-row').evaluate(el => (
    getComputedStyle(el).gridTemplateColumns.trim().split(/\s+/).length
  ))).toBe(1);
});

test('all adjustment popups remain usable on a narrow phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await openBlankEditor(page, { width: 240, height: 180 }, 'Mobile adjustment matrix E2E');

  for (const type of [
    'brightness-contrast', 'exposure', 'white-balance', 'hue-saturation',
    'vibrance', 'black-white', 'shadows-highlights', 'levels', 'curves',
    'color-balance', 'selective-color', 'gradient-map',
  ]) {
    await addAdjustment(page, type);
    const popup = await page.locator('.ge-adj-popup').boundingBox();
    expect(popup, `${type} popup should be visible`).not.toBeNull();
    expect(popup.x).toBeGreaterThanOrEqual(0);
    expect(popup.x + popup.width).toBeLessThanOrEqual(320);

    const body = page.locator('.ge-adj-body');
    expect(await body.evaluate(el => el.scrollWidth <= el.clientWidth + 1), `${type} body should not overflow horizontally`).toBe(true);
    await expect(page.locator('.ge-adj-foot [data-adj-action="cancel"]')).toBeVisible();
    await expect(page.locator('.ge-adj-foot [data-adj-action="ok"]')).toBeVisible();
    await page.locator('[data-adj-action="cancel"]').click();
  }
});

test('retained Gaussian Blur survives flattening and draft reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Retained effects E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.2, y: 0.25 }, { x: 0.8, y: 0.7 });
  const before = await flattenedPixelDigest(page);

  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-blur-gaussian"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[data-key="radius"]').fill('14');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();

  let current = await editorState(page);
  const layer = current.layers.find(item => item.effects?.length);
  expect(layer.effects).toHaveLength(1);
  expect(layer.effects[0].type).toBe('gaussian-blur');
  expect(layer.effects[0].params.radius).toBe(14);
  const after = await flattenedPixelDigest(page);
  expect(after).not.toEqual(before);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  const reopened = current.layers.find(item => item.effects?.length);
  expect(reopened.effects).toEqual(layer.effects);
  expect(await flattenedPixelDigest(page)).toEqual(after);

  await page.locator('.ge-effect-sub-item .ge-adj-sub-name').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[data-key="radius"]').fill('22');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  expect((await editorState(page)).layers.find(item => item.effects?.length).effects[0].params.radius).toBe(22);
});

test('retained Sharpen keeps editable amount and survives draft reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Retained sharpen E2E');
  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-preset-crisp-detail"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  expect(await page.locator('.ge-filter-row input[data-key="amount"]').inputValue()).toBe('35');
  await page.locator('.ge-filter-row input[data-key="amount"]').fill('75');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  let current = await editorState(page);
  let layer = current.layers.find(item => item.effects?.length);
  expect(layer.effects[0].type).toBe('sharpen');
  expect(layer.effects[0].params.amount).toBeCloseTo(0.75, 2);
  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  layer = current.layers.find(item => item.effects?.length);
  expect(layer.effects[0].type).toBe('sharpen');
  expect(layer.effects[0].params.amount).toBeCloseTo(0.75, 2);
});

test('retained overlay and shadow effects keep editable metadata', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Retained color effects E2E');
  await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
  const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.move(canvasBox.x + 24, canvasBox.y + 24);
  await page.mouse.down();
  await page.mouse.move(canvasBox.x + 150, canvasBox.y + 140);
  await page.mouse.up();
  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-color-overlay"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[type="color"]').fill('#336699');
  await page.locator('.ge-filter-row input[data-key="opacity"]').fill('35');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  await page.locator('button[title="Add effect mask from selection"]').click();

  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-drop-shadow"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[data-key="blur"]').fill('18');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();

  let current = await editorState(page);
  let layer = current.layers.find(item => item.effects?.length);
  expect(layer.effects.map(effect => effect.type)).toEqual(['color-overlay', 'drop-shadow']);
  expect(layer.effects[0].params.color).toBe('#336699');
  expect(layer.effects[0].mask.size).toEqual([320, 220]);
  await page.locator('button[title="Hide effect mask"]').click();
  expect((await editorState(page)).layers.find(item => item.effects?.length).effects[0].mask.visible).toBe(false);
  await page.locator('button[title="Remove effect mask"]').click();
  expect((await editorState(page)).layers.find(item => item.effects?.length).effects[0].mask).toBeNull();
  expect(layer.effects[0].params.opacity).toBeCloseTo(0.35, 2);
  expect(layer.effects[1].params.blur).toBe(18);

  await page.locator('.ge-effect-sub-item').nth(0).locator('.ge-layer-vis').click();
  expect((await editorState(page)).layers.find(item => item.effects?.length).effects[0].visible).toBe(false);
  await page.locator('.ge-effect-sub-item').nth(1).locator('button[title="Move effect up"]').click();
  current = await editorState(page);
  layer = current.layers.find(item => item.effects?.length);
  expect(layer.effects.map(effect => effect.type)).toEqual(['drop-shadow', 'color-overlay']);
  await page.locator('.ge-effect-sub-item').nth(1).locator('button[title="Delete effect"]').click();
  expect((await editorState(page)).layers.find(item => item.effects?.length).effects).toHaveLength(1);
});

test('retained Stroke uses layer alpha and persists its controls', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 220 }, 'Retained stroke E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.25, y: 0.25 }, { x: 0.75, y: 0.7 });
  await page.locator('#ge-filter-menu-btn').click();
  await page.locator('[data-filter-action="effect-stroke"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[data-key="width"]').fill('9');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  const layer = (await editorState(page)).layers.find(item => item.effects?.length);
  expect(layer.effects[0].type).toBe('stroke');
  expect(layer.effects[0].params.width).toBe(9);
  const beforeRasterize = await flattenedPixelDigest(page);
  await page.locator('.ge-effect-sub-item button[title="Rasterize effects"]').click();
  await expect.poll(async () => (await editorState(page)).layers.find(item => item.name === 'Edit').effects.length).toBe(0);
  const rasterized = (await editorState(page)).layers.find(item => item.name === 'Edit');
  expect(rasterized.effects).toHaveLength(0);
  expect(await flattenedPixelDigest(page)).toEqual(beforeRasterize);
  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  expect((await editorState(page)).layers.find(item => item.name === 'Edit').effects).toHaveLength(0);
});
