// ============================================
// Section Management — collapse/expand + drag reorder
// ============================================

/**
 * Initialize section collapse/expand with chevron buttons.
 * @param {Object} Storage - Storage module
 */
export function initSectionCollapse(Storage) {
  const _chevronHtml = '<button type="button" class="section-collapse-btn" title="Collapse section"><svg class="section-collapse-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>';
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

    // Accordion: animate the section's height between its open and collapsed
    // sizes so the rows below glide instead of snapping. The target state is
    // tracked separately from the .collapsed class, so a click mid-animation
    // reverses smoothly from the current height.
    function toggleCollapse() {
      const target = section._collapseTarget ?? section.classList.contains('collapsed');
      const willCollapse = !target;
      section._collapseTarget = willCollapse;
      const state = Storage.getJSON('section-collapsed') || {};
      state[section.id] = willCollapse;
      Storage.setJSON('section-collapsed', state);

      section._heightAnim?.cancel();
      const gen = (section._collapseGen = (section._collapseGen || 0) + 1);
      const from = section.getBoundingClientRect().height;
      section.classList.toggle('collapsed', willCollapse);
      const to = section.getBoundingClientRect().height;
      const settle = () => {
        if (section._collapseGen !== gen) return; // a newer click owns the section now
        section.classList.toggle('collapsed', willCollapse);
        section.classList.remove('will-collapse');
        section.style.overflow = '';
        section._collapseTarget = undefined;
      };
      if (from === to || matchMedia('(prefers-reduced-motion: reduce)').matches) { settle(); return; }

      // Keep the rows rendered while the section shrinks; lock in at the end.
      if (willCollapse) section.classList.remove('collapsed');
      section.classList.toggle('will-collapse', willCollapse);
      section.style.overflow = 'hidden';
      const timing = { duration: 200, easing: 'cubic-bezier(.2, .8, .2, 1)' };
      const anim = section._heightAnim = section.animate({ height: [`${from}px`, `${to}px`] }, timing);
      for (const row of section.children) {
        if (!row.classList.contains('section-header-flex')) {
          row.animate({ opacity: willCollapse ? [1, 0] : [0, 1] }, timing);
        }
      }
      // Commit on finish, with a timer as the safety net: if the finish event
      // never arrives (cancelled, or a background tab that isn't rendering),
      // the stale target would make the next click toggle the wrong way.
      anim.onfinish = settle;
      setTimeout(settle, timing.duration + 50);
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

    // Chats can contain hundreds of rows, so provide a sticky collapse action
    // at the end of that list. Reuse the same handler/state as the header
    // chevron instead of creating a second persistence path.

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
