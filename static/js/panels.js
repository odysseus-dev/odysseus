/**
 * Panel loader registry — imports the modules behind a feature panel the
 * first time that panel is actually used.
 *
 * The panels already populate themselves on open (each fetches its own data).
 * They were just not *loaded* on demand: every one of them sat on the critical
 * path of every page load, opened or not.
 *
 * A panel only belongs here once it has been checked for import-time side
 * effects that something outside the panel depends on at startup. Entries get
 * added one panel at a time, not in bulk.
 *
 * Note the service worker still precaches these modules (PANEL_PRECACHE in
 * sw.js) — they are off the critical path, not off the offline manifest.
 */

const LOADERS = {
  editor: () => import('./galleryEditor.js'),
};

/**
 * Build a memoising loader over a name -> import-thunk map. Exported so the
 * behaviour can be tested without pulling a real panel's module graph in.
 */
export function createPanelLoader(loaders) {
  const cache = new Map();
  return function load(name) {
    const cached = cache.get(name);
    if (cached) return cached;
    const loader = loaders[name];
    if (!loader) throw new Error(`loadPanel: unknown panel "${name}"`);
    // A failed load (offline, 404, syntax error) is not memoised — caching the
    // rejection would leave the panel broken for the rest of the session even
    // after the network came back.
    const pending = Promise.resolve().then(loader).catch((err) => {
      cache.delete(name);
      throw err;
    });
    cache.set(name, pending);
    return pending;
  };
}

/**
 * Load a panel's module, once. Returns the same promise on every call for the
 * same panel; throws for a name that is not registered.
 */
export const loadPanel = createPanelLoader(LOADERS);

/** Registered panel names — the registry is the list, not a second copy of it. */
export function panelNames() {
  return Object.keys(LOADERS);
}
