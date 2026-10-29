/** Retained text-layer model and raster-cache renderer. */

export const DEFAULT_TEXT_DATA = Object.freeze({
  content: 'Text',
  fontFamily: 'Arial',
  fontSize: 48,
  fontWeight: '400',
  fontStyle: 'normal',
  align: 'left',
  color: '#ffffff',
  strokeColor: '#000000',
  strokeWidth: 0,
  lineHeight: 1.2,
  letterSpacing: 0,
  frameWidth: 320,
  frameHeight: 0,
  autoWidth: true,
  verticalAlign: 'top',
  vertical: false,
  smallCaps: false,
  kerning: true,
  ligatures: true,
  warp: 'none',
  warpAmount: 0,
  path: null,
  transform: Object.freeze({ scaleX: 1, scaleY: 1, rotation: 0, flipH: false, flipV: false }),
});

export const WARP_STYLES = Object.freeze([
  'none', 'arc', 'arch', 'bulge', 'flag', 'wave', 'rise', 'fisheye', 'inflate', 'squeeze', 'twist',
]);

/* Self-hosted editor text fonts (see @font-face in 00-tokens.css). Bundled so
   every device renders distinct faces; the trailing two are kept as optional
   system extras but are filtered out at runtime when unavailable. */
export const EDITOR_TEXT_FONTS = Object.freeze([
  'Inter', 'Roboto', 'Playfair Display', 'Lobster', 'Bebas Neue', 'Roboto Mono',
  'Space Grotesk', 'Fraunces', 'Syne', 'Bungee', 'Instrument Serif',
  'Georgia', 'Courier New',
]);

/* Ask the browser to fetch each family. Resolves once they settle so a canvas
   re-render can pick up faces that were not yet loaded on first paint. */
export function loadEditorFonts() {
  if (typeof document === 'undefined' || !document.fonts || !document.fonts.load) return Promise.resolve([]);
  return Promise.all(EDITOR_TEXT_FONTS.flatMap((family) => ['400', '700'].map((weight) => {
    try { return document.fonts.load(`${weight} 48px "${family}"`).catch(() => []); } catch (_) { return Promise.resolve([]); }
  }))).catch(() => []);
}

/* Only offer families the browser can actually render distinctly. */
export function availableEditorFonts() {
  if (typeof document === 'undefined' || !document.fonts || !document.fonts.check) return EDITOR_TEXT_FONTS.slice();
  return EDITOR_TEXT_FONTS.filter((family) => {
    try { return document.fonts.check(`48px "${family}"`); } catch (_) { return true; }
  });
}

function finite(value, fallback, min = -Infinity, max = Infinity) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback;
}

export function normalizeTextData(value = {}) {
  const transform = value.transform || {};
  return {
    content: String(value.content ?? DEFAULT_TEXT_DATA.content),
    fontFamily: String(value.fontFamily || DEFAULT_TEXT_DATA.fontFamily),
    fontSize: finite(value.fontSize, DEFAULT_TEXT_DATA.fontSize, 1, 2000),
    fontWeight: String(value.fontWeight || DEFAULT_TEXT_DATA.fontWeight),
    fontStyle: value.fontStyle === 'italic' ? 'italic' : 'normal',
    align: ['left', 'center', 'right'].includes(value.align) ? value.align : 'left',
    color: String(value.color || DEFAULT_TEXT_DATA.color),
    strokeColor: String(value.strokeColor || DEFAULT_TEXT_DATA.strokeColor),
    strokeWidth: finite(value.strokeWidth, DEFAULT_TEXT_DATA.strokeWidth, 0, 100),
    lineHeight: finite(value.lineHeight, DEFAULT_TEXT_DATA.lineHeight, 0.5, 5),
    letterSpacing: finite(value.letterSpacing, DEFAULT_TEXT_DATA.letterSpacing, -100, 500),
    frameWidth: finite(value.frameWidth, DEFAULT_TEXT_DATA.frameWidth, 1, 10000),
    frameHeight: finite(value.frameHeight, DEFAULT_TEXT_DATA.frameHeight, 0, 10000),
    autoWidth: value.autoWidth !== false,
    verticalAlign: ['top', 'middle', 'bottom'].includes(value.verticalAlign) ? value.verticalAlign : 'top',
    vertical: !!value.vertical,
    smallCaps: !!value.smallCaps,
    kerning: value.kerning !== false,
    ligatures: value.ligatures !== false,
    warp: WARP_STYLES.includes(value.warp) ? value.warp : 'none',
    warpAmount: finite(value.warpAmount, DEFAULT_TEXT_DATA.warpAmount, -100, 100),
    path: Array.isArray(value.path)
      ? value.path
          .filter((point) => Number.isFinite(Number(point?.x)) && Number.isFinite(Number(point?.y)))
          .map((point) => ({ x: Number(point.x), y: Number(point.y) }))
      : null,
    transform: {
      scaleX: finite(transform.scaleX, 1, 0.01, 100),
      scaleY: finite(transform.scaleY, 1, 0.01, 100),
      rotation: finite(transform.rotation, 0, -36000, 36000),
      flipH: !!transform.flipH,
      flipV: !!transform.flipV,
    },
  };
}

