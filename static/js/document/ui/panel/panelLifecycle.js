import { _clearRichImageSelection } from '../richEditor/imageOps.js';
// ui/panel/panelLifecycle.js

import { _markDocVisibleState } from '../../application/documentSession.js';

import { _lastSessionId, _minimizedDocId, activeDocId, bumpNavigationGeneration, docs, isOpen, setActiveDocId, setMinimizedDocId, setPanelOpen } from '../../application/documentState.js';

import { _flushAutoSave } from '../../application/persistence.js';

import { _autoCreateSession, _detachDocFromSession, createDocument } from '../documentFlows.js';

import { _autoDetectDebounce, _autoTitleDebounce, _hlDebounce } from '../editor/sourceEditor.js';

import { _persistEmailLocalDraftNow } from '../email/localDraft.js';

import { _closeVersionPanel } from '../history/versionHistory.js';

import { initDividerDrag } from './paneLayout.js';

import { _emailRichbodyActive } from '../richEditor/richBodySync.js';

import { _hideRichSelectionToolbar, _richSelectionToolbar } from '../richEditor/selectionToolbar.js';

import { _hideRichSlashMenu, _richSlashMenu } from '../richEditor/slashMenu.js';

import { _selections, clearSelection } from '../selection/selectionHighlights.js';

import { _closeDocumentOutline, _renderDocumentStats } from '../status/documentStatus.js';

import { _closeDocTabMenu, _docTabMenu, renderTabs, showEmptyState } from '../tabs/tabBar.js';

import { initMdToolbar } from '../toolbar/toolbar.js';

import { Modals, bindMenuDismiss, dismissOrRemove, dismissTopMenu, sessionModule, spinnerModule, topPortalZ } from '../host.js';

import { bindPaneLayoutPointerSupport, bindPaneLayoutControls } from './paneLayout.js';

import { mountPaneTemplate } from './paneTemplate.js';

import { bindHeaderActionsPrimary, bindHeaderActionsSecondary } from '../header/headerActions.js';

import { bindLanguagePicker } from '../header/languagePicker.js';

import { bindComposerWiring } from '../email/composerWiring.js';

import { bindSourceEditor } from '../editor/sourceEditorBinding.js';

import { disposePdfTools } from '../moduleWiring.js';

import { disposeSourceEditor } from '../editor/sourceEditor.js';

import { disposeSelectionHighlights } from '../selection/selectionHighlights.js';
import { _diffModeActive, exitDiffMode } from '../diffViewer.js';
import { _hasPendingPdfPaneSave } from '../pdfTools.js';
import { saveCurrentToMap, switchToDoc } from './documentPresenter.js';
import { suspendSuggestions } from '../suggestions.js';

let _hasMountedPanel = false;

export function prepareDocumentOpen(sessionId) {
    if (sessionId) _markDocVisibleState(sessionId, 'open');
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }
  }

let _loadingSpinner = null;

export function _showLoadingOverlay() {
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    let overlay = wrap.querySelector('.doc-loading-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'doc-loading-overlay';
      wrap.appendChild(overlay);
    }
    overlay.innerHTML = '';
    overlay.style.display = '';
    _loadingSpinner = spinnerModule.create('', 'clean', 'whirlpool');
    const el = _loadingSpinner.createElement();
    overlay.appendChild(el);
    _loadingSpinner.start();
  }

export function _hideLoadingOverlay() {
    if (_loadingSpinner) { _loadingSpinner.destroy(); _loadingSpinner = null; }
    const overlay = document.querySelector('.doc-loading-overlay');
    if (overlay) overlay.style.display = 'none';
  }

export function _minimizeNotesForDocumentOpen() {
    try {
      if (window.notesModule?.isPanelOpen?.()) {
        window.notesModule.closePanel('down');
        return;
      }
    } catch (_) {}
    if (!document.getElementById('notes-pane') && !document.getElementById('notes-pane-backdrop')) return;
    import('/static/js/notes.js?v=20260911notesselectioncancel1')
      .then(mod => {
        const close = mod.closeNotes || mod.closePanel || mod.default?.closeNotes || mod.default?.closePanel;
        if (typeof close === 'function') close('down');
      })
      .catch(() => {
        try { document.getElementById('notes-pane')?.remove(); } catch (_) {}
        try { document.getElementById('notes-pane-backdrop')?.remove(); } catch (_) {}
      });
  }

