const { test, expect } = require('@playwright/test');
const { dragOnCanvas, editorState, flattenedPixelDigest, openBlankEditor } = require('./helpers.js');

test('tool switching cancels incomplete crop and selection gestures without stale edits', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Gesture cancellation');
  const canvas = page.locator('.ge-main-canvas');
  const box = await canvas.boundingBox();

  await page.locator('.ge-tool-btn[data-tool="crop"]').click();
  await page.mouse.move(box.x + 40, box.y + 35);
  await page.mouse.down();
  await page.mouse.move(box.x + 190, box.y + 150, { steps: 4 });
  expect(await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { cropping: state.cropping, rect: state.cropRect };
  })).toMatchObject({ cropping: true, rect: { w: 150, h: 115 } });
  await page.locator('.ge-tool-btn[data-tool="move"]').dispatchEvent('click');
  expect(await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { tool: state.tool, cropping: state.cropping, moving: state.cropMoving, rect: state.cropRect };
  })).toEqual({ tool: 'move', cropping: false, moving: false, rect: null });
  await page.mouse.up();
  await expect(page.locator('.ge-crop-apply')).toHaveCount(0);

  await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
  await dragOnCanvas(page, { x: 0.2, y: 0.2 }, { x: 0.55, y: 0.55 });
  const beforeMove = await editorState(page);
  const bounds = beforeMove.selection.bounds;
  await page.mouse.move(
    box.x + bounds.x + bounds.width / 2,
    box.y + bounds.y + bounds.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(box.x + bounds.x + bounds.width / 2 + 35, box.y + bounds.y + bounds.height / 2 + 20);
  expect((await editorState(page)).selection.bounds).not.toEqual(bounds);
  await page.locator('.ge-tool-btn[data-tool="brush"]').dispatchEvent('click');
  const afterSwitch = await editorState(page);
  expect(afterSwitch.selection.bounds).toEqual(bounds);
  expect(afterSwitch.undo).toBe(beforeMove.undo);
  expect(await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { tool: state.tool, selectionMoving: state.selectionMoving, marqueeActive: state.marqueeActive };
  })).toEqual({ tool: 'brush', selectionMoving: false, marqueeActive: false });
  await page.mouse.up();
  expect((await editorState(page)).selection.bounds).toEqual(bounds);
});

test('Escape cancels an active crop without changing the document', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Escape crop cancellation');
  const canvas = page.locator('.ge-main-canvas');
  const box = await canvas.boundingBox();
  await page.locator('.ge-tool-btn[data-tool="crop"]').click();
  await page.mouse.move(box.x + 40, box.y + 35);
  await page.mouse.down();
  await page.mouse.move(box.x + 190, box.y + 150, { steps: 4 });
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(page.locator('.ge-crop-apply')).toHaveCount(0);
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { cropping: state.cropping, moving: state.cropMoving, rect: state.cropRect };
  })).toEqual({ cropping: false, moving: false, rect: null });
});

test('touch crop and selection gestures complete through the shared lifecycle', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await openBlankEditor(page, { width: 320, height: 240 }, 'Touch gestures');
    const cdp = await context.newCDPSession(page);
    const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
    const touch = async (type, x, y) => cdp.send('Input.dispatchTouchEvent', {
      type,
      touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1, radiusX: 6, radiusY: 6 }],
    });

    await page.locator('.ge-tool-btn[data-tool="crop"]').click();
    await touch('touchStart', canvasBox.x + 35, canvasBox.y + 30);
    await touch('touchMove', canvasBox.x + 190, canvasBox.y + 145);
    await touch('touchEnd', canvasBox.x + 190, canvasBox.y + 145);
    await expect(page.locator('.ge-crop-apply')).toBeVisible();
    expect(await page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.cropRect;
    })).toMatchObject({ w: 155, h: 115 });

    await page.locator('.ge-tool-btn[data-tool="marquee"]').click();
    await touch('touchStart', canvasBox.x + 45, canvasBox.y + 40);
    await touch('touchMove', canvasBox.x + 165, canvasBox.y + 125);
    await touch('touchEnd', canvasBox.x + 165, canvasBox.y + 125);
    expect((await editorState(page)).selection.bounds).toBeTruthy();
  } finally {
    await context.close();
  }
});

test('touch brush paints and undoes as one stroke', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await openBlankEditor(page, { width: 320, height: 240 }, 'Touch brush');
    const cdp = await context.newCDPSession(page);
    const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
    const touch = async (type, x, y) => cdp.send('Input.dispatchTouchEvent', {
      type,
      touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1, radiusX: 6, radiusY: 6 }],
    });
    await page.locator('.ge-tool-btn[data-tool="brush"]').click();
    const before = await flattenedPixelDigest(page);
    await touch('touchStart', canvasBox.x + canvasBox.width * 0.25, canvasBox.y + canvasBox.height * 0.5);
    await touch('touchMove', canvasBox.x + canvasBox.width * 0.75, canvasBox.y + canvasBox.height * 0.5);
    await touch('touchEnd', canvasBox.x + canvasBox.width * 0.75, canvasBox.y + canvasBox.height * 0.5);
    const after = await flattenedPixelDigest(page);
    expect(after).not.toEqual(before);
    await page.locator('#ge-undo').click();
    expect(await flattenedPixelDigest(page)).toEqual(before);
  } finally {
    await context.close();
  }
});

test('touch cancellation rolls back a partial brush stroke', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await openBlankEditor(page, { width: 320, height: 240 }, 'Touch cancellation');
    const cdp = await context.newCDPSession(page);
    const canvasBox = await page.locator('.ge-main-canvas').boundingBox();
    await page.locator('.ge-tool-btn[data-tool="brush"]').click();
    const before = await flattenedPixelDigest(page);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: canvasBox.x + 70, y: canvasBox.y + 110, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: canvasBox.x + 230, y: canvasBox.y + 110, id: 1 }],
    });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
    await expect.poll(async () => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.drawing;
    })).toBe(false);
    expect(await flattenedPixelDigest(page)).toEqual(before);
  } finally {
    await context.close();
  }
});

test('pen input completes a crop gesture', async ({ page, context, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP pen injection');
  await openBlankEditor(page, { width: 320, height: 240 }, 'Pen crop gesture');
  await page.locator('.ge-tool-btn[data-tool="crop"]').click();
  const box = await page.locator('.ge-main-canvas').boundingBox();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', pointerType: 'pen', button: 'left', buttons: 1,
    clickCount: 1, x: box.x + 30, y: box.y + 25,
  });
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseMoved', pointerType: 'pen', button: 'none', buttons: 1,
    x: box.x + 175, y: box.y + 130,
  });
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', pointerType: 'pen', button: 'left', buttons: 0,
    clickCount: 1, x: box.x + 175, y: box.y + 130,
  });
  await expect(page.locator('.ge-crop-apply')).toBeVisible();
  expect(await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { cropping: state.cropping, rect: state.cropRect };
  })).toEqual({ cropping: false, rect: { x: 30, y: 25, w: 145, h: 105 } });
});
