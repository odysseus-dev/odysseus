// brain.js — the Brain view renderer.
//
// Renders a force-directed graph of memory entries grouped by topic.
// Node size = content length, node fill = tier, edges = associations.
// Pure SVG, monochrome, CSS-variable driven — no dependencies.
//
// On open: fetch /api/memory-brain/overview (on-request).

const NS = 'http://www.w3.org/2000/svg'

const TYPE_COLORS = {
  core: 'var(--color-accent)',
  skill: 'var(--color-brand-blue)',
  external: 'var(--fg)',
}
const THREAD = 'var(--color-brand-blue)'

// Neuron order is derived from data, not hardcoded.

let svgEl = null
let zoomG = null
let scale = 1
let panX = 0, panY = 0
let dragging = false
let dragStart = null
let focusedNode = null

export async function openBrain() {
  const container = document.getElementById('brain-container')
  if (!container) return
  container.innerHTML = ''

  svgEl = document.createElementNS(NS, 'svg')
  svgEl.setAttribute('width', '100%')
  svgEl.setAttribute('height', '100%')
  svgEl.style.cursor = 'grab'
  container.appendChild(svgEl)

  zoomG = document.createElementNS(NS, 'g')
  svgEl.appendChild(zoomG)

  // Fetch brain data.
  try {
    const res = await fetch(`${window.location.origin}/api/memory-brain/overview`)
    if (!res.ok) {
      container.innerHTML = '<p style="opacity:0.5;text-align:center;padding:40px;">Brain view unavailable</p>'
      return
    }
    const data = await res.json()
    renderBrain(data)
  } catch (e) {
    console.error('Brain fetch failed:', e)
    container.innerHTML = '<p style="opacity:0.5;text-align:center;padding:40px;">Brain view unavailable</p>'
  }

  // Zoom/pan.
  svgEl.addEventListener('wheel', (e) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    scale = Math.max(0.1, Math.min(5, scale * delta))
    updateTransform()
  })

  svgEl.addEventListener('mousedown', (e) => {
    if (e.target === svgEl || e.target === zoomG) {
      dragging = true
      dragStart = { x: e.clientX - panX, y: e.clientY - panY }
      svgEl.style.cursor = 'grabbing'
    }
  })

  svgEl.addEventListener('mousemove', (e) => {
    if (dragging && dragStart) {
      panX = e.clientX - dragStart.x
      panY = e.clientY - dragStart.y
      updateTransform()
    }
  })

  svgEl.addEventListener('mouseup', () => {
    dragging = false
    dragStart = null
    svgEl.style.cursor = 'grab'
  })

  svgEl.addEventListener('click', (e) => {
    if (e.target === svgEl) {
      clearFocus()
    }
  })
}

function updateTransform() {
  if (zoomG) {
    zoomG.setAttribute('transform', `translate(${panX},${panY}) scale(${scale})`)
  }
}

function renderBrain(data) {
  const { associations, neurons, topics } = data
  if (!associations && !neurons) return

  // Build nodes from neurons.
  const nodes = (neurons || []).map((n, i) => ({
    id: n.id,
    label: n.slug || n.text?.substring(0, 30) || `entry-${n.id}`,
    text: n.text || '',
    topic: n.kind || 'memory',
    size: Math.max(4, Math.min(20, (n.text || '').length / 5)),
  }))

  // Derive topic order from data (not hardcoded).
  const topicOrder = Object.keys(topics || {}).sort((a, b) => (topics[b] || 0) - (topics[a] || 0))
  if (topicOrder.length === 0) topicOrder.push('memory')

  // Position nodes in clusters.
  const width = svgEl.clientWidth || 800
  const height = svgEl.clientHeight || 600
  const cx = width / 2, cy = height / 2
  const clusterRadius = Math.min(width, height) * 0.3

  nodes.forEach((node, i) => {
    const topicIdx = topicOrder.indexOf(node.topic)
    const angle = (topicIdx / topicOrder.length) * Math.PI * 2 - Math.PI / 2
    const clusterX = cx + Math.cos(angle) * clusterRadius
    const clusterY = cy + Math.sin(angle) * clusterRadius
    const jitter = 30
    node.x = clusterX + (Math.random() - 0.5) * jitter
    node.y = clusterY + (Math.random() - 0.5) * jitter
  })

  // Draw edges.
  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n]))
  for (const assoc of (associations || [])) {
    const src = nodeMap[assoc.source]
    const dst = nodeMap[assoc.target]
    if (!src || !dst) continue
    const line = document.createElementNS(NS, 'line')
    line.setAttribute('x1', src.x)
    line.setAttribute('y1', src.y)
    line.setAttribute('x2', dst.x)
    line.setAttribute('y2', dst.y)
    line.setAttribute('stroke', 'var(--border)')
    line.setAttribute('stroke-width', '0.5')
    line.setAttribute('opacity', '0.3')
    line.classList.add('brain-edge')
    line.dataset.src = assoc.source
    line.dataset.dst = assoc.target
    zoomG.appendChild(line)
  }

  // Draw nodes.
  for (const node of nodes) {
    const g = document.createElementNS(NS, 'g')
    g.classList.add('brain-node')
    g.dataset.id = node.id
    g.style.cursor = 'pointer'

    const circle = document.createElementNS(NS, 'circle')
    circle.setAttribute('cx', node.x)
    circle.setAttribute('cy', node.y)
    circle.setAttribute('r', node.size)
    circle.setAttribute('fill', TYPE_COLORS[node.topic] || TYPE_COLORS.external)
    circle.setAttribute('opacity', '0.7')
    g.appendChild(circle)

    const text = document.createElementNS(NS, 'text')
    text.setAttribute('x', node.x)
    text.setAttribute('y', node.y + node.size + 12)
    text.setAttribute('text-anchor', 'middle')
    text.setAttribute('font-size', '10')
    text.setAttribute('fill', 'var(--fg)')
    text.setAttribute('opacity', '0.6')
    text.textContent = node.label.substring(0, 20)
    g.appendChild(text)

    g.addEventListener('click', (e) => {
      e.stopPropagation()
      focusNode(node.id)
    })

    zoomG.appendChild(g)
  }

  // Status strip.
  const status = document.createElement('div')
  status.style.cssText = 'position:absolute;bottom:8px;left:8px;font-size:11px;opacity:0.5;'
  status.textContent = `${nodes.length} entries · ${associations?.length || 0} links`
  svgEl.parentElement.appendChild(status)
}

function focusNode(nodeId) {
  clearFocus()
  focusedNode = nodeId

  // Dim all nodes and edges.
  zoomG.querySelectorAll('.brain-node').forEach(g => {
    g.style.opacity = g.dataset.id === String(nodeId) ? '1' : '0.15'
  })
  zoomG.querySelectorAll('.brain-edge').forEach(line => {
    const src = line.dataset.src
    const dst = line.dataset.dst
    const connected = src === String(nodeId) || dst === String(nodeId)
    line.setAttribute('stroke', connected ? THREAD : 'var(--border)')
    line.setAttribute('opacity', connected ? '0.8' : '0.05')
    line.setAttribute('stroke-width', connected ? '2' : '0.5')
  })
}

function clearFocus() {
  focusedNode = null
  zoomG.querySelectorAll('.brain-node').forEach(g => {
    g.style.opacity = '1'
  })
  zoomG.querySelectorAll('.brain-edge').forEach(line => {
    line.setAttribute('stroke', 'var(--border)')
    line.setAttribute('opacity', '0.3')
    line.setAttribute('stroke-width', '0.5')
  })
}

export default { openBrain }
