import { topPortalZ } from '../host.js';
// frontend/src/features/documents/ui/richEditor/selectionToolbar.js
/**
 * Document Editor Rich-Text Subsystem: selectionToolbar
 */

import { _richSelectionHasFormatting, applyMdFormat } from './formatting.js';
import { _showRichImageSourceMenu } from './imageMenus.js';
import { setRichImageInsertRange } from './imageOps.js';
import { _richLinkAtRange } from './links.js';

export let _richSelectionToolbar = null;

let _richSelectionToolbarFrame = 0;

let _richSelectionToolbarFollowFrame = 0;

let _richSelectionToolbarRange = null;

export function _hideRichSelectionToolbar() {
    if (_richSelectionToolbarFrame) cancelAnimationFrame(_richSelectionToolbarFrame);
    _richSelectionToolbarFrame = 0;
    _richSelectionToolbarRange = null;
    _richSelectionToolbar?.remove();
    _richSelectionToolbar = null;
  }

function _restoreRichSelectionToolbarRange(rich) {
    const range = _richSelectionToolbarRange;
    if (!range || !rich?.isConnected) return false;
    try {
      // Focus first. On mobile, focusing after addRange() can collapse the
      // restored range and make the following palette action a no-op.
      rich.focus({ preventScroll: true });
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range.cloneRange());
      return true;
    } catch (_) {
      return false;
    }
  }

function _richSelectionToolbarButton(action, label, html, title) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.richSelectionAction = action;
    button.setAttribute('aria-label', label);
    button.title = title || label;
    button.innerHTML = html;
    return button;
  }

function _ensureRichSelectionToolbar(rich) {
    if (_richSelectionToolbar?.isConnected) return _richSelectionToolbar;
    const toolbar = document.createElement('div');
    toolbar.id = 'doc-rich-selection-toolbar';
    toolbar.className = 'doc-rich-selection-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Format selected text');
    toolbar.append(
      _richSelectionToolbarButton('bold', 'Bold', '<b>B</b>', 'Bold (Ctrl+B)'),
      _richSelectionToolbarButton('italic', 'Italic', '<i>I</i>', 'Italic (Ctrl+I)'),
      _richSelectionToolbarButton('underline', 'Underline', '<u>U</u>', 'Underline (Ctrl+U)'),
      _richSelectionToolbarButton('strike', 'Strikethrough', '<s>S</s>', 'Strikethrough'),
      _richSelectionToolbarButton('link', 'Link', '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'),
      _richSelectionToolbarButton('highlight:#fef08a', 'Highlight', '<svg class="rich-highlight-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 11-6 6v3h3l6-6"/><path d="m22 12-7-7-8.5 8.5 7 7Z"/></svg>'),
      _richSelectionToolbarButton('insert-image', 'Add image', '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/><path d="M19 5v6M16 8h6"/></svg>'),
      _richSelectionToolbarButton('removeformat', 'Clear formatting', '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16M12 5v14M8 19h8"/><path d="m4 4 16 16"/></svg>')
    );
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'doc-rich-selection-close';
    closeButton.setAttribute('aria-label', 'Close formatting toolbar');
    closeButton.title = 'Close';
    closeButton.innerHTML = '<span aria-hidden="true">×</span>';
    toolbar.appendChild(closeButton);
    const preserve = event => event.preventDefault();
    toolbar.addEventListener('pointerdown', preserve);
    toolbar.addEventListener('mousedown', preserve);
    toolbar.addEventListener('click', event => {
      const close = event.target.closest('.doc-rich-selection-close');
      if (close) {
        event.preventDefault();
        event.stopPropagation();
        _hideRichSelectionToolbar();
        return;
      }
      const button = event.target.closest('[data-rich-selection-action]');
      if (!button || !_restoreRichSelectionToolbarRange(rich)) return;
      event.preventDefault();
      event.stopPropagation();
      if (button.dataset.richSelectionAction === 'insert-image') {
        setRichImageInsertRange(_richSelectionToolbarRange?.cloneRange?.() || null);
        _showRichImageSourceMenu(button);
        _hideRichSelectionToolbar();
        return;
      }
      applyMdFormat(button.dataset.richSelectionAction);
      if (button.dataset.richSelectionAction !== 'link') {
        requestAnimationFrame(() => _scheduleRichSelectionToolbar(rich));
      } else {
        _hideRichSelectionToolbar();
      }
    });
    document.body.appendChild(toolbar);
    _richSelectionToolbar = toolbar;
    return toolbar;
  }