function fontString(text) {
  const family = text.fontFamily.includes(',')
    ? text.fontFamily
    : `"${text.fontFamily.replace(/["\\]/g, '')}", Arial, sans-serif`;
  return `${text.fontStyle} ${text.fontWeight} ${text.fontSize}px ${family}`;
}

function measureSpacedText(ctx, value, spacing) {
  const chars = [...String(value || '')];
  if (!chars.length) return 0;
  return chars.reduce((width, char) => width + ctx.measureText(char).width, 0) + spacing * Math.max(0, chars.length - 1);
}

function drawSpacedText(ctx, value, x, y, spacing, stroke = false) {
  const chars = [...String(value || '')];
  if (!chars.length) return;
  const width = measureSpacedText(ctx, value, spacing);
  let cursor = ctx.textAlign === 'center' ? x - width / 2 : ctx.textAlign === 'right' ? x - width : x;
  const draw = stroke ? ctx.strokeText.bind(ctx) : ctx.fillText.bind(ctx);
  for (const char of chars) {
    draw(char, cursor, y);
    cursor += ctx.measureText(char).width + spacing;
  }
}

function wrapText(ctx, content, width, spacing) {
  const result = [];
  for (const paragraph of String(content).split('\n')) {
    if (!width) {
      result.push(paragraph);
      continue;
    }
    const words = paragraph.split(/(\s+)/).filter(Boolean);
    if (!words.length) {
      result.push('');
      continue;
    }
    let line = '';
    for (const word of words) {
      const candidate = line + word;
      if (line && measureSpacedText(ctx, candidate, spacing) > width) {
        result.push(line.trimEnd());
        line = word.trimStart();
      } else {
        line = candidate;
      }
    }
    result.push(line);
  }
  return result.length ? result : [''];
}

function applyFontOptions(ctx, text) {
  try { ctx.fontKerning = text.kerning === false ? 'none' : 'normal'; } catch {}
  try { if ('fontVariantCaps' in ctx) ctx.fontVariantCaps = text.smallCaps ? 'small-caps' : 'normal'; } catch {}
  try { if ('fontVariantLigatures' in ctx) ctx.fontVariantLigatures = text.ligatures === false ? 'none' : 'normal'; } catch {}
}

