const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

async function compositeAlphaAtCenter(page) {
  return page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const x = Math.floor(state.imgWidth / 2);
    const y = Math.floor(state.imgHeight / 2);
    return state.documentCompositeCanvas.getContext('2d').getImageData(x, y, 1, 1).data[3];
  });
}

test('group mask paints, toggles, survives history and reopen, and protects ungroup', async ({ page, request }) => {
  await openBlankEditor(page, { width: 420, height: 300 }, 'Group mask E2E');
  await page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first().click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  const groupId = (await editorState(page)).groups[0].id;
  const groupRow = page.locator(`.ge-layer-group-row[data-group-id="${groupId}"]`);
  await page.locator('#ge-layer-tools .ge-group-mask-btn').click();

  await expect(page.locator('.ge-group-mask-sub-item')).toHaveCount(1);
  await expect(page.locator('#ge-layer-tools button[title="Delete group masks before ungrouping"]')).toBeDisabled();
  let current = await editorState(page);
  expect(current.groups[0].masks).toHaveLength(1);
  expect(current.groups[0].masks[0]).toMatchObject({ mode: 'group', size: [420, 300] });
  const whiteMaskHash = current.groups[0].masks[0].pixelHash;
  expect(await compositeAlphaAtCenter(page)).toBe(255);

  await page.locator('.ge-tool-btn[data-tool="eraser"]').click();
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 90;
    state.eraserSoftness = 0;
  });
  await dragOnCanvas(page, { x: 0.46, y: 0.5 }, { x: 0.54, y: 0.5 });
  current = await editorState(page);
  const erasedMaskHash = current.groups[0].masks[0].pixelHash;
  expect(erasedMaskHash).not.toBe(whiteMaskHash);
  expect(await compositeAlphaAtCenter(page)).toBe(0);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).groups[0].masks[0].pixelHash).toBe(whiteMaskHash);
  expect(await compositeAlphaAtCenter(page)).toBe(255);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).groups[0].masks[0].pixelHash).toBe(erasedMaskHash);
  expect(await compositeAlphaAtCenter(page)).toBe(0);

  const maskRow = page.locator('.ge-group-mask-sub-item');
  await maskRow.locator('.ge-layer-vis').click();
  expect(await compositeAlphaAtCenter(page)).toBe(255);
  await maskRow.locator('.ge-layer-vis').click();
  expect(await compositeAlphaAtCenter(page)).toBe(0);

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.groups).toEqual(beforeReopen.groups);
  expect(await compositeAlphaAtCenter(page)).toBe(0);
  await expect(page.locator('.ge-group-mask-sub-item')).toHaveCount(1);

  await page.locator('.ge-group-mask-sub-item button[title="Delete group mask"]').click();
  await expect(page.locator('.ge-group-mask-sub-item')).toHaveCount(0);
  await groupRow.click();
  await page.locator('#ge-layer-tools button[title="Ungroup layers"]').click();
  expect((await editorState(page)).groups).toHaveLength(0);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});
