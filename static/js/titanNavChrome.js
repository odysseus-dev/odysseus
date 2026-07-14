/**
 * Apply TITAN sidebar brand lockup, filled nav icons, and icon-rail tiles.
 *
 * Layout model (sidebar ↔ rail):
 *   [toggle band]                   rail-header-slot  ↔  .sidebar-header (toggle only)
 *   [nav rows]                      #rail-nav-stack    ↔  .sidebar-inner rows
 */
import {
  navIcon,
  toolIcon,
  titanSidebarBrandHtml,
  titanWelcomeBrandHtml,
  titanToggleMarkHtml,
  NAV_ICON_TARGETS,
  TOOL_ICON_TARGETS,
  RAIL_ICON_SOURCES,
  RAIL_ROW_ANCHORS,
  RAIL_TOOL_ANCHORS,
} from './titanBrand.js';
import { FUGASSA_SIDEBAR_SVG } from './fugassa/fugassaIcons.js';
import { mountTitanModalIcons, applyModalHeaderIcons } from './titanModalIcons.js';

function ensureSidebarIconTile(root, svg) {
  if (!svg) return null;
  let tile = svg.closest('.sidebar-icon-tile');
  if (tile) return tile;
  tile = document.createElement('span');
  tile.className = 'sidebar-icon-tile';
  svg.parentNode.insertBefore(tile, svg);
  tile.appendChild(svg);
  return tile;
}

function wrapSidebarIconTiles() {
  for (const id of Object.keys(NAV_ICON_TARGETS)) {
    const root = document.getElementById(id);
    if (!root) continue;
    const svg = root.querySelector('.section-icon, .sidebar-action-icon, svg');
    ensureSidebarIconTile(root, svg);
  }

  document.querySelectorAll('#tools-section .list-item').forEach((item) => {
    const svg = item.querySelector(':scope > svg.sidebar-action-icon, :scope > svg');
    ensureSidebarIconTile(item, svg);
  });

  const settingsBtn = document.getElementById('user-bar-settings');
  if (settingsBtn) {
    const svg = settingsBtn.querySelector('svg');
    ensureSidebarIconTile(settingsBtn, svg);
  }
}

function applySidebarNavIcons() {
  for (const [id, name] of Object.entries(NAV_ICON_TARGETS)) {
    const root = document.getElementById(id);
    if (!root) continue;
    const className = id.includes('sidebar') ? 'sidebar-action-icon' : 'section-icon';
    const markup = navIcon(name, { size: 13, className });
    const tile = root.querySelector('.sidebar-icon-tile');
    if (tile) {
      tile.innerHTML = markup;
      continue;
    }
    const host = root.querySelector('.section-icon, .sidebar-action-icon, svg');
    if (host) host.outerHTML = markup;
  }
}

function applyToolSidebarIcons() {
  for (const [id, name] of Object.entries(TOOL_ICON_TARGETS)) {
    const root = document.getElementById(id);
    if (!root) continue;
    const markup = toolIcon(name, { size: 13 });
    const tile = root.querySelector('.sidebar-icon-tile');
    if (tile) {
      tile.innerHTML = markup;
      continue;
    }
    const host = root.querySelector(':scope > svg');
    if (host) host.outerHTML = markup;
  }

  const fugassaSidebar = document.querySelector('#tool-fugassa-btn .sidebar-icon-tile');
  if (fugassaSidebar) {
    fugassaSidebar.innerHTML = FUGASSA_SIDEBAR_SVG;
  }

  const settingsTile = document.querySelector('#user-bar-settings .sidebar-icon-tile');
  if (settingsTile) {
    settingsTile.innerHTML = navIcon('settings', { size: 13, className: '' });
  }
}

function cloneRailIconMarkup(svg) {
  const el = svg.cloneNode(true);
  const srcW = svg.getAttribute('width');
  const srcH = svg.getAttribute('height');
  if (srcW) el.setAttribute('width', srcW);
  if (srcH) el.setAttribute('height', srcH);
  return el.outerHTML;
}

function querySidebarIconSvg(sourceSelector) {
  const hostSel = sourceSelector.replace(/ svg$/, '');
  const host = document.querySelector(hostSel);
  if (!host) return null;
  return host.querySelector('.sidebar-icon-tile svg, .section-icon, .sidebar-action-icon, svg');
}

