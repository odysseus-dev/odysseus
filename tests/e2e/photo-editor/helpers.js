const { expect } = require('@playwright/test');

async function openBlankEditor(page, size = { width: 640, height: 480 }, name = 'Photo editor E2E') {
  let lastError = null;
  for (let pageAttempt = 0; pageAttempt < 3; pageAttempt += 1) {
    try {
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await page.locator('#tool-gallery-btn').waitFor({ state: 'attached', timeout: 20_000 });
      await page.evaluate(async () => {
        window.__photoEditorImport = async path => {
          let lastImportError = null;
          for (let attempt = 0; attempt < 4; attempt += 1) {
            const url = attempt === 0 ? path : `${path}?e2e_retry=${attempt}-${Date.now()}`;
            try { return await import(url); } catch (error) { lastImportError = error; }
            await new Promise(resolve => setTimeout(resolve, 150 * (attempt + 1)));
          }
          throw lastImportError;
        };
        const gallery = await window.__photoEditorImport('/static/js/gallery.js?v=20260830editor4');
        gallery.openGallery();
      });
      await page.locator('#gallery-editor-tab').waitFor({ state: 'visible', timeout: 20_000 });
      await page.locator('#gallery-editor-tab').click();
      await page.evaluate(async ({ size: nextSize, name: nextName }) => {
        const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
        editor.openEditor(null, null, { w: nextSize.width, h: nextSize.height }, nextName);
      }, { size, name });
      await page.locator('.ge-main-canvas').waitFor({ state: 'visible', timeout: 20_000 });
      lastError = null;
      break;
    } catch (error) {
      lastError = error;
      await page.goto('about:blank');
      await page.waitForTimeout(250 * (pageAttempt + 1));
    }
  }
  if (lastError) throw lastError;
  await expect.poll(async () => page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    return state.layers.length;
  })).toBe(2);
}

