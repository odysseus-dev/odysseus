import { topPortalZ, bindMenuDismiss } from '../host.js';
// frontend/src/features/documents/ui/richEditor/slashMenu.js
/**
 * Document Editor Rich-Text Subsystem: slashMenu
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _RICH_BLOCK_INPUT_RULES, _RICH_SLASH_COMMANDS } from '../../domain/richTextRules.js';
import { applyMdFormat } from './formatting.js';
import { _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';
import { _normalizeRichChecklists, _richSelectionChecklistItem, _setRichChecklistItemChecked } from './richOperations.js';

export let _richSlashMenu = null;

let _richSlashClose = null;

let _richSlashOwner = null;

export let _richSlashActiveIndex = 0;

export function setRichSlashActiveIndex(idx) {
  _richSlashActiveIndex = idx;
}

const _RICH_ROOT_BLOCK_BOUNDARY_TAGS = new Set([
  'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'BLOCKQUOTE', 'PRE', 'TABLE', 'HR', 'FIGURE', 'BR',
]);

function _isRichRootBlockBoundary(node) {
  return node?.nodeType === Node.ELEMENT_NODE && _RICH_ROOT_BLOCK_BOUNDARY_TAGS.has(node.tagName);
}

export function _applyRichBlockInputRule(rich) {
    const selection = window.getSelection?.();
    if (!selection?.rangeCount || !selection.isCollapsed) return false;
    const caretRange = selection.getRangeAt(0);
    if (!rich.contains(caretRange.commonAncestorContainer)) return false;
    const node = caretRange.startContainer;
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!element) return false;
    const listItem = element.closest('li');
    if (listItem && rich.contains(listItem) && listItem.parentElement?.tagName === 'UL') {
      const markerRange = document.createRange();
      try {
        markerRange.selectNodeContents(listItem);
        markerRange.setEnd(caretRange.startContainer, caretRange.startOffset);
      } catch (_) {
        return false;
      }
      const marker = markerRange.toString();
      if (marker !== '[]' && marker !== '[ ]' && marker !== '[x]' && marker !== '[X]') return false;
      selection.removeAllRanges();
      selection.addRange(markerRange);
      document.execCommand('delete');
      listItem.parentElement.classList.add('rich-checklist');
      _normalizeRichChecklists(listItem.parentElement);
      const checked = marker === '[x]' || marker === '[X]';
      listItem.dataset.checked = checked ? 'true' : 'false';
      listItem.setAttribute('aria-checked', checked ? 'true' : 'false');
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      rich._syncActive?.();
      return true;
    }
    if (element.closest('pre, blockquote, td, th')) return false;
    const block = element.closest('p, div');
    if (!block || (block !== rich && !rich.contains(block))) return false;
    // Text typed straight into an empty editor has no wrapping block. Only Markdown
    // Visual applies input rules there; other editors never did.
    if (block === rich && !rich.classList.contains('markdown-visual-mode')) return false;
    const markerRange = document.createRange();
    try {
      if (block === rich) {
        // Text typed into an empty editor has no block wrapper yet. The marker
        // then starts at the inline run, after the last block boundary, that
        // holds the caret.
        let top = caretRange.startContainer;
        if (top === rich) return false;
        while (top.parentNode !== rich) top = top.parentNode;
        let first = top;
        while (first.previousSibling && !_isRichRootBlockBoundary(first.previousSibling)) first = first.previousSibling;
        markerRange.setStartBefore(first);
      } else {
        markerRange.selectNodeContents(block);
      }
      markerRange.setEnd(caretRange.startContainer, caretRange.startOffset);
    } catch (_) {
      return false;
    }
    const rule = _RICH_BLOCK_INPUT_RULES.get(markerRange.toString());
    if (!rule) return false;
    selection.removeAllRanges();
    selection.addRange(markerRange);
    document.execCommand('delete');
    applyMdFormat(rule.action);
    if (rule.checked) {
      const item = _richSelectionChecklistItem(rich);
      if (item) _setRichChecklistItemChecked(rich, item, true);
    }
    return true;
  }

export function _richSlashContext(rich) {
    const selection = window.getSelection?.();
    if (!selection?.rangeCount || !selection.isCollapsed) return null;
    const caretRange = selection.getRangeAt(0);
    if (!rich.contains(caretRange.commonAncestorContainer)) return null;
    const node = caretRange.startContainer;
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    const block = element?.closest?.('p, div, h1, h2, h3, h4, h5, h6, blockquote, li, pre') || rich;
    if (block !== rich && !rich.contains(block)) return null;
    const queryRange = document.createRange();
    try {
      queryRange.selectNodeContents(block);
      queryRange.setEnd(caretRange.startContainer, caretRange.startOffset);
    } catch (_) {
      return null;
    }
    const match = queryRange.toString().match(/^\/([^\s/]*)$/);
    if (!match) return null;
    return { block, caretRange: caretRange.cloneRange(), queryRange, query: match[1].toLowerCase() };
  }

function _richSlashCommands(query, rich) {
    const doc = activeDocId && docs.get(activeDocId);
    return _RICH_SLASH_COMMANDS.filter(command => {
      if (doc?.language === 'email' && command.action === 'check') return false;
      if (doc?.language === 'markdown' && command.action === 'pagebreak') return false;
      if (!query) return true;
      return `${command.label} ${command.keywords}`.toLowerCase().includes(query);
    });
  }

export function _hideRichSlashMenu() {
    if (_richSlashClose) {
      _richSlashClose();
      return;
    }
    _richSlashMenu?.remove();
    _richSlashMenu = null;
    if (_richSlashOwner) {
      _richSlashOwner.removeAttribute('aria-controls');
      _richSlashOwner.removeAttribute('aria-expanded');
      _richSlashOwner.removeAttribute('aria-activedescendant');
      _richSlashOwner.removeAttribute('aria-haspopup');
      _richSlashOwner = null;
    }
    _richSlashActiveIndex = 0;
  }

function _syncRichSlashActiveOption(menu, rich, { reveal = false } = {}) {
    const items = Array.from(menu.querySelectorAll('.doc-rich-slash-item'));
    items.forEach((item, index) => {
      const active = index === _richSlashActiveIndex;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    const activeItem = items[_richSlashActiveIndex];
    if (activeItem) rich.setAttribute('aria-activedescendant', activeItem.id);
    else rich.removeAttribute('aria-activedescendant');
    if (!reveal || !activeItem) return;
    const list = menu.querySelector('.doc-rich-slash-list');
    const itemTop = activeItem.offsetTop - list.offsetTop;
    const itemBottom = itemTop + activeItem.offsetHeight;
    if (itemTop < list.scrollTop) list.scrollTop = itemTop;
    else if (itemBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = itemBottom - list.clientHeight;
    }
  }

export function _renderRichSlashMenu(rich, context) {
    const menu = _richSlashMenu;
    if (!menu) return;
    const commands = _richSlashCommands(context.query, rich);
    _richSlashActiveIndex = Math.max(0, Math.min(_richSlashActiveIndex, commands.length - 1));
    menu._commands = commands;
    const list = menu.querySelector('.doc-rich-slash-list');
    list.replaceChildren();
    if (!commands.length) {
      const empty = document.createElement('div');
      empty.className = 'doc-rich-slash-empty';
      empty.textContent = 'No commands';
      list.appendChild(empty);
    } else {
      commands.forEach((command, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'doc-rich-slash-item';
        button.classList.toggle('is-active', index === _richSlashActiveIndex);
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', index === _richSlashActiveIndex ? 'true' : 'false');
        button.id = `doc-rich-slash-option-${index}`;
        button.dataset.slashIndex = String(index);
        button.innerHTML = '<span class="doc-rich-slash-icon"></span><span class="doc-rich-slash-label"></span>';
        button.querySelector('.doc-rich-slash-icon').textContent = command.icon;
        button.querySelector('.doc-rich-slash-label').textContent = command.label;
        button.addEventListener('pointerenter', () => {
          _richSlashActiveIndex = index;
          _syncRichSlashActiveOption(menu, rich);
        });
        list.appendChild(button);
      });
    }
    const count = menu.querySelector('.doc-rich-slash-count');
    if (count) count.textContent = commands.length ? String(commands.length) : '';

    const caretRect = context.queryRange.getBoundingClientRect();
    const width = Math.min(280, window.innerWidth - 16);
    menu.style.width = `${width}px`;
    menu.style.zIndex = String(topPortalZ());
    menu.style.visibility = 'hidden';
    const height = Math.min(menu.scrollHeight, Math.max(160, window.innerHeight - 24));
    const left = Math.max(8, Math.min(caretRect.left, window.innerWidth - width - 8));
    const below = caretRect.bottom + 7;
    const preferredTop = below + height <= window.innerHeight - 8
      ? below
      : Math.max(8, caretRect.top - height - 7);
    const top = Math.max(8, Math.min(preferredTop, window.innerHeight - height - 8));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.visibility = '';
    _syncRichSlashActiveOption(menu, rich, { reveal: true });
  }

export function _chooseRichSlashCommand(rich, index = _richSlashActiveIndex) {
    const context = _richSlashContext(rich);
    const commands = context ? _richSlashCommands(context.query, rich) : [];
    const command = commands[index];
    if (!context || !command) return;
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(context.queryRange);
    rich.focus({ preventScroll: true });
    document.execCommand('delete');
    _hideRichSlashMenu();
    if (command.action === 'image') {
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      document.getElementById('doc-md-image-input')?.click();
      return;
    }
    applyMdFormat(command.action);
  }

export function _syncRichSlashMenu(rich) {
    const context = _richSlashContext(rich);
    if (!context) {
      _hideRichSlashMenu();
      return;
    }
    if (!_richSlashMenu) {
      const menu = document.createElement('div');
      menu.id = 'doc-rich-slash-menu';
      menu.className = 'doc-rich-slash-menu';
      menu.setAttribute('role', 'listbox');
      menu.setAttribute('aria-label', 'Insert block');
      menu.innerHTML = '<div class="doc-rich-slash-header"><span>Insert</span><span class="doc-rich-slash-count"></span></div><div class="doc-rich-slash-list"></div>';
      menu.addEventListener('pointerdown', event => event.preventDefault());
      menu.addEventListener('mousedown', event => event.preventDefault());
      menu.addEventListener('click', event => {
        const item = event.target.closest('[data-slash-index]');
        if (!item) return;
        event.preventDefault();
        event.stopPropagation();
        _chooseRichSlashCommand(rich, Number(item.dataset.slashIndex));
      });
      document.body.appendChild(menu);
      _richSlashMenu = menu;
      _richSlashOwner = rich;
      rich.setAttribute('aria-controls', menu.id);
      rich.setAttribute('aria-expanded', 'true');
      rich.setAttribute('aria-haspopup', 'listbox');
      const close = bindMenuDismiss(menu, () => {
        menu.remove();
        if (_richSlashMenu === menu) _richSlashMenu = null;
        if (_richSlashClose === close) _richSlashClose = null;
        if (_richSlashOwner === rich) {
          rich.removeAttribute('aria-controls');
          rich.removeAttribute('aria-expanded');
          rich.removeAttribute('aria-activedescendant');
          rich.removeAttribute('aria-haspopup');
          _richSlashOwner = null;
        }
        _richSlashActiveIndex = 0;
      });
      _richSlashClose = close;
    }
    _renderRichSlashMenu(rich, context);
  }
