/* NOMAD Orbital UI — ambient ASCII instrument layer.
 * Decorative only: the existing application remains the source of truth.
 */

const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');
const GLYPHS = ' .·:+*#%@';
const TELEMETRY = [
  'LOCAL FIRST', 'PRIVATE BY DESIGN', 'MODEL ONLINE', 'TOOLS ARMED',
  'MEMORY SYNC', 'CONTEXT OPEN', 'VOYAGE READY', 'SIGNAL CLEAN',
];

function buildAtmosphere() {
  if (document.querySelector('.orbital-atmosphere')) return;

  const atmosphere = document.createElement('div');
  atmosphere.className = 'orbital-atmosphere';
  atmosphere.setAttribute('aria-hidden', 'true');
  atmosphere.innerHTML = `
    <canvas id="orbital-ascii-stage"></canvas>
    <div class="orbital-blob orbital-blob-a"></div>
    <div class="orbital-blob orbital-blob-b"></div>
    <div class="orbital-blob orbital-blob-c"></div>
    <div class="orbital-grain"></div>
    <div class="orbital-scanline"></div>
    <div class="orbital-hud orbital-hud-nw"><b>ODY/SYS</b><span>LOCAL INTELLIGENCE</span></div>
    <div class="orbital-hud orbital-hud-ne"><b>01—∞</b><span>PRIVATE ORBIT</span></div>
    <div class="orbital-hud orbital-hud-sw"><i></i><span>SYSTEM NOMINAL</span></div>
    <div class="orbital-hud orbital-hud-se"><b id="orbital-clock">00:00:00</b><span>SHIP TIME</span></div>
    <div class="orbital-marquee"><div>${[...TELEMETRY, ...TELEMETRY].map((item, i) => `<span>${String((i % TELEMETRY.length) + 1).padStart(2, '0')} / ${item}</span>`).join('')}</div></div>
  `;
  document.body.prepend(atmosphere);

  const brand = document.querySelector('.sidebar-brand-title');
  if (brand && !brand.dataset.orbitalLabel) {
    brand.dataset.orbitalLabel = 'ORBITAL INTELLIGENCE';
  }

  const welcome = document.querySelector('#welcome-screen .welcome-name');
  if (welcome && !welcome.dataset.orbitalLabel) {
    welcome.dataset.orbitalLabel = 'LOCAL / PRIVATE / UNBOUNDED';
  }

  const textarea = document.querySelector('#message');
  if (textarea) textarea.placeholder = 'Transmit to NOMAD…';

  const currentMeta = document.querySelector('#current-meta');
  if (currentMeta?.textContent === 'Odysseus Chat') currentMeta.textContent = 'NOMAD Chat';
}

function startClock() {
  const clock = document.getElementById('orbital-clock');
  if (!clock) return;
  const tick = () => {
    clock.textContent = new Intl.DateTimeFormat([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(new Date());
  };
  tick();
  window.setInterval(tick, 1000);
}

function createRenderer() {
  const canvas = document.getElementById('orbital-ascii-stage');
  if (!canvas) return null;
  const context = canvas.getContext('2d', { alpha: true });
  if (!context) return null;

  let width = 0;
  let height = 0;
  let dpr = 1;
  let frame = 0;
  let last = 0;
  let raf = 0;
  const stars = Array.from({ length: 54 }, (_, index) => ({
    x: ((index * 47) % 101) / 100,
    y: ((index * 83) % 97) / 96,
    speed: 0.00003 + (index % 7) * 0.000009,
    glyph: index % 4 === 0 ? '+' : index % 3 === 0 ? '·' : '.',
  }));

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawStars(time) {
    context.font = '10px "Fira Code", monospace';
    context.fillStyle = 'rgba(255,255,255,.25)';
    for (const star of stars) {
      const y = (star.y * height + time * star.speed * height) % height;
      context.fillText(star.glyph, star.x * width, y);
    }
  }

  function drawOrb(time) {
    const cx = width * (width > 900 ? 0.72 : 0.66);
    const cy = height * 0.38;
    const scale = Math.min(width, height) * (width > 900 ? 0.24 : 0.18);
    const ax = time * 0.00017;
    const ay = time * 0.00011;
    const points = [];

    for (let lat = -10; lat <= 10; lat += 1) {
      const phi = (lat / 10) * Math.PI / 2;
      for (let lon = 0; lon < 38; lon += 1) {
        const theta = (lon / 38) * Math.PI * 2;
        let x = Math.cos(phi) * Math.cos(theta);
        let y = Math.sin(phi);
        let z = Math.cos(phi) * Math.sin(theta);

        const x1 = x * Math.cos(ay) - z * Math.sin(ay);
        const z1 = x * Math.sin(ay) + z * Math.cos(ay);
        const y1 = y * Math.cos(ax) - z1 * Math.sin(ax);
        const z2 = y * Math.sin(ax) + z1 * Math.cos(ax);
        const perspective = 1.6 / (2.6 - z2 * 0.52);
        const light = Math.max(0, (x1 * -0.35 + y1 * -0.55 + z2 * 0.75 + 1) / 2);
        points.push({
          x: cx + x1 * scale * perspective,
          y: cy + y1 * scale * perspective * 0.82,
          z: z2,
          light,
        });
      }
    }

    points.sort((a, b) => a.z - b.z);
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.font = `${Math.max(8, Math.round(scale / 18))}px "Fira Code", monospace`;
    for (const point of points) {
      const glyph = GLYPHS[Math.min(GLYPHS.length - 1, Math.floor(point.light * GLYPHS.length))];
      const alpha = 0.05 + point.light * 0.42;
      context.fillStyle = `rgba(255,255,255,${alpha})`;
      context.fillText(glyph, point.x, point.y);
    }
    context.textAlign = 'left';
    context.textBaseline = 'alphabetic';
  }

  function draw(time) {
    if (time - last < 32) {
      raf = window.requestAnimationFrame(draw);
      return;
    }
    last = time;
    frame += 1;
    context.clearRect(0, 0, width, height);
    drawStars(time);
    drawOrb(time);
    raf = window.requestAnimationFrame(draw);
  }

  function start() {
    window.cancelAnimationFrame(raf);
    resize();
    if (REDUCED_MOTION.matches) {
      context.clearRect(0, 0, width, height);
      drawStars(0);
      drawOrb(0);
      return;
    }
    raf = window.requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize, { passive: true });
  REDUCED_MOTION.addEventListener('change', start);
  return { start };
}

function boot() {
  buildAtmosphere();
  startClock();
  createRenderer()?.start();
  document.documentElement.classList.add('orbital-ui-ready');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
