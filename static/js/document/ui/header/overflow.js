// frontend/src/features/documents/ui/header/overflow.js
/**
 * Document action overflow and recent-action order.
 */

import { showExportMenu } from '../exportDocx.js';

export let _syncOverflow = null;

const _DOC_RECENTS_KEY = 'odysseus-doc-actions-recent';

const _DOC_MAX_VISIBLE = 2;

function _getDocRecent() {
    try { return JSON.parse(localStorage.getItem(_DOC_RECENTS_KEY) || '[]'); } catch { return []; }
  }

function _trackDocAction(id) {
    let recent = _getDocRecent().filter(x => x !== id);
    recent.unshift(id);
    if (recent.length > 10) recent.length = 10;
    localStorage.setItem(_DOC_RECENTS_KEY, JSON.stringify(recent));
  }

function initActionOverflow() {
    const actionsEl = document.getElementById('doc-editor-actions');
    const wrapper = document.getElementById('doc-overflow-wrapper');
    const toggle = document.getElementById('doc-overflow-toggle');
    const menu = document.getElementById('doc-overflow-menu');
    if (!actionsEl || !wrapper || !toggle || !menu) return;

    const allBtns = Array.from(actionsEl.querySelectorAll('.doc-collapsible-btn'));
    let _menuOpen = false;

    function syncOverflow() {
      allBtns.forEach(b => { b.classList.remove('doc-collapsed'); });
      menu.innerHTML = '';

      // Filter to currently visible buttons
      const available = allBtns.filter(b => b.style.display !== 'none');

      // Sort by recent usage, defaults: copy, export, save
      const recent = _getDocRecent();
      const defaults = ['doc-copy-btn', 'doc-export-btn', 'doc-save-btn'];
      const order = recent.length > 0 ? recent : defaults;

      // Auto-pin: md preview when language is markdown
      const lang = document.getElementById('doc-language-select')?.value;
      const pinned = [];
      if (lang === 'markdown') {
        const mdBtn = available.find(b => b.id === 'doc-md-btn');
        if (mdBtn) pinned.push(mdBtn);
      }

      const sorted = [...available].sort((a, b) => {
        const ai = order.indexOf(a.id), bi = order.indexOf(b.id);
        if (ai >= 0 && bi >= 0) return ai - bi;
        if (ai >= 0) return -1;
        if (bi >= 0) return 1;
        return 0;
      });

      // Pinned + top N (deduplicated) — pinned count against the max
      const visible = [...pinned];
      for (const btn of sorted) {
        if (visible.length >= _DOC_MAX_VISIBLE) break;
        if (!visible.includes(btn)) visible.push(btn);
      }
      // Ensure we never exceed MAX_VISIBLE
      while (visible.length > _DOC_MAX_VISIBLE) visible.pop();
      const overflow = sorted.filter(b => !visible.includes(b));

      // Show visible, hide overflow
      overflow.forEach(b => b.classList.add('doc-collapsed'));

      // Reorder DOM: visible buttons before wrapper
      for (const btn of visible) {
        actionsEl.insertBefore(btn, wrapper);
      }

      if (overflow.length > 0) {
        wrapper.style.display = '';
        overflow.forEach(btn => {
          const item = document.createElement('button');
          item.className = 'doc-overflow-item';
          item.innerHTML = btn.innerHTML + '<span>' + (btn.title || '') + '</span>';
          item.addEventListener('click', (e) => {
            _trackDocAction(btn.id);
            // Export button has its own submenu
            if (btn.id === 'doc-export-btn') {
              e.stopPropagation();
              const savedRect = item.getBoundingClientRect();
              closeMenu();
              setTimeout(() => showExportMenu(null, savedRect), 50);
              return;
            }
            closeMenu();
            btn.click();
            syncOverflow(); // re-sort with new recency
          });
          menu.appendChild(item);
        });
      } else {
        wrapper.style.display = 'none';
      }
    }

    function closeMenu() {
      _menuOpen = false;
      menu.classList.remove('open');
    }

    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      _menuOpen = !_menuOpen;
      if (_menuOpen) {
        // Move to body to escape overflow:hidden on doc-editor-pane
        document.body.appendChild(menu);
        const rect = toggle.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.top = (rect.bottom + 2) + 'px';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.style.left = 'auto';
      } else {
        wrapper.appendChild(menu);
      }
      menu.classList.toggle('open', _menuOpen);
    });
    document.addEventListener('click', () => {
      if (_menuOpen) { closeMenu(); wrapper.appendChild(menu); }
    });

    // Also track when visible buttons are clicked directly
    allBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        _trackDocAction(btn.id);
        // Defer re-sort so the click handler fires first
        setTimeout(syncOverflow, 100);
      });
    });

    requestAnimationFrame(syncOverflow);
    _syncOverflow = syncOverflow;
  }
