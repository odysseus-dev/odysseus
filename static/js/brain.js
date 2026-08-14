// brain.js — the Brain view renderer.
//
// Renders the memory system as an interactive node-link graph:
//
//   - nodes   core     (persona / identity — what the agent is becoming)
//             skill    (reusable knowledge / capability modules)
//             external (store facts, projects, preferences)
//   - node size tracks stored content length (longer entry = larger node)
//   - edges are the precomputed association graph — dense hubs of things the
//     store actually links at recall, not a flat web
//
// Interaction (focus+context):
//   - wheel / drag to zoom & pan
//   - click a node to focus it: its neighbours light up and the connecting
//     edges draw as blue threads; everything else recedes to a grey wireframe
//   - click empty space to reset
//
// Pure SVG, monochrome, CSS-variable driven — matches Odysseus's visual
// language. No dependencies.
//
// On open: fetch /api/memory-brain/overview (on-request, per-turn state).

const NS = 'http://www.w3.org/2000/svg'

const TYPE_COLORS = {
  core: 'var(--color-accent)',
  skill: 'var(--color-brand-blue)',
  external: 'var(--fg)',
}
const THREAD = 'var(--color-brand-blue)'

// ---- tiny force simulation -------------------------------------------------
// Repulsion between every pair + spring attraction along the association
// edges. Associations are precomputed at write time, so settled clusters
// mirror the store's real recall hubs (denser here = linked at recall).

function simulate(nodes, edges, w, h, iterations = 220) {
  const n = nodes.length
  const pos = nodes.map((nd, i) => {
    const ang = (i / Math.max(n, 1)) * Math.PI * 2 - Math.PI / 2
    const rad = Math.min(w, h) * 0.32
    return { x: w / 2 + rad * Math.cos(ang), y: h / 2 + rad * Math.sin(ang), vx: 0, vy: 0 }
  })
  const adj = edges.map(e => {
    const ia = nodes.findIndex(x => String(x.id) === String(e.a))
    const ib = nodes.findIndex(x => String(x.id) === String(e.b))
    return { ia, ib, s: e.s }
  })
  const radius = nodes.map(n => nodeRadius(n))
  const REP = 9000, SPRING = 0.02, DAMP = 0.86, CENTER = 0.012

  for (let it = 0; it < iterations; it++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x
        const dy = pos[i].y - pos[j].y
        const d2 = dx * dx + dy * dy + 0.01
        const minD = radius[i] + radius[j] + 8
        const f = REP / d2
        const fx = (dx / Math.sqrt(d2)) * f
        const fy = (dy / Math.sqrt(d2)) * f
        pos[i].vx += fx; pos[i].vy += fy
        pos[j].vx -= fx; pos[j].vy -= fy
      }
    }
    for (const { ia, ib } of adj) {
      if (ia < 0 || ib < 0) continue
      const dx = pos[ib].x - pos[ia].x
      const dy = pos[ib].y - pos[ia].y
      const d = Math.sqrt(dx * dx + dy * dy) + 0.01
      const f = SPRING * (d - 110)
      pos[ia].vx += (dx / d) * f; pos[ia].vy += (dy / d) * f
      pos[ib].vx -= (dx / d) * f; pos[ib].vy -= (dy / d) * f
    }
    for (const p of pos) {
      p.vx *= DAMP; p.vy *= DAMP
      p.vx += (w / 2 - p.x) * CENTER; p.vy += (h / 2 - p.y) * CENTER
      p.x += p.vx; p.y += p.vy
    }
  }
  return pos
}

function nodeRadius(n) {
  return 3.5 + Math.min(10, Math.log(1 + (n.length || 24)) * 1.6)
}

function el(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v)
  return e
}

