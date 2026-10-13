const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

test('text edits directly on canvas and remains retained after reopen', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="text"]').click();
  await page.locator('#ge-text-frame-width').fill('260');
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(canvas.x + canvas.width * 0.2, canvas.y + canvas.height * 0.2);
  const editor = page.locator('.ge-direct-text-editor');
  await expect(editor).toBeVisible();
  await editor.fill('Editable canvas title');
  await editor.press('Control+Enter');
  await expect(editor).toHaveCount(0);

  await page.locator('#ge-text-letter-spacing').fill('3.5');
  await page.locator('#ge-text-line-height').fill('1.4');
  await page.locator('#ge-text-font').selectOption('Georgia');
  await page.locator('.ge-text-auto-width-option').click();
  await expect(page.locator('#ge-text-auto-width')).toBeChecked();
  await expect(page.locator('#ge-text-frame-width')).toBeDisabled();
  await page.locator('#ge-text-frame-height').fill('180');
  await page.locator('#ge-text-vertical-align').selectOption('bottom');
  const before = await editorState(page);
  const textLayer = before.layers.find(layer => layer.kind === 'text');
  expect(textLayer.text).toMatchObject({
    content: 'Editable canvas title',
    fontFamily: 'Georgia',
    lineHeight: 1.4,
    letterSpacing: 3.5,
    autoWidth: true,
    frameHeight: 180,
    verticalAlign: 'bottom',
  });

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(layer => layer.kind === 'text').text).toEqual(textLayer.text);
});

test('text paragraph controls remain usable in a narrow phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await openBlankEditor(page, { width: 240, height: 180 }, 'Mobile text layout E2E');
  await page.locator('.ge-tool-btn[data-tool="text"]').click();
  const section = page.locator('#ge-text-section');
  await expect(section).toBeVisible();
  expect(await section.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await expect(page.locator('#ge-text-frame-height')).toBeVisible();
  await expect(page.locator('#ge-text-vertical-align')).toBeVisible();
});

test('rectangle ellipse line and polygon remain editable shape layers', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="shape"]').click();
  await expect(page.locator('#ge-shape-section')).toBeVisible();

  const types = ['rectangle', 'ellipse', 'line', 'polygon'];
  for (let index = 0; index < types.length; index += 1) {
    const type = types[index];
    await page.locator('.ge-layer-item').filter({ hasText: 'Edit' }).first().click();
    await page.locator(`[data-shape-type="${type}"]`).click();
    await dragOnCanvas(
      page,
      { x: 0.12 + index * 0.18, y: 0.2 },
      { x: 0.25 + index * 0.18, y: 0.42 },
    );
  }

  let current = await editorState(page);
  const shapes = current.layers.filter(layer => layer.kind === 'shape');
  expect(shapes.map(layer => layer.shape.type)).toEqual(types);
  await page.locator('#ge-shape-sides').fill('7');
  await page.locator('#ge-shape-stroke-width').fill('6');
  await page.locator('#ge-shape-radius').fill('14');
  current = await editorState(page);
  const polygon = current.layers.find(layer => layer.kind === 'shape' && layer.shape.type === 'polygon');
  expect(polygon.shape).toMatchObject({ sides: 7, strokeWidth: 6, cornerRadius: 14 });

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  const originalWidth = Number(await page.locator('#ge-transform-w').inputValue());
  await page.locator('#ge-transform-w').fill(String(originalWidth + 40));
  await page.locator('#ge-transform-apply').click();
  current = await editorState(page);
  const transformed = current.layers.find(layer => layer.id === polygon.id);
  expect(transformed.kind).toBe('shape');
  expect(transformed.shape.transform.scaleX).toBeGreaterThan(1);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.layers.filter(layer => layer.kind === 'shape').map(layer => layer.shape.type)).toEqual(types);
});

