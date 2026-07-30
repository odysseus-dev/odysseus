// Memory Graph View (beta)
// Interactive Cytoscape.js visualization of a user's own memories and their
// derived (semantic similarity, same-session) and manual relationships.
// Backed by GET /api/memory/graph and POST/DELETE /api/memory/{id}/links
// (routes/memory/memory_graph_routes.py).

import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
const escapeHtml = uiModule.esc;

// Category → CSS custom-property name. Resolved to a concrete color at
// render time via getComputedStyle so it tracks the active dark/light theme.
const CATEGORY_VAR = {
  fact: '--fg',
  identity: '--hl-keyword',
  preference: '--warn',
  contact: '--color-accent',
  project: '--color-brand-blue',
  goal: '--accent-warm',
  task: '--green',
};
const CATEGORY_FALLBACK_VAR = '--color-muted-alt';
const KNOWN_CATEGORIES = Object.keys(CATEGORY_VAR);

function _cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function _categoryColor(category) {
  const varName = CATEGORY_VAR[category] || CATEGORY_FALLBACK_VAR;
  return _cssVar(varName, '#888');
}

// ---- placeholder data, shown only when the caller has zero real memories ----
const DEMO_GRAPH = (() => {
  const now = Math.floor(Date.now() / 1000);
  const nodes = [
    { id: 'demo-1', text: 'Works as a product designer at a small startup', category: 'identity', uses: 4, pinned: true, timestamp: now - 86400 * 30 },
    { id: 'demo-2', text: 'Prefers dark roast coffee, no sugar', category: 'preference', uses: 2, pinned: false, timestamp: now - 86400 * 20 },
    { id: 'demo-3', text: 'Working on a side project called "Lighthouse"', category: 'project', uses: 6, pinned: true, timestamp: now - 86400 * 14, session_id: 'demo-session' },
    { id: 'demo-4', text: 'Wants to launch Lighthouse beta by end of quarter', category: 'goal', uses: 3, pinned: false, timestamp: now - 86400 * 10, session_id: 'demo-session' },
    { id: 'demo-5', text: "Partner's birthday is on the 12th", category: 'fact', uses: 1, pinned: false, timestamp: now - 86400 * 6 },
    { id: 'demo-6', text: 'Best reached by email rather than phone', category: 'contact', uses: 1, pinned: false, timestamp: now - 86400 * 2 },
  ];
  const edges = [
    { source: 'demo-3', target: 'demo-4', type: 'session', weight: 1 },
    { source: 'demo-1', target: 'demo-3', type: 'manual', weight: 1 },
    { source: 'demo-1', target: 'demo-2', type: 'similarity', weight: 0.81 },
  ];
  return { nodes, edges, meta: { node_count: nodes.length, edge_count: edges.length, total_memories: nodes.length, truncated: false } };
})();

// ---- module state ----
let _modal = null;
let _cy = null;
let _open = false;
let _isDemo = false;
let _graph = { nodes: [], edges: [] };
let _activeCategory = null; // null = show all categories
let _searchTerm = '';
let _minSimilarity = 0.75;
let _linkMode = false;
let _linkSourceId = null;
let _selectedId = null;
let _isolateRootId = null; // set = only this node's connected component is shown
let _keyHandler = null;
let _resizeWired = false;

// ---- lazy-load Cytoscape, mirroring documentLibrary.js's ensureXLSX/ensureMammoth ----
let _cytoscapeReady = null;
function ensureCytoscape() {
  if (_cytoscapeReady) return _cytoscapeReady;
  if (window.cytoscape) return (_cytoscapeReady = Promise.resolve());
  _cytoscapeReady = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/lib/cytoscape.min.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Failed to load Cytoscape library'));
    document.head.appendChild(s);
  });
  return _cytoscapeReady;
}

