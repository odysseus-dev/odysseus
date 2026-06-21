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
    <div id="ge-brush-controls">
      <div class="ge-control-row" id="ge-color-row">
        <label>Color</label>
        <input type="color" class="ge-color-picker" value="${color}" />
      </div>
      <div class="ge-control-row">
        <label>Size <span class="ge-size-label">${brushSize}px</span></label>
      <input type="range" class="ge-size-slider" min="0" max="1000" value="${brushSliderValue}" />
    </div>
    </div>
    <div class="ge-lasso-section" id="ge-lasso-section" style="display:none;">
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
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-lasso-invert" title="Invert selection (Ctrl+Alt+I)">
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
      </div>
      <p style="font-size:9px;opacity:0.4;margin:4px 0 0;">Draw a freehand selection. Esc to cancel.</p>
    </div>
    <div class="ge-wand-section" id="ge-wand-section" style="display:none;">
      <div class="ge-control-row" style="display:flex;gap:4px;margin-bottom:4px;" title="How the next click combines with the current selection. Shift / Alt held during a click override this for one click.">
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn active" data-wand-mode="replace" title="Replace selection on each click">New</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="add" title="Add to selection (Shift)">+ Add</button>
        <button type="button" class="ge-btn ge-btn-sm ge-wand-mode-btn" data-wand-mode="subtract" title="Subtract from selection (Alt)">− Subtract</button>
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
        <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-wand-invert" title="Invert selection (Ctrl+Alt+I)">
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
      </div>
      <p style="font-size:9px;opacity:0.4;margin:4px 0 0;">Click a region to select similar pixels. Shift+click to add, Alt+click to subtract. Esc to clear.</p>
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
    </div>
    <div class="ge-eraser-section" id="ge-clone-section" style="display:none;">
      <div class="ge-section-title ge-section-title-with-help"><span>Clone</span><span class="ge-section-help" tabindex="0" role="img" aria-label="How clone works" title="Alt-click (desktop) or double-tap (mobile) somewhere on the canvas to set the sample source. Then drag elsewhere to clone those pixels onto the active layer. The source point moves with your brush so the offset stays constant. Size / Opacity / Flow / Softness come from the Brush panel.">?</span></div>
      <p class="ge-section-hint" style="margin-top:0;">
        <strong class="ge-clone-hint-desktop">Alt-click</strong><strong class="ge-clone-hint-mobile">Double-tap</strong> to set source · drag to paint
      </p>
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
      <div class="ge-section-title">Brush</div>
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
      <div class="ge-control-row ge-tool-model-row">
        <label for="ge-ai-sharpen">Model</label>
        <select class="ge-tool-model" id="ge-ai-sharpen" data-ge-tool-model="sharpen" title="Model for AI sharpen">
          <option value="">Auto</option>
        </select>
      </div>
      <div class="ge-control-row ge-actions" style="margin-top:4px;">
        <button class="ge-btn ge-btn-primary" id="ge-sharpen-run">Sharpen</button>
      </div>
    </div>
    <div class="ge-rembg-section" id="ge-rembg-section" style="display:none;">
      <div class="ge-section-title ge-section-title-with-help"><span>Background Remove</span><span class="ge-section-help" tabindex="0" role="img" aria-label="What this does" title="Runs an ML model that keeps whatever it learned to call the foreground (usually a person, product, or animal). Draw cyan sample strokes on background pixels to remove matching connected background areas. If you have a Lasso or Wand selection active, it's still used as a foreground keep hint.">?</span></div>
      <div class="ge-dep-notice" id="ge-rembg-dep-missing" style="display:none;">
        <div class="ge-dep-notice-text">
          <strong>rembg not installed.</strong>
          Natural rembg needs the <code>rembg</code> package on this
          server. You can still use an image model or heuristic sample mode.
        </div>
        <button type="button" class="ge-btn ge-btn-sm" id="ge-rembg-install-link">Install rembg</button>
      </div>
      <div class="ge-control-row ge-tool-model-row">
        <label for="ge-rembg-pipeline">Pipeline</label>
        <select id="ge-rembg-pipeline" title="Choose how background removal runs">
          <option value="auto">Auto</option>
          <option value="model">Local/API model</option>
          <option value="rembg">Natural rembg</option>
          <option value="heuristic">Heuristic sample</option>
        </select>
      </div>
      <div class="ge-control-row ge-tool-model-row">
        <label for="ge-ai-rembg">Model</label>
        <select class="ge-tool-model" id="ge-ai-rembg" data-ge-tool-model="rembg" title="Model for background removal">
          <option value="">Auto</option>
        </select>
      </div>
      <div class="ge-control-row ge-eraser-row">
        <span class="ge-eraser-preview" id="ge-rembg-strength-preview" aria-hidden="true"></span>
        <label>Strength <span id="ge-rembg-strength-label">70%</span></label>
        <input type="range" id="ge-rembg-strength" min="10" max="100" value="70" title="How aggressively cyan background samples expand into connected matching background areas." />
      </div>
      <div class="ge-control-row ge-actions" id="ge-rembg-run-row">
        <button class="ge-btn ge-btn-primary ge-btn-ai" id="ge-rembg-run">
          <span class="ge-btn-ai-mark" aria-hidden="true">✦</span>
          Bg Remove
        </button>
      </div>
      <div class="ge-rembg-sample-panel">
        <div class="ge-section-title ge-section-title-with-help"><span>Sample BG</span><span class="ge-section-help" tabindex="0" role="img" aria-label="Background sample help" title="With Bg Remove selected, draw a short cyan line on the background. The remove pass uses that stroke as a background sample and removes matching connected pixels.">?</span></div>
        <div class="ge-control-row ge-actions ge-rembg-sample-actions">
          <button class="ge-btn ge-btn-sm ge-btn-iconlabel ge-mask-vis-btn visible" id="ge-rembg-sample-vis" title="Hide background sample overlay">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            <span id="ge-rembg-sample-vis-label">Hide</span>
          </button>
          <button class="ge-btn ge-btn-sm ge-btn-iconlabel" id="ge-rembg-sample-clear" title="Clear background sample strokes">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
            Clear
          </button>
        </div>
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
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-merge-down" title="Merge down" aria-label="Merge down">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-merge-all" title="Merge all" aria-label="Merge all">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v6M9 6l3-3 3 3M3 14h18M12 14v7M9 18l3 3 3-3"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm ge-icon-btn" id="ge-flatten" title="Flatten copy (keeps originals)" aria-label="Flatten copy">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L4 6 L4 18 L12 22 L20 18 L20 6 Z"/><path d="M12 2 L12 22"/><path d="M4 6 L20 6"/><path d="M4 18 L20 18"/></svg>
      </button>
      <button class="ge-btn ge-btn-sm" id="ge-add-layer" title="Add empty layer">+ Add</button>
    </div><div class="ge-layers-list" id="ge-layers-list"></div>`;
}