export function openPanel(options = {}) {
_minimizeNotesForDocumentOpen();

if (isOpen) return;

document.getElementById('doc-editor-pane')?._dispose?.();

document.getElementById('doc-editor-pane')?.remove();

document.getElementById('doc-divider')?.remove();

if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      setMinimizedDocId(null);
      Modals.unregister('doc-panel');
    }

const container = document.getElementById('chat-container');

if (!container) return;

setPanelOpen(true);

document.body.classList.remove('email-front');

_markDocVisibleState(_lastSessionId, 'open');

document.body.classList.add('doc-view');

const toggleBtn = document.getElementById('overflow-doc-btn');

if (toggleBtn) toggleBtn.classList.add('active');

const docInd = document.getElementById('doc-indicator-btn');

if (docInd) docInd.classList.add('active');

const divider = document.createElement('div');

divider.className = 'doc-divider';

divider.id = 'doc-divider';

divider.innerHTML = '<button type="button" class="doc-divider-collapse" title="Collapse panel" data-mode="collapse"><span>›</span></button>' +
      '<button type="button" class="doc-divider-hide" title="Hide panel" aria-label="Hide panel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';

const _divHide = divider.querySelector('.doc-divider-hide');

if (_divHide) {
      _divHide.addEventListener('mousedown', (e) => e.stopPropagation());
      _divHide.addEventListener('click', (e) => { e.stopPropagation(); closePanel('down'); });
    }

const pane = document.createElement('div');

pane.id = 'doc-editor-pane';

pane.className = 'doc-editor-pane';

const paneLifetime = new AbortController();

pane._lifetime = paneLifetime;

pane._dispose = () => {
      paneLifetime.abort();
      divider._disposeResize?.();
      pane.querySelector('#doc-md-toolbar')?._resizeObserver?.disconnect();
      pane.querySelector('#doc-tab-bar')?._resizeObserver?.disconnect();
      disposeSourceEditor(pane);
      _clearRichImageSelection();
      disposeSelectionHighlights();
      clearTimeout(_hlDebounce);
      clearTimeout(_autoDetectDebounce);
      clearTimeout(_autoTitleDebounce);
    };

if (options.restore) pane.classList.add('doc-pane-restoring');

    bindPaneLayoutPointerSupport({ pane });

    mountPaneTemplate({ pane });

{
      const _footer = pane.querySelector('#doc-actions-footer');
      const _split = _footer && _footer.querySelector('#doc-copy-export-split');
      const _undo = pane.querySelector('#doc-undo-btn');
      const _redo = pane.querySelector('#doc-redo-btn');
      const _lang = pane.querySelector('#doc-language-select');
      const _preview = pane.querySelector('#doc-header-preview-btn');  // single Run ▶ for python/bash/js/csv
      const _exportPdf = pane.querySelector('#doc-export-pdf-btn');
      const _pdfView = pane.querySelector('#doc-pdf-view-btn');
      if (_footer && _split) {
        // Footer order (left → right): Undo, Run/Preview, Lang, …, Copy/Export.
        // The X close was here too but is now redundant with the per-tab close
        // button in the title strip — removed.
        if (_undo) _footer.insertBefore(_undo, _footer.firstChild);
        const _anchor = _undo;
        if (_redo && _anchor) _anchor.after(_redo);
        const _historyAnchor = _redo || _anchor;
        if (_preview && _historyAnchor) _historyAnchor.after(_preview);
        if (_lang) _split.before(_lang);
        // Pull every remaining header-only control into the footer so we
        // only ever render ONE bottom action row. The standalone top header
        // was leaving a duplicate row above (with fullscreen + version badge
        // + stream indicator). Each item keeps its own display: toggling.
        const _streamInd = pane.querySelector('#doc-stream-indicator');
        const _versionBadge = pane.querySelector('#doc-version-badge');
        if (_split) {
          if (_pdfView)      _split.before(_pdfView);
          if (_exportPdf)    _split.before(_exportPdf);
          if (_versionBadge) _split.before(_versionBadge);
          if (_streamInd)    _split.before(_streamInd);
        }
      }
      // iOS keeps the soft keyboard up when you tap a <button> (it doesn't blur
      // the focused textarea), so it lingers after you've typed. Dismiss it on
      // any footer control tap.
      if (_footer) _footer.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('button, select')) return;
        const _ta = document.getElementById('doc-editor-textarea');
        if (_ta && document.activeElement === _ta) _ta.blur();
      });
    }