// ---- modal shell ----
function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'memory-graph-modal';
  _modal.className = 'modal hidden';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content memory-graph-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px">
            <circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="13" r="2.4"/><circle cx="6" cy="19" r="2.4"/><circle cx="18" cy="19" r="2.4"/>
            <line x1="7.7" y1="7.3" x2="10.6" y2="11.5"/><line x1="16.3" y1="7.3" x2="13.4" y2="11.5"/><line x1="10.9" y1="14.8" x2="7.7" y2="17.7"/><line x1="13.1" y1="14.8" x2="16.3" y2="17.7"/>
          </svg>Memory Graph <span style="font-size:10px;opacity:0.5;font-weight:400;">beta</span>
        </h4>
        <button class="close-btn" id="memory-graph-close">✖</button>
      </div>
      <div class="memory-graph-modal-body">
        <div class="memory-graph-toolbar">
          <input type="text" class="memory-graph-search" id="memory-graph-search" placeholder="Search memories…" />
          <div class="memory-graph-cat-chips" id="memory-graph-cat-chips"></div>
          <div class="memory-graph-slider-row">
            <span>min match</span>
            <input type="range" id="memory-graph-similarity" min="0.5" max="0.95" step="0.05" value="${_minSimilarity}" />
          </div>
          <button type="button" class="memory-graph-link-mode-btn" id="memory-graph-link-mode-btn" title="Click two memories to draw a relationship between them">Link mode</button>
        </div>
        <div class="memory-graph-main">
          <div class="memory-graph-canvas-wrap" style="position:relative;flex:1;min-width:0;">
            <div class="memory-graph-canvas" id="memory-graph-canvas"></div>
            <div class="memory-graph-demo-banner hidden" id="memory-graph-demo-banner">Showing demo data — add memories to see your real graph</div>
            <div class="memory-graph-demo-banner hidden" id="memory-graph-isolate-banner"></div>
            <div class="memory-graph-legend" id="memory-graph-legend">
              <div class="memory-graph-legend-header" id="memory-graph-legend-toggle">
                <span>Legend</span><span class="memory-graph-legend-caret">▾</span>
              </div>
              <div class="memory-graph-legend-body">
                <div class="memory-graph-legend-row"><span class="memory-graph-legend-line"></span><span>similarity</span></div>
                <div class="memory-graph-legend-row"><span class="memory-graph-legend-line dashed"></span><span>same session</span></div>
                <div class="memory-graph-legend-row"><span class="memory-graph-legend-line" style="border-top-color:var(--red);"></span><span>manual link</span></div>
              </div>
            </div>
          </div>
          <div class="memory-graph-detail-panel hidden" id="memory-graph-detail">
            <div class="memory-graph-detail-empty">Select a memory to see details.</div>
          </div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#memory-graph-close').addEventListener('click', closeMemoryGraph);
  _modal.addEventListener('click', (e) => { if (e.target === _modal) closeMemoryGraph(); });

  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  if (content) {
    const obs = new ResizeObserver(() => { if (_cy) _cy.resize(); });
    obs.observe(content);
  }

  _wireToolbar();
  return _modal;
}

function _wireToolbar() {
  const search = document.getElementById('memory-graph-search');
  if (search) {
    let debounceTimer;
    search.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => { _searchTerm = search.value; _applySearch(); }, 220);
    });
  }
  const slider = document.getElementById('memory-graph-similarity');
  if (slider) {
    slider.addEventListener('input', () => {
      _minSimilarity = parseFloat(slider.value) || 0.75;
      _applyFilters();
    });
  }
  const linkBtn = document.getElementById('memory-graph-link-mode-btn');
  if (linkBtn) linkBtn.addEventListener('click', () => _setLinkMode(!_linkMode));
  const legendToggle = document.getElementById('memory-graph-legend-toggle');
  if (legendToggle) {
    legendToggle.addEventListener('click', () => {
      document.getElementById('memory-graph-legend')?.classList.toggle('collapsed');
    });
  }
}

function _wireResize() {
  if (_resizeWired) return;
  _resizeWired = true;
  window.addEventListener('resize', () => { if (_cy && _open) _cy.resize(); });
}

// ---- data loading ----
function _buildQuery() {
  const params = new URLSearchParams();
  // Over-fetch at a floor below the lowest UI slider position (0.5) and a
  // generous per-node edge cap; category/similarity filtering beyond that is
  // done client-side against this cached graph so chip/slider changes are
  // instant and don't re-hit the backend (see docs/memory-graph-design.md,
  // "Performance considerations").
  params.set('min_similarity', '0.5');
  params.set('max_edges_per_node', '8');
  return params.toString();
}