async function editorState(page) {
  return page.evaluate(async () => {
    const { state } = await import('/static/js/editor/state.js');
    const hashCanvas = canvas => {
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let hash = 2166136261;
      for (let index = 0; index < data.length; index += Math.max(4, Math.floor(data.length / 20_000 / 4) * 4)) {
        const alpha = data[index + 3] / 255;
        hash ^= Math.round(data[index] * alpha);
        hash = Math.imul(hash, 16777619);
        hash ^= Math.round(data[index + 1] * alpha);
        hash = Math.imul(hash, 16777619);
        hash ^= Math.round(data[index + 2] * alpha);
        hash = Math.imul(hash, 16777619);
        hash ^= data[index + 3];
        hash = Math.imul(hash, 16777619);
      }
      return hash >>> 0;
    };
    const selectionBounds = canvas => {
      if (!canvas) return null;
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let left = canvas.width, top = canvas.height, right = -1, bottom = -1;
      for (let y = 0; y < canvas.height; y += 1) for (let x = 0; x < canvas.width; x += 1) {
        if (data[(y * canvas.width + x) * 4 + 3] < 1) continue;
        left = Math.min(left, x); top = Math.min(top, y);
        right = Math.max(right, x); bottom = Math.max(bottom, y);
      }
      return right < left ? null : { x: left, y: top, width: right - left + 1, height: bottom - top + 1 };
    };
    return {
      documentRenderReady: !!state.documentRenderReady,
      dimensions: [state.imgWidth, state.imgHeight],
      activeLayerId: state.activeLayerId,
      selectedLayerIds: [...(state.selectedLayerIds || [])],
      groups: (state.layerGroups || []).map(group => ({
        id: group.id,
        name: group.name,
        layerIds: [...group.layerIds],
        parentId: group.parentId || null,
        visible: group.visible !== false,
        opacity: group.opacity,
        blendMode: group.blendMode,
        locked: !!group.locked,
        collapsed: !!group.collapsed,
        activeMaskId: group.activeMaskId || null,
        effects: (group.effects || []).map(effect => ({
          id: effect.id,
          type: effect.type,
          name: effect.name,
          visible: effect.visible !== false,
          opacity: effect.opacity,
          params: JSON.parse(JSON.stringify(effect.params || {})),
        })),
        masks: (group.masks || []).map(mask => ({
          id: mask.id,
          name: mask.name,
          visible: mask.visible !== false,
          density: Number.isFinite(Number(mask.density)) ? mask.density : 1,
          feather: Number.isFinite(Number(mask.feather)) ? mask.feather : 0,
          mode: mask.mode,
          size: [mask.canvas.width, mask.canvas.height],
          pixelHash: hashCanvas(mask.canvas),
        })),
      })),
      layers: state.layers.map(layer => ({
        id: layer.id,
        name: layer.name,
        kind: layer.kind || 'raster',
        effects: (layer.effects || []).map(effect => ({
          id: effect.id,
          type: effect.type,
          name: effect.name,
          visible: effect.visible !== false,
          opacity: effect.opacity,
          mask: effect.mask ? {
            visible: effect.mask.visible !== false,
            size: [effect.mask.canvas?.width || effect.mask.canvasW, effect.mask.canvas?.height || effect.mask.canvasH],
          } : null,
          params: JSON.parse(JSON.stringify(effect.params || {})),
        })),
        visible: layer.visible !== false,
        opacity: layer.opacity,
        locked: !!layer.locked,
        locks: {
          pixels: !!layer.locks?.pixels,
          transparency: !!layer.locks?.transparency,
          position: !!layer.locks?.position,
        },
        clipped: !!layer.clipped,
        size: [layer.canvas.width, layer.canvas.height],
        offset: state.layerOffsets.get(layer.id) || { x: 0, y: 0 },
        pixelHash: hashCanvas(layer.canvas),
        text: layer.text ? {
          content: layer.text.content,
          fontSize: layer.text.fontSize,
          fontFamily: layer.text.fontFamily,
          lineHeight: layer.text.lineHeight,
          letterSpacing: layer.text.letterSpacing,
          frameWidth: layer.text.frameWidth,
          frameHeight: layer.text.frameHeight,
          verticalAlign: layer.text.verticalAlign,
          autoWidth: layer.text.autoWidth,
          transform: { ...layer.text.transform },
        } : null,
        shape: layer.shape ? {
          type: layer.shape.type,
          width: layer.shape.width,
          height: layer.shape.height,
          fillColor: layer.shape.fillColor,
          fillType: layer.shape.fillType,
          gradientStart: layer.shape.gradientStart,
          gradientMid: layer.shape.gradientMid,
          gradientMidEnabled: layer.shape.gradientMidEnabled,
          gradientMidPosition: layer.shape.gradientMidPosition,
          gradientEnd: layer.shape.gradientEnd,
          gradientStops: layer.shape.gradientStops ? layer.shape.gradientStops.map(stop => ({ ...stop })) : undefined,
          gradientAngle: layer.shape.gradientAngle,
          strokeColor: layer.shape.strokeColor,
          strokeWidth: layer.shape.strokeWidth,
          cornerRadius: layer.shape.cornerRadius,
          sides: layer.shape.sides,
          transform: { ...layer.shape.transform },
        } : null,
        adjustment: layer.adjustment ? JSON.parse(JSON.stringify(layer.adjustment)) : null,
        placed: layer.kind === 'placed' && layer.placed?.sourceCanvas ? {
          sourceSize: [layer.placed.sourceCanvas.width, layer.placed.sourceCanvas.height],
          sourceName: layer.placed.sourceName,
          matrix: [...layer.placed.matrix],
          sourcePixelHash: hashCanvas(layer.placed.sourceCanvas),
        } : null,
        masks: (layer.masks || []).map(mask => ({
          id: mask.id,
          mode: mask.mode,
          space: mask.space,
          linked: mask.mode === 'layer' ? mask.linked !== false : true,
          density: Number.isFinite(Number(mask.density)) ? mask.density : 1,
          feather: Number.isFinite(Number(mask.feather)) ? mask.feather : 0,
          offset: { x: Number(mask.offset?.x) || 0, y: Number(mask.offset?.y) || 0 },
          size: [mask.canvas.width, mask.canvas.height],
          pixelHash: hashCanvas(mask.canvas),
        })),
      })),
      guides: state.guides,
      draftId: state.draftId,
      undo: state.undoStack.length,
      redo: state.redoStack.length,
      quickMaskActive: !!state.quickMaskActive,
      selection: state.wandMask ? {
        space: state.wandMaskSpace,
        source: state.selectionSource,
        size: [state.wandMask.width, state.wandMask.height],
        pixelHash: hashCanvas(state.wandMask),
        bounds: selectionBounds(state.wandMask),
      } : null,
      savedSelections: (state.savedSelections || []).map(selection => ({
        id: selection.id,
        name: selection.name,
        size: [selection.canvas.width, selection.canvas.height],
        pixelHash: hashCanvas(selection.canvas),
        bounds: selectionBounds(selection.canvas),
      })),
      lastSelection: state.lastSelection?.canvas ? {
        pixelHash: hashCanvas(state.lastSelection.canvas),
        bounds: selectionBounds(state.lastSelection.canvas),
      } : null,
      persistIdle: !state.persistTimer && !state.persistInFlight,
    };
  });
}

async function dragOnCanvas(page, start, end) {
  const box = await page.locator('.ge-main-canvas').boundingBox();
  if (!box) throw new Error('Canvas has no visible bounds');
  await page.mouse.move(box.x + box.width * start.x, box.y + box.height * start.y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * end.x, box.y + box.height * end.y, { steps: 10 });
  await page.mouse.up();
}

async function waitForDraft(page) {
  await expect.poll(async () => {
    const current = await editorState(page);
    return !!current.draftId && current.persistIdle;
  }, { timeout: 15_000 }).toBe(true);
  return (await editorState(page)).draftId;
}

