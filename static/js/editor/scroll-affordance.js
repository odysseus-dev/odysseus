// Edge-fade affordance for horizontally scrolling bars (audit #5).
//
// On phones the editor's menu row (.ge-topbar-left) and tool rail (.ge-toolbar)
// scroll sideways with their scrollbars hidden, so they look static and people
// miss the off-screen File…Help menus and tools. This mirrors the doc-tab
// behaviour (static/js/document.js updateArrowVisibility): toggle is-at-left /
// is-at-right so the CSS mask gradient only fades the side that still has
// content to scroll to.
export function attachScrollAffordance(el) {
  if (!el) return () => {};
  if (el.__geScrollAffordance) return el.__geScrollAffordance;
  el.classList.add('ge-scroll-fade');

  const update = () => {
    const max = el.scrollWidth - el.clientWidth;
    const atLeft = el.scrollLeft <= 1;
    const atRight = el.scrollLeft >= max - 1;
    el.classList.toggle('is-at-left', atLeft);
    el.classList.toggle('is-at-right', atRight);
  };

  el.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  let ro = null;
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(update);
    ro.observe(el);
  }
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(update);
  else update();

  const detach = () => {
    el.removeEventListener('scroll', update);
    window.removeEventListener('resize', update);
    if (ro) ro.disconnect();
    el.classList.remove('ge-scroll-fade', 'is-at-left', 'is-at-right');
    delete el.__geScrollAffordance;
  };
  el.__geScrollAffordance = detach;
  return detach;
}
