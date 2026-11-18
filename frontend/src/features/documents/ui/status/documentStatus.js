// frontend/src/features/documents/ui/status/documentStatus.js
/**
 * Document save state, editor placeholder, stats, history controls, and outline.
 */

import { activeDocId, docs, _closingDocs } from '../../application/documentState.js';
import { _DOC_SAVE_STATE, _isRichTextLang } from '../../domain/documentKinds.js';
import { isMarkdownVisualModeActive } from '../richEditor/markdownVisualSync.js';
import {
  bindMenuDismiss,
  getDocumentStats,
  parseMarkdownOutline,
  topPortalZ,
} from '../host.js';

let _docStatsFrame = 0;

let _docOutlineMenu = null;

let _docOutlineClose = null;

let _docHistoryStateFrame = 0;

export function _renderDocumentSaveState() {
    const status = document.getElementById('doc-footer-copy-btn');
    if (!status) return;
    const doc = activeDocId && (docs.get(activeDocId) || _closingDocs.get(activeDocId));
    const state = doc?._saveState || 'saved';
    const meta = _DOC_SAVE_STATE[state] || _DOC_SAVE_STATE.saved;
    status.dataset.saveState = state;
    status.title = `${meta.title} (Ctrl+S)`;
    status.setAttribute('aria-label', meta.label);
    const label = status.querySelector('.doc-save-button-label');
    if (label) label.textContent = meta.label;
  }

export function _syncEditorPlaceholder() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    const placeholders = {
      '': 'Start typing or paste text to create a document...',
      markdown: 'Write in Markdown...',
      richtext: 'Start writing...',
      email: 'Write your email...',
      csv: 'Enter CSV data...',
      json: 'Enter JSON...',
      yaml: 'Enter YAML...',
      toml: 'Enter TOML...',
      ini: 'Enter INI configuration...',
      html: 'Write HTML...',
      css: 'Write CSS...',
      xml: 'Write XML...',
      svg: 'Write SVG...',
      sql: 'Write SQL...',
    };
    textarea.placeholder = placeholders[lang] || `Write ${lang} code...`;
  }

export function _setDocumentHistoryControlState(undoAvailable, redoAvailable) {
    const undo = document.getElementById('doc-undo-btn');
    const redo = document.getElementById('doc-redo-btn');
    if (undo) {
      undo.disabled = !undoAvailable;
      undo.setAttribute('aria-disabled', undoAvailable ? 'false' : 'true');
    }
    if (redo) {
      redo.disabled = !redoAvailable;
      redo.setAttribute('aria-disabled', redoAvailable ? 'false' : 'true');
    }
  }

export function _syncDocumentHistoryControlsNow() {
    if (_docHistoryStateFrame) cancelAnimationFrame(_docHistoryStateFrame);
    _docHistoryStateFrame = 0;
    const pdfPane = document.getElementById('doc-pdf-view');
    if (pdfPane && pdfPane.style.display !== 'none') {
      _setDocumentHistoryControlState(true, false);
      return;
    }
    let canUndo = false;
    let canRedo = false;
    try {
      canUndo = document.queryCommandEnabled('undo');
      canRedo = document.queryCommandEnabled('redo');
    } catch (_) {}
    _setDocumentHistoryControlState(canUndo, canRedo);
  }

export function _scheduleDocumentHistoryControls() {
    if (_docHistoryStateFrame) return;
    _docHistoryStateFrame = requestAnimationFrame(_syncDocumentHistoryControlsNow);
  }

function _normalizeRichStatsText(value) {
    return String(value ?? '')
      .replace(/\u00a0/gu, ' ')
      .replace(/\n{2,}/gu, '\n')
      .replace(/\n$/u, '');
  }