async function _fetchGraph() {
  const res = await fetch(`${API_BASE}/api/memory/graph?${_buildQuery()}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Memory graph request failed (${res.status})`);
  return res.json();
}

async function _loadGraph() {
  _renderLoadingState();
  try {
    const data = await _fetchGraph();
    if (!data.nodes || !data.nodes.length) {
      _graph = DEMO_GRAPH;
      _isDemo = true;
    } else {
      _graph = data;
      _isDemo = false;
    }
  } catch (err) {
    console.error('[memoryGraph] load failed', err);
    _graph = DEMO_GRAPH;
    _isDemo = true;
    uiModule.showToast?.('Could not load your memory graph — showing demo data', 3000);
  }
  try {
    await ensureCytoscape();
  } catch (err) {
    console.error('[memoryGraph] cytoscape load failed', err);
    uiModule.showToast?.('Could not load the graph renderer', 3000);
    return;
  }
  _renderCategoryChips();
  _renderGraph();
}

function _renderLoadingState() {
  const canvas = document.getElementById('memory-graph-canvas');
  if (!canvas) return;
  canvas.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'memory-graph-loading';
  wrap.appendChild(spinnerModule.createLoadingRow('Loading memory graph…', 18));
  canvas.appendChild(wrap);
}

// ---- cytoscape element/style construction ----
function _nodeSize(n) {
  return Math.max(20, Math.min(60, 20 + (n.uses || 0) * 4));
}

function _toElements(graph) {
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]));
  const nodes = graph.nodes.map(n => ({
    data: {
      id: n.id,
      label: (n.text || '').length > 42 ? `${n.text.slice(0, 42)}…` : (n.text || ''),
      category: n.category || 'fact',
      color: _categoryColor(n.category || 'fact'),
      size: _nodeSize(n),
      pinned: !!n.pinned,
    },
  }));
  const edges = [];
  graph.edges.forEach((e, i) => {
    if (!nodeById.has(e.source) || !nodeById.has(e.target)) return;
    edges.push({
      data: {
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: e.type,
        weight: e.weight || 1,
      },
    });
  });
  return { nodes, edges };
}

function _cyStyle() {
  const fg = _cssVar('--fg', '#9cdef2');
  const bg = _cssVar('--bg', '#282c34');
  const border = _cssVar('--border', '#355a66');
  const accent = _cssVar('--accent', _cssVar('--red', '#e06c75'));
  return [
    { selector: 'node', style: {
      'background-color': 'data(color)',
      'label': 'data(label)',
      'width': 'data(size)',
      'height': 'data(size)',
      'font-size': 9,
      'color': fg,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'text-wrap': 'wrap',
      'text-max-width': '90px',
      'border-width': 1,
      'border-color': border,
      'text-outline-width': 2,
      'text-outline-color': bg,
    } },
    { selector: 'node[?pinned]', style: {
      'border-width': 3,
      'border-color': accent,
    } },
    { selector: 'edge[type = "similarity"]', style: {
      'width': 'mapData(weight, 0.5, 1, 1, 4)',
      'line-color': fg,
      'opacity': 'mapData(weight, 0.5, 1, 0.25, 0.7)',
      'curve-style': 'bezier',
      'target-arrow-shape': 'none',
    } },
    { selector: 'edge[type = "session"]', style: {
      'width': 1.4,
      'line-color': _cssVar('--color-muted-alt', '#6b7280'),
      'line-style': 'dashed',
      'opacity': 0.5,
      'curve-style': 'bezier',
    } },
    { selector: 'edge[type = "manual"]', style: {
      'width': 2.4,
      'line-color': accent,
      'opacity': 0.85,
      'curve-style': 'bezier',
    } },
    { selector: '.mg-dimmed', style: { 'opacity': 0.08 } },
    { selector: 'node.mg-highlighted', style: {
      'border-width': 3,
      'border-color': _cssVar('--color-accent', '#00aaff'),
    } },
    { selector: 'edge.mg-highlighted', style: { 'opacity': 1, 'width': 4 } },
    { selector: 'node.mg-search-match', style: {
      'border-width': 3,
      'border-color': _cssVar('--warn', '#f0ad4e'),
    } },
    { selector: 'node.mg-link-source', style: {
      'border-width': 4,
      'border-color': _cssVar('--color-accent', '#00aaff'),
      'border-style': 'double',
    } },
    { selector: 'node:selected', style: {
      'border-width': 3,
      'border-color': fg,
    } },
  ];
}

