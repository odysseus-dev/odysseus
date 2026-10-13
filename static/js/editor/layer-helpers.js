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
    case 'exposure':            return { exposure: 0, offset: 0, gamma: 1 };
    case 'white-balance':       return { temperature: 0, tint: 0 };
    case 'hue-saturation':      return { hue: 0, saturation: 1, lightness: 0 };
    case 'vibrance':            return { vibrance: 0 };
    case 'black-white':         return { red: 30, green: 59, blue: 11, constant: 0 };
    case 'shadows-highlights':  return { shadows: 0, highlights: 0 };
    case 'levels':              return {
      channel: 'rgb',
      inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255,
      channels: {
        red: { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 },
        green: { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 },
        blue: { inBlack: 0, inWhite: 255, gamma: 1.0, outBlack: 0, outWhite: 255 },
      },
    };
    case 'curves':              return {
      channel: 'rgb',
      points: {
        rgb: [[0, 0], [255, 255]],
        red: [[0, 0], [255, 255]],
        green: [[0, 0], [255, 255]],
        blue: [[0, 0], [255, 255]],
      },
    };
    case 'color-balance':       return {
      shadows:    { r: 0, g: 0, b: 0 },
      midtones:   { r: 0, g: 0, b: 0 },
      highlights: { r: 0, g: 0, b: 0 },
    };
    case 'selective-color':     return {
      range: 'reds',
      ranges: Object.fromEntries(
        ['reds', 'yellows', 'greens', 'cyans', 'blues', 'magentas', 'neutrals', 'blacks']
          .map(name => [name, { cyan: 0, magenta: 0, yellow: 0, black: 0 }]),
      ),
    };
    case 'gradient-map':        return {
      shadows: '#000000',
      highlights: '#ffffff',
      midpoint: 50,
      reverse: false,
    };
  }
  return {};
}

/** Preset names for the retained adjustment popup. */
export function adjustmentPresetOptions(type) {
  const options = {
    'brightness-contrast': ['Default', 'High Contrast'],
    'exposure': ['Default', 'Lift Exposure', 'Lower Exposure'],
    'white-balance': ['Default', 'Warm', 'Cool'],
    'hue-saturation': ['Default', 'Desaturate', 'Boost Color'],
    'vibrance': ['Default', 'Muted', 'Color Pop'],
    'black-white': ['Default', 'High Contrast'],
    'shadows-highlights': ['Default', 'Lift Shadows', 'Recover Highlights'],
    'levels': ['Default', 'Auto Contrast'],
    'curves': ['Default', 'S-Curve'],
    'color-balance': ['Default', 'Warm Highlights', 'Cool Shadows'],
    'selective-color': ['Default', 'Deep Reds'],
    'gradient-map': ['Default', 'Warm Tone', 'Cool Tone'],
  };
  return options[type] || ['Default'];
}

/** Return a fresh parameter object for a named adjustment preset. */
export function adjustmentPresetParams(type, preset) {
  const params = defaultAdjParams(type);
  switch (`${type}:${preset}`) {
    case 'brightness-contrast:High Contrast':
      return { ...params, contrast: 1.28 };
    case 'exposure:Lift Exposure':
      return { ...params, exposure: 0.45 };
    case 'exposure:Lower Exposure':
      return { ...params, exposure: -0.45 };
    case 'white-balance:Warm':
      return { ...params, temperature: 35 };
    case 'white-balance:Cool':
      return { ...params, temperature: -35 };
    case 'hue-saturation:Desaturate':
      return { ...params, saturation: 0.35 };
    case 'hue-saturation:Boost Color':
      return { ...params, saturation: 1.35 };
    case 'vibrance:Muted':
      return { ...params, vibrance: -30 };
    case 'vibrance:Color Pop':
      return { ...params, vibrance: 45 };
    case 'black-white:High Contrast':
      return { ...params, red: 38, green: 54, blue: 8, constant: 0 };
    case 'shadows-highlights:Lift Shadows':
      return { ...params, shadows: 38 };
    case 'shadows-highlights:Recover Highlights':
      return { ...params, highlights: -38 };
    case 'levels:Auto Contrast':
      return { ...params, inBlack: 12, inWhite: 243 };
    case 'curves:S-Curve':
      return { ...params, points: { ...params.points, rgb: [[0, 12], [76, 64], [178, 194], [255, 243]] } };
    case 'color-balance:Warm Highlights':
      return { ...params, highlights: { r: 16, g: 4, b: -14 } };
    case 'color-balance:Cool Shadows':
      return { ...params, shadows: { r: -12, g: 3, b: 16 } };
    case 'selective-color:Deep Reds':
      return { ...params, ranges: { ...params.ranges, reds: { ...params.ranges.reds, black: -18 } } };
    case 'gradient-map:Warm Tone':
      return { ...params, shadows: '#21151a', highlights: '#ffd59a' };
    case 'gradient-map:Cool Tone':
      return { ...params, shadows: '#101c36', highlights: '#d8f2ff' };
    default:
      return params;
  }
}


/** Human-readable name for an adjustment type. */
export function adjLayerLabel(type) {
  return {
    'brightness-contrast': 'Brightness/Contrast',
    'exposure': 'Exposure',
    'white-balance': 'White Balance',
    'hue-saturation': 'Hue/Saturation',
    'vibrance': 'Vibrance',
    'black-white': 'Black & White',
    'shadows-highlights': 'Shadows/Highlights',
    'levels': 'Levels',
    'curves': 'Curves',
    'color-balance': 'Color Balance',
    'selective-color': 'Selective Color',
    'gradient-map': 'Gradient Map',
  }[type] || type;
}


/**
 * Per-type SVG icon strings. Used in popup title bars, the minimised
 * FX-dock chips, and the layer-panel sub-row name so the same glyph
 * shows up everywhere a given adjustment type appears.
 */
export const ADJ_ICONS = {
  'brightness-contrast': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>',
  'exposure': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4.5 13H11l-1 9 8.5-11H12z"/></svg>',
  'white-balance': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v10"/><circle cx="12" cy="17" r="4"/><path d="M9 6h3"/></svg>',
  'hue-saturation': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="12" r="4"/><circle cx="15" cy="9.5" r="4"/><circle cx="15" cy="14.5" r="4"/></svg>',
  'vibrance': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 18 10 6l4 12 6-12"/><path d="M7 14h10"/></svg>',
  'black-white': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>',
  'shadows-highlights': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="4"/><path d="M14 14h7M17.5 10.5v7"/><path d="M5 17h6"/></svg>',
  'levels': '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="14" width="3" height="6" rx="0.5"/><rect x="8" y="9" width="3" height="11" rx="0.5"/><rect x="13" y="11" width="3" height="9" rx="0.5"/><rect x="18" y="6" width="3" height="14" rx="0.5"/></svg>',
  'curves': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19C7 19 8 5 13 5s4 8 7 8"/><path d="M4 4v16h16"/></svg>',
  'color-balance': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12a9 9 0 0 1 9-9v18a9 9 0 0 1-9-9z" fill="currentColor" stroke="none"/></svg>',
  'selective-color': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M21 12h-6M12 21v-6M3 12h6"/></svg>',
  'gradient-map': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M12 6v12"/></svg>',
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
