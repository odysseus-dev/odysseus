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
      <div class="ge-file-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-file-menu-btn" title="File actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">File</span>
        </button>
        <div class="ge-file-menu dropdown" id="ge-file-menu" hidden>
          <div class="ge-menu-label">New</div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="file-new" aria-haspopup="true" aria-expanded="false"><span>New Image</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="file-new">
            <button class="dropdown-item-compact" data-file-action="new-default"><span>Default</span><span class="dropdown-shortcut">1920 × 1080</span></button>
            <button class="dropdown-item-compact" data-file-action="new-square"><span>Square</span><span class="dropdown-shortcut">1080 × 1080</span></button>
            <button class="dropdown-item-compact" data-file-action="new-portrait"><span>Portrait</span><span class="dropdown-shortcut">1080 × 1350</span></button>
            <button class="dropdown-item-compact" data-file-action="new-story"><span>Story</span><span class="dropdown-shortcut">1080 × 1920</span></button>
            <button class="dropdown-item-compact" data-file-action="new-clipboard"><span>From Clipboard</span></button>
            <button class="dropdown-item-compact" data-file-action="new-custom"><span>Custom Size…</span><span class="dropdown-shortcut">Ctrl+N</span></button>
          </div>
          <div class="ge-menu-label">Open</div>
          <button class="dropdown-item-compact" data-file-action="import"><span>Import Image…</span></button>
          <button class="dropdown-item-compact" data-file-action="load-project"><span>Load Project…</span></button>
          <div class="ge-menu-label">Save</div>
          <button class="dropdown-item-compact" data-file-action="save"><span>Save Over Original</span><span class="dropdown-shortcut">Ctrl+S</span></button>
          <button class="dropdown-item-compact" data-file-action="save-copy"><span>Save As Copy</span><span class="dropdown-shortcut">Ctrl+Shift+S</span></button>
          <button class="dropdown-item-compact" data-file-action="export"><span>Export Image…</span></button>
          <button class="dropdown-item-compact" data-file-action="export-psd"><span>Export Layers as PSD…</span></button>
          <div class="ge-menu-label">Project</div>
          <button class="dropdown-item-compact" data-file-action="save-project"><span>Save Project (.json)</span></button>
        </div>
      </div>
      <div class="ge-edit-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-edit-menu-btn" title="Edit actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Edit</span>
        </button>
        <div class="ge-edit-menu dropdown" id="ge-edit-menu" hidden>
          <div class="ge-menu-label">History</div>
          <button class="dropdown-item-compact" data-edit-action="undo"><span>Undo</span><span class="dropdown-shortcut">Ctrl+Z</span></button>
          <button class="dropdown-item-compact" data-edit-action="redo"><span>Redo</span><span class="dropdown-shortcut">Ctrl+Shift+Z</span></button>
          <button class="dropdown-item-compact" data-edit-action="history"><span>History…</span></button>
          <div class="ge-menu-label">Clipboard</div>
          <button class="dropdown-item-compact" data-edit-action="copy-merged" title="Copy the flattened visible image to the clipboard"><span>Copy Merged</span><span class="dropdown-shortcut">Ctrl+Shift+C</span></button>
          <div class="ge-menu-label">Transform (active layer)</div>
          <button class="dropdown-item-compact" data-edit-action="rotate-90" title="Rotate the active layer 90° CW"><span>Rotate 90° CW</span></button>
          <button class="dropdown-item-compact" data-edit-action="rotate-180" title="Rotate the active layer 180°"><span>Rotate 180°</span></button>
          <button class="dropdown-item-compact" data-edit-action="flip-h" title="Flip the active layer horizontally"><span>Flip Horizontal</span></button>
          <button class="dropdown-item-compact" data-edit-action="flip-v" title="Flip the active layer vertically"><span>Flip Vertical</span></button>
          <div class="ge-menu-label">Content-Aware</div>
          <button class="dropdown-item-compact" data-edit-action="content-aware-move" title="Move the selection content and fill the hole it leaves behind"><span>Content-Aware Move…</span></button>
          <div class="ge-menu-label">Stroke</div>
          <button class="dropdown-item-compact" data-edit-action="stroke" title="Stroke the active layer or selection"><span>Stroke…</span></button>
          <div class="ge-menu-label">Presets</div>
          <button class="dropdown-item-compact" data-edit-action="define-brush" title="Save the current brush settings as a reusable preset"><span>Define Brush Preset…</span></button>
        </div>
      </div>
      <div class="ge-image-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-image-menu-btn" title="Image actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Image</span>
        </button>
        <div class="ge-image-menu dropdown" id="ge-image-menu" hidden>
          <button class="dropdown-item-compact" data-image-action="fill" title="Fill with foreground color (Alt+Backspace)"><span>Fill</span></button>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="image-adjustments" aria-haspopup="true" aria-expanded="false"><span>Adjustments</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="image-adjustments">
            <button class="dropdown-item-compact" data-image-action="desaturate" title="Desaturate"><span>Desaturate</span></button>
            <button class="dropdown-item-compact" data-image-action="auto-tone" title="Auto Tone"><span>Auto Tone</span></button>
            <button class="dropdown-item-compact" data-image-action="auto-contrast" title="Auto Contrast"><span>Auto Contrast</span></button>
            <button class="dropdown-item-compact" data-image-action="auto-color" title="Auto Color"><span>Auto Color</span></button>
            <button class="dropdown-item-compact" data-image-action="equalize" title="Equalize the histogram of the active layer"><span>Equalize</span></button>
            <button class="dropdown-item-compact" data-image-action="replace-color" title="Recolour pixels near a target colour"><span>Replace Color…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-brightness-contrast" title="Brightness/Contrast"><span>Brightness/Contrast…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-hue-saturation" title="Hue/Saturation"><span>Hue/Saturation…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-selective-color" title="Selective Color"><span>Selective Color…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-levels" title="Levels"><span>Levels…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-curves" title="Curves"><span>Curves…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-exposure" title="Exposure"><span>Exposure…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-develop" title="Develop (Camera Raw)"><span>Develop…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-vibrance" title="Vibrance"><span>Vibrance…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-color-balance" title="Color Balance"><span>Color Balance…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-black-white" title="Black &amp; White"><span>Black &amp; White…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-photo-filter" title="Photo Filter"><span>Photo Filter…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-channel-mixer" title="Channel Mixer"><span>Channel Mixer…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-invert" title="Invert"><span>Invert</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-posterize" title="Posterize"><span>Posterize…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-threshold" title="Threshold"><span>Threshold…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-gradient-map" title="Gradient Map"><span>Gradient Map…</span></button>
            <button class="dropdown-item-compact" data-image-action="adj-shadows-highlights" title="Shadows/Highlights"><span>Shadows/Highlights…</span></button>
          </div>
          <button class="dropdown-item-compact" data-image-action="canvas-size">
            <span class="dropdown-icon">⤢</span>
            <span>Canvas Size...</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="image-size">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg></span>
            <span>Image Size...</span>
          </button>
          <div class="ge-filter-submenu-label">Trim</div>
          <button class="dropdown-item-compact" data-image-action="trim" title="Trim transparent edges from the active layer"><span>Trim…</span></button>
          <div class="ge-filter-submenu-label">Image Rotation (whole image)</div>
          <button class="dropdown-item-compact" data-image-action="rotate-90">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg></span>
            <span>Rotate 90° CW</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="rotate-ccw">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><polyline points="3 3 3 9 9 9"/></svg></span>
            <span>Rotate 90° CCW</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="rotate-180">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg></span>
            <span>Rotate 180°</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="rotate-arbitrary">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></span>
            <span>Arbitrary…</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="perspective-crop">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6l18-2-2 14-16-2z"/><circle cx="3" cy="6" r="1.6" fill="currentColor" stroke="none"/><circle cx="21" cy="4" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="18" r="1.6" fill="currentColor" stroke="none"/><circle cx="3" cy="16" r="1.6" fill="currentColor" stroke="none"/></svg></span>
            <span>Perspective Crop…</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-h">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7"/><line x1="12" y1="3" x2="12" y2="21"/><polyline points="7 11 4 7 7 3"/><polyline points="17 11 20 7 17 3"/></svg></span>
            <span>Flip Canvas Horizontal</span>
          </button>
          <button class="dropdown-item-compact" data-image-action="flip-v">
            <span class="dropdown-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h10"/><line x1="3" y1="12" x2="21" y2="12"/><polyline points="11 7 7 4 3 7"/><polyline points="11 17 7 20 3 17"/></svg></span>
            <span>Flip Canvas Vertical</span>
          </button>
          <div class="ge-filter-submenu-label">Mode</div>
          <button class="dropdown-item-compact" data-image-action="mode-rgb"><span>RGB Color</span></button>
          <button class="dropdown-item-compact" data-image-action="mode-grayscale"><span>Grayscale</span></button>
          <button class="dropdown-item-compact" data-image-action="mode-cmyk" disabled title="CMYK needs a color-managed pipeline"><span>CMYK Color</span></button>
          <button class="dropdown-item-compact" data-image-action="mode-lab" disabled title="Lab needs a color-managed pipeline"><span>Lab Color</span></button>
          <div class="ge-filter-submenu-label">Bits/Channel</div>
          <button class="dropdown-item-compact" data-image-action="bits-8"><span>8 Bits/Channel</span></button>
          <button class="dropdown-item-compact" data-image-action="bits-16" disabled title="16-bit needs a 16-bit working canvas"><span>16 Bits/Channel</span></button>
          <button class="dropdown-item-compact" data-image-action="bits-32" disabled title="32-bit needs a float working canvas"><span>32 Bits/Channel</span></button>
        </div>
      </div>
      <div class="ge-layer-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-layer-menu-btn" title="Layer actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="m3 13 9 5 9-5"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Layer</span>
        </button>
        <div class="ge-layer-menu dropdown" id="ge-layer-menu" hidden>
          <div class="ge-menu-label">New</div>
          <button class="dropdown-item-compact" data-layer-action="new"><span>New Layer…</span></button>
          <button class="dropdown-item-compact" data-layer-action="new-fill"><span>New Fill Layer…</span></button>
          <button class="dropdown-item-compact" data-layer-action="duplicate"><span>Duplicate Layer</span><span class="dropdown-shortcut">Ctrl+J</span></button>
          <button class="dropdown-item-compact" data-layer-action="group"><span>Group Selected</span></button>
          <button class="dropdown-item-compact" data-layer-action="rasterize"><span>Rasterize Layer</span></button>
          <div class="ge-menu-label">Modify</div>
          <button class="dropdown-item-compact" data-layer-action="delete"><span>Delete Layer</span><span class="dropdown-shortcut">Del</span></button>
          <div class="ge-menu-label">Arrange</div>
          <button class="dropdown-item-compact" data-layer-action="arrange-front"><span>Bring to Front</span></button>
          <button class="dropdown-item-compact" data-layer-action="arrange-forward"><span>Bring Forward</span></button>
          <button class="dropdown-item-compact" data-layer-action="arrange-backward"><span>Send Backward</span></button>
          <button class="dropdown-item-compact" data-layer-action="arrange-back"><span>Send to Back</span></button>
          <div class="ge-menu-label">Merge</div>
          <button class="dropdown-item-compact" data-layer-action="merge-down"><span>Merge Down</span><span class="dropdown-shortcut">Ctrl+E</span></button>
          <button class="dropdown-item-compact" data-layer-action="merge-layers"><span>Merge Layers</span></button>
          <button class="dropdown-item-compact" data-layer-action="merge-all"><span>Merge All</span></button>
          <button class="dropdown-item-compact" data-layer-action="merge-visible"><span>Merge Visible</span></button>
          <button class="dropdown-item-compact" data-layer-action="flatten"><span>Flatten Image</span></button>
          <div class="ge-menu-label">Select</div>
          <button class="dropdown-item-compact" data-layer-action="select-all"><span>Select All Layers</span></button>
          <div class="ge-menu-label">Vector Mask</div>
          <button class="dropdown-item-compact" data-layer-action="vector-mask-reveal" title="Add a mask that reveals the whole layer"><span>Reveal All</span></button>
          <button class="dropdown-item-compact" data-layer-action="vector-mask-hide" title="Add a mask that hides the whole layer"><span>Hide All</span></button>
          <div class="ge-menu-label">Combine Shapes</div>
          <button class="dropdown-item-compact" data-layer-action="combine-unite"><span>Unite Shapes</span></button>
          <button class="dropdown-item-compact" data-layer-action="combine-subtract"><span>Subtract Shapes</span></button>
          <button class="dropdown-item-compact" data-layer-action="combine-intersect"><span>Intersect Shapes</span></button>
          <button class="dropdown-item-compact" data-layer-action="combine-exclude"><span>Exclude Shapes</span></button>
          <div class="ge-menu-label">Fill</div>
          <button class="dropdown-item-compact" data-layer-action="fill"><span>Fill</span></button>
        </div>
      </div>
      <div class="ge-type-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-type-menu-btn" title="Type actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Type</span>
        </button>
        <div class="ge-type-menu dropdown" id="ge-type-menu" hidden>
          <div class="ge-menu-label">Create</div>
          <button class="dropdown-item-compact" data-type-action="add"><span>Add Text Layer</span></button>
          <div class="ge-menu-label">Panels</div>
          <button class="dropdown-item-compact" data-type-action="character"><span>Character…</span></button>
          <button class="dropdown-item-compact" data-type-action="paragraph"><span>Paragraph…</span></button>
          <div class="ge-menu-label">Warp</div>
          <button class="dropdown-item-compact" data-type-action="warp"><span>Warp Text…</span></button>
          <div class="ge-menu-label">Path</div>
          <button class="dropdown-item-compact" data-type-action="text-on-path" title="Lay the active text layer along the selected path layer"><span>Text on a Path</span></button>
          <button class="dropdown-item-compact" data-type-action="text-on-circle" title="Wrap the active text layer around a circle"><span>Text on a Circle</span></button>
          <button class="dropdown-item-compact" data-type-action="text-off-path" title="Return the text layer to a straight baseline"><span>Remove from Path</span></button>
        </div>
      </div>
      <div class="ge-selection-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-selection-menu-btn" title="Selection actions" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Select</span>
        </button>
        <div class="ge-selection-menu dropdown" id="ge-selection-menu" hidden>
          <button class="dropdown-item-compact" data-selection-action="all"><span>Select All</span><span class="dropdown-shortcut">Ctrl+A</span></button>
          <button class="dropdown-item-compact" data-selection-action="deselect"><span>Deselect</span><span class="dropdown-shortcut">Ctrl+D</span></button>
          <button class="dropdown-item-compact" data-selection-action="reselect"><span>Reselect</span></button>
          <button class="dropdown-item-compact" data-selection-action="invert"><span>Invert</span><span class="dropdown-shortcut">Ctrl+Alt+Shift+I</span></button>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="selection-modify" aria-haspopup="true" aria-expanded="false"><span>Modify</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="selection-modify">
            <button class="dropdown-item-compact" data-selection-action="modifyBorder"><span>Border…</span></button>
            <button class="dropdown-item-compact" data-selection-action="modifySmooth"><span>Smooth…</span></button>
            <button class="dropdown-item-compact" data-selection-action="modifyExpand"><span>Expand…</span></button>
            <button class="dropdown-item-compact" data-selection-action="modifyContract"><span>Contract…</span></button>
            <button class="dropdown-item-compact" data-selection-action="modifyFeather"><span>Feather…</span></button>
          </div>
          <button class="dropdown-item-compact" data-selection-action="grow"><span>Grow</span></button>
          <button class="dropdown-item-compact" data-selection-action="similar"><span>Similar</span></button>
          <button class="dropdown-item-compact" data-selection-action="subject"><span>Subject</span></button>
          <button class="dropdown-item-compact" data-selection-action="transform"><span>Transform Selection</span></button>
          <button class="dropdown-item-compact" data-selection-action="refine"><span>Refine Selection…</span></button>
          <button class="dropdown-item-compact" data-selection-action="colorRange"><span>Color Range…</span></button>
          <button class="dropdown-item-compact" data-selection-action="selectAndMask"><span>Select and Mask…</span></button>
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
          <span class="ge-stacked-label">Filter</span>
        </button>
        <div class="ge-filter-menu dropdown" id="ge-filter-menu" hidden>
          <div class="ge-menu-label">Enhance</div>
          <button class="dropdown-item-compact" data-filter-action="dehaze"><span class="dropdown-icon" aria-hidden="true">≈</span><span>Haze Removal…</span></button>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-retained" aria-haspopup="true" aria-expanded="false"><span>Retained effects</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-retained">
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
            <button class="dropdown-item-compact" data-filter-action="effect-motion-blur">
              <span class="dropdown-icon" aria-hidden="true">↔</span>
              <span>Motion Blur (retained)…</span>
            </button>
            <button class="dropdown-item-compact" data-filter-action="effect-zoom-blur">
              <span class="dropdown-icon" aria-hidden="true">✳</span>
              <span>Zoom Blur (retained)…</span>
            </button>
            <button class="dropdown-item-compact" data-filter-action="effect-pixelate">
              <span class="dropdown-icon" aria-hidden="true">▦</span>
              <span>Pixelate (retained)…</span>
            </button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-presets" aria-haspopup="true" aria-expanded="false"><span>Presets</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-presets">
            <button class="dropdown-item-compact" data-filter-action="effect-preset-soft-blur"><span>Soft Blur</span></button>
            <button class="dropdown-item-compact" data-filter-action="effect-preset-crisp-detail"><span>Crisp Detail</span></button>
            <button class="dropdown-item-compact" data-filter-action="effect-preset-soft-shadow"><span>Soft Shadow</span></button>
            <button class="dropdown-item-compact" data-filter-action="effect-preset-white-outline"><span>White Outline</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-blur" aria-haspopup="true" aria-expanded="false"><span>Blur</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-blur">
            <button class="dropdown-item-compact" data-filter-action="blur-gaussian">
              <span class="dropdown-icon ge-blur-icon ge-blur-gaussian" aria-hidden="true"></span>
              <span>Gaussian Blur…</span>
            </button>
            <button class="dropdown-item-compact" data-filter-action="blur-zoom">
              <span class="dropdown-icon ge-blur-icon ge-blur-zoom" aria-hidden="true"></span>
              <span>Zoom Blur…</span>
            </button>
            <button class="dropdown-item-compact" data-filter-action="blur-motion"><span>Motion Blur…</span></button>
            <button class="dropdown-item-compact" data-filter-action="radial-blur"><span>Radial Blur…</span></button>
            <button class="dropdown-item-compact" data-filter-action="blur-box"><span>Box Blur…</span></button>
            <button class="dropdown-item-compact" data-filter-action="blur-surface"><span>Surface Blur…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-sharpen" aria-haspopup="true" aria-expanded="false"><span>Sharpen</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-sharpen">
            <button class="dropdown-item-compact" data-filter-action="sharpen"><span>Sharpen…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sharpen-edges"><span>Sharpen Edges…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sharpen-more"><span>Sharpen More…</span></button>
            <button class="dropdown-item-compact" data-filter-action="unsharp-mask"><span>Unsharp Mask…</span></button>
            <button class="dropdown-item-compact" data-filter-action="smart-sharpen"><span>Smart Sharpen…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-noise" aria-haspopup="true" aria-expanded="false"><span>Noise</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-noise">
            <button class="dropdown-item-compact" data-filter-action="noise-add"><span>Add Noise…</span></button>
            <button class="dropdown-item-compact" data-filter-action="noise-despeckle"><span>Despeckle…</span></button>
            <button class="dropdown-item-compact" data-filter-action="reduce-noise"><span>Reduce Noise…</span></button>
            <button class="dropdown-item-compact" data-filter-action="dust-scratches"><span>Dust &amp; Scratches…</span></button>
            <button class="dropdown-item-compact" data-filter-action="noise-median"><span>Median…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-pixelate" aria-haspopup="true" aria-expanded="false"><span>Pixelate</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-pixelate">
            <button class="dropdown-item-compact" data-filter-action="pixelate-mosaic"><span>Mosaic…</span></button>
            <button class="dropdown-item-compact" data-filter-action="pixelate-crystallize"><span>Crystallize…</span></button>
            <button class="dropdown-item-compact" data-filter-action="pixelate-mosaic-tiles"><span>Mosaic Tiles…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-stylize" aria-haspopup="true" aria-expanded="false"><span>Stylize</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-stylize">
            <button class="dropdown-item-compact" data-filter-action="stylize-emboss"><span>Emboss…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-find-edges"><span>Find Edges…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-glowing-edges"><span>Glowing Edges…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-solarize"><span>Solarize…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-oil-paint"><span>Oil Paint…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-diffuse"><span>Diffuse…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-wind"><span>Wind…</span></button>
            <button class="dropdown-item-compact" data-filter-action="stylize-trace-contour"><span>Trace Contour…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-sketch" aria-haspopup="true" aria-expanded="false"><span>Sketch</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-sketch">
            <button class="dropdown-item-compact" data-filter-action="sketch-halftone-pattern"><span>Halftone Pattern…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-reticulation"><span>Reticulation…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-note-paper"><span>Note Paper…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-charcoal"><span>Charcoal…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-chalk-charcoal"><span>Chalk &amp; Charcoal…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-graphic-pen"><span>Graphic Pen…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-photocopy"><span>Photocopy…</span></button>
            <button class="dropdown-item-compact" data-filter-action="sketch-water-paper"><span>Water Paper…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-artistic" aria-haspopup="true" aria-expanded="false"><span>Artistic</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-artistic">
            <button class="dropdown-item-compact" data-filter-action="artistic-poster-edges"><span>Poster Edges…</span></button>
            <button class="dropdown-item-compact" data-filter-action="artistic-cutout"><span>Cutout…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-brush" aria-haspopup="true" aria-expanded="false"><span>Brush Strokes</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-brush">
            <button class="dropdown-item-compact" data-filter-action="brush-ink-outlines"><span>Ink Outlines…</span></button>
            <button class="dropdown-item-compact" data-filter-action="brush-crosshatch"><span>Crosshatch…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-texture" aria-haspopup="true" aria-expanded="false"><span>Texture</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-texture">
            <button class="dropdown-item-compact" data-filter-action="texture-stained-glass"><span>Stained Glass…</span></button>
            <button class="dropdown-item-compact" data-filter-action="texture-grain"><span>Grain…</span></button>
            <button class="dropdown-item-compact" data-filter-action="texture-texturizer"><span>Texturizer…</span></button>
            <button class="dropdown-item-compact" data-filter-action="texture-patchwork"><span>Patchwork…</span></button>
            <button class="dropdown-item-compact" data-filter-action="texture-craquelure"><span>Craquelure…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-distort" aria-haspopup="true" aria-expanded="false"><span>Distort</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-distort">
            <button class="dropdown-item-compact" data-filter-action="distort-twirl"><span>Twirl…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-pinch"><span>Pinch…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-spherize"><span>Spherize…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-ripple"><span>Ripple…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-polar"><span>Polar Coordinates…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-lens-correction"><span>Lens Correction…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-ocean-ripple"><span>Ocean Ripple…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-glass"><span>Glass…</span></button>
            <button class="dropdown-item-compact" data-filter-action="distort-shear"><span>Shear…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-render" aria-haspopup="true" aria-expanded="false"><span>Render</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-render">
            <button class="dropdown-item-compact" data-filter-action="render-clouds"><span>Clouds…</span></button>
            <button class="dropdown-item-compact" data-filter-action="render-difference-clouds"><span>Difference Clouds…</span></button>
            <button class="dropdown-item-compact" data-filter-action="render-lens-flare"><span>Lens Flare…</span></button>
            <button class="dropdown-item-compact" data-filter-action="render-fibers"><span>Fibers…</span></button>
          </div>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="filter-other" aria-haspopup="true" aria-expanded="false"><span>Other</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="filter-other">
            <button class="dropdown-item-compact" data-filter-action="other-offset"><span>Offset…</span></button>
            <button class="dropdown-item-compact" data-filter-action="other-custom"><span>Custom…</span></button>
            <button class="dropdown-item-compact" data-filter-action="other-maximum"><span>Maximum…</span></button>
            <button class="dropdown-item-compact" data-filter-action="other-minimum"><span>Minimum…</span></button>
          </div>
        </div>
      </div>
      <div class="ge-3d-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-3d-menu-btn" title="3D" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="M3 7l9 5 9-5"/><path d="M12 12v10"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">3D</span>
        </button>
        <div class="ge-3d-menu dropdown" id="ge-3d-menu" hidden>
          <div class="ge-menu-label">New 3D</div>
          <button class="dropdown-item-compact" data-3d-action="from-file" disabled title="3D is not available in this build"><span>New 3D Layer from File…</span></button>
          <button class="dropdown-item-compact" data-3d-action="from-layer" disabled title="3D is not available in this build"><span>New Mesh from Layer…</span></button>
          <div class="ge-menu-label">Render</div>
          <button class="dropdown-item-compact" data-3d-action="render" disabled title="3D is not available in this build"><span>Render 3D Layer</span></button>
          <div class="ge-menu-label">3D Printing</div>
          <button class="dropdown-item-compact" data-3d-action="print" disabled title="3D is not available in this build"><span>Export as 3D…</span></button>
          <div class="dropdown-section-divider"></div>
          <button class="dropdown-item-compact" data-3d-action="about" disabled title="3D is not available in this build"><span>About 3D support</span></button>
        </div>
      </div>
      <div class="ge-view-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-view-menu-btn" title="Canvas view" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">View</span>
        </button>
        <div class="ge-view-menu dropdown" id="ge-view-menu" hidden>
          <div class="ge-menu-label">Overlays</div>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="rulers"><span class="dropdown-icon ge-view-check">✓</span><span>Rulers</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="grid"><span class="dropdown-icon ge-view-check">✓</span><span>Grid</span></button>
          <label class="ge-view-grid-size"><span>Grid size</span><input id="ge-grid-size" type="number" min="2" max="1000" step="1" value="16"><span>px</span></label>
          <div class="ge-menu-label">Snapping</div>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="snap"><span class="dropdown-icon ge-view-check">✓</span><span>Snap</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="snap-grid"><span class="dropdown-icon ge-view-check">✓</span><span>Snap to grid</span></button>
          <button class="dropdown-item-compact" data-view-action="new-h-guide" title="Add a horizontal guide across the middle"><span class="dropdown-icon">⊢</span><span>New horizontal guide</span></button>
          <button class="dropdown-item-compact" data-view-action="new-v-guide" title="Add a vertical guide down the middle"><span class="dropdown-icon">⊣</span><span>New vertical guide</span></button>
          <button class="dropdown-item-compact" data-view-action="clear-guides"><span class="dropdown-icon">×</span><span>Clear guides</span></button>
          <div class="ge-menu-label">Panels</div>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-options"><span class="dropdown-icon ge-view-check">✓</span><span>Tool Options</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-layers"><span class="dropdown-icon ge-view-check">✓</span><span>Layers</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-swatches"><span class="dropdown-icon ge-view-check">✓</span><span>Swatches</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-gradients"><span class="dropdown-icon ge-view-check">✓</span><span>Gradients</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-patterns"><span class="dropdown-icon ge-view-check">✓</span><span>Patterns</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-properties"><span class="dropdown-icon ge-view-check">✓</span><span>Properties</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-adjustments"><span class="dropdown-icon ge-view-check">✓</span><span>Adjustments</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-histogram"><span class="dropdown-icon ge-view-check">✓</span><span>Histogram</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-info"><span class="dropdown-icon ge-view-check">✓</span><span>Info</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-channels"><span class="dropdown-icon ge-view-check">✓</span><span>Channels</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-paths"><span class="dropdown-icon ge-view-check">✓</span><span>Paths</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-character"><span class="dropdown-icon ge-view-check">✓</span><span>Character</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-navigator"><span class="dropdown-icon ge-view-check">✓</span><span>Navigator</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-history"><span class="dropdown-icon ge-view-check">✓</span><span>History</span></button>
          <button class="dropdown-item-compact ge-view-toggle" role="menuitemcheckbox" data-view-action="panel-actions"><span class="dropdown-icon ge-view-check">✓</span><span>Actions</span></button>
          <div class="ge-menu-label">Panel layout</div>
          <button class="dropdown-item-compact" data-view-action="panel-show-all"><span class="dropdown-icon">▣</span><span>Show all</span></button>
          <button class="dropdown-item-compact" data-view-action="panel-hide-all"><span class="dropdown-icon">▢</span><span>Hide all</span></button>
          <button class="dropdown-item-compact" data-view-action="panel-reset"><span class="dropdown-icon">↺</span><span>Reset to default</span></button>
          <div class="ge-menu-label">Screen</div>
          <button class="dropdown-item-compact" data-view-action="screen-mode" title="Toggle a distraction-free, maximized screen mode"><span class="dropdown-icon">⛶</span><span>Screen Mode</span></button>
        </div>
      </div>
      <div class="ge-plugins-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-plugins-menu-btn" title="Plugins" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h4a2 2 0 1 1 4 0h4v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Plugins</span>
        </button>
        <div class="ge-plugins-menu dropdown" id="ge-plugins-menu" hidden>
          <div class="ge-menu-label">Plugins</div>
          <button class="dropdown-item-compact" data-plugins-action="browse" disabled title="Plugins are not available in this build"><span>Browse Plugins…</span></button>
          <button class="dropdown-item-compact" data-plugins-action="manage" disabled title="Plugins are not available in this build"><span>Manage Plugins…</span></button>
          <div class="dropdown-section-divider"></div>
          <button class="dropdown-item-compact" data-plugins-action="script" disabled title="Plugins are not available in this build"><span>Run Editor Script…</span></button>
        </div>
      </div>
      <div class="ge-window-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-window-menu-btn" title="Window panels" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Window</span>
        </button>
        <div class="ge-window-menu dropdown" id="ge-window-menu" hidden>
          <button type="button" class="dropdown-item-compact ge-menu-parent" data-submenu="window-workspace" aria-haspopup="true" aria-expanded="false"><span>Workspace</span><span class="ge-submenu-caret">›</span></button>
          <div class="ge-submenu" data-submenu-panel="window-workspace">
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-essentials"><span class="dropdown-icon ge-window-check">●</span><span>Essentials</span></button>
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-photography"><span class="dropdown-icon ge-window-check">●</span><span>Photography</span></button>
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-painting"><span class="dropdown-icon ge-window-check">●</span><span>Painting</span></button>
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-graphic"><span class="dropdown-icon ge-window-check">●</span><span>Graphic &amp; Web</span></button>
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-minimal"><span class="dropdown-icon ge-window-check">●</span><span>Minimal</span></button>
            <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="workspace-custom"><span class="dropdown-icon ge-window-check">●</span><span>Custom</span></button>
            <div class="ge-submenu-sep"></div>
            <button type="button" class="dropdown-item-compact ge-window-save" data-window-action="workspace-save"><span class="dropdown-icon">＋</span><span>Save Current</span></button>
          </div>
          <div class="ge-menu-label">Panels</div>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="quick-edit"><span class="dropdown-icon ge-window-check">●</span><span>Quick Edit</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="options"><span class="dropdown-icon ge-window-check">●</span><span>Tool Options</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="layers"><span class="dropdown-icon ge-window-check">●</span><span>Layers</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="properties"><span class="dropdown-icon ge-window-check">●</span><span>Properties</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="character"><span class="dropdown-icon ge-window-check">●</span><span>Character</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="adjustments"><span class="dropdown-icon ge-window-check">●</span><span>Adjustments</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="swatches"><span class="dropdown-icon ge-window-check">●</span><span>Swatches</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="gradients"><span class="dropdown-icon ge-window-check">●</span><span>Gradients</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="channels"><span class="dropdown-icon ge-window-check">●</span><span>Channels</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="paths"><span class="dropdown-icon ge-window-check">●</span><span>Paths</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="patterns"><span class="dropdown-icon ge-window-check">●</span><span>Patterns</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="histogram"><span class="dropdown-icon ge-window-check">●</span><span>Histogram</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="info"><span class="dropdown-icon ge-window-check">●</span><span>Info</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="navigator"><span class="dropdown-icon ge-window-check">●</span><span>Navigator</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="history"><span class="dropdown-icon ge-window-check">●</span><span>History</span></button>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="actions"><span class="dropdown-icon ge-window-check">●</span><span>Actions</span></button>
          <div class="ge-menu-label">Help</div>
          <button class="dropdown-item-compact ge-window-toggle" role="menuitemcheckbox" aria-checked="false" data-window-action="shortcuts"><span class="dropdown-icon ge-window-check">●</span><span>Keyboard Shortcuts</span></button>
        </div>
      </div>
      <div class="ge-help-wrap">
        <button class="ge-btn ge-btn-sm ge-stacked-btn" id="ge-help-menu-btn" title="Help" aria-haspopup="true">
          <span class="ge-stacked-glyph"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span class="ge-stacked-caret">▾</span></span>
          <span class="ge-stacked-label">Help</span>
        </button>
        <div class="ge-help-menu dropdown" id="ge-help-menu" hidden>
          <div class="ge-menu-label">Help</div>
          <button class="dropdown-item-compact" data-help-action="shortcuts"><span>Keyboard Shortcuts</span></button>
          <button class="dropdown-item-compact" data-help-action="about"><span>About Ajax Image Editor</span></button>
        </div>
      </div>
    </div>
    <div class="ge-topbar-right">
      <span class="ge-alpha-badge" title="This editor is in active development — expect rough edges">ALPHA</span>
      <button class="ge-btn ge-btn-sm" id="ge-shortcuts-btn" title="Keyboard shortcuts (?)" aria-label="Shortcuts">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="position:relative;top:2px;"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm" id="ge-maximize-toggle" title="Maximize editor" aria-label="Maximize editor" aria-pressed="false">
        <svg class="ge-max-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
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
            <svg hidden class="ge-save-state-icon ge-save-state-error" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          </span>
          <span class="ge-stacked-label ge-save-action-label">SAVE</span>
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
          <button class="dropdown-item-compact" id="ge-export-png" title="Download the flattened image as a PNG file">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15l4-4 5 5"/><circle cx="9" cy="8" r="1.3"/></svg></span>
            <span>Save as PNG</span>
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
    <div class="ge-footer-history" role="group" aria-label="History">
      <button class="ge-btn ge-btn-sm" id="ge-undo" title="Undo" aria-label="Undo">↩</button>
      <button class="ge-btn ge-btn-sm" id="ge-redo" title="Redo" aria-label="Redo">↪</button>
      <button class="ge-btn ge-btn-sm" id="ge-history-btn" title="History — click an entry to jump to that state" aria-label="History"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><polyline points="12 7 12 12 16 14"/></svg></button>
      <button class="ge-btn ge-btn-sm" id="ge-compare-btn" title="Show the document before editing" aria-label="Show the document before editing" aria-pressed="false"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 7h14M5 17h14"/></svg></button>
    </div>
    <span class="ge-status-tool" id="ge-status-tool" aria-live="polite"></span>
    <span class="ge-canvas-size" id="ge-canvas-size" title="Document size"></span>
    <span class="ge-status-model" id="ge-status-model" title="AI edit model"></span>
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