function _renderGraph() {
  const canvas = document.getElementById('memory-graph-canvas');
  if (!canvas) return;
  canvas.innerHTML = '';
  const { nodes, edges } = _toElements(_graph);
  if (_cy) { _cy.destroy(); _cy = null; }
  _cy = window.cytoscape({
    container: canvas,
    elements: { nodes, edges },
    style: _cyStyle(),
    layout: { name: 'cose', animate: false, padding: 30, nodeRepulsion: 8000, idealEdgeLength: 90 },
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.2,
  });
  _wireCyEvents();
  _isolateRootId = null;
  _applyFilters();
  _renderDemoBanner();
  _renderIsolateBanner();
  _selectedId = null;
  _renderDetailPanel();
}

function _renderDemoBanner() {
  const banner = document.getElementById('memory-graph-demo-banner');
  if (banner) banner.classList.toggle('hidden', !_isDemo);
}

// ---- category filter + search + similarity threshold ----
function _renderCategoryChips() {
  const container = document.getElementById('memory-graph-cat-chips');
  if (!container) return;
  const seen = new Set(_graph.nodes.map(n => n.category || 'fact'));
  const cats = [
    ...KNOWN_CATEGORIES.filter(c => seen.has(c)),
    ...[...seen].filter(c => !KNOWN_CATEGORIES.includes(c)),
  ];
  container.innerHTML = '';
  const allChip = document.createElement('button');
  allChip.type = 'button';
  allChip.className = `memory-graph-cat-chip${!_activeCategory ? ' active' : ''}`;
  allChip.textContent = 'all';
  allChip.addEventListener('click', () => { _activeCategory = null; _renderCategoryChips(); _applyFilters(); });
  container.appendChild(allChip);
  cats.forEach(cat => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = `memory-graph-cat-chip${_activeCategory === cat ? ' active' : ''}`;
    chip.style.setProperty('--mg-cat-color', _categoryColor(cat));
    chip.textContent = cat;
    chip.addEventListener('click', () => { _activeCategory = cat; _renderCategoryChips(); _applyFilters(); });
    container.appendChild(chip);
  });
}

// Pure BFS over a graph's edges (undirected) — the set of node ids reachable
// from rootId, including rootId itself. Used by the "Isolate" detail-panel
// action to show only a node's connected component. No DOM/Cytoscape
// dependency, so this is unit-testable in isolation (see memoryGraph tests).
function _componentNodeIds(graph, rootId) {
  const adjacency = new Map();
  (graph.nodes || []).forEach(n => adjacency.set(n.id, new Set()));
  (graph.edges || []).forEach(e => {
    if (!adjacency.has(e.source) || !adjacency.has(e.target)) return;
    adjacency.get(e.source).add(e.target);
    adjacency.get(e.target).add(e.source);
  });
  const seen = new Set();
  if (!adjacency.has(rootId)) return seen;
  const stack = [rootId];
  while (stack.length) {
    const id = stack.pop();
    if (seen.has(id)) continue;
    seen.add(id);
    for (const neighbor of adjacency.get(id) || []) {
      if (!seen.has(neighbor)) stack.push(neighbor);
    }
  }
  return seen;
}

