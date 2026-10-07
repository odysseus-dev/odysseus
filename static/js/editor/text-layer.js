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
  transform: Object.freeze({ scaleX: 1, scaleY: 1, rotation: 0, flipH: false, flipV: false }),
});

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

function baseTextCanvas(text) {
  const measure = document.createElement('canvas');
  const measureCtx = measure.getContext('2d');
  measureCtx.font = fontString(text);
  const padding = Math.ceil(Math.max(4, text.strokeWidth + 3));
  const requestedWidth = text.autoWidth ? 0 : Math.max(1, text.frameWidth);
  const lines = wrapText(measureCtx, text.content, requestedWidth, text.letterSpacing);
  const measuredWidth = Math.max(1, ...lines.map(line => measureSpacedText(measureCtx, line || ' ', text.letterSpacing)));
  const contentWidth = text.autoWidth ? measuredWidth : requestedWidth;
  const lineHeightPx = text.fontSize * text.lineHeight;
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

export function renderTextLayer(layer) {
  if (!layer?.canvas || !layer?.ctx) return null;
  layer.kind = 'text';
  layer.text = normalizeTextData(layer.text);
  const base = baseTextCanvas(layer.text);
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
