const { test, expect } = require('@playwright/test');
const { openBlankEditor } = require('./helpers');

test('transform frame exposes eight accurate handles and supports edge resize', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Transform frame');
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await expect(page.locator('.ge-transform-popup')).toBeVisible();

  const frame = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const { transformFrameGeometry } = await import('/static/js/editor/transform-frame-geometry.js');
    const { getHandleAt } = await import('/static/js/editor/tools/transform-handles.js');
    const geometry = transformFrameGeometry({
      centerX: state.transformCenter.x,
      centerY: state.transformCenter.y,
      width: state.transformPendingW,
      height: state.transformPendingH,
      rotation: state.transformPendingRot,
    }, { zoom: state.zoom });
    const canvasRect = state.mainCanvas.getBoundingClientRect();
    const right = geometry.resizeHandles.find(handle => handle.id === 'r');
    return {
      width: state.transformPendingW,
      ids: geometry.resizeHandles.map(handle => getHandleAt(handle.x, handle.y)),
      rightClient: {
        x: canvasRect.left + right.x * canvasRect.width / state.mainCanvas.width,
        y: canvasRect.top + right.y * canvasRect.height / state.mainCanvas.height,
      },
    };
  });

  expect(frame.ids).toEqual(['tl', 't', 'tr', 'r', 'br', 'b', 'bl', 'l']);
  await page.mouse.move(frame.rightClient.x, frame.rightClient.y);
  await page.mouse.down();
  await page.mouse.move(frame.rightClient.x + 36, frame.rightClient.y, { steps: 6 });
  await page.mouse.up();

  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.transformPendingW;
  })).toBeGreaterThan(frame.width);
});

test('rotated edge resize follows the frame axis and keeps its opposite edge anchored', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Rotated transform frame');
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-rot').fill('90');

  const frame = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const { transformFrameGeometry } = await import('/static/js/editor/transform-frame-geometry.js');
    const geometry = transformFrameGeometry({
      centerX: state.transformCenter.x,
      centerY: state.transformCenter.y,
      width: state.transformPendingW,
      height: state.transformPendingH,
      rotation: state.transformPendingRot,
    }, { zoom: state.zoom });
    const right = geometry.resizeHandles.find(handle => handle.id === 'r');
    const canvasRect = state.mainCanvas.getBoundingClientRect();
    return {
      width: state.transformPendingW,
      center: { ...state.transformCenter },
      rightClient: {
        x: canvasRect.left + right.x * canvasRect.width / state.mainCanvas.width,
        y: canvasRect.top + right.y * canvasRect.height / state.mainCanvas.height,
      },
    };
  });

  await page.mouse.move(frame.rightClient.x, frame.rightClient.y);
  await page.mouse.down();
  await page.mouse.move(frame.rightClient.x, frame.rightClient.y + 36, { steps: 6 });
  await page.mouse.up();

  const after = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { width: state.transformPendingW, center: { ...state.transformCenter } };
  });
  expect(after.width).toBeGreaterThan(frame.width);
  expect(after.center.x).toBeCloseTo(frame.center.x, 4);
  expect(after.center.y).toBeGreaterThan(frame.center.y);
});

test('rotated frame moves only from its visible interior and supports keyboard nudging', async ({ page }) => {
  await openBlankEditor(page, { width: 640, height: 480 }, 'Transform interaction');
  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-rot').fill('45');

  const frame = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.snapEnabled = false;
    const rect = state.mainCanvas.getBoundingClientRect();
    const toClient = point => ({
      x: rect.left + point.x * rect.width / state.mainCanvas.width,
      y: rect.top + point.y * rect.height / state.mainCanvas.height,
    });
    return {
      center: { ...state.transformCenter },
      emptyCorner: toClient({ x: 5, y: 5 }),
      centerClient: toClient(state.transformCenter),
    };
  });

  await page.mouse.move(frame.emptyCorner.x, frame.emptyCorner.y);
  await page.mouse.down();
  await page.mouse.move(frame.emptyCorner.x + 24, frame.emptyCorner.y + 18);
  await page.mouse.up();
  const afterEmptyCorner = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { ...state.transformCenter };
  });
  expect(afterEmptyCorner.x).toBeCloseTo(frame.center.x, 5);
  expect(afterEmptyCorner.y).toBeCloseTo(frame.center.y, 5);

  await page.mouse.move(frame.centerClient.x, frame.centerClient.y);
  await page.mouse.down();
  await page.mouse.move(frame.centerClient.x + 24, frame.centerClient.y + 18, { steps: 4 });
  await page.mouse.up();
  const afterDrag = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { ...state.transformCenter };
  });
  expect(afterDrag.x).toBeGreaterThan(frame.center.x);
  expect(afterDrag.y).toBeGreaterThan(frame.center.y);

  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Shift+ArrowDown');
  const afterKeys = await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { ...state.transformCenter };
  });
  expect(afterKeys.x).toBeCloseTo(afterDrag.x + 1, 5);
  expect(afterKeys.y).toBeCloseTo(afterDrag.y + 10, 5);
  const readout = {
    x: Number(await page.locator('#ge-transform-x').inputValue()),
    y: Number(await page.locator('#ge-transform-y').inputValue()),
    w: Number(await page.locator('#ge-transform-w').inputValue()),
    h: Number(await page.locator('#ge-transform-h').inputValue()),
    angle: Number(await page.locator('#ge-transform-rot').inputValue()),
  };
  expect(readout.x).toBeCloseTo(afterKeys.x, 2);
  expect(readout.y).toBeCloseTo(afterKeys.y, 2);
  expect(readout.w).toBeGreaterThan(0);
  expect(readout.h).toBeGreaterThan(0);
  expect(readout.angle).toBe(45);

  await page.locator('#ge-transform-x').fill('250');
  await page.locator('#ge-transform-y').fill('180');
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return { ...state.transformCenter };
  })).toEqual({ x: 250, y: 180 });

  await page.keyboard.press('Escape');
  await expect(page.locator('.ge-transform-popup')).toBeHidden();
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.transformActive;
  })).toBe(false);
});