{
      const statsRoot = pane.querySelector('#doc-stats');
      const statsButton = pane.querySelector('#doc-stats-btn');
      const statsPopover = pane.querySelector('#doc-stats-popover');
      if (statsRoot && statsButton && statsPopover) {
        statsButton.addEventListener('mousedown', (event) => event.preventDefault());
        statsButton.addEventListener('click', () => {
          if (!statsPopover.hidden) {
            dismissOrRemove(statsPopover);
            return;
          }
          _renderDocumentStats();
          statsPopover.hidden = false;
          statsPopover.style.zIndex = String(topPortalZ());
          // The editor pane clips overflow, so a footer-anchored absolute
          // popover can disappear underneath the document. Float it against
          // the viewport and place it above the stats button.
          const buttonRect = statsButton.getBoundingClientRect();
          const popoverHeight = statsPopover.offsetHeight;
          const popoverWidth = statsPopover.offsetWidth;
          const left = Math.max(8, Math.min(
            window.innerWidth - popoverWidth - 8,
            buttonRect.right - popoverWidth,
          ));
          const above = buttonRect.top - popoverHeight - 7;
          statsPopover.style.left = `${Math.round(left)}px`;
          statsPopover.style.top = `${Math.round(above >= 8 ? above : buttonRect.bottom + 7)}px`;
          statsButton.setAttribute('aria-expanded', 'true');
          bindMenuDismiss(
            statsPopover,
            () => {
              statsPopover.hidden = true;
              statsButton.setAttribute('aria-expanded', 'false');
            },
            event => !statsRoot.contains(event.target),
          );
        });
      }
    }

const desktopEntrance = window.innerWidth > 768;

const sidebar = document.getElementById('sidebar');

const isRight = sidebar && sidebar.classList.contains('right-side');

if (isRight) {
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

const isStartupMount = !!document.getElementById('app-loader');

if (desktopEntrance && (_hasMountedPanel || isStartupMount)) {
      const isFirstMount = !_hasMountedPanel;
      _hasMountedPanel = true;
      // Hide the surface, not the flex item. This keeps the chat/document
      // split stable for the entire animation.
      pane.style.clipPath = 'inset(0 0 0 100%)';
      pane.style.opacity = '0';
      pane.style.borderColor = 'transparent';
      pane.style.boxShadow = 'none';
      divider.style.opacity = '0';
      const reveal = () => {
        if (!pane.isConnected || !isOpen) return;
        pane.style.transition = 'clip-path 0.35s cubic-bezier(0.22,1,0.36,1), opacity 0.28s ease-out';
        pane.style.clipPath = 'inset(0 0 0 0)';
        pane.style.opacity = '1';
        pane.style.borderColor = '';
        pane.style.boxShadow = '';
        divider.style.transition = 'opacity 0.2s ease-out';
        divider.style.opacity = '1';
        setTimeout(() => {
          pane.style.transition = '';
          pane.style.clipPath = '';
          pane.style.opacity = '';
          pane.style.borderColor = '';
          pane.style.boxShadow = '';
          divider.style.transition = '';
          divider.style.opacity = '';
        }, 380);
      };
      // Commit the new flex layout first, then pause briefly before revealing.
      // The pause plus the reveal is roughly one second overall.
      requestAnimationFrame(() => {
        const scheduleReveal = () => setTimeout(reveal, 650);
        if (isFirstMount) requestAnimationFrame(scheduleReveal);
        else scheduleReveal();
      });
    } else if (desktopEntrance) {
      _hasMountedPanel = true;
    }

    bindPaneLayoutControls({ divider, pane, paneLifetime, isRight });

    bindHeaderActionsPrimary({ pane, divider, paneLifetime, isRight });

    bindLanguagePicker({ pane, paneLifetime });

    bindComposerWiring({ pane, paneLifetime });

    bindHeaderActionsSecondary({ pane, divider, paneLifetime, isRight });

initMdToolbar();

    bindSourceEditor({ pane, paneLifetime });

renderTabs();

if (docs.size === 0 || !activeDocId) {
      showEmptyState();
    }
}

