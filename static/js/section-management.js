// ============================================
// Section Management — collapse/expand + drag reorder
// ============================================

import i18n from './i18n.js';

/**
 * Initialize section collapse/expand with chevron buttons.
 * @param {Object} Storage - Storage module
 */
export function initSectionCollapse(Storage) {
  const _chevronHtml = `<button type="button" class="section-collapse-btn" title="${i18n.t('sidebar.collapse_section')}"><svg class="section-collapse-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>`;
  const savedState = Storage.getJSON('section-collapsed') || {};

  document.querySelectorAll('.section .section-header-flex').forEach(header => {
    const section = header.closest('.section');
    if (!section || !section.id) return;

    // Skip email section — it doesn't collapse (title opens popup instead)
    if (section.id === 'email-section') return;

    // Add chevron (always visible — rotates when collapsed)
    header.insertAdjacentHTML('beforeend', _chevronHtml);

    // Restore saved state
    if (savedState[section.id]) {
      section.classList.add('collapsed');
    }

    function toggleCollapse() {
      const wasCollapsed = section.classList.contains('collapsed');
      const willCollapse = !wasCollapsed;
      const state = Storage.getJSON('section-collapsed') || {};
      state[section.id] = willCollapse;
      Storage.setJSON('section-collapsed', state);

      // Always clear any in-flight animation classes from a previous toggle
      // so back-to-back clicks restart cleanly.
      section.classList.remove('section-just-expanded', 'section-just-collapsing');

      if (willCollapse) {
        // Domino-out: play the fade/slide-down on .list-item children
        // BEFORE actually adding .collapsed (which hides them via
        // display:none). After the cascade finishes, lock in collapse.
        // Force reflow so the keyframes restart.
        // eslint-disable-next-line no-unused-expressions
        section.offsetHeight;
        section.classList.add('section-just-collapsing');
        const itemCount = Math.min(12, section.querySelectorAll('.list-item').length);
        const total = itemCount * 25 + 230; // matches CSS keyframes + stagger
        setTimeout(() => {
          section.classList.remove('section-just-collapsing');
          section.classList.add('collapsed');
        }, total);
      } else {
        // Expand path — already had this: remove .collapsed and replay
        // the inbound domino.
        section.classList.remove('collapsed');
        // eslint-disable-next-line no-unused-expressions
        section.offsetHeight;
        section.classList.add('section-just-expanded');
        setTimeout(() => section.classList.remove('section-just-expanded'), 700);
      }
    }

    // Click title to collapse/expand
    const title = header.querySelector('h4') || header.querySelector('.section-title');
    if (title) {
      title.style.cursor = 'pointer';
      title.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleCollapse();
      });
    }

    // Click chevron button
    const chevronBtn = header.querySelector('.section-collapse-btn');
    if (chevronBtn) {
      chevronBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleCollapse();
      });
    }

    // Click anywhere on collapsed section to expand
    section.addEventListener('click', (e) => {
      if (!section.classList.contains('collapsed')) return;
      if (e.target.closest('button, select, .dropdown')) return;
      e.stopPropagation();
      toggleCollapse();
    });
  });
}

/**
 * Initialize section drag reorder (mouse-based, desktop only).
 * @param {Object} Storage - Storage module
 * @param {Function} loadUIVis - Function that returns UI visibility state
 */
export function initSectionDrag(Storage, loadUIVis) {
  const sidebar = document.getElementById('sidebar');
  const sidebarInner = sidebar ? sidebar.querySelector('.sidebar-inner') : null;
  if (!sidebarInner) return;

  // Disable draggable on mobile to prevent scroll interference
  if ('ontouchstart' in window) {
    document.querySelectorAll('.section[draggable]').forEach(s => {
      s.setAttribute('draggable', 'false');
    });
  }

  let draggedSection = null;
  let placeholder = null;
  let offsetY = 0;

  function getSections() {
    return Array.from(sidebar.querySelectorAll('.section[draggable="true"]'));
  }

  function onMouseDown(e) {
    if (!e.target.classList.contains('drag-handle')) return;

    // Check if drag reorder is enabled
    const uiState = loadUIVis();
    if (uiState['section-drag-reorder'] === false) return;

    const section = e.target.closest('.section[draggable="true"]');
    if (!section) return;

    e.preventDefault();

    const rect = section.getBoundingClientRect();
    offsetY = e.clientY - rect.top;
    draggedSection = section;

    // Create placeholder
    placeholder = document.createElement('div');
    placeholder.className = 'section-placeholder';
    placeholder.style.cssText = `
      height: ${rect.height}px;
      margin: 4px 0;
      border: 2px dashed rgba(0, 170, 255, 0.5);
      border-radius: 8px;
      background: rgba(0, 170, 255, 0.1);
    `;
    section.parentNode.insertBefore(placeholder, section);

    // Float the section
    Object.assign(section.style, {
      position: 'fixed',
      width: rect.width + 'px',
      left: rect.left + 'px',
      top: rect.top + 'px',
      zIndex: '9999',
      opacity: '0.95',
      boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
      pointerEvents: 'none',
      transition: 'none'
    });

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  function onMouseMove(e) {
    if (!draggedSection) return;

    // Only move vertically - horizontal stays locked
    draggedSection.style.top = (e.clientY - offsetY) + 'px';

    const sections = getSections().filter(s => s !== draggedSection);
    const dragRect = draggedSection.getBoundingClientRect();
    const dragTop = dragRect.top;

    let insertBefore = null;

    // Find which section we should go before
    for (let i = 0; i < sections.length; i++) {
      const section = sections[i];
      const rect = section.getBoundingClientRect();

      // If our top edge is above this section's bottom, we go before it
      if (dragTop < rect.bottom - 10) {
        insertBefore = section;
        break;
      }
    }

    // Move placeholder
    if (insertBefore) {
      if (placeholder.nextElementSibling !== insertBefore) {
        sidebarInner.insertBefore(placeholder, insertBefore);
      }
    } else if (sections.length > 0) {
      const lastSection = sections[sections.length - 1];
      if (placeholder !== lastSection.nextElementSibling) {
        sidebarInner.insertBefore(placeholder, lastSection.nextElementSibling);
      }
    }
  }

  function onMouseUp() {
    if (!draggedSection) return;

    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);

    // Snap to placeholder - fast!
    const phRect = placeholder.getBoundingClientRect();
    draggedSection.style.transition = 'top 0.08s ease-out';
    draggedSection.style.top = phRect.top + 'px';

    setTimeout(() => {
      placeholder.parentNode.insertBefore(draggedSection, placeholder);
      placeholder.remove();
      draggedSection.style.cssText = '';

      // Save order
      const ids = getSections().map(s => s.id).filter(Boolean);
      Storage.setJSON(Storage.KEYS.SECTION_ORDER, ids);

      draggedSection = null;
      placeholder = null;
    }, 80);
  }

  sidebar.addEventListener('mousedown', onMouseDown);

  // Restore saved order on load
  try {
    const saved = Storage.get(Storage.KEYS.SECTION_ORDER);
    if (saved) {
      const order = JSON.parse(saved);
      order.forEach(id => {
        const section = document.getElementById(id);
        if (section) sidebarInner.appendChild(section);
      });
    }
  } catch (e) {}
}