function baseTextCanvas(text) {
  const measure = document.createElement('canvas');
  const measureCtx = measure.getContext('2d');
  measureCtx.font = fontString(text);
  applyFontOptions(measureCtx, text);
  const padding = Math.ceil(Math.max(4, text.strokeWidth + 3));
  const lineHeightPx = text.fontSize * text.lineHeight;

  if (text.vertical) {
    const lines = String(text.content).split('\n');
    const advance = Math.max(1, lineHeightPx + text.letterSpacing);
    const columnWidth = Math.max(1, lineHeightPx + text.letterSpacing);
    const maxChars = Math.max(1, ...lines.map(line => [...line].length));
    const contentWidth = Math.max(1, lines.length * columnWidth);
    const contentHeight = Math.max(1, maxChars * advance);
    const frameWidth = text.autoWidth ? contentWidth : Math.max(contentWidth, Math.max(1, text.frameWidth));
    const frameHeight = text.frameHeight > 0 ? Math.max(contentHeight, text.frameHeight) : contentHeight;
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.ceil(frameWidth + padding * 2));
    canvas.height = Math.max(1, Math.ceil(frameHeight + padding * 2));
    const ctx = canvas.getContext('2d');
    ctx.font = fontString(text);
    applyFontOptions(ctx, text);
    ctx.textBaseline = 'top';
    ctx.textAlign = 'center';
    ctx.fillStyle = text.color;
    ctx.strokeStyle = text.strokeColor;
    ctx.lineWidth = text.strokeWidth * 2;
    ctx.lineJoin = 'round';
    const columnsWidth = lines.length * columnWidth;
    const baseX = text.align === 'center'
      ? (canvas.width - columnsWidth) / 2
      : text.align === 'right' ? canvas.width - padding - columnsWidth : padding;
    const verticalOffset = text.verticalAlign === 'middle'
      ? (frameHeight - contentHeight) / 2
      : text.verticalAlign === 'bottom' ? frameHeight - contentHeight : 0;
    lines.forEach((line, column) => {
      const x = baseX + column * columnWidth + columnWidth / 2;
      [...line].forEach((char, index) => {
        const y = padding + verticalOffset + index * advance;
        if (text.strokeWidth > 0) ctx.strokeText(char, x, y);
        ctx.fillText(char, x, y);
      });
    });
    return canvas;
  }

  const requestedWidth = text.autoWidth ? 0 : Math.max(1, text.frameWidth);
  const lines = wrapText(measureCtx, text.content, requestedWidth, text.letterSpacing);
  const measuredWidth = Math.max(1, ...lines.map(line => measureSpacedText(measureCtx, line || ' ', text.letterSpacing)));
  const contentWidth = text.autoWidth ? measuredWidth : requestedWidth;
  const contentHeight = Math.max(1, lines.length * lineHeightPx);
  const frameHeight = text.frameHeight > 0 ? Math.max(contentHeight, text.frameHeight) : contentHeight;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.ceil(contentWidth + padding * 2));
  canvas.height = Math.max(1, Math.ceil(frameHeight + padding * 2));
  const ctx = canvas.getContext('2d');
  ctx.font = fontString(text);
  ctx.textBaseline = 'top';
  ctx.textAlign = text.align;
  ctx.fillStyle = text.color;
  ctx.strokeStyle = text.strokeColor;
  ctx.lineWidth = text.strokeWidth * 2;
  ctx.lineJoin = 'round';
  const x = text.align === 'center' ? canvas.width / 2 : text.align === 'right' ? canvas.width - padding : padding;
  const verticalOffset = text.verticalAlign === 'middle'
    ? (frameHeight - contentHeight) / 2
    : text.verticalAlign === 'bottom' ? frameHeight - contentHeight : 0;
  lines.forEach((line, index) => {
    const y = padding + verticalOffset + index * lineHeightPx;
    if (text.strokeWidth > 0) drawSpacedText(ctx, line, x, y, text.letterSpacing, true);
    drawSpacedText(ctx, line, x, y, text.letterSpacing, false);
  });
  return canvas;
}

