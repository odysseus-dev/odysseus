/** Retained vector-shape model and raster-cache renderer. */

import { normalizeGradientStops } from './gradient-stops.js';

export { normalizeGradientStops } from './gradient-stops.js';

export const DEFAULT_SHAPE_DATA = Object.freeze({
  type: 'rectangle',
  width: 180,
  height: 120,
  fillColor: '#ffffff',
  fillType: 'solid',
  gradientStart: '#ffffff',
  gradientMid: '#808080',
  gradientMidEnabled: false,
  gradientMidPosition: 50,
  gradientEnd: '#000000',
  gradientAngle: 0,
  strokeColor: '#111111',
  strokeWidth: 2,
  cornerRadius: 0,
  sides: 5,
  transform: Object.freeze({ scaleX: 1, scaleY: 1, rotation: 0, flipH: false, flipV: false }),
});

function finite(value, fallback, min = -Infinity, max = Infinity) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback;
}

export function normalizeShapeData(value = {}) {
  const transform = value.transform || {};
  const gradientStart = String(value.gradientStart || DEFAULT_SHAPE_DATA.gradientStart);
  const gradientMid = String(value.gradientMid || DEFAULT_SHAPE_DATA.gradientMid);
  const gradientMidEnabled = !!value.gradientMidEnabled;
  const gradientMidPosition = finite(value.gradientMidPosition, DEFAULT_SHAPE_DATA.gradientMidPosition, 1, 99);
  const gradientEnd = String(value.gradientEnd || DEFAULT_SHAPE_DATA.gradientEnd);
  return {
    type: ['rectangle', 'ellipse', 'line', 'polygon'].includes(value.type) ? value.type : 'rectangle',
    width: finite(value.width, DEFAULT_SHAPE_DATA.width, 1, 10000),
    height: finite(value.height, DEFAULT_SHAPE_DATA.height, 1, 10000),
    fillColor: String(value.fillColor || DEFAULT_SHAPE_DATA.fillColor),
    fillType: value.fillType === 'linear-gradient' ? 'linear-gradient' : DEFAULT_SHAPE_DATA.fillType,
    gradientStart,
    gradientMid,
    gradientMidEnabled,
    gradientMidPosition,
    gradientEnd,
    gradientStops: normalizeGradientStops(value.gradientStops, {
      start: gradientStart,
      mid: gradientMid,
      midEnabled: gradientMidEnabled,
      midPosition: gradientMidPosition,
      end: gradientEnd,
    }),
    gradientAngle: finite(value.gradientAngle, DEFAULT_SHAPE_DATA.gradientAngle, -36000, 36000),
    strokeColor: String(value.strokeColor || DEFAULT_SHAPE_DATA.strokeColor),
    strokeWidth: finite(value.strokeWidth, DEFAULT_SHAPE_DATA.strokeWidth, 0, 500),
    cornerRadius: finite(value.cornerRadius, DEFAULT_SHAPE_DATA.cornerRadius, 0, 5000),
    sides: Math.round(finite(value.sides, DEFAULT_SHAPE_DATA.sides, 3, 24)),
    transform: {
      scaleX: finite(transform.scaleX, 1, 0.01, 100),
      scaleY: finite(transform.scaleY, 1, 0.01, 100),
      rotation: finite(transform.rotation, 0, -36000, 36000),
      flipH: !!transform.flipH,
      flipV: !!transform.flipV,
    },
  };
}

function shapePath(ctx, shape, padding) {
  const x = padding;
  const y = padding;
  const width = shape.width;
  const height = shape.height;
  ctx.beginPath();
  if (shape.type === 'ellipse') {
    ctx.ellipse(x + width / 2, y + height / 2, width / 2, height / 2, 0, 0, Math.PI * 2);
  } else if (shape.type === 'line') {
    ctx.moveTo(x, y + height);
    ctx.lineTo(x + width, y);
  } else if (shape.type === 'polygon') {
    const cx = x + width / 2;
    const cy = y + height / 2;
    for (let index = 0; index < shape.sides; index += 1) {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / shape.sides;
      const px = cx + Math.cos(angle) * width / 2;
      const py = cy + Math.sin(angle) * height / 2;
      if (!index) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
  } else {
    const radius = Math.min(shape.cornerRadius, width / 2, height / 2);
    if (ctx.roundRect) ctx.roundRect(x, y, width, height, radius);
    else ctx.rect(x, y, width, height);
  }
}

function baseShapeCanvas(shape) {
  const padding = Math.ceil(Math.max(2, shape.strokeWidth / 2 + 2));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.ceil(shape.width + padding * 2));
  canvas.height = Math.max(1, Math.ceil(shape.height + padding * 2));
  const ctx = canvas.getContext('2d');
  shapePath(ctx, shape, padding);
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  if (shape.type !== 'line' && shape.fillColor !== 'transparent') {
    if (shape.fillType === 'linear-gradient' && typeof ctx.createLinearGradient === 'function') {
      const radians = shape.gradientAngle * Math.PI / 180;
      const dx = Math.cos(radians) * shape.width / 2;
      const dy = Math.sin(radians) * shape.height / 2;
      const gradient = ctx.createLinearGradient(
        padding + shape.width / 2 - dx,
        padding + shape.height / 2 - dy,
        padding + shape.width / 2 + dx,
        padding + shape.height / 2 + dy,
      );
      for (const stop of shape.gradientStops) {
        gradient.addColorStop(stop.position / 100, stop.color);
      }
      ctx.fillStyle = gradient;
    } else {
      ctx.fillStyle = shape.fillColor;
    }
    ctx.fill();
  }
  if (shape.strokeWidth > 0) {
    ctx.strokeStyle = shape.strokeColor;
    ctx.lineWidth = shape.strokeWidth;
    ctx.stroke();
  }
  return canvas;
}

export function renderShapeLayer(layer) {
  if (!layer?.canvas || !layer?.ctx) return null;
  layer.kind = 'shape';
  layer.shape = normalizeShapeData(layer.shape);
  const base = baseShapeCanvas(layer.shape);
  const transform = layer.shape.transform;
  const scaledWidth = Math.max(1, base.width * transform.scaleX);
  const scaledHeight = Math.max(1, base.height * transform.scaleY);
  const radians = transform.rotation * Math.PI / 180;
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

export function rasterizeShapeLayer(layer) {
  if (!layer || layer.kind !== 'shape') return false;
  layer.kind = 'raster';
  layer.shape = null;
  return true;
}

export function scaleShapeLayer(layer, scaleX, scaleY) {
  layer.shape = normalizeShapeData(layer.shape);
  layer.shape.transform.scaleX *= scaleX;
  layer.shape.transform.scaleY *= scaleY;
  return renderShapeLayer(layer);
}

export function rotateShapeLayer(layer, degrees) {
  layer.shape = normalizeShapeData(layer.shape);
  layer.shape.transform.rotation += degrees;
  return renderShapeLayer(layer);
}

export function flipShapeLayer(layer, axis) {
  layer.shape = normalizeShapeData(layer.shape);
  if (axis === 'h') layer.shape.transform.flipH = !layer.shape.transform.flipH;
  else layer.shape.transform.flipV = !layer.shape.transform.flipV;
  return renderShapeLayer(layer);
}
