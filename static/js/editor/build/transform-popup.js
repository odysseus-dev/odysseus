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
      <span class="ge-adj-title">${window.t('Transform')}</span>
      <button type="button" id="ge-transform-aspect" class="ge-transform-aspect-btn" title="${window.t('Lock aspect ratio')}" aria-pressed="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      </button>
      <span class="ge-head-btns">
        <button class="ge-adj-min" type="button" title="${window.t('Minimise')}" id="ge-transform-min">&minus;</button>
        <button class="ge-adj-close" type="button" title="${window.t('Cancel')}" id="ge-transform-cancel">&times;</button>
      </span>
    </div>
    <div class="ge-transform-popup-body">
      <div class="ge-transform-field">
        <label>W</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-w" step="1" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-w">
          <button type="button" data-spin="down" tabindex="-1" aria-label="${window.t('Decrease width')}">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="${window.t('Increase width')}">+</button>
        </span>
      </div>
      <div class="ge-transform-field">
        <label>H</label>
        <input type="number" class="ge-transform-popup-input" id="ge-transform-h" step="1" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-h">
          <button type="button" data-spin="down" tabindex="-1" aria-label="${window.t('Decrease height')}">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="${window.t('Increase height')}">+</button>
        </span>
      </div>
      <div class="ge-row-break"></div>
      <div class="ge-transform-field">
        <label>↻</label>
        <input type="number" class="ge-transform-popup-input ge-transform-popup-input-rot" id="ge-transform-rot" step="1" value="0" />
        <span class="ge-transform-spin" data-spin-for="ge-transform-rot">
          <button type="button" data-spin="down" tabindex="-1" aria-label="${window.t('Rotate -1°')}">−</button>
          <button type="button" data-spin="up" tabindex="-1" aria-label="${window.t('Rotate +1°')}">+</button>
        </span>
      </div>
      <button type="button" class="ge-btn ge-btn-sm" id="ge-transform-cancel-btn">${window.t('Cancel')}</button>
      <button type="button" class="ge-btn ge-btn-sm ge-btn-primary" id="ge-transform-apply">${window.t('Apply')}</button>
    </div>
    <p class="ge-transform-popup-hint">${window.t('Type')} <strong>-</strong> ${window.t('before W / H to flip.')}</p>
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