function _documentStatsSource() {
    const rich = document.getElementById('doc-email-richbody');
    if (rich && rich.style.display !== 'none') {
      const fullText = _normalizeRichStatsText(rich.innerText || rich.textContent || '');
      const selection = window.getSelection?.();
      if (!isMarkdownVisualModeActive() && selection && !selection.isCollapsed && selection.rangeCount) {
        const range = selection.getRangeAt(0);
        if (rich.contains(range.commonAncestorContainer)) {
          return { text: _normalizeRichStatsText(selection.toString()), selected: true };
        }
      }
      return { text: fullText, selected: false };
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return { text: '', selected: false };
    const start = Number(textarea.selectionStart) || 0;
    const end = Number(textarea.selectionEnd) || 0;
    if (end > start) return { text: textarea.value.slice(start, end), selected: true };
    return { text: textarea.value || '', selected: false };
  }

export function _renderDocumentStats() {
    _docStatsFrame = 0;
    const root = document.getElementById('doc-stats');
    if (!root) return;
    const source = _documentStatsSource();
    const stats = getDocumentStats(source.text);
    const count = root.querySelector('#doc-stats-count');
    const unit = root.querySelector('#doc-stats-unit');
    const scope = root.querySelector('#doc-stats-scope');
    if (count) count.textContent = stats.words.toLocaleString();
    if (unit) unit.textContent = source.selected ? ' selected' : ` word${stats.words === 1 ? '' : 's'}`;
    if (scope) scope.textContent = source.selected ? 'Selection' : 'Document';
    const values = {
      'doc-stats-words': stats.words,
      'doc-stats-characters': stats.characters,
      'doc-stats-characters-no-spaces': stats.charactersNoSpaces,
      'doc-stats-lines': stats.lines,
      'doc-stats-reading': stats.readingMinutes ? `${stats.readingMinutes} min` : '0 min',
    };
    for (const [id, value] of Object.entries(values)) {
      const el = root.querySelector(`#${id}`);
      if (el) el.textContent = typeof value === 'number' ? value.toLocaleString() : value;
    }
    const button = root.querySelector('#doc-stats-btn');
    if (button) {
      button.classList.toggle('doc-stats-selected', source.selected);
      button.title = source.selected
        ? `${stats.words.toLocaleString()} words selected`
        : `${stats.words.toLocaleString()} words in document`;
      button.setAttribute('aria-label', button.title);
    }
  }

export function _scheduleDocumentStats() {
    if (_docStatsFrame) cancelAnimationFrame(_docStatsFrame);
    _docStatsFrame = requestAnimationFrame(_renderDocumentStats);
  }

export function _documentOutlineSupported() {
    const language = _activeDocLanguage();
    return language === 'markdown' || _isRichTextLang(language);
  }

export function _documentOutlineEntries() {
    const doc = activeDocId && docs.get(activeDocId);
    if (!doc) return [];
    if (_isRichTextLang(doc.language)) {
      const rich = document.getElementById('doc-email-richbody');
      if (!rich || rich.style.display === 'none') return [];
      return Array.from(rich.querySelectorAll('h1, h2, h3, h4, h5, h6'))
        .map(node => ({
          level: Number(node.tagName.slice(1)) || 1,
          text: (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim(),
          node,
          mode: 'richtext',
        }))
        .filter(entry => entry.text);
    }
    if (doc.language !== 'markdown') return [];
    const textarea = document.getElementById('doc-editor-textarea');
    const entries = parseMarkdownOutline(textarea?.value || doc.content || '');
    const preview = document.getElementById('doc-md-preview');
    const rich = document.getElementById('doc-email-richbody');
    if (preview && preview.style.display !== 'none') {
      const nodes = Array.from(preview.querySelectorAll('h1, h2, h3, h4, h5, h6'));
      entries.forEach((entry, index) => {
        if (nodes[index]) {
          entry.node = nodes[index];
          entry.mode = 'preview';
        }
      });
    } else if (rich && rich.style.display !== 'none' && isMarkdownVisualModeActive()) {
      const nodes = Array.from(rich.querySelectorAll('h1, h2, h3, h4, h5, h6'));
      entries.forEach((entry, index) => {
        if (nodes[index]) {
          entry.node = nodes[index];
          entry.mode = 'visual';
        }
      });
    }
    return entries;
  }

function _flashOutlineTarget(element) {
    if (!element) return;
    element.classList.remove('doc-outline-target');
    void element.offsetWidth;
    element.classList.add('doc-outline-target');
    setTimeout(() => element.classList.remove('doc-outline-target'), 1100);
  }

function _jumpToDocumentOutlineEntry(entry) {
    if (entry.node?.isConnected) {
      if (entry.mode === 'visual') {
        const textarea = document.getElementById('doc-editor-textarea');
        if (textarea && typeof entry.start === 'number' && typeof entry.end === 'number') {
          textarea.setSelectionRange(entry.start, entry.end);
        }
      }
      entry.node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      _flashOutlineTarget(entry.node);
      if (entry.mode === 'richtext' || entry.mode === 'visual') {
        const rich = document.getElementById('doc-email-richbody');
        const selection = window.getSelection?.();
        if (rich && selection) {
          const range = document.createRange();
          range.selectNodeContents(entry.node);
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          try { rich.focus({ preventScroll: true }); } catch (_) { rich.focus(); }
        }
      }
      return;
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
    textarea.setSelectionRange(entry.start, entry.end);
    const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 18;
    const visibleLines = Math.max(3, Math.floor(textarea.clientHeight / lineHeight));
    textarea.scrollTop = Math.max(0, (entry.line - Math.floor(visibleLines / 3)) * lineHeight);
    const pre = document.getElementById('doc-editor-highlight');
    if (pre) pre.scrollTop = textarea.scrollTop;
    _flashOutlineTarget(document.getElementById('doc-editor-wrap'));
  }

function _renderDocumentOutlineMenu() {
    const menu = _docOutlineMenu;
    if (!menu) return;
    const entries = _documentOutlineEntries();
    const count = menu.querySelector('.doc-outline-count');
    const list = menu.querySelector('.doc-outline-list');
    if (count) count.textContent = entries.length ? String(entries.length) : '';
    if (!list) return;
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement('div');
      empty.className = 'doc-outline-empty';
      empty.textContent = 'No headings';
      list.appendChild(empty);
      return;
    }
    entries.forEach(entry => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'doc-outline-item';
      item.style.paddingLeft = `${7 + Math.min(Math.max(0, entry.level - 1), 4) * 11}px`;
      item.title = entry.text;
      item.innerHTML = `<span class="doc-outline-level">H${entry.level}</span><span class="doc-outline-label"></span>`;
      item.querySelector('.doc-outline-label').textContent = entry.text;
      item.addEventListener('click', () => {
        _jumpToDocumentOutlineEntry(entry);
        _docOutlineClose?.();
      });
      list.appendChild(item);
    });
  }

let _outlineFrame = 0;
export function _scheduleDocumentOutline() {
  if (_outlineFrame) return;
  _outlineFrame = requestAnimationFrame(() => {
    _outlineFrame = 0;
    _refreshDocumentOutline();
  });
}

export function _refreshDocumentOutline() {
    const anchor = document.getElementById('doc-outline-toolbar-btn');
    const hasEntries = _documentOutlineSupported() && _documentOutlineEntries().length > 0;
    if (anchor) anchor.style.display = hasEntries ? '' : 'none';
    if (!hasEntries) _closeDocumentOutline();
    if (_docOutlineMenu) _renderDocumentOutlineMenu();
  }

export function _closeDocumentOutline() {
    if (_docOutlineClose) _docOutlineClose();
  }

export function _openDocumentOutline() {
    const anchor = document.getElementById('doc-outline-toolbar-btn');
    if (!anchor || !_documentOutlineSupported()) return;
    if (_docOutlineMenu) {
      _closeDocumentOutline();
      return;
    }

    const menu = document.createElement('div');
    menu.id = 'doc-outline-menu';
    menu.className = 'doc-outline-menu';
    menu.setAttribute('role', 'navigation');
    menu.setAttribute('aria-label', 'Document outline');
    menu.tabIndex = -1;
    menu.innerHTML = `
      <div class="doc-outline-header"><span>Outline</span><span class="doc-outline-count"></span></div>
      <div class="doc-outline-list"></div>
    `;
    document.body.appendChild(menu);
    _docOutlineMenu = menu;
    _renderDocumentOutlineMenu();

    const rect = anchor.getBoundingClientRect();
    const width = Math.min(320, Math.max(240, window.innerWidth - 16));
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const height = Math.min(menu.scrollHeight, Math.max(160, window.innerHeight - 24));
    const below = rect.bottom + 6;
    const top = below + height <= window.innerHeight - 8
      ? below
      : Math.max(8, rect.top - height - 6);
    menu.style.width = `${width}px`;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.zIndex = String(topPortalZ());
    anchor.classList.add('is-active');
    anchor.setAttribute('aria-expanded', 'true');

    const close = bindMenuDismiss(menu, () => {
      menu.remove();
      window.removeEventListener('resize', close);
      window.visualViewport?.removeEventListener('resize', close);
      if (_docOutlineMenu === menu) _docOutlineMenu = null;
      if (_docOutlineClose === close) _docOutlineClose = null;
      anchor.classList.remove('is-active');
      anchor.setAttribute('aria-expanded', 'false');
    }, event => !menu.contains(event.target) && !anchor.contains(event.target));
    _docOutlineClose = close;
    window.addEventListener('resize', close);
    window.visualViewport?.addEventListener('resize', close);
    menu.addEventListener('keydown', event => {
      const items = Array.from(menu.querySelectorAll('.doc-outline-item'));
      if (!items.length) return;
      const current = items.indexOf(document.activeElement);
      let next = current;
      if (event.key === 'ArrowDown') next = Math.min(items.length - 1, current + 1);
      else if (event.key === 'ArrowUp') next = Math.max(0, current < 0 ? 0 : current - 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = items.length - 1;
      else return;
      event.preventDefault();
      items[next]?.focus();
    });
    requestAnimationFrame(() => (menu.querySelector('.doc-outline-item') || menu).focus());
  }

export function _activeDocLanguage() {
    const doc = activeDocId && docs.get(activeDocId);
    return ((doc && doc.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
  }
