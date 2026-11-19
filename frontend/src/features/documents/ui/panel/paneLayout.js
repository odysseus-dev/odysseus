import { closePanel } from '../../application/documentCommands.js';
// ui/panel/paneLayout.js

import { syncHighlighting, updateLineNumbers } from '../editor/sourceEditor.js';

export function _wireSwipeDismiss(el) {
    if (!el) return;
    const DISMISS_THRESHOLD = 50;    // px
    const VELOCITY_THRESHOLD = 0.3;  // px/ms — fast flick dismisses below threshold
    const RUBBER_RESISTANCE = 0.35;  // resistance when dragging up past origin
    let startY = 0, startX = 0, lastY = 0, lastT = 0, velocity = 0;
    let dragging = false, cancelled = false;
    const getPane = () => document.getElementById('doc-editor-pane');
    let pane = null;

    el.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 768 || e.touches.length !== 1) return;
      pane = getPane();
      if (!pane) return;
      const t = e.touches[0];
      startY = t.clientY; startX = t.clientX; lastY = startY; lastT = e.timeStamp;
      velocity = 0; dragging = false; cancelled = false;
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (cancelled || !pane || window.innerWidth > 768) return;
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - startX);
      const dy = t.clientY - startY;
      if (!dragging) {
        if (dx > 40 && dx > Math.abs(dy) * 2) { cancelled = true; return; } // horizontal → tab scroll
        if (Math.abs(dy) > 8) {
          dragging = true;
          // Clear the open animation — its `both` fill-mode otherwise pins
          // transform and overrides our inline finger-following transform.
          pane.style.animation = 'none';
          pane.style.transition = 'none';
          pane.style.willChange = 'transform';
        } else return;
      }
      const dt = e.timeStamp - lastT;
      if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
      lastY = t.clientY; lastT = e.timeStamp;
      e.preventDefault();
      pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER_RESISTANCE}px)`;
    }, { passive: false });

    const endSwipe = () => {
      if (!dragging || !pane) { pane = null; return; }
      const p = pane; pane = null; dragging = false;
      p.style.willChange = '';
      const dy = lastY - startY;
      const shouldDismiss = dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD);
      if (shouldDismiss) {
        closePanel('down');
      } else {
        p.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
        p.style.transform = '';
        setTimeout(() => { p.style.transition = ''; }, 260);
      }
    };
    el.addEventListener('touchend', endSwipe, { passive: true });
    el.addEventListener('touchcancel', endSwipe, { passive: true });
  }

export function _dismissDocKb() {
    if (!(('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) return;
    const ta = document.getElementById('doc-editor-textarea');
    const ae = document.activeElement;
    const el = (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) ? ae : ta;
    if (!el) return;
    try {
      el.setAttribute('readonly', 'readonly');
      el.blur();
      setTimeout(() => { try { el.removeAttribute('readonly'); } catch (_) {} }, 120);
    } catch (_) { try { el.blur(); } catch (_) {} }
  }

export function initDividerDrag(divider, pane, isRight) {
    divider._disposeResize?.();
    const controller = new AbortController();
    const options = { signal: controller.signal };
    let dragging = false;
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      syncHighlighting();
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) updateLineNumbers(textarea.value);
    };
    divider.addEventListener('mousedown', event => {
      dragging = true;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      event.preventDefault();
    }, options);
    document.addEventListener('mousemove', event => {
      if (!dragging) return;
      const width = isRight ? event.clientX : window.innerWidth - event.clientX;
      pane.style.width = Math.max(250, Math.min(width, window.innerWidth * 0.7)) + 'px';
      pane.style.flex = 'none';
    }, options);
    document.addEventListener('mouseup', endDrag, options);
    divider._disposeResize = () => {
      endDrag();
      controller.abort();
    };
  }

export function toggleFullscreen() {
    const pane = document.getElementById('doc-editor-pane');
    const container = document.getElementById('chat-container');
    if (!pane) return;
    // Note: the divider stays in the DOM during fullscreen so its chevron can
    // act as the exit-fullscreen affordance (the CSS rule
    // `body:has(.doc-editor-pane.doc-fullscreen) .doc-divider-collapse` slides
    // it into a forced-inside position). Hiding the divider here would hide
    // the chevron with it.

    // Hide the tab bar during the layout shift so any in-flight smooth
    // scroll / reflow doesn't visibly "fly" the active tab across the
    // pane as it expands. Restored after the layout settles.
    const tabBar = document.getElementById('doc-tab-bar');
    if (tabBar) {
      tabBar.style.visibility = 'hidden';
      clearTimeout(tabBar._fsHideTimer);
      tabBar._fsHideTimer = setTimeout(() => {
        tabBar.style.visibility = '';
      }, 100);
    }

    if (pane.classList.contains('doc-fullscreen')) {
      pane.classList.remove('doc-fullscreen');
      if (container) container.style.display = '';
    } else {
      pane.classList.add('doc-fullscreen');
      if (container) container.style.display = 'none';
    }
    // Re-check md toolbar overflow after layout change
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar?._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
  }

export function bindPaneLayoutPointerSupport({ pane }) {
{
      let _kbBtn = null;
      pane.addEventListener('pointerdown', (e) => {
        _kbBtn = null;
        if (e.pointerType !== 'touch') return;
        const btn = e.target.closest && e.target.closest('button');
        if (!btn) return;
        const ae = document.activeElement;
        if (!(ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA'))) return;
        e.preventDefault();   // keep focus; this cancels the native touch click
        _kbBtn = btn;
      }, true);
      pane.addEventListener('pointerup', (e) => {
        const btn = _kbBtn; _kbBtn = null;
        if (!btn) return;
        if (e.target.closest && e.target.closest('button') === btn) {
          btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        }
      }, true);
      pane.addEventListener('pointercancel', () => { _kbBtn = null; }, true);
    }
}

export function bindPaneLayoutControls({ divider, pane, paneLifetime, isRight }) {
initDividerDrag(divider, pane, isRight);
const _divCollapse = divider.querySelector('.doc-divider-collapse');
if (_divCollapse) {
      let _handleIdleTimer = null;
      const _scheduleHandleIdle = () => {
        if (_handleIdleTimer) clearTimeout(_handleIdleTimer);
        _handleIdleTimer = setTimeout(() => {
          if (!divider.matches(':hover') && !divider.contains(document.activeElement)) {
            divider.classList.add('doc-divider-handle-idle');
          }
        }, 2000);
      };
      const _showDividerHandle = () => {
        divider.classList.remove('doc-divider-handle-idle');
        _scheduleHandleIdle();
      };
      divider.addEventListener('pointerenter', _showDividerHandle);
      divider.addEventListener('pointermove', _showDividerHandle);
      divider.addEventListener('pointerleave', _scheduleHandleIdle);
      divider.addEventListener('focusin', _showDividerHandle);
      divider.addEventListener('focusout', _scheduleHandleIdle);
      _scheduleHandleIdle();

      _divCollapse.addEventListener('mousedown', (e) => e.stopPropagation());
      let _dragging = false;
      _divCollapse.addEventListener('click', (e) => {
        e.stopPropagation();
        _showDividerHandle();
        if (_dragging) { _dragging = false; return; }  // suppress click after drag
        const mode = _divCollapse.dataset.mode;
        if (mode === 'fullscreen' || mode === 'unfullscreen') toggleFullscreen();
        else closePanel('down');
      });
      const HYSTERESIS = 24;
      const _applyMode = (ev) => {
        // Fullscreen state takes precedence — once the pane is fullscreen the
        // chevron always offers the "exit fullscreen" affordance regardless
        // of cursor position.
        const isFull = pane.classList.contains('doc-fullscreen');
        if (isFull) {
          if (_divCollapse.dataset.mode !== 'unfullscreen') {
            _divCollapse.dataset.mode = 'unfullscreen';
            _divCollapse.title = 'Exit fullscreen';
          }
          return;
        }
        if (!ev) return;
        const rect = divider.getBoundingClientRect();
        const midX = (rect.left + rect.right) / 2;
        const cur = _divCollapse.dataset.mode;
        if (ev.clientX > midX + HYSTERESIS && cur !== 'collapse') {
          _divCollapse.dataset.mode = 'collapse';
          _divCollapse.title = 'Collapse panel';
        } else if (ev.clientX < midX - HYSTERESIS && cur !== 'fullscreen') {
          _divCollapse.dataset.mode = 'fullscreen';
          _divCollapse.title = 'Fullscreen';
        }
      };
      const _onMove = (ev) => _applyMode(ev);
      document.addEventListener('pointermove', _onMove, { passive: true, signal: paneLifetime.signal });
      paneLifetime.signal.addEventListener('abort', () => clearTimeout(_handleIdleTimer), { once: true });
      // Reflect the fullscreen state immediately on toggle (no cursor move).
      const _classObs = new MutationObserver(() => _applyMode());
      _classObs.observe(pane, { attributes: true, attributeFilter: ['class'] });

      // Drag-to-reposition: hold + drag vertically moves the chevron along
      // the divider. Stored as a percent so resizing the pane keeps it
      // proportional. Only kicks in after a small movement so a normal tap
      // still registers as a click.
      const DRAG_THRESHOLD = 4;
      let _startY = 0, _moved = false, _pid = null;
      _divCollapse.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 && ev.pointerType === 'mouse') return;
        _startY = ev.clientY;
        _moved = false;
        _pid = ev.pointerId;
        _divCollapse.setPointerCapture?.(_pid);
        ev.preventDefault();
      });
      _divCollapse.addEventListener('pointermove', (ev) => {
        if (_pid === null) return;
        const dy = ev.clientY - _startY;
        if (!_moved && Math.abs(dy) < DRAG_THRESHOLD) return;
        _moved = true;
        _dragging = true;
        const rect = divider.getBoundingClientRect();
        if (!rect.height) return;
        const pct = Math.max(6, Math.min(94, ((ev.clientY - rect.top) / rect.height) * 100));
        _divCollapse.style.top = pct + '%';
      });
      const _endDrag = () => {
        if (_pid !== null) {
          try { _divCollapse.releasePointerCapture?.(_pid); } catch {}
          _pid = null;
        }
      };
      _divCollapse.addEventListener('pointerup', _endDrag);
      _divCollapse.addEventListener('pointercancel', _endDrag);

      const _obs = new MutationObserver(() => {
        if (!document.body.contains(divider)) {
          document.removeEventListener('pointermove', _onMove);
          _classObs.disconnect();
          _obs.disconnect();
        }
      });
      _obs.observe(document.body, { childList: true, subtree: true });
    }
}
