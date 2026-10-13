/**
 * Static markup for misc floating popups that live above the canvas.
 *
 * All pure DOM. Caller wires every ID via document.getElementById /
 * el.querySelector after appending.
 */

import { TOOL_SHORTCUTS } from '../tool-shortcuts.js';

/** Keyboard-shortcuts popover. */
export function shortcutsPopupHTML() {
  return `
      <div id="ge-shortcuts-handle" style="display:flex;align-items:center;gap:6px;margin:-4px -6px 4px;padding:4px 6px;cursor:grab;user-select:none;touch-action:none;">
        <span style="display:inline-flex;flex-direction:column;gap:2px;margin-right:2px;opacity:0.35;">
          <span style="display:block;width:18px;height:2px;border-radius:1px;background:currentColor;"></span>
          <span style="display:block;width:18px;height:2px;border-radius:1px;background:currentColor;"></span>
        </span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.8"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>
        <strong style="font-size:12px;letter-spacing:0.3px;">Editor Shortcuts</strong>
        <span style="flex:1"></span>
        <button id="ge-shortcuts-close" class="ge-btn ge-btn-sm" style="padding:0 6px;height:20px;line-height:1;background:none;border:none;opacity:0.55;cursor:pointer;color:var(--fg);">✖</button>
      </div>
      <div class="ge-shortcuts-grid">
        <div class="ge-shortcuts-col">
          <h5>Tools</h5>
          <div><kbd>V</kbd> Move</div>
          <div><kbd>H</kbd> Hand <span style="opacity:0.5">(hold Space temporarily)</span></div>
          <div><kbd>Arrow</kbd> Move 1 px <span style="opacity:0.5">(Shift = 10 px)</span></div>
          <div><kbd>T</kbd> Text</div>
          <div><kbd>B</kbd> Brush</div>
          <div><kbd>E</kbd> Eraser</div>
          <div><kbd>${TOOL_SHORTCUTS.clone}</kbd> Clone Stamp <span style="opacity:0.5">(Alt-click = set source)</span></div>
          <div><kbd>L</kbd> Lasso</div>
          <div><kbd>W</kbd> Wand</div>
          <div><kbd>${TOOL_SHORTCUTS.marquee}</kbd> Marquee</div>
          <div><kbd>C</kbd> Crop</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Edit</h5>
          <div><kbd>Ctrl</kbd>+<kbd>Z</kbd> Undo</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> Redo</div>
          <div><kbd>Ctrl</kbd>+<kbd>S</kbd> Save</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> Save to Gallery</div>
          <div><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>J</kbd> New Layer</div>
          <div><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>G</kbd> Clipping Mask</div>
          <div><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> Free Transform</div>
          <div><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>T</kbd> Canvas size…</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Selection</h5>
          <div><kbd>Ctrl</kbd>+<kbd>A</kbd> Select All</div>
          <div><kbd>Ctrl</kbd>+<kbd>D</kbd> Deselect</div>
          <div><kbd>Ctrl</kbd>+<kbd>C</kbd> Copy</div>
          <div><kbd>Ctrl</kbd>+<kbd>X</kbd> Cut</div>
          <div><kbd>Ctrl</kbd>+<kbd>J</kbd> Copy selection to layer / duplicate layer</div>
          <div><kbd>Delete</kbd> Delete pixels</div>
          <div><kbd>Esc</kbd> Cancel selection / crop</div>
        </div>
        <div class="ge-shortcuts-col">
          <h5>Brush / Mask</h5>
          <div><kbd>[</kbd> Brush size −</div>
          <div><kbd>]</kbd> Brush size +</div>
          <div>Drag tolerance slider → live wand retune</div>
        </div>
      </div>
      <div style="margin-top:8px;font-size:10px;opacity:0.5;text-align:center;">Press <kbd>?</kbd> or click the keyboard icon to toggle.</div>
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
          <div class="modal-header"><h4 id="ge-canvas-prompt-title">New project</h4></div>
          <div class="modal-body">
            <div class="ge-canvas-prompt-section-label"><svg class="ge-canvas-prompt-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"></rect><path d="M3 9h18M9 3v18"></path></svg><span>Resolution templates</span></div>
            <div class="ge-canvas-preset-grid" role="group" aria-label="Resolution templates">
              <button type="button" class="ge-canvas-preset" data-canvas-preset="1024x1024"><svg class="ge-canvas-preset-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="1"></rect></svg><span>Square</span><strong>1024 × 1024</strong></button>
              <button type="button" class="ge-canvas-preset" data-canvas-preset="1920x1080"><svg class="ge-canvas-preset-icon" width="14" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="1"></rect></svg><span>Landscape</span><strong>1920 × 1080</strong></button>
              <button type="button" class="ge-canvas-preset" data-canvas-preset="1080x1920"><svg class="ge-canvas-preset-icon" width="10" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="1"></rect></svg><span>Story</span><strong>1080 × 1920</strong></button>
              <button type="button" class="ge-canvas-preset" data-canvas-preset="1080x1350"><svg class="ge-canvas-preset-icon" width="11" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="1"></rect></svg><span>Portrait</span><strong>1080 × 1350</strong></button>
              <button type="button" class="ge-canvas-preset" data-canvas-preset="2480x3508"><svg class="ge-canvas-preset-icon" width="11" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 2h9l3 3v17H6zM15 2v4h4"></path></svg><span>A4</span><strong>2480 × 3508</strong></button>
              <button type="button" class="ge-canvas-preset" data-canvas-preset="3840x2160"><svg class="ge-canvas-preset-icon" width="14" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="1"></rect><path d="M8 22h8M12 20v2"></path></svg><span>4K</span><strong>3840 × 2160</strong></button>
            </div>
            <div class="ge-canvas-prompt-section-label ge-canvas-prompt-custom-label">Custom size</div>
            <label class="ge-canvas-units-field"><span class="ge-canvas-control-label"><svg class="ge-canvas-prompt-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16v16H4z"></path><path d="M8 4v16M16 4v16M4 8h16M4 16h16"></path></svg><span>Units</span></span><select id="ge-canvas-prompt-units"><option value="px">Pixels</option><option value="percent">Percent</option></select></label>
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
            <div class="ge-canvas-anchor-options">
              <div class="ge-canvas-anchor-label">Anchor</div>
              <div class="ge-canvas-anchor-grid" role="group" aria-label="Canvas anchor">
              ${[['0,0','Top left'],['0.5,0','Top'],['1,0','Top right'],['0,0.5','Left'],['0.5,0.5','Center'],['1,0.5','Right'],['0,1','Bottom left'],['0.5,1','Bottom'],['1,1','Bottom right']].map(([value, label], index) => `<button type="button" class="ge-canvas-anchor${index === 0 ? ' active' : ''}" data-canvas-anchor="${value}" title="${label}" aria-label="${label}"></button>`).join('')}
              </div>
            </div>
            <div class="ge-canvas-resize-options">
              <label class="ge-canvas-lock-option"><input type="checkbox" id="ge-canvas-prompt-lock" /> <span>Keep proportions</span></label>
              <label class="ge-canvas-interpolation-field"><span class="ge-canvas-control-label"><svg class="ge-canvas-prompt-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 18 9 13l3 3 8-8"></path><path d="M15 8h5v5"></path></svg><span>Interpolation</span></span><select id="ge-canvas-prompt-interpolation"><option value="high">Smooth</option><option value="medium">Balanced</option><option value="low">Crisp</option></select></label>
            </div>
          </div>
          <div class="modal-footer ge-canvas-prompt-footer">
            <p class="ge-canvas-prompt-hint">Pixels, or type a ratio like 3x5 / 16:9 in either field.</p>
            <button class="confirm-btn confirm-btn-secondary" id="ge-canvas-prompt-cancel">Cancel</button>
            <button class="confirm-btn confirm-btn-primary" id="ge-canvas-prompt-ok">Create</button>
          </div>
        </div>`;
}
