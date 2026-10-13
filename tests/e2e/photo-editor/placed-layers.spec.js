const { test, expect } = require('@playwright/test');
const { editorState, flattenedPixelDigest, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

async function patternedPng(page, width, height, colors) {
  const base64 = await page.evaluate(({ width: w, height: h, colors: palette }) => {
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const context = canvas.getContext('2d');
    for (let y = 0; y < h; y += 1) for (let x = 0; x < w; x += 1) {
      context.fillStyle = palette[(x + y * w) % palette.length];
      context.fillRect(x, y, 1, 1);
    }
    return canvas.toDataURL('image/png').split(',')[1];
  }, { width, height, colors });
  return Buffer.from(base64, 'base64');
}

test('placed image transforms from source, replaces in place, rasterizes, and reopens', async ({ page, request }) => {
  await openBlankEditor(page, { width: 300, height: 200 }, 'Placed layers E2E');
  const firstImage = await patternedPng(page, 40, 20, ['#f44336', '#4caf50', '#2196f3', '#ffeb3b']);
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-import-topbar').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: 'first-pattern.png', mimeType: 'image/png', buffer: firstImage });

  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);
  const imported = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  expect(imported.placed.sourceSize).toEqual([40, 20]);
  const sourceHash = imported.placed.sourcePixelHash;

  const row = page.locator(`.ge-layer-item[data-layer-id="${imported.id}"]`);
  await page.locator('#ge-layer-tools .ge-true-mask-btn').click();
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  const originalWidth = Number(await page.locator('#ge-transform-w').inputValue());
  await page.locator('#ge-transform-w').fill(String(Math.round(originalWidth / 2)));
  await page.locator('#ge-transform-apply').click();
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-w').fill(String(originalWidth));
  await page.locator('#ge-transform-apply').click();

  const restoredSize = (await editorState(page)).layers.find(layer => layer.id === imported.id);
  expect(restoredSize.placed.sourcePixelHash).toBe(sourceHash);
  expect(restoredSize.size[0]).toBe(originalWidth);
  expect(restoredSize.masks).toHaveLength(1);
  const beforeReplaceFrame = { size: restoredSize.size, offset: restoredSize.offset };
  const beforeReplaceMask = restoredSize.masks[0];

  const replacement = await patternedPng(page, 20, 40, ['#111111', '#f8f8f8', '#ff00aa']);
  const replaceChooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-layer-tools button[title="Replace placed image"]').click();
  const replaceChooser = await replaceChooserPromise;
  await replaceChooser.setFiles({ name: 'replacement.png', mimeType: 'image/png', buffer: replacement });
  await expect.poll(async () => {
    const layer = (await editorState(page)).layers.find(item => item.id === imported.id);
    return layer.placed?.sourceName;
  }).toBe('replacement.png');
  const replaced = (await editorState(page)).layers.find(layer => layer.id === imported.id);
  expect(replaced.size).toEqual(beforeReplaceFrame.size);
  expect(replaced.offset).toEqual(beforeReplaceFrame.offset);
  expect(replaced.placed.sourcePixelHash).not.toBe(sourceHash);
  expect(replaced.masks[0]).toEqual(beforeReplaceMask);

  const beforeRasterize = await flattenedPixelDigest(page);
  await page.locator('#ge-layer-tools button[title="Rasterize placed layer"]').click();
  const rasterized = (await editorState(page)).layers.find(layer => layer.id === imported.id);
  expect(rasterized.kind).toBe('raster');
  expect(rasterized.placed).toBeNull();
  expect(await flattenedPixelDigest(page)).toEqual(beforeRasterize);

  await page.locator('#ge-undo').click();
  await expect.poll(async () => {
    const layer = (await editorState(page)).layers.find(item => item.id === imported.id);
    return layer.kind;
  }).toBe('placed');
  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = (await editorState(page)).layers.find(layer => layer.id === imported.id);
  expect(reopened.kind).toBe('placed');
  expect(reopened.placed.sourceName).toBe('replacement.png');
  expect(reopened.masks[0]).toEqual(beforeReplaceMask);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('raster layers can be converted to editable sources without changing pixels', async ({ page, request }) => {
  await openBlankEditor(page, { width: 180, height: 120 }, 'Editable source E2E');
  const image = await patternedPng(page, 36, 24, ['#ef5350', '#42a5f5', '#66bb6a']);
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-import-topbar').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: 'editable-source.png', mimeType: 'image/png', buffer: image });

  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);
  const placed = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  await page.locator('#ge-layer-tools button[title="Rasterize placed layer"]').click();
  const raster = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  const before = { pixelHash: raster.pixelHash, size: raster.size, offset: raster.offset };

  await page.locator('#ge-layer-tools button[title="Convert to editable source"]').click();
  await expect.poll(async () => (await editorState(page)).layers.find(layer => layer.id === placed.id).kind).toBe('placed');
  const converted = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  expect(converted.placed.sourceSize).toEqual(before.size);
  expect(converted.placed.sourcePixelHash).toBe(before.pixelHash);
  expect(converted.size).toEqual(before.size);
  expect(converted.offset).toEqual(before.offset);

  const draftId = await waitForDraft(page);
  await reopenDraft(page, draftId);
  const reopened = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  expect(reopened.kind).toBe('placed');
  expect(reopened.placed.sourcePixelHash).toBe(before.pixelHash);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('external clipboard images use the editable source import path', async ({ page, request }) => {
  await openBlankEditor(page, { width: 180, height: 120 }, 'Clipboard source E2E');
  const image = await patternedPng(page, 28, 18, ['#ef5350', '#42a5f5', '#66bb6a']);
  await page.evaluate(base64 => {
    const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
    const file = new File([bytes], 'clipboard.png', { type: 'image/png' });
    const data = new DataTransfer();
    data.items.add(file);
    window.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
  }, image.toString('base64'));
  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);
  const pasted = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  expect(pasted.placed.sourceName).toBe('Pasted image');
  expect(pasted.placed.sourceSize).toEqual([28, 18]);
  const draftId = await waitForDraft(page);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('duplicating a placed layer copies its source independently', async ({ page, request }) => {
  await openBlankEditor(page, { width: 180, height: 120 }, 'Duplicate source E2E');
  const image = await patternedPng(page, 28, 18, ['#ef5350', '#42a5f5', '#66bb6a']);
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-import-topbar').click();
  await (await chooserPromise).setFiles({ name: 'duplicate-source.png', mimeType: 'image/png', buffer: image });
  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);
  const original = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  await page.locator(`.ge-layer-item[data-layer-id="${original.id}"]`).click();
  await page.locator('#ge-layer-tools button[title="Duplicate layer"]').click();
  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(2);
  const sourceOwnership = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const placed = state.layers.filter(layer => layer.kind === 'placed');
    return {
      count: placed.length,
      sameSource: placed[0].placed.sourceCanvas === placed[1].placed.sourceCanvas,
      sizes: placed.map(layer => [layer.placed.sourceCanvas.width, layer.placed.sourceCanvas.height]),
    };
  });
  expect(sourceOwnership).toEqual({ count: 2, sameSource: false, sizes: [[28, 18], [28, 18]] });
  const draftId = await waitForDraft(page);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('raster transforms preserve source pixels by default and can opt out', async ({ page, request }) => {
  await openBlankEditor(page, { width: 180, height: 120 }, 'Non-destructive transform E2E');
  const image = await patternedPng(page, 42, 28, ['#ff7043', '#26a69a', '#5c6bc0']);
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-import-topbar').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: 'transform-source.png', mimeType: 'image/png', buffer: image });
  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);

  const placed = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  await page.locator('#ge-layer-tools button[title="Rasterize placed layer"]').click();
  const rasterBeforeTransform = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  const sourceHash = rasterBeforeTransform.pixelHash;
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('#ge-transform-preserve-source')).toBeChecked();
  await page.locator('#ge-transform-w').fill('84');
  await page.locator('#ge-transform-apply').click();
  const preserved = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  expect(preserved.kind).toBe('placed');
  expect(preserved.placed.sourceSize).toEqual([42, 28]);
  expect(preserved.placed.sourcePixelHash).toBe(sourceHash);

  const draftId = await waitForDraft(page);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('raster transforms can explicitly replace source pixels', async ({ page, request }) => {
  await openBlankEditor(page, { width: 180, height: 120 }, 'Destructive transform E2E');
  const image = await patternedPng(page, 42, 28, ['#ff7043', '#26a69a', '#5c6bc0']);
  const chooserPromise = page.waitForEvent('filechooser');
  await page.locator('#ge-import-topbar').click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: 'destructive-transform.png', mimeType: 'image/png', buffer: image });
  await expect.poll(async () => (await editorState(page)).layers.filter(layer => layer.kind === 'placed').length).toBe(1);

  const placed = (await editorState(page)).layers.find(layer => layer.kind === 'placed');
  await page.locator('#ge-layer-tools button[title="Rasterize placed layer"]').click();
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('#ge-transform-preserve-source')).toBeChecked();
  await page.locator('#ge-transform-preserve-source').uncheck();
  await page.locator('#ge-transform-w').fill('60');
  await page.locator('#ge-transform-apply').click();
  const destructive = (await editorState(page)).layers.find(layer => layer.id === placed.id);
  expect(destructive.kind).toBe('raster');
  expect(destructive.placed).toBeNull();

  const draftId = await waitForDraft(page);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
