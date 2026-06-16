// codex.js — Make Odysseus read like the Codex App.
// Runs ONLY under a codex theme; idempotent; re-applies on SPA view changes and
// reverts cleanly when the user switches to a non-codex theme at runtime.
(function () {
  // Runs for both Codex modes (codex = dark, codex-light = light).
  function isCodex() {
    return (document.documentElement.dataset.theme || '').indexOf('codex') === 0;
  }

  var HEADING = 'What should we build?';
  var PLACEHOLDER = 'Do anything';

  // Codex-matching sidebar labels (items stay fully functional — this is a skin).
  var RELABEL = {
    'sidebar-new-chat-btn': 'New chat',  // Codex casing
    'tool-tasks-btn': 'Automations',     // Odysseus Tasks == Codex Automations
    'tools-section': 'Plugins',          // Codex "Plugins" section vocabulary
    'tool-memory-btn': 'Memory',
  };
  var _origLabels = {};  // id -> original label text, captured once for restore.

  // Prefer the explicit label span (robust to upstream adding badges/counts);
  // fall back to the first non-empty text node (skips the icon svg).
  function labelNode(el) {
    var span = el.querySelector('.grow, .section-title');
    if (span && span.textContent.trim()) return span;
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (node.textContent.trim()) return node;
    }
    return null;
  }
  function relabelSidebar() {
    Object.keys(RELABEL).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || el.dataset.codexRelabeled) return;  // done-flag: skip rebuilt walks
      var target = labelNode(el);
      if (!target) return;
      if (!(id in _origLabels)) _origLabels[id] = target.textContent;
      var want = RELABEL[id];
      if (target.textContent.trim() !== want) target.textContent = want;
      el.dataset.codexRelabeled = '1';
    });
  }
  function restoreLabels() {
    Object.keys(RELABEL).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || !el.dataset.codexRelabeled) return;
      var target = labelNode(el);
      if (target && (id in _origLabels)) target.textContent = _origLabels[id];
      delete el.dataset.codexRelabeled;
    });
  }

  // Codex puts the model selector inline in the control row (bottom-right, by
  // the send button) rather than floating top-right. Move it there, remembering
  // its original home so we can put it back when leaving the codex theme.
  var _pickerHome = null;  // { parent, next }
  function composer() {
    var mpw = document.getElementById('model-picker-wrap');
    var right = document.querySelector('.chat-input-right');
    if (!mpw || !right) return;
    if (mpw.parentElement !== right) {
      if (!_pickerHome) _pickerHome = { parent: mpw.parentElement, next: mpw.nextSibling };
      right.insertBefore(mpw, right.firstChild);
      mpw.dataset.codexMoved = '1';
    }
  }
  function restoreComposer() {
    var mpw = document.getElementById('model-picker-wrap');
    if (!mpw || !mpw.dataset.codexMoved || !_pickerHome || !_pickerHome.parent) return;
    var ref = (_pickerHome.next && _pickerHome.next.parentNode === _pickerHome.parent)
      ? _pickerHome.next : null;
    _pickerHome.parent.insertBefore(mpw, ref);
    delete mpw.dataset.codexMoved;
  }

  function apply() {
    if (!isCodex()) return;
    var name = document.querySelector('#welcome-screen .welcome-name');
    // Don't fight upstream mode labels: app.js stashes dataset.researchOrigHtml
    // (Deep Research) / dataset.originalHtml (Nobody/incognito) while those modes
    // are active, so leave the heading alone whenever a mode label is showing.
    if (name && !name.dataset.researchOrigHtml && !name.dataset.originalHtml
        && name.textContent.trim() !== HEADING) {
      name.textContent = HEADING;
    }
    var msg = document.getElementById('message');
    if (msg && msg.getAttribute('placeholder') !== PLACEHOLDER) msg.setAttribute('placeholder', PLACEHOLDER);
    relabelSidebar();
    composer();
  }

  // Revert the runtime DOM tweaks so switching to a non-codex theme is clean.
  function restore() {
    restoreComposer();
    restoreLabels();
    // The welcome heading + composer placeholder are owned by app.js; it rewrites
    // them on the next view change/resize, so no manual revert is needed.
  }

  function syncTheme() {
    if (isCodex()) apply();
    else restore();
  }

  // Coalesce bursts of mutations into one apply() per frame so streamed tokens
  // (childList churn in #chat-history) don't trigger a full pass each time.
  var _scheduled = false;
  function schedule() {
    if (_scheduled) return;
    _scheduled = true;
    requestAnimationFrame(function () { _scheduled = false; apply(); });
  }

  function start() {
    apply();
    try {
      // Re-apply the skin as the SPA swaps views. childList for structural change
      // + the placeholder attr (which app.js rewrites on composer resize) — NOT
      // characterData, so per-token text edits during streaming are ignored.
      new MutationObserver(schedule).observe(document.body, {
        childList: true, subtree: true,
        attributes: true, attributeFilter: ['placeholder'],
      });
      // Apply on switch *to* a codex theme and revert on switch *away* at runtime
      // (theme.js writes data-theme on <html>).
      new MutationObserver(syncTheme).observe(document.documentElement, {
        attributes: true, attributeFilter: ['data-theme'],
      });
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
