// shadcn.js — Expressive micro-interaction layer for the shadcn theme.
// Runs ONLY under a shadcn theme; idempotent; attaches a single delegated
// pointermove listener that feeds cursor coordinates to the spotlight cards,
// and detaches cleanly when the user switches to a non-shadcn theme.
//
// Everything else in the shadcn skin is pure CSS (shadcn.css). This file adds
// only the one thing CSS can't do alone: track the cursor inside cards so the
// monochrome spotlight border/glow follows it.
(function () {
  function isShadcn() {
    return (document.documentElement.dataset.theme || '').indexOf('shadcn') === 0;
  }

  // The card surfaces that opt into the spotlight (must match shadcn.css).
  var SPOT_SELECTOR = '.admin-card, .gallery-card, .memory-item, .doclib-card';

  // One delegated handler for the whole document — auto-covers cards added
  // later by the SPA (no re-binding needed). Cheap: a closest() lookup + one
  // getBoundingClientRect + two setProperty calls, only while a card is hovered.
  function onPointerMove(e) {
    var card = e.target.closest && e.target.closest(SPOT_SELECTOR);
    if (!card) return;
    var r = card.getBoundingClientRect();
    card.style.setProperty('--shad-mx', (e.clientX - r.left) + 'px');
    card.style.setProperty('--shad-my', (e.clientY - r.top) + 'px');
  }

  var _bound = false;
  function bind() {
    if (_bound) return;
    document.addEventListener('pointermove', onPointerMove, { passive: true });
    _bound = true;
  }
  function unbind() {
    if (!_bound) return;
    document.removeEventListener('pointermove', onPointerMove, { passive: true });
    _bound = false;
  }

  function syncTheme() {
    if (isShadcn()) bind();
    else unbind();
  }

  function start() {
    syncTheme();
    try {
      // Re-evaluate on runtime theme switches (theme.js writes data-theme on <html>).
      new MutationObserver(syncTheme).observe(document.documentElement, {
        attributes: true, attributeFilter: ['data-theme'],
      });
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
