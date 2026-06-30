// static/js/htmlPreview.js
// Focus and input routing for sandboxed HTML preview iframes (doc editor + compare).

const BODY_ACTIVE_CLASS = 'doc-html-preview-active';

/**
 * True when an HTML preview iframe is visible and should receive pointer/keyboard.
 */
export function isInteractiveHtmlPreviewActive() {
  if (document.body.classList.contains(BODY_ACTIVE_CLASS)) return true;
  const docFrame = document.getElementById('doc-html-preview');
  if (docFrame && docFrame.style.display !== 'none') return true;
  return [...document.querySelectorAll('.compare-pane-iframe')].some(
    (el) => el.style.display !== 'none',
  );
}

function _visibleCompareIframe() {
  return [...document.querySelectorAll('.compare-pane-iframe')].find(
    (el) => el.style.display !== 'none',
  ) || null;
}

function _blurWithin(root) {
  if (!root) return;
  const active = document.activeElement;
  if (active && root.contains(active)) {
    try { active.blur(); } catch (_) {}
  }
}

function _focusIframe(iframe) {
  if (!iframe) return;
  iframe.tabIndex = 0;
  try {
    iframe.focus({ preventScroll: true });
  } catch (_) {}
  try {
    iframe.contentWindow?.focus();
  } catch (_) {}
}

/**
 * Prepare a sandboxed preview iframe for interactive HTML (games, forms, keyboard UI).
 * @param {HTMLIFrameElement} iframe
 * @param {{ blurRoot?: Element | null }} [opts]
 */
export function activateInteractivePreview(iframe, opts = {}) {
  if (!iframe) return;

  const blurRoot = opts.blurRoot ?? iframe.closest('.doc-editor-pane') ?? document.body;
  _blurWithin(blurRoot);

  iframe.dataset.interactivePreview = '1';
  iframe.setAttribute('data-no-swipe-dismiss', '');
  document.body.classList.add(BODY_ACTIVE_CLASS);

  const onLoad = () => {
    _focusIframe(iframe);
  };

  if (iframe._htmlPreviewOnLoad) {
    iframe.removeEventListener('load', iframe._htmlPreviewOnLoad);
  }
  iframe._htmlPreviewOnLoad = onLoad;
  iframe.addEventListener('load', onLoad);

  // srcdoc may already be applied; load may not fire again until next assignment.
  if (iframe.contentDocument?.readyState === 'complete') {
    requestAnimationFrame(() => _focusIframe(iframe));
  }
}

/** Tear down interactive preview state when leaving preview mode. */
export function deactivateInteractivePreview(iframe) {
  if (iframe) {
    if (iframe._htmlPreviewOnLoad) {
      iframe.removeEventListener('load', iframe._htmlPreviewOnLoad);
      delete iframe._htmlPreviewOnLoad;
    }
    delete iframe.dataset.interactivePreview;
    iframe.removeAttribute('data-no-swipe-dismiss');
    iframe.tabIndex = -1;
  }

  const docFrame = document.getElementById('doc-html-preview');
  const compareFrame = _visibleCompareIframe();
  const anyVisible =
    (docFrame && docFrame.style.display !== 'none')
    || compareFrame;

  if (!anyVisible) {
    document.body.classList.remove(BODY_ACTIVE_CLASS);
  }
}
