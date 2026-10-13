/** Layer-style definitions and canvas rendering shared with the effects worker. */
const color = (key, label, value) => ({ key, label, type: 'color', value });
const number = (key, label, value, min, max, suffix = 'px') => ({ key, label, value, min, max, step: 1, suffix });
const opacity = () => number('opacity', 'Opacity', 65, 0, 100, '%');
const size = () => number('size', 'Size', 8, 0, 100);
const angle = () => number('angle', 'Angle', 120, -180, 180, 'deg');
export const LAYER_STYLES = {
  'bevel-emboss': { label: 'Bevel & Emboss', controls: [size(), angle(), number('depth', 'Depth', 100, 0, 300, '%'), color('highlight', 'Highlight', '#ffffff'), color('color', 'Shadow', '#000000'), opacity()] },
  'inner-shadow': { label: 'Inner Shadow', controls: [color('color', 'Color', '#000000'), opacity(), size(), angle(), number('distance', 'Distance', 6, 0, 100)] },
  'inner-glow': { label: 'Inner Glow', controls: [color('color', 'Color', '#ffffff'), opacity(), size()] },
  satin: { label: 'Satin', controls: [color('color', 'Color', '#000000'), opacity(), size(), angle(), number('distance', 'Distance', 12, 0, 100)] },
  'gradient-overlay': { label: 'Gradient Overlay', controls: [color('color', 'Start', '#e06c75'), color('endColor', 'End', '#ffffff'), opacity(), angle(), number('scale', 'Scale', 100, 10, 300, '%')] },
  'pattern-overlay': { label: 'Pattern Overlay', controls: [color('color', 'Color', '#ffffff'), color('endColor', 'Background', '#222222'), opacity(), number('scale', 'Tile size', 16, 2, 128)] },
  'outer-glow': { label: 'Outer Glow', controls: [color('color', 'Color', '#ffffff'), opacity(), size()] },
};

