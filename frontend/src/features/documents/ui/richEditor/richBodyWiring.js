import { _selections, clearSelection, updateRichSelectionState } from '../selection/selectionHighlights.js';
import { scheduleAutoTitle } from '../editor/sourceEditor.js';
import { _scheduleDocumentStats, _refreshDocumentOutline, _scheduleDocumentHistoryControls } from '../status/documentStatus.js';
import { _richSelectionCell, _appendRichTableRow } from '../richTables.js';
import { _uploadMarkdownImages } from '../editor/markdownImages.js';
// frontend/src/features/documents/ui/richEditor/richBodyWiring.js
/**
 * Document Editor Rich-Text Subsystem: richBodyWiring
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _isMarkdownImageFile, _isRichTextLang } from '../../domain/documentKinds.js';
import { _RICH_FONT_SIZE_PX } from '../../domain/richTextRules.js';
import { _richDropdownCurrentActions } from './formatMenus.js';
import { _currentBlockTag, applyMdFormat } from './formatting.js';
import { _clearRichImageSelection, _normalizeRichTextImages, _selectRichImage, setRichImageInsertRange } from './imageOps.js';
import { _applyRichImageAction, _richLinkAtRange } from './links.js';
import { _richTextContentToHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport } from './richBodySync.js';
import { _activeRichImage, _cleanRichTextPasteHtml, _handleRichChecklistEnter, _handleRichHeadingEnter, _insertSmartRichPasteLink, _normalizeRichChecklists, _normalizeRichInlineCode, _richInlineCodeTypingArmed, _richSelectedSpacingBlockIndexes, _richSelectionChecklistItem, _richSelectionInlineCode, _richSpacingBlocks, _selectedRichImage, _setRichChecklistItemChecked, setRichInlineCodeTypingArmed } from './richOperations.js';
import { _followRichSelectionToolbarLayout, _hideRichSelectionToolbar, _richSelectionToolbar, _scheduleRichSelectionToolbar } from './selectionToolbar.js';
import { _applyRichBlockInputRule, _chooseRichSlashCommand, _hideRichSlashMenu, _renderRichSlashMenu, _richSlashActiveIndex, _richSlashContext, _richSlashMenu, _syncRichSlashMenu, setRichSlashActiveIndex } from './slashMenu.js';

export function _wireEmailRichbody(rich) {
    if (rich._wired) { _syncEmailRichbody(rich); return; }
    rich._wired = true;
    rich.addEventListener('input', () => {
      if (_richInlineCodeTypingArmed) {
        document.execCommand('styleWithCSS', false, false);
        setRichInlineCodeTypingArmed(false);
      }
      if (_activeRichImage && !_activeRichImage.isConnected) _clearRichImageSelection();
      _normalizeRichTextImages(rich);
      _normalizeRichChecklists(rich);
      _normalizeRichInlineCode(rich);
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      if (_selections.length) clearSelection({ preserveCaret: true });
      const findBar = document.getElementById('doc-find-bar');
      const findInput = document.getElementById('doc-find-input');
      if (findBar?.style.display !== 'none' && findInput?.value) {
        findInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      const docId = activeDocId;
      const doc = docId && docs.get(docId);
      const pane = rich.closest('#doc-editor-pane');
      if (doc && _isRichTextLang(doc.language) && !(doc.title || '').trim()
          && rich.isConnected && (!pane || (pane.isConnected && pane.dataset.activeDocumentId === docId))) {
        scheduleAutoTitle(rich.innerText || '', docId, 500);
      }
      _scheduleDocumentStats();
      _refreshDocumentOutline();
      _scheduleRichSelectionToolbar(rich);
      _syncRichSlashMenu(rich);
      _scheduleDocumentHistoryControls();
    });
    // Highlight toolbar buttons (B / I / S, headings, lists) when the caret
    // sits inside formatted text. queryCommandState reflects the live
    // selection — we just translate that into .is-active classes the CSS
    // already understands.
    let syncActiveFrame = 0;
    const syncActiveNow = () => {
      syncActiveFrame = 0;
      if (!rich.isConnected || rich.style.display === 'none') return;
      // Only sync when focus is inside the rich body — otherwise selection
      // outside it (e.g. clicking the toolbar itself) gives misleading state.
      if (!rich.contains(document.activeElement) && document.activeElement !== rich) return;
      const tb = document.getElementById('doc-md-toolbar');
      if (!tb) return;
      const set = (sel, on) => { const b = tb.querySelector(sel); if (b) b.classList.toggle('is-active', !!on); };
      try {
        set('[data-md="bold"]',   document.queryCommandState('bold'));
        set('[data-md="italic"]', document.queryCommandState('italic'));
        set('[data-md="strike"]', document.queryCommandState('strikeThrough'));
        set('[data-md="underline"]', document.queryCommandState('underline'));
        set('[data-md="superscript"]', document.queryCommandState('superscript'));
        set('[data-md="subscript"]', document.queryCommandState('subscript'));
        const sizeValue = String(document.queryCommandValue('fontSize') || '3');
        const sizeIcon = tb.querySelector('.rich-font-size-icon');
        if (sizeIcon) sizeIcon.textContent = String(_RICH_FONT_SIZE_PX[sizeValue] || 16);
      } catch (_) {}
      // Block-level: heading / list dropdown toggles read their active state
      // from the current block tag.
      const cur = _currentBlockTag(rich);
      const hBtn = tb.querySelector('[data-dd="heading"]');
      if (hBtn) hBtn.classList.toggle('is-active', /^h[1-6]$/.test(cur));
      try {
        const inList = document.queryCommandState('insertOrderedList') || document.queryCommandState('insertUnorderedList');
        const lBtn = tb.querySelector('[data-dd="list"]');
        if (lBtn) lBtn.classList.toggle('is-active', !!inList);
        // queryCommandState() is inconsistent between browsers and can report
        // right alignment for a plain paragraph. Read the selected block's
        // actual style instead, so the control reflects what the user sees.
        const alignActions = _richDropdownCurrentActions('align', rich, null, null);
        const aBtn = tb.querySelector('[data-dd="align"]');
        const selection = window.getSelection();
        const node = selection?.anchorNode;
        const element = node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
        const selectedBlock = element?.closest?.('p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre');
        const hasExplicitAlignment = !!selectedBlock?.style?.textAlign;
        if (aBtn) aBtn.classList.toggle('is-active', hasExplicitAlignment || !alignActions.has('alignleft'));
      } catch (_) {}
      const tableBtn = tb.querySelector('[data-dd="table"]');
      if (tableBtn) tableBtn.classList.toggle('is-active', !!_richSelectionCell(rich));
      const imageBtn = tb.querySelector('[data-dd="image"]');
      if (imageBtn) imageBtn.classList.toggle('is-active', !!_selectedRichImage(rich));
      const codeBtn = tb.querySelector('[data-dd="code"]');
      if (codeBtn) codeBtn.classList.toggle('is-active', cur === 'pre' || _richInlineCodeTypingArmed || !!_richSelectionInlineCode(rich));
      const selection = window.getSelection();
      const selectionRange = selection?.rangeCount ? selection.getRangeAt(0) : null;
      const currentLink = _richLinkAtRange(rich, selectionRange);
      set('[data-md="link"]', !!currentLink);
      const spacingBtn = tb.querySelector('[data-dd="spacing"]');
      if (spacingBtn) {
        const blocks = _richSpacingBlocks(rich);
        const indexes = _richSelectedSpacingBlockIndexes(rich);
        spacingBtn.classList.toggle('is-active', indexes.some(index => !!blocks[index]?.style.lineHeight));
      }
      // Keep dropdown tools visibly latched while their formatting is active.
      // This is especially useful for color, font, size, and alignment where
      // the current state is otherwise only visible inside the popup.
      const dropdownActive = (selector, kind, defaultAction) => {
        const button = tb.querySelector(selector);
        if (!button) return;
        const actions = _richDropdownCurrentActions(kind, rich, null, null);
        const active = actions.size > 0 && !(defaultAction && actions.size === 1 && actions.has(defaultAction));
        button.classList.toggle('is-active', active);
      };
      dropdownActive('[data-dd="textsize"]', 'textsize', 'fontsize:3');
      dropdownActive('[data-dd="color"]', 'color', 'forecolor:default');
      dropdownActive('[data-dd="highlight"]', 'highlight', 'highlight:transparent');
      dropdownActive('[data-dd="spacing"]', 'spacing');
    };
    const syncActive = () => {
      if (syncActiveFrame) return;
      syncActiveFrame = requestAnimationFrame(syncActiveNow);
    };
    rich.addEventListener('keyup',    syncActive);
    rich.addEventListener('mouseup',  syncActive);
    rich.addEventListener('focus',    syncActive);
    rich.addEventListener('focus', _scheduleDocumentHistoryControls);
    rich.addEventListener('mouseup', () => {
      setTimeout(() => {
        updateRichSelectionState(rich);
        _scheduleRichSelectionToolbar(rich);
      }, 50);
    });
    rich.addEventListener('keyup', (e) => {
      if (e.shiftKey) updateRichSelectionState(rich);
      _scheduleRichSelectionToolbar(rich);
    });
    rich.addEventListener('scroll', () => _scheduleRichSelectionToolbar(rich), { passive: true });
    rich.addEventListener('cut', () => {
      const selection = window.getSelection?.();
      const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      if (!range || range.collapsed || !rich.contains(range.commonAncestorContainer)) return;
      const caretRange = range.cloneRange();
      caretRange.collapse(true);
      setTimeout(() => {
        try {
          rich.focus();
          const current = window.getSelection?.();
          current?.removeAllRanges();
          current?.addRange(caretRange);
        } catch (_) {}
      }, 0);
    });
    rich.addEventListener('paste', (e) => {
      const doc = activeDocId && docs.get(activeDocId);
      if (!doc || !_isRichTextLang(doc.language)) return;
      const images = Array.from(e.clipboardData?.files || []).filter(_isMarkdownImageFile);
      if (images.length) {
        e.preventDefault();
        e.stopPropagation();
        const selection = window.getSelection();
        if (selection?.rangeCount && rich.contains(selection.getRangeAt(0).commonAncestorContainer)) {
          setRichImageInsertRange(selection.getRangeAt(0).cloneRange());
        }
        _uploadMarkdownImages(images);
        return;
      }

      const html = e.clipboardData?.getData('text/html') || '';
      const text = e.clipboardData?.getData('text/plain') || '';
      if (!html && !text) return;
      e.preventDefault();
      rich.focus();
      if (!html && _insertSmartRichPasteLink(rich, text)) {
        // Smart URL paste is already inserted through the native undo stack.
      } else if (html) document.execCommand('insertHTML', false, _cleanRichTextPasteHtml(html));
      else document.execCommand('insertText', false, text);
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      e.stopPropagation();
    });
    rich.addEventListener('dragover', (e) => {
      const doc = activeDocId && docs.get(activeDocId);
      if (!doc || !_isRichTextLang(doc.language)) return;
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });
    rich.addEventListener('drop', (e) => {
      const doc = activeDocId && docs.get(activeDocId);
      if (!doc || !_isRichTextLang(doc.language)) return;
      const images = Array.from(e.dataTransfer?.files || []).filter(_isMarkdownImageFile);
      if (!images.length) return;
      e.preventDefault();
      e.stopPropagation();
      const range = document.caretRangeFromPoint?.(e.clientX, e.clientY);
      setRichImageInsertRange(range && rich.contains(range.commonAncestorContainer) ? range.cloneRange() : null);
      _uploadMarkdownImages(images);
    });
    rich.addEventListener('pointerdown', (e) => {
      const image = e.target?.closest?.('img.richtext-image, figure.richtext-image img');
      if (image && rich.contains(image)) {
        e.preventDefault();
        _selectRichImage(rich, image);
        return;
      }
      if (_activeRichImage) _clearRichImageSelection();
      const item = e.target?.closest?.('ul.rich-checklist > li');
      if (!item || !rich.contains(item)) return;
      const rect = item.getBoundingClientRect();
      if (e.clientX < rect.left - 30 || e.clientX > rect.left + 4) return;
      e.preventDefault();
      _setRichChecklistItemChecked(rich, item, item.dataset.checked !== 'true');
    });
    rich.addEventListener('keydown', (e) => {
      if (_richSlashMenu) {
        const commandCount = _richSlashMenu._commands?.length || 0;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
          e.preventDefault();
          e.stopPropagation();
          if (!commandCount) setRichSlashActiveIndex(0);
          else if (e.key === 'Home') setRichSlashActiveIndex(0);
          else if (e.key === 'End') setRichSlashActiveIndex(commandCount - 1);
          else {
            const delta = e.key === 'ArrowDown' ? 1 : -1;
            setRichSlashActiveIndex((_richSlashActiveIndex + delta + commandCount) % commandCount);
          }
          const context = _richSlashContext(rich);
          if (context) _renderRichSlashMenu(rich, context);
          return;
        }
        if (e.key === 'Enter' && commandCount) {
          e.preventDefault();
          e.stopPropagation();
          _chooseRichSlashCommand(rich);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          e.stopPropagation();
          _hideRichSlashMenu();
          return;
        }
      }
      if (e.key === ' ' && !e.ctrlKey && !e.metaKey && !e.altKey && _applyRichBlockInputRule(rich)) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const liveSelection = window.getSelection?.();
      if (e.key === 'Escape' && _richSelectionToolbar && liveSelection && !liveSelection.isCollapsed) {
        e.preventDefault();
        e.stopPropagation();
        liveSelection.collapseToEnd();
        _hideRichSelectionToolbar();
        return;
      }
      if (_activeRichImage && (e.key === 'Delete' || e.key === 'Backspace')) {
        e.preventDefault();
        _applyRichImageAction(rich, 'image:delete');
        return;
      }
      if (_activeRichImage && e.key === 'Escape') {
        e.preventDefault();
        _clearRichImageSelection();
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      const key = String(e.key || '').toLowerCase();
      if (mod && key === 'enter') {
        const item = _richSelectionChecklistItem(rich);
        if (item) {
          e.preventDefault();
          _setRichChecklistItemChecked(rich, item, item.dataset.checked !== 'true');
          return;
        }
      }
      if (e.key === 'Enter' && !mod && !e.shiftKey && !e.altKey && _handleRichChecklistEnter(rich)) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (e.key === 'Enter' && !mod && !e.shiftKey && !e.altKey && _handleRichHeadingEnter(rich)) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      let action = '';
      if (mod && e.altKey && !e.shiftKey && /^[0-6]$/.test(key)) action = key === '0' ? 'paragraph' : `h${key}`;
      else if (mod && !e.altKey && key === 'b') action = 'bold';
      else if (mod && !e.altKey && key === 'i') action = 'italic';
      else if (mod && !e.altKey && key === 'u') action = 'underline';
      else if (mod && !e.altKey && key === 'k') action = 'link';
      else if (mod && e.shiftKey && key === 'x') action = 'strike';
      else if (mod && e.shiftKey && key === '5') action = 'strike';
      else if (mod && e.shiftKey && key === '7') action = 'ol';
      else if (mod && e.shiftKey && key === '8') action = 'ul';
      else if (mod && e.shiftKey && !e.altKey && key === 'l') action = 'alignleft';
      else if (mod && e.shiftKey && !e.altKey && key === 'e') action = 'aligncenter';
      else if (mod && e.shiftKey && !e.altKey && key === 'j') action = 'alignjustify';
      else if (mod && !e.altKey && key === '[') action = 'outdent';
      else if (mod && !e.altKey && key === ']') action = 'indent';
      else if (mod && !e.altKey && key === '.') action = 'superscript';
      else if (mod && !e.altKey && key === ',') action = 'subscript';
      else if (mod && !e.altKey && key === '\\') action = 'removeformat';
      else if (mod && e.shiftKey && key === '`') action = 'codeblock';
      else if (mod && !e.altKey && key === '`') action = 'code';
      if (action) {
        e.preventDefault();
        applyMdFormat(action);
        return;
      }
      if (e.key === 'Tab') {
        const selection = window.getSelection();
        const anchor = selection?.anchorNode;
        const anchorEl = anchor?.nodeType === Node.ELEMENT_NODE ? anchor : anchor?.parentElement;
        const cell = anchorEl?.closest?.('td, th');
        if (cell && rich.contains(cell)) {
          e.preventDefault();
          const table = cell.closest('table');
          let cells = Array.from(table.querySelectorAll('th, td'));
          let index = cells.indexOf(cell) + (e.shiftKey ? -1 : 1);
          if (index >= cells.length && !e.shiftKey) {
            if (_appendRichTableRow(rich, table)) {
              _syncEmailRichbody(rich);
              _scheduleEmailRichbodySave();
              _scheduleDocumentHistoryControls();
            }
            return;
          }
          const target = cells[Math.max(0, index)];
          if (target) {
            const range = document.createRange();
            range.selectNodeContents(target);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
          }
          return;
        }
        let inList = false;
        try { inList = document.queryCommandState('insertOrderedList') || document.queryCommandState('insertUnorderedList'); } catch (_) {}
        if (inList) {
          e.preventDefault();
          applyMdFormat(e.shiftKey ? 'outdent' : 'indent');
        }
      }
    });
    const editorSignal = rich.closest('#doc-editor-pane')?._lifetime?.signal;
    // selectionchange fires on the document; filter to selections inside rich.
    document.addEventListener('selectionchange', () => {
      const sel = window.getSelection();
      if (sel && sel.rangeCount && rich.contains(sel.anchorNode)) {
        syncActive();
        _scheduleDocumentStats();
        _scheduleRichSelectionToolbar(rich);
      } else if (_richSelectionToolbar) {
        _hideRichSelectionToolbar();
      }
      if (!sel || !sel.rangeCount || !rich.contains(sel.anchorNode)) _hideRichSlashMenu();
    }, { signal: editorSignal });
    window.addEventListener('resize', () => {
      _scheduleRichSelectionToolbar(rich);
      _followRichSelectionToolbarLayout(rich);
    }, { passive: true, signal: editorSignal });
    window.visualViewport?.addEventListener('resize', () => {
      _scheduleRichSelectionToolbar(rich);
      _followRichSelectionToolbarLayout(rich);
    }, { passive: true, signal: editorSignal });
    rich._syncActive = syncActive;
  }

export function _showRichTextEditor(doc) {
    const rich = document.getElementById('doc-email-richbody');
    const source = document.getElementById('doc-editor-wrap');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!rich || !source) return;

    _clearRichImageSelection();
    setRichInlineCodeTypingArmed(false);
    document.execCommand('styleWithCSS', false, false);
    source.style.display = 'none';
    rich.style.display = '';
    // Keep the plain-text mirror available to save/send code, but never show
    // its second "Start writing" surface alongside the rich editor.
    if (textarea) textarea.style.display = 'none';
    rich.classList.add('richtext-mode');
    // The rich editor is a formatting surface, not a browser spellcheck
    // field. Disable native red underlines, which are especially distracting
    // for names, code, and mixed-language email text.
    rich.spellcheck = false;
    rich.setAttribute('aria-label', 'Rich text document');
    rich.innerHTML = _richTextContentToHtml(doc?.content || '');
    _normalizeRichTextImages(rich);
    _normalizeRichChecklists(rich);
    _normalizeRichInlineCode(rich);
    _wireEmailRichbody(rich);
    _syncEmailRichbody(rich);
    _syncRichEmptyImport(rich);
    if (textarea) textarea.spellcheck = true;
  }