test('transform previews remain source-derived and reject unsafe allocations', async ({ page }) => {
  await openBlankEditor(page, { width: 240, height: 160 }, 'Transform source integrity');
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    for (let y = 0; y < layer.canvas.height; y += 8) {
      for (let x = 0; x < layer.canvas.width; x += 8) {
        layer.ctx.fillStyle = ((x / 8 + y / 8) % 2) ? '#f24f5f' : '#27c2a3';
        layer.ctx.fillRect(x, y, 8, 8);
      }
    }
  });
  const layerSnapshot = () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const layer = state.layers.find(item => item.id === state.activeLayerId);
    const pixels = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    let hash = 2166136261;
    for (const value of pixels) hash = Math.imul(hash ^ value, 16777619);
    return { width: layer.canvas.width, height: layer.canvas.height, hash: hash >>> 0 };
  });

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  const originalWidth = Number(await page.locator('#ge-transform-w').inputValue());
  await page.locator('#ge-transform-w').fill(String(Math.round(originalWidth * 1.8)));
  await page.locator('#ge-transform-w').fill(String(Math.round(originalWidth * 1.35)));
  const afterSequentialPreviews = await layerSnapshot();
  await page.locator('#ge-transform-cancel-btn').click();

  await page.locator('.ge-tool-btn[data-tool="transform"]').click();
  await page.locator('#ge-transform-w').fill(String(Math.round(originalWidth * 1.35)));
  const afterDirectPreview = await layerSnapshot();
  expect(afterSequentialPreviews).toEqual(afterDirectPreview);

  const safeWidth = await page.locator('#ge-transform-w').inputValue();
  const beforeRejected = await layerSnapshot();
  await page.locator('#ge-transform-w').fill('40000');
  await expect(page.locator('#toast')).toContainText('dimension limit');
  await expect(page.locator('#ge-transform-w')).toHaveValue(safeWidth);
  expect(await layerSnapshot()).toEqual(beforeRejected);
  await page.locator('#ge-transform-cancel-btn').click();
});

test('mobile touch input can grab every transform handle', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'Uses Chromium CDP touch injection');
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await openBlankEditor(page, { width: 640, height: 480 }, 'Mobile transform handles');
    await page.locator('.ge-tool-btn[data-tool="transform"]').click();
    await expect(page.locator('.ge-transform-popup')).toBeVisible();
    const cdp = await context.newCDPSession(page);
    const ids = ['tl', 't', 'tr', 'r', 'br', 'b', 'bl', 'l', 'rot'];

    const handlePoint = id => page.evaluate(async handleId => {
      const { state } = await import('/static/js/editor/state.js');
      const { transformFrameGeometry } = await import('/static/js/editor/transform-frame-geometry.js');
      const geometry = transformFrameGeometry({
        centerX: state.transformCenter.x,
        centerY: state.transformCenter.y,
        width: state.transformPendingW,
        height: state.transformPendingH,
        rotation: state.transformPendingRot,
      }, { zoom: state.zoom, rotationInside: handleId === 'rot' });
      const handle = geometry.handles.find(item => item.id === handleId);
      const rect = state.mainCanvas.getBoundingClientRect();
      return {
        x: rect.left + handle.x * rect.width / state.mainCanvas.width,
        y: rect.top + handle.y * rect.height / state.mainCanvas.height,
      };
    }, id);

    for (let index = 0; index < ids.length; index += 1) {
      const expectedId = ids[index];
      const point = await handlePoint(expectedId);
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x: point.x, y: point.y, id: index + 1, radiusX: 4, radiusY: 4 }],
      });
      await expect.poll(async () => page.evaluate(async () => {
        const { state } = await import('/static/js/editor/state.js');
        return state.transformHandle;
      })).toBe(expectedId);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await expect.poll(async () => page.evaluate(async () => {
        const { state } = await import('/static/js/editor/state.js');
        return state.transformHandle;
      })).toBe(null);
    }

    const right = await handlePoint('r');
    const widthBefore = await page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.transformPendingW;
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: right.x, y: right.y, id: 20, radiusX: 4, radiusY: 4 }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: right.x + 80, y: right.y, id: 20, radiusX: 4, radiusY: 4 }],
    });
    await expect.poll(async () => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.transformPendingW;
    })).toBeGreaterThan(widthBefore);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

    const center = await page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      const rect = state.mainCanvas.getBoundingClientRect();
      return {
        x: rect.left + state.transformCenter.x * rect.width / state.mainCanvas.width,
        y: rect.top + state.transformCenter.y * rect.height / state.mainCanvas.height,
        zoom: state.zoom,
      };
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: center.x, y: center.y, id: 30, radiusX: 4, radiusY: 4 }],
    });
    await expect.poll(async () => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.transformHandle;
    })).toBe('move');
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [
        { x: center.x - 20, y: center.y, id: 30, radiusX: 4, radiusY: 4 },
        { x: center.x + 20, y: center.y, id: 31, radiusX: 4, radiusY: 4 },
      ],
    });
    await expect.poll(async () => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.transformHandle;
    })).toBe(null);
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [
        { x: center.x - 45, y: center.y, id: 30, radiusX: 4, radiusY: 4 },
        { x: center.x + 45, y: center.y, id: 31, radiusX: 4, radiusY: 4 },
      ],
    });
    await expect.poll(async () => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return state.zoom;
    })).toBeGreaterThan(center.zoom);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } finally {
    await context.close();
  }
});
