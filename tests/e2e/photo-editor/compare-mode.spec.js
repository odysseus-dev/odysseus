const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor } = require('./helpers.js');

async function mainCanvasHash(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('.ge-main-canvas');
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 2166136261;
    for (let index = 0; index < data.length; index += 4) {
      hash ^= data[index];
      hash = Math.imul(hash, 16777619);
      hash ^= data[index + 1];
      hash = Math.imul(hash, 16777619);
      hash ^= data[index + 2];
      hash = Math.imul(hash, 16777619);
      hash ^= data[index + 3];
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  });
}

test('before compare is visual-only and toggles back to the edited document', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 180 }, 'Compare mode E2E');
  await expect(page.locator('#ge-compare-btn')).toBeVisible();
  await expect.poll(async () => (await editorState(page)).documentRenderReady).toBe(true);

  const baselineHash = await mainCanvasHash(page);
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.2, y: 0.3 }, { x: 0.8, y: 0.7 });
  const editedState = await editorState(page);
  const editedHash = await mainCanvasHash(page);
  expect(editedHash).not.toBe(baselineHash);

  await page.locator('#ge-compare-btn').click();
  await expect(page.locator('#ge-compare-btn')).toHaveAttribute('aria-pressed', 'true');
  expect(await mainCanvasHash(page)).toBe(baselineHash);
  expect((await editorState(page)).layers).toEqual(editedState.layers);

  await page.locator('#ge-compare-btn').click();
  await expect(page.locator('#ge-compare-btn')).toHaveAttribute('aria-pressed', 'false');
  expect(await mainCanvasHash(page)).toBe(editedHash);
});
