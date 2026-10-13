/* Worker implementation for retained-effect rasterization. */
import { LAYER_STYLES, renderLayerStyle, renderDropShadow } from './layer-styles.js';

function copyCanvas(source) {
  const out = new OffscreenCanvas(source.width, source.height);
  out.getContext('2d').drawImage(source, 0, 0);
  return out;
}

function sharpenCanvas(source, amount) {
  if (!amount) return copyCanvas(source);
  const out = new OffscreenCanvas(source.width, source.height);
  const src = source.getContext('2d').getImageData(0, 0, source.width, source.height);
  const dst = out.getContext('2d').createImageData(source.width, source.height);
  const a = Math.max(0, Math.min(1, Number(amount) || 0));
  const stride = source.width * 4;
  for (let y = 0; y < source.height; y += 1) {
    for (let x = 0; x < source.width; x += 1) {
      const at = (y * source.width + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        const center = src.data[at + channel] * (1 + 4 * a);
        const left = src.data[y * stride + Math.max(0, x - 1) * 4 + channel];
        const right = src.data[y * stride + Math.min(source.width - 1, x + 1) * 4 + channel];
        const above = src.data[Math.max(0, y - 1) * stride + x * 4 + channel];
        const below = src.data[Math.min(source.height - 1, y + 1) * stride + x * 4 + channel];
        dst.data[at + channel] = Math.max(0, Math.min(255, center - a * (left + right + above + below)));
      }
      dst.data[at + 3] = src.data[at + 3];
    }
  }
  out.getContext('2d').putImageData(dst, 0, 0);
  return out;
}

function maskedContribution(effectCanvas, mask) {
  if (!mask) return effectCanvas;
  const out = new OffscreenCanvas(effectCanvas.width, effectCanvas.height);
  const ctx = out.getContext('2d');
  ctx.drawImage(effectCanvas, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  ctx.drawImage(mask, 0, 0, out.width, out.height);
  ctx.globalCompositeOperation = 'source-over';
  return out;
}

function gradientColor(value, alpha = 1) {
  const raw = String(value || '').replace(/^#/, '');
  if (!/^[0-9a-f]{6}$/i.test(raw)) return `rgba(0,0,0,${alpha})`;
  const channels = [0, 2, 4].map(offset => parseInt(raw.slice(offset, offset + 2), 16));
  return `rgba(${channels.join(',')},${alpha})`;
}

function renderEffects(source, effects, masks) {
  let current = copyCanvas(source);
  for (let index = 0; index < effects.length; index += 1) {
    const effect = effects[index];
    if (effect.visible === false || effect.opacity <= 0) continue;
    const next = new OffscreenCanvas(current.width, current.height);
    const ctx = next.getContext('2d');
    if (LAYER_STYLES[effect.type]) {
      ctx.drawImage(renderLayerStyle(current, effect.type, effect.params), 0, 0);
    } else if (effect.type === 'gaussian-blur') {
      ctx.filter = `blur(${effect.params.radius}px)`;
      ctx.drawImage(current, 0, 0);
      ctx.filter = 'none';
    } else if (effect.type === 'sharpen') {
      ctx.drawImage(sharpenCanvas(current, effect.params.amount), 0, 0);
    } else if (effect.type === 'color-overlay') {
      ctx.drawImage(current, 0, 0);
      ctx.globalAlpha = effect.params.opacity;
      ctx.globalCompositeOperation = effect.params.blendMode;
      ctx.fillStyle = effect.params.color;
      ctx.fillRect(0, 0, next.width, next.height);
    } else if (effect.type === 'drop-shadow') {
      ctx.drawImage(renderDropShadow(current, effect.params), 0, 0);
    } else if (effect.type === 'stroke') {
      ctx.globalAlpha = effect.params.opacity;
      ctx.shadowColor = effect.params.color;
      ctx.shadowBlur = effect.params.width;
      ctx.drawImage(current, 0, 0);
    } else if (effect.type === 'linear-gradient' || effect.type === 'radial-gradient') {
      const p = effect.params;
      const gradient = effect.type === 'radial-gradient'
        ? ctx.createRadialGradient(p.x1, p.y1, 0, p.x1, p.y1, Math.max(0.5, Math.hypot(p.x2 - p.x1, p.y2 - p.y1)))
        : ctx.createLinearGradient(p.x1, p.y1, p.x2, p.y2);
      for (const stop of p.stops || []) gradient.addColorStop(stop.position / 100, gradientColor(stop.color, stop.alpha));
      ctx.globalAlpha = p.opacity;
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, next.width, next.height);
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
      ctx.drawImage(current, 0, 0);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    const contribution = maskedContribution(next, masks[index]);
    const blended = copyCanvas(current);
    const blendCtx = blended.getContext('2d');
    if (effect.type === 'drop-shadow') blendCtx.globalCompositeOperation = 'destination-over';
    blendCtx.globalAlpha = effect.opacity;
    blendCtx.drawImage(contribution, 0, 0);
    blendCtx.globalAlpha = 1;
    current = blended;
  }
  return current;
}

self.onmessage = event => {
  try {
    const { source, effects, masks } = event.data;
    const output = renderEffects(source, effects || [], masks || []);
    const bitmap = output.transferToImageBitmap();
    self.postMessage({ bitmap }, [bitmap]);
  } catch (error) {
    self.postMessage({ error: String(error?.message || error) });
  }
};
