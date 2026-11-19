import { _scheduleDocumentHistoryControls } from '../status/documentStatus.js';
import { markdownModule } from '../host.js';
// frontend/src/features/documents/ui/richEditor/richOperations.js
/**
 * Document Editor Rich-Text Subsystem: richOperations
 */

import { _isRichInlineCodeMarker, _normalizeRichLinkUrl } from '../../domain/richTextRules.js';
import { _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';

function _richSelectionElement(rich) {
    const selection = window.getSelection();
    if (!selection?.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return null;
    const node = range.startContainer;
    return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  }

function _richSelectionListItem(rich) {
    const item = _richSelectionElement(rich)?.closest?.('li');
    return item && rich.contains(item) ? item : null;
  }

export function _richSelectionChecklistItem(rich) {
    const item = _richSelectionListItem(rich);
    return item?.parentElement?.classList.contains('rich-checklist') ? item : null;
  }

function _richChecklistItems(list) {
    return Array.from(list?.children || []).filter(child => child.tagName === 'LI');
  }

export function _normalizeRichChecklists(root) {
    const lists = root.matches?.('ul.rich-checklist')
      ? [root, ...root.querySelectorAll('ul.rich-checklist')]
      : Array.from(root.querySelectorAll('ul.rich-checklist'));
    lists.forEach(list => {
      _richChecklistItems(list).forEach(item => {
        const checked = item.dataset.checked === 'true';
        item.dataset.checked = checked ? 'true' : 'false';
        item.setAttribute('aria-checked', checked ? 'true' : 'false');
      });
    });
  }

function _richTextOffsetWithin(root) {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !root.contains(selection.anchorNode)) return null;
    const before = document.createRange();
    before.selectNodeContents(root);
    before.setEnd(selection.anchorNode, selection.anchorOffset);
    return before.toString().length;
  }

const _richSpacingBlockSelector = 'h1,h2,h3,h4,h5,h6,p,div,pre,blockquote,li,td,th';

function _richSelectionTextOffsets(root) {
    const selection = window.getSelection();
    if (!selection?.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
    const prefix = document.createRange();
    prefix.selectNodeContents(root);
    prefix.setEnd(range.startContainer, range.startOffset);
    const throughSelection = document.createRange();
    throughSelection.selectNodeContents(root);
    throughSelection.setEnd(range.endContainer, range.endOffset);
    return { start: prefix.toString().length, end: throughSelection.toString().length };
  }

function _richTextPointAtOffset(root, requestedOffset) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let remaining = Math.max(0, requestedOffset);
    let node;
    let last = null;
    while ((node = walker.nextNode())) {
      last = node;
      const length = (node.nodeValue || '').length;
      if (remaining <= length) return { node, offset: remaining };
      remaining -= length;
    }
    if (last) return { node: last, offset: (last.nodeValue || '').length };
    return { node: root, offset: 0 };
  }

function _restoreRichSelectionTextOffsets(root, offsets) {
    if (!offsets) return;
    const start = _richTextPointAtOffset(root, offsets.start);
    const end = _richTextPointAtOffset(root, offsets.end);
    const range = document.createRange();
    range.setStart(start.node, start.offset);
    range.setEnd(end.node, end.offset);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

export function _richSpacingBlocks(root) {
    return Array.from(root.querySelectorAll(_richSpacingBlockSelector));
  }

function _richClosestSpacingBlock(root, node) {
    let element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    if (!element || element === root) return null;
    const block = element.matches?.(_richSpacingBlockSelector)
      ? element
      : element.closest?.(_richSpacingBlockSelector);
    return block && root.contains(block) ? block : null;
  }

export function _richSelectedSpacingBlockIndexes(root) {
    const selection = window.getSelection();
    if (!selection?.rangeCount) return [];
    const range = selection.getRangeAt(0);
    if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return [];
    const blocks = _richSpacingBlocks(root);
    let startNode = range.startContainer;
    if (startNode === root && root.childNodes.length) {
      startNode = root.childNodes[Math.min(range.startOffset, root.childNodes.length - 1)];
    }
    if (range.collapsed) {
      const block = _richClosestSpacingBlock(root, startNode);
      const index = blocks.indexOf(block);
      return index >= 0 ? [index] : [];
    }

    const selected = new Set();
    const addBlockForNode = node => {
      const block = _richClosestSpacingBlock(root, node);
      const index = blocks.indexOf(block);
      if (index >= 0) selected.add(index);
    };
    addBlockForNode(startNode);
    addBlockForNode(range.endContainer);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let textNode;
    while ((textNode = walker.nextNode())) {
      try { if (range.intersectsNode(textNode)) addBlockForNode(textNode); } catch (_) {}
    }
    return [...selected].sort((a, b) => a - b);
  }

export function _applyRichLineSpacing(rich, value) {
    const indexes = _richSelectedSpacingBlockIndexes(rich);
    const offsets = _richSelectionTextOffsets(rich);
    if (!indexes.length || !offsets) return false;
    const clone = rich.cloneNode(true);
    const blocks = _richSpacingBlocks(clone);
    indexes.forEach(index => {
      const block = blocks[index];
      if (!block) return;
      if (value === 'normal') block.style.removeProperty('line-height');
      else block.style.lineHeight = value;
      if (!block.getAttribute('style')) block.removeAttribute('style');
    });
    clone.querySelectorAll('[data-editor-image-selected]').forEach(image => {
      image.removeAttribute('data-editor-image-selected');
    });

    const wholeDocument = document.createRange();
    wholeDocument.selectNodeContents(rich);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(wholeDocument);
    rich.focus();
    if (!document.execCommand('insertHTML', false, clone.innerHTML)) return false;
    _restoreRichSelectionTextOffsets(rich, offsets);
    return true;
  }

function _focusRichTextOffset(rich, root, textOffset = null) {
    if (!root) return;
    rich.focus();
    const range = document.createRange();
    if (!Number.isFinite(textOffset)) {
      range.selectNodeContents(root);
      range.collapse(false);
    } else {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let remaining = Math.max(0, textOffset);
      let textNode = walker.nextNode();
      while (textNode && remaining > textNode.data.length) {
        remaining -= textNode.data.length;
        textNode = walker.nextNode();
      }
      if (textNode) range.setStart(textNode, Math.min(remaining, textNode.data.length));
      else {
        range.selectNodeContents(root);
        range.collapse(false);
      }
      range.collapse(true);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

function _replaceRichChecklist(rich, original, replacement, targetIndex = 0, textOffset = null) {
    const token = `doc-checklist-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    replacement.dataset.editorChecklistToken = token;
    const range = document.createRange();
    range.selectNode(original);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertHTML', false, replacement.outerHTML);
    const inserted = rich.querySelector(`[data-editor-checklist-token="${token}"]`);
    if (!inserted) return;
    inserted.removeAttribute('data-editor-checklist-token');
    _normalizeRichChecklists(inserted);
    const items = _richChecklistItems(inserted);
    _focusRichTextOffset(rich, items[Math.max(0, Math.min(targetIndex, items.length - 1))], textOffset);
  }

export function _setRichChecklistItemChecked(rich, item, checked) {
    const list = item.parentElement;
    const items = _richChecklistItems(list);
    const itemIndex = items.indexOf(item);
    const textOffset = _richTextOffsetWithin(item);
    const clone = list.cloneNode(true);
    const cloneItem = _richChecklistItems(clone)[itemIndex];
    if (!cloneItem) return;
    cloneItem.dataset.checked = checked ? 'true' : 'false';
    cloneItem.setAttribute('aria-checked', checked ? 'true' : 'false');
    _replaceRichChecklist(rich, list, clone, itemIndex, textOffset);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
    rich._syncActive?.();
  }

export function _toggleRichChecklist(rich) {
    const currentItem = _richSelectionListItem(rich);
    const currentList = currentItem?.parentElement;
    const itemIndex = currentList ? _richChecklistItems(currentList).indexOf(currentItem) : 0;
    const textOffset = currentItem ? _richTextOffsetWithin(currentItem) : null;

    if (currentList?.classList.contains('rich-checklist')) {
      const clone = currentList.cloneNode(true);
      clone.classList.remove('rich-checklist');
      _richChecklistItems(clone).forEach(item => {
        item.removeAttribute('data-checked');
        item.removeAttribute('aria-checked');
      });
      _replaceRichChecklist(rich, currentList, clone, itemIndex, textOffset);
      return true;
    }

    if (currentList && (currentList.tagName === 'UL' || currentList.tagName === 'OL')) {
      const clone = document.createElement('ul');
      clone.className = 'rich-checklist';
      _richChecklistItems(currentList).forEach(item => clone.appendChild(item.cloneNode(true)));
      _normalizeRichChecklists(clone);
      _replaceRichChecklist(rich, currentList, clone, itemIndex, textOffset);
      return true;
    }

    document.execCommand('insertUnorderedList');
    const insertedItem = _richSelectionListItem(rich);
    const insertedList = insertedItem?.parentElement;
    if (!insertedList) return false;
    insertedList.classList.add('rich-checklist');
    _normalizeRichChecklists(insertedList);
    return true;
  }

export function _handleRichChecklistEnter(rich) {
    const item = _richSelectionChecklistItem(rich);
    if (!item) return false;
    const hasContent = !!item.textContent.trim() || !!item.querySelector('img, table, hr');
    if (hasContent) {
      document.execCommand('insertParagraph');
      const nextItem = _richSelectionChecklistItem(rich);
      if (nextItem) {
        nextItem.dataset.checked = 'false';
        nextItem.setAttribute('aria-checked', 'false');
      }
    } else {
      document.execCommand('outdent');
      document.execCommand('removeFormat');
      document.execCommand('formatBlock', false, 'p');
    }
    _normalizeRichChecklists(rich);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
    rich._syncActive?.();
    return true;
  }

export function _handleRichHeadingEnter(rich) {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !selection.isCollapsed) return false;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return false;
    const element = range.startContainer.nodeType === Node.ELEMENT_NODE
      ? range.startContainer
      : range.startContainer.parentElement;
    const heading = element?.closest?.('h1, h2, h3, h4, h5, h6');
    if (!heading || !rich.contains(heading)) return false;

    const afterRange = document.createRange();
    afterRange.selectNodeContents(heading);
    afterRange.setStart(range.startContainer, range.startOffset);
    const after = afterRange.cloneContents();
    const headingHasContent = !!heading.textContent?.trim() || !!heading.querySelector('img, hr');
    const caretAtEnd = !after.textContent?.trim() && !after.querySelector?.('img, hr');
    if (headingHasContent && !caretAtEnd) return false;

    let applied = false;
    if (!headingHasContent) {
      applied = document.execCommand('formatBlock', false, 'p');
    } else {
      document.execCommand('defaultParagraphSeparator', false, 'p');
      applied = document.execCommand('insertParagraph');
    }
    if (!applied) return false;
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
    _scheduleDocumentHistoryControls();
    rich._syncActive?.();
    return true;
  }

export let _richInlineCodeTypingArmed = false;

export function setRichInlineCodeTypingArmed(val) {
  _richInlineCodeTypingArmed = val;
}

export function _richSelectionInlineCode(rich) {
    const candidate = _richSelectionElement(rich)?.closest?.('code, span');
    if (!candidate || !rich.contains(candidate) || candidate.closest('pre')) return null;
    if (candidate.tagName === 'CODE' || _isRichInlineCodeMarker(candidate)) return candidate;
    return null;
  }

export function _normalizeRichInlineCode(root) {
    root.querySelectorAll('code, span').forEach(code => {
      if (code.closest('pre') || (code.tagName !== 'CODE' && !_isRichInlineCodeMarker(code))) return;
      code.removeAttribute('data-editor-inline-code-token');
    });
  }

export function _toggleRichInlineCode(rich) {
    if (_richInlineCodeTypingArmed) {
      document.execCommand('removeFormat');
      document.execCommand('styleWithCSS', false, false);
      _richInlineCodeTypingArmed = false;
      return true;
    }
    const existing = _richSelectionInlineCode(rich);
    if (existing) {
      const range = document.createRange();
      range.selectNodeContents(existing);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand('removeFormat');
      return true;
    }

    const selection = window.getSelection();
    if (!selection?.rangeCount) return false;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return false;
    if (range.collapsed) {
      document.execCommand('styleWithCSS', false, true);
      document.execCommand('fontName', false, 'OdysseusInlineCode, ui-monospace, monospace');
      document.execCommand('backColor', false, 'rgba(127, 127, 127, 0.12)');
      _richInlineCodeTypingArmed = true;
      return true;
    }
    const code = document.createElement('span');
    code.style.fontFamily = 'OdysseusInlineCode, ui-monospace, monospace';
    code.style.backgroundColor = 'rgba(127, 127, 127, 0.12)';
    const selectedText = range.toString();
    code.textContent = selectedText;
    document.execCommand('insertHTML', false, code.outerHTML);
    const inserted = _richSelectionInlineCode(rich);
    if (!inserted) return false;
    const caret = document.createRange();
    caret.selectNodeContents(inserted);
    selection.removeAllRanges();
    selection.addRange(caret);
    return true;
  }

function _smartRichPasteUrl(rawText) {
    const candidate = String(rawText || '').trim();
    if (!candidate || /\s/.test(candidate)) return '';
    let normalized = '';
    if (/^[^@/:\s]+@[^@/:\s]+\.[^@/:\s]+$/.test(candidate)) {
      normalized = _normalizeRichLinkUrl(`mailto:${candidate}`);
    } else if (/^mailto:/i.test(candidate)) {
      normalized = /^mailto:[^@\s]+@[^@\s]+\.[^@\s]+$/i.test(candidate)
        ? _normalizeRichLinkUrl(candidate)
        : '';
    } else if (/^tel:/i.test(candidate)) {
      normalized = /^tel:\+?[\d().-]+$/i.test(candidate) ? _normalizeRichLinkUrl(candidate) : '';
    } else {
      const absoluteWebUrl = /^(?:https?:)?\/\/[^/\s]+(?:\/[^\s]*)?$/i.test(candidate);
      const bareDomain = /^(?:www\.)?[^./:\s]+\.[^/\s]+(?:\/[^\s]*)?$/i.test(candidate);
      if (!absoluteWebUrl && !bareDomain) return '';
      normalized = _normalizeRichLinkUrl(candidate);
    }
    if (!normalized) return '';
    try {
      const parsed = new URL(normalized);
      if (parsed.protocol === 'mailto:' || parsed.protocol === 'tel:') {
        return parsed.href;
      }
      if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.hostname) {
        return parsed.href;
      }
      return '';
    } catch (_) {
      return '';
    }
  }

export function _insertSmartRichPasteLink(rich, rawText) {
    const url = _smartRichPasteUrl(rawText);
    if (!url) return false;
    let safeHref = '';
    if (url.startsWith('https://')) safeHref = `https://${url.slice('https://'.length)}`;
    else if (url.startsWith('http://')) safeHref = `http://${url.slice('http://'.length)}`;
    else if (url.startsWith('mailto:')) safeHref = `mailto:${url.slice('mailto:'.length)}`;
    else if (url.startsWith('tel:')) safeHref = `tel:${url.slice('tel:'.length)}`;
    if (!safeHref) return false;
    const selection = window.getSelection();
    if (!selection?.rangeCount) return false;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return false;
    if (!range.collapsed) {
      const selectionBlock = node => {
        const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
        return element?.closest?.('p, div, h1, h2, h3, h4, h5, h6, li, blockquote, pre, td, th');
      };
      if (selectionBlock(range.startContainer) !== selectionBlock(range.endContainer)) return false;
    }

    const anchor = document.createElement('a');
    anchor.setAttribute('href', safeHref);
    anchor.setAttribute('rel', 'noopener noreferrer');
    if (range.collapsed) {
      anchor.textContent = String(rawText || '').trim();
    } else {
      const contents = range.cloneContents();
      contents.querySelectorAll?.('a').forEach(link => link.replaceWith(...Array.from(link.childNodes)));
      anchor.appendChild(contents);
    }
    const token = `doc-link-paste-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    anchor.dataset.editorLinkToken = token;
    if (!document.execCommand('insertHTML', false, anchor.outerHTML)) return false;
    const inserted = rich.querySelector(`[data-editor-link-token="${token}"]`);
    if (!inserted) return false;
    inserted.removeAttribute('data-editor-link-token');
    const after = document.createRange();
    after.setStartAfter(inserted);
    after.collapse(true);
    selection.removeAllRanges();
    selection.addRange(after);
    return true;
  }

export function _cleanRichTextPasteHtml(rawHtml) {
    const rawText = String(rawHtml || '');
    if (!rawText) return '';
    if (typeof markdownModule?.sanitizeAllowedHtml !== 'function' || typeof DOMParser === 'undefined') {
      return rawText.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }
    const sanitizedResult = markdownModule.sanitizeAllowedHtml(rawText);
    const sanitized = typeof sanitizedResult === 'string' ? sanitizedResult : '';
    if (!sanitized) return '';
    const root = new DOMParser().parseFromString(sanitized, 'text/html').body;
    if (!root) return '';
    const allowedTags = new Set([
      'A', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'DEL', 'SPAN',
      'P', 'DIV', 'BR', 'HR', 'UL', 'OL', 'LI', 'H1', 'H2', 'H3', 'H4',
      'BLOCKQUOTE', 'CODE', 'PRE', 'SUP', 'SUB',
      'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TH', 'TD',
    ]);
    const blockedTags = new Set([
      'SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH',
      'FORM', 'BASE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE',
    ]);

    // Keep useful document structure but discard presentation copied from the
    // source page. Editor-applied formatting is unaffected; this only cleans
    // newly pasted HTML before it enters the browser's undo stack.
    root.querySelectorAll('img, video, audio, canvas').forEach(el => el.remove());
    Array.from(root.querySelectorAll('*')).forEach(el => {
      if (blockedTags.has(el.tagName.toUpperCase())) el.remove();
    });
    root.querySelectorAll('*').forEach(el => {
      const tag = el.tagName.toUpperCase();
      if (!allowedTags.has(tag)) {
        el.replaceWith(...Array.from(el.childNodes));
        return;
      }
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        const keepLink = tag === 'A' && name === 'href';
        const keepTableSpan = (tag === 'TD' || tag === 'TH')
          && (name === 'colspan' || name === 'rowspan');
        const keepChecklistClass = tag === 'UL' && name === 'class'
          && el.classList.contains('rich-checklist');
        const keepChecklistState = tag === 'LI'
          && (name === 'data-checked' || name === 'aria-checked')
          && el.parentElement?.classList.contains('rich-checklist');
        if (!keepLink && !keepTableSpan && !keepChecklistClass && !keepChecklistState) {
          el.removeAttribute(attr.name);
        }
      }
      if (tag === 'A') {
        const safeUrl = _normalizeRichLinkUrl(el.getAttribute('href'));
        if (!safeUrl) el.replaceWith(...Array.from(el.childNodes));
        else {
          el.setAttribute('href', safeUrl);
          el.setAttribute('rel', 'noopener noreferrer');
        }
      }
    });
    return root.innerHTML;
  }

export let _activeRichImage = null;

export function setActiveRichImage(image) {
  _activeRichImage = image;
}

export function _selectedRichImage(rich) {
    if (_activeRichImage?.isConnected && rich.contains(_activeRichImage)) {
      return _activeRichImage;
    }
    const element = _richSelectionElement(rich);
    const image = element?.closest?.('img.richtext-image, figure.richtext-image img');
    return image && rich.contains(image) ? image : null;
  }
