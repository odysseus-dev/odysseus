const { test, expect } = require('@playwright/test');
const { openBlankEditor, selectTool } = require('./helpers.js');

// Counts live listeners on window and document so a test can tell whether
// closing the editor really removed what opening it registered. Listeners
// registered with an AbortSignal drop out of the count when it aborts.
async function trackGlobalListeners(page) {
  await page.addInitScript(() => {
    const live = new Map();
    const targetName = target => (target === window ? 'window' : target === document ? 'document' : null);
    const keyOf = (name, type, options) => {
      const capture = typeof options === 'boolean' ? options : !!(options && options.capture);
      return `${name}|${type}|${capture}`;
    };
    const add = EventTarget.prototype.addEventListener;
    const remove = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function (type, listener, options) {
      const name = targetName(this);
      const signal = options && typeof options === 'object' ? options.signal : null;
      if (name && listener && !signal?.aborted) {
        const key = keyOf(name, type, options);
        if (!live.has(key)) live.set(key, new Set());
        const set = live.get(key);
        if (!set.has(listener)) {
          set.add(listener);
          signal?.addEventListener('abort', () => set.delete(listener), { once: true });
        }
      }
      return add.call(this, type, listener, options);
    };
    EventTarget.prototype.removeEventListener = function (type, listener, options) {
      const name = targetName(this);
      if (name && listener) live.get(keyOf(name, type, options))?.delete(listener);
      return remove.call(this, type, listener, options);
    };
    window.__globalListenerCount = () => {
      let total = 0;
      for (const set of live.values()) total += set.size;
      return total;
    };
  });
}

async function closeEditor(page) {
  await page.evaluate(async () => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    editor.closeEditor({ force: true });
  });
  await expect.poll(async () => page.evaluate(async () => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    return editor.isEditorOpen();
  })).toBe(false);
}

async function reopenEditor(page, name) {
  await page.evaluate(async nextName => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    editor.openEditor(null, null, { w: 320, h: 240 }, nextName);
  }, name);
  await page.locator('.ge-main-canvas').waitFor({ state: 'visible', timeout: 20_000 });
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.layers.length;
  })).toBe(2);
}

const paletteCount = page => page.evaluate(() => document.querySelectorAll('.ge-command-palette').length);

test('closing the editor removes its global listeners and command palette', async ({ page }) => {
  await trackGlobalListeners(page);
  await openBlankEditor(page, { width: 320, height: 240 }, 'Session teardown');

  const afterClose = [];
  for (let cycle = 1; cycle <= 4; cycle += 1) {
    expect(await paletteCount(page)).toBeLessThanOrEqual(1);
    await closeEditor(page);
    expect(await paletteCount(page)).toBe(0);
    afterClose.push(await page.evaluate(() => window.__globalListenerCount()));
    await reopenEditor(page, `Session teardown ${cycle + 1}`);
  }
  // Every listener a session adds must be gone once it closes, so the count
  // after the fourth close matches the count after the first.
  expect(afterClose[3]).toBe(afterClose[0]);

  await page.keyboard.press('Control+k');
  await expect(page.locator('.ge-command-palette:not([hidden])')).toHaveCount(1);
  await expect(page.locator('.ge-command-palette')).toHaveCount(1);
});

test('Escape still cancels crop, transform and lasso after reopening the editor', async ({ page }) => {
  await openBlankEditor(page, { width: 320, height: 240 }, 'Reopen Escape');
  for (let cycle = 1; cycle <= 2; cycle += 1) {
    await closeEditor(page);
    await reopenEditor(page, `Reopen Escape ${cycle + 1}`);
  }
  const canvas = page.locator('.ge-main-canvas');
  const box = await canvas.boundingBox();
  const editorState = () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return {
      cropping: state.cropping,
      cropRect: state.cropRect,
      transformActive: state.transformActive,
      lassoActive: state.lassoActive,
      lassoPoints: state.lassoPoints.length,
    };
  });

  await selectTool(page, 'crop');
  await page.mouse.move(box.x + 40, box.y + 35);
  await page.mouse.down();
  await page.mouse.move(box.x + 190, box.y + 150, { steps: 4 });
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect.poll(async () => {
    const current = await editorState();
    return { cropping: current.cropping, cropRect: current.cropRect };
  }).toEqual({ cropping: false, cropRect: null });

  await selectTool(page, 'transform');
  await expect(page.locator('#ge-transform-section')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect.poll(async () => (await editorState()).transformActive).toBe(false);

  await selectTool(page, 'lasso');
  await page.mouse.move(box.x + 40, box.y + 40);
  await page.mouse.down();
  await page.mouse.move(box.x + 150, box.y + 60, { steps: 4 });
  await page.mouse.move(box.x + 120, box.y + 160, { steps: 4 });
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect.poll(async () => {
    const current = await editorState();
    return { lassoActive: current.lassoActive, lassoPoints: current.lassoPoints };
  }).toEqual({ lassoActive: false, lassoPoints: 0 });
});
