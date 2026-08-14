// brain.js — the Brain view renderer.
//
// Renders a living overview of the memory system: a brain silhouette in the
// background, with memory data points positioned and linked through the
// neuron network. Persona and identity nodes are highlighted to show how
// that layer is forming. Pure SVG, monochrome, CSS-variable driven — matches
// Odysseus's visual language (the memory modal is already labelled "Brain").
//
// On open: fetch /api/memory-brain/overview (on-request, per-turn state) and draw.

export function loadBrain(open = false) {
  const el = document.getElementById('assoc-graph-canvas')
  if (!el) return
  el.innerHTML = ''
  const w = el.clientWidth || 480
  const h = Math.max(el.clientHeight, 240) || 240

  // --- brain silhouette (background) ---------------------------------------
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('width', w)
  svg.setAttribute('height', h)
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`)

  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  bg.setAttribute('opacity', '0.12')
  // simple two-lobe brain path centred in the viewbox
  const cx = w / 2, cy = h / 2
  const r = Math.min(w, h) / 2.6
  const brainPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  brainPath.setAttribute('d', `
    M ${cx - r * 1.1} ${cy - r * 0.6}
    C ${cx - r * 1.4} ${cy - r * 1.3}, ${cx - r * 0.4} ${cy - r * 1.4}, ${cx} ${cy - r * 0.8}
    C ${cx + r * 0.4} ${cy - r * 1.4}, ${cx + r * 1.4} ${cy - r * 1.3}, ${cx + r * 1.1} ${cy - r * 0.6}
    C ${cx + r * 1.5} ${cy + r * 0.2}, ${cx + r * 1.2} ${cy + r * 1.1}, ${cx} ${cy + r * 1.2}
    C ${cx - r * 1.2} ${cy + r * 1.1}, ${cx - r * 1.5} ${cy + r * 0.2}, ${cx - r * 1.1} ${cy - r * 0.6}
    Z`)
  brainPath.setAttribute('fill', 'var(--fg)')
  // brain midline
  const mid = document.createElementNS('http://www.w3.org/2000/svg', 'line')
  mid.setAttribute('x1', cx); mid.setAttribute('y1', cy - r * 1.2)
  mid.setAttribute('x2', cx); mid.setAttribute('y2', cy + r * 1.1)
  mid.setAttribute('stroke', 'var(--fg)'); mid.setAttribute('stroke-width', '1')
  bg.appendChild(brainPath); bg.appendChild(mid)
  svg.appendChild(bg)

  const msg = document.createElementNS('http://www.w3.org/2000/svg', 'text')
  msg.setAttribute('x', cx); msg.setAttribute('y', cy)
  msg.setAttribute('text-anchor', 'middle'); msg.setAttribute('font-size', '12')
  msg.setAttribute('fill', 'var(--fg)'); msg.setAttribute('opacity', '0.7')
  msg.textContent = 'Loading brain…'
  svg.appendChild(msg)
  el.appendChild(svg)

  // --- fetch the real state -------------------------------------------------
  fetch('/api/memory-brain/overview')
    .then(r => r.json())
    .then(data => draw(el, svg, data, w, h, cx, cy, r))
    .catch(() => {
      msg.textContent = 'Brain view unavailable'
    })
}

function draw(el, svg, data, w, h, cx, cy, r) {
  // clear the loading message
  svg.innerHTML = ''
  // redraw background
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  bg.setAttribute('opacity', '0.12')
  const brainPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  brainPath.setAttribute('d', `
    M ${cx - r * 1.1} ${cy - r * 0.6}
    C ${cx - r * 1.4} ${cy - r * 1.3}, ${cx - r * 0.4} ${cy - r * 1.4}, ${cx} ${cy - r * 0.8}
    C ${cx + r * 0.4} ${cy - r * 1.4}, ${cx + r * 1.4} ${cy - r * 1.3}, ${cx + r * 1.1} ${cy - r * 0.6}
    C ${cx + r * 1.5} ${cy + r * 0.2}, ${cx + r * 1.2} ${cy + r * 1.1}, ${cx} ${cy + r * 1.2}
    C ${cx - r * 1.2} ${cy + r * 1.1}, ${cx - r * 1.5} ${cy + r * 0.2}, ${cx - r * 1.1} ${cy - r * 0.6}
    Z`)
  brainPath.setAttribute('fill', 'var(--fg)')
  bg.appendChild(brainPath)
  svg.appendChild(bg)

  const nodes = data.associations?.nodes || []
  const edges = data.associations?.edges || []

  // position nodes on an ellipse inside the brain, persona/identity inward
  const isIdentity = n => data.identity?.some(i => String(i.id) === String(n.id))
  const isPersona = n => data.persona?.some(p => String(p.id) === String(n.id))
  const pos = {}
  nodes.forEach((n, i) => {
    const ang = (i / Math.max(nodes.length, 1)) * Math.PI * 2 - Math.PI / 2
    const rad = isIdentity(n) ? r * 0.45 : isPersona(n) ? r * 0.65 : r * 0.85
    pos[n.id] = [cx + rad * Math.cos(ang), cy + rad * Math.sin(ang)]
  })

  // edges (neuron-network links)
  const eg = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  edges.forEach(e => {
    const [x1, y1] = pos[e.a] || []
    const [x2, y2] = pos[e.b] || []
    if (x1 === undefined || x2 === undefined) return
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
    line.setAttribute('x1', x1); line.setAttribute('y1', y1)
    line.setAttribute('x2', x2); line.setAttribute('y2', y2)
    line.setAttribute('stroke', 'var(--accent, var(--red))')
    line.setAttribute('stroke-width', String(Math.max(0.75, e.s * 3)))
    line.setAttribute('opacity', '0.35')
    eg.appendChild(line)
  })
  svg.appendChild(eg)

  // nodes (data points)
  const ng = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  nodes.forEach(n => {
    const [x, y] = pos[n.id] || []
    if (x === undefined) return
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
    c.setAttribute('cx', x); c.setAttribute('cy', y)
    c.setAttribute('r', isIdentity(n) ? 5 : isPersona(n) ? 4 : 3)
    c.setAttribute('fill', isIdentity(n) ? 'var(--accent, var(--red))'
                 : isPersona(n) ? 'var(--fg)' : 'var(--fg)')
    c.setAttribute('opacity', isIdentity(n) ? '1' : isPersona(n) ? '0.85' : '0.55')
    ng.appendChild(c)
    if (isIdentity(n) || isPersona(n)) {
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      t.setAttribute('x', x + 7); t.setAttribute('y', y + 3)
      t.setAttribute('font-size', '8'); t.setAttribute('fill', 'var(--fg)')
      t.setAttribute('opacity', '0.8')
      t.textContent = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label
      ng.appendChild(t)
    }
  })
  svg.appendChild(ng)

  // legend — persona / identity / association layers
  const legend = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  const lx = 10, ly = h - 18
  ;[
    ['identity', 'var(--accent, var(--red))', 'identity'],
    ['persona', 'var(--fg)', 'persona'],
    ['neuron', 'var(--fg)', 'associations'],
  ].forEach(([label, color, kind], i) => {
    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
    dot.setAttribute('cx', lx + i * 95); dot.setAttribute('cy', ly)
    dot.setAttribute('r', kind === 'identity' ? 4 : kind === 'persona' ? 3 : 2)
    dot.setAttribute('fill', color); dot.setAttribute('opacity', '0.8')
    legend.appendChild(dot)
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text')
    t.setAttribute('x', lx + i * 95 + 7); t.setAttribute('y', ly + 3)
    t.setAttribute('font-size', '9'); t.setAttribute('fill', 'var(--fg)')
    t.setAttribute('opacity', '0.7')
    t.textContent = label
    legend.appendChild(t)
  })
  svg.appendChild(legend)
}
