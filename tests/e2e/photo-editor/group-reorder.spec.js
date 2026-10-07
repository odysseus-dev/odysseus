const { test, expect } = require('@playwright/test');
const { editorState, openBlankEditor, reopenDraft, waitForDraft } = require('./helpers.js');

test('group drag reorders the complete subtree with undo and server persistence', async ({ page, request }) => {
  await openBlankEditor(page, { width: 420, height: 300 }, 'Group reorder E2E');
  await page.locator('.ge-layer-item[data-layer-id]').filter({ hasText: 'Background' }).first().click({ modifiers: ['Control'] });
  await page.locator('#ge-group-selected').click();
  let current = await editorState(page);
  const group = current.groups[0];
  const originalOrder = current.layers.map(layer => layer.id);

  await page.locator('#ge-add-layer').click();
  await page.locator('.ge-add-layer-menu [data-layer-kind="raster"]').click();
  current = await editorState(page);
  const looseId = current.activeLayerId;
  expect(current.layers.map(layer => layer.id)).toEqual([...originalOrder, looseId]);

  const handle = page.locator(`.ge-layer-group-row[data-group-id="${group.id}"] .ge-layer-group-drag`);
  const looseRow = page.locator(`.ge-layer-item[data-layer-id="${looseId}"]`);
  const handleBox = await handle.boundingBox();
  const looseBox = await looseRow.boundingBox();
  const dragReadiness = await page.evaluate(async ({ groupId, point }) => {
    const { state } = await import('/static/js/editor/state.js');
    const { groupSiblingUnits } = await import('/static/js/editor/layer-groups.js');
    const group = state.layerGroups.find(item => item.id === groupId);
    const hit = document.elementFromPoint(point.x, point.y);
    return {
      siblingIds: groupSiblingUnits(state, group.parentId || null).map(unit => unit.id),
      hitHandle: !!hit?.closest('.ge-layer-group-drag'),
    };
  }, { groupId: group.id, point: { x: handleBox.x + handleBox.width / 2, y: handleBox.y + handleBox.height / 2 } });
  expect(dragReadiness.hitHandle, JSON.stringify(dragReadiness)).toBe(true);
  expect(dragReadiness.siblingIds).toEqual([group.id, looseId]);
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(looseBox.x + looseBox.width / 2, looseBox.y + 1, { steps: 8 });
  await expect(page.locator('.ge-group-drop-line')).toBeVisible();
  await page.mouse.up();

  current = await editorState(page);
  expect(current.layers.map(layer => layer.id)).toEqual([looseId, ...originalOrder]);
  expect(current.groups[0].layerIds).toEqual(group.layerIds);

  await page.locator('#ge-undo').click();
  expect((await editorState(page)).layers.map(layer => layer.id)).toEqual([...originalOrder, looseId]);
  await page.locator('#ge-redo').click();
  expect((await editorState(page)).layers.map(layer => layer.id)).toEqual([looseId, ...originalOrder]);

  const draftId = await waitForDraft(page);
  const beforeReopen = await editorState(page);
  await reopenDraft(page, draftId);
  current = await editorState(page);
  expect(current.layers.map(layer => layer.id)).toEqual(beforeReopen.layers.map(layer => layer.id));
  expect(current.groups).toEqual(beforeReopen.groups);
  await request.delete(`/api/editor-drafts/${encodeURIComponent(draftId)}`);
});

test('mobile layer drag handle supports long-press reorder', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await openBlankEditor(page, { width: 320, height: 240 }, 'Mobile layer reorder');
    await page.locator('.ge-layers-header').click();
    await expect(page.locator('.ge-right-panel')).toHaveClass(/expanded/);
    const before = await editorState(page);
    // State is bottom-to-top; the panel is top-to-bottom.
    const dragId = before.layers[0].id;
    const targetId = before.layers[before.layers.length - 1].id;
    const bottomHandle = page.locator(`.ge-layer-item[data-layer-id="${dragId}"] .ge-layer-drag`);
    const topRow = page.locator(`.ge-layer-item[data-layer-id="${targetId}"]`);
    await bottomHandle.scrollIntoViewIfNeeded();
    const handleBox = await bottomHandle.boundingBox();
    const topBox = await topRow.boundingBox();
    expect(handleBox.width).toBeGreaterThanOrEqual(24);
    const point = { x: handleBox.x + handleBox.width / 2, y: handleBox.y + handleBox.height / 2 };
    const hitHandle = await page.evaluate(({ x, y }) => {
      const hit = document.elementFromPoint(x, y);
      return !!hit?.closest('.ge-layer-drag');
    }, point);
    expect(hitHandle).toBe(true);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: point.x, y: point.y, id: 1, radiusX: 8, radiusY: 8 }],
    });
    await page.waitForTimeout(450);
    await expect(page.locator(`.ge-layer-item[data-layer-id="${dragId}"]`)).toHaveClass(/dragging/);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: point.x, y: topBox.y + 1, id: 1, radiusX: 8, radiusY: 8 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });
    await page.waitForTimeout(120);
    expect((await editorState(page)).layers.map(layer => layer.id)).toEqual([targetId, dragId]);
  } finally {
    await context.close();
  }
});
