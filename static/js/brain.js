// brain.js — the Brain view renderer.
//
// A faithful map of how the memory system actually operates. It does not
// invent structure — it renders what the store really holds:
//
//   - every node is a real stored memory entry, labelled with its content
//   - nodes are grouped by the neuron cluster they belong to (persona /
//     philosophy / game / memory) — that grouping is computed by the same
//     classifier the store uses, so the sections are how the network really
//     connects stored memory, not a drawing choice
//   - node fill shows the entry's tier: core (persona/identity), skill
//     (knowledge modules), external (store facts/projects/preferences)
//   - node size tracks stored content length (longer entry = larger node)
//   - edges are the precomputed association graph — two memories are joined
//     only where the store actually links them at recall (cosine >= threshold)
//   - a status strip shows what the system is doing: which neurons are
//     firing, and the consolidation pressure
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
// neuron sections — a fixed display order so the map is stable
const NEURON_ORDER = ['persona', 'philosophy', 'game', 'memory']

function el(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v)
  return e
}

function nodeRadius(n) {
  return 3.5 + Math.min(10, Math.log(1 + (n.length || 24)) * 1.6)
}

// ---- layout ---------------------------------------------------------------
// Group nodes by neuron into labelled sections, then run a small force
// simulation within each section (repulsion + spring along associations).
// The sections are the neuron clusters; the edges are real store links.