export function loadBrain(open = false) {
  const elc = document.getElementById('assoc-graph-canvas')
  if (!elc) return
  elc.innerHTML = ''
  const w = elc.clientWidth || 480
  const h = Math.max(elc.clientHeight, 240) || 240

  const svg = el('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}` })
  const msg = el('text', { x: w / 2, y: h / 2, 'text-anchor': 'middle', 'font-size': '12', fill: 'var(--fg)', opacity: 0.7 })
  msg.textContent = 'Loading brain…'
  svg.appendChild(msg)
  elc.appendChild(svg)

  fetch('/api/memory-brain/overview')
    .then(r => r.json())
    .then(data => draw(elc, svg, data, w, h))
    .catch(() => { msg.textContent = 'Brain view unavailable' })
}

function draw(elc, svg, data, w, h) {
  svg.innerHTML = ''
  const nodes = data.associations?.nodes || []
  const edges = data.associations?.edges || []
  const pos = simulate(nodes, edges, w, h)
  const byId = {}
  nodes.forEach((n, i) => { byId[n.id] = i })

  // zoom/pan state
  let scale = 1, tx = 0, ty = 0, selected = null
  const root = el('g')
  svg.appendChild(root)

  // edge layer (behind nodes)
  const edgeG = el('g')
  const edgeMap = {}
  edges.forEach((e, i) => {
    const ia = byId[e.a], ib = byId[e.b]
    if (ia === undefined || ib === undefined) return
    const line = el('line', {
      x1: pos[ia].x, y1: pos[ia].y, x2: pos[ib].x, y2: pos[ib].y,
      stroke: 'var(--fg)', 'stroke-width': String(Math.max(0.75, e.s * 2.5)),
      opacity: 0.22,
    })
    edgeMap[i] = line
    edgeG.appendChild(line)
  })
  root.appendChild(edgeG)

  // node layer
  const nodeG = el('g')
  const nodeEls = {}
  nodes.forEach((n, i) => {
    const r = nodeRadius(n)
    const circ = el('circle', {
      cx: pos[i].x, cy: pos[i].y, r,
      fill: TYPE_COLORS[n.type] || 'var(--fg)',
      opacity: 0.7, cursor: 'pointer',
    })
    circ.dataset.i = i
    nodeEls[n.id] = circ
    nodeG.appendChild(circ)

    const t = el('text', {
      x: pos[i].x, y: pos[i].y + r + 10,
      'text-anchor': 'middle', 'font-size': '9', fill: 'var(--fg)',
      opacity: 0.75, 'pointer-events': 'none',
    })
    t.textContent = n.label.length > 24 ? n.label.slice(0, 23) + '…' : n.label
    nodeG.appendChild(t)
  })
  root.appendChild(nodeG)

  // legend — core / skill / external + hint
  const legend = el('g')
  const lx = 10, ly = h - 14
  let lxi = 0
  for (const [label, color, r] of [
    ['core', TYPE_COLORS.core, 4],
    ['skill', TYPE_COLORS.skill, 3],
    ['external', TYPE_COLORS.external, 3],
  ]) {
    const d = el('circle', { cx: lx + lxi * 84, cy: ly, r, fill: color, opacity: 0.8 })
    legend.appendChild(d)
    const t = el('text', { x: lx + lxi * 84 + 8, y: ly + 3, 'font-size': '9', fill: 'var(--fg)', opacity: 0.7 })
    t.textContent = label
    legend.appendChild(t)
    lxi++
  }
  const hint = el('text', { x: w - 10, y: h - 8, 'text-anchor': 'end', 'font-size': '9', fill: 'var(--fg)', opacity: 0.45 })
  hint.textContent = 'click a node to trace its associations · scroll to zoom'
  legend.appendChild(hint)
  svg.appendChild(legend)

  // ---- apply focus: highlight selected node + neighbours with blue threads ---
  function focus(id) {
    selected = id
    const selIdx = byId[id]
    const neigh = new Set()
    edges.forEach((e, i) => {
      const touches = String(e.a) === String(id) || String(e.b) === String(id)
      if (!touches) return
      neigh.add(String(e.a)); neigh.add(String(e.b))
      edgeMap[i].setAttribute('stroke', THREAD)
      edgeMap[i].setAttribute('opacity', '0.85')
      edgeMap[i].setAttribute('stroke-width', String(Math.max(1.4, e.s * 3)))
    })
    nodes.forEach((n, i) => {
      const isSel = String(n.id) === String(id)
      const isNear = neigh.has(String(n.id))
      const c = nodeEls[n.id]
      if (isSel) { c.setAttribute('opacity', '1'); c.setAttribute('stroke', THREAD); c.setAttribute('stroke-width', '2') }
      else if (isNear) { c.setAttribute('opacity', '0.95'); c.setAttribute('stroke', THREAD); c.setAttribute('stroke-width', '1.2') }
      else { c.setAttribute('opacity', '0.08') }
      c.style.cursor = 'pointer'
    })
    // dim labels of unrelated nodes, brighten related ones
    Array.from(nodeG.children).forEach(ch => {
      if (ch.tagName !== 'text') return
      const idx = Number(ch.dataset ? ch.dataset.i : -1)
      const nid = nodes[idx]?.id
      const on = neigh.has(nid) || String(nid) === String(id)
      ch.setAttribute('opacity', on ? '0.9' : '0.08')
    })
  }

  function reset() {
    selected = null
    edges.forEach((e, i) => {
      edgeMap[i].setAttribute('stroke', 'var(--fg)')
      edgeMap[i].setAttribute('opacity', '0.22')
      edgeMap[i].setAttribute('stroke-width', String(Math.max(0.75, e.s * 2.5)))
    })
    nodes.forEach((n, i) => {
      const c = nodeEls[n.id]
      c.setAttribute('opacity', '0.7')
      c.removeAttribute('stroke'); c.removeAttribute('stroke-width')
    })
    Array.from(nodeG.children).forEach(ch => {
      if (ch.tagName === 'text') ch.setAttribute('opacity', '0.75')
    })
  }

  // ---- interaction --------------------------------------------------------
  svg.addEventListener('click', ev => {
    const t = ev.target
    if (t && t.dataset && t.dataset.i !== undefined) {
      focus(nodes[Number(t.dataset.i)].id)
    } else {
      reset()
    }
  })

  svg.addEventListener('wheel', ev => {
    ev.preventDefault()
    const rect = svg.getBoundingClientRect()
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top
    const k = ev.deltaY < 0 ? 1.12 : 0.89
    scale = Math.min(8, Math.max(0.3, scale * k))
    const wx = (mx - tx) / scale, wy = (my - ty) / scale
    tx = mx - wx * scale; ty = my - wy * scale
    apply()
  }, { passive: false })

  let drag = null
  svg.addEventListener('mousedown', ev => {
    if (ev.target !== svg && ev.target !== root) return
    drag = { x: ev.clientX, y: ev.clientY }
  })
  window.addEventListener('mousemove', ev => {
    if (!drag) return
    tx += ev.clientX - drag.x; ty += ev.clientY - drag.y
    drag.x = ev.clientX; drag.y = ev.clientY
    apply()
  })
  window.addEventListener('mouseup', () => { drag = null })

  function apply() {
    root.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`)
  }
  apply()
}
