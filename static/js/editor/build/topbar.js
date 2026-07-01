/**
 * Build the editor's top bar (undo/redo/history, zoom group, Image
 * menu, Filter menu, Selection-edge menu, Shortcuts, Import, Save).
 *
 * Pure DOM — no module state, no event listeners. All wiring is done
 * by the caller via `document.getElementById(...)` against the IDs
 * baked into the markup.
 *
 * @returns {HTMLDivElement}
 */
export function buildTopbar() {
  const topBar = document.createElement('div');
  topBar.className = 'ge-topbar';
  topBar.innerHTML = `
    <div class="ge-topbar-left">
      <span class="ge-alpha-badge" title="${window.t('This editor is in active development — expect rough edges')}">ALPHA</span>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-undo" title="${window.t('Undo')}">
        <span class="ge-stacked-glyph">↩</span>
        <span class="ge-stacked-label">${window.t('UNDO')}</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-redo" title="${window.t('Redo')}">
        <span class="ge-stacked-glyph">↪</span>
        <span class="ge-stacked-label">${window.t('REDO')}</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-history-btn" title="${window.t('History — click an entry to jump to that state')}" aria-label="${window.t('History')}">
        <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><polyline points="12 7 12 12 16 14"/></svg></span>
        <span class="ge-stacked-label">${window.t('HISTORY')}</span>
      </button>
      <span class="ge-topbar-sep"></span>
      <button class="ge-btn ge-btn-sm" id="ge-zoom-out" title="${window.t('Zoom out')}">&minus;</button>
      <span class="ge-zoom-stack">
        <span class="ge-zoom-glyph">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <span class="ge-zoom-label">100%</span>
      </span>
      <button class="ge-btn ge-btn-sm" id="ge-zoom-in" title="${window.t('Zoom in')}">+</button>
      <span class="ge-topbar-sep"></span>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-zoom-fit" title="${window.t('Fit to view')}" aria-pressed="false">
        <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 4 20 10 20"/><polyline points="20 10 20 4 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg></span>
        <span class="ge-stacked-label">${window.t('FIT')}</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-zoom-100" title="${window.t('Actual size')}" aria-pressed="false">
        <span class="ge-stacked-glyph">1:1</span>
        <span class="ge-stacked-label">${window.t('SCALE')}</span>
      </button>
      <span class="ge-topbar-sep"></span>
    </div>
    <div class="ge-topbar-right">
      <span class="ge-canvas-size" id="ge-canvas-size" title="${window.t('Canvas size')}" hidden></span>
      <div class="ge-image-wrap">
        <button class="ge-btn ge-btn-sm" id="ge-image-menu-btn" title="${window.t('Image actions')}" aria-haspopup="true">${window.t('Image')} ▾</button>
        <div class="ge-image-menu dropdown" id="ge-image-menu" hidden>
          <button class="dropdown-item-compact" data-image-action="resize">
            <span class="dropdown-icon">⤢</span>
            <span>${window.t('Canvas…')}</span>
          </button>
          <div class="ge-filter-submenu-label">${window.t('Transform')}</div>
          <button class="dropdown-item-compact" data-image-action="rotate-90">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg></span>
            <span>${window.t('Rotate 90° CW')}</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="rotate-180">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg></span>
            <span>${window.t('Rotate 180°')}</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-h">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7"/><line x1="12" y1="3" x2="12" y2="21"/><polyline points="7 11 4 7 7 3"/><polyline points="17 11 20 7 17 3"/></svg></span>
            <span>${window.t('Flip horizontal')}</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-v">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h10"/><line x1="3" y1="12" x2="21" y2="12"/><polyline points="11 7 7 4 3 7"/><polyline points="11 17 7 20 3 17"/></svg></span>
            <span>${window.t('Flip vertical')}</span>
          </button>
        </div>
      </div>
      <div class="ge-filter-wrap">
        <button class="ge-btn ge-btn-sm" id="ge-filter-menu-btn" title="${window.t('Filters')}" aria-haspopup="true">${window.t('Filter')} ▾</button>
        <div class="ge-filter-menu dropdown" id="ge-filter-menu" hidden>
          <div class="ge-filter-submenu-label">${window.t('Blur')}</div>
          <button class="dropdown-item-compact" data-filter-action="blur-gaussian">
            <span class="dropdown-icon ge-blur-icon ge-blur-gaussian" aria-hidden="true"></span>
            <span>${window.t('Gaussian Blur…')}</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="blur-zoom">
            <span class="dropdown-icon ge-blur-icon ge-blur-zoom" aria-hidden="true"></span>
            <span>${window.t('Zoom Blur…')}</span>
          </button>
        </div>
      </div>
      <span class="ge-topbar-sep"></span>
      <button class="ge-btn ge-btn-sm" id="ge-shortcuts-btn" title="${window.t('Keyboard shortcuts (?)')}" aria-label="${window.t('Shortcuts')}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="position:relative;top:2px;"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm" id="ge-import-topbar" title="${window.t('Import image as layer')}">${window.t('+ Import')}</button>
      <div class="ge-save-wrap">
        <button class="ge-btn ge-btn-primary" id="ge-save-menu-btn" title="${window.t('Save options')}" style="display:inline-flex;align-items:center;gap:4px;">${window.t('Save')}
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div class="ge-save-menu dropdown" id="ge-save-menu" hidden>
          <div class="dropdown-section-label">${window.t('Image')}</div>
          <button class="dropdown-item-compact" id="ge-save" title="${window.t('Overwrite the original image')}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>
            <span>${window.t('Save over original')}</span>
            <span class="dropdown-shortcut">Ctrl+S</span>
          </button>
          <button class="dropdown-item-compact" id="ge-export-gallery" title="${window.t('Save as a new image in the gallery')}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg></span>
            <span>${window.t('Save as copy')}</span>
            <span class="dropdown-shortcut">Ctrl+Shift+S</span>
          </button>
          <button class="dropdown-item-compact" id="ge-download" title="${window.t('Download PNG to your computer')}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>
            <span>${window.t('Download PNG')}</span>
          </button>
          <div class="dropdown-section-divider"></div>
          <div class="dropdown-section-label">${window.t('Project')}</div>
          <button class="dropdown-item-compact" id="ge-save-project" title="${window.t('Save layered project (.json) — keeps every layer editable for later')}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="13" height="13" rx="1"/><rect x="8" y="8" width="13" height="13" rx="1"/></svg></span>
            <span>${window.t('Save project (.json)')}</span>
          </button>
          <button class="dropdown-item-compact" id="ge-load-project" title="${window.t('Open a previously-saved project file')}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></span>
            <span>${window.t('Load project…')}</span>
          </button>
        </div>
      </div>
    </div>
  `;
  return topBar;
}