function _applyFilters() {
  if (!_cy) return;
  const isolateIds = _isolateRootId ? _componentNodeIds(_graph, _isolateRootId) : null;
  _cy.batch(() => {
    _cy.nodes().forEach(n => {
      const categoryOk = !_activeCategory || n.data('category') === _activeCategory;
      const isolateOk = !isolateIds || isolateIds.has(n.id());
      n.style('display', (categoryOk && isolateOk) ? 'element' : 'none');
    });
    _cy.edges().forEach(e => {
      const src = _cy.getElementById(e.data('source'));
      const tgt = _cy.getElementById(e.data('target'));
      const endpointsVisible = src.style('display') !== 'none' && tgt.style('display') !== 'none';
      const passesSimilarity = e.data('type') !== 'similarity' || e.data('weight') >= _minSimilarity;
      e.style('display', (endpointsVisible && passesSimilarity) ? 'element' : 'none');
    });
  });
  _applySearch();
}

function _renderIsolateBanner() {
  const banner = document.getElementById('memory-graph-isolate-banner');
  if (!banner) return;
  if (!_isolateRootId) { banner.classList.add('hidden'); return; }
  const count = _componentNodeIds(_graph, _isolateRootId).size;
  banner.textContent = `Isolated — showing ${count} connected ${count === 1 ? 'memory' : 'memories'}`;
  banner.classList.remove('hidden');
}

function _toggleIsolate(id) {
  _isolateRootId = (_isolateRootId === id) ? null : id;
  _applyFilters();
  _renderIsolateBanner();
  _renderDetailPanel();
}

function _applySearch() {
  if (!_cy) return;
  _cy.nodes().removeClass('mg-search-match');
  const term = _searchTerm.trim().toLowerCase();
  if (!term) return;
  // A fresh search supersedes any leftover node-selection highlight —
  // otherwise .mg-dimmed's opacity:0.08 masks nodes that match the search
  // but weren't part of the previously selected node's neighborhood.
  if (_selectedId) { _selectedId = null; _renderDetailPanel(); }
  _cy.elements().removeClass('mg-highlighted mg-dimmed');
  const matches = _cy.nodes().filter(n => {
    const src = _findNode(n.id());
    return (src?.text || '').toLowerCase().includes(term);
  });
  matches.addClass('mg-search-match');
  if (matches.length) {
    _cy.animate({ fit: { eles: matches, padding: 60 } }, { duration: 250 });
  }
}

// ---- selection / highlighting ----
function _findNode(id) {
  return _graph.nodes.find(n => n.id === id) || null;
}

function _manualLinksFor(id) {
  return _graph.edges
    .filter(e => e.type === 'manual' && (e.source === id || e.target === id))
    .map(e => {
      const otherId = e.source === id ? e.target : e.source;
      return { id: otherId, node: _findNode(otherId) };
    })
    .filter(l => l.node);
}

function _highlightNeighborhood(id) {
  if (!_cy) return;
  _cy.elements().removeClass('mg-highlighted mg-dimmed');
  const node = _cy.getElementById(id);
  if (!node || node.empty()) return;
  const neighborhood = node.closedNeighborhood();
  _cy.elements().difference(neighborhood).addClass('mg-dimmed');
  neighborhood.addClass('mg-highlighted');
}

function _selectNode(id) {
  _selectedId = id;
  _highlightNeighborhood(id);
  _renderDetailPanel();
}

function _clearSelection() {
  _selectedId = null;
  if (_isolateRootId) {
    _isolateRootId = null;
    _applyFilters();
    _renderIsolateBanner();
  }
  if (_cy) _cy.elements().removeClass('mg-highlighted mg-dimmed');
  _renderDetailPanel();
}

function _wireCyEvents() {
  if (!_cy) return;
  _cy.on('tap', 'node', (evt) => {
    const node = evt.target;
    if (_linkMode) { _handleLinkModeClick(node.id()); return; }
    _selectNode(node.id());
  });
  _cy.on('tap', 'edge', (evt) => {
    const edge = evt.target;
    _cy.elements().removeClass('mg-highlighted mg-dimmed');
    const eles = edge.connectedNodes().union(edge);
    _cy.elements().difference(eles).addClass('mg-dimmed');
    eles.addClass('mg-highlighted');
    _selectedId = null;
  });
  _cy.on('tap', (evt) => {
    if (evt.target === _cy) _clearSelection();
  });
}

