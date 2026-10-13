/**
 * Build the editor's top bar (undo/redo/history, Image
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
      <span class="ge-alpha-badge" title="This editor is in active development — expect rough edges">ALPHA</span>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-undo" title="Undo">
        <span class="ge-stacked-glyph">↩</span>
        <span class="ge-stacked-label">UNDO</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-redo" title="Redo">
        <span class="ge-stacked-glyph">↪</span>
        <span class="ge-stacked-label">REDO</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-history-btn" title="History — click an entry to jump to that state" aria-label="History">
        <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><polyline points="12 7 12 12 16 14"/></svg></span>
        <span class="ge-stacked-label">HISTORY</span>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-compare-btn" title="Show the document before editing" aria-label="Show the document before editing" aria-pressed="false">
        <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"></path><path d="M5 7h14M5 17h14"></path></svg></span>
        <span class="ge-stacked-label">BEFORE</span>
      </button>
    </div>
    <div class="ge-topbar-right">
      <div class="ge-view-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-view-menu-btn" title="Canvas view" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">VIEW</span>
        </button>
        <div class="ge-view-menu dropdown" id="ge-view-menu" hidden>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="rulers"><span class="dropdown-icon ge-view-check">✓</span><span>Rulers</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="grid"><span class="dropdown-icon ge-view-check">✓</span><span>Grid</span></button>
          <label class="ge-view-grid-size"><span>Grid size</span><input id="ge-grid-size" type="number" min="2" max="1000" step="1" value="16"><span>px</span></label>
          <div class="dropdown-section-divider"></div>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="snap"><span class="dropdown-icon ge-view-check">✓</span><span>Snap</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="snap-grid"><span class="dropdown-icon ge-view-check">✓</span><span>Snap to grid</span></button>
          <button class="dropdown-item-compact" data-view-action="clear-guides"><span class="dropdown-icon">×</span><span>Clear guides</span></button>
        </div>
      </div>
      <div class="ge-image-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-image-menu-btn" title="Image actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">IMAGE</span>
        </button>
        <div class="ge-image-menu dropdown" id="ge-image-menu" hidden>
          <button class="dropdown-item-compact" data-image-action="fill" title="Fill with foreground color (Alt+Backspace)"><span>Fill</span></button>
          <button class="dropdown-item-compact" data-image-action="canvas-size">
            <span class="dropdown-icon">⤢</span>
            <span>Canvas Size...</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="image-size">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg></span>
            <span>Image Size...</span>
          </button>
          <div class="ge-filter-submenu-label">Transform</div>
          <button class="dropdown-item-compact" data-image-action="rotate-90">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg></span>
            <span>Rotate 90° CW</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="rotate-180">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg></span>
            <span>Rotate 180°</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-h">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7"/><line x1="12" y1="3" x2="12" y2="21"/><polyline points="7 11 4 7 7 3"/><polyline points="17 11 20 7 17 3"/></svg></span>
            <span>Flip horizontal</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-v">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h10"/><line x1="3" y1="12" x2="21" y2="12"/><polyline points="11 7 7 4 3 7"/><polyline points="11 17 7 20 3 17"/></svg></span>
            <span>Flip vertical</span>
          </button>
        </div>
      </div>
      <div class="ge-selection-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-selection-menu-btn" title="Selection actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">SELECT</span>
        </button>
        <div class="ge-selection-menu dropdown" id="ge-selection-menu" hidden>
          <button class="dropdown-item-compact" data-selection-action="all"><span>Select All</span><span class="dropdown-shortcut">Ctrl+A</span></button>
          <button class="dropdown-item-compact" data-selection-action="deselect"><span>Deselect</span><span class="dropdown-shortcut">Ctrl+D</span></button>
          <button class="dropdown-item-compact" data-selection-action="reselect"><span>Reselect</span></button>
          <button class="dropdown-item-compact" data-selection-action="invert"><span>Invert</span><span class="dropdown-shortcut">Ctrl+Alt+Shift+I</span></button>
          <button class="dropdown-item-compact" data-selection-action="transform"><span>Transform Selection</span></button>
          <button class="dropdown-item-compact" data-selection-action="refine"><span>Refine Selection…</span></button>
          <div class="dropdown-section-divider"></div>
          <div class="ge-selection-save-row">
            <input id="ge-selection-name" type="text" maxlength="100" placeholder="Selection name" aria-label="Saved selection name" />
            <button type="button" class="ge-icon-btn" data-selection-action="save" title="Save current selection" aria-label="Save current selection">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
            </button>
          </div>
          <div class="ge-filter-submenu-label">Saved selections</div>
          <div id="ge-saved-selection-list" class="ge-saved-selection-list"></div>
        </div>
      </div>
      <div class="ge-filter-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-filter-menu-btn" title="Filters" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">FILTER</span>
        </button>
        <div class="ge-filter-menu dropdown" id="ge-filter-menu" hidden>
          <div class="ge-filter-submenu-label">Retained effects</div>
          <button class="dropdown-item-compact" data-filter-action="effect-blur-gaussian">
            <span class="dropdown-icon ge-blur-icon ge-blur-gaussian" aria-hidden="true"></span>
            <span>Gaussian Blur (retained)…</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="effect-sharpen">
            <span class="dropdown-icon" aria-hidden="true">◈</span>
            <span>Sharpen (retained)…</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="effect-color-overlay">
            <span class="dropdown-icon" aria-hidden="true">◐</span>
            <span>Color Overlay (retained)…</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="effect-drop-shadow">
            <span class="dropdown-icon" aria-hidden="true">◒</span>
            <span>Drop Shadow (retained)…</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="effect-stroke">
            <span class="dropdown-icon" aria-hidden="true">□</span>
            <span>Stroke (retained)…</span>
          </button>
          <div class="ge-filter-submenu-label">Presets</div>
          <button class="dropdown-item-compact" data-filter-action="effect-preset-soft-blur"><span>Soft Blur</span></button>
          <button class="dropdown-item-compact" data-filter-action="effect-preset-crisp-detail"><span>Crisp Detail</span></button>
          <button class="dropdown-item-compact" data-filter-action="effect-preset-soft-shadow"><span>Soft Shadow</span></button>
          <button class="dropdown-item-compact" data-filter-action="effect-preset-white-outline"><span>White Outline</span></button>
          <div class="ge-filter-submenu-label">Blur</div>
          <button class="dropdown-item-compact" data-filter-action="blur-gaussian">
            <span class="dropdown-icon ge-blur-icon ge-blur-gaussian" aria-hidden="true"></span>
            <span>Gaussian Blur…</span>
          </button>
          <button class="dropdown-item-compact" data-filter-action="blur-zoom">
            <span class="dropdown-icon ge-blur-icon ge-blur-zoom" aria-hidden="true"></span>
            <span>Zoom Blur…</span>
          </button>
        </div>
      </div>
      <span class="ge-topbar-sep"></span>
      <button class="ge-btn ge-btn-sm" id="ge-shortcuts-btn" title="Keyboard shortcuts (?)" aria-label="Shortcuts">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="position:relative;top:2px;"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-import-topbar" title="Import image as layer">
        <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><polyline points="7 8 12 3 17 8"/><path d="M5 14v5h14v-5"/></svg></span>
        <span class="ge-stacked-label">IMPORT</span>
      </button>
      <div class="ge-save-wrap">
        <button class="ge-btn ge-btn-sm ge-btn-primary ge-stacked-btn" id="ge-save-menu-btn" title="Save options">
          <span class="ge-stacked-glyph">
            <svg hidden class="ge-save-state-icon ge-save-state-dirty" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 3h12l4 4v14H4z"/><path d="M8 3v6h8V3"/><path d="m16 3 6 6m0-6-6 6" stroke="var(--fg)" stroke-width="3"/></svg>
            <svg class="ge-save-state-icon ge-save-state-saved" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
          </span>
          <span class="ge-stacked-label ge-save-label" id="ge-draft-status" role="status" aria-live="polite">Saved</span>
        </button>
        <div class="ge-save-menu dropdown" id="ge-save-menu" hidden>
          <div class="dropdown-section-label">Image</div>
          <button class="dropdown-item-compact" id="ge-save" title="Overwrite the original image">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>
            <span>Save over original</span>
            <span class="dropdown-shortcut">Ctrl+S</span>
          </button>
          <button class="dropdown-item-compact" id="ge-export-gallery" title="Save as a new image in the gallery">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg></span>
            <span>Save as copy</span>
            <span class="dropdown-shortcut">Ctrl+Shift+S</span>
          </button>
          <button class="dropdown-item-compact" id="ge-download" title="Export an image to your computer">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>
            <span>Export image...</span>
          </button>
          <div class="dropdown-section-divider"></div>
          <div class="dropdown-section-label">Project</div>
          <button class="dropdown-item-compact" id="ge-save-project" title="Save layered project (.json) — keeps every layer editable for later">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="13" height="13" rx="1"/><rect x="8" y="8" width="13" height="13" rx="1"/></svg></span>
            <span>Save project (.json)</span>
          </button>
          <button class="dropdown-item-compact" id="ge-load-project" title="Open a previously-saved project file">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></span>
            <span>Load project…</span>
          </button>
        </div>
      </div>
    </div>
  `;
  return topBar;
}

export function buildZoomFooter() {
  const footer = document.createElement('div');
  footer.className = 'ge-editor-footer';
  footer.innerHTML = `
    <span class="ge-canvas-size" id="ge-canvas-size" title="Canvas size" hidden></span>
    <div class="ge-footer-zoom" role="group" aria-label="Canvas zoom">
      <button class="ge-btn ge-btn-sm" id="ge-zoom-out" title="Zoom out" aria-label="Zoom out">&minus;</button>
      <span class="ge-zoom-stack" aria-live="off">
        <span class="ge-zoom-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
        <span class="ge-zoom-label">100%</span>
      </span>
      <button class="ge-btn ge-btn-sm" id="ge-zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
      <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-zoom-100" title="Switch to actual size" aria-label="Fit view; switch to actual size" aria-pressed="false">
        <span class="ge-stacked-glyph">1:1</span><span class="ge-stacked-label">FIT</span>
      </button>
    </div>`;
  return footer;
}
