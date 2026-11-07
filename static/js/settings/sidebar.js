const STORAGE_WIDTH = 'odysseus-settings-sidebar-width';
const STORAGE_COLLAPSED = 'odysseus-settings-sidebar-collapsed';

export const SETTINGS_SIDEBAR_DEFAULT_WIDTH = 220;
export const SETTINGS_SIDEBAR_MIN_WIDTH = 150;
export const SETTINGS_SIDEBAR_MAX_WIDTH = 340;
export const SETTINGS_SIDEBAR_COLLAPSE_THRESHOLD = 110;

const _bound = new WeakSet();

function clampWidth(value) {
  const width = Number(value);
  if (!Number.isFinite(width)) return SETTINGS_SIDEBAR_DEFAULT_WIDTH;
  return Math.max(
    SETTINGS_SIDEBAR_MIN_WIDTH,
    Math.min(SETTINGS_SIDEBAR_MAX_WIDTH, width),
  );
}

function readStoredWidth() {
  try {
    const stored = localStorage.getItem(STORAGE_WIDTH);
    if (stored == null || String(stored).trim() === '') {
      return SETTINGS_SIDEBAR_DEFAULT_WIDTH;
    }
    return clampWidth(stored);
  } catch {
    return SETTINGS_SIDEBAR_DEFAULT_WIDTH;
  }
}

function readStoredCollapsed() {
  try {
    return localStorage.getItem(STORAGE_COLLAPSED) === '1';
  } catch {
    return false;
  }
}

function storeWidth(width) {
  try {
    localStorage.setItem(STORAGE_WIDTH, String(Math.round(width)));
  } catch {}
}

function storeCollapsed(collapsed) {
  try {
    localStorage.setItem(STORAGE_COLLAPSED, collapsed ? '1' : '0');
  } catch {}
}

function syncResizeHandleAria(modalEl, width = null) {
  const handle = modalEl?.querySelector('#settings-sidebar-resize-handle');
  if (!handle) return;

  const sidebar = modalEl.querySelector('.settings-sidebar');
  const collapsed = sidebar?.classList.contains('settings-sidebar-collapsed');

  const current = collapsed
    ? SETTINGS_SIDEBAR_MIN_WIDTH
    : clampWidth(
        width ?? sidebar?.getBoundingClientRect?.().width
          ?? SETTINGS_SIDEBAR_DEFAULT_WIDTH
      );

  handle.setAttribute('aria-valuemin', String(SETTINGS_SIDEBAR_MIN_WIDTH));
  handle.setAttribute('aria-valuemax', String(SETTINGS_SIDEBAR_MAX_WIDTH));
  handle.setAttribute('aria-valuenow', String(Math.round(current)));
}

function isDesktopSidebarMode(modalEl) {
  const content = modalEl?.querySelector('.settings-modal-content');
  if (!content) return false;

  // Mirrors the existing container breakpoint where the sidebar becomes a
  // horizontal rail. Resizing/collapse applies only to the vertical desktop
  // navigation layout.
  return content.getBoundingClientRect().width > 620;
}

export function setSettingsSidebarCollapsed(modalEl, collapsed, options = {}) {
  const sidebar = modalEl?.querySelector('.settings-sidebar');
  if (!sidebar) return false;

  const next = collapsed === true;
  sidebar.classList.toggle('settings-sidebar-collapsed', next);

  const toggle = sidebar.querySelector('#settings-sidebar-toggle');
  if (toggle) {
    toggle.setAttribute('aria-expanded', next ? 'false' : 'true');
    toggle.setAttribute(
      'aria-label',
      next ? 'Expand settings navigation' : 'Collapse settings navigation',
    );
    toggle.title = next
      ? 'Expand settings navigation'
      : 'Collapse settings navigation';
  }

  if (!next) {
    const width = clampWidth(options.width ?? readStoredWidth());
    sidebar.style.setProperty('--settings-sidebar-width', `${width}px`);
    syncResizeHandleAria(modalEl, width);
  } else {
    syncResizeHandleAria(modalEl);
  }

  if (options.persist !== false) storeCollapsed(next);
  return next;
}

export function setSettingsSidebarWidth(modalEl, width, options = {}) {
  const sidebar = modalEl?.querySelector('.settings-sidebar');
  if (!sidebar) return null;

  const next = clampWidth(width);
  sidebar.style.setProperty('--settings-sidebar-width', `${next}px`);
  syncResizeHandleAria(modalEl, next);

  if (options.persist !== false) storeWidth(next);
  return next;
}

/* ── Build provenance ──
   The bottom-left of the panel answers "which build am I actually looking
   at": the version this build registers and the commit its process loaded.
   Both come from /api/version, which resolves the commit once at import via
   `git rev-parse HEAD` and reports the string "unknown" when it cannot — a
   read-only Docker tree with no .git, for instance. */
const BUILD_COMMIT_SHORT_LEN = 8;
const BUILD_UNKNOWN = 'unknown';
const BUILD_COMMIT_SHA = /^[0-9a-f]{7,64}$/i;

function buildField(value) {
  const text = String(value ?? '').trim();
  return text && text.toLowerCase() !== BUILD_UNKNOWN ? text : '';
}

/** Turn an /api/version payload into the two sidebar lines, or null when it
 *  carries nothing worth a footer. */
