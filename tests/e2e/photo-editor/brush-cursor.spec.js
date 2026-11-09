const { test, expect } = require('@playwright/test');
const { selectTool } = require('./helpers.js');
const { openBlankEditor } = require('./helpers.js');

test('brush cursor matches the rendered brush diameter and replaces the crosshair', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  await selectTool(page, 'brush');

  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 80;
  });

  const canvas = page.locator('.ge-main-canvas');
  const canvasBox = await canvas.boundingBox();
  await page.mouse.move(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);

  const cursor = page.locator('.ge-brush-cursor');
  await expect(cursor).toBeVisible();
  await expect(canvas).toHaveCSS('cursor', 'none');

  const measurements = await page.evaluate(() => {
    const canvasEl = document.querySelector('.ge-main-canvas');
    const cursorEl = document.querySelector('.ge-brush-cursor');
    const canvasRect = canvasEl.getBoundingClientRect();
    const cursorRect = cursorEl.getBoundingClientRect();
    return {
      expectedWidth: 80 * canvasRect.width / canvasEl.width,
      expectedHeight: 80 * canvasRect.height / canvasEl.height,
      cursorWidth: cursorRect.width,
      cursorHeight: cursorRect.height,
      cursorZ: Number(getComputedStyle(cursorEl).zIndex),
      galleryZ: Number(getComputedStyle(document.getElementById('gallery-modal')).zIndex || 0),
    };
  });

  expect(measurements.cursorWidth).toBeCloseTo(measurements.expectedWidth, 1);
  expect(measurements.cursorHeight).toBeCloseTo(measurements.expectedHeight, 1);
  expect(measurements.cursorZ).toBeGreaterThan(measurements.galleryZ);

  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.brushSize = 160;
  });
  await page.mouse.move(canvasBox.x + canvasBox.width / 2 + 1, canvasBox.y + canvasBox.height / 2);
  await expect.poll(async () => (await cursor.boundingBox()).width)
    .toBeCloseTo(measurements.expectedWidth * 2, 1);
});


test('brush cursor follows the pointer before, during, and after a stroke and cancels animations on close', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  await selectTool(page, 'brush');
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  const cursor = page.locator('.ge-brush-cursor');
  const checkCenter = async (x, y) => {
    await expect.poll(async () => {
      const box = await cursor.boundingBox();
      return box ? Math.max(Math.abs(box.x + box.width / 2 - x), Math.abs(box.y + box.height / 2 - y)) : Infinity;
    }).toBeLessThan(1);
  };
  const a = { x: canvas.x + canvas.width * 0.3, y: canvas.y + canvas.height * 0.4 };
  const b = { x: canvas.x + canvas.width * 0.7, y: canvas.y + canvas.height * 0.6 };
  await page.mouse.move(a.x, a.y);
  await checkCenter(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(b.x, b.y, { steps: 8 });
  await checkCenter(b.x, b.y);
  await page.mouse.up();
  await page.mouse.move(a.x, a.y);
  await checkCenter(a.x, a.y);

  // 1. Ensure at least one animation exists before checking cancellation
  // 2. Verify the actual cursor-position animation is paused while active
  const activeAnimation = await page.evaluate(() => {
    const cursorEl = document.querySelector('.ge-brush-cursor');
    const anims = cursorEl ? cursorEl.getAnimations() : [];
    return {
      count: anims.length,
      hasPositionAnim: Boolean(cursorEl?._positionAnimation),
      positionAnimPaused: cursorEl?._positionAnimation?.playState === 'paused',
      positionAnimCurrentTime: cursorEl?._positionAnimation?.currentTime,
      allPaused: anims.length > 0 && anims.every(a => a.playState === 'paused'),
    };
  });
  expect(activeAnimation.count).toBeGreaterThanOrEqual(1);
  expect(activeAnimation.hasPositionAnim).toBe(true);
  expect(activeAnimation.positionAnimPaused).toBe(true);
  expect(activeAnimation.positionAnimCurrentTime).toBe(0);
  expect(activeAnimation.allPaused).toBe(true);

  // 3. Verify it is canceled when the editor closes
  // 4. Ensure temporary test-only references are cleaned up
  const closeOutcome = await page.evaluate(async () => {
    const cursorEl = document.querySelector('.ge-brush-cursor');
    const anims = cursorEl ? cursorEl.getAnimations() : [];
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    editor.closeEditor({ force: true });
    return {
      trackedCount: anims.length,
      allIdle: anims.length > 0 && anims.every(a => a.playState === 'idle'),
      states: anims.map(a => a.playState),
    };
  });
  await expect(cursor).toHaveCount(0);
  expect(closeOutcome.trackedCount).toBeGreaterThanOrEqual(1);
  expect(closeOutcome.allIdle).toBe(true);
  expect(closeOutcome.states.every(s => s === 'idle')).toBe(true);

  // Verify no temporary references linger on window
  const windowClean = await page.evaluate(() => {
    return !('__cursorAnimationsForTest' in window) && !('__styleTransformMutations' in window);
  });
  expect(windowClean).toBe(true);
});