// ---- link (manual relationship) mode ----
function _setLinkMode(on) {
  _linkMode = on;
  _linkSourceId = null;
  if (_cy) _cy.nodes().removeClass('mg-link-source');
  const btn = document.getElementById('memory-graph-link-mode-btn');
  if (btn) btn.classList.toggle('active', on);
}

async function _handleLinkModeClick(id) {
  if (_isDemo) { uiModule.showToast?.('Demo data — add a real memory first', 2500); return; }
  if (!_linkSourceId) {
    _linkSourceId = id;
    _cy.getElementById(id).addClass('mg-link-source');
    uiModule.showToast?.('Click another memory to connect it', 2500);
    return;
  }
  if (_linkSourceId === id) return;
  const sourceId = _linkSourceId;
  _cy.nodes().removeClass('mg-link-source');
  _linkSourceId = null;
  try {
    await _apiAddLink(sourceId, id);
    uiModule.showToast?.('Relationship added');
    await _reloadAfterMutation();
  } catch (err) {
    console.error('[memoryGraph] add link failed', err);
    uiModule.showToast?.('Could not add relationship');
  }
}

// ---- detail panel ----
function _formatTimestamp(ts) {
  if (!ts) return 'Unknown';
  try { return new Date(ts * 1000).toLocaleString(); } catch { return 'Unknown'; }
}

function _renderDetailPanel() {
  const panel = document.getElementById('memory-graph-detail');
  if (!panel) return;
  panel.classList.remove('hidden');
  if (!_selectedId) {
    panel.innerHTML = '<div class="memory-graph-detail-empty">Select a memory to see details.</div>';
    return;
  }
  const node = _findNode(_selectedId);
  if (!node) { _selectedId = null; _renderDetailPanel(); return; }

  const color = _categoryColor(node.category);
  const links = _manualLinksFor(_selectedId);
  panel.innerHTML = `
    <span class="memory-graph-detail-cat" style="--mg-cat-color:${color}">${escapeHtml(node.category || 'fact')}</span>
    <div class="memory-graph-detail-text" id="memory-graph-detail-text">${escapeHtml(node.text || '')}</div>
    <div class="memory-graph-detail-meta">
      <span>${node.uses || 0} use${node.uses === 1 ? '' : 's'} · ${node.pinned ? 'pinned' : 'not pinned'}</span>
      <span>${escapeHtml(_formatTimestamp(node.timestamp))}</span>
    </div>
    <div class="memory-graph-detail-actions" id="memory-graph-detail-actions">
      <button type="button" data-action="pin">${node.pinned ? 'Unpin' : 'Pin'}</button>
      <button type="button" data-action="edit">Edit</button>
      <button type="button" data-action="link">Start link</button>
      <button type="button" data-action="isolate">${_isolateRootId === node.id ? 'Show all' : 'Isolate'}</button>
      <button type="button" class="danger" data-action="delete">Delete</button>
    </div>
    <div class="memory-graph-detail-links">
      <div class="memory-graph-detail-links-title">Relationships (${links.length})</div>
      ${links.length ? links.map(l => `
        <div class="memory-graph-link-row" data-target="${escapeHtml(l.id)}">
          <span class="memory-graph-link-row-text">${escapeHtml((l.node.text || '').slice(0, 40))}</span>
          <span class="memory-graph-link-row-remove" title="Remove relationship">✕</span>
        </div>`).join('') : '<div style="font-size:11px;opacity:0.5;">No manual relationships yet.</div>'}
    </div>
    ${_isDemo ? '<div style="font-size:10.5px;opacity:0.6;">Demo data — actions disabled. Add a real memory to try this.</div>' : ''}
  `;

  if (_isDemo) {
    panel.querySelectorAll('button, .memory-graph-link-row-remove').forEach(b => {
      b.disabled = true;
      b.style.pointerEvents = 'none';
      b.style.opacity = '0.4';
    });
    return;
  }

  panel.querySelector('[data-action="pin"]')?.addEventListener('click', () => _actionPin(node));
  panel.querySelector('[data-action="edit"]')?.addEventListener('click', () => _enterEditMode(node));
  panel.querySelector('[data-action="link"]')?.addEventListener('click', () => {
    _setLinkMode(true);
    _linkSourceId = node.id;
    _cy.getElementById(node.id).addClass('mg-link-source');
    uiModule.showToast?.('Click another memory to connect it', 2500);
  });
  panel.querySelector('[data-action="isolate"]')?.addEventListener('click', () => _toggleIsolate(node.id));
  panel.querySelector('[data-action="delete"]')?.addEventListener('click', () => _actionDelete(node));
  panel.querySelectorAll('.memory-graph-link-row-remove').forEach(el => {
    el.addEventListener('click', (e) => {
      const row = e.target.closest('.memory-graph-link-row');
      const targetId = row?.dataset.target;
      if (targetId) _actionRemoveLink(node.id, targetId);
    });
  });
}