export function styleParams(type, values = {}) {
  return Object.fromEntries(LAYER_STYLES[type].controls.map(control => {
    const raw = values[control.key] ?? control.value;
    const value = control.type === 'color'
      ? (/^#[0-9a-f]{6}$/i.test(raw) ? raw : control.value)
      : Math.max(control.min, Math.min(control.max, Number.isFinite(Number(raw)) ? Number(raw) : control.value));
    return [control.key, value];
  }));
}

function surface(width, height) {
  if (typeof document === 'undefined') return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  return canvas;
}

export function renderDropShadow(source, params) {
  // Tint alpha first so RGB content cannot leak into the shadow. Draw only
  // this silhouette; compositing the source again thickens translucent edges.
  const silhouette = surface(source.width, source.height);
  const sc = silhouette.getContext('2d');
  sc.drawImage(source, 0, 0);
  sc.globalCompositeOperation = 'source-in';
  sc.fillStyle = params.color;
  sc.fillRect(0, 0, source.width, source.height);
  const shadow = surface(source.width, source.height), ctx = shadow.getContext('2d');
  ctx.globalAlpha = params.opacity;
  ctx.filter = `blur(${params.blur / 2}px)`;
  ctx.drawImage(silhouette, params.x, params.y);
  return shadow;
}

// Produce only the effect's contribution; the caller composites it over source.
export function renderLayerStyle(source, type, values) {
  const p = styleParams(type, values), w = source.width, h = source.height;
  const out = surface(w, h), ctx = out.getContext('2d');
  const rad = (p.angle || 0) * Math.PI / 180;
  const dx = Math.cos(rad) * (p.distance || 0), dy = -Math.sin(rad) * (p.distance || 0);
  const clip = () => {
    ctx.globalCompositeOperation = 'destination-in';
    ctx.drawImage(source, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
  };
  if (type === 'gradient-overlay' || type === 'pattern-overlay') {
    if (type === 'gradient-overlay') {
      const length = (Math.abs(Math.cos(rad)) * w + Math.abs(Math.sin(rad)) * h) * p.scale / 200;
      const gradient = ctx.createLinearGradient(w / 2 - Math.cos(rad) * length, h / 2 + Math.sin(rad) * length, w / 2 + Math.cos(rad) * length, h / 2 - Math.sin(rad) * length);
      gradient.addColorStop(0, p.color); gradient.addColorStop(1, p.endColor);
      ctx.fillStyle = gradient;
    } else {
      const tile = surface(p.scale * 2, p.scale * 2), tc = tile.getContext('2d');
      tc.fillStyle = p.endColor; tc.fillRect(0, 0, tile.width, tile.height);
      tc.fillStyle = p.color; tc.fillRect(0, 0, p.scale, p.scale); tc.fillRect(p.scale, p.scale, p.scale, p.scale);
      ctx.fillStyle = ctx.createPattern(tile, 'repeat');
    }
    ctx.fillRect(0, 0, w, h); clip();
  } else if (type === 'outer-glow') {
    ctx.filter = `blur(${p.size}px)`;
    ctx.drawImage(source, 0, 0); ctx.filter = 'none';
    ctx.globalCompositeOperation = 'source-in'; ctx.fillStyle = p.color; ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'destination-out'; ctx.drawImage(source, 0, 0);
  } else if (type === 'inner-shadow' || type === 'inner-glow') {
    // Pad the inverse alpha so edges touching the canvas still cast inward.
    const pad = Math.ceil(p.size * 3 + (p.distance || 0) + 2);
    const inverse = surface(w + pad * 2, h + pad * 2), ic = inverse.getContext('2d');
    ic.fillStyle = p.color; ic.fillRect(0, 0, inverse.width, inverse.height);
    ic.globalCompositeOperation = 'destination-out'; ic.drawImage(source, pad, pad);
    ctx.filter = `blur(${p.size}px)`;
    ctx.drawImage(inverse, -pad + dx, -pad + dy); ctx.filter = 'none'; clip();
  } else if (type === 'satin') {
    const a = surface(w, h), ac = a.getContext('2d');
    ac.filter = `blur(${p.size}px)`; ac.drawImage(source, dx, dy);
    ac.globalCompositeOperation = 'xor'; ac.drawImage(source, -dx, -dy); ac.filter = 'none';
    ctx.drawImage(a, 0, 0);
    ctx.globalCompositeOperation = 'source-in'; ctx.fillStyle = p.color; ctx.fillRect(0, 0, w, h); clip();
  } else if (type === 'bevel-emboss') {
    const blurred = surface(w, h), bc = blurred.getContext('2d');
    bc.filter = `blur(${Math.max(0.5, p.size / 2)}px)`; bc.drawImage(source, 0, 0);
    const alpha = bc.getImageData(0, 0, w, h).data;
    const original = source.getContext('2d').getImageData(0, 0, w, h).data;
    const pixels = ctx.createImageData(w, h), step = Math.max(1, Math.round(p.size / 2));
    const sample = (x, y) => x < 0 || x >= w || y < 0 || y >= h ? 0 : alpha[(y * w + x) * 4 + 3] / 255;
    const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    const light = rgb(p.highlight), dark = rgb(p.color);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const at = (y * w + x) * 4;
      const slope = ((sample(x + step, y) - sample(x - step, y)) * Math.cos(rad)
        - (sample(x, y + step) - sample(x, y - step)) * Math.sin(rad)) * p.depth / 100;
      pixels.data.set(slope > 0 ? light : dark, at);
      pixels.data[at + 3] = Math.min(1, Math.abs(slope)) * original[at + 3];
    }
    ctx.putImageData(pixels, 0, 0);
  }
  const faded = surface(w, h), fc = faded.getContext('2d');
  fc.globalAlpha = p.opacity / 100; fc.drawImage(out, 0, 0);
  return faded;
}
