/** Shared mobile bottom-sheet helpers (Notes, FormFlow, etc.). */

export function isMobileSheet() {
  return window.innerWidth <= 768;
}

export function collapseSidebarForMobileSheet() {
  if (!isMobileSheet()) return;
  const sb = document.getElementById('sidebar');
  if (sb) sb.classList.add('hidden');
  document.body.classList.add('sidebar-collapsed');
}

/**
 * Finger-following swipe-down on grabber/header to dismiss a bottom sheet.
 * @param {HTMLElement} el - touch target (grabber or header)
 * @param {HTMLElement} pane - sheet element to translate
 * @param {() => void} onDismiss - called after slide-off (e.g. closePanel('down'))
 */
export function wireSwipeDismiss(el, pane, onDismiss) {
  if (!el || !pane || typeof onDismiss !== 'function') return;
  const DISMISS_THRESHOLD = 50;
  const VELOCITY_THRESHOLD = 0.3;
  const RUBBER = 0.35;
  let startY = 0;
  let startX = 0;
  let lastY = 0;
  let lastT = 0;
  let velocity = 0;
  let dragging = false;
  let cancelled = false;

  el.addEventListener('touchstart', (e) => {
    if (!isMobileSheet() || e.touches.length !== 1) return;
    if (e.target.closest('button, input, select, label, textarea')) return;
    const t = e.touches[0];
    startY = t.clientY;
    startX = t.clientX;
    lastY = startY;
    lastT = e.timeStamp;
    velocity = 0;
    dragging = false;
    cancelled = false;
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (cancelled || !isMobileSheet()) return;
    const t = e.touches[0];
    const dx = Math.abs(t.clientX - startX);
    const dy = t.clientY - startY;
    if (!dragging) {
      if (dx > 40 && dx > Math.abs(dy) * 2) {
        cancelled = true;
        return;
      }
      if (Math.abs(dy) > 8) {
        dragging = true;
        pane.style.animation = 'none';
        pane.style.transition = 'none';
        pane.style.willChange = 'transform';
      } else {
        return;
      }
    }
    const dt = e.timeStamp - lastT;
    if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
    lastY = t.clientY;
    lastT = e.timeStamp;
    e.preventDefault();
    pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER}px)`;
  }, { passive: false });

  const endSwipe = () => {
    if (!dragging) return;
    dragging = false;
    pane.style.willChange = '';
    const dy = lastY - startY;
    if (dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD)) {
      pane.style.transition = 'transform 0.2s cubic-bezier(0.2, 0, 0.4, 1)';
      pane.style.transform = 'translateY(100%)';
      setTimeout(onDismiss, 200);
    } else {
      pane.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
      pane.style.transform = '';
      setTimeout(() => { pane.style.transition = ''; }, 260);
    }
  };
  el.addEventListener('touchend', endSwipe, { passive: true });
  el.addEventListener('touchcancel', endSwipe, { passive: true });
}
