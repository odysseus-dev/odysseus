import { state } from './state.js';

export function ensureRembgSampleCanvas() {
  const w = Math.max(1, state.imgWidth || state.mainCanvas?.width || 0);
  const h = Math.max(1, state.imgHeight || state.mainCanvas?.height || 0);
  if (
    !state.rembgSampleCanvas ||
    state.rembgSampleCanvas.width !== w ||
    state.rembgSampleCanvas.height !== h
  ) {
    const prev = state.rembgSampleCanvas;
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    const ctx = c.getContext('2d');
    if (prev && prev.width && prev.height) {
      ctx.drawImage(prev, 0, 0, prev.width, prev.height, 0, 0, w, h);
    }
    state.rembgSampleCanvas = c;
    state.rembgSampleCtx = ctx;
  }
  return state.rembgSampleCanvas;
}

export function hasRembgSampleMask() {
  const c = state.rembgSampleCanvas ? ensureRembgSampleCanvas() : null;
  if (!c || !c.width || !c.height) return false;
  try {
    const data = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 8) return true;
    }
  } catch {}
  return false;
}

export function clearRembgSampleMask() {
  const c = state.rembgSampleCanvas;
  if (!c || !c.width || !c.height) return;
  c.getContext('2d').clearRect(0, 0, c.width, c.height);
}

export function rembgSampleMaskToBase64() {
  const c = state.rembgSampleCanvas;
  if (!hasRembgSampleMask() || !c) return null;
  return c.toDataURL('image/png').split(',')[1];
}
