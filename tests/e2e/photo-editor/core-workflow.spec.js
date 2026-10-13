const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
const {
  compareExportPixels,
  dragOnCanvas,
  encodedImagePixelDigest,
  editorState,
  flattenedPixelDigest,
  openBlankEditor,
  openExportDialog,
  reopenDraft,
  waitForDraft,
} = require('./helpers.js');

test('layered document survives edit, mask, transform, crop, reopen, and export', async ({ page, request }) => {
  await openBlankEditor(page);
  const initial = await editorState(page);

  await page.locator('.ge-tool-btn[data-tool="brush"]').click();
  await dragOnCanvas(page, { x: 0.20, y: 0.30 }, { x: 0.72, y: 0.60 });
  const painted = await editorState(page);
  const paintedLayer = painted.layers.find(layer => layer.name === 'Edit');
  const initialLayer = initial.layers.find(layer => layer.name === 'Edit');
  expect(paintedLayer.pixelHash).not.toBe(initialLayer.pixelHash);

  const editItem = page.locator('.ge-layer-item').filter({ hasText: 'Edit' }).first();
  await editItem.click();
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  let current = await editorState(page);
  expect(current.layers.find(layer => layer.name === 'Edit').masks).toHaveLength(1);

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('#ge-transform-w')).toBeVisible();
  await page.locator('#ge-transform-w').fill('560');
  await page.locator('#ge-transform-apply').click();
  current = await editorState(page);
  const transformed = current.layers.find(layer => layer.name === 'Edit');
  expect(transformed.size[0]).toBe(560);
  expect(transformed.masks[0].size).toEqual(transformed.size);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').size[0]).toBe(640);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers.find(layer => layer.name === 'Edit').size[0]).toBe(560);

  await page.locator('.ge-tool-btn[data-tool="text"]').click();
  await expect(page.locator('#ge-text-section')).toBeVisible();
  const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
  await page.mouse.click(canvasBox.x + canvasBox.width * 0.28, canvasBox.y + canvasBox.height * 0.22);
  await page.locator('#ge-text-content').fill('Durable photo edit');
  await page.locator('#ge-text-size').fill('42');
  await page.locator('#ge-text-size').press('Enter');
  await expect(page.locator('.ge-layer-item').filter({ hasText: 'Durable photo edit' })).toBeVisible();

  await page.locator('.ge-tool-btn[data-tool="crop"]').click();
  await dragOnCanvas(page, { x: 0.08, y: 0.10 }, { x: 0.92, y: 0.88 });
  await expect(page.locator('.ge-crop-apply-btn')).toBeVisible();
  await page.locator('.ge-crop-apply-btn').click();
  const cropped = await editorState(page);
  expect(cropped.dimensions[0]).toBeLessThan(640);
  expect(cropped.dimensions[1]).toBeLessThan(480);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).dimensions).toEqual([640, 480]);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).dimensions).toEqual(cropped.dimensions);

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  const beforeReopenPixels = await flattenedPixelDigest(page);
  await reopenDraft(page, draftId);
  const reopened = await editorState(page);
  expect(reopened.dimensions).toEqual(beforeReopen.dimensions);
  expect(reopened.activeLayerId).toBe(beforeReopen.activeLayerId);
  expect(reopened.layers).toEqual(beforeReopen.layers);
  expect(await flattenedPixelDigest(page)).toEqual(beforeReopenPixels);

  await openExportDialog(page);
  await page.locator('[data-format="png"]').click();
  await page.locator('#ge-export-width').fill('320');
  const expectedHeight = Number(await page.locator('#ge-export-height').inputValue());
  await page.locator('#ge-export-filename').fill('photo-editor-release-gate');
  const downloadPromise = page.waitForEvent('download');
  await page.locator('.ge-export-dialog button[type="submit"]').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('photo-editor-release-gate.png');
  const bytes = fs.readFileSync(await download.path());
  expect(bytes.subarray(1, 4).toString('ascii')).toBe('PNG');
  expect(bytes.readUInt32BE(16)).toBe(320);
  expect(bytes.readUInt32BE(20)).toBe(expectedHeight);
  const resizedComparison = await compareExportPixels(page, bytes, { width: 320, height: expectedHeight });
  expect(resizedComparison.meanAbsoluteError).toBeLessThanOrEqual(1);
  expect(resizedComparison.maximumError).toBeLessThanOrEqual(32);
  expect(resizedComparison.changedPixelRatio).toBeLessThanOrEqual(0.08);

  await openExportDialog(page);
  await page.locator('[data-format="png"]').click();
  await page.locator('#ge-export-filename').fill('photo-editor-native-fidelity');
  const nativeDownloadPromise = page.waitForEvent('download');
  await page.locator('.ge-export-dialog button[type="submit"]').click();
  const nativeDownload = await nativeDownloadPromise;
  const nativeBytes = fs.readFileSync(await nativeDownload.path());
  expect(await encodedImagePixelDigest(page, nativeBytes)).toEqual(beforeReopenPixels);

  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