test('gradient shape fill remains editable after reopening the project', async ({ page }) => {
  await openBlankEditor(page);
  await page.locator('.ge-tool-btn[data-tool="shape"]').click();
  await expect(page.locator('#ge-shape-gradient-add-stop')).toBeHidden();
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.65, y: 0.55 });
  await page.locator('#ge-shape-gradient-angle').evaluate((angle) => {
    const section = angle.closest('#ge-shape-section');
    section.querySelector('#ge-shape-fill-type').value = 'linear-gradient';
    section.querySelector('#ge-shape-gradient-start').value = '#ff0000';
    section.querySelector('#ge-shape-gradient-mid').value = '#00ff00';
    section.querySelector('#ge-shape-gradient-mid-enabled').checked = true;
    section.querySelector('#ge-shape-gradient-mid-position').value = '42';
    section.querySelector('#ge-shape-gradient-end').value = '#0000ff';
    section.querySelector('#ge-shape-fill-type').dispatchEvent(new Event('change', { bubbles: true }));
    angle.value = '35';
    angle.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('#ge-shape-gradient-add-stop')).toBeVisible();

  const before = await editorState(page);
  const shape = before.layers.find(layer => layer.kind === 'shape');
  expect(shape.shape).toMatchObject({
    fillType: 'linear-gradient',
    gradientStart: '#ff0000',
    gradientMid: '#00ff00',
    gradientMidEnabled: true,
    gradientMidPosition: 42,
    gradientEnd: '#0000ff',
    gradientAngle: 35,
  });
  expect(page.locator('.ge-layer-inline-thumb')).toHaveCount(before.layers.length);
  const thumbnailColorRange = await page.locator('.ge-layer-inline-thumb').evaluateAll((canvases) => canvases.map(canvas => {
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return {
      red: Math.max(...Array.from(pixels).filter((_, index) => index % 4 === 0)),
      blue: Math.max(...Array.from(pixels).filter((_, index) => index % 4 === 2)),
    };
  }));
  expect(thumbnailColorRange.some(({ red, blue }) => red > 180 && blue > 180)).toBe(true);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(layer => layer.id === shape.id).shape).toMatchObject(shape.shape);
});

test('shape gradients retain added stops through editing and reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 360, height: 240 }, 'Multi-stop gradient E2E');
  await page.locator('.ge-tool-btn[data-tool="shape"]').click();
  await dragOnCanvas(page, { x: 0.15, y: 0.2 }, { x: 0.75, y: 0.65 });
  await page.locator('#ge-shape-fill-type').selectOption('linear-gradient');
  await page.evaluate(() => {
    for (const [id, value] of [['ge-shape-gradient-start', '#ff0000'], ['ge-shape-gradient-end', '#0000ff']]) {
      const input = document.getElementById(id);
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  await page.locator('#ge-shape-gradient-add-stop').click();
  await expect(page.locator('[data-gradient-extra-stop]')).toHaveCount(1);
  await page.evaluate(() => {
    const color = document.querySelector('[data-gradient-stop-color]');
    color.value = '#00ff00';
    color.dispatchEvent(new Event('input', { bubbles: true }));
    const position = document.querySelector('[data-gradient-stop-position]');
    position.value = '50';
    position.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.locator('#ge-shape-gradient-add-stop').click();
  await expect(page.locator('[data-gradient-extra-stop]')).toHaveCount(2);
  await page.evaluate(() => {
    const rows = [...document.querySelectorAll('[data-gradient-extra-stop]')];
    const setRow = (row, colorValue, positionValue) => {
      const color = row.querySelector('[data-gradient-stop-color]');
      color.value = colorValue;
      color.dispatchEvent(new Event('input', { bubbles: true }));
      const position = row.querySelector('[data-gradient-stop-position]');
      position.value = positionValue;
      position.dispatchEvent(new Event('input', { bubbles: true }));
    };
    setRow(rows[0], '#00ff00', '50');
    setRow(rows[1], '#ffff00', '75');
  });

  let current = await editorState(page);
  const shape = current.layers.find(layer => layer.kind === 'shape');
  expect(shape.shape.gradientStops).toEqual([
    { position: 0, color: '#ff0000' },
    { position: 50, color: '#00ff00' },
    { position: 75, color: '#ffff00' },
    { position: 100, color: '#0000ff' },
  ]);

  await page.locator('[data-gradient-extra-stop]').first().locator('[data-gradient-stop-remove]').click();
  current = await editorState(page);
  expect(current.layers.find(layer => layer.id === shape.id).shape.gradientStops).toEqual([
    { position: 0, color: '#ff0000' },
    { position: 75, color: '#ffff00' },
    { position: 100, color: '#0000ff' },
  ]);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.layers.find(layer => layer.id === shape.id).shape.gradientStops).toEqual([
    { position: 0, color: '#ff0000' },
    { position: 75, color: '#ffff00' },
    { position: 100, color: '#0000ff' },
  ]);
});

test('shape gradient stop normalization keeps imported endpoints bounded', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 180 }, 'Gradient stop bounds E2E');
  const result = await page.evaluate(async () => {
    const { MAX_GRADIENT_STOPS, normalizeGradientStops } = await import('/static/js/editor/gradient-stops.js');
    const stops = normalizeGradientStops(Array.from({ length: 30 }, (_, index) => ({
      position: index * 3,
      color: `#${String(index).padStart(6, '0')}`,
    })));
    return { max: MAX_GRADIENT_STOPS, stops };
  });
  expect(result.stops).toHaveLength(result.max);
  expect(result.stops[0].position).toBe(0);
  expect(result.stops.at(-1).position).toBe(100);
});

