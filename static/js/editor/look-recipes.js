// Named-look -> deterministic adjustment recipe.
//
// A "look" is a film, photographer, era or colour style (Blade Runner, National
// Geographic, Wes Anderson, Kodachrome, moody Scandinavian, ...). When the
// server has no authored recipe for the phrase, the agent still needs to turn
// it into an explicit, deterministic grade instead of a random tweak or a full
// repaint. This module maps look phrasings to concrete adjustment steps using
// the editor's own control ops, so the result is reproducible.
//
// Steps are `{ op, params }` pairs run through window.geEditorControl.invoke:
//   photo.autoTone {}
//   adjustments.apply { type, params }   (brightness-contrast & vibrance are
//                                         -100..100, 0 = neutral)
//   filters.apply { id, ... }            (vignette / clarity / noise-add ...)

const A = (type, params) => ({ op: 'adjustments.apply', params: { type, params } });
const VIG = (amount, feather) => ({ op: 'filters.apply', params: { id: 'vignette', amount, feather } });
const GRAIN = () => ({ op: 'filters.apply', params: { id: 'noise-add' } });
const BLUR = () => ({ op: 'filters.apply', params: { id: 'blur-gaussian' } });
const CLARITY = (amount, radius) => ({ op: 'filters.apply', params: { id: 'clarity', amount, radius } });
const WB = (temperature, tint = 0) => A('white-balance', { temperature, tint });
const BC = (brightness, contrast) => A('brightness-contrast', { brightness, contrast });
const VIB = (vibrance) => A('vibrance', { vibrance });
const SH = (shadows, highlights) => A('shadows-highlights', { shadows, highlights });
const CB = (shadows, midtones, highlights) => A('color-balance', { shadows, midtones, highlights });
const BW = (red = 35, green = 45, blue = 20) => A('black-white', { red, green, blue, constant: 0 });
const AUTO = () => ({ op: 'photo.autoTone', params: {} });

