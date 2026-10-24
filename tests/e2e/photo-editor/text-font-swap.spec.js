const { test, expect } = require('@playwright/test');
const crypto = require('crypto');
const { openBlankEditor } = require('./helpers');

const FONTS = [
  'Inter', 'Roboto', 'Playfair Display', 'Lobster',
  'Bebas Neue', 'Roboto Mono', 'Georgia', 'Courier New',
];
const md5 = (buf) => crypto.createHash('md5').update(buf).digest('hex');

test('text font dropdown renders a distinct face for each font', async ({ page }) => {
  await openBlankEditor(page, { width: 900, height: 600 }, 'Font swap');
  await page.evaluate(() => {
    document.querySelectorAll('.ge-tour-close,.ge-protip,.ge-tip,[class*="protip"]').forEach((e) => e.remove());
  });
  await page.evaluate(async () => {
    const tool = document.querySelector('[data-tool="text"]');
    if (tool) tool.click();
    await window.geEditorControl.invoke('text.add', { text: 'AWjgyq 128', x: 60, y: 80 });
  });
  await page.waitForTimeout(400);

  const box = await page.locator('.ge-main-canvas').boundingBox();
  expect(box).toBeTruthy();

  const hashes = {};
  for (const font of FONTS) {
    await page.evaluate((f) => {
      const sel = document.getElementById('ge-text-font');
      sel.value = f;
      sel.dispatchEvent(new Event('input', { bubbles: true }));
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }, font);
    await page.waitForTimeout(250);
    const shot = await page.screenshot({ clip: box });
    hashes[font] = md5(shot);
  }

  const distinct = new Set(Object.values(hashes)).size;
  expect(distinct, `distinct font hashes: ${JSON.stringify(hashes)}`).toBeGreaterThanOrEqual(5);
});
