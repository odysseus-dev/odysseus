// frontend/src/features/documents/ui/pdf/geometry.js
/**
 * Pure PDF coordinate conversion, scale, placement, drag, and resize math.
 */

import { _newAnnotationId } from './annotations.js';

export function clamp(val, min = 0, max = 100) {
  return Math.max(min, Math.min(max, val));
}

export function pxToPct(px, total) {
  if (!total) return 0;
  return (px / total) * 100;
}

export function pctToPx(pct, total) {
  if (!total) return 0;
  return (pct / 100) * total;
}

export function calculateFontSizeCqh(fontSize = 11) {
  return (fontSize * 1.5) / 11;
}

export function calculateFieldRectPct(rectPx = [0, 0, 0, 0], pageWidth = 1, pageHeight = 1) {
  const [x0, y0, x1, y1] = rectPx;
  const pw = pageWidth || 1;
  const ph = pageHeight || 1;
  return {
    left: pxToPct(x0, pw),
    top: pxToPct(y0, ph),
    width: pxToPct(x1 - x0, pw),
    height: pxToPct(y1 - y0, ph),
  };
}

export function calculateAnnotationPlacement({
  clientX = 0,
  clientY = 0,
  pageRect = { left: 0, top: 0, width: 1, height: 1 },
  mode = 'text',
  pageNumber = 1,
  lastLineHeight = 1.3,
  lastFontSize = 11,
} = {}) {
  const rect = pageRect || { left: 0, top: 0, width: 1, height: 1 };
  const xPct = pxToPct(clientX - rect.left, rect.width);
  const yPct = pxToPct(clientY - rect.top, rect.height);
  const sizes = {
    text: { w: 8, h: 2.5 },
    check: { w: 2.5, h: 2.5 },
    signature: { w: 22, h: 6 },
  };
  const size = sizes[mode] || sizes.text;
  const centered = mode === 'check';
  const x = clamp(centered ? xPct - size.w / 2 : xPct, 0, 100 - size.w);
  const y = clamp(centered ? yPct - size.h / 2 : yPct, 0, 100 - size.h);
  return {
    id: _newAnnotationId(),
    page: pageNumber,
    x,
    y,
    w: size.w,
    h: size.h,
    value: mode === 'check' ? '[ ]' : '',
    kind: mode,
    lineHeight: mode === 'text' ? lastLineHeight : undefined,
    fontSize: mode === 'text' ? lastFontSize : undefined,
  };
}

export function calculateDragMove(start, current, pageRect, widthPct, heightPct) {
  const pw = pageRect?.width || 1;
  const ph = pageRect?.height || 1;
  const dxPct = pxToPct(current.mx - start.mx, pw);
  const dyPct = pxToPct(current.my - start.my, ph);
  const w = widthPct || 0;
  const h = heightPct || 0;
  return {
    x: clamp(start.x + dxPct, 0, 100 - w),
    y: clamp(start.y + dyPct, 0, 100 - h),
  };
}

export function calculateResize(start, current, pageRect, leftPct, topPct) {
  const pw = pageRect?.width || 1;
  const ph = pageRect?.height || 1;
  const dwPct = pxToPct(current.mx - start.mx, pw);
  const dhPct = pxToPct(current.my - start.my, ph);
  const x = leftPct || 0;
  const y = topPct || 0;
  return {
    w: clamp(start.w + dwPct, 1, 100 - x),
    h: clamp(start.h + dhPct, 0.8, 100 - y),
  };
}
