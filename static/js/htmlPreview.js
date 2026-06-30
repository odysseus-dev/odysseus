// static/js/htmlPreview.js
// Focus and input routing for sandboxed HTML preview iframes (doc editor + compare).

const BODY_ACTIVE_CLASS = 'doc-html-preview-active';
const BRIDGE_MARKER = 'odysseus-html-preview-bridge';
const MSG_TYPE = 'odysseus-html-preview';

const CAPTURE_CLASS = 'doc-html-preview-capture';
const WRAP_CLASS = 'doc-html-preview-wrap';

/** Injected into preview HTML so the parent can forward input via postMessage. */
const BRIDGE_SCRIPT = `<script id="${BRIDGE_MARKER}">(function(){
window.addEventListener('message',function(e){
  var d=e.data;if(!d||d.type!=='${MSG_TYPE}')return;
  if(d.kind==='key'){
    try{
      var ev=new KeyboardEvent('keydown',{
        key:d.key,code:d.code,bubbles:true,cancelable:true
      });
      window.dispatchEvent(ev);
      document.dispatchEvent(ev);
    }catch(_){}
  }else if(d.kind==='pointer'){
    var el=document.elementFromPoint(d.x,d.y);
    if(!el)return;
    try{
      el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:d.x,clientY:d.y}));
      el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:d.x,clientY:d.y}));
      el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:d.x,clientY:d.y}));
    }catch(_){}
  }
});
})();</script>`;

/**
 * Append the preview input bridge before </body> (or at end of fragment).
 * @param {string} html
 * @returns {string}
 */
export function injectPreviewBridge(html) {
  if (!html || html.includes(BRIDGE_MARKER)) return html || '';
  const lower = html.toLowerCase();
  const closeBody = lower.lastIndexOf('</body>');
  if (closeBody !== -1) {
    return html.slice(0, closeBody) + BRIDGE_SCRIPT + html.slice(closeBody);
  }
  return html + BRIDGE_SCRIPT;
}

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

function _activePreviewIframe() {
  const docFrame = document.getElementById('doc-html-preview');
  if (docFrame && docFrame.style.display !== 'none') return docFrame;
  return _visibleCompareIframe();
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

function _iframeLocalPoint(iframe, clientX, clientY) {
  const rect = iframe.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
  return { x, y };
}

function _postPointer(iframe, clientX, clientY) {
  const pt = _iframeLocalPoint(iframe, clientX, clientY);
  if (!pt) return;
  try {
    iframe.contentWindow?.postMessage({
      type: MSG_TYPE,
      kind: 'pointer',
      x: pt.x,
      y: pt.y,
    }, '*');
  } catch (_) {}
}

function _ensurePreviewWrap(iframe) {
  const parent = iframe.parentElement;
  if (parent?.classList.contains(WRAP_CLASS)) return parent;
  const wrap = document.createElement('div');
  wrap.className = WRAP_CLASS;
  parent.insertBefore(wrap, iframe);
  wrap.appendChild(iframe);
  return wrap;
}

function _createCaptureOverlay(iframe) {
  const wrap = _ensurePreviewWrap(iframe);
  let capture = wrap.querySelector(`.${CAPTURE_CLASS}`);
  if (capture) return capture;
  capture = document.createElement('div');
  capture.className = CAPTURE_CLASS;
  capture.setAttribute('data-no-swipe-dismiss', '');
  capture.setAttribute('aria-hidden', 'true');
  const onPointer = (e) => {
    if (!iframe.dataset.interactivePreview) return;
    e.preventDefault();
    _postPointer(iframe, e.clientX, e.clientY);
    _focusIframe(iframe);
  };
  capture._onPreviewPointer = onPointer;
  capture.addEventListener('pointerdown', onPointer);
  wrap.appendChild(capture);
  return capture;
}

function _removeCaptureOverlay(iframe) {
  const wrap = iframe.parentElement;
  if (!wrap?.classList.contains(WRAP_CLASS)) return;
  const capture = wrap.querySelector(`.${CAPTURE_CLASS}`);
  if (capture) {
    if (capture._onPreviewPointer) {
      capture.removeEventListener('pointerdown', capture._onPreviewPointer);
      delete capture._onPreviewPointer;
    }
    capture.remove();
  }
  if (wrap.childElementCount === 1 && wrap.firstElementChild === iframe) {
    wrap.parentElement.insertBefore(iframe, wrap);
    wrap.remove();
  }
}

function _onBridgeKeydown(e) {
  if (!isInteractiveHtmlPreviewActive()) return;
  if (e.code !== 'Space' || e.repeat) return;
  const iframe = _activePreviewIframe();
  if (!iframe?.contentWindow) return;
  e.preventDefault();
  try {
    iframe.contentWindow.postMessage({
      type: MSG_TYPE,
      kind: 'key',
      key: ' ',
      code: 'Space',
    }, '*');
  } catch (_) {}
}

function _wireParentPreviewBridge(iframe) {
  if (!iframe || iframe._previewBridgeWired) return;
  iframe._previewBridgeWired = true;
  _createCaptureOverlay(iframe);

  if (!document._htmlPreviewBridgeKeydown) {
    document._htmlPreviewBridgeKeydown = true;
    document.addEventListener('keydown', _onBridgeKeydown, true);
  }
}

function _unwireParentPreviewBridge(iframe) {
  if (!iframe || !iframe._previewBridgeWired) return;
  _removeCaptureOverlay(iframe);
  delete iframe._previewBridgeWired;
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
  _wireParentPreviewBridge(iframe);

  const onLoad = () => {
    _focusIframe(iframe);
  };

  if (iframe._htmlPreviewOnLoad) {
    iframe.removeEventListener('load', iframe._htmlPreviewOnLoad);
  }
  iframe._htmlPreviewOnLoad = onLoad;
  iframe.addEventListener('load', onLoad);

  try {
    if (iframe.contentDocument?.readyState === 'complete') {
      requestAnimationFrame(() => _focusIframe(iframe));
    }
  } catch (_) {
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
    _unwireParentPreviewBridge(iframe);
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