function syncRailIconsFromSidebar() {
  for (const [railId, selector] of RAIL_ICON_SOURCES) {
    const src = querySidebarIconSvg(selector);
    const btn = document.getElementById(railId);
    if (!src || !btn) continue;
    const tile = btn.querySelector('.icon-rail-tile');
    if (!tile) continue;
    tile.innerHTML = cloneRailIconMarkup(src);
  }
}

function wrapRailTiles() {
  document.querySelectorAll('.icon-rail-btn').forEach((btn) => {
    if (btn.id === 'rail-delete-session') {
      btn.hidden = true;
      return;
    }
    if (btn.querySelector('.icon-rail-tile')) return;
    const tile = document.createElement('span');
    tile.className = 'icon-rail-tile';
    const keep = [];
    [...btn.childNodes].forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE && node.classList?.contains('rail-notes-badge')) {
        keep.push(node);
        return;
      }
      if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim()) return;
      tile.appendChild(node);
    });
    if (!tile.childNodes.length) return;
    btn.prepend(tile);
    keep.forEach((node) => btn.appendChild(node));
  });
}

function applySidebarBrand() {
  const brand = document.getElementById('sidebar-brand-btn');
  if (!brand) return;
  brand.classList.add('sidebar-brand-lockup');
  brand.innerHTML = titanSidebarBrandHtml();
}

function applyWelcomeBrand() {
  const brand = document.querySelector('#welcome-screen .welcome-name');
  if (!brand) return;
  brand.classList.remove('welcome-mode-lockup');
  brand.classList.add('welcome-brand-lockup');
  brand.innerHTML = titanWelcomeBrandHtml();
  brand.setAttribute('aria-label', 'TITAN');
}

export { applyWelcomeBrand };

function applyToggleBrand() {
  const mark = titanToggleMarkHtml({ size: 18 });
  for (const id of ['hamburger-btn', 'sidebar-toggle-btn']) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.innerHTML = mark;
    btn.classList.add('titan-toggle-btn');
    btn.setAttribute('aria-label', 'Toggle sidebar');
  }
}

function isLayoutVisible(el) {
  if (!el || el.hidden) return false;
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = el.getBoundingClientRect();
  return rect.height > 0 && rect.width > 0;
}

function resolveAnchor(entry) {
  if (entry.anchorSelector) {
    return document.querySelector(entry.anchorSelector);
  }
  return document.getElementById(entry.anchor);
}

function clearRailNavInlineLayout() {
  document.querySelectorAll('#rail-nav-stack .icon-rail-btn, .rail-tools-scroll .icon-rail-btn').forEach((el) => {
    el.style.marginTop = '';
    el.style.display = '';
  });
  const spacer = document.getElementById('rail-models-spacer');
  if (spacer) {
    spacer.style.display = 'none';
    spacer.style.height = '0';
  }
}

/** Mirror sidebar visibility for optional rail rows; keep uniform row stacking in CSS. */
function syncRailVisibility() {
  clearRailNavInlineLayout();

  for (const entry of RAIL_ROW_ANCHORS) {
    const railEl = document.getElementById(entry.rail);
    const anchor = resolveAnchor(entry);
    if (!railEl || !entry.optional) continue;
    railEl.style.display = anchor && isLayoutVisible(anchor) ? '' : 'none';
  }

  for (const [railId, toolId] of RAIL_TOOL_ANCHORS) {
    const railBtn = document.getElementById(railId);
    const toolRow = document.getElementById(toolId);
    if (!railBtn) continue;
    if (!toolRow || !isLayoutVisible(toolRow)) {
      railBtn.style.display = 'none';
    }
  }
}

