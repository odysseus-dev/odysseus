// static/js/researchSynapse.js
//
// Live SVG visualization of a deep-research run: central query node with
// sub-question branches and source leaves that pop in as rounds progress.
// Driven imperatively by chat.js when SSE research_progress events arrive.

const SVG_NS = 'http://www.w3.org/2000/svg';

const PHASE_LABEL = {
  probing:   'verifying model',
  explaining:'building explanation',
  planning:  'planning strategy',
  searching: 'searching',
  reading:   'reading sources',
  analyzing: 'analyzing findings',
  writing:   'writing report',
  error:     'error',
  done:      'complete',
};

function rand(a, b) { return Math.random() * (b - a) + a; }
function pick(arr)  { return arr[Math.floor(Math.random() * arr.length)]; }

export default function createResearchSynapse(container, opts = {}) {
  const W = 520, H = 220;
  const cx = W / 2, cy = H / 2;

  const wrap = document.createElement('div');
  wrap.className = 'research-synapse research-synapse-ascii-filter' + (opts.compact ? ' research-synapse-compact' : '');
  wrap.innerHTML = `
    <div class="rs-stage">
      <div class="rs-meta rs-meta-header">
        <span class="rs-status">starting…</span>
        <span class="rs-sep">·</span>
        <span class="rs-round">round <b>0</b></span>
        <span class="rs-sep">·</span>
        <span class="rs-sources"><b>0</b> sources</span>
        <span class="rs-sep">·</span>
        <span class="rs-timer">00:00</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Live research map">
        <g class="rs-guide" aria-hidden="true">
          <ellipse cx="${cx}" cy="${cy}" rx="116" ry="72"></ellipse>
          <ellipse cx="${cx}" cy="${cy}" rx="58" ry="36"></ellipse>
        </g>
        <g class="rs-edges"></g>
        <g class="rs-nodes"></g>
        <circle class="rs-root-halo" cx="${cx}" cy="${cy}" r="18"></circle>
        <circle class="rs-pulse" cx="${cx}" cy="${cy}" r="6"></circle>
      </svg>
      <pre class="rs-ascii-overlay" aria-hidden="true"></pre>
    </div>
  `;
  container.appendChild(wrap);

  const svg     = wrap.querySelector('svg');
  const edgesG  = wrap.querySelector('.rs-edges');
  const nodesG  = wrap.querySelector('.rs-nodes');
  const statusE = wrap.querySelector('.rs-status');
  const phaseChipE = wrap.querySelector('.rs-phase-chip');
  const roundE  = wrap.querySelector('.rs-round b');
  const srcE    = wrap.querySelector('.rs-sources b');
  const timerE  = wrap.querySelector('.rs-timer');
  const asciiE  = wrap.querySelector('.rs-ascii-overlay');

  // ── root (query) ───────────────────────────────────────────────
  const root = document.createElementNS(SVG_NS, 'circle');
  root.setAttribute('cx', cx); root.setAttribute('cy', cy);
  root.setAttribute('r', 11);
  root.setAttribute('class', 'rs-node rs-node-root');
  nodesG.appendChild(root);
  const rootLabel = document.createElementNS(SVG_NS, 'text');
  rootLabel.setAttribute('x', cx);
  rootLabel.setAttribute('y', cy + 28);
  rootLabel.setAttribute('text-anchor', 'middle');
  rootLabel.setAttribute('class', 'rs-label');
  rootLabel.textContent = _trunc(opts.query || 'query', 28);
  nodesG.appendChild(rootLabel);

  const subs = []; // { x, y, count }
  let sourceCount = 0;
  let lastRound = 0;
  let completed = false;
  const pendingSourceTitles = [];
  const seenSourceTitles = new Set();

  const asciiCanvas = document.createElement('canvas');
  const asciiCtx = asciiCanvas.getContext('2d', { willReadFrequently: true });
  const asciiRamp = ' .,:;irsXA253hMHGS#9B&@';
  let asciiTick = 0;
  let asciiMode = 'network';
  function _renderAsciiOverlay() {
    if (!asciiE || !asciiCtx) return;
    const columns = 52, rows = 12, scale = 2;
    asciiCanvas.width = columns * scale; asciiCanvas.height = rows * scale;
    const width = asciiCanvas.width, height = asciiCanvas.height;
    asciiCtx.fillStyle = 'rgb(2, 8, 4)'; asciiCtx.fillRect(0, 0, width, height);
    asciiCtx.strokeStyle = 'rgb(145, 145, 145)'; asciiCtx.fillStyle = 'rgb(225, 225, 225)';
    asciiCtx.lineWidth = 1;
    const sx = value => value / 520 * width;
    const sy = value => value / 220 * height;
    const pulse = (Math.sin(asciiTick * 0.25) + 1) / 2;
    if (asciiMode === 'network') subs.forEach((sub, index) => {
      asciiCtx.beginPath(); asciiCtx.moveTo(sx(cx), sy(cy)); asciiCtx.lineTo(sx(sub.x), sy(sub.y)); asciiCtx.stroke();
      asciiCtx.beginPath(); asciiCtx.arc(sx(sub.x), sy(sub.y), 2 + pulse, 0, Math.PI * 2); asciiCtx.fill();
      // Small packets travel from the root to each round node. These are
      // deliberately separate from the node glow so the ASCII scene reads
      // as an active network rather than one pulsing center point.
      asciiCtx.fillStyle = 'rgb(255, 255, 255)';
      for (let packet = 0; packet < 2; packet++) {
        const progress = (asciiTick * 0.018 + index * 0.17 + packet * 0.5) % 1;
        const px = cx + (sub.x - cx) * progress;
        const py = cy + (sub.y - cy) * progress;
        asciiCtx.beginPath(); asciiCtx.arc(sx(px), sy(py), 1.4 + pulse * 0.8, 0, Math.PI * 2); asciiCtx.fill();
      }
      asciiCtx.fillStyle = 'rgb(225, 225, 225)';
      const leaves = Math.min(sub.count, 8);
      for (let i = 0; i < leaves; i++) {
        const angle = (i - (leaves - 1) / 2) * 0.38;
        const x = sub.x + Math.cos(angle) * (18 + (i % 2) * 4);
        const y = sub.y + Math.sin(angle) * (18 + (i % 2) * 4);
        asciiCtx.fillRect(sx(x), sy(y), 1.5, 1.5);
      }
      if (index >= 8) return;
    });
    if (asciiMode === 'orbit') {
      // Reading and analysis: sources orbit the question while packets move
      // around each evidence ring.
      const rings = [[105, 42], [155, 66], [205, 88]];
      rings.forEach(([rx, ry], ring) => {
        asciiCtx.strokeStyle = 'rgb(92, 92, 92)';
        asciiCtx.beginPath(); asciiCtx.ellipse(sx(cx), sy(cy), sx(rx), sy(ry), 0, 0, Math.PI * 2); asciiCtx.stroke();
        for (let i = 0; i < 5; i++) {
          const angle = asciiTick * (0.018 - ring * 0.002) * (ring % 2 ? -1 : 1) + i * Math.PI * 2 / 5;
          const x = cx + Math.cos(angle) * rx; const y = cy + Math.sin(angle) * ry;
          asciiCtx.fillStyle = 'rgb(245, 245, 245)'; asciiCtx.beginPath(); asciiCtx.arc(sx(x), sy(y), 1.5, 0, Math.PI * 2); asciiCtx.fill();
        }
      });
    }
    if (asciiMode === 'streams') {
      // Writing: independent evidence streams converge, then leave as a
      // single ordered report stream.
      const hub = [cx + 20, cy];
      for (let i = 0; i < 7; i++) {
        const start = [38, 18 + i * 6];
        asciiCtx.strokeStyle = 'rgb(55, 55, 55)'; asciiCtx.beginPath(); asciiCtx.moveTo(sx(start[0]), sy(start[1])); asciiCtx.lineTo(sx(hub[0]), sy(hub[1])); asciiCtx.stroke();
        for (let packet = 0; packet < 2; packet++) {
          const progress = (asciiTick * 0.015 + i * 0.13 + packet * 0.5) % 1;
          const x = start[0] + (hub[0] - start[0]) * progress;
          const y = start[1] + (hub[1] - start[1]) * progress;
          asciiCtx.fillStyle = 'rgb(255, 255, 255)'; asciiCtx.fillRect(sx(x), sy(y), 1.8, 1.8);
        }
      }
      asciiCtx.fillStyle = 'rgb(240, 240, 240)'; asciiCtx.beginPath(); asciiCtx.arc(sx(hub[0]), sy(hub[1]), 4 + pulse * 2, 0, Math.PI * 2); asciiCtx.fill();
      for (let i = 0; i < 4; i++) {
        const y = cy - 9 + i * 6; asciiCtx.strokeStyle = 'rgb(160, 160, 160)'; asciiCtx.beginPath(); asciiCtx.moveTo(sx(hub[0] + 10), sy(y)); asciiCtx.lineTo(sx(500 - i * 6), sy(y)); asciiCtx.stroke();
      }
    }
    asciiCtx.beginPath(); asciiCtx.arc(sx(cx), sy(cy), 4 + pulse * 2, 0, Math.PI * 2); asciiCtx.fill();
    const pixels = asciiCtx.getImageData(0, 0, width, height).data;
    const lines = [];
    for (let y = 0; y < rows; y++) {
      let line = '';
      for (let x = 0; x < columns; x++) {
        let luminosity = 0;
        for (let py = 0; py < scale; py++) for (let px = 0; px < scale; px++) {
          luminosity += pixels[((y * scale + py) * width + x * scale + px) * 4];
        }
        luminosity /= scale * scale;
        line += asciiRamp[Math.min(asciiRamp.length - 1, Math.floor(luminosity / 256 * asciiRamp.length))];
      }
      lines.push(line);
    }
    asciiE.textContent = lines.join('\n');
    asciiTick++;
  }

  // ── timer ──────────────────────────────────────────────────────
  const startedAt = opts.startedAt || Date.now();
  const updateTimer = () => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    timerE.textContent =
      String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' +
      String(elapsed % 60).padStart(2, '0');
  };
  updateTimer();
  _renderAsciiOverlay();
  let timerInterval = setInterval(updateTimer, 1000);
  let asciiInterval = setInterval(() => { if (!completed) _renderAsciiOverlay(); }, 66);

  // ── helpers ────────────────────────────────────────────────────
  function _trunc(s, n) {
    if (!s) return '';
    s = String(s).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function _addSub(label) {
    if (subs.length >= 10) return; // cap visual clutter
    // Fixed, alternating slots keep early rounds balanced instead of making
    // every new branch crowd the right side while the run is in progress.
    const angles = [-90, 90, 180, 0, -135, 45, 135, -45, -112, 68];
    const slot = subs.length;
    const angle = angles[slot] * Math.PI / 180;
    const rx = slot < 4 ? 92 : 112;
    const ry = slot < 4 ? 58 : 72;
    const x = cx + Math.cos(angle) * rx;
    const y = cy + Math.sin(angle) * ry;
    const tone = slot % 3;

    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', cx); edge.setAttribute('y1', cy);
    edge.setAttribute('x2', x);  edge.setAttribute('y2', y);
    edge.setAttribute('class', `rs-edge rs-edge-tone-${tone} rs-edge-firing`);
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const n = document.createElementNS(SVG_NS, 'circle');
    n.setAttribute('cx', x); n.setAttribute('cy', y); n.setAttribute('r', 7);
    n.setAttribute('class', `rs-node rs-node-sub rs-node-tone-${tone} rs-node-new`);
    nodesG.appendChild(n);

    if (label) {
      const t = document.createElementNS(SVG_NS, 'text');
      // Position label outside the circle on the same angle
      const lx = x + Math.cos(angle) * 13;
      const ly = y + Math.sin(angle) * 11;
      t.setAttribute('x', lx); t.setAttribute('y', ly + 3);
      t.setAttribute('text-anchor', Math.cos(angle) > 0.15 ? 'start' :
                                    Math.cos(angle) < -0.15 ? 'end' : 'middle');
      t.setAttribute('class', 'rs-label rs-label-sub');
      t.textContent = _trunc(label, 14);
      nodesG.appendChild(t);
    }

    subs.push({ x, y, count: 0, tone, node: n });
  }

  function _rememberSource(title, url = '') {
    const clean = String(title || '').replace(/\s+/g, ' ').trim();
    const key = String(url || clean).trim();
    if (!clean || !key || seenSourceTitles.has(key)) return;
    seenSourceTitles.add(key);
    pendingSourceTitles.push(clean);
  }

  function _addLeaf(label, sourceIndex) {
    if (!subs.length) _addSub('');
    // Always attach the new source to the CURRENT round's sub (i.e. the
    // most-recently-added one). That gives a clean per-round attribution
    // — 10 sources across 3 rounds ends up as 10/10/10 across the three
    // sub-nodes, not a random scatter.
    const sub = subs[subs.length - 1];
    sub.count++;
    if (sub.count === 1 && sub.node) sub.node.classList.add('rs-node-expanded');
    // Lay leaves out in concentric arcs around the sub: 6 per ring fanned
    // across ~140°, then a second ring further out for the next 6, etc.
    // Keeps things readable past 10+ leaves per sub.
    const baseAngle = Math.atan2(sub.y - cy, sub.x - cx);
    const idx = sub.count - 1;
    const perRing = 6;
    const ring = Math.floor(idx / perRing);
    const slot = idx % perRing;
    const arcSpan = 2.4;
    const angle = baseAngle + (slot - (perRing - 1) / 2) * (arcSpan / perRing) + rand(-0.05, 0.05);
    const r = 26 + ring * 14 + rand(-1.5, 1.5);
    const lx = sub.x + Math.cos(angle) * r;
    const ly = sub.y + Math.sin(angle) * r;

    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', sub.x); edge.setAttribute('y1', sub.y);
    edge.setAttribute('x2', lx);    edge.setAttribute('y2', ly);
    edge.setAttribute('class', `rs-edge rs-edge-tone-${sub.tone} rs-edge-firing`);
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const leaf = document.createElementNS(SVG_NS, 'circle');
    leaf.setAttribute('cx', lx); leaf.setAttribute('cy', ly);
    leaf.setAttribute('r', 5.5);
    leaf.setAttribute('class', `rs-node rs-node-leaf rs-source-node rs-node-tone-${sub.tone} rs-node-new`);
    leaf.setAttribute('aria-label', label || `Source ${sourceIndex}`);
    const title = document.createElementNS(SVG_NS, 'title');
    title.textContent = label || `Source ${sourceIndex}`;
    leaf.appendChild(title);
    nodesG.appendChild(leaf);

    const sourceLabel = document.createElementNS(SVG_NS, 'text');
    sourceLabel.setAttribute('x', lx);
    sourceLabel.setAttribute('y', ly + 2);
    sourceLabel.setAttribute('text-anchor', 'middle');
    sourceLabel.setAttribute('class', 'rs-source-index');
    sourceLabel.textContent = sourceIndex > 99 ? '·' : String(sourceIndex);
    nodesG.appendChild(sourceLabel);
  }

  // ── public API ─────────────────────────────────────────────────
  return {
    element: wrap,

    /** Reflect a phase change in the status text + side effects. */
    setPhase(phase, extra = {}) {
      if (completed) return;
      const label = PHASE_LABEL[phase] || phase || '';
      let txt = label;
      if (phase === 'searching' && extra.queries) txt += ` · ${extra.queries} queries`;
      else if (phase === 'reading' && extra.title) {
        txt = `reading: ${_trunc(extra.title, 32)}`;
        _rememberSource(extra.title, extra.url);
      }
      else if (phase === 'analyzing' && extra.total_findings) txt += ` · ${extra.total_findings} findings`;
      statusE.textContent = txt;
      if (phaseChipE) phaseChipE.textContent = label || 'working';
      Array.from(wrap.classList)
        .filter(name => name.startsWith('rs-phase-'))
        .forEach(name => wrap.classList.remove(name));
      if (phase) wrap.classList.add(`rs-phase-${phase}`);
      asciiMode = ['reading', 'analyzing'].includes(phase)
        ? 'orbit'
        : ['writing', 'done'].includes(phase) ? 'streams' : 'network';
      _renderAsciiOverlay();
      _renderAsciiOverlay();
      // Visual cue per phase
      if (phase === 'error') wrap.classList.add('rs-error');
    },

    /** Bump the round counter — adds a sub-question node when round grows. */
    setRound(round, opts = {}) {
      if (completed) return;
      if (typeof round !== 'number' || round < 1) return;
      if (round > lastRound) {
        // Add one sub-question node per new round we see
        for (let i = lastRound; i < round && subs.length < 10; i++) {
          _addSub(opts.label || `Round ${i + 1}`);
        }
        lastRound = round;
        roundE.textContent = round;
        _renderAsciiOverlay();
      }
    },

    /** Update the total source count — one outer node per source, under the current round. */
    setSourceCount(total) {
      if (completed) return;
      if (typeof total !== 'number' || total <= sourceCount) return;
      const previousTotal = sourceCount;
      const delta = total - previousTotal;
      for (let i = 0; i < delta; i++) {
        const sourceIndex = previousTotal + i + 1;
        const sourceTitle = pendingSourceTitles.shift() || `Source ${sourceIndex}`;
        // Keep all source nodes accurate while using a short capped stagger so
        // reconnecting a large run does not spend seconds replaying animation.
        setTimeout(() => _addLeaf(sourceTitle, sourceIndex), Math.min(i, 10) * 70);
      }
      sourceCount = total;
      srcE.textContent = total;
      _renderAsciiOverlay();
    },

    /** Mark the run as done — freezes the pulse and tints the graph green. */
    complete() {
      if (completed) return;
      completed = true;
      wrap.classList.add('rs-complete');
      statusE.textContent = 'complete';
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (asciiInterval) { clearInterval(asciiInterval); asciiInterval = null; }
    },

    destroy() {
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (asciiInterval) { clearInterval(asciiInterval); asciiInterval = null; }
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    },
  };
}
