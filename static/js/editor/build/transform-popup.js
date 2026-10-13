/**
 * Static markup for the Transform popup that floats over the canvas
 * when the user activates the Resize/Transform tool.
 *
 * Pure DOM — no module state, no event listeners. The caller wires all
 * IDs via document.getElementById / pop.querySelector.
 *
 * @returns {string}
 */
export function transformPopupHTML() {
  return `
    <div class="ge-adj-head ge-transform-popup-head" data-transform-drag>
      <span class="ge-adj-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 7 7 3 11 7"/><line x1="7" y1="3" x2="7" y2="21"/><polyline points="21 17 17 21 13 17"/><line x1="17" y1="21" x2="17" y2="3"/></svg>
      </span>
      <span class="ge-adj-title">Transform</span>
      <button type="button" id="ge-transform-aspect" class="ge-transform-aspect-btn" title="Lock aspect ratio" aria-pressed="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      </button>
      <span class="ge-head-btns">
        <button class="ge-adj-min" type="button" title="Minimise" id="ge-transform-min">&minus;</button>
        <button class="ge-adj-close" type="button" title="Cancel" id="ge-transform-cancel">&times;</button>
      </span>
    </div>
    <div class="ge-transform-popup-body">
      <div class="ge-transform-field ge-transform-position-field">
        <label for="ge-transform-x" title="Transform center X">X</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-x" step="1" aria-label="Transform center X" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-x">
          <button type="button" data-spin="down" tabindex="-1" aria-label="Move left">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="Move right">+</button>
        </span>
      </div>
      <div class="ge-transform-field ge-transform-position-field">
        <label for="ge-transform-y" title="Transform center Y">Y</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-y" step="1" aria-label="Transform center Y" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-y">
          <button type="button" data-spin="down" tabindex="-1" aria-label="Move up">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="Move down">+</button>
        </span>
      </div>
      <div class="ge-row-break ge-transform-position-break"></div>
      <div class="ge-transform-field">
        <label>W</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-w" step="1" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-w">
          <button type="button" data-spin="down" tabindex="-1" aria-label="Decrease width">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="Increase width">+</button>
        </span>
      </div>
      <div class="ge-transform-field">
        <label>H</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-h" step="1" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-h">
          <button type="button" data-spin="down" tabindex="-1" aria-label="Decrease height">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="Increase height">+</button>
        </span>
      </div>
      <div class="ge-row-break"></div>
      <div class="ge-transform-field">
        <label>↻</label>
        <input type="number" class="ge-transform-popup-input ge-transform-popup-input-rot" id="ge-transform-rot" step="1" value="0" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-rot">
          <button type="button" data-spin="down" tabindex="-1" aria-label="Rotate -1°">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="Rotate +1°">+</button>
        </span>
      </div>
      <span class="ge-transform-quick" aria-label="Transform actions">
        <button type="button" class="ge-transform-quick-btn" id="ge-transform-flip-h" title="Flip horizontally" aria-label="Flip horizontally">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="m8 7-4 5 4 5"/><path d="m16 7 4 5-4 5"/></svg>
        </button>
        <button type="button" class="ge-transform-quick-btn" id="ge-transform-flip-v" title="Flip vertically" aria-label="Flip vertically">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h18"/><path d="m7 8 5-4 5 4"/><path d="m7 16 5 4 5-4"/></svg>
        </button>
        <button type="button" class="ge-transform-quick-btn" id="ge-transform-rot-90" title="Rotate 90 degrees (Shift: counter-clockwise)" aria-label="Rotate 90 degrees">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>
        </button>
      </span>
      <button type="button" class="ge-btn ge-btn-sm" id="ge-transform-cancel-btn">Cancel</button>
      <button type="button" class="ge-btn ge-btn-sm ge-btn-primary" id="ge-transform-apply">Apply</button>
    </div>
    <label class="ge-transform-preserve-field">
      <input type="checkbox" id="ge-transform-preserve-source" />
      <span>Preserve source pixels</span>
    </label>
    <p class="ge-transform-popup-hint">Type <strong>-</strong> before W / H to flip.</p>
  `;
}


/**
 * Wire a `<span class="ge-transform-spin">…<button data-spin="up|down"/>…</span>`
 * group with tap-to-tick + hold-to-repeat. After 1.5 s the repeat
 * accelerates from 70ms→30ms intervals so users can rapidly scrub a
 * numeric field without mashing the button.
 *
 * On each tick, the helper looks up the target `<input>` by the
 * spin-group's `data-spin-for` attribute and dispatches an `input`
 * event so the rest of the popup's wiring picks up the change.
 *
 * @param {HTMLElement} root   Element that owns one or more spin groups
 *                             (e.g. the transform popup).
 */
export function attachSpinRepeat(root) {
  root.querySelectorAll('.ge-transform-spin button').forEach(btn => {
    const tick = (shift) => {
      const targetId = btn.parentElement?.dataset?.spinFor;
      if (!targetId) return;
      const input = root.querySelector('#' + CSS.escape(targetId));
      if (!input || input.readOnly) return;
      const step = shift ? 10 : 1;
      const cur = parseInt(input.value, 10) || 0;
      const next = btn.dataset.spin === 'up' ? cur + step : cur - step;
      input.value = String(next);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    let holdTimeout = null, repeatInterval = null, started = 0;
    btn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      tick(e.shiftKey);
      started = Date.now();
      holdTimeout = setTimeout(() => {
        repeatInterval = setInterval(() => {
          tick(false);
          if (Date.now() - started > 1500 && repeatInterval) {
            clearInterval(repeatInterval);
            repeatInterval = setInterval(() => tick(false), 30);
          }
        }, 70);
      }, 350);
    });
    const endHold = () => {
      if (holdTimeout) clearTimeout(holdTimeout);
      if (repeatInterval) clearInterval(repeatInterval);
      holdTimeout = null; repeatInterval = null;
    };
    btn.addEventListener('pointerup', endHold);
    btn.addEventListener('pointerleave', endHold);
    btn.addEventListener('pointercancel', endHold);
  });
}
