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
      const panel = parent.nextElementSibling;
      if (panel && panel.classList.contains('ge-submenu')) {
        panel.style.top = parent.offsetTop + 'px';
      }
    }
  });
}