export function formatSettingsBuildInfo(payload) {
  const version = buildField(payload?.version);
  const build = buildField(payload?.build);
  const commit = buildField(payload?.source_commit);

  const parts = [];
  if (version) parts.push(`v${version}`);
  // The preview harness versions itself separately from the public semver, so
  // show both — but never the same number twice.
  if (build && build !== version) parts.push(`build ${build}`);

  // An ODYSSEUS_SOURCE_COMMIT override need not be a sha; leave those intact
  // and let the CSS ellipsis deal with the width.
  const commitLabel = BUILD_COMMIT_SHA.test(commit)
    ? commit.slice(0, BUILD_COMMIT_SHORT_LEN).toLowerCase()
    : commit;

  if (!parts.length && !commitLabel) return null;

  return {
    versionLabel: parts.join(' \u00b7 '),
    commitLabel,
    commitTitle: commit,
  };
}

function paintSettingsBuildInfo(modalEl, info) {
  const host = modalEl?.querySelector('#settings-sidebar-build');
  if (!host) return;

  const versionEl = host.querySelector('#settings-sidebar-build-version');
  const commitEl = host.querySelector('#settings-sidebar-build-commit');

  if (!info) {
    host.hidden = true;
    return;
  }

  if (versionEl) versionEl.textContent = info.versionLabel;
  if (commitEl) {
    commitEl.textContent = info.commitLabel;
    // The short hash is what fits; keep the full one reachable on hover.
    if (info.commitTitle) commitEl.title = info.commitTitle;
  }

  host.hidden = false;
}

async function loadSettingsBuildInfo(modalEl) {
  try {
    const res = await fetch('/api/version', { credentials: 'same-origin' });
    if (!res.ok) return;
    paintSettingsBuildInfo(modalEl, formatSettingsBuildInfo(await res.json()));
  } catch {
    // Provenance is informational: a failed probe leaves the footer hidden
    // rather than putting an error in the middle of the settings nav.
  }
}

export function bindSettingsSidebar(modalEl) {
  if (!modalEl || _bound.has(modalEl)) return;
  _bound.add(modalEl);

  loadSettingsBuildInfo(modalEl);

  const sidebar = modalEl.querySelector('.settings-sidebar');
  const handle = modalEl.querySelector('#settings-sidebar-resize-handle');
  const toggle = modalEl.querySelector('#settings-sidebar-toggle');

  if (!sidebar || !handle || !toggle) return;

  setSettingsSidebarWidth(modalEl, readStoredWidth(), { persist: false });
  setSettingsSidebarCollapsed(
    modalEl,
    readStoredCollapsed(),
    { persist: false },
  );

  toggle.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();

    if (!isDesktopSidebarMode(modalEl)) return;

    const collapsed = sidebar.classList.contains('settings-sidebar-collapsed');
    setSettingsSidebarCollapsed(modalEl, !collapsed);
  });

  let startX = 0;
  let startWidth = 0;

  function stopResize() {
    if (!sidebar.classList.contains('settings-sidebar-resizing')) return;

    sidebar.classList.remove('settings-sidebar-resizing');
    document.body.classList.remove('settings-sidebar-resize-active');

    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', stopResize);

    const width = sidebar.getBoundingClientRect().width;
    if (width < SETTINGS_SIDEBAR_COLLAPSE_THRESHOLD) {
      setSettingsSidebarCollapsed(modalEl, true);
      return;
    }

    setSettingsSidebarCollapsed(modalEl, false, { persist: false });
    setSettingsSidebarWidth(modalEl, width);
    storeCollapsed(false);
  }

  function onPointerMove(event) {
    const rawWidth = startWidth + (event.clientX - startX);

    if (rawWidth < SETTINGS_SIDEBAR_COLLAPSE_THRESHOLD) {
      sidebar.style.setProperty(
        '--settings-sidebar-width',
        `${Math.max(34, rawWidth)}px`,
      );
      return;
    }

    setSettingsSidebarCollapsed(modalEl, false, { persist: false });
    setSettingsSidebarWidth(modalEl, rawWidth, { persist: false });
  }

  handle.addEventListener('pointerdown', event => {
    if (!isDesktopSidebarMode(modalEl)) return;

    event.preventDefault();
    startX = event.clientX;
    startWidth = sidebar.getBoundingClientRect().width;

    sidebar.classList.remove('settings-sidebar-collapsed');
    sidebar.classList.add('settings-sidebar-resizing');
    document.body.classList.add('settings-sidebar-resize-active');

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', stopResize);
  });

  handle.addEventListener('keydown', event => {
    if (!isDesktopSidebarMode(modalEl)) return;

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle.click();
      return;
    }

    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;

    event.preventDefault();

    if (sidebar.classList.contains('settings-sidebar-collapsed')) {
      setSettingsSidebarCollapsed(modalEl, false);
    }

    const current = sidebar.getBoundingClientRect().width;
    const delta = event.key === 'ArrowLeft' ? -16 : 16;

    // Once keyboard resizing reaches the declared minimum, another ArrowLeft
    // collapses the rail. Without this explicit boundary transition the width
    // setter clamps 134px back to 150px forever, making keyboard collapse via
    // ArrowLeft unreachable.
    if (
      event.key === 'ArrowLeft'
      && current <= SETTINGS_SIDEBAR_MIN_WIDTH
    ) {
      setSettingsSidebarCollapsed(modalEl, true);
      return;
    }

    setSettingsSidebarWidth(modalEl, current + delta);
  });
}
