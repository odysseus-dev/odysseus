/**
 * Build the editor's right-panel controls innerHTML.
 *
 * Returns the string — caller creates the wrapper element, attaches its
 * own touch / swipe-to-dismiss listeners, then sets innerHTML. Per-tool
 * sections are all toggled `display:none` here; the tool-switch handler
 * in galleryEditor.js shows the section matching the active tool.
 *
 * @param {{ color: string, brushSize: number, wandTolerance: number }} ctx
 * @returns {string}
 */
export function controlsHTML({ color, brushSize, wandTolerance }) {
  const brushSliderValue = Math.round(Math.log(Math.max(1, brushSize)) / Math.log(800) * 1000);
  return `
    <div id="ge-pen-section" style="display:none;">
      <div class="ge-section-title">Pen Selection</div>
      <div class="ge-control-row ge-actions">
        <button type="button" class="ge-btn ge-btn-sm" id="ge-pen-commit" title="Close path and make selection (Enter)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg> Make selection</button>
        <button type="button" class="ge-btn ge-btn-sm" id="ge-pen-cancel" title="Cancel path (Escape)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m18 6-12 12M6 6l12 12"/></svg> Cancel</button>
      </div>
    </div>
    <div class="ge-layer-geometry-section" id="ge-layer-geometry-section">
      <div class="ge-section-title"><span>Position</span><span id="ge-layer-geometry-name"></span></div>
      <div class="ge-layer-geometry-grid">
        <label><span>X</span><input id="ge-layer-x" type="number" step="1" inputmode="numeric" /></label>
        <label><span>Y</span><input id="ge-layer-y" type="number" step="1" inputmode="numeric" /></label>
        <label><span>W</span><input id="ge-layer-width" type="number" readonly title="Edit width with Transform" /></label>
        <label><span>H</span><input id="ge-layer-height" type="number" readonly title="Edit height with Transform" /></label>
      </div>
    </div>
    <div id="ge-brush-controls">
      <div class="ge-control-row" id="ge-color-row">
        <label>Color</label>
        <input type="color" class="ge-color-picker" value="${color}" />
      </div>
      <div class="ge-control-row">
        <label>Size <span class="ge-size-label">${brushSize}px</span></label>
      <input type="range" class="ge-size-slider" min="0" max="1000" value="${brushSliderValue}" />
      </div>
      <div class="ge-section-title">Stroke</div>
      <div class="ge-control-row ge-eraser-row">
        <label>Spacing <span id="ge-brush-spacing-label">15%</span></label>
        <input type="range" id="ge-brush-spacing" min="1" max="100" value="15" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <label>Smoothing <span id="ge-brush-smoothing-label">25%</span></label>
        <input type="range" id="ge-brush-smoothing" min="0" max="95" value="25" />
      </div>
      <div class="ge-control-row">
        <label for="ge-brush-blend">Blend</label>
        <select id="ge-brush-blend">
          <option value="source-over">Normal</option><option value="multiply">Multiply</option>
          <option value="screen">Screen</option><option value="overlay">Overlay</option>
          <option value="soft-light">Soft Light</option><option value="color">Color</option>
        </select>
      </div>
      <div class="ge-control-row ge-brush-pressure-row">
        <span class="ge-pressure-option" title="Pen pressure changes brush size"><span>Size</span><label class="toggle-switch"><input type="checkbox" id="ge-pressure-size" checked /><span class="toggle-slider"></span></label></span>
        <span class="ge-pressure-option" title="Pen pressure changes opacity"><span>Opacity</span><label class="toggle-switch"><input type="checkbox" id="ge-pressure-opacity" /><span class="toggle-slider"></span></label></span>
        <span class="ge-pressure-option" title="Pen pressure changes flow"><span>Flow</span><label class="toggle-switch"><input type="checkbox" id="ge-pressure-flow" checked /><span class="toggle-slider"></span></label></span>
      </div>
      <div class="ge-control-row ge-brush-preset-row">
        <select id="ge-brush-preset" aria-label="Brush preset"></select>
        <button type="button" class="ge-btn ge-btn-sm" id="ge-brush-preset-save" title="Save current brush preset">Save</button>
        <button type="button" class="ge-btn ge-btn-sm ge-icon-btn" id="ge-brush-preset-delete" title="Delete selected preset" aria-label="Delete selected preset">×</button>
      </div>
    </div>
    <div class="ge-gradient-section" id="ge-gradient-section" style="display:none;">
      <div class="ge-section-title">Gradient</div>
      <div class="ge-control-row"><label for="ge-gradient-type">Type</label><select id="ge-gradient-type"><option value="linear-gradient">Linear</option><option value="radial-gradient">Radial</option></select></div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Start</span><input type="color" class="ge-color-picker" id="ge-gradient-start" value="${color}" /></label>
        <label class="ge-text-field"><span>End</span><input type="color" class="ge-color-picker" id="ge-gradient-end" value="#ffffff" /></label>
      </div>
      <div class="ge-control-row ge-gradient-mid-row">
        <span class="ge-gradient-mid-toggle"><span>Midpoint</span><label class="toggle-switch"><input type="checkbox" id="ge-gradient-mid-enabled" /><span class="toggle-slider"></span></label></span>
        <input type="color" class="ge-color-picker" id="ge-gradient-mid" value="#808080" title="Midpoint color" disabled />
        <input type="range" id="ge-gradient-mid-position" min="1" max="99" value="50" title="Midpoint position" disabled />
        <span id="ge-gradient-mid-position-label">50%</span>
      </div>
      <div class="ge-gradient-extra-stops" id="ge-gradient-extra-stops"></div>
      <button type="button" class="ge-btn ge-btn-sm ge-gradient-add-stop" id="ge-gradient-add-stop">Add stop</button>
      <div class="ge-control-row"><label for="ge-gradient-end-alpha">End opacity <span id="ge-gradient-end-alpha-label">100%</span></label><input type="range" id="ge-gradient-end-alpha" min="0" max="100" value="100" /></div>
      <div class="ge-control-row"><label for="ge-gradient-opacity">Opacity <span id="ge-gradient-opacity-label">100%</span></label><input type="range" id="ge-gradient-opacity" min="0" max="100" value="100" /></div>
      <p class="ge-section-hint">Drag across the active layer. Switch tools or press Esc to cancel.</p>
    </div>
    <div class="ge-eraser-section" id="ge-eyedropper-section" style="display:none;">
      <div class="ge-section-title">Eyedropper</div>
      <div class="ge-eyedropper-live" id="ge-eyedropper-live" aria-live="polite">
        <canvas class="ge-eyedropper-loupe" id="ge-eyedropper-loupe" width="84" height="84" aria-hidden="true"></canvas>
        <span class="ge-eyedropper-live-swatch" id="ge-eyedropper-live-swatch" aria-hidden="true"></span>
        <span class="ge-eyedropper-live-values">
          <code id="ge-eyedropper-live-value">Move over the canvas</code>
          <span id="ge-eyedropper-live-rgb">RGB --</span>
          <span id="ge-eyedropper-live-hsl">HSL --</span>
        </span>
      </div>
      <div class="ge-control-row"><label for="ge-eyedropper-sample">Sample</label><select id="ge-eyedropper-sample"><option value="composite">All layers</option><option value="layer">Active layer</option></select></div>
    </div>
    <div class="ge-text-section" id="ge-text-section" style="display:none;">
      <textarea id="ge-text-content" rows="3" placeholder="Type text..." aria-label="Text content"></textarea>
      <div class="ge-text-grid">
        <label class="ge-text-field ge-text-font-field"><span>Font</span>
          <select id="ge-text-font">
            <option value="Arial">Arial</option>
            <option value="Verdana">Verdana</option>
            <option value="Georgia">Georgia</option>
            <option value="Times New Roman">Times New Roman</option>
            <option value="Courier New">Courier New</option>
            <option value="Impact">Impact</option>
            <option value="system-ui">System</option>
          </select>
        </label>
        <label class="ge-text-field ge-text-size-field"><span>Size</span>
          <input id="ge-text-size" type="number" min="1" max="2000" value="48" />
        </label>
      </div>
      <div class="ge-control-row ge-text-format-row">
        <input type="color" class="ge-color-picker" id="ge-text-color" value="${color}" title="Text color" aria-label="Text color" />
        <button type="button" class="ge-text-toggle" id="ge-text-bold" title="Bold" aria-pressed="false"><strong>B</strong></button>
        <button type="button" class="ge-text-toggle" id="ge-text-italic" title="Italic" aria-pressed="false"><em>I</em></button>
        <span class="ge-text-format-sep"></span>
        <button type="button" class="ge-text-toggle active" data-text-align="left" title="Align left" aria-pressed="true">&#8676;</button>
        <button type="button" class="ge-text-toggle" data-text-align="center" title="Align center" aria-pressed="false">&#8596;</button>
        <button type="button" class="ge-text-toggle" data-text-align="right" title="Align right" aria-pressed="false">&#8677;</button>
      </div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Line</span>
          <input id="ge-text-line-height" type="number" min="0.5" max="5" step="0.1" value="1.2" />
        </label>
        <label class="ge-text-field"><span>Spacing</span>
          <input id="ge-text-letter-spacing" type="number" min="-100" max="500" step="0.5" value="0" />
        </label>
      </div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Frame</span>
          <input id="ge-text-frame-width" type="number" min="1" max="10000" step="1" value="320" />
        </label>
        <label class="ge-text-field"><span>Height</span>
          <input id="ge-text-frame-height" type="number" min="0" max="10000" step="1" value="0" />
        </label>
        <label class="ge-text-field"><span>Vertical</span>
          <select id="ge-text-vertical-align"><option value="top">Top</option><option value="middle">Middle</option><option value="bottom">Bottom</option></select>
        </label>
      </div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Stroke</span>
          <input id="ge-text-stroke-width" type="number" min="0" max="100" step="1" value="0" />
        </label>
        <input type="color" class="ge-color-picker" id="ge-text-stroke-color" value="#000000" title="Stroke color" aria-label="Stroke color" />
      </div>
      <label class="ge-text-auto-width-option"><span>Auto width</span><span class="toggle-switch"><input type="checkbox" id="ge-text-auto-width" /><span class="toggle-slider"></span></span></label>
      <button type="button" class="ge-btn ge-btn-sm" id="ge-text-rasterize">Rasterize</button>
    </div>
    <div class="ge-shape-section" id="ge-shape-section" style="display:none;">
      <div class="ge-section-title">Shape</div>
      <div class="ge-shape-types" role="group" aria-label="Shape type">
        <button type="button" class="ge-text-toggle active" data-shape-type="rectangle" title="Rectangle" aria-pressed="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="5" width="16" height="14" rx="1"/></svg></button>
        <button type="button" class="ge-text-toggle" data-shape-type="ellipse" title="Ellipse" aria-pressed="false"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="8" ry="7"/></svg></button>
        <button type="button" class="ge-text-toggle" data-shape-type="line" title="Line" aria-pressed="false"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="19" x2="20" y2="5"/></svg></button>
        <button type="button" class="ge-text-toggle" data-shape-type="polygon" title="Polygon" aria-pressed="false"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3 9 7-3.5 11h-11L3 10Z"/></svg></button>
      </div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Fill</span><input type="color" class="ge-color-picker" id="ge-shape-fill" value="${color}" /></label>
        <label class="ge-text-field"><span>Stroke</span><input type="color" class="ge-color-picker" id="ge-shape-stroke" value="#111111" /></label>
      </div>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Fill type</span>
          <select id="ge-shape-fill-type"><option value="solid">Solid</option><option value="linear-gradient">Linear gradient</option></select>
        </label>
        <label class="ge-text-field ge-shape-gradient-angle-field" hidden><span>Angle</span><input id="ge-shape-gradient-angle" type="number" min="-36000" max="36000" step="1" value="0" /></label>
      </div>
      <div class="ge-text-grid ge-shape-gradient-fields" hidden>
        <label class="ge-text-field"><span>Start</span><input type="color" class="ge-color-picker" id="ge-shape-gradient-start" value="#ffffff" /></label>
        <label class="ge-text-field"><span>End</span><input type="color" class="ge-color-picker" id="ge-shape-gradient-end" value="#000000" /></label>
        <label class="ge-text-field"><span>Midpoint</span><input type="color" class="ge-color-picker" id="ge-shape-gradient-mid" value="#808080" /></label>
        <label class="ge-text-field"><span>Position</span><input id="ge-shape-gradient-mid-position" type="number" min="1" max="99" step="1" value="50" /></label>
        <label class="ge-text-field ge-shape-gradient-mid-enabled"><span>Use midpoint</span><input id="ge-shape-gradient-mid-enabled" type="checkbox" /></label>
      </div>
      <div class="ge-shape-gradient-extra-stops" id="ge-shape-gradient-extra-stops"></div>
      <button type="button" class="ge-btn ge-btn-sm ge-shape-gradient-add-stop" id="ge-shape-gradient-add-stop">Add stop</button>
      <div class="ge-text-grid">
        <label class="ge-text-field"><span>Width</span><input id="ge-shape-stroke-width" type="number" min="0" max="500" step="1" value="2" /></label>
        <label class="ge-text-field"><span>Radius</span><input id="ge-shape-radius" type="number" min="0" max="5000" step="1" value="0" /></label>
      </div>
      <label class="ge-text-field ge-shape-sides-field" hidden><span>Sides</span><input id="ge-shape-sides" type="number" min="3" max="24" step="1" value="5" /></label>
      <button type="button" class="ge-btn ge-btn-sm" id="ge-shape-rasterize">Rasterize</button>
    </div>
    <div class="ge-marquee-section" id="ge-marquee-section" style="display:none;">
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;">
        <button type="button" class="ge-btn ge-btn-sm ge-marquee-shape-btn active" data-marquee-shape="rectangle" title="Rectangular marquee" aria-pressed="true">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="5" width="16" height="14"/></svg>
        </button>
        <button type="button" class="ge-btn ge-btn-sm ge-marquee-shape-btn" data-marquee-shape="ellipse" title="Elliptical marquee" aria-pressed="false">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="8" ry="7"/></svg>
        </button>
        <span style="flex:1"></span>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn active" data-wand-mode="replace" title="New selection">New</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="add" title="Add to selection">+</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="subtract" title="Subtract from selection">−</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="intersect" title="Intersect selection">∩</button>
      </div>
      <div class="ge-marquee-constraint-row">
        <label for="ge-marquee-constraint">Style</label>
        <select id="ge-marquee-constraint" title="Marquee sizing style">
          <option value="free">Free</option>
          <option value="ratio">Fixed ratio</option>
          <option value="size">Fixed size</option>
        </select>
        <div class="ge-marquee-dimensions" hidden>
          <label>W <input type="number" id="ge-marquee-width" min="1" step="1" value="1" inputmode="decimal" /></label>
          <label>H <input type="number" id="ge-marquee-height" min="1" step="1" value="1" inputmode="decimal" /></label>
          <button type="button" class="ge-icon-btn" id="ge-marquee-swap" title="Swap width and height" aria-label="Swap width and height">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m16 3 4 4-4 4"/><path d="M4 7h16"/><path d="m8 21-4-4 4-4"/><path d="M20 17H4"/></svg>
          </button>
        </div>
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;flex-wrap:wrap;">
        <button class="ge-btn ge-btn-sm ge-mask-vis-btn visible" id="ge-marquee-vis" title="Hide selection overlay" aria-label="Toggle selection overlay">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-marquee-clear" title="Clear selection">Clear</button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-marquee-invert" title="Invert selection">Invert</button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-marquee-delete" title="Erase selected pixels">Erase</button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-marquee-copy" title="Copy selection to a new layer">Copy Layer</button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-marquee-mask" title="Add selection to mask">To Mask</button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-iconlabel ge-quick-mask-toggle" title="Edit selection as Quick Mask (Q)" aria-pressed="false">Quick Mask</button>
      </div>
    </div>
    <div class="ge-lasso-section" id="ge-lasso-section" style="display:none;">
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;" title="How the next lasso combines with the current selection.">
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn active" data-wand-mode="replace" title="New selection">New</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="add" title="Add to selection">+</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="subtract" title="Subtract from selection">−</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="intersect" title="Intersect selection">∩</button>
      </div>
      <div class="ge-control-row ge-eraser-row ge-sel-refine" id="ge-lasso-refine-feather" style="display:none;">
        <span class="ge-eraser-preview" id="ge-lasso-feather-preview" aria-hidden="true"></span>
        <label>Feather <span id="ge-lasso-feather-label">0px</span></label>
        <input type="range" id="ge-lasso-feather" min="0" max="200" value="0" title="Soften the selection edge — feathers the mask alpha." />
      </div>
      <div class="ge-control-row ge-eraser-row ge-sel-refine" id="ge-lasso-refine-grow" style="display:none;">
        <span class="ge-eraser-preview" id="ge-lasso-grow-preview" aria-hidden="true"></span>
        <label>Edge stroke <span id="ge-lasso-grow-label">0px</span></label>
        <input type="range" id="ge-lasso-grow" min="-40" max="40" value="0" title="Expand (+) or contract (−) the selection before baking." />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;flex-wrap:wrap;">
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-lasso-invert" title="Invert selection (Ctrl+Alt+Shift+I)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
          Invert
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-lasso-delete" title="Delete selected pixels from the layer">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          Delete
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-lasso-copy" title="Copy selection to new layer">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          Copy Layer
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-lasso-mask" title="Convert selection to inpaint mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>
          To Mask
        </button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-iconlabel ge-quick-mask-toggle" title="Edit selection as Quick Mask (Q)" aria-pressed="false">Quick Mask</button>
      </div>
      <p style="font-size:9px;opacity:0.4;margin:4px 0 0;">Draw a freehand selection. Esc to cancel.</p>
    </div>
    <div class="ge-wand-section" id="ge-wand-section" style="display:none;">
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;" title="How the next click combines with the current selection. Shift / Alt held during a click override this for one click.">
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn active" data-wand-mode="replace" title="Replace selection on each click">New</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="add" title="Add to selection (Shift)">+ Add</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="subtract" title="Subtract from selection (Alt)">− Subtract</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="intersect" title="Intersect selection">∩</button>
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-wand-tol-preview" aria-hidden="true"></span>
        <label>Tolerance <span id="ge-wand-tol-label">${wandTolerance}</span></label>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-live-btn" id="ge-wand-live" title="Retune selection while dragging tolerance" aria-pressed="false">Live</button>
        <input type="range" id="ge-wand-tolerance" min="0" max="100" value="${wandTolerance}" />
      </div>
      <div class="ge-control-row ge-eraser-row ge-sel-refine" id="ge-wand-refine-feather" style="display:none;">
        <span class="ge-eraser-preview" id="ge-wand-feather-preview" aria-hidden="true"></span>
        <label>Feather <span id="ge-wand-feather-label">0px</span></label>
        <input type="range" id="ge-wand-feather" min="0" max="200" value="0" title="Soften the selection edge — feathers the mask alpha." />
      </div>
      <div class="ge-control-row ge-eraser-row ge-sel-refine" id="ge-wand-refine-grow" style="display:none;">
        <span class="ge-eraser-preview" id="ge-wand-grow-preview" aria-hidden="true"></span>
        <label>Edge stroke <span id="ge-wand-grow-label">0px</span></label>
        <input type="range" id="ge-wand-grow" min="-40" max="40" value="0" title="Expand (+) or contract (−) the selection before baking." />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;flex-wrap:wrap;">
        <button class="ge-btn ge-btn-sm ge-mask-vis-btn visible" id="ge-wand-vis" title="Hide selection overlay" aria-label="Toggle selection overlay">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-clear" title="Clear the selection">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          Clear
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-invert" title="Invert selection (Ctrl+Alt+Shift+I)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
          Invert
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-delete" title="Delete selected pixels from the layer">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
          Erase
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-copy" title="Copy selection to a new layer">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          Copy Layer
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-mask" title="Add selection to the inpaint mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>
          To Mask
        </button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-iconlabel ge-quick-mask-toggle" title="Edit selection as Quick Mask (Q)" aria-pressed="false">Quick Mask</button>
      </div>
      <p style="font-size:9px;opacity:0.4;margin:4px 0 0;">Click a region to select similar pixels. Shift+click to add, Alt+click to subtract. Esc to clear.</p>
    </div>
    <div class="ge-sam-section" id="ge-sam-section" style="display:none;">
      <div class="ge-section-title ge-section-title-with-help"><span>SAM</span><span class="ge-section-help" tabindex="0" role="img" aria-label="SAM selection help" title="Click an object for visual SAM selection, or type a neutral object label and use Find. The text is only used to locate a region before SAM creates the mask.">?</span></div>
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;" title="How the next SAM selection combines with the current selection. Shift / Alt held during a click override this for one click.">
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn active" data-wand-mode="replace" title="Replace selection">New</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="add" title="Add to selection">+ Add</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="subtract" title="Subtract from selection">− Subtract</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="intersect" title="Intersect selection">∩</button>
      </div>
      <div class="ge-control-row" style="display:flex;gap:6px;align-items:center;min-width:0;">
        <input type="text" class="ge-inpaint-prompt" id="ge-sam-query" placeholder="Object to select..." style="flex:1 1 auto;min-width:0;" />
        <button class="ge-btn ge-btn-sm ge-btn-ai" id="ge-sam-find" style="height:28px;display:inline-flex;align-items:center;gap:5px;" title="Find object and create a SAM mask">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          Find
        </button>
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;flex-wrap:wrap;">
        <button class="ge-btn ge-btn-sm ge-mask-vis-btn visible" id="ge-sam-vis" title="Hide selection overlay" aria-label="Toggle selection overlay">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-sam-clear" title="Clear the selection">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          Clear
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-sam-mask" title="Add selection to the inpaint mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>
          To Mask
        </button>
        <button type="button" class="ge-btn ge-btn-sm ge-btn-iconlabel ge-quick-mask-toggle" title="Edit selection as Quick Mask (Q)" aria-pressed="false">Quick Mask</button>
      </div>
      <p style="font-size:9px;opacity:0.4;margin:4px 0 0;">Click an object, or type a neutral object label. Shift adds, Alt subtracts.</p>
    </div>
    <div class="ge-inpaint-section" id="ge-inpaint-section" style="display:none;">
      <div class="ge-inpaint-popover-head" data-inpaint-drag>
        <div class="ge-section-title ge-section-title-with-help ge-inpaint-popover-title"><span>INPAINT</span><span class="ge-section-help" tabindex="0" role="img" aria-label="How inpaint works" title="Brush the area you want the AI to redraw — the red preview marks the mask region. Use Paint to add, Erase to subtract (or hold Ctrl+Alt to flip for one stroke). Generate fills with what your prompt describes; Remove fills with the surrounding background.">?</span></div>
        <button class="ge-inpaint-popover-close" id="ge-inpaint-popover-close" type="button" title="Close inpaint panel" aria-label="Close inpaint panel">&times;</button>
      </div>
      <div class="ge-section-title ge-section-title-with-help"><span>INPAINT</span><span class="ge-section-help" tabindex="0" role="img" aria-label="How inpaint works" title="Brush the area you want the AI to redraw — the red preview marks the mask region. Use Paint to add, Erase to subtract (or hold Ctrl+Alt to flip for one stroke). Generate fills with what your prompt describes; Remove fills with the surrounding background.">?</span></div>
      <p class="ge-section-hint" style="margin-top:0;">
        Generates or removes from the mask you have selected. Set <strong>Strength</strong> before and adjust <strong>Edge feather / stroke</strong> after.
      </p>
      <div class="ge-section-title" style="margin-top:8px;display:flex;align-items:center;gap:6px;">
        <span>Mask Brush</span>
        <input type="color" class="ge-color-picker ge-inpaint-mask-color" value="#ff6e6e" title="Mask overlay color — purely visual, the model still sees a hard mask either way." />
      </div>
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;" title="Hold Ctrl+Alt to flip temporarily for a single stroke.">
        <button type="button" class="ge-btn ge-btn-sm ge-inpaint-mode-btn active" id="ge-inpaint-mode-paint" style="flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:4px;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>
          Paint
        </button>
        <button type="button" class="ge-btn ge-btn-sm ge-inpaint-mode-btn" id="ge-inpaint-mode-erase" style="flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:4px;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19.4 14.6 14.6 19.4a2 2 0 0 1-2.83 0L4.6 12.23a2 2 0 0 1 0-2.83l7.17-7.17a2 2 0 0 1 2.83 0l4.8 4.8a2 2 0 0 1 0 2.83Z"/><line x1="22" y1="21" x2="7" y2="21"/><line x1="14" y1="3" x2="9" y2="8"/></svg>
          Erase
        </button>
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-inpaint-brush-preview" aria-hidden="true"></span>
        <label>Mask Brush Size <span id="ge-inpaint-brush-label">${brushSize}px</span></label>
        <input type="range" id="ge-inpaint-brush-slider" min="0" max="1000" value="${brushSliderValue}" title="Brush diameter (log scale 1→800px). Use [ and ] for ±10%." />
      </div>
      <div class="ge-control-row ge-actions ge-inpaint-mask-row" style="margin-top:4px;">
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel ge-mask-vis-btn visible" id="ge-mask-vis" title="Hide mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          <span id="ge-mask-vis-label">Hide</span>
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-inpaint-invert" title="Invert mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
          Invert
        </button>
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-inpaint-clear" title="Clear mask">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          Clear
        </button>
      </div>
      <hr class="ge-section-divider" />
      <div class="ge-section-title" style="margin-top:8px;"><span>PROMPT</span></div>
      <input type="text" class="ge-inpaint-prompt" id="ge-inpaint-prompt" placeholder="What to fill the masked area with..." />
      <div class="ge-control-row ge-inpaint-model-row" style="margin-top:6px;">
        <label for="ge-ai-inpaint">Model</label>
        <select id="ge-ai-inpaint" class="ge-ai-model" title="Model for inpainting">
          <option value="">Auto</option>
          <option value="" disabled>──────────</option>
          <option value="__serve_cookbook__">+ Serve a model in Cookbook…</option>
        </select>
      </div>
      <div class="ge-control-row ge-eraser-row" style="margin-top:6px;">
        <span class="ge-eraser-preview" id="ge-strength-preview" aria-hidden="true"></span>
        <label>Strength <span id="ge-strength-label">0.75</span><span class="ge-section-help" tabindex="0" role="img" aria-label="Strength help" title="How much the AI redraws inside the mask. 0 = no change · 1 = full re-generation from your prompt. Recommended: 0.9–1.0 to add/replace an object, 0.6–0.8 to change material or color, 0.3–0.5 for subtle touch-ups. Default 0.75 works for most edits.">?</span></label>
        <input type="range" id="ge-strength-slider" min="10" max="100" value="75" title="How much the AI redraws inside the mask (0 = no change, 1 = full diffusion)." />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:6px;display:flex;gap:6px;align-items:center;min-width:0;">
        <button class="ge-btn ge-btn-primary ge-btn-ai" id="ge-inpaint-run" style="flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:6px;" title="Fill the masked area with what your prompt describes.">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          <span id="ge-inpaint-run-label">Generate</span>
        </button>
        <button class="ge-btn ge-btn-ai" id="ge-inpaint-remove" style="flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:6px;" title="Erase the masked content and fill with the surrounding background. Ignores your prompt.">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          <span id="ge-inpaint-remove-label">Remove</span>
        </button>
        <button class="ge-btn ge-btn-ai" id="ge-inpaint-outpaint" style="flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:6px;" title="Fill the empty (transparent) areas of the canvas with AI-generated content that blends with the existing image. Ignores your brush mask.">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          <span id="ge-inpaint-outpaint-label">Outpaint</span>
        </button>
      </div>
      <hr class="ge-section-divider" id="ge-inpaint-postedge-divider" style="margin-top:14px;" />
      <div class="ge-section-title ge-section-title-with-help" id="ge-inpaint-postedge-title"><span>POSTPROCESS</span><span class="ge-section-help" tabindex="0" role="img" aria-label="What this does" title="Live edge trimming for the last Inpaint Result layer. Edge feather softens the alpha boundary; Edge stroke expands (+) or contracts (−) the visible edge into the AI buffer that was generated around your brush.">?</span></div>
      <p class="ge-section-hint" id="ge-inpaint-postedge-hint" style="margin-top:0;opacity:0.45;">
        Available after Generate.
      </p>
      <div class="ge-control-row ge-eraser-row" id="ge-inpaint-postfeather-row" style="display:none;">
        <span class="ge-eraser-preview" id="ge-feather-preview" aria-hidden="true"></span>
        <label>Edge feather <span id="ge-feather-label">0px</span></label>
        <input type="range" id="ge-feather-slider" min="0" max="200" value="0" title="Blurs the inpaint result's alpha edge — drag to blend the AI fill into the surrounding image. Updates live." />
      </div>
      <div class="ge-control-row ge-eraser-row" id="ge-inpaint-edgestroke-row" style="display:none;">
        <span class="ge-eraser-preview" id="ge-edgestroke-preview" aria-hidden="true"></span>
        <label>Edge stroke <span id="ge-edgestroke-label">0px</span></label>
        <input type="range" id="ge-edgestroke-slider" min="-80" max="80" value="0" title="Expand (+) or contract (−) the inpaint layer's edge before feathering. Uses the AI buffer generated around your brush." />
      </div>
      <div class="ge-control-row ge-actions" id="ge-inpaint-automatch-row" style="display:none;margin-top:6px;">
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel ge-btn-ai" id="ge-inpaint-automatch" style="width:100%;justify-content:center;" title="Match the latest inpaint result to the surrounding colour and lighting using an adjustment layer.">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          Auto match color
        </button>
      </div>
    </div>
    <div class="ge-eraser-section" id="ge-clone-section" style="display:none;">
      <div class="ge-section-title ge-section-title-with-help"><span>Clone</span><span class="ge-section-help" tabindex="0" role="img" aria-label="How clone works" title="Alt-click (desktop) or double-tap (mobile) somewhere on the canvas to set the sample source. Then drag elsewhere to clone those pixels onto the active layer. The source point moves with your brush so the offset stays constant. Size / Opacity / Flow / Softness come from the Brush panel.">?</span></div>
      <p class="ge-section-hint" style="margin-top:0;">
        <strong class="ge-clone-hint-desktop">Alt-click</strong><strong class="ge-clone-hint-mobile">Double-tap</strong> to set source · drag to paint
      </p>
      <div class="ge-clone-source-status" id="ge-clone-source-status" aria-live="polite">
        <span id="ge-clone-source-label">No source selected</span>
        <button type="button" class="ge-btn ge-btn-sm ge-icon-btn" id="ge-clone-source-clear" title="Clear sampled source" aria-label="Clear sampled source">×</button>
      </div>
      <div class="ge-control-row">
        <label for="ge-clone-sample-mode">Sample</label>
        <select id="ge-clone-sample-mode">
          <option value="active-layer">Active layer</option>
          <option value="composite">All visible layers</option>
        </select>
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-clone-preview-opacity" aria-hidden="true"></span>
        <label>Opacity <span id="ge-clone-opacity-label">100%</span></label>
        <input type="range" id="ge-clone-opacity" min="10" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-clone-preview-flow" aria-hidden="true"></span>
        <label>Flow <span id="ge-clone-flow-label">100%</span></label>
        <input type="range" id="ge-clone-flow" min="5" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-clone-preview-softness" aria-hidden="true"></span>
        <label>Softness <span id="ge-clone-softness-label">100%</span></label>
        <input type="range" id="ge-clone-softness" min="0" max="300" value="100" title="Soft brush edge — blurs each stamp for a feathered fade." />
      </div>
    </div>
    <div class="ge-eraser-section" id="ge-brush-section" style="display:none;">
      <div class="ge-section-title"><span>Brush</span></div>
      <div class="ge-control-row ge-eraser-row" id="ge-smudge-strength-row" style="display:none;">
        <span class="ge-eraser-preview" id="ge-smudge-preview-strength" aria-hidden="true"></span>
        <label>Strength <span id="ge-smudge-strength-label">65%</span></label>
        <input type="range" id="ge-smudge-strength" min="5" max="100" value="65" title="How strongly Smudge carries sampled pixels into the stroke." />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-brush-preview-opacity" aria-hidden="true"></span>
        <label>Opacity <span id="ge-brush-opacity-label">100%</span></label>
        <input type="range" id="ge-brush-opacity" min="10" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-brush-preview-flow" aria-hidden="true"></span>
        <label>Flow <span id="ge-brush-flow-label">100%</span></label>
        <input type="range" id="ge-brush-flow" min="5" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-brush-preview-softness" aria-hidden="true"></span>
        <label>Softness <span id="ge-brush-softness-label">100%</span></label>
        <input type="range" id="ge-brush-softness" min="0" max="300" value="100" title="Soft brush edge — blurs the stroke's alpha for a feathered fade at the perimeter." />
      </div>
    </div>
    <div class="ge-eraser-section" id="ge-eraser-section" style="display:none;">
      <div class="ge-section-title">Eraser</div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-eraser-preview-opacity" aria-hidden="true"></span>
        <label>Opacity <span id="ge-eraser-opacity-label">100%</span></label>
        <input type="range" id="ge-eraser-opacity" min="10" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-eraser-preview-flow" aria-hidden="true"></span>
        <label>Flow <span id="ge-eraser-flow-label">100%</span></label>
        <input type="range" id="ge-eraser-flow" min="5" max="100" value="100" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-eraser-preview-softness" aria-hidden="true"></span>
        <label>Softness <span id="ge-eraser-softness-label">100%</span></label>
        <input type="range" id="ge-eraser-softness" min="0" max="300" value="100" title="Soft brush edge — blurs the stroke's alpha so the eraser fades out at the perimeter." />
      </div>
    </div>
    <div class="ge-sharpen-section" id="ge-sharpen-section" style="display:none;">
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-sharpen-preview" aria-hidden="true"></span>
        <label>Amount <span id="ge-sharpen-label">50%</span></label>
        <input type="range" id="ge-sharpen-amount" min="10" max="100" value="50" />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;">
        <button class="ge-btn ge-btn-primary" id="ge-sharpen-run">Sharpen</button>
      </div>
    </div>
    <div class="ge-rembg-section" id="ge-rembg-section" style="display:none;">
      <div class="ge-section-title ge-section-title-with-help"><span>Background Remove</span><span class="ge-section-help" tabindex="0" role="img" aria-label="What this does" title="Runs an ML model that keeps whatever it learned to call the foreground (usually a person, product, or animal). If you have a Lasso or Wand selection active, it's used as a hint — the model only looks inside that region and anything outside is forced transparent.">?</span></div>
      <div class="ge-dep-notice" id="ge-rembg-dep-missing" style="display:none;">
        <div class="ge-dep-notice-text">
          <strong>rembg not installed.</strong>
          Background Remove needs the <code>rembg</code> package on this
          server. Click to install it from Cookbook → Dependencies.
        </div>
        <button type="button" class="ge-btn ge-btn-sm" id="ge-rembg-install-link">Install rembg</button>
      </div>
      <div class="ge-control-row ge-actions" id="ge-rembg-run-row">
        <button class="ge-btn ge-btn-primary ge-btn-ai" id="ge-rembg-run">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          Bg Remove
        </button>
      </div>
      <hr class="ge-section-divider" />
      <div class="ge-section-title ge-section-title-with-help"><span>Edge cleanup</span><span class="ge-section-help" tabindex="0" role="img" aria-label="What this does" title="Live-applied to the last Bg Removed layer. Feather softens the edge; Edge nudges it inward (−) or outward (+).">?</span></div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-rembg-feather-preview" aria-hidden="true"></span>
        <label>Feather <span id="ge-rembg-feather-label">0px</span></label>
        <input type="range" id="ge-rembg-feather" min="0" max="20" value="0" />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-rembg-grow-preview" aria-hidden="true"></span>
        <label>Edge <span id="ge-rembg-grow-label">0px</span></label>
        <input type="range" id="ge-rembg-grow" min="-10" max="10" value="0" />
      </div>
    </div>
    <div class="ge-import-section" id="ge-import-section" style="display:none;">
      <p style="font-size:10px;opacity:0.5;margin:0 0 6px;">Import an image as a new layer. Drag to position it.</p>
      <div class="ge-control-row ge-actions">
        <button class="ge-btn" id="ge-import-file">File</button>
        <button class="ge-btn" id="ge-import-paste">Clipboard</button>
        <button class="ge-btn" id="ge-import-gallery">Gallery</button>
      </div>
    </div>
    <div class="ge-harmonize-section" id="ge-harmonize-section" style="display:none;">
      <div class="ge-section-title">Harmonize <span class="ge-section-help" tabindex="0" role="img" title="Blends pasted layers into the base photo. Color match shifts the layer's lighting/tone to match its surroundings (no pixel redraw). Seam fix uses inpaint to clean jagged cutout edges (needs a self-hosted img2img/inpaint model).">?</span></div>
      <div class="ge-control-row ge-tool-model-row">
        <label>Model</label>
        <select class="ge-tool-model" data-ge-tool-model="harmonize" title="Model for harmonize">
          <option value="">Auto</option>
        </select>
      </div>
      <div class="ge-control-row">
        <label style="font-size:11px;opacity:0.6;">Prompt (only used if Seam fix &gt; 0)</label>
      </div>
      <input type="text" class="ge-inpaint-prompt" id="ge-harmonize-prompt" placeholder="photorealistic, natural lighting, seamless blend..." />
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-harmonize-color-preview" aria-hidden="true"></span>
        <label>Color match <span id="ge-harmonize-color-label">0.65</span></label>
        <input type="range" id="ge-harmonize-color" min="0" max="100" value="65" title="How much of the Reinhard color/luminance shift to apply. 0 = no shift, 1 = fully match surroundings." />
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-harmonize-seam-preview" aria-hidden="true"></span>
        <label>Seam fix <span id="ge-harmonize-seam-label">0.00</span></label>
        <input type="range" id="ge-harmonize-seam" min="0" max="100" value="0" title="Strength of the narrow inpaint pass on the alpha edge band. 0 = off, 1 = max blend at boundary." />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;">
        <button class="ge-btn ge-btn-primary" id="ge-harmonize-run">Harmonize</button>
      </div>
    </div>
    <div class="ge-style-section" id="ge-style-section" style="display:none;">
      <p style="font-size:10px;opacity:0.5;margin:0 0 6px;">Apply an art style to the image using img2img. Requires a running diffusion model.</p>
      <div class="ge-control-row ge-tool-model-row">
        <label>Model</label>
        <select class="ge-tool-model" data-ge-tool-model="style" title="Model for Style transfer">
          <option value="">Auto</option>
        </select>
      </div>
      <div class="ge-control-row">
        <label style="font-size:11px;opacity:0.6;">Style prompt</label>
      </div>
      <input type="text" class="ge-inpaint-prompt" id="ge-style-prompt" placeholder="oil painting, impressionist, Van Gogh..." />
      <div class="ge-control-row">
        <label style="font-size:11px;opacity:0.6;">Strength <span id="ge-style-strength-label">0.55</span></label>
        <input type="range" id="ge-style-strength" min="10" max="90" value="55" style="flex:1;" />
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;">
        <button class="ge-btn ge-btn-primary" id="ge-style-run">Apply Style</button>
      </div>
    </div>
  `;
}


