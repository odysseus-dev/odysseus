/**
 * Photoshop-style nested flyout menus for the editor topbar.
 *
 * A `.ge-menu-parent` row reveals its sibling `.ge-submenu` panel. Hover is
 * handled in CSS; this wires the click toggle (and aligns the flyout with the
 * clicked row). The children stay in the same menu subtree, so the existing
 * delegated `[data-*-action]` handlers keep firing.
 *
 * Shared by the Image / Filter / Selection menus (wire-topbar-menus.js) and
 * the Window > Workspace submenu (wire-extra-menus.js).
 */
export function wireSubmenuToggles(menu) {
  menu.addEventListener('mouseover', (e) => {
    if (e.target.closest('.ge-menu-parent, .ge-submenu')) {
      menu.classList.add('ge-flyout-open');
    }
  });
  menu.addEventListener('mouseleave', () => {
    if (!menu.querySelector('.ge-menu-parent.open')) {
      menu.classList.remove('ge-flyout-open');
    }
  });
  function positionPanel(parent) {
    const panel = parent.nextElementSibling;
    if (!panel?.classList.contains('ge-submenu')) return;
    const row = parent.getBoundingClientRect();
    const origin = (panel.offsetParent || menu).getBoundingClientRect();
    const top = Math.max(8, Math.min(row.top, window.innerHeight - panel.offsetHeight - 8));
    const left = row.right + panel.offsetWidth > window.innerWidth - 8
      ? Math.max(8, row.left - panel.offsetWidth) : row.right - 2;
    panel.style.top = `${top - origin.top + (panel.offsetParent?.scrollTop || 0)}px`;
    panel.style.left = `${left - origin.left + (panel.offsetParent?.scrollLeft || 0)}px`;
  }
  for (const eventName of ['pointerover', 'focusin']) {
    menu.addEventListener(eventName, (event) => {
      const parent = event.target.closest('.ge-menu-parent');
      if (parent) positionPanel(parent);
    });
  }
  menu.addEventListener('click', (e) => {
    const parent = e.target.closest('.ge-menu-parent');
    if (!parent) return;
    e.stopPropagation();
    const willOpen = !parent.classList.contains('open');
    menu.querySelectorAll('.ge-menu-parent.open').forEach((other) => {
      if (other === parent) return;
      other.classList.remove('open');
      other.setAttribute('aria-expanded', 'false');
    });
    parent.classList.toggle('open', willOpen);
    parent.setAttribute('aria-expanded', String(willOpen));
    if (willOpen) {
      // Align the flyout with the clicked row rather than the menu top.
      positionPanel(parent);
    }
  });
}
