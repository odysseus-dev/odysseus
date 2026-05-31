/**
 * Static markup for misc floating popups that live above the canvas.
 *
 * All pure DOM. Caller wires every ID via document.getElementById /
 * el.querySelector after appending.
 */

/** Keyboard-shortcuts popover. */
export function shortcutsPopupHTML() {
  return `
      <div id="ge-shortcuts-handle" style="display:flex;align-items:center;gap:6px;margin:-4px -6px 4px;padding:4px 6px;cursor:grab;user-select:none;touch-action:none;">
        <span style="display:inline-flex;flex-direction:column;gap:2px;margin-right:2px;opacity:0.35;">
          <span style="display:block;width:18px;height:2px;border-radius:1px;background:currentColor;"></span>
          <span style="display:block;width:18px;height:2px;border-radius:1px;background:currentColor;"></span>
        </span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.8"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>
        <strong style="font-size:0.75rem;letter-spacing:0.3px;">Editor Shortcuts</strong>
        <span style="flex:1"></span>
        <button id="ge-shortcuts-close" class="ge-btn ge-btn-sm" style="padding:0 6px;height:20px;line-height:1;background:none;border:none;opacity:0.55;cursor:pointer;color:var(--fg);">✖</button>
      </div>
      <div class="ge-shortcuts-grid">
        <div class="ge-shortcuts-col">
          <h5>Tools</h5>
          <div><kbd>V</kbd> Move</div>
          <div><kbd>T</kbd> Transform</div>
          <div><kbd>B</kbd> Brush</div>
          <div><kbd>E</kbd> Eraser</div>
          <div><kbd>K</kbd> Clone Stamp <span style="opacity:0.5">(Alt-click = set source)</span></div>
          <div><kbd>L</kbd> Lasso</div>
          <div><kbd>W</kbd> Wand</div>
          <div><kbd>M</kbd> Inpaint</div>
          <div><kbd>E</kbd> Eraser</div>
          <div><kbd>C</kbd> Crop</div>
          <div><kbd>S</kbd> Sharpen</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Edit</h5>
          <div><kbd>Ctrl</kbd>+<kbd>Z</kbd> Undo</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> Redo</div>
          <div><kbd>Ctrl</kbd>+<kbd>S</kbd> Save</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> Save to Gallery</div>
          <div><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>J</kbd> New Layer</div>
          <div><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> Free Transform</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>T</kbd> Canvas size…</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Selection</h5>
          <div><kbd>Ctrl</kbd>+<kbd>A</kbd> Select All</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>D</kbd> Deselect</div>
          <div><kbd>Ctrl</kbd>+<kbd>C</kbd> Copy to layer</div>
          <div><kbd>Ctrl</kbd>+<kbd>X</kbd> Cut lasso</div>
          <div><kbd>Ctrl</kbd>+<kbd>D</kbd> Delete pixels</div>
          <div><kbd>Esc</kbd> Cancel selection / crop</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Brush / Mask</h5>
          <div><kbd>[</kbd> Brush size −</div>
          <div><kbd>]</kbd> Brush size +</div>
          <div>Drag tolerance slider → live wand retune</div>
        </div>
      </div>
      <div style="margin-top:8px;font-size:0.6rem;opacity:0.5;text-align:center;">Press <kbd>?</kbd> or click the keyboard icon to toggle.</div>
    `;
}


/**
 * History panel — sidebar listing all undo entries.
 * @param {string} historyIcon  Inline SVG markup for the title icon.
 */
export function historyPanelHTML(historyIcon) {
  return `
    <div class="ge-history-head" data-history-drag>
      <span class="ge-adj-icon">${historyIcon}</span>
      <span class="ge-history-title">History</span>
      <span class="ge-head-btns">
        <button class="ge-adj-min" type="button" title="Minimise">&minus;</button>
      </span>
    </div>
    <div class="ge-history-list" id="ge-history-list"></div>
  `;
}


/**
 * Empty-canvas size-prompt modal — body markup (caller controls show /
 * hide and wires the Cancel / Create buttons).
 */
export function canvasSizePromptHTML() {
  return `
        <div class="modal-content ge-canvas-prompt">
          <div class="modal-header"><h4 id="ge-canvas-prompt-title">New canvas</h4></div>
          <div class="modal-body">
            <div class="ge-canvas-prompt-row">
              <label class="ge-canvas-prompt-field">
                <span>Width</span>
                <input type="text" id="ge-canvas-prompt-w" inputmode="numeric" value="1024">
              </label>
              <span class="ge-canvas-prompt-x">×</span>
              <label class="ge-canvas-prompt-field">
                <span>Height</span>
                <input type="text" id="ge-canvas-prompt-h" inputmode="numeric" value="1024">
              </label>
            </div>
            <p class="ge-canvas-prompt-hint">Pixels, or type a ratio like 3x5 / 16:9 in either field.</p>
          </div>
          <div class="modal-footer">
            <button class="confirm-btn confirm-btn-secondary" id="ge-canvas-prompt-cancel">Cancel</button>
            <button class="confirm-btn confirm-btn-primary" id="ge-canvas-prompt-ok">Create</button>
          </div>
        </div>`;
}
