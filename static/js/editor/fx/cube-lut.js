/**
 * Adobe .cube 3D LUT support — parse, apply (trilinear), and generate.
 *
 * Pure functions (no DOM, no network, no module state) so they run identically
 * on the main thread, inside the adjustment worker, and under node in tests.
 *
 * Data layout: `data` is a flat RGB array with the RED component varying
 * fastest, then green, then blue — the ordering the .cube spec uses for its
 * row list. Sample index = (ri + gi*size + bi*size*size) * 3.
 */

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function sampleChannel(data, size, ri, gi, bi, ch) {
  return data[(ri + gi * size + bi * size * size) * 3 + ch];
}

/** Parse an Adobe .cube text file into { size, domainMin, domainMax, data }. */
export function parseCubeLut(text) {
  if (typeof text !== 'string' || !text.trim()) throw new Error('empty .cube data');
  const lines = text.split(/\r?\n/);
  let size = 0;
  let domainMin = [0, 0, 0];
  let domainMax = [1, 1, 1];
  const values = [];
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const parts = line.split(/\s+/);
    const keyword = parts[0].toUpperCase();
    if (keyword === 'TITLE') continue;
    if (keyword === 'LUT_3D_SIZE') { size = Math.max(2, Math.round(Number(parts[1]) || 0)); continue; }
    if (keyword === 'LUT_1D_SIZE') throw new Error('1D .cube LUTs are not supported');
    if (keyword === 'DOMAIN_MIN') { domainMin = [Number(parts[1]) || 0, Number(parts[2]) || 0, Number(parts[3]) || 0]; continue; }
    if (keyword === 'DOMAIN_MAX') { domainMax = [Number(parts[1]) || 1, Number(parts[2]) || 1, Number(parts[3]) || 1]; continue; }
    const r = Number(parts[0]);
    const g = Number(parts[1]);
    const b = Number(parts[2]);
    if (Number.isFinite(r) && Number.isFinite(g) && Number.isFinite(b)) values.push(r, g, b);
  }
  if (!size) throw new Error('missing LUT_3D_SIZE');
  const expected = size * size * size * 3;
  if (values.length < expected) throw new Error(`incomplete .cube data (${values.length / 3} of ${size ** 3} rows)`);
  for (let i = 0; i < size; i += 1) {
    if (domainMax[i] <= domainMin[i]) domainMax[i] = domainMin[i] + 1;
  }
  return { size, domainMin, domainMax, data: new Float64Array(values.slice(0, expected)) };
}

/** Trilinear lookup of an already domain-normalized [0,1] rgb triple. */
function trilinear(lut, r, g, b) {
  const n = lut.size;
  const max = n - 1;
  const fr = clamp01(r) * max;
  const fg = clamp01(g) * max;
  const fb = clamp01(b) * max;
  const r0 = Math.floor(fr); const r1 = Math.min(max, r0 + 1); const tr = fr - r0;
  const g0 = Math.floor(fg); const g1 = Math.min(max, g0 + 1); const tg = fg - g0;
  const b0 = Math.floor(fb); const b1 = Math.min(max, b0 + 1); const tb = fb - b0;
  const out = [0, 0, 0];
  for (let ch = 0; ch < 3; ch += 1) {
    const c000 = sampleChannel(lut.data, n, r0, g0, b0, ch);
    const c100 = sampleChannel(lut.data, n, r1, g0, b0, ch);
    const c010 = sampleChannel(lut.data, n, r0, g1, b0, ch);
    const c110 = sampleChannel(lut.data, n, r1, g1, b0, ch);
    const c001 = sampleChannel(lut.data, n, r0, g0, b1, ch);
    const c101 = sampleChannel(lut.data, n, r1, g0, b1, ch);
    const c011 = sampleChannel(lut.data, n, r0, g1, b1, ch);
    const c111 = sampleChannel(lut.data, n, r1, g1, b1, ch);
    const c00 = c000 + (c100 - c000) * tr;
    const c10 = c010 + (c110 - c010) * tr;
    const c01 = c001 + (c101 - c001) * tr;
    const c11 = c011 + (c111 - c011) * tr;
    const c0 = c00 + (c10 - c00) * tg;
    const c1 = c01 + (c11 - c01) * tg;
    out[ch] = c0 + (c1 - c0) * tb;
  }
  return out;
}

/**
 * Apply a parsed LUT in place to an RGBA byte array.
 * @param {Uint8ClampedArray|Uint8Array} data RGBA bytes
 * @param {number} strength 0..1 blend (1 = full LUT)
 */
export function applyCubeLut(data, width, height, lut, { strength = 1 } = {}) {
  if (!lut || !lut.data || !lut.size) return data;
  const s = clamp01(Number.isFinite(strength) ? strength : 1);
  if (s <= 0) return data;
  const { domainMin, domainMax } = lut;
  const n = data.length;
  const out = [0, 0, 0];
  for (let i = 0; i < n; i += 4) {
    const r = (data[i] / 255 - domainMin[0]) / (domainMax[0] - domainMin[0]);
    const g = (data[i + 1] / 255 - domainMin[1]) / (domainMax[1] - domainMin[1]);
    const b = (data[i + 2] / 255 - domainMin[2]) / (domainMax[2] - domainMin[2]);
    const mapped = trilinear(lut, r, g, b);
    data[i] = Math.round((data[i] / 255 + (clamp01(mapped[0]) - data[i] / 255) * s) * 255);
    data[i + 1] = Math.round((data[i + 1] / 255 + (clamp01(mapped[1]) - data[i + 1] / 255) * s) * 255);
    data[i + 2] = Math.round((data[i + 2] / 255 + (clamp01(mapped[2]) - data[i + 2] / 255) * s) * 255);
  }
  return data;
}

/** Serialize a { size, data } LUT to .cube text (r fastest). */
export function generateCubeLut(lut) {
  const size = lut?.size;
  if (!size || !lut.data) throw new Error('lut with size + data required');
  const domainMin = lut.domainMin || [0, 0, 0];
  const domainMax = lut.domainMax || [1, 1, 1];
  const lines = [
    'TITLE "odysseus"',
    `LUT_3D_SIZE ${size}`,
    `DOMAIN_MIN ${domainMin[0].toFixed(6)} ${domainMin[1].toFixed(6)} ${domainMin[2].toFixed(6)}`,
    `DOMAIN_MAX ${domainMax[0].toFixed(6)} ${domainMax[1].toFixed(6)} ${domainMax[2].toFixed(6)}`,
  ];
  for (let b = 0; b < size; b += 1) {
    for (let g = 0; g < size; g += 1) {
      for (let r = 0; r < size; r += 1) {
        const o = (r + g * size + b * size * size) * 3;
        lines.push(`${lut.data[o].toFixed(6)} ${lut.data[o + 1].toFixed(6)} ${lut.data[o + 2].toFixed(6)}`);
      }
    }
  }
  return lines.join('\n') + '\n';
}

/** Identity LUT of the given grid size. */
export function identityLut(size = 2) {
  const n = Math.max(2, Math.round(size));
  const data = new Float64Array(n * n * n * 3);
  const max = n - 1;
  for (let b = 0; b < n; b += 1) {
    for (let g = 0; g < n; g += 1) {
      for (let r = 0; r < n; r += 1) {
        const o = (r + g * n + b * n * n) * 3;
        data[o] = r / max; data[o + 1] = g / max; data[o + 2] = b / max;
      }
    }
  }
  return { size: n, domainMin: [0, 0, 0], domainMax: [1, 1, 1], data };
}

export default { parseCubeLut, applyCubeLut, generateCubeLut, identityLut };
