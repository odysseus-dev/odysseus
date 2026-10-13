import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { appCss } from './helpers/stylesheets.mjs';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://editor.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<button id="fx" style="position:fixed;bottom:4px;right:4px">fx</button>' });
    if (!path.endsWith('.js')) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('../static/js/editor' + path, import.meta.url), 'utf8') });
  });
  await page.goto('http://editor.test/');
  const results = await page.evaluate(async () => {
    const { LAYER_STYLES } = await import('/layer-styles.js');
    const { normalizeEffect, renderEffects, renderEffectsAsync, effectsWithPreview } = await import('/effects.js');
    const source = document.createElement('canvas'); source.width = source.height = 64;
    const ctx = source.getContext('2d'); ctx.fillStyle = '#668899'; ctx.fillRect(16, 16, 32, 32);
    const original = ctx.getImageData(0, 0, 64, 64).data;
    const shadow = normalizeEffect({ id: 'shadow', type: 'drop-shadow', params: { color: '#ff0000', opacity: 1, blur: 0, x: 8, y: 8 } });
    const shadowed = renderEffects(source, [shadow]);
    const pixel = (canvas, x, y) => [...canvas.getContext('2d').getImageData(x, y, 1, 1).data];
    if (String(pixel(shadowed, 30, 30)) !== String(pixel(source, 30, 30))) throw Error('shadow covers source');
    if (String(pixel(shadowed, 50, 50)) !== '255,0,0,255') throw Error('shadow offset or color');
    const workerShadow = await renderEffectsAsync(source, [shadow]);
    if (String(pixel(workerShadow, 50, 50)) !== String(pixel(shadowed, 50, 50))) throw Error('shadow worker mismatch');
    const edited = { ...shadow, params: { ...shadow.params, x: 12 } };
    const preview = effectsWithPreview({ effects: [shadow], _effectPreview: edited });
    if (preview.length !== 1 || preview[0] !== edited) throw Error('duplicate preview');
    const result = [];
    for (const type of Object.keys(LAYER_STYLES)) {
      const effect = normalizeEffect({ type });
      if (normalizeEffect(JSON.parse(JSON.stringify(effect))).type !== type) throw Error('restore ' + type);
      const sync = renderEffects(source, [effect]).getContext('2d').getImageData(0, 0, 64, 64).data;
      if (!sync.some((v, i) => v !== original[i])) throw Error('no effect ' + type);
      const asyncCanvas = await renderEffectsAsync(source, [effect]);
      const asyncPixels = asyncCanvas.getContext('2d').getImageData(0, 0, 64, 64).data;
      const difference = sync.reduce((max, v, i) => Math.max(max, Math.abs(v - asyncPixels[i])), 0);
      if (difference > 2) throw Error('worker mismatch ' + type + ': ' + difference);
      const hidden = renderEffects(source, [{ ...effect, visible: false }]).getContext('2d').getImageData(0, 0, 64, 64).data;
      if (hidden.some((v, i) => v !== original[i])) throw Error('hidden ' + type);
      if (ctx.getImageData(0, 0, 64, 64).data.some((v, i) => v !== original[i])) throw Error('mutated ' + type);
      result.push(type);
    }
    return result;
  });
  assert.equal(results.length, 7);
  await page.addStyleTag({ content: await appCss() });
  await page.evaluate(async () => {
    const { openLayerStyleMenu } = await import('/layer-style-menu.js');
    document.querySelector('#fx').onclick = event => { event.stopPropagation(); openLayerStyleMenu(event.currentTarget, () => {}); };
  });
  for (const viewport of [{ width: 1280, height: 720 }, { width: 375, height: 420 }]) {
    await page.setViewportSize(viewport);
    await page.locator('#fx').click();
    const menu = page.locator('.ge-layer-style-menu');
    assert.equal(await menu.locator('button').count(), 10);
    const bounds = await menu.boundingBox();
    assert.equal(await menu.evaluate(el => getComputedStyle(el).opacity), '1');
    assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height);
    await page.screenshot({ path: `/tmp/editor-layer-styles-${viewport.width}.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(30);
    assert.equal(await menu.count(), 0);
  }
  await page.locator('#fx').click();
  await page.locator('#fx').click();
  await page.waitForTimeout(30);
  assert.equal(await page.locator('.ge-layer-style-menu').count(), 0, 'repeat click closes menu');
  console.log('7 styles: render, worker parity, restoration, visibility and source preservation passed; 10-item menu fits desktop/mobile and closes on Escape.');
} finally { await browser.close(); }
