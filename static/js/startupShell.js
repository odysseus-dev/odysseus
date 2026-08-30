// Odysseus UI — startup shell sequencing
// ES6 module — no application dependencies, DOM only.
//
// Revealing the application shell, retiring the boot loader, settling the
// sidebar's own loading state, and firing a deferred URL route are separate
// startup concerns that used to sit inline in app.js behind a single promise.
// They live here so each step has one owner and so the whole contract can be
// exercised directly (tests/test_startup_shell_js.py) without booting the app.

const LOADER_ID = 'app-loader';
const SESSION_BOOTSTRAP_ROW_ID = 'session-list-loading';

// Route openers that read the hydrated session list. Everything else only
// needs module wiring and must not wait on /api/sessions. `/email` spawns a
// fresh chat, and that path falls back to the most recent session's model
// (_createDirectChatFromPreferredModel in app.js) when there is no default
// chat configured, so it genuinely needs the list.
const ROUTES_NEEDING_SESSIONS = new Set(['/email']);

let _routeOpener = null;
let _routeOpenerNeedsSessions = false;

function _loader() {
  return document.getElementById(LOADER_ID);
}

/** Run `fn` after the next paint has committed (two animation frames). */
export function afterNextPaint(fn) {
  requestAnimationFrame(() => requestAnimationFrame(fn));
}

// The loader node stays in the DOM while sessions hydrate — sidebar-layout.js
// and sessions.js both read its presence as a "still starting up" sentinel —
// but it must stop covering, announcing, and animating over a usable shell.
function _makeLoaderInert(loader) {
  if (!loader || loader.dataset.shellRevealed === 'true') return;
  loader.dataset.shellRevealed = 'true';
  loader.setAttribute('aria-hidden', 'true');
  loader.style.pointerEvents = 'none';
  loader.style.opacity = '0';
  // index.html's inline bootstrap animates the wave on a 150ms interval.
  // Nothing of it is visible any more, so stop rendering into it.
  try { window.__odysseusLoaderWaveStop?.(); } catch (_) {}
}

/**
 * Hand the shell to the user once core wiring is done. Deferred by one paint
 * so the first frame lands with the app already laid out.
 */
export function revealApplicationShellAfterPaint() {
  const loader = _loader();
  if (!loader || loader.dataset.shellRevealScheduled === 'true') return;
  loader.dataset.shellRevealScheduled = 'true';
  afterNextPaint(() => _makeLoaderInert(_loader()));
}

/** Retire the loader node for good. Safe to call after a reveal. */
export function removeApplicationLoader() {
  const loader = _loader();
  if (!loader) return;
  _makeLoaderInert(loader);
  setTimeout(() => loader.remove(), 300);
}

/**
 * Turn the sidebar's bootstrap row into a failure row. The write is delayed
 * until the session renderer's frame has committed so a late success cannot
 * leave stale failure text behind.
 */
export function markSessionListUnavailableIfStillBootstrapping() {
  afterNextPaint(() => {
    const row = document.getElementById(SESSION_BOOTSTRAP_ROW_ID);
    if (!row) return;
    const status = row.querySelector('[data-session-list-status]') || row;
    status.textContent = 'Chats unavailable';
  });
}

/** True when `path`'s route opener reads the hydrated session list. */
export function routeNeedsSessionData(path) {
  return ROUTES_NEEDING_SESSIONS.has(path);
}

/**
 * Stash a URL route opener for later. At the point app.js resolves the route,
 * the modules its handlers drive (the rail new-chat handler, the email
 * section header handler, sessionModule) are still being wired further down
 * the same init pass, so the opener cannot run inline.
 */
export function deferRouteOpener(path, opener) {
  if (!opener) return;
  _routeOpener = opener;
  _routeOpenerNeedsSessions = routeNeedsSessionData(path);
}

/**
 * Fire the deferred route opener if its data is ready. Called once when
 * wiring completes and again after authoritative session hydration; a route
 * that needs no session data takes the first call, one that does takes the
 * second.
 *
 * @returns {boolean} whether an opener ran.
 */
export function runDeferredRouteOpener({ sessionsSettled = false } = {}) {
  if (!_routeOpener) return false;
  if (_routeOpenerNeedsSessions && !sessionsSettled) return false;
  const opener = _routeOpener;
  _routeOpener = null;
  _routeOpenerNeedsSessions = false;
  try { opener(); } catch (e) { console.warn('route opener failed:', e); }
  return true;
}

/**
 * Drive session hydration and everything that hangs off it settling: the
 * sidebar's failure row, the loader node, and any session-dependent route.
 *
 * @param {(() => Promise<boolean>)|null} loadSessions — resolves true only
 *   after the session list was authoritatively loaded and applied. Null means
 *   the session module failed to load.
 */
export function settleSessionHydration(loadSessions) {
  const settle = (succeeded) => {
    if (!succeeded) {
      markSessionListUnavailableIfStillBootstrapping();
      // A later unrelated caller must not be able to release a stale startup
      // opener against unknown session state.
      _routeOpener = null;
      _routeOpenerNeedsSessions = false;
    }
    removeApplicationLoader();
    if (succeeded) runDeferredRouteOpener({ sessionsSettled: true });
    return succeeded;
  };
  if (!loadSessions) {
    return Promise.resolve(settle(false));
  }
  // Kick the request off synchronously — a microtask hop here would delay the
  // fetch this whole change exists to get off the critical path.
  let pending;
  try {
    pending = loadSessions();
  } catch (e) {
    console.warn('loadSessions error:', e);
    return Promise.resolve(settle(false));
  }
  return Promise.resolve(pending)
    .then(result => settle(result === true))
    .catch(e => {
      console.warn('loadSessions error:', e);
      return settle(false);
    });
}