test('steady pointer movement does not perform repeated inline style-transform writes', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  await selectTool(page, 'brush');

  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  const cursor = page.locator('.ge-brush-cursor');

  // Warm up the cursor first: move pointer onto canvas
  const startX = canvas.x + canvas.width * 0.25;
  const startY = canvas.y + canvas.height * 0.25;
  await page.mouse.move(startX, startY);
  await expect(cursor).toBeVisible();

  // Observe style attribute mutations during steady pointer movement.
  // Brush size, color, and mode remain unchanged throughout.
  await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    window.__styleMutationRecords = [];
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'attributes' && m.attributeName === 'style') {
          const oldStyle = m.oldValue || '';
          const currentStyle = el.getAttribute('style') || '';
          // Track mutations where style-transform was written inline
          if (el.style.transform || currentStyle.includes('transform:') || oldStyle.includes('transform:')) {
            window.__styleMutationRecords.push({
              oldStyle,
              currentStyle,
              inlineTransform: el.style.transform,
            });
          }
        }
      }
    });
    observer.observe(el, { attributes: true, attributeFilter: ['style'], attributeOldValue: true });
    window.__styleMutationObserver = observer;
  });

  // Preserve actual mouse interactions: steady movement across 10 steps
  const endX = canvas.x + canvas.width * 0.75;
  const endY = canvas.y + canvas.height * 0.75;
  await page.mouse.move(endX, endY, { steps: 10 });

  // Confirm the cursor accurately tracked the pointer
  await expect.poll(async () => {
    const box = await cursor.boundingBox();
    return box ? Math.max(Math.abs(box.x + box.width / 2 - endX), Math.abs(box.y + box.height / 2 - endY)) : Infinity;
  }).toBeLessThan(1);

  // Collect observation results and clean up observer and globals
  const { mutationCount, inlineTransform, animCount } = await page.evaluate(() => {
    window.__styleMutationObserver?.disconnect();
    delete window.__styleMutationObserver;
    const records = window.__styleMutationRecords || [];
    delete window.__styleMutationRecords;
    const el = document.querySelector('.ge-brush-cursor');
    return {
      mutationCount: records.length,
      inlineTransform: el?.style.transform || '',
      animCount: el?.getAnimations?.().length || 0,
    };
  });

  // The optimized implementation uses paused Web Animations keyframes, avoiding repeated
  // inline style-transform writes that trigger expensive :has() document invalidations.
  expect(mutationCount).toBe(0);
  expect(inlineTransform).toBe('');
  expect(animCount).toBeGreaterThanOrEqual(1);
});