function _enterEditMode(node) {
  const textEl = document.getElementById('memory-graph-detail-text');
  const actions = document.getElementById('memory-graph-detail-actions');
  if (!textEl || !actions) return;

  const textarea = document.createElement('textarea');
  textarea.className = 'memory-graph-detail-textarea';
  textarea.value = node.text || '';
  textEl.replaceWith(textarea);
  textarea.focus();

  actions.innerHTML = `
    <button type="button" data-action="save">Save</button>
    <button type="button" data-action="cancel">Cancel</button>
  `;
  actions.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const newText = textarea.value.trim();
    if (!newText) { uiModule.showToast?.('Memory text cannot be empty'); return; }
    await _actionUpdateText(node, newText);
  });
  actions.querySelector('[data-action="cancel"]').addEventListener('click', () => _renderDetailPanel());
}

// ---- mutations against the Milestone 1 backend ----
function _formBody(fields) {
  const body = new URLSearchParams();
  Object.entries(fields || {}).forEach(([k, v]) => { if (v !== undefined && v !== null) body.set(k, String(v)); });
  return body.toString();
}

async function _postForm(url, fields) {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: _formBody(fields),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json().catch(() => ({}));
}

async function _putForm(url, fields) {
  const res = await fetch(url, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: _formBody(fields),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json().catch(() => ({}));
}

async function _apiAddLink(sourceId, targetId) {
  const url = `${API_BASE}/api/memory/${encodeURIComponent(sourceId)}/links?target_id=${encodeURIComponent(targetId)}`;
  const res = await fetch(url, { method: 'POST', credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

async function _reloadAfterMutation(keepSelection = true) {
  const prevSelected = keepSelection ? _selectedId : null;
  try {
    _graph = await _fetchGraph();
    _isDemo = false;
  } catch (err) {
    console.error('[memoryGraph] reload failed', err);
    return;
  }
  _renderCategoryChips();
  _renderGraph();
  if (prevSelected && _cy && _cy.getElementById(prevSelected).nonempty()) {
    _selectNode(prevSelected);
  }
}

async function _actionPin(node) {
  try {
    await _postForm(`${API_BASE}/api/memory/${encodeURIComponent(node.id)}/pin`, { pinned: !node.pinned });
    uiModule.showToast?.(node.pinned ? 'Unpinned' : 'Pinned');
    await _reloadAfterMutation();
  } catch (err) {
    console.error('[memoryGraph] pin failed', err);
    uiModule.showToast?.('Could not update pin state');
  }
}

async function _actionUpdateText(node, newText) {
  try {
    await _putForm(`${API_BASE}/api/memory/${encodeURIComponent(node.id)}`, { text: newText, category: node.category });
    uiModule.showToast?.('Memory updated');
    await _reloadAfterMutation();
  } catch (err) {
    console.error('[memoryGraph] update failed', err);
    uiModule.showToast?.('Could not update memory');
  }
}

async function _actionDelete(node) {
  const ok = await uiModule.styledConfirm(`Delete this memory?\n\n"${node.text}"`, { confirmText: 'Delete', danger: true });
  if (!ok) return;
  try {
    const res = await fetch(`${API_BASE}/api/memory/${encodeURIComponent(node.id)}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    uiModule.showToast?.('Memory deleted');
    await _reloadAfterMutation(false);
  } catch (err) {
    console.error('[memoryGraph] delete failed', err);
    uiModule.showToast?.('Could not delete memory');
  }
}

async function _actionRemoveLink(sourceId, targetId) {
  try {
    const res = await fetch(`${API_BASE}/api/memory/${encodeURIComponent(sourceId)}/links/${encodeURIComponent(targetId)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    uiModule.showToast?.('Relationship removed');
    await _reloadAfterMutation();
  } catch (err) {
    console.error('[memoryGraph] remove link failed', err);
    uiModule.showToast?.('Could not remove relationship');
  }
}

// ---- keyboard navigation ----
function _visibleNodeIds() {
  if (!_cy) return [];
  return _cy.nodes().filter(n => n.style('display') !== 'none').map(n => n.id());
}

function _navigateNodes(delta) {
  const ids = _visibleNodeIds();
  if (!ids.length) return;
  const curIdx = _selectedId ? ids.indexOf(_selectedId) : -1;
  const nextIdx = curIdx === -1 ? 0 : (curIdx + delta + ids.length) % ids.length;
  const id = ids[nextIdx];
  _selectNode(id);
  const node = _cy.getElementById(id);
  if (node && node.nonempty()) _cy.animate({ center: { eles: node } }, { duration: 150 });
}

// ---- open / close ----
export function isMemoryGraphOpen() {
  if (Modals.isMinimized('memory-graph-modal')) return false;
  return _open;
}

export function openMemoryGraph() {
  if (_open) return;
  if (Modals.isMinimized('memory-graph-modal')) {
    Modals.restore('memory-graph-modal');
    _open = true;
    return;
  }
  _open = true;
  const modal = _getModal();
  modal.classList.remove('hidden', 'modal-minimized');
  const content = modal.querySelector('.modal-content');
  if (content) {
    content.classList.remove('modal-closing', 'sheet-ready');
    content.style.transform = '';
    content.style.transition = '';
    content.style.animation = '';
    content.style.opacity = '';
  }
  modal.style.display = 'flex';
  Modals.register('memory-graph-modal', {
    railBtnId: 'tool-memory-graph-btn',
    closeFn: () => _doCloseMemoryGraph(),
    restoreFn: () => { if (_cy) _cy.resize(); },
    label: 'Memory Graph',
    icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="13" r="2.4"/><circle cx="6" cy="19" r="2.4"/><circle cx="18" cy="19" r="2.4"/></svg>',
  });
  const btn = document.getElementById('tool-memory-graph-btn');
  if (btn) btn.classList.add('active');

  _keyHandler = (e) => {
    if (Modals.isMinimized('memory-graph-modal')) return;
    const active = document.activeElement;
    const typing = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');

    if (e.key === 'Escape') {
      if (active && active.id === 'memory-graph-search' && active.value) {
        active.value = '';
        _searchTerm = '';
        _applySearch();
        return;
      }
      if (_linkMode) { _setLinkMode(false); return; }
      closeMemoryGraph();
      return;
    }
    if (typing) return; // don't hijack f/arrows while the user is typing anywhere in the modal
    if (e.key === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      document.getElementById('memory-graph-search')?.focus();
      return;
    }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); _navigateNodes(1); return; }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); _navigateNodes(-1); return; }
  };
  document.addEventListener('keydown', _keyHandler);

  _wireResize();
  _loadGraph();
  requestAnimationFrame(() => { if (_cy) _cy.resize(); });
}

function _doCloseMemoryGraph() {
  _open = false;
  _setLinkMode(false);
  if (_modal) { _modal.style.display = 'none'; _modal.classList.add('hidden'); }
  if (_keyHandler) { document.removeEventListener('keydown', _keyHandler); _keyHandler = null; }
  const btn = document.getElementById('tool-memory-graph-btn');
  if (btn) btn.classList.remove('active');
}

export function closeMemoryGraph() {
  if (!_open && !Modals.isMinimized('memory-graph-modal')) return;
  if (Modals.isRegistered('memory-graph-modal')) {
    Modals.close('memory-graph-modal');
  } else {
    _doCloseMemoryGraph();
  }
}

const memoryGraphModule = { openMemoryGraph, closeMemoryGraph, isMemoryGraphOpen };
export default memoryGraphModule;