// Curated named looks. Match SPECIFIC names (film / photographer / era / colour
// style) so generic mood words ("dramatic", "nice") keep their existing
// behaviour; a generic look that names nothing here falls to the general
// deriver below.
export const NAMED_LOOKS = [
  // "faded" intent is matched FIRST so "faded Kodachrome" reads as faded, not vivid.
  { name: 'faded / washed', re: /\bfaded\b|\bwashed[\s-]*out\b|\bmatte\b|\bhazy\b/i,
    steps: [AUTO(), A('photo-filter', { color: '#d8c3a2', density: 42, preserve_luminosity: true }), CB({ r: 4, g: 2, b: 2 }, { r: 2, g: 0, b: 2 }, { r: 0, g: 0, b: 0 }), BC(14, -20), VIB(-18), SH(26, -10), GRAIN(), VIG(18, 68)] },
  { name: 'Wes Anderson', re: /\bwes\s*anderson\b/i,
    steps: [AUTO(), A('photo-filter', { color: '#f3cfa4', density: 46, preserve_luminosity: true }), CB({ r: 0, g: 0, b: 12 }, { r: 6, g: 2, b: 6 }, { r: 16, g: 4, b: -8 }), VIB(-6), BC(6, 6), VIG(16, 70)] },
  { name: 'National Geographic', re: /\bnational\s*geographic\b|\bnat\s*geo\b|\bnatgeo\b/i,
    steps: [AUTO(), VIB(26), CLARITY(24, 28), CB({ r: 0, g: 0, b: 8 }, { r: 2, g: 2, b: 0 }, { r: 8, g: 2, b: -6 }), BC(2, 14), SH(8, -12)] },
  { name: 'Ansel Adams', re: /\bansel\s*adams\b/i,
    steps: [BW(30, 50, 20), CLARITY(34, 30), BC(0, 30), SH(-6, 14), VIG(20, 58)] },
  { name: 'Kurosawa', re: /\bkurosawa\b/i,
    steps: [AUTO(), WB(10, 4), CB({ r: -8, g: 0, b: 12 }, { r: -4, g: -2, b: 6 }, { r: 10, g: 3, b: -8 }), VIB(-6), BC(0, 16), GRAIN(), VIG(22, 60)] },
  { name: 'Kodachrome / Kodak', re: /\bkodachrome\b|\bkodak\b|\bportra\b|\bektar\b/i,
    steps: [AUTO(), WB(32, 10), A('photo-filter', { color: '#ffbe86', density: 18, preserve_luminosity: true }), CB({ r: 8, g: 2, b: -12 }, { r: 12, g: 2, b: -12 }, { r: 24, g: 4, b: -26 }), VIB(-6), BC(2, 4), SH(16, -28), VIG(14, 66)] },
  { name: 'teal and orange', re: /\bteal\s*(?:and|&|\+)\s*orange\b|\bcinematic\s+grade\b/i,
    steps: [AUTO(), CB({ r: -14, g: 4, b: 26 }, { r: 4, g: 0, b: 0 }, { r: 26, g: 4, b: -20 }), VIB(14), BC(0, 16)] },
  { name: 'film noir', re: /\bfilm\s*noir\b|\bnoir\b/i,
    steps: [BW(35, 45, 20), BC(-2, 26), VIG(30, 55)] },
  { name: '1980s / VHS', re: /\bvhs\b|\b1980s\b|\b80s\b/i,
    steps: [AUTO(), WB(4, 28), VIB(-16), BC(12, -16), CB({ r: 10, g: -6, b: 12 }, { r: 0, g: 0, b: 2 }, { r: -6, g: 8, b: -10 }), BLUR(), GRAIN(), GRAIN(), VIG(28, 58)] },
  { name: 'golden hour', re: /\bgolden\s*hour\b|\bgolden\s+light\b/i,
    steps: [WB(34, 10), VIB(22), BC(10, -6), SH(16, -14)] },
  { name: 'Technicolor', re: /\btechnicolor\b/i,
    steps: [AUTO(), CB({ r: -6, g: 2, b: 16 }, { r: 12, g: 0, b: -8 }, { r: 18, g: 3, b: -14 }), VIB(38), BC(2, 14), A('photo-filter', { color: '#ffcf8a', density: 24, preserve_luminosity: true })] },
  { name: 'Wong Kar-wai', re: /\bwong\s*kar[\s-]?wai\b/i,
    steps: [AUTO(), CB({ r: -12, g: 18, b: 12 }, { r: 0, g: 6, b: 6 }, { r: 22, g: 3, b: -10 }), VIB(26), BC(-2, 16)] },
  { name: 'Western', re: /\bwestern\b|\bspaghetti\s*western\b/i,
    steps: [AUTO(), WB(26, 8), VIB(-18), BC(6, 12), SH(14, -8), GRAIN(), VIG(22, 60)] },
  { name: 'disposable camera', re: /\bdisposable\b|\b1990s\b|\b90s\b|\bpoint[\s-]*(?:and|&)[\s-]*shoot\b/i,
    steps: [AUTO(), WB(16, 10), BC(9, -14), VIB(-10), GRAIN(), VIG(22, 64)] },
  { name: 'documentary', re: /\bdocumentary\b|\battenborough\b/i,
    steps: [AUTO(), VIB(22), CLARITY(24, 28), CB({ r: 0, g: 0, b: 6 }, { r: 2, g: 2, b: 0 }, { r: 6, g: 2, b: -6 }), BC(1, 12), SH(6, -10)] },
  { name: 'vintage film', re: /\b(?:retro|vintage|analog|analogue)\b|\b1970s\b|\b70s\b|\bseventies\b|\bpolaroid\b|\binstant\s*(?:camera|photo)\b|\bfilm\s*photograph\b|\bold\s+photo\b|\bdisposable\s+camera\b/i,
    steps: [AUTO(), WB(20, 10), BC(12, -18), VIB(-12), SH(20, -8), GRAIN(), VIG(24, 60)] },
  { name: 'sepia', re: /\bsepia\b|\btan\s+tone\b/i,
    steps: [BW(40, 42, 18), A('photo-filter', { color: '#b98a52', density: 70, preserve_luminosity: true }), BC(0, 14)] },
  { name: 'black and white', re: /\bblack\s*(?:and|&)\s*white\b|\bgrayscale\b|\bgreyscale\b|\bmonochrome\b|\bb\s*&\s*w\b/i,
    steps: [BW(35, 45, 20), BC(0, 22)] },
  { name: 'moody Scandinavian', re: /\bscandinavian\b|\bnordic\b|\bnorse\b|\bmoody\s+scandi\b/i,
    steps: [AUTO(), WB(-24, -6), VIB(-18), BC(2, 12), SH(20, 0)] },
  { name: 'Fuji / natural film', re: /\bfuji\s*film\b|\bfujifilm\b|\bsuperia\b|\bvelvia\b/i,
    steps: [AUTO(), WB(-8, 4), VIB(28), BC(1, 14)] },
  { name: 'Ilford / Tri-X', re: /\bilford\b|\btri[\s-]*x\b|\bhp5\b|\bhigh[\s-]*contrast\s+b\s*&?\s*w\b/i,
    steps: [BW(30, 49, 21), BC(0, 32), VIG(20, 56)] },
];