test('cross-tool cursor positioning, sizing, and styling across brush, eraser, pixel, lasso, inpaint, liquify, and clone', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  const cursor = page.locator('.ge-brush-cursor');

  const checkCenter = async (x, y) => {
    await expect.poll(async () => {
      const box = await cursor.boundingBox();
      return box ? Math.max(Math.abs(box.x + box.width / 2 - x), Math.abs(box.y + box.height / 2 - y)) : Infinity;
    }).toBeLessThan(1);
  };

  const midX = canvas.x + canvas.width / 2;
  const midY = canvas.y + canvas.height / 2;

  // 1. Brush tool
  await selectTool(page, 'brush');
  await page.mouse.move(midX, midY);
  await expect(cursor).toBeVisible();
  await checkCenter(midX, midY);
  let props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      isPixel: el.classList.contains('ge-brush-cursor--pixel'),
      display: el.style.display,
      borderStyle: el.style.borderStyle,
    };
  });
  expect(props.isPixel).toBe(false);
  expect(props.display).toBe('block');

  // 2. Eraser tool
  await selectTool(page, 'eraser');
  await page.mouse.move(midX + 10, midY + 10);
  await checkCenter(midX + 10, midY + 10);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      borderColor: el.style.borderColor,
      isPixel: el.classList.contains('ge-brush-cursor--pixel'),
    };
  });
  expect(props.borderColor).toBe('rgba(255, 255, 255, 0.6)');
  expect(props.isPixel).toBe(false);

  // 3. Pixel brush (via tool / preset)
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.tool = 'pixel';
    state.brushPixel = true;
    state.brushSize = 16;
  });
  await page.mouse.move(midX - 10, midY - 10);
  await checkCenter(midX - 10, midY - 10);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      isPixel: el.classList.contains('ge-brush-cursor--pixel'),
      width: parseFloat(el.style.width),
    };
  });
  expect(props.isPixel).toBe(true);

  // 4. Lasso tool
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.tool = 'lasso';
    state.brushPixel = false;
  });
  await page.mouse.move(midX + 20, midY - 10);
  await checkCenter(midX + 20, midY - 10);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      borderColor: el.style.borderColor,
      background: el.style.background,
    };
  });
  expect(props.borderColor).toBe('rgba(255, 255, 255, 0.85)');
  expect(props.background).toBe('rgba(0, 0, 0, 0.15)');

  // 5. Inpaint tool (paint mode and erase mode)
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.tool = 'inpaint';
    state.inpaintEraseMode = false;
  });
  await page.mouse.move(midX, midY);
  await checkCenter(midX, midY);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      borderColor: el.style.borderColor,
      background: el.style.background,
      borderStyle: el.style.borderStyle,
    };
  });
  expect(props.borderColor).toBe('rgba(255, 80, 80, 0.8)');
  expect(props.borderStyle).toBe('solid');

  // Inpaint erase mode
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.inpaintEraseMode = true;
  });
  await page.mouse.move(midX + 5, midY + 5);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      borderColor: el.style.borderColor,
      borderStyle: el.style.borderStyle,
    };
  });
  expect(props.borderColor).toBe('rgba(255, 255, 255, 0.9)');
  expect(props.borderStyle).toBe('dashed');

  // 6. Liquify tool
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.tool = 'liquify';
    state.liquifySize = 100;
  });
  await page.mouse.move(midX - 15, midY + 15);
  await checkCenter(midX - 15, midY + 15);
  props = await page.evaluate(() => {
    const el = document.querySelector('.ge-brush-cursor');
    return {
      width: parseFloat(el.style.width),
      height: parseFloat(el.style.height),
      display: el.style.display,
    };
  });
  expect(props.display).toBe('block');
  expect(props.width).toBeGreaterThan(0);

  // 7. Clone tool
  await page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    state.tool = 'clone';
    state.brushSize = 50;
  });
  await page.mouse.move(midX, midY);
  await checkCenter(midX, midY);
  await expect(cursor).toBeVisible();

  // Clean close
  await page.evaluate(async () => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    editor.closeEditor({ force: true });
  });
  await expect(cursor).toHaveCount(0);
});