export function warpCanvas(base, style, amount) {
  if (!base || !style || style === 'none' || !amount) return base;
  const w = base.width;
  const h = base.height;
  if (w < 2 || h < 2) return base;
  const src = base.getContext('2d').getImageData(0, 0, w, h);
  const out = document.createElement('canvas');
  out.width = w;
  out.height = h;
  const octx = out.getContext('2d');
  const dst = octx.createImageData(w, h);
  const a = Math.max(-1, Math.min(1, amount / 100));
  for (let y = 0; y < h; y += 1) {
    const ny = (y + 0.5) / h - 0.5;
    for (let x = 0; x < w; x += 1) {
      const nx = (x + 0.5) / w - 0.5;
      let sx = x;
      let sy = y;
      switch (style) {
        case 'arc': sy = y - a * h * 0.35 * (1 - 4 * nx * nx); break;
        case 'arch': sy = y + a * h * 0.35 * (1 - 4 * nx * nx); break;
        case 'flag': sy = y - a * h * 0.25 * Math.sin(nx * Math.PI * 2); break;
        case 'wave': sy = y - a * h * 0.2 * Math.sin(nx * Math.PI * 4); break;
        case 'rise': sy = y - a * h * (nx + 0.5); break;
        case 'bulge': { const f = 1 + a * 0.5 * (1 - 4 * ny * ny); sx = (x - w / 2) / Math.max(0.2, f) + w / 2; sy = (y - h / 2) / Math.max(0.2, f) + h / 2; break; }
        case 'fisheye': { const f = 1 + a * (1 - Math.min(1, Math.hypot(nx, ny) * 2)); sx = (x - w / 2) / Math.max(0.2, f) + w / 2; sy = (y - h / 2) / Math.max(0.2, f) + h / 2; break; }
        case 'inflate': { const f = 1 - a * 0.5 * (1 - 4 * ny * ny); sx = (x - w / 2) / Math.max(0.2, f) + w / 2; break; }
        case 'squeeze': { const f = 1 + a * 0.5 * (1 - 4 * ny * ny); sx = (x - w / 2) / Math.max(0.2, f) + w / 2; break; }
        case 'twist': {
          const angle = a * Math.PI * 0.5 * (1 - Math.min(1, Math.hypot(nx, ny) * 2));
          const c = Math.cos(angle);
          const s = Math.sin(angle);
          const dx = x - w / 2;
          const dy = y - h / 2;
          sx = w / 2 + dx * c - dy * s;
          sy = h / 2 + dx * s + dy * c;
          break;
        }
        default: break;
      }
      const sxi = sx < 0 ? 0 : sx >= w ? w - 1 : Math.round(sx);
      const syi = sy < 0 ? 0 : sy >= h ? h - 1 : Math.round(sy);
      const si = (syi * w + sxi) * 4;
      const di = (y * w + x) * 4;
      dst.data[di] = src.data[si];
      dst.data[di + 1] = src.data[si + 1];
      dst.data[di + 2] = src.data[si + 2];
      dst.data[di + 3] = src.data[si + 3];
    }
  }
  octx.putImageData(dst, 0, 0);
  return out;
}

/* Distance (in path-local pixels) kept around the path so glyphs laid along it
   are never clipped at the canvas edges. Shared by the renderer and by the
   caller that positions the text layer over the path. */
export function pathTextPadding(text) {
  const t = text || {};
  return Math.ceil(Math.max(8, (Number(t.strokeWidth) || 0) + (Number(t.fontSize) || 48) * 0.8));
}

export function hasTextPath(value) {
  const text = value || {};
  return Array.isArray(text.path) && text.path.length >= 2;
}

/* Lay a single line of text out along a polyline path, advancing each glyph by
   its measured width (plus letterSpacing) and rotating it to the local tangent
   of the segment it lands on. Coordinates are the path's own local pixels; the
   caller offsets the layer so the drawn path matches where the user drew it. */
