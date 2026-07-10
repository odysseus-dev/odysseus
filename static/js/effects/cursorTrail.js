// Cursor sparkle trail — lightweight visual flourish for fine-pointer devices.

const STORAGE_KEY = 'odysseus-cursor-trail';
const coarsePointer = window.matchMedia?.('(pointer: coarse)') || null;
const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)') || null;

let enabled = readPreference();
let canvas = null;
let ctx = null;
let frameId = 0;
let width = 0;
let height = 0;
let dpr = 1;
let lastX = -1000;
let lastY = -1000;
const particles = [];
const maxParticles = 30;

function readPreference() {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'off';
  } catch (_) {
    return true;
  }
}

function shouldRun() {
  return enabled && !coarsePointer?.matches && !reducedMotion?.matches;
}

function getAccentColor() {
  try {
    const styles = getComputedStyle(document.documentElement);
    return styles.getPropertyValue('--accent').trim()
      || styles.getPropertyValue('--red').trim()
      || '#e06c75';
  } catch (_) {
    return '#e06c75';
  }
}

function hexToHsl(hex) {
  let r = 0;
  let g = 0;
  let b = 0;
  if (hex.length === 4) {
    r = parseInt(hex[1] + hex[1], 16) / 255;
    g = parseInt(hex[2] + hex[2], 16) / 255;
    b = parseInt(hex[3] + hex[3], 16) / 255;
  } else if (hex.length === 7) {
    r = parseInt(hex.slice(1, 3), 16) / 255;
    g = parseInt(hex.slice(3, 5), 16) / 255;
    b = parseInt(hex.slice(5, 7), 16) / 255;
  }

  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let h = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const delta = max - min;
    switch (max) {
      case r:
        h = ((g - b) / delta + (g < b ? 6 : 0)) / 6;
        break;
      case g:
        h = ((b - r) / delta + 2) / 6;
        break;
      default:
        h = ((r - g) / delta + 4) / 6;
        break;
    }
  }
  return h * 360;
}

function resizeCanvas() {
  if (!canvas || !ctx) return;
  width = window.innerWidth;
  height = window.innerHeight;
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function handleMouseMove(event) {
  const x = event.clientX;
  const y = event.clientY;
  const dx = x - lastX;
  const dy = y - lastY;
  if (dx * dx + dy * dy < 25) return;
  lastX = x;
  lastY = y;

  const hue = hexToHsl(getAccentColor());
  particles.push({
    x,
    y,
    vx: (Math.random() - 0.5) * 1.5,
    vy: (Math.random() - 0.5) * 1.5 - 0.5,
    life: 1,
    decay: 0.02 + Math.random() * 0.02,
    size: 2 + Math.random() * 3,
    hue: hue + (Math.random() - 0.5) * 40,
  });
  if (particles.length > maxParticles) particles.shift();
  if (!frameId) frameId = requestAnimationFrame(draw);
}

function draw() {
  if (!canvas?.isConnected || !ctx || !shouldRun()) {
    stopCursorTrail();
    return;
  }

  ctx.clearRect(0, 0, width, height);
  for (let i = particles.length - 1; i >= 0; i--) {
    const particle = particles[i];
    particle.x += particle.vx;
    particle.y += particle.vy;
    particle.vy += 0.05;
    particle.life -= particle.decay;
    if (particle.life <= 0) {
      particles.splice(i, 1);
      continue;
    }

    const alpha = particle.life * 0.7;
    const gradient = ctx.createRadialGradient(
      particle.x,
      particle.y,
      0,
      particle.x,
      particle.y,
      particle.size * 2,
    );
    gradient.addColorStop(0, `hsla(${particle.hue}, 80%, 70%, ${alpha})`);
    gradient.addColorStop(1, `hsla(${particle.hue}, 80%, 70%, 0)`);
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(particle.x, particle.y, particle.size * 2, 0, Math.PI * 2);
    ctx.fill();
  }
  frameId = particles.length ? requestAnimationFrame(draw) : 0;
}

function startCursorTrail() {
  if (!shouldRun() || canvas?.isConnected || !document.body) return;

  canvas = document.createElement('canvas');
  canvas.id = 'cursor-trail-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:1;';
  document.body.appendChild(canvas);
  ctx = canvas.getContext('2d', { alpha: true, desynchronized: true }) || canvas.getContext('2d');
  if (!ctx) {
    canvas.remove();
    canvas = null;
    return;
  }

  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);
  document.addEventListener('mousemove', handleMouseMove);
}

function stopCursorTrail() {
  if (frameId) cancelAnimationFrame(frameId);
  frameId = 0;
  window.removeEventListener('resize', resizeCanvas);
  document.removeEventListener('mousemove', handleMouseMove);
  canvas?.remove();
  canvas = null;
  ctx = null;
  particles.length = 0;
  lastX = -1000;
  lastY = -1000;
}

export function setCursorTrailEnabled(nextEnabled) {
  enabled = !!nextEnabled;
  if (shouldRun()) startCursorTrail();
  else stopCursorTrail();
}

export function syncCursorTrailPreference() {
  enabled = readPreference();
  setCursorTrailEnabled(enabled);
}

const handleCapabilityChange = () => setCursorTrailEnabled(enabled);
coarsePointer?.addEventListener?.('change', handleCapabilityChange);
reducedMotion?.addEventListener?.('change', handleCapabilityChange);

if (document.body) syncCursorTrailPreference();
else document.addEventListener('DOMContentLoaded', syncCursorTrailPreference, { once: true });
