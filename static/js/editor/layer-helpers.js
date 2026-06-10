/**
 * Pure helpers + constants for layers and adjustment sub-layers.
 *
 * Everything in this module is stateless — feed in a layer object and
 * get back a value. The legacy gallery editor's module-level helpers
 * re-export from here so existing call sites keep working unchanged.
 */

/** True if the layer has at least one FX/adjustment sub-layer. */
export function layerHasAdjustments(layer) {
  return !!(layer && layer.adjLayers && layer.adjLayers.length > 0);
}


/**
 * True if the layer carries a non-identity Levels OR Color-Balance
 * adjustment that needs the per-pixel pass (vs the cheap CSS-filter
 * path for plain B/C/H/S).
 */
export function layerNeedsPixelPass(layer) {
  if (!layer || !layer.adjustments) return false;
  const a = layer.adjustments;
  if (a.levels && (a.levels.inBlack !== 0 || a.levels.inWhite !== 255 ||
                   a.levels.gamma !== 1 ||
                   a.levels.outBlack !== 0 || a.levels.outWhite !== 255)) return true;
  if (a.colorBalance) {
    for (const tone of ['shadows', 'midtones', 'highlights']) {
      const v = a.colorBalance[tone];
      if (v && (v.r || v.g || v.b)) return true;
    }
  }
  return false;
}


/**
 * Compact hash of a layer's Levels + Color-Balance values. Used to
 * key the per-pixel adjustment cache so we can skip recomputing when
 * nothing changed.
 */
export function adjustmentsKey(adj) {
  adj = adj && typeof adj === 'object' ? adj : {};
  const l = adj.levels || {};
  const cb = adj.colorBalance || {};
  const s = cb.shadows || {}, m = cb.midtones || {}, h = cb.highlights || {};
  return [
    l.inBlack|0, l.inWhite|0, l.gamma || 1, l.outBlack|0, l.outWhite|0,
    s.r|0, s.g|0, s.b|0, m.r|0, m.g|0, m.b|0, h.r|0, h.g|0, h.b|0,
  ].join('|');
}


/** Identity params for each adjustment type. */
export function defaultAdjParams(type) {
  switch (type) {
    case 'brightness-contrast': return { brightness: 1, contrast: 1 };
    case 'hue-saturation':      return { hue: 0, saturation: 1 };
    case 'levels':              return { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 };
    case 'color-balance':       return {
      shadows:    { r: 0, g: 0, b: 0 },
      midtones:   { r: 0, g: 0, b: 0 },
      highlights: { r: 0, g: 0, b: 0 },
    };
  }
  return {};
}


/** Human-readable name for an adjustment type. */
export function adjLayerLabel(type) {
  return {
    'brightness-contrast': 'Brightness/Contrast',
    'hue-saturation': 'Hue/Saturation',
    'levels': 'Levels',
    'color-balance': 'Color Balance',
  }[type] || type;
}


/**
 * Per-type SVG icon strings. Used in popup title bars, the minimised
 * FX-dock chips, and the layer-panel sub-row name so the same glyph
 * shows up everywhere a given adjustment type appears.
 */
export const ADJ_ICONS = {
  'brightness-contrast': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>',
  'hue-saturation': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="12" r="4"/><circle cx="15" cy="9.5" r="4"/><circle cx="15" cy="14.5" r="4"/></svg>',
  'levels': '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="14" width="3" height="6" rx="0.5"/><rect x="8" y="9" width="3" height="11" rx="0.5"/><rect x="13" y="11" width="3" height="9" rx="0.5"/><rect x="18" y="6" width="3" height="14" rx="0.5"/></svg>',
  'color-balance': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12a9 9 0 0 1 9-9v18a9 9 0 0 1-9-9z" fill="currentColor" stroke="none"/></svg>',
};


/** SVG used in the topbar/history button glyphs. */
export const HISTORY_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><polyline points="12 7 12 12 16 14"/></svg>';


/** Quick downsampled-alpha check: are there any opaque pixels on this canvas? */
export function isMaskCanvasEmpty(canvas) {
  if (!canvas) return true;
  try {
    const w = canvas.width, h = canvas.height;
    if (!w || !h) return true;
    const sw = Math.min(200, w), sh = Math.min(200, h);
    const tmp = document.createElement('canvas');
    tmp.width = sw; tmp.height = sh;
    tmp.getContext('2d').drawImage(canvas, 0, 0, sw, sh);
    const d = tmp.getContext('2d').getImageData(0, 0, sw, sh).data;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return false;
    return true;
  } catch { return false; }
}


/** Same as `isMaskCanvasEmpty` but accepts a layer wrapper. */
export function isLayerEmpty(layer) {
  if (!layer || !layer.canvas) return true;
  return isMaskCanvasEmpty(layer.canvas);
}


/**
 * Compact "now / 30s / 12m / 4h" relative-time string. Used in the
 * editor's history panel labels.
 */
export function relTime(ts) {
  if (!ts) return '';
  const dt = (Date.now() - ts) / 1000;
  if (dt < 5) return 'now';
  if (dt < 60) return Math.round(dt) + 's';
  if (dt < 3600) return Math.round(dt / 60) + 'm';
  return Math.round(dt / 3600) + 'h';
}
