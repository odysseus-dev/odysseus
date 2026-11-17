const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers.js');

test('channel and path panels display hostile layer metadata as text', async ({ page }) => {
  await openBlankEditor(page, { width: 64, height: 64 });
  const result = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const { createColorPanels } = await import('/static/js/editor/panels/panels.js');
    const { createStudioPanels } = await import('/static/js/editor/panels/studio-panels.js');
    const attack = '<img src=x onerror="window.__panelXss=true">';
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.name = attack;
    layer.kind = 'shape';
    layer.shape = { type: attack };
    const colors = createColorPanels({ state });
    const studio = createStudioPanels({ state });
    document.body.append(colors.channels, studio.paths);
    colors.refresh();
    studio.refresh();
    return {
      attack,
      channel: colors.channels.querySelector('.ge-channel-name').textContent,
      path: studio.paths.querySelector('.ge-channel-name').textContent,
      images: colors.channels.querySelectorAll('img').length + studio.paths.querySelectorAll('img').length,
      executed: !!window.__panelXss,
    };
  });
  expect(result.channel).toBe(result.attack);
  expect(result.path).toBe(`${result.attack} · ${result.attack}`);
  expect(result.images).toBe(0);
  expect(result.executed).toBe(false);
});

test('unknown effect types cannot inject markup into the layer panel', async ({ page }) => {
  await openBlankEditor(page, { width: 64, height: 64 });
  const attack = '<img src=x onerror="window.__effectXss=true">';
  const id = await page.evaluate(async value => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.effects = [{ id: 'effect-test', type: value, visible: true, params: {} }];
    return layer.id;
  }, attack);
  await page.locator(`.ge-layer-item[data-layer-id="${id}"] .ge-layer-vis`).first().click();
  const list = page.locator('#ge-layers-list');
  await expect(list).toContainText('Gaussian Blur');
  await expect(list).not.toContainText(attack);
  await expect(list.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => !!window.__effectXss)).toBe(false);
});

test('saved brush preset names remain literal text in the flyout', async ({ page }) => {
  const attack = '<img src=x onerror="window.__brushXss=true">';
  await page.addInitScript(value => {
    localStorage.setItem('odysseus.editor.brush-presets.v1', JSON.stringify([
      { id: 'hostile-preset', name: value, size: 8, opacity: 100, flow: 100, softness: 20 },
    ]));
  }, attack);
  await openBlankEditor(page, { width: 64, height: 64 });
  const preset = page.locator('.ge-tool-flyout-preset[data-brush-preset="hostile-preset"]');
  await expect(preset.locator('.ge-tool-flyout-label')).toHaveText(attack);
  await expect(preset.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => !!window.__brushXss)).toBe(false);
});

test('imported adjustment parameters cannot inject markup into the adjustment popup', async ({ page }) => {
  await openBlankEditor(page, { width: 64, height: 64 });
  const attack = '"><img src=x onerror="window.__adjXss=true">';
  const cases = [
    { type: 'color-lookup', params: { cube: { size: attack, data: [0, 0, 0] }, strength: attack }, text: '.ge-adj-lut-name', expected: 'Loaded LUT · 0³' },
    { type: 'gradient-map', params: { shadows: attack, highlights: attack } },
    { type: 'levels', params: { inBlack: attack, outWhite: attack } },
  ];
  for (const item of cases) {
    await page.locator('#ge-add-fx').click();
    await page.locator(`.ge-add-layer-menu [data-adjustment-type="${item.type}"]`).click();
    await page.locator('.ge-adj-popup [data-adj-action="ok"]').click();
    await expect(page.locator('.ge-adj-popup')).toHaveCount(0);
    // Project files and drafts reach the editor through the codec's
    // normalizer, which validates the type but not every parameter.
    await page.evaluate(async ({ type, params }) => {
      const { state } = await import('/static/js/editor/state.js');
      const { normalizeAdjustmentData } = await import('/static/js/editor/adjustment-layer.js');
      const layer = state.layers.find(entry => entry.id === state.activeLayerId);
      layer.adjustment = normalizeAdjustmentData({ type, params });
    }, item);
    await page.locator('.ge-layer-controls:visible .ge-layer-fx-btn').click();
    const popup = page.locator('.ge-adj-popup');
    await expect(popup).toBeVisible();
    await expect(popup.locator('img')).toHaveCount(0);
    if (item.text) await expect(popup.locator(item.text)).toHaveText(item.expected);
    expect(await popup.evaluate(node => node.innerHTML.includes('onerror'))).toBe(false);
    await popup.locator('[data-adj-action="cancel"]').click();
  }
  expect(await page.evaluate(() => !!window.__adjXss)).toBe(false);
});
