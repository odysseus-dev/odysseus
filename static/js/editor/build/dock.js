/**
 * Docked panel stack (PhotoCraft-parity phase 0/3).
 *
 * Wraps a list of existing panel elements into a Photoshop-style dock:
 * several panel GROUPS are visible at once, stacked vertically. Each
 * group has clickable tabs (one panel visible per group), a collapse
 * control that shrinks it to its tab strip, and a drag divider so the
 * user can resize the group above.
 *
 * Purely presentational: it never owns editor state. Each entry is an
 * element that already exists elsewhere (so all existing ids, classes
 * and event listeners keep working). Switching a tab only toggles the
 * `hidden` attribute on the panel wrapper.
 *
 * A compact `<select>` variant (`compact: true`) is kept as a fallback
 * for narrow columns where a multi-group stack will not fit.
 *
 * @param {{
 *   entries: Array<{id: string, label: string, element: HTMLElement, group?: string}>,
 *   persistKey?: string,
 *   defaultId?: string,
 *   compact?: boolean,
 * }} opts
 * @returns {{ dock: HTMLDivElement, show: (id: string) => void, readonly activeId: string|null }}
 */
export function buildDock({ entries = [], persistKey = 'ge-dock-active', defaultId = null, compact = false, onCloseGroup = null } = {}) {
  const dock = document.createElement('div');
  dock.className = 'ge-dock';
  if (compact) dock.classList.add('ge-dock--compact');

  // Collect entries into ordered groups (Photoshop panel groups).
  const groups = [];
  entries.forEach(entry => {
    const name = entry.group || 'Panels';
    let group = groups.find(g => g.name === name);
    if (!group) {
      group = { name, entries: [], size: entry.groupSize || null, collapsed: !!entry.groupCollapsed };
      groups.push(group);
    }
    group.entries.push(entry);
  });

  const groupOfId = new Map();
  groups.forEach(group => group.entries.forEach(e => groupOfId.set(e.id, group)));

  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(persistKey) || '{}') || {}; } catch {}
  const activeByGroup = new Map();
  groups.forEach(group => {
    const fallback = group.entries[0]?.id || null;
    const savedId = saved[group.name];
    const pick = (savedId && group.entries.some(e => e.id === savedId)) ? savedId
      : (defaultId && group.entries.some(e => e.id === defaultId)) ? defaultId
      : fallback;
    activeByGroup.set(group.name, pick);
  });

  let activeId = null;
  const persist = () => {
    const out = {};
    activeByGroup.forEach((v, k) => { if (v) out[k] = v; });
    try { localStorage.setItem(persistKey, JSON.stringify(out)); } catch {}
  };

  const groupEls = new Map();
  const detachPlacers = new Map();
  const activateInGroup = (group, id) => {
    activeByGroup.set(group.name, id);
    activeId = id;
    groupEls.get(group.name)?.querySelectorAll('.ge-dock-tab').forEach(tab => {
      const on = tab.dataset.panel === id;
      tab.classList.toggle('active', on);
      tab.setAttribute('aria-selected', String(on));
    });
    group.entries.forEach(entry => {
      const panel = groupEls.get(group.name)?.querySelector(`.ge-dock-panel[data-panel="${entry.id}"]`);
      if (panel) panel.hidden = entry.id !== id;
    });
    // Keep the group's undock control anchored beside the active tab.
    detachPlacers.get(group.name)?.();
    persist();
  };

  function show(id) {
    const group = groupOfId.get(id);
    if (!group) return;
    groupEls.get(group.name)?.classList.remove('is-collapsed');
    activateInGroup(group, id);
  }

  // A dock tab plus a small "undock" control (Photoshop lets you tear a single
  // panel tab out of its group). Used both for the initial build and when a
  // floated panel is re-docked.
  function makeTabItem(group, entry, groupEl) {
    const item = document.createElement('div');
    item.className = 'ge-dock-tab-item';
    item.dataset.panel = entry.id;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'ge-dock-tab';
    tab.dataset.panel = entry.id;
    tab.textContent = entry.label;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', 'false');
    tab.title = entry.label;
    tab.addEventListener('click', () => {
      groupEl.classList.remove('is-collapsed');
      activateInGroup(group, entry.id);
    });
    item.appendChild(tab);
    return item;
  }

  // Float a single panel into its own draggable window (Photoshop undock-tab).
  function floatPanel(group, entry) {
    const groupEl = groupEls.get(group.name);
    if (!groupEl) return;
    const panelEl = groupEl.querySelector(`.ge-dock-panel[data-panel="${entry.id}"]`);
    if (!panelEl || panelEl.closest('.ge-float-window')) return;
    const win = document.createElement('div');
    win.className = 'ge-float-window';
    win.innerHTML = `
      <div class="ge-float-head">
        <span class="ge-float-title">${entry.label}</span>
        <span class="ge-float-btns">
          <button class="ge-float-dock" type="button" title="Dock panel" aria-label="Dock panel">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/></svg>
          </button>
        </span>
      </div>
      <div class="ge-float-body"></div>`;
    win.querySelector('.ge-float-body').appendChild(panelEl);
    panelEl.hidden = false;
    document.body.appendChild(win);
    const r = groupEl.getBoundingClientRect();
    win.style.left = `${Math.max(8, Math.min(window.innerWidth - 240, r.left - 260))}px`;
    win.style.top = `${Math.max(8, r.top)}px`;
    makeFloatDraggable(win, win.querySelector('.ge-float-head'));
    const tabsEl = groupEl.querySelector('.ge-dock-tabs');
    const itemEl = tabsEl?.querySelector(`.ge-dock-tab-item[data-panel="${entry.id}"]`);
    itemEl?.remove();
    const wasActive = activeId === entry.id;
    win.querySelector('.ge-float-dock').addEventListener('click', () => {
      const bodyEl = groupEl.querySelector('.ge-dock-panels');
      if (bodyEl) bodyEl.appendChild(panelEl); else groupEl.appendChild(panelEl);
      panelEl.hidden = activeId !== entry.id;
      win.remove();
      if (tabsEl) tabsEl.appendChild(makeTabItem(group, entry, groupEl));
      if (wasActive) activateInGroup(group, entry.id);
      detachPlacers.get(group.name)?.();
      syncTabsOverflow(groupEl);
    });
    if (wasActive) {
      const next = group.entries.find(e => {
        const p = groupEl.querySelector(`.ge-dock-panel[data-panel="${e.id}"]`);
        return p && !p.closest('.ge-float-window');
      });
      if (next) activateInGroup(group, next.id);
    }
    syncTabsOverflow(groupEl);
  }

  if (compact) {
    const tabbar = document.createElement('div');
    tabbar.className = 'ge-dock-tabbar';
    const select = document.createElement('select');
    select.className = 'ge-dock-select';
    select.setAttribute('aria-label', 'Editor panel');
    const optgroups = new Map();
    entries.forEach(({ id, label, group }) => {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = label;
      if (group) {
        let optgroup = optgroups.get(group);
        if (!optgroup) {
          optgroup = document.createElement('optgroup');
          optgroup.label = group;
          optgroups.set(group, optgroup);
          select.appendChild(optgroup);
        }
        optgroup.appendChild(option);
      } else {
        select.appendChild(option);
      }
    });
    tabbar.appendChild(select);
    const panels = document.createElement('div');
    panels.className = 'ge-dock-panels';
    entries.forEach(({ id, element }) => {
      const panel = document.createElement('div');
      panel.className = 'ge-dock-panel';
      panel.dataset.panel = id;
      if (element) panel.appendChild(element);
      panels.appendChild(panel);
    });
    dock.appendChild(tabbar);
    dock.appendChild(panels);

    const applyFlat = (id) => {
      activeId = id;
      panels.querySelectorAll('.ge-dock-panel').forEach(p => { p.hidden = p.dataset.panel !== id; });
      const group = groupOfId.get(id);
      if (group) activeByGroup.set(group.name, id);
      persist();
    };
    select.addEventListener('change', () => applyFlat(select.value));
    const initial = defaultId || entries[0]?.id || null;
    if (initial) { select.value = initial; applyFlat(initial); }
    return { dock, show, get activeId() { return activeId; } };
  }

  // Stacked mode: one `.ge-dock-group` per panel group, with a drag
  // divider between groups (skip a leading divider).
  groups.forEach((group, groupIndex) => {
    if (groupIndex > 0) {
      const divider = document.createElement('div');
      divider.className = 'ge-dock-divider';
      divider.setAttribute('role', 'separator');
      divider.setAttribute('aria-orientation', 'horizontal');
      divider.title = 'Drag to resize · double-click to expand/collapse a panel group';
      dock.appendChild(divider);
      wireDivider(divider, groupIndex);
    }

    const groupEl = document.createElement('div');
    groupEl.className = 'ge-dock-group';
    groupEl.dataset.group = group.name;
    if (group.size) {
      groupEl.style.flex = `0 0 ${group.size}px`;
      groupEl.style.height = `${group.size}px`;
    }
    if (group.collapsed) groupEl.classList.add('is-collapsed');

    const head = document.createElement('div');
    head.className = 'ge-dock-group-head';

    const tabs = document.createElement('div');
    tabs.className = 'ge-dock-tabs';
    tabs.setAttribute('role', 'tablist');
    group.entries.forEach(entry => {
      tabs.appendChild(makeTabItem(group, entry, groupEl));
    });

    const collapse = document.createElement('button');
    collapse.type = 'button';
    collapse.className = 'ge-dock-collapse';
    collapse.title = 'Collapse panel group';
    collapse.setAttribute('aria-label', 'Collapse panel group');
    collapse.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    collapse.addEventListener('click', () => {
      const collapsed = groupEl.classList.toggle('is-collapsed');
      collapse.title = collapsed ? 'Expand panel group' : 'Collapse panel group';
      collapse.setAttribute('aria-label', collapse.title);
    });

    // Overflow affordance: when a group's tabs do not fit the column,
    // show a chevron that opens a menu of every tab so none become
    // unreachable or unreadable (Photoshop-style tab overflow).
    const tabsMore = document.createElement('button');
    tabsMore.type = 'button';
    tabsMore.className = 'ge-dock-tabs-more';
    tabsMore.title = 'More panels';
    tabsMore.setAttribute('aria-label', 'More panels');
    tabsMore.hidden = true;
    tabsMore.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="6" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="18" cy="12" r="1.9"/></svg>';
    tabsMore.addEventListener('click', (e) => {
      e.stopPropagation();
      openTabsMenu(tabsMore, group, groupEl);
    });


    head.appendChild(tabs);
    head.appendChild(tabsMore);
    head.appendChild(collapse);

    const body = document.createElement('div');
    body.className = 'ge-dock-panels';
    group.entries.forEach(entry => {
      const panel = document.createElement('div');
      panel.className = 'ge-dock-panel';
      panel.dataset.panel = entry.id;
      if (entry.element) panel.appendChild(entry.element);
      body.appendChild(panel);
    });

    groupEl.appendChild(head);
    groupEl.appendChild(body);
    dock.appendChild(groupEl);
    groupEls.set(group.name, groupEl);
    syncTabsOverflow(groupEl);
  });

  // Re-evaluate tab overflow whenever the column width changes, and keep any
  // torn-off (floating) panels on-screen after a viewport resize so they never
  // drift off the edge or vanish.
  window.addEventListener('resize', () => {
    groupEls.forEach(el => syncTabsOverflow(el));
    document.querySelectorAll('.ge-float-window').forEach((w) => {
      const r = w.getBoundingClientRect();
      const maxL = Math.max(8, window.innerWidth - r.width - 8);
      const maxT = Math.max(8, window.innerHeight - 48);
      w.style.left = Math.min(Math.max(8, r.left), maxL) + 'px';
      w.style.top = Math.min(Math.max(8, r.top), maxT) + 'px';
    });
  });

  // The dock is built before it is attached to the DOM (so its width is 0 at
  // build time and the overflow check above cannot see the real column width).
  // Observe size changes to recompute overflow once it is laid out and whenever
  // the panel is resized, collapsed or expanded — otherwise a tab strip that
  // overflows keeps the chevron hidden and clips its trailing labels.
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => groupEls.forEach(el => syncTabsOverflow(el)));
    ro.observe(dock);
  }
  requestAnimationFrame(() => groupEls.forEach(el => syncTabsOverflow(el)));

  function syncTabsOverflow(groupEl) {
    const tabsEl = groupEl.querySelector('.ge-dock-tabs');
    const moreEl = groupEl.querySelector('.ge-dock-tabs-more');
    if (!tabsEl || !moreEl) return;
    const items = Array.from(tabsEl.querySelectorAll('.ge-dock-tab-item'));
    // Reset any previous hiding so widths are measured at their natural size.
    items.forEach(item => { item.style.display = ''; });
    const overflowing = tabsEl.scrollWidth - tabsEl.clientWidth > 1;
    // Keep the menu reachable even when the tabs fit, so its "Close <group>"
    // action (and every tab) stays available for every section.
    moreEl.hidden = !(overflowing || typeof onCloseGroup === 'function');
    if (!overflowing) return;
    // Photoshop-style tab overflow: rather than clipping a label mid-word
    // ("PATTERNS" -> "P…", "CHARACTER" -> "CHARA"), keep only the tabs that
    // fit whole and let the chevron expose the rest. The active tab is always
    // kept visible so the current panel never loses its anchor.
    const active = tabsEl.querySelector('.ge-dock-tab.active');
    const activeItem = active ? active.closest('.ge-dock-tab-item') : null;
    const limit = tabsEl.clientWidth;
    let gap = 0;
    try { gap = parseFloat(getComputedStyle(tabsEl).columnGap) || 0; } catch {}
    // Keep the leading run of tabs that fits, then tuck the trailing tabs
    // (and everything after the first that no longer fits) into the chevron.
    // The active tab is always kept visible so the current panel stays anchored.
    let used = 0;
    let cutting = false;
    items.forEach(item => {
      const w = item.getBoundingClientRect().width;
      if (item === activeItem || (!cutting && used + w <= limit + 0.5)) {
        used += w + gap;
      } else {
        cutting = true;
        item.style.display = 'none';
      }
    });
  }

  let tabsMenu = null;
  function closeTabsMenu() {
    if (!tabsMenu) return;
    tabsMenu.remove();
    tabsMenu = null;
    document.removeEventListener('pointerdown', onTabsMenuDocDown, true);
  }
  function onTabsMenuDocDown(e) {
    if (tabsMenu && !tabsMenu.contains(e.target) && !e.target.closest?.('.ge-dock-tabs-more')) closeTabsMenu();
  }
  function openTabsMenu(anchor, group, groupEl) {
    closeTabsMenu();
    tabsMenu = document.createElement('div');
    tabsMenu.className = 'ge-dock-tabs-menu';
    group.entries.forEach(entry => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'ge-dock-tabs-menu-item';
      item.textContent = entry.label;
      item.addEventListener('click', () => {
        groupEl.classList.remove('is-collapsed');
        activateInGroup(group, entry.id);
        syncTabsOverflow(groupEl);
        closeTabsMenu();
      });
      tabsMenu.appendChild(item);
    });
    // Close the whole section (panel group) from the same menu — Photoshop's
    // "Close Tab Group". Only offered when the host supplies a handler.
    if (typeof onCloseGroup === 'function') {
      const sep = document.createElement('div');
      sep.className = 'ge-dock-tabs-menu-sep';
      tabsMenu.appendChild(sep);
      const closeItem = document.createElement('button');
      closeItem.type = 'button';
      closeItem.className = 'ge-dock-tabs-menu-item ge-dock-tabs-menu-close';
      closeItem.textContent = `Close ${group.name}`;
      closeItem.addEventListener('click', () => {
        closeTabsMenu();
        try { onCloseGroup(group.name); } catch (_) {}
      });
      tabsMenu.appendChild(closeItem);
    }
    document.body.appendChild(tabsMenu);
    const r = anchor.getBoundingClientRect();
    tabsMenu.style.left = `${Math.max(4, Math.min(window.innerWidth - 160, r.left))}px`;
    tabsMenu.style.top = `${r.bottom + 2}px`;
    document.addEventListener('pointerdown', onTabsMenuDocDown, true);
  }

  function wireDivider(divider, groupIndex) {
    const groupEl = () => dock.querySelectorAll('.ge-dock-group')[groupIndex - 1];
    let startY = 0;
    let startH = 0;
    let resizing = false;
    let moved = false;
    divider.addEventListener('pointerdown', (event) => {
      const target = groupEl();
      if (!target) return;
      resizing = true;
      moved = false;
      startY = event.clientY;
      startH = target.getBoundingClientRect().height;
      divider.setPointerCapture(event.pointerId);
      document.body.style.cursor = 'ns-resize';
      event.preventDefault();
    });
    divider.addEventListener('pointermove', (event) => {
      if (!resizing) return;
      const target = groupEl();
      if (!target) return;
      // A real drag supersedes any double-click expand state (a plain click,
      // which a double-click fires, must not, or the toggle would never fire).
      if (!moved) { moved = true; target.dataset.dockExpanded = '0'; target.classList.remove('is-expanded'); }
      const next = Math.max(28, startH + (event.clientY - startY));
      // Fixed basis (no grow/shrink): a `1 1` basis lets the height-constrained
      // dock shrink the dragged group straight back to 0, so the divider looked
      // dead. Keeping it `0 0` holds the size and lets the dock scroll instead.
      target.style.flex = `0 0 ${Math.round(next)}px`;
      target.style.height = `${Math.round(next)}px`;
    });
    const end = (event) => {
      if (!resizing) return;
      resizing = false;
      document.body.style.cursor = '';
      try { divider.releasePointerCapture?.(event.pointerId); } catch {}
    };
    divider.addEventListener('pointerup', end);
    divider.addEventListener('pointercancel', end);
    // Double-click toggles the group above between "show everything" and the
    // previous size. Handy on a cramped sidebar: one double-click unrolls the
    // whole group (its body is normally height-clamped and scrolls), another
    // restores exactly what it was.
    divider.addEventListener('dblclick', (event) => {
      event.preventDefault();
      const target = groupEl();
      if (!target) return;
      const body = target.querySelector('.ge-dock-panels');
      if (target.dataset.dockExpanded === '1') {
        const prev = parseFloat(target.dataset.dockPrevSize || '');
        const restore = Number.isFinite(prev) && prev > 0 ? prev : 200;
        target.style.flex = `0 0 ${Math.round(restore)}px`;
        target.style.height = `${Math.round(restore)}px`;
        target.dataset.dockExpanded = '0';
        target.classList.remove('is-expanded');
        return;
      }
      const head = target.querySelector('.ge-dock-tabs');
      const headH = head ? head.getBoundingClientRect().height : 0;
      const bodyH = body ? body.scrollHeight : 0;
      const full = Math.ceil(headH + bodyH + 2);
      target.dataset.dockPrevSize = String(Math.round(target.getBoundingClientRect().height));
      target.classList.remove('is-collapsed');
      target.style.flex = `0 0 ${full}px`;
      target.style.height = `${full}px`;
      target.dataset.dockExpanded = '1';
      target.classList.add('is-expanded');
    });
  }

  // Move a panel group out of the dock into a floating, draggable
  // window; the window offers a "Dock" control that returns the exact
  // same `.ge-dock-group` node (DOM identity preserved) to its original
  // position in the stack.
  function detachGroup(group, groupEl, dockEl) {
    if (groupEl.classList.contains('is-floating')) return;
    const prevDivider = groupEl.previousElementSibling;
    const hadDivider = !!prevDivider && prevDivider.classList.contains('ge-dock-divider');
    if (hadDivider) prevDivider.hidden = true;
    const dockIndex = Array.from(dockEl.querySelectorAll('.ge-dock-group')).indexOf(groupEl);
    groupEl.classList.add('is-floating');
    const win = document.createElement('div');
    win.className = 'ge-float-window';
    win.dataset.group = group.name;
    win.innerHTML = `
      <div class="ge-float-head">
        <span class="ge-float-title">${group.name}</span>
        <span class="ge-float-btns">
          <button class="ge-float-dock" type="button" title="Dock panel group" aria-label="Dock panel group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/></svg>
          </button>
        </span>
      </div>
      <div class="ge-float-body"></div>`;
    win.querySelector('.ge-float-body').appendChild(groupEl);
    document.body.appendChild(win);
    const r = groupEl.getBoundingClientRect();
    win.style.left = `${Math.max(8, Math.min(window.innerWidth - 260, r.left - 60))}px`;
    win.style.top = `${Math.max(8, r.top)}px`;
    makeFloatDraggable(win, win.querySelector('.ge-float-head'));
    win.querySelector('.ge-float-dock').addEventListener('click', () => {
      const groupsNow = Array.from(dockEl.querySelectorAll('.ge-dock-group'));
      const anchor = groupsNow[dockIndex] || null;
      if (anchor) dockEl.insertBefore(groupEl, anchor); else dockEl.appendChild(groupEl);
      if (hadDivider) prevDivider.hidden = false;
      groupEl.classList.remove('is-floating');
      win.remove();
    });
  }

  function makeFloatDraggable(win, handle) {
    if (!handle) return;
    handle.style.touchAction = 'none';
    handle.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      e.preventDefault();
      const startX = e.clientX, startY = e.clientY;
      const r0 = win.getBoundingClientRect();
      try { handle.setPointerCapture(e.pointerId); } catch {}
      handle.style.cursor = 'grabbing';
      const onMove = (ev) => {
        win.style.left = `${Math.max(0, Math.min(window.innerWidth - 80, r0.left + (ev.clientX - startX)))}px`;
        win.style.top = `${Math.max(0, Math.min(window.innerHeight - 30, r0.top + (ev.clientY - startY)))}px`;
      };
      const onUp = () => {
        try { handle.releasePointerCapture(e.pointerId); } catch {}
        handle.style.cursor = '';
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
    });
  }

  groupEls.forEach((groupEl, name) => {
    const group = groups.find(g => g.name === name);
    const id = activeByGroup.get(name) || group?.entries[0]?.id;
    if (id) activateInGroup(group, id);
  });
  return {
    dock,
    show,
    get activeId() { return activeId; },
  };
}