export function pathTextCanvas(text) {
  const points = text.path;
  const pad = pathTextPadding(text);
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const width = Math.max(1, Math.ceil(Math.max(...xs) + pad));
  const height = Math.max(1, Math.ceil(Math.max(...ys) + pad));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.font = fontString(text);
  applyFontOptions(ctx, text);
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';
  ctx.fillStyle = text.color;
  ctx.strokeStyle = text.strokeColor;
  ctx.lineWidth = text.strokeWidth * 2;
  ctx.lineJoin = 'round';

  const segments = [];
  let total = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy);
    segments.push({ a, dx, dy, len, start: total });
    total += len;
  }
  const atDistance = (distance) => {
    const clamped = Math.max(0, Math.min(total, distance));
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i];
      if (clamped <= seg.start + seg.len || i === segments.length - 1) {
        const t = seg.len ? (clamped - seg.start) / seg.len : 0;
        return { x: seg.a.x + seg.dx * t, y: seg.a.y + seg.dy * t, angle: Math.atan2(seg.dy, seg.dx) };
      }
    }
    const last = points[points.length - 1];
    return { x: last.x, y: last.y, angle: 0 };
  };

  const glyphs = [...String(text.content || '')];
  let textWidth = 0;
  glyphs.forEach((char, index) => {
    textWidth += ctx.measureText(char).width;
    if (index < glyphs.length - 1) textWidth += Number(text.letterSpacing) || 0;
  });
  // Centre the run of glyphs on the path so a closed curve (circle) reads as
  // symmetric instead of piling every glyph at the start of the sweep.
  let distance = Math.max(0, (total - textWidth) / 2);
  for (const char of glyphs) {
    const glyphWidth = ctx.measureText(char).width;
    const mid = distance + glyphWidth / 2;
    const pos = atDistance(mid);
    ctx.save();
    ctx.translate(pos.x, pos.y);
    ctx.rotate(pos.angle);
    if (text.strokeWidth > 0) ctx.strokeText(char, 0, 0);
    ctx.fillText(char, 0, 0);
    ctx.restore();
    distance += glyphWidth + (Number(text.letterSpacing) || 0);
  }
  return canvas;
}

export function renderTextLayer(layer) {
  if (!layer?.canvas || !layer?.ctx) return null;
  layer.kind = 'text';
  layer.text = normalizeTextData(layer.text);
  const base = hasTextPath(layer.text)
    ? pathTextCanvas(layer.text)
    : warpCanvas(baseTextCanvas(layer.text), layer.text.warp, layer.text.warpAmount);
  const transform = layer.text.transform;
  const scaledWidth = Math.max(1, base.width * transform.scaleX);
  const scaledHeight = Math.max(1, base.height * transform.scaleY);
  const radians = (transform.rotation * Math.PI) / 180;
  const cos = Math.abs(Math.cos(radians));
  const sin = Math.abs(Math.sin(radians));
  const width = Math.max(1, Math.ceil(scaledWidth * cos + scaledHeight * sin));
  const height = Math.max(1, Math.ceil(scaledWidth * sin + scaledHeight * cos));
  const rendered = document.createElement('canvas');
  rendered.width = width;
  rendered.height = height;
  const ctx = rendered.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.translate(width / 2, height / 2);
  ctx.rotate(radians);
  ctx.scale(transform.flipH ? -1 : 1, transform.flipV ? -1 : 1);
  ctx.drawImage(base, -scaledWidth / 2, -scaledHeight / 2, scaledWidth, scaledHeight);
  layer.canvas.width = width;
  layer.canvas.height = height;
  layer.ctx = layer.canvas.getContext('2d');
  layer.ctx.clearRect(0, 0, width, height);
  layer.ctx.drawImage(rendered, 0, 0);
  layer._adjCache = null;
  layer._adjCacheKey = null;
  layer._adjFinal = null;
  layer._adjFinalKey = null;
  return layer.canvas;
}

/** Mark a retained text cache as ordinary pixels after a destructive edit. */
export function rasterizeTextLayer(layer) {
  if (!layer || layer.kind !== 'text') return false;
  layer.kind = 'raster';
  layer.text = null;
  return true;
}

export function scaleTextLayer(layer, scaleX, scaleY) {
  layer.text = normalizeTextData(layer.text);
  layer.text.transform.scaleX *= scaleX;
  layer.text.transform.scaleY *= scaleY;
  return renderTextLayer(layer);
}

export function rotateTextLayer(layer, degrees) {
  layer.text = normalizeTextData(layer.text);
  layer.text.transform.rotation += degrees;
  return renderTextLayer(layer);
}

export function flipTextLayer(layer, axis) {
  layer.text = normalizeTextData(layer.text);
  if (axis === 'h') layer.text.transform.flipH = !layer.text.transform.flipH;
  else layer.text.transform.flipV = !layer.text.transform.flipV;
  return renderTextLayer(layer);
}