export function _ensureDocChipRegistered() {
    if (Modals.isRegistered('doc-panel')) return;
    Modals.register('doc-panel', {
      // The ✕ / drag-to-trash on the minimized chip is a real close — detach
      // the doc from the chat session so it doesn't reappear in that chat.
      closeFn: () => {
        // Content was already saved to the map when the panel was minimized,
        // so just detach (don't re-read the now-removed editor).
        const id = _minimizedDocId;
        setMinimizedDocId(null);
        if (id) _detachDocFromSession(id);
      },
      restoreFn: () => {
        const id = _minimizedDocId;
        setMinimizedDocId(null);
        // openPanel builds the pane shell; switchToDoc re-renders the
        // saved doc content into it (including PDF render-pages, syntax
        // highlighting, etc.). Without switchToDoc, the pane is empty.
        openPanel();
        if (id && docs.has(id)) {
          try { switchToDoc(id); } catch (e) { console.error('Restore doc failed:', e); }
        }
      },
    });
  }

export function closePanel(direction) {
    if (!isOpen) {
      if (direction !== 'down' && Modals.isRegistered('doc-panel')) {
        setMinimizedDocId(null);
        _markDocVisibleState(_lastSessionId, 'closed');
        Modals.unregister('doc-panel');
      }
      return;
    }
    bumpNavigationGeneration();
    _closeVersionPanel();
    document.getElementById('doc-version-panel')?.remove();
    setPanelOpen(false);
    _closeDocumentOutline();
    _hideRichSelectionToolbar();
    _hideRichSlashMenu();
    if (_diffModeActive) exitDiffMode(true);
    try {
      CSS.highlights?.delete('doc-find-results');
      CSS.highlights?.delete('doc-find-current');
      CSS.highlights?.delete('doc-ai-selections');
    } catch (_) {}
    // On touch, closing the doc should leave the keyboard DOWN. The tap blurs
    // the textarea (keyboard starts down), but a stray refocus during teardown
    // (the view behind regaining focus, etc.) was bouncing it back up. Blur any
    // focused field now and again after the close settles to keep it down.
    if (direction !== 'down' && (('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) {
      const _dropKb = () => {
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) { try { ae.blur(); } catch (_) {} }
      };
      _dropKb();
      requestAnimationFrame(_dropKb);
      setTimeout(_dropKb, 80);
    }
    // Snapshot before disposing the pane. PDF timers need a captured draft,
    // rather than DOM references that disappear during the close animation.
    saveCurrentToMap();
    _persistEmailLocalDraftNow();
    if (_hasPendingPdfPaneSave(activeDocId)) _flushAutoSave(activeDocId);
    disposePdfTools();
    suspendSuggestions();

    // A "down" close means minimize, not close. Register the chip and flip
    // the dock state to minimized so a chip appears at the bottom. Any
    // other direction is a real close — make sure any leftover chip from a
    // prior minimize cycle is cleared too.
    if (direction === 'down') {
      setMinimizedDocId(activeDocId);
      _markDocVisibleState(_lastSessionId, 'minimized');
      _ensureDocChipRegistered();
      Modals.minimize('doc-panel');
    } else if (Modals.isRegistered('doc-panel')) {
      setMinimizedDocId(null);
      _markDocVisibleState(_lastSessionId, 'closed');
      Modals.unregister('doc-panel');
    } else {
      _markDocVisibleState(_lastSessionId, 'closed');
    }

    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');
    pane?._dispose?.();

    const _finishClose = () => {
      // If the panel was reopened during the slide-out animation (close →
      // reopen fast, e.g. close a draft then immediately compose a new one),
      // bail — otherwise this stale close strips doc-view after the new open
      // re-added it, and the fresh pane drops into the desktop split layout
      // (renders as a narrow "sidebar" on mobile).
      if (isOpen) { if (pane) pane.remove(); if (divider) divider.remove(); return; }
      document.body.classList.remove('doc-view');
      const container = document.getElementById('chat-container');
      if (container) container.style.display = '';
      if (pane) pane.remove();
      if (divider) divider.remove();
      setActiveDocId(null);
      const btn = document.getElementById('overflow-doc-btn');
      if (btn) btn.classList.remove('active');
      const docInd = document.getElementById('doc-indicator-btn');
      if (docInd) docInd.classList.remove('active');
    };

    if (pane) {
      // Determine slide direction
      let transform;
      if (direction === 'down') {
        // Full slide off-screen on mobile (sheet dismiss); small nudge on desktop.
        transform = window.innerWidth <= 768 ? 'translateY(100%)' : 'translateY(30px)';
      } else {
        const fromLeft = pane.classList.contains('doc-left');
        transform = fromLeft ? 'translateX(-40px)' : 'translateX(40px)';
      }
      pane.style.transition = 'transform 0.15s ease-in, opacity 0.1s ease-in';
      pane.style.transform = transform;
      pane.style.opacity = '0';
      if (divider) { divider.style.transition = 'opacity 0.1s ease-in'; divider.style.opacity = '0'; }
      pane.addEventListener('transitionend', _finishClose, { once: true });
      // Safety fallback
      setTimeout(_finishClose, 200);
    } else {
      _finishClose();
    }
  }

export function swapSide() {
    if (!isOpen) return;
    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');
    const container = document.getElementById('chat-container');
    if (!pane || !divider || !container) return;

    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');

    if (isRight) {
      // Sidebar moved right → doc goes left (before chat)
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      // Sidebar moved left → doc goes right (after chat)
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

    // Re-init divider drag for the new side
    initDividerDrag(divider, pane, isRight);
  }

export async function newDocument() {
    let sessionId = docs.get(activeDocId)?.sessionId
      || _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try { sessionId = await _autoCreateSession(); }
      catch (e) { console.error('Failed to auto-create session for document:', e); return; }
    }
    await createDocument(sessionId);
  }

let escapeLayeringInstalled = false;

export function installEscapeLayering() {
if (!escapeLayeringInstalled) {
    escapeLayeringInstalled = true;
    window.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !isOpen) return;

      // If email is the visible foreground window, its own inner Escape
      // handler owns the event. A document selection behind it should not
      // intercept the email reader's Escape key.
      const emailModal = document.getElementById('email-lib-modal');
      const target = e.target;
      if (emailModal && !emailModal.classList.contains('hidden')
          && !target?.closest?.('.doc-editor-pane, #doc-rich-selection-toolbar, #doc-selection-badge, #doc-md-dd-menu')) {
        return;
      }

      const versionPanel = document.getElementById('doc-version-panel');
      if (versionPanel && !versionPanel.classList.contains('hidden')) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeVersionPanel({ restoreFocus: true });
        return;
      }

      // Menus are transient and must close before selection state. The stack
      // also covers color pickers and other document dropdowns.
      if (dismissTopMenu()) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        return;
      }
      const menu = document.getElementById('doc-md-dd-menu');
      const menuVisible = menu && !menu.hidden && getComputedStyle(menu).display !== 'none';
      if (menuVisible) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        menu._dismiss?.(true);
        return;
      }
      if (_docTabMenu && _docTabMenu.style.display === 'block') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeDocTabMenu();
        return;
      }
      if (_richSlashMenu) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _hideRichSlashMenu();
        return;
      }

      // A pinned text selection is the user's most local Escape target. Clear
      // it before closing the document itself.
      if (_selections.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        clearSelection();
        return;
      }

      if (_richSelectionToolbar) {
        const rich = _emailRichbodyActive();
        const selection = window.getSelection?.();
        if (rich && selection && !selection.isCollapsed
            && rich.contains(selection.anchorNode) && rich.contains(selection.focusNode)) {
          selection.collapseToEnd();
        }
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _hideRichSelectionToolbar();
        return;
      }
    }, true);
  }
}