function runWithSidebarMeasurable(fn) {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return null;

  const prev = {
    hidden: sidebar.classList.contains('hidden'),
    width: sidebar.style.width,
    visibility: sidebar.style.visibility,
    pointerEvents: sidebar.style.pointerEvents,
    opacity: sidebar.style.opacity,
    position: sidebar.style.position,
    left: sidebar.style.left,
  };

  const needsReveal = prev.hidden || sidebar.offsetWidth === 0;
  if (needsReveal) {
    sidebar.classList.remove('hidden');
    sidebar.style.visibility = 'hidden';
    sidebar.style.pointerEvents = 'none';
    sidebar.style.opacity = '0';
    sidebar.style.position = 'absolute';
    sidebar.style.left = '-10000px';
    sidebar.style.width = sidebar.style.width || 'var(--sidebar-w, 260px)';
  }

  try {
    return fn();
  } finally {
    if (needsReveal) {
      if (prev.hidden) sidebar.classList.add('hidden');
      sidebar.style.visibility = prev.visibility;
      sidebar.style.pointerEvents = prev.pointerEvents;
      sidebar.style.opacity = prev.opacity;
      sidebar.style.position = prev.position;
      sidebar.style.left = prev.left;
      sidebar.style.width = prev.width;
    }
  }
}

/** Measure sidebar inner padding even when sidebar is collapsed. */
function measureSidebarNavMetrics() {
  let metrics = null;
  runWithSidebarMeasurable(() => {
    const inner = document.querySelector('.sidebar-inner');
    const innerPad = inner ? parseFloat(getComputedStyle(inner).paddingTop) || 0 : 10;
    metrics = { innerPad };
  });
  return metrics;
}

/** Place toggle in rail header, or fixed on body when rail+sidebar both hidden. */
function reparentSidebarToggle() {
  const btn = document.getElementById('hamburger-btn');
  const slot = document.querySelector('.rail-header-slot');
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (!btn || !slot || !sidebar || !rail) return;

  const sidebarHidden = sidebar.classList.contains('hidden');
  const railHidden = rail.classList.contains('rail-hidden');
  const railVisible = sidebarHidden && !railHidden && getComputedStyle(rail).display !== 'none';

  btn.classList.remove('hamburger-fixed');

  if (sidebarHidden && railHidden) {
    if (btn.parentElement !== document.body) {
      document.body.insertBefore(btn, document.body.firstChild);
    }
    btn.classList.add('hamburger-fixed');
    return;
  }

  if (railVisible && btn.parentElement !== slot) {
    slot.insertBefore(btn, slot.firstChild);
  } else if (!railVisible && btn.parentElement === document.body && !btn.classList.contains('hamburger-fixed')) {
    slot.insertBefore(btn, slot.firstChild);
  } else if (!sidebarHidden && btn.parentElement !== slot) {
    slot.insertBefore(btn, slot.firstChild);
  }
}

export function syncNavLayout() {
  const metrics = measureSidebarNavMetrics();
  if (metrics) {
    document.documentElement.style.setProperty('--titan-rail-inner-pad', `${metrics.innerPad}px`);
  }
  reparentSidebarToggle();
  syncRailVisibility();
}

export function mountTitanNavChrome() {
  const resync = () => {
    syncNavLayout();
    wrapSidebarIconTiles();
    wrapRailTiles();
    applySidebarNavIcons();
    applyToolSidebarIcons();
    syncRailIconsFromSidebar();
    applyModalHeaderIcons();
  };

  document.querySelectorAll('#icon-rail .icon-rail-btn').forEach((btn) => {
    if (btn.id === 'rail-delete-session' || btn.id === 'rail-documents') return;
    btn.hidden = false;
  });

  applyToggleBrand();
  resync();
  applySidebarBrand();
  applyWelcomeBrand();
  window.applyTitanWelcomeBrand = applyWelcomeBrand;
  mountTitanModalIcons();

  window.syncNavLayout = syncNavLayout;

  window.addEventListener('resize', () => requestAnimationFrame(syncNavLayout));

  const sidebar = document.getElementById('sidebar');
  if (sidebar) {
    const layoutObserver = new MutationObserver(() => {
      requestAnimationFrame(syncNavLayout);
    });
    layoutObserver.observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
  }

  const rail = document.getElementById('icon-rail');
  if (rail) {
    const railObserver = new MutationObserver(() => {
      requestAnimationFrame(syncNavLayout);
    });
    railObserver.observe(rail, { attributes: true, attributeFilter: ['class', 'style'] });
  }

  document.getElementById('hamburger-btn')?.addEventListener('click', () => {
    requestAnimationFrame(resync);
  });
  document.getElementById('sidebar-toggle-btn')?.addEventListener('click', () => {
    requestAnimationFrame(resync);
  });

  requestAnimationFrame(() => requestAnimationFrame(resync));
}