// Generic tone words -> a modest deterministic recipe for a named look that has
// no authored recipe. Requires an actual look marker so plain tone edits keep
// their existing deterministic branches.
const LOOK_MARK = /\b(?:look|style|aesthetic|vibe|mood|film|era|grade|camera|kodak|fuji)\b/i;
function generalSteps(text) {
  const t = String(text || '').toLowerCase();
  const warm = /\b(?:warm|warmer|golden|amber|sunset|orange|sepia)\b/.test(t);
  const cool = /\b(?:cool|cooler|cold|blue|teal|cyan|winter|scandinavian|nordic)\b/.test(t);
  const muted = /\b(?:muted|desaturated?|faded|washed|pastel|soft|dreamy|hazy|matte)\b/.test(t);
  const vivid = /\b(?:vibrant|vivid|saturated?|punchy|bold|rich|technicolor)\b/.test(t);
  const contrasty = /\b(?:contrasty?|dramatic|gritty|noir|high[\s-]*contrast)\b/.test(t);
  const faded = /\b(?:faded|washed|retro|vintage|film|analog|analogue|old|polaroid|kodak|8mm|16mm|vhs)\b/.test(t);
  const dark = /\b(?:dark|moody|low[\s-]*key|shadow|night)\b/.test(t);
  const bright = /\b(?:bright|airy|light|high[\s-]*key|clean)\b/.test(t);
  if (!(warm || cool || muted || vivid || contrasty || faded || dark || bright)) return null;
  const steps = [AUTO()];
  if (warm) steps.push(WB(18, 2));
  if (cool) steps.push(WB(-16));
  if (vivid) steps.push(VIB(20));
  if (muted) steps.push(VIB(-10));
  if (contrasty) steps.push(BC(0, 14));
  if (dark) steps.push(BC(-6, 8));
  if (bright) steps.push(BC(8, -4));
  if (faded) { steps.push(BC(5, -8)); steps.push(GRAIN()); }
  if (faded || dark) steps.push(VIG(16, 70));
  return steps.length > 1 ? steps : null;
}

// Looks that need a CONTENT change (new light, glow, an artistic medium) are
// not a colour grade: they route to the image model instead of a recipe.
export const CONTENT_LOOKS = /\b(?:blade\s*runner|neon|synthwave|vaporwave|cyberpunk|bloom|holograph\w*|ghibli|anime|manga|watercolo?ur|oil\s*paint|painting|painterly|comic(?:\s*book)?|cartoon|illustrat\w*|pixel\s*art|voxel|low\s*poly|steampunk|stained\s*glass|surreal|fantasy|3d\s*render|cgi|claymation|clay|mosaic|graffiti|glowing)\b/i;

function scaleTone(tone, k) {
  return tone ? { r: Math.round(tone.r * k), g: Math.round(tone.g * k), b: Math.round(tone.b * k) } : tone;
}
// Scale a recipe's adjustment strength so grades read at a glance. Weights
// (black-white / channel-mixer) and colours (gradient-map) are left as-is.
function scaleParams(type, params, k) {
  if (!params || k === 1) return params;
  const out = { ...params };
  if (type === 'color-balance') {
    out.shadows = scaleTone(out.shadows, k);
    out.midtones = scaleTone(out.midtones, k);
    out.highlights = scaleTone(out.highlights, k);
  } else if (type === 'black-white' || type === 'channel-mixer' || type === 'gradient-map') {
    return out;
  } else if (type === 'photo-filter') {
    if (typeof out.density === 'number') out.density = Math.min(100, Math.round(out.density * k));
  } else {
    for (const key of ['temperature', 'tint', 'brightness', 'contrast', 'vibrance', 'saturation', 'hue', 'lightness', 'shadows', 'highlights', 'exposure']) {
      if (typeof out[key] === 'number') out[key] = Math.round(out[key] * k * 10) / 10;
    }
  }
  return out;
}
function scaleSteps(steps, k) {
  if (!k || k === 1) return steps;
  return steps.map((s) => {
    if (s.op === 'adjustments.apply') {
      return { op: s.op, params: { type: s.params.type, params: scaleParams(s.params.type, s.params.params, k) } };
    }
    if (s.op === 'filters.apply' && typeof s.params.amount === 'number') {
      return { op: s.op, params: { ...s.params, amount: Math.round(s.params.amount * k) } };
    }
    return s;
  });
}

// Returns { name, steps } when `text` names a look we can render deterministically,
// or null when it is not a look (leave it to the normal routing). `opts.strength`
// (default 1) scales the grade so it is clearly visible.
export function lookRecipeSteps(text, opts = {}) {
  const s = String(text || '');
  if (CONTENT_LOOKS.test(s)) return null;
  const raw = Number(opts && opts.strength);
  const strength = Number.isFinite(raw) && raw > 0 ? raw : 1;
  for (const entry of NAMED_LOOKS) {
    if (entry.re.test(s)) return { name: entry.name, steps: scaleSteps(entry.steps, strength) };
  }
  if (LOOK_MARK.test(s)) {
    const steps = generalSteps(s);
    if (steps) return { name: 'look grade', steps: scaleSteps(steps, strength) };
  }
  return null;
}

export function hasNamedLook(text) {
  const s = String(text || '');
  return NAMED_LOOKS.some((entry) => entry.re.test(s));
}