/**
 * Layer-panel header markup. Static; static IDs are wired by the caller.
 * @returns {string}
 */
export function layerPanelHTML() {
  return `<div class="ge-layers-header">
      <span class="ge-layers-grab"></span>
      <span class="ge-layers-title">Layers</span>
      <div class="ge-layer-header-tools" role="group" aria-label="Layer actions">
      <button type="button" class="ge-btn ge-btn-sm ge-icon-btn" id="ge-add-layer" title="New layer" aria-label="New layer">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-merge-down" title="Merge down" aria-label="Merge down">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-merge-all" title="Merge all" aria-label="Merge all">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v6M9 6l3-3 3 3M3 14h18M12 14v7M9 18l3 3 3-3"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-flatten" title="Flatten copy (keeps originals)" aria-label="Flatten copy">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L4 6 L4 18 L12 22 L20 18 L20 6 Z"/><path d="M12 2 L12 22"/><path d="M4 6 L20 6"/><path d="M4 18 L20 18"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-select-all-layers" title="Select all layers" aria-label="Select all layers">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="13" height="13" rx="1"/><path d="M4 16H3V3h13v1"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-group-selected" title="Group selected layers" aria-label="Group selected layers">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h7l2 2h9v10H3z"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm" id="ge-layer-fill" title="Fill selected layer or selection with foreground color (Alt+Backspace)">Fill</button>
      </div>
    </div>
    <div class="ge-layer-blend-row">
      <label for="ge-layer-blend">Blend</label>
      <select id="ge-layer-blend" title="Active layer blend mode">
        <option value="source-over">Normal</option>
        <option value="multiply">Multiply</option>
        <option value="screen">Screen</option>
        <option value="overlay">Overlay</option>
        <option value="soft-light">Soft Light</option>
        <option value="hard-light">Hard Light</option>
        <option value="darken">Darken</option>
        <option value="lighten">Lighten</option>
        <option value="color-dodge">Color Dodge</option>
        <option value="color-burn">Color Burn</option>
        <option value="difference">Difference</option>
        <option value="exclusion">Exclusion</option>
        <option value="hue">Hue</option>
        <option value="saturation">Saturation</option>
        <option value="color">Color</option>
        <option value="luminosity">Luminosity</option>
      </select>
    </div>
    <div class="ge-layer-selection-bar" id="ge-layer-selection-bar" hidden>
      <span id="ge-layer-selection-count">2 layers</span>
      <button class="ge-icon-btn" id="ge-selected-visibility" title="Toggle selected visibility" aria-label="Toggle selected visibility">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
      </button>
      <button class="ge-icon-btn" id="ge-selected-lock" title="Toggle selected lock" aria-label="Toggle selected lock">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
      </button>
      <button class="ge-icon-btn" id="ge-selected-align" title="Align or distribute selected layers" aria-label="Align or distribute selected layers">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 5h16M7 12h10M4 19h16"/><path d="M12 3v18"/></svg>
      </button>
      <button class="ge-icon-btn danger" id="ge-selected-delete" title="Delete selected layers" aria-label="Delete selected layers">×</button>
    </div>
    <div class="ge-layers-list" id="ge-layers-list"></div>
    <div class="ge-layer-tools" id="ge-layer-tools" aria-label="Selected layer tools"></div>`;
}
