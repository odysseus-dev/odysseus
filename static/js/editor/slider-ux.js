/**
 * Slider-UX wiring shared across the editor:
 *
 *   1. `is-using` class while a slider is being dragged (eraser-rows
 *      expand to a wider track when in use). Cleared 0.5s after
 *      pointerup so a quick click doesn't snap back instantly.
 *   2. Floating value bubble above the thumb during drag.
 *      Desktop: only the layer-opacity slider gets a bubble (the
 *      eraser-row sliders already show a value chip on the right).
 *      Mobile: every slider in the editor gets a bubble.
 *   3. Click the value chip to type a number directly — replaces
 *      the span with an inline input until blur/Enter.
 *
 * Wired ONCE on editor open; the listeners stay alive for the whole
 * session via state.container delegation.
 *
 * @param {{
 *   registerDocClickAway: (handler: (e: Event) => void) => void,
 * }} deps
 */
import { state } from './state.js';

export function wireSliderUx({ registerDocClickAway }) {
  const container = state.container;
  if (!container) return;

  // ── Floating bubble ──
  const sliderBubble = document.createElement('div');
  sliderBubble.className = 'ge-slider-bubble';
  sliderBubble.hidden = true;
  let sliderBubbleSlider = null;

  // Find the container row for any slider — works for ge-eraser-row
  // sliders AND the layer-opacity slider on each layer item.
  function bubbleRowFor(slider) {
    return slider.closest('.ge-eraser-row, .ge-layer-item, .ge-control-row, .ge-adj-row');
  }
  function bubbleText(slider) {
    const row = bubbleRowFor(slider);
    // Pulled-out value chip (after the slider) wins; fall back to
    // the various `<label> <span>` styles used across the editor.
    const chip = row?.querySelector('.ge-slider-value')
      || row?.querySelector('label > span[id$="-label"]')
      || row?.querySelector('label > .ge-size-label')
      || row?.querySelector('.ge-adj-value');
    if (chip) return chip.textContent;
    if (slider.classList.contains('ge-layer-opacity')) {
      return Math.round(parseFloat(slider.value)) + '%';
    }
    return slider.value;
  }
  function bubblePos(slider, cursorX) {
    // Bubble is fixed-positioned on document.body so it escapes any
    // overflow:hidden / overflow:auto on the row's ancestors. The
    // bubble's X is CLAMPED to the slider's track so it can't follow
    // a finger that drags way past either end.
    const sliderRect = slider.getBoundingClientRect();
    const minX = sliderRect.left + 8;
    const maxX = sliderRect.right - 8;
    const x = Math.max(minX, Math.min(maxX, cursorX));
    sliderBubble.style.left = x + 'px';
    sliderBubble.style.top  = (sliderRect.top - 8) + 'px';
  }
  function showSliderBubble(slider, e) {
    if (sliderBubble.parentElement !== document.body) document.body.appendChild(sliderBubble);
    sliderBubble.textContent = bubbleText(slider);
    bubblePos(slider, e ? e.clientX : slider.getBoundingClientRect().left + slider.offsetWidth / 2);
    sliderBubble.hidden = false;
    sliderBubble.classList.add('visible');
    sliderBubbleSlider = slider;
  }
  function hideSliderBubble() {
    sliderBubble.classList.remove('visible');
    sliderBubble.hidden = true;
    sliderBubbleSlider = null;
  }
  window.__geHideSliderBubble = hideSliderBubble;
  document.addEventListener('ge:hide-slider-bubble', hideSliderBubble);

  const slidingTimers = new WeakMap();
  // Desktop: only the layer-opacity slider gets the bubble (eraser-
  // rows have their own chip). Mobile: every slider gets one.
  const isMobileSliders = window.matchMedia('(max-width: 820px)').matches;
  const SLIDER_SEL = isMobileSliders
    ? '.ge-layer-opacity, .ge-eraser-row input[type="range"], .ge-control-row input[type="range"], .ge-adj-row input[type="range"]'
    : '.ge-layer-opacity';

  container.addEventListener('pointerdown', (e) => {
    const slider = e.target.closest(SLIDER_SEL);
    if (!slider) {
      hideSliderBubble();
      return;
    }
    const t = slidingTimers.get(slider);
    if (t) { clearTimeout(t); slidingTimers.delete(slider); }
    slider.classList.add('is-using');
    showSliderBubble(slider, e);
    // Compensate for the leftward-expanding eraser sliders so the
    // thumb lands at the cursor's X on the new (wider) track. Layer-
    // opacity doesn't shift left when it grows, so it uses the
    // browser default.
    if (slider.matches('.ge-eraser-row input[type="range"]')) {
      const rect = slider.getBoundingClientRect();
      const valFrac = Math.max(0, Math.min(1, 1 - (rect.right - e.clientX) / 140));
      const min = parseFloat(slider.min) || 0;
      const max = parseFloat(slider.max) || 100;
      const step = parseFloat(slider.step) || 1;
      const raw = min + valFrac * (max - min);
      const stepped = Math.round(raw / step) * step;
      requestAnimationFrame(() => {
        slider.value = String(stepped);
        slider.dispatchEvent(new Event('input', { bubbles: true }));
        sliderBubble.textContent = bubbleText(slider);
      });
    } else {
      requestAnimationFrame(() => {
        sliderBubble.textContent = bubbleText(slider);
      });
    }
  }, true);
  document.addEventListener('pointermove', (e) => {
    if (!sliderBubbleSlider) return;
    if (!sliderBubbleSlider.isConnected) {
      hideSliderBubble();
      return;
    }
    bubblePos(sliderBubbleSlider, e.clientX);
    sliderBubble.textContent = bubbleText(sliderBubbleSlider);
  });
  const scheduleSliderRelease = (slider) => {
    if (!slider) return;
    const old = slidingTimers.get(slider);
    if (old) clearTimeout(old);
    const t = setTimeout(() => {
      slider.classList.remove('is-using');
      slidingTimers.delete(slider);
    }, 500);
    slidingTimers.set(slider, t);
  };
  document.addEventListener('pointerup', () => {
    container.querySelectorAll('input[type="range"].is-using').forEach(scheduleSliderRelease);
    hideSliderBubble();
  });
  document.addEventListener('pointercancel', hideSliderBubble);
  document.addEventListener('touchcancel', hideSliderBubble, { passive: true });
  window.addEventListener('blur', hideSliderBubble);
  window.addEventListener('resize', hideSliderBubble);
  window.addEventListener('orientationchange', hideSliderBubble);

  // ── Click value chip to type a number ──
  // Replaces the chip with a tiny inline input until blur/Enter,
  // then writes back to the slider and dispatches `input` so
  // previews react. Matches the legacy chip AND the pulled-out
  // `.ge-slider-value` chip so every slider row in the editor is
  // click-to-type editable.
  registerDocClickAway((e) => {
    const chip = e.target.closest(
      '.ge-eraser-row .ge-slider-value, ' +
      '.ge-eraser-row label > span[id$="-label"], ' +
      '.ge-eraser-row > span[id$="-label"], ' +
      '.ge-adj-row .ge-adj-value'
    );
    if (!chip) return;
    const row = chip.closest('.ge-eraser-row, .ge-adj-row');
    const slider = row?.querySelector('input[type="range"]');
    if (!slider) return;
    e.preventDefault();
    e.stopPropagation();
    const numeric = (slider.value ?? '').toString();
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = numeric;
    inp.className = 'ge-slider-edit';
    chip.style.visibility = 'hidden';
    row.appendChild(inp);
    // Position the input over where the chip sits.
    const crect = chip.getBoundingClientRect();
    const rrect = row.getBoundingClientRect();
    inp.style.left = (crect.left - rrect.left) + 'px';
    inp.style.top = (crect.top - rrect.top - 1) + 'px';
    inp.style.width = Math.max(40, crect.width + 8) + 'px';
    inp.focus();
    inp.select();
    const commit = () => {
      const v = parseFloat(inp.value);
      if (!Number.isNaN(v)) {
        const min = parseFloat(slider.min) || 0;
        const max = parseFloat(slider.max) || 100;
        const clamped = Math.max(min, Math.min(max, v));
        slider.value = String(clamped);
        slider.dispatchEvent(new Event('input', { bubbles: true }));
      }
      cleanup();
    };
    const cleanup = () => {
      inp.remove();
      chip.style.visibility = '';
    };
    inp.addEventListener('blur', commit);
    inp.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); cleanup(); }
    });
  });
}