test('gradient tool paints a reversible drag on the active layer', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Gradient tool E2E');
  await page.locator('.ge-tool-btn[data-tool="gradient"]').click();
  await expect(page.locator('#ge-gradient-section')).toBeVisible();

  await page.evaluate(() => {
    for (const [id, value] of [['ge-gradient-start', '#ff0000'], ['ge-gradient-end', '#0000ff']]) {
      const input = document.getElementById(id);
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const midpoint = document.getElementById('ge-gradient-mid');
    midpoint.value = '#00ff00';
    midpoint.dispatchEvent(new Event('input', { bubbles: true }));
    const enabled = document.getElementById('ge-gradient-mid-enabled');
    enabled.checked = true;
    enabled.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.locator('#ge-gradient-add-stop').click();
  await page.evaluate(() => {
    const row = document.querySelector('#ge-gradient-extra-stops [data-gradient-extra-stop]');
    const color = row.querySelector('[data-gradient-stop-color]');
    color.value = '#ffff00';
    color.dispatchEvent(new Event('input', { bubbles: true }));
    const position = row.querySelector('[data-gradient-stop-position]');
    position.value = '25';
    position.dispatchEvent(new Event('input', { bubbles: true }));
  });

  const box = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.move(box.x + 8, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 8, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();

  await expect.poll(async () => (await editorState(page)).layers.at(-1).effects?.length || 0).toBe(1);
  await expect.poll(async () => (await editorState(page)).documentRenderReady).toBe(true);

  const samples = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    const rendered = state.documentCompositeCanvas;
    const ctx = rendered.getContext('2d');
    const y = Math.floor(rendered.height / 2);
    return [
      ctx.getImageData(4, y, 1, 1).data,
      ctx.getImageData(Math.floor(rendered.width / 2), y, 1, 1).data,
      ctx.getImageData(rendered.width - 5, y, 1, 1).data,
    ];
  });
  expect(samples[0][0]).toBeGreaterThan(220);
  expect(samples[0][2]).toBeLessThan(40);
  expect(samples[1][1]).toBeGreaterThan(180);
  expect(samples[1][0]).toBeLessThan(80);
  expect(samples[1][2]).toBeLessThan(80);
  expect(samples[2][2]).toBeGreaterThan(220);
  expect(samples[2][0]).toBeLessThan(40);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  const reopenedGradient = reopened.layers.find(layer => layer.effects?.some(effect => effect.type === 'linear-gradient'));
  expect(reopenedGradient).toBeTruthy();
  expect(reopenedGradient.effects.find(effect => effect.type === 'linear-gradient').params.stops).toEqual([
    { position: 0, color: '#ff0000', alpha: 1 },
    { position: 25, color: '#ffff00', alpha: 1 },
    { position: 50, color: '#00ff00', alpha: 1 },
    { position: 100, color: '#0000ff', alpha: 1 },
  ]);

  await page.locator('.ge-effect-sub-item .ge-adj-sub-name').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await expect(page.locator('.ge-filter-row input[data-key="stopColor0"]')).toHaveValue('#ffff00');
  await expect(page.locator('.ge-filter-row input[data-key="stopPosition0"]')).toHaveValue('25');
  await expect(page.locator('.ge-filter-row input[data-key="stopColor1"]')).toHaveValue('#00ff00');
  await page.locator('.ge-filter-row input[data-key="stopColor0"]').fill('#ffff00');
  await page.locator('.ge-filter-row input[data-key="stopPosition0"]').fill('30');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  const edited = (await editorState(page)).layers
    .flatMap(layer => layer.effects || [])
    .find(effect => effect.type === 'linear-gradient');
  expect(edited.params.stops).toEqual([
    { position: 0, color: '#ff0000', alpha: 1 },
    { position: 30, color: '#ffff00', alpha: 1 },
    { position: 50, color: '#00ff00', alpha: 1 },
    { position: 100, color: '#0000ff', alpha: 1 },
  ]);
});

test('radial gradient remains retained and survives reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Radial gradient E2E');
  await page.locator('.ge-tool-btn[data-tool="gradient"]').click();
  await page.locator('#ge-gradient-type').selectOption('radial-gradient');
  await page.evaluate(() => {
    const start = document.getElementById('ge-gradient-start');
    start.value = '#ffffff';
    start.dispatchEvent(new Event('input', { bubbles: true }));
    const end = document.getElementById('ge-gradient-end');
    end.value = '#000000';
    end.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await dragOnCanvas(page, { x: 0.5, y: 0.5 }, { x: 0.85, y: 0.5 });
  let current = await editorState(page);
  const gradient = current.layers.flatMap(layer => layer.effects || []).find(effect => effect.type === 'radial-gradient');
  expect(gradient).toBeTruthy();
  expect(gradient.params.stops).toEqual([
    { position: 0, color: '#ffffff', alpha: 1 },
    { position: 100, color: '#000000', alpha: 1 },
  ]);
  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.layers.flatMap(layer => layer.effects || []).some(effect => effect.type === 'radial-gradient')).toBe(true);
});
