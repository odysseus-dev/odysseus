const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const {
  compareExportPixels,
  dragOnCanvas,
  editorState,
  flattenedPixelDigest,
  openBlankEditor,
  openExportDialog,
  reopenDraft,
  waitForDraft,
} = require('./helpers.js');

async function downloadPng(page, filename) {
  await openExportDialog(page);
  await page.locator('[data-format="png"]').click();
  await page.locator('#ge-export-filename').fill(filename);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('.ge-export-dialog button[type="submit"]').click();
  const download = await downloadPromise;
  return fs.readFileSync(await download.path());
}

test('group retained effects edit, toggle, reorder surface, and survive reopen', async ({ page, request }) => {
  await openBlankEditor(page, { width: 360, height: 240 }, 'Group effects E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 48;
    state.brushSoftness = 0;
  });
  await dragOnCanvas(page, { x: 0.25, y: 0.35 }, { x: 0.75, y: 0.65 });

  const backgroundRow = page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first();
  await backgroundRow.click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  const groupId = (await editorState(page)).groups[0].id;
  const groupRow = page.locator(`.ge-layer-group-row[data-group-id="${groupId}"]`);

  await page.locator('#ge-layer-tools .ge-layer-btn[title*="effect"]').click();
  await page.locator('[data-filter-action="effect-blur-gaussian"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[data-key="radius"]').fill('14');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();

  let current = await editorState(page);
  expect(current.groups[0].effects).toHaveLength(1);
  expect(current.groups[0].effects[0].params.radius).toBe(14);
  const afterAdd = await flattenedPixelDigest(page);
  await expect(page.locator('.ge-group-effect-sub-item')).toHaveCount(1);

  await page.locator('.ge-group-effect-sub-item .ge-adj-sub-name').click();
  await page.locator('.ge-filter-row input[data-key="radius"]').fill('22');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();
  current = await editorState(page);
  expect(current.groups[0].effects[0].params.radius).toBe(22);
  expect(await flattenedPixelDigest(page)).not.toEqual(afterAdd);

  await page.locator('.ge-group-effect-sub-item .ge-layer-vis').click();
  expect((await editorState(page)).groups[0].effects[0].visible).toBe(false);
  await page.locator('.ge-group-effect-sub-item .ge-layer-vis').click();

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  expect((await editorState(page)).groups).toEqual(beforeReopen.groups);
  await expect(page.locator('.ge-group-effect-sub-item')).toHaveCount(1);

  await page.locator(`.ge-layer-group-row[data-group-id="${groupId}"]`).click();
  await expect(page.locator('.ge-group-effect-sub-item')).toHaveCount(1);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('group retained effects export the visible composite and survive reopen', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 180 }, 'Group effects export E2E');
  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 64;
    state.brushSoftness = 0;
  });
  await dragOnCanvas(page, { x: 0.2, y: 0.3 }, { x: 0.8, y: 0.7 });
  await page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first().click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();

  await page.locator('#ge-layer-tools .ge-layer-btn[title*="effect"]').click();
  await page.locator('[data-filter-action="effect-color-overlay"]').click();
  await expect(page.locator('.ge-filter-modal')).toBeVisible();
  await page.locator('.ge-filter-row input[type="color"]').fill('#336699');
  await page.locator('.ge-filter-row input[data-key="opacity"]').fill('40');
  await page.locator('.ge-filter-modal [data-action="apply"]').click();

  const renderedPixels = await flattenedPixelDigest(page);
  const pngBytes = await downloadPng(page, 'group-effects');
  const comparison = await compareExportPixels(page, pngBytes, { width: 240, height: 180 });
  expect(comparison.meanAbsoluteError).toBe(0);
  expect(comparison.maximumError).toBe(0);
  expect(await flattenedPixelDigest(page)).toEqual(renderedPixels);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.groups[0].effects).toHaveLength(1);
  expect(reopened.groups[0].effects[0]).toMatchObject({
    type: 'color-overlay',
    params: { color: '#336699', opacity: 0.4 },
  });
});