test('canvas boundaries, pointer exit and re-entry, and viewport resize', async ({ page }) => {
  await openBlankEditor(page, { width: 400, height: 300 });
  await selectTool(page, 'brush');

  const canvas = await page.locator('.ge-main-canvas').boundingBox();
  const cursor = page.locator('.ge-brush-cursor');

  const checkCenter = async (x, y) => {
    await expect.poll(async () => {
      const box = await cursor.boundingBox();
      return box ? Math.max(Math.abs(box.x + box.width / 2 - x), Math.abs(box.y + box.height / 2 - y)) : Infinity;
    }).toBeLessThan(1);
  };

  // 1. Enter canvas
  const insideX = canvas.x + canvas.width * 0.5;
  const insideY = canvas.y + canvas.height * 0.5;
  await page.mouse.move(insideX, insideY);
  await expect(cursor).toBeVisible();
  await checkCenter(insideX, insideY);

  // 2. Exit canvas boundary: cursor should hide
  const outsideX = canvas.x - 50;
  const outsideY = canvas.y - 50;
  await page.mouse.move(outsideX, outsideY);
  await expect(cursor).toBeHidden();

  // 3. Re-enter canvas: cursor should restore visibility and position
  await page.mouse.move(insideX, insideY);
  await expect(cursor).toBeVisible();
  await checkCenter(insideX, insideY);

  // 4. Viewport resize
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(100);
  const newCanvas = await page.locator('.ge-main-canvas').boundingBox();
  const newInsideX = newCanvas.x + newCanvas.width * 0.5;
  const newInsideY = newCanvas.y + newCanvas.height * 0.5;
  await page.mouse.move(newInsideX, newInsideY);
  await expect(cursor).toBeVisible();
  await checkCenter(newInsideX, newInsideY);

  // Clean close
  await page.evaluate(async () => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    editor.closeEditor({ force: true });
  });
  await expect(cursor).toHaveCount(0);
});


for (const view of ['2x zoom', 'fit-to-view']) {
  test(`zoom and fit-to-view cursor scaling and positioning: ${view}`, async ({ page }) => {
    const size = view === '2x zoom' ? { width: 400, height: 300 } : { width: 1600, height: 1200 };
    await openBlankEditor(page, size);
    await selectTool(page, 'brush');

    await page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      state.brushSize = 50;
      // The zoom-in control applies 1.25x through the real zoom handler.
      state.zoom = 1.6;
      state.zoomViewMode = 'scale';
    });
    await page.locator('#ge-zoom-in').click();
    await expect.poll(() => page.evaluate(async () => {
      const { state } = await import('/static/js/editor/state.js');
      return { zoom: state.zoom, mode: state.zoomViewMode };
    })).toEqual({ zoom: 2, mode: 'scale' });

    if (view === 'fit-to-view') {
      await page.locator('#ge-zoom-100').click();
      const fitted = await page.evaluate(async () => {
        const { state } = await import('/static/js/editor/state.js');
        const area = document.querySelector('.ge-canvas-area');
        const pad = state._footerDocked ? 0 : 20;
        const rulerPad = state.rulersVisible ? 18 : 0;
        return {
          zoom: state.zoom,
          mode: state.zoomViewMode,
          expectedZoom: Math.min(1,
            (area.clientWidth - 2 * pad - rulerPad) / state.imgWidth,
            (area.clientHeight - 2 * pad - rulerPad) / state.imgHeight),
        };
      });
      expect(fitted.mode).toBe('fit');
      expect(fitted.zoom).toBeGreaterThan(0);
      expect(fitted.zoom).toBeLessThan(1);
      expect(fitted.zoom).toBeCloseTo(fitted.expectedZoom, 6);
    }

    const canvas = page.locator('.ge-main-canvas');
    const canvasBox = await canvas.boundingBox();
    const cursor = page.locator('.ge-brush-cursor');
    if (view === '2x zoom') {
      expect(canvasBox.width).toBeCloseTo(size.width * 2, 1);
      expect(canvasBox.height).toBeCloseTo(size.height * 2, 1);
    }
    const targetX = canvasBox.x + canvasBox.width * 0.4;
    const targetY = canvasBox.y + canvasBox.height * 0.4;
    await page.mouse.move(targetX, targetY);
    await expect(cursor).toBeVisible();

    // Pointer coordinates can be rounded to device pixels; keep the existing
    // subpixel alignment tolerance separate from the stricter diameter check.
    await expect.poll(async () => {
      const box = await cursor.boundingBox();
      return box ? Math.max(
        Math.abs(box.x + box.width / 2 - targetX),
        Math.abs(box.y + box.height / 2 - targetY),
      ) : Infinity;
    }).toBeLessThan(1);
    await expect.poll(() => page.evaluate(() => {
      const canvasEl = document.querySelector('.ge-main-canvas');
      const canvasRect = canvasEl.getBoundingClientRect();
      const cursorRect = document.querySelector('.ge-brush-cursor').getBoundingClientRect();
      return Math.max(
        Math.abs(cursorRect.width - 50 * canvasRect.width / canvasEl.width),
        Math.abs(cursorRect.height - 50 * canvasRect.height / canvasEl.height),
      );
    })).toBeLessThan(0.1);

    await page.evaluate(async () => {
      const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
      editor.closeEditor({ force: true });
    });
    await expect(cursor).toHaveCount(0);
  });
}


