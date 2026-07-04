// tourAutoplay.js — auto-fires the matching `/tour-<x>` slash command the
// first time the user opens a tool modal. One-shot per modal: dismissed or
// not, the marker is set so reopens never auto-trigger again.
//
// Pairs with the existing tourHints.js (which shows a single global "drag
// title bar to snap" hint). Tours are richer per-feature walkthroughs.
//
// Mobile is excluded — tours position halos by rect math that doesn't fit
// the bottom-sheet layout cleanly.

import { handleSlashCommand } from './slashCommands.js';

// Modal id → slash command to fire (without the leading "/"). Add to this
// map when a new feature picks up a `tour-*` command.
const TOUR_FOR_MODAL = {
  'doclib-modal':           'tour-library',
  'cookbook-modal':         'tour-cookbook',
  'research-overlay':       'tour-research',
  'compare-model-overlay':  'tour-compare',
  'theme-modal':            'tour-theme',
  'settings-modal':         'tour-settings',
  'gallery-modal':          'tour-gallery',
};

const SEEN_KEY = (tour) => `void-tour-autoplay-seen-${tour}`;

let _initialized = false;
// Suppress re-fire if a tour is already active or another modal opens while
// we're mid-tour. The slash command itself adds `body.tour-active` for the
// duration of its halos.
function _tourActive() {
  return document.body.classList.contains('tour-active');
}

function _isVisible(el) {
  if (!el || el.classList.contains('hidden')) return false;
  if (el.style.display === 'none') return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

async function _maybeFire(modal) {
  const id = modal.id;
  const tour = TOUR_FOR_MODAL[id];
  if (!tour) return;
  if (_tourActive()) {
    try { window.cancelActiveTour?.('modal-opened'); } catch (_) {}
    return;
  }
  let seen = false;
  try { seen = localStorage.getItem(SEEN_KEY(tour)) === '1'; } catch (_) {}
  if (seen) return;
  // Mark immediately so a quick double-trigger (e.g. modal-class observer
  // fires twice during animation) can't queue two tours.
  try { localStorage.setItem(SEEN_KEY(tour), '1'); } catch (_) {}
  // Let the modal's own enter-animation settle before halos try to position
  // off the title bar / first card / etc. ~400ms matches tourHints.
  setTimeout(() => {
    if (_tourActive()) return;
    try {
      handleSlashCommand('/' + tour);
    } catch (e) {
      // If firing fails we don't unmark — re-attempting on every modal open
      // would be more annoying than a missed tour. User can run `/tour-x`
      // manually from the chat input.
      // eslint-disable-next-line no-console
      console.warn(`Tour autoplay failed for ${id}:`, e);
    }
  }, 400);
}

function _watchModals() {
  if (typeof MutationObserver === 'undefined') return;
  const observer = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.attributeName !== 'class' && m.attributeName !== 'style') continue;
      const el = m.target;
      if (!(el instanceof HTMLElement)) continue;
      if (!(el.id in TOUR_FOR_MODAL)) continue;
      const wasHidden = !m.oldValue
        || /\bhidden\b/.test(m.oldValue)
        || /display:\s*none/.test(m.oldValue);
      if (wasHidden && _isVisible(el)) _maybeFire(el);
    }
  });
  // Observe each known target if it exists at boot…
  Object.keys(TOUR_FOR_MODAL).forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      observer.observe(el, {
        attributes: true,
        attributeOldValue: true,
        attributeFilter: ['class', 'style'],
      });
    }
  });
  // …and also for any matching modal added later (research overlay is
  // appended on demand, for example).
  const docObserver = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes.forEach(node => {
        if (!(node instanceof HTMLElement)) return;
        if (node.id in TOUR_FOR_MODAL) {
          observer.observe(node, {
            attributes: true,
            attributeOldValue: true,
            attributeFilter: ['class', 'style'],
          });
          if (_isVisible(node)) _maybeFire(node);
        }
      });
    }
  });
  docObserver.observe(document.body, { childList: true, subtree: false });
}

export function init() {
  if (_initialized) return;
  _initialized = true;
  // Disabled for v1 stability: opening ordinary app windows must never
  // auto-spawn tour overlays or interfere with close/backdrop behavior.
  // Manual slash tours still work through slashCommands.js.
}

if (typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}

export default { init };