function _renderRichSelectionToolbar(rich) {
    _richSelectionToolbarFrame = 0;
    // Markdown Visual mode uses the fixed Markdown toolbar, never this floating one.
    if (!rich?.isConnected || rich.style.display === 'none' || rich.classList.contains('markdown-visual-mode')) {
      _hideRichSelectionToolbar();
      return;
    }
    const selection = window.getSelection?.();
    if (!selection?.rangeCount || selection.isCollapsed) {
      _hideRichSelectionToolbar();
      return;
    }
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer) || !range.toString().trim()) {
      _hideRichSelectionToolbar();
      return;
    }
    const rects = Array.from(range.getClientRects());
    const visibleRects = rects.filter(item => item.width > 0 && item.height > 0);
    const bounds = range.getBoundingClientRect();
    const anchorRect = visibleRects[0] || bounds;
    const selectionTop = visibleRects.length
      ? Math.min(...visibleRects.map(item => item.top))
      : bounds.top;
    const selectionBottom = visibleRects.length
      ? Math.max(...visibleRects.map(item => item.bottom))
      : bounds.bottom;
    const visualViewport = window.visualViewport;
    const viewportLeft = visualViewport?.offsetLeft || 0;
    const viewportTop = visualViewport?.offsetTop || 0;
    const viewportWidth = visualViewport?.width || window.innerWidth;
    const viewportHeight = visualViewport?.height || window.innerHeight;
    const viewportRight = viewportLeft + viewportWidth;
    const viewportBottom = viewportTop + viewportHeight;
    if (!bounds || (!bounds.width && !bounds.height)
        || selectionBottom < viewportTop || selectionTop > viewportBottom) {
      _hideRichSelectionToolbar();
      return;
    }

    _richSelectionToolbarRange = range.cloneRange();
    const toolbar = _ensureRichSelectionToolbar(rich);
    toolbar.querySelectorAll('[data-rich-selection-action]').forEach(button => button.classList.remove('is-active'));
    const activeCommands = {
      bold: 'bold',
      italic: 'italic',
      underline: 'underline',
      strike: 'strikeThrough',
    };
    for (const [action, command] of Object.entries(activeCommands)) {
      try {
        toolbar.querySelector(`[data-rich-selection-action="${action}"]`)
          ?.classList.toggle('is-active', document.queryCommandState(command));
      } catch (_) {}
    }
    toolbar.querySelector('[data-rich-selection-action="link"]')
      ?.classList.toggle('is-active', !!_richLinkAtRange(rich, range));
    const clearButton = toolbar.querySelector('[data-rich-selection-action="removeformat"]');
    if (clearButton) clearButton.style.display = _richSelectionHasFormatting(rich, range) ? '' : 'none';

    toolbar.style.visibility = 'hidden';
    toolbar.style.zIndex = String(topPortalZ());
    const toolbarRect = toolbar.getBoundingClientRect();
    const richRect = rich.getBoundingClientRect();
    let safeLeft = Math.max(viewportLeft + 8, richRect.left + 4);
    let safeRight = Math.min(viewportRight - 8, richRect.right - 4);
    let safeTop = Math.max(viewportTop + 8, richRect.top + 4);
    const safeBottom = Math.min(viewportBottom - 8, richRect.bottom - 4);

    // Toolbars and find controls sit immediately above the editable surface.
    // Keep the contextual formatter below any visible control that overlaps it.
    for (const blocker of [
      document.getElementById('doc-md-toolbar'),
      document.getElementById('doc-find-bar'),
    ]) {
      if (!blocker || blocker.hidden || blocker.offsetParent === null) continue;
      const blockerRect = blocker.getBoundingClientRect();
      const overlapsHorizontally = blockerRect.right > safeLeft && blockerRect.left < safeRight;
      if (overlapsHorizontally && blockerRect.top < selectionBottom && blockerRect.bottom > safeTop) {
        safeTop = blockerRect.bottom + 6;
      }
    }
    if (safeRight - safeLeft < toolbarRect.width) {
      safeLeft = viewportLeft + 8;
      safeRight = viewportRight - 8;
    }
    const maxLeft = Math.max(safeLeft, safeRight - toolbarRect.width);
    const left = Math.max(safeLeft, Math.min(
      anchorRect.left + anchorRect.width / 2 - toolbarRect.width / 2,
      maxLeft
    ));
    const maxTop = Math.max(safeTop, safeBottom - toolbarRect.height);
    const above = selectionTop - toolbarRect.height - 8;
    const below = selectionBottom + 8;
    const isMobileViewport = viewportWidth <= 768;
    // Mobile browsers own the Copy/Paste selection menu and do not expose
    // its geometry. Dock our formatter away from the selected range so the
    // two floating controls do not stack on top of each other.
    const top = isMobileViewport
      ? Math.max(safeTop, Math.min(maxTop, safeBottom - toolbarRect.height))
      : (above >= safeTop
        ? above
        : (below + toolbarRect.height <= safeBottom ? below : Math.min(maxTop, below)));
    toolbar.style.left = `${left}px`;
    toolbar.style.top = `${Math.max(safeTop, top)}px`;
    toolbar.style.visibility = '';
  }

export function _scheduleRichSelectionToolbar(rich) {
    if (rich?.classList?.contains('markdown-visual-mode')) return;
    if (_richSelectionToolbarFrame) cancelAnimationFrame(_richSelectionToolbarFrame);
    _richSelectionToolbarFrame = requestAnimationFrame(() => _renderRichSelectionToolbar(rich));
  }

export function _followRichSelectionToolbarLayout(rich, duration = 360) {
    if (rich?.classList?.contains('markdown-visual-mode')) return;
    if (_richSelectionToolbarFollowFrame) cancelAnimationFrame(_richSelectionToolbarFollowFrame);
    const deadline = performance.now() + duration;
    const follow = () => {
      _renderRichSelectionToolbar(rich);
      if (performance.now() < deadline) {
        _richSelectionToolbarFollowFrame = requestAnimationFrame(follow);
      } else {
        _richSelectionToolbarFollowFrame = 0;
      }
    };
    _richSelectionToolbarFollowFrame = requestAnimationFrame(follow);
  }