test('memory and lifecycle: repeated editor open/close cycles retain no detached cursors or active animations', async ({ page }) => {
  for (let cycle = 0; cycle < 5; cycle++) {
    await openBlankEditor(page, { width: 400, height: 300 });
    await selectTool(page, 'brush');

    const canvas = await page.locator('.ge-main-canvas').boundingBox();
    await page.mouse.move(canvas.x + canvas.width * 0.5, canvas.y + canvas.height * 0.5);
    const cursor = page.locator('.ge-brush-cursor');
    await expect(cursor).toBeVisible();

    // Verify cursor layer and animation are active
    const cycleState = await page.evaluate(() => {
      const el = document.querySelector('.ge-brush-cursor');
      window.__lastCycleAnim = el?._positionAnimation;
      return {
        cursorCount: document.querySelectorAll('.ge-brush-cursor').length,
        layerCount: document.querySelectorAll('#ge-cursor-layer').length,
        hasAnim: Boolean(el?._positionAnimation),
        animPaused: el?._positionAnimation?.playState === 'paused',
      };
    });
    expect(cycleState.cursorCount).toBe(1);
    expect(cycleState.layerCount).toBe(1);
    expect(cycleState.animPaused).toBe(true);

    // Close editor
    await page.evaluate(async () => {
      const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
      editor.closeEditor({ force: true });
    });

    // Verify complete cleanup after close
    await expect(cursor).toHaveCount(0);
    const postClose = await page.evaluate(() => {
      const anim = window.__lastCycleAnim;
      delete window.__lastCycleAnim;
      return {
        cursorCount: document.querySelectorAll('.ge-brush-cursor').length,
        layerCount: document.querySelectorAll('#ge-cursor-layer').length,
        animIdle: anim ? anim.playState === 'idle' : true,
        activeAnims: document.getAnimations().filter(a => {
          const target = a.effect?.target;
          return target && (target.classList?.contains('ge-brush-cursor') || target.id === 'ge-cursor-layer');
        }).length,
      };
    });
    expect(postClose.cursorCount).toBe(0);
    expect(postClose.layerCount).toBe(0);
    expect(postClose.animIdle).toBe(true);
    expect(postClose.activeAnims).toBe(0);
  }
});