async function reopenDraft(page, draftId) {
  await page.evaluate(async id => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    window.__galleryAllowCloseEditor = true;
    editor.closeEditor();
    await editor.openEditor(null, null, null, 'Reopened E2E document', id);
  }, draftId);
  await page.locator('.ge-main-canvas').waitFor({ state: 'visible', timeout: 20_000 });
  await expect.poll(async () => (await editorState(page)).draftId).toBe(draftId);
  await expect.poll(async () => (await editorState(page)).documentRenderReady, {
    timeout: 20_000,
  }).toBe(true);
}

async function openExportDialog(page) {
  await page.locator('#ge-save-menu-btn').click();
  await page.locator('#ge-download').click();
  await expect(page.locator('.ge-export-dialog')).toBeVisible();
}

async function flattenedPixelDigest(page, size = null) {
  return page.evaluate(async targetSize => {
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    const image = new Image();
    image.src = editor.exportPNG();
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = targetSize?.width || image.naturalWidth;
    canvas.height = targetSize?.height || image.naturalHeight;
    const context = canvas.getContext('2d');
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const raw = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const pixels = new Uint8ClampedArray(raw.length);
    for (let index = 0; index < raw.length; index += 4) {
      const alpha = raw[index + 3] / 255;
      pixels[index] = Math.round(raw[index] * alpha);
      pixels[index + 1] = Math.round(raw[index + 1] * alpha);
      pixels[index + 2] = Math.round(raw[index + 2] * alpha);
      pixels[index + 3] = raw[index + 3];
    }
    const digest = await crypto.subtle.digest('SHA-256', pixels);
    return {
      width: canvas.width,
      height: canvas.height,
      sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join(''),
    };
  }, size);
}

async function encodedImagePixelDigest(page, bytes) {
  return page.evaluate(async base64 => {
    const binary = atob(base64);
    const encoded = Uint8Array.from(binary, character => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([encoded], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
    const raw = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const pixels = new Uint8ClampedArray(raw.length);
    for (let index = 0; index < raw.length; index += 4) {
      const alpha = raw[index + 3] / 255;
      pixels[index] = Math.round(raw[index] * alpha);
      pixels[index + 1] = Math.round(raw[index + 1] * alpha);
      pixels[index + 2] = Math.round(raw[index + 2] * alpha);
      pixels[index + 3] = raw[index + 3];
    }
    const digest = await crypto.subtle.digest('SHA-256', pixels);
    return {
      width: canvas.width,
      height: canvas.height,
      sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join(''),
    };
  }, bytes.toString('base64'));
}

async function compareExportPixels(page, bytes, size) {
  return page.evaluate(async ({ base64, targetSize }) => {
    const decode = async source => {
      const image = new Image();
      image.src = source;
      await image.decode();
      return image;
    };
    const editor = await window.__photoEditorImport('/static/js/galleryEditor.js');
    const expectedImage = await decode(editor.exportPNG());
    const binary = atob(base64);
    const actualImage = await decode(URL.createObjectURL(new Blob([
      Uint8Array.from(binary, character => character.charCodeAt(0)),
    ], { type: 'image/png' })));
    const pixels = image => {
      const canvas = document.createElement('canvas');
      canvas.width = targetSize.width;
      canvas.height = targetSize.height;
      const context = canvas.getContext('2d');
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      return context.getImageData(0, 0, canvas.width, canvas.height).data;
    };
    const expected = pixels(expectedImage);
    const actual = pixels(actualImage);
    URL.revokeObjectURL(actualImage.src);
    let total = 0;
    let maximum = 0;
    let changedPixels = 0;
    for (let index = 0; index < expected.length; index += 4) {
      const expectedAlpha = expected[index + 3] / 255;
      const actualAlpha = actual[index + 3] / 255;
      let pixelMaximum = Math.abs(expected[index + 3] - actual[index + 3]);
      total += pixelMaximum;
      for (let channel = 0; channel < 3; channel += 1) {
        const delta = Math.abs(
          expected[index + channel] * expectedAlpha - actual[index + channel] * actualAlpha,
        );
        total += delta;
        pixelMaximum = Math.max(pixelMaximum, delta);
      }
      maximum = Math.max(maximum, pixelMaximum);
      if (pixelMaximum > 1) changedPixels += 1;
    }
    return {
      meanAbsoluteError: total / expected.length,
      maximumError: maximum,
      changedPixelRatio: changedPixels / (expected.length / 4),
    };
  }, { base64: bytes.toString('base64'), targetSize: size });
}

module.exports = {
  compareExportPixels,
  dragOnCanvas,
  encodedImagePixelDigest,
  editorState,
  flattenedPixelDigest,
  openBlankEditor,
  openExportDialog,
  reopenDraft,
  waitForDraft,
};
