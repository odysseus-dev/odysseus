// static/js/researchSynapse.js
//
// Live SVG visualization of a deep-research run: central query node with
// sub-question branches and source leaves that pop in as rounds progress.
// Driven imperatively by chat.js when SSE research_progress events arrive.

const SVG_NS = 'http://www.w3.org/2000/svg';

const PHASE_LABEL = {
  probing:   'verifying model',
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
  wrap.className = 'research-synapse' + (opts.compact ? ' research-synapse-compact' : '');
  wrap.innerHTML = `
    <div class="rs-stage">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        <g class="rs-edges"></g>
        <g class="rs-nodes"></g>
        <circle class="rs-pulse" cx="${cx}" cy="${cy}" r="6"></circle>
      </svg>
    </div>
    <div class="rs-meta">
      <span class="rs-status">starting…</span>
      <span class="rs-sep">·</span>
      <span class="rs-round">round <b>0</b></span>
      <span class="rs-sep">·</span>
      <span class="rs-sources"><b>0</b> sources</span>
      <span class="rs-sep">·</span>
      <span class="rs-timer">00:00</span>
    </div>
  `;
  container.appendChild(wrap);

  const svg     = wrap.querySelector('svg');
  const edgesG  = wrap.querySelector('.rs-edges');
  const nodesG  = wrap.querySelector('.rs-nodes');
  const statusE = wrap.querySelector('.rs-status');
  const roundE  = wrap.querySelector('.rs-round b');
  const srcE    = wrap.querySelector('.rs-sources b');
  const timerE  = wrap.querySelector('.rs-timer');

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

  // ── timer ──────────────────────────────────────────────────────
  const startedAt = opts.startedAt || Date.now();
  let timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    timerE.textContent =
      String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' +
      String(elapsed % 60).padStart(2, '0');
  }, 1000);

  // ── helpers ────────────────────────────────────────────────────
  function _trunc(s, n) {
    if (!s) return '';
    s = String(s).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function _addSub(label) {
    if (subs.length >= 10) return; // cap visual clutter
    // Spread subs around a circle; reserve slight offset so first sub doesn't
    // sit directly above the root label.
    const slot = subs.length;
    const totalSlots = Math.max(6, subs.length + 1);
    const angle = (slot / totalSlots) * Math.PI * 2 - Math.PI / 2;
    const r = 78;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;

    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', cx); edge.setAttribute('y1', cy);
    edge.setAttribute('x2', x);  edge.setAttribute('y2', y);
    edge.setAttribute('class', 'rs-edge rs-edge-firing');
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const n = document.createElementNS(SVG_NS, 'circle');
    n.setAttribute('cx', x); n.setAttribute('cy', y); n.setAttribute('r', 7);
    n.setAttribute('class', 'rs-node rs-node-sub rs-node-new');
    nodesG.appendChild(n);

    if (label) {
      const t = document.createElementNS(SVG_NS, 'text');
      // Position label outside the circle on the same angle
      const lx = cx + Math.cos(angle) * (r + 14);
      const ly = cy + Math.sin(angle) * (r + 14);
      t.setAttribute('x', lx); t.setAttribute('y', ly + 3);
      t.setAttribute('text-anchor', Math.cos(angle) > 0.15 ? 'start' :
                                    Math.cos(angle) < -0.15 ? 'end' : 'middle');
      t.setAttribute('class', 'rs-label rs-label-sub');
      t.textContent = _trunc(label, 14);
      nodesG.appendChild(t);
    }

    subs.push({ x, y, count: 0 });
  }

  function _addLeaf() {
    if (!subs.length) _addSub('');
    // Always attach the new source to the CURRENT round's sub (i.e. the
    // most-recently-added one). That gives a clean per-round attribution
    // — 10 sources across 3 rounds ends up as 10/10/10 across the three
    // sub-nodes, not a random scatter.
    const sub = subs[subs.length - 1];
    // Cap total leaves to prevent DOM blowup (same spirit as _addSub's cap).
    // 10 leaves per sub × 10 subs = 100 max leaves → ~200 SVG elements,
    // which keeps rendering fast even when a research job has many rounds.
    const totalLeaves = subs.reduce((sum, s) => sum + s.count, 0);
    if (totalLeaves >= 100) return;
    sub.count++;
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
    edge.setAttribute('class', 'rs-edge rs-edge-firing');
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const leaf = document.createElementNS(SVG_NS, 'circle');
    leaf.setAttribute('cx', lx); leaf.setAttribute('cy', ly);
    leaf.setAttribute('r', 4);
    leaf.setAttribute('class', 'rs-node rs-node-leaf rs-node-new');
    nodesG.appendChild(leaf);
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
      else if (phase === 'reading' && extra.title) txt = `reading: ${_trunc(extra.title, 32)}`;
      else if (phase === 'analyzing' && extra.total_findings) txt += ` · ${extra.total_findings} findings`;
      statusE.textContent = txt;
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
          _addSub(opts.label || `R${i + 1}`);
        }
        lastRound = round;
        roundE.textContent = round;
      }
    },

    /** Update the total source count — adds leaf nodes for any new sources. */
    setSourceCount(total) {
      if (completed) return;
      if (typeof total !== 'number' || total <= sourceCount) return;
      const delta = Math.min(total - sourceCount, 6); // animate at most 6 at a time
      for (let i = 0; i < delta; i++) {
        // Stagger leaves slightly so they don't all pop on the same frame
        setTimeout(_addLeaf, i * 110);
      }
      // Cap the tracked count so future calls don't try to fill a huge delta,
      // and the timer/label don't show absurd numbers.
      sourceCount = Math.min(total, 200);
      srcE.textContent = total;
    },

    /** Mark the run as done — freezes the pulse and tints the graph green. */
    complete() {
      if (completed) return;
      completed = true;
      wrap.classList.add('rs-complete');
      statusE.textContent = 'complete';
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    },

    destroy() {
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    },
  };
}