function layout(nodes, edges, w, h) {
  const byId = {}
  nodes.forEach((n, i) => { byId[n.id] = i })

  // cluster nodes by neuron
  const clusters = {}
  for (const n of nodes) {
    const neu = n.neuron || 'memory'
    ;(clusters[neu] = clusters[neu] || []).push(n)
  }
  const order = [...NEURON_ORDER, ...Object.keys(clusters).filter(k => !NEURON_ORDER.includes(k))]
  const present = order.filter(k => clusters[k])

  // place section centres across the canvas
  const sections = {}
  const cols = Math.ceil(Math.sqrt(present.length))
  const rows = Math.ceil(present.length / cols)
  const cellW = w / cols, cellH = h / (rows + 1.2)
  present.forEach((k, i) => {
    const cx = cellW * (0.5 + (i % cols))
    const cy = cellH * (0.6 + Math.floor(i / cols))
    sections[k] = { cx, cy, nodes: clusters[k], r: Math.min(cellW, cellH) * 0.42 }
  })

  // per-section inner layout: nodes on a ring around the section centre,
  // then relax with repulsion so they don't overlap
  const pos = {}
  const radius = nodes.map(n => nodeRadius(n))
  for (const n of nodes) {
    const sec = sections[n.neuron || 'memory']
    const idx = sec.nodes.findIndex(x => x.id === n.id)
    const ang = (idx / Math.max(sec.nodes.length, 1)) * Math.PI * 2 - Math.PI / 2
    const rad = sec.r * 0.75
    pos[n.id] = { x: sec.cx + rad * Math.cos(ang), y: sec.cy + rad * Math.sin(ang), vx: 0, vy: 0 }
  }

  // relax within each section
  for (let it = 0; it < 60; it++) {
    for (const k of present) {
      const ids = clusters[k].map(n => n.id)
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const a = pos[ids[i]], b = pos[ids[j]]
          const dx = a.x - b.x, dy = a.y - b.y
          const d2 = dx * dx + dy * dy + 0.01
          const minD = radius[byId[ids[i]]] + radius[byId[ids[j]]] + 6
          const f = 600 / d2
          const fx = (dx / Math.sqrt(d2)) * f, fy = (dy / Math.sqrt(d2)) * f
          a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
        }
      }
      for (const id of ids) {
        const p = pos[id], sec = sections[k]
        const dx = sec.cx - p.x, dy = sec.cy - p.y
        p.vx += dx * 0.02; p.vy += dy * 0.02
        p.vx *= 0.85; p.vy *= 0.85
        p.x += p.vx; p.y += p.vy
      }
    }
  }
  return { pos, sections, order: present }
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
  const { pos, sections, order } = layout(nodes, edges, w, h)
  const byId = {}
  nodes.forEach((n, i) => { byId[n.id] = i })

  let scale = 1, tx = 0, ty = 0, selected = null
  const root = el('g')
  svg.appendChild(root)

  // ---- neuron sections (how the network groups stored memory) ------------
  const secG = el('g')
  for (const k of order) {
    const sec = sections[k]
    const pad = 10
    const box = el('rect', {
      x: sec.cx - sec.r - pad, y: sec.cy - sec.r - pad,
      width: sec.r * 2 + pad * 2, height: sec.r * 2 + pad * 2,
      rx: 10, fill: 'none', stroke: 'var(--border)', 'stroke-width': '1',
      'stroke-dasharray': '3 3', opacity: 0.6,
    })
    secG.appendChild(box)
    const lab = el('text', {
      x: sec.cx, y: sec.cy - sec.r - pad + 12,
      'text-anchor': 'middle', 'font-size': '10', fill: 'var(--fg)',
      opacity: 0.85, 'font-weight': '600',
    })
    lab.textContent = `${k} neuron`
    secG.appendChild(lab)
  }
  root.appendChild(secG)

  // ---- edge layer (association links) ------------------------------------
  const edgeG = el('g')
  const edgeMap = {}
  edges.forEach((e, i) => {
    const ia = byId[e.a], ib = byId[e.b]
    if (ia === undefined || ib === undefined) return
    const line = el('line', {
      x1: pos[e.a].x, y1: pos[e.a].y, x2: pos[e.b].x, y2: pos[e.b].y,
      stroke: 'var(--fg)', 'stroke-width': String(Math.max(0.75, e.s * 2.5)),
      opacity: 0.22,
    })
    edgeMap[i] = line
    edgeG.appendChild(line)
  })
  root.appendChild(edgeG)
  // map edges by their endpoint ids so focus can find them without relying
  // on array indices (robust even if some edges reference missing nodes)
  const edgeByNode = {}
  edges.forEach((e, i) => {
    if (!edgeMap[i]) return
    ;(edgeByNode[String(e.a)] = edgeByNode[String(e.a)] || []).push(i)
    ;(edgeByNode[String(e.b)] = edgeByNode[String(e.b)] || []).push(i)
  })

  // ---- node layer (every node is a real memory, labelled) ----------------
  const nodeG = el('g')
  const nodeEls = {}
  nodes.forEach((n, i) => {
    const r = nodeRadius(n)
    const circ = el('circle', {
      cx: pos[n.id].x, cy: pos[n.id].y, r,
      fill: TYPE_COLORS[n.type] || 'var(--fg)',
      opacity: 0.75, cursor: 'pointer',
    })
    circ.dataset.i = i
    nodeEls[n.id] = circ
    nodeG.appendChild(circ)

    // label every node with what it represents
    const t = el('text', {
      x: pos[n.id].x, y: pos[n.id].y + r + 10,
      'text-anchor': 'middle', 'font-size': '8', fill: 'var(--fg)',
      opacity: 0.8, 'pointer-events': 'none',
    })
    t.textContent = n.label.length > 28 ? n.label.slice(0, 27) + '…' : n.label
    t.dataset.i = i
    nodeG.appendChild(t)
  })
  root.appendChild(nodeG)

  // ---- legend: tiers + what the system is doing --------------------------
  const legend = el('g')
  const lx = 10, ly = h - 14
  let lxi = 0
  for (const [label, color, r] of [
    ['core', TYPE_COLORS.core, 4],
    ['skill', TYPE_COLORS.skill, 3],
    ['external', TYPE_COLORS.external, 3],
  ]) {
    const d = el('circle', { cx: lx + lxi * 84, cy: ly, r, fill: color, opacity: 0.85 })
    legend.appendChild(d)
    const t = el('text', { x: lx + lxi * 84 + 8, y: ly + 3, 'font-size': '9', fill: 'var(--fg)', opacity: 0.7 })
    t.textContent = label
    legend.appendChild(t)
    lxi++
  }

  // status strip: what the system is doing — firing neurons + pressure
  const firing = (data.neurons || []).filter(n => n.firing).map(n => n.slug)
  const statusBits = []
  if (firing.length) statusBits.push(`firing: ${firing.join(', ')}`)
  if (data.sleep?.pressure?.score != null) {
    statusBits.push(`pressure ${Math.round(data.sleep.pressure.score * 100)}%`)
  }
  if (!statusBits.length) statusBits.push('idle')
  const status = el('text', {
    x: w - 10, y: h - 8, 'text-anchor': 'end', 'font-size': '9',
    fill: 'var(--fg)', opacity: 0.5,
  })
  status.textContent = `click a node to trace its associations · scroll to zoom · ${statusBits.join(' · ')}`
  legend.appendChild(status)
  svg.appendChild(legend)

  // ---- focus: highlight selected node + its real neighbours ---------------
  function focus(id) {
    selected = id
    const neigh = new Set()
    ;(edgeByNode[String(id)] || []).forEach(i => {
      const e = edges[i]
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
    Array.from(nodeG.children).forEach(ch => {
      if (ch.tagName !== 'text') return
      const idx = Number(ch.dataset ? ch.dataset.i : -1)
      const nid = nodes[idx]?.id
      const on = neigh.has(nid) || String(nid) === String(id)
      ch.setAttribute('opacity', on ? '0.95' : '0.08')
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
      c.setAttribute('opacity', '0.75')
      c.removeAttribute('stroke'); c.removeAttribute('stroke-width')
    })
    Array.from(nodeG.children).forEach(ch => {
      if (ch.tagName === 'text') ch.setAttribute('opacity', '0.8')
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
