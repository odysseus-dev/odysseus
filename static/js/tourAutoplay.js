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
import { isTourActive, seenGet, seenSet, watchModals } from './tour-core.js';

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

const SEEN_KEY = (tour) => `odysseus-tour-autoplay-seen-${tour}`;

let _initialized = false;

async function _maybeFire(modal) {
  const id = modal.id;
  const tour = TOUR_FOR_MODAL[id];
  if (!tour) return;
  // Suppress re-fire if a tour is already active (the slash command adds
  // body.tour-active for the duration of its halos).
  if (isTourActive()) {
    try { window.cancelActiveTour?.('modal-opened'); } catch (_) {}
    return;
  }
  if (seenGet(SEEN_KEY(tour))) return;
  // Mark immediately so a quick double-trigger (e.g. modal-class observer
  // fires twice during animation) can't queue two tours.
  seenSet(SEEN_KEY(tour));
  // Let the modal's own enter-animation settle before halos try to position
  // off the title bar / first card / etc. ~400ms matches tourHints.
  setTimeout(() => {
    if (isTourActive()) return;
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

// Defined for when autoplay is re-enabled (init is currently a no-op, below).
function _watchModals() {
  // Observe the known tour-triggering modals; _maybeFire filters by the map,
  // guards on the seen flag, and skips while a tour is active.
  watchModals((el) => el.id in TOUR_FOR_MODAL, _maybeFire);
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
