import { _applyRichTableAction } from '../richTables.js';
import { _scheduleDocumentHistoryControls } from '../status/documentStatus.js';
import { _replaceRange } from '../editor/sourceEditor.js';
// frontend/src/features/documents/ui/richEditor/formatting.js
/**
 * Document Editor Rich-Text Subsystem: formatting
 */

import { _RICH_FONT_SIZE_PX } from '../../domain/richTextRules.js';
import { _applyRichImageAction, _editRichImageAlt, _editRichImageCaption, _removeRichLink, _richLinkAtRange, _wysiwygInsertLink } from './links.js';
import { _emailRichbodyActive, _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';
import { _applyRichLineSpacing, _toggleRichChecklist, _toggleRichInlineCode } from './richOperations.js';
import { isMarkdownVisualModeActive } from './markdownVisualSync.js';

function _isMarkdownVisualUnsupportedAction(action) {
  if (!action) return false;
  if (
    action === 'underline'
    || action === 'superscript'
    || action === 'subscript'
    || action === 'alignleft'
    || action === 'aligncenter'
    || action === 'alignright'
    || action === 'alignjustify'
    || action === 'pagebreak'
    || action === 'image:alt'
    || action === 'image:caption'
  ) {
    return true;
  }
  return (
    action.startsWith('fontname:')
    || action.startsWith('fontsize:')
    || action.startsWith('forecolor:')
    || action.startsWith('highlight:')
    || action.startsWith('linespacing:')
    || action.startsWith('image:size:')
    || action.startsWith('image:align:')
    || action === 'table:merge-right'
    || action === 'table:merge-down'
    || action === 'table:split-cell'
    || action.startsWith('table:cell-align:')
    || action === 'table:toggle-header-column'
  );
}

export function _richSelectionHasFormatting(rich, range) {
    if (!rich || !range) return false;
    const formattedTags = /^(b|i|u|s|strong|em|del|strike|a|font|h[1-6]|blockquote|pre|li)$/i;
    const hasFormattedAncestor = (node) => {
      for (let current = node?.nodeType === Node.TEXT_NODE ? node.parentElement : node;
           current && current !== rich;
           current = current.parentElement) {
        if (formattedTags.test(current.tagName || '') ||
            (current.tagName === 'SPAN' && current.hasAttribute('style'))) return true;
      }
      return false;
    };
    if (hasFormattedAncestor(range.startContainer) || hasFormattedAncestor(range.endContainer)) return true;
    const fragment = range.cloneContents();
    return !!fragment.querySelector?.('b,i,u,s,strong,em,del,strike,a,font,h1,h2,h3,h4,h5,h6,blockquote,pre,li,span[style]');
  }

function _clearRichSelectionFormatting(rich) {
    const selection = window.getSelection?.();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!rich || !range || range.collapsed || !rich.contains(range.commonAncestorContainer)) return false;
    const plainText = range.toString();
    if (!plainText) return false;
    rich.focus({ preventScroll: true });
    if (!document.execCommand('insertText', false, plainText)) return false;
    return true;
  }

let _lastMdFormat = { action: null, t: 0 };

let _savedFormatTextareaSelection = null;

let _savedFormatRichRange = null;

function _insertRichPageBreak(rich) {
    const token = `doc-page-break-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const html = `<hr class="richtext-page-break" data-editor-page-break-token="${token}"><p><br></p>`;
    if (!document.execCommand('insertHTML', false, html)) return false;
    const pageBreak = rich.querySelector(`[data-editor-page-break-token="${token}"]`);
    if (!pageBreak) return false;
    pageBreak.removeAttribute('data-editor-page-break-token');
    pageBreak.setAttribute('title', 'Page break');
    const next = pageBreak.nextElementSibling;
    const range = document.createRange();
    if (next) {
      range.selectNodeContents(next);
      range.collapse(true);
    } else {
      range.setStartAfter(pageBreak);
      range.collapse(true);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

function _applyRichPixelFontSize(rich, pixels) {
    const size = Math.max(8, Math.min(96, Math.round(Number(pixels) || 16)));
    document.execCommand('styleWithCSS', false, true);
    document.execCommand('fontSize', false, '7');
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !rich.contains(range.commonAncestorContainer)) return;
    rich.querySelectorAll('font[size="7"]').forEach(font => {
      try {
        if (!range.intersectsNode(font)) return;
        const span = document.createElement('span');
        span.style.fontSize = `${size}px`;
        span.append(...Array.from(font.childNodes));
        font.replaceWith(span);
      } catch (_) {}
    });
  }

function _applyRichFontFamily(rich, family) {
    const name = String(family || '').trim();
    if (!rich || !name) return;
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !rich.contains(range.commonAncestorContainer)) return;

    // For a collapsed caret, execCommand is still needed to set the browser's
    // typing style. An empty span looks correct in HTML but the caret can sit
    // just outside it, causing the next characters to fall back to the body
    // font.
    if (range.collapsed) {
      document.execCommand('styleWithCSS', false, true);
      document.execCommand('fontName', false, name);
      return;
    }

    // Applying fontName through execCommand is browser-dependent: it can
    // return true while leaving a collapsed/cross-node selection unchanged.
    // Wrap the live range directly so the visual result and saved HTML agree.
    const span = document.createElement('span');
    span.style.fontFamily = name;
    span.appendChild(range.extractContents());
    range.insertNode(span);
    range.selectNodeContents(span);
    selection.removeAllRanges();
    selection.addRange(range);
  }

export function applyMdFormat(action, { bypassDuplicateGuard = false } = {}) {
    // Guard against a duplicate/"ghost" click firing the same toggle twice in
    // quick succession — that would wrap then immediately unwrap, so the
    // markers appear for a split second and vanish. Keyboard shortcuts pass
    // bypassDuplicateGuard for deliberate (non-repeat) keypresses: two quick
    // Ctrl+B presses are an intended on/off toggle, not a ghost click.
    const _now = Date.now();
    // Keep this short: a longer window swallows legitimate repeated use of
    // the same color on separate selections.
    const isPaletteAction = action.startsWith('highlight:') || action.startsWith('forecolor:');
    if (!bypassDuplicateGuard) {
      if (!isPaletteAction && _lastMdFormat.action === action && _now - _lastMdFormat.t < 120) return;
      // Only pointer-originated calls arm the guard: a keypress followed by a
      // toolbar click is two deliberate actions, never one ghost click.
      _lastMdFormat = { action, t: _now };
    }
    // Email WYSIWYG: format the live rich text via execCommand instead of
    // inserting markdown markers into the (hidden) source textarea.
    const _rich = _emailRichbodyActive();
    if (_rich) {
      const mdVisual = isMarkdownVisualModeActive();
      if (mdVisual && _isMarkdownVisualUnsupportedAction(action)) return;
      if (mdVisual && (action === 'indent' || action === 'outdent')) {
        const anchor = window.getSelection?.()?.anchorNode;
        const el = anchor?.nodeType === Node.ELEMENT_NODE ? anchor : anchor?.parentElement;
        if (!el?.closest?.('li')) return;
      }
      let _richFormatRange = null;
      const _selection = window.getSelection?.();
      const _range = _savedFormatRichRange || (_selection?.rangeCount ? _selection.getRangeAt(0) : null);
      if (_range && !_range.collapsed && _rich.contains(_range.commonAncestorContainer)) {
        // Toolbar clicks can move focus away from the contenteditable and
        // collapse the browser range before execCommand runs. Preserve every
        // formatting selection, not only color-palette selections.
        _richFormatRange = _range.cloneRange();
      }
      _savedFormatRichRange = null;
      // Markdown Visual keeps the caret where it is; other editors refocus as before.
      if (!mdVisual || document.activeElement !== _rich) {
        _rich.focus();
      }
      if (_richFormatRange && _selection) {
        try {
          _selection.removeAllRanges();
          _selection.addRange(_richFormatRange.cloneRange());
        } catch (_) {}
      }
      // Link needs an async styled URL prompt — handle it separately so we can
      // save/restore the selection (opening the modal collapses it otherwise).
      if (action === 'link') {
        const selection = window.getSelection();
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        const existingLink = _richLinkAtRange(_rich, range);
        if (existingLink) {
          if (!_removeRichLink(_rich, existingLink)) return;
          _syncEmailRichbody(_rich);
          _scheduleEmailRichbodySave();
          _rich._syncActive?.();
          return;
        }
        _wysiwygInsertLink(_rich);
        return;
      }
      if (action === 'removeformat') {
        if (_clearRichSelectionFormatting(_rich)) {
          _syncEmailRichbody(_rich);
          _scheduleEmailRichbodySave();
          _rich._syncActive?.();
          return;
        }
        document.execCommand('removeFormat');
        // Reset explicit text sizing along with the other inline formatting.
        if (!mdVisual) {
          _applyRichPixelFontSize(_rich, _RICH_FONT_SIZE_PX[3]);
        }
        document.execCommand('unlink');
        // Clear the block style too, so H1-H6 become a normal paragraph.
        document.execCommand('formatBlock', false, 'p');
        _syncEmailRichbody(_rich);
        _scheduleEmailRichbodySave();
        _rich._syncActive?.();
        return;
      }
      if (action === 'image:alt') { _editRichImageAlt(_rich); return; }
      if (action === 'image:caption') { _editRichImageCaption(_rich); return; }
      const _cmd = {
        bold: 'bold', italic: 'italic', underline: 'underline', strike: 'strikeThrough',
        superscript: 'superscript', subscript: 'subscript',
        ul: 'insertUnorderedList', ol: 'insertOrderedList',
        indent: 'indent', outdent: 'outdent', alignleft: 'justifyLeft',
        aligncenter: 'justifyCenter', alignright: 'justifyRight', alignjustify: 'justifyFull',
        removeformat: 'removeFormat',
      };
      try {
        if (_cmd[action]) {
          document.execCommand(_cmd[action]);
          if (action === 'removeformat') document.execCommand('unlink');
        } else if (action === 'unlink') {
          const selection = window.getSelection();
          const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
          if (!_removeRichLink(_rich, _richLinkAtRange(_rich, range))) return;
        } else if (action.startsWith('forecolor:')) {
          const requestedColor = action.slice('forecolor:'.length);
          const color = requestedColor === 'default' || requestedColor === 'theme-fg'
            ? 'var(--fg)'
            : requestedColor === 'theme-bg'
              ? getComputedStyle(_rich).getPropertyValue('--bg').trim()
              : requestedColor;
          if (requestedColor === 'default') _applyRichDefaultTextColor(_rich);
          else document.execCommand('foreColor', false, color);
        } else if (action.startsWith('highlight:')) {
          const color = action.slice('highlight:'.length);
          _applyRichHighlight(_rich, color, _richFormatRange);
        } else if (action.startsWith('fontname:')) {
          // _applyRichFontFamily preserves the browser fontName command while
          // normalizing its legacy output for storage.
          _applyRichFontFamily(_rich, action.slice('fontname:'.length));
        } else if (action.startsWith('fontsize:')) {
          const value = action.slice('fontsize:'.length);
          if (/^\d+(?:\.\d+)?px$/.test(value)) _applyRichPixelFontSize(_rich, parseFloat(value));
          else document.execCommand('fontSize', false, value);
        } else if (action === 'check') {
          if (!_toggleRichChecklist(_rich)) return;
        } else if (action.startsWith('image:')) {
          if (!_applyRichImageAction(_rich, action)) return;
        } else if (action.startsWith('table:')) {
          if (!_applyRichTableAction(_rich, action)) return;
        } else if (action.startsWith('linespacing:')) {
          if (!_applyRichLineSpacing(_rich, action.slice('linespacing:'.length))) return;
        } else if ((mdVisual && action === 'hr') || action.startsWith('separator:')) {
          const style = action.startsWith('separator:') ? action.slice('separator:'.length) : 'solid';
          const border = style === 'double' ? '3px double currentColor' : `1px ${style} currentColor`;
          // Always create a real paragraph after the divider and put the caret
          // there. Without this, browsers leave the caret on the HR's row,
          // making the next text appear beside the line instead of below it.
          const token = `doc-separator-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
          const hrTag = mdVisual
            ? `<hr data-editor-separator-token="${token}" />`
            : `<hr class="richtext-separator" data-editor-separator-token="${token}" style="border:0;border-top:${border};margin:1em 0;" />`;
          document.execCommand('insertHTML', false, `${hrTag}<p data-editor-separator-caret="${token}"><br></p>`);
          const nextParagraph = _rich.querySelector(`[data-editor-separator-caret="${token}"]`);
          if (nextParagraph) {
            nextParagraph.removeAttribute('data-editor-separator-caret');
            const caretRange = document.createRange();
            caretRange.selectNodeContents(nextParagraph);
            caretRange.collapse(true);
            const caretSelection = window.getSelection();
            caretSelection?.removeAllRanges();
            caretSelection?.addRange(caretRange);
          }
          _rich.querySelector(`[data-editor-separator-token="${token}"]`)?.removeAttribute('data-editor-separator-token');
        } else if (action === 'pagebreak') {
          if (!_insertRichPageBreak(_rich)) return;
        } else if (action === 'paragraph') {
          document.execCommand('formatBlock', false, 'p');
        } else if (/^h[1-6]$/.test(action)) {
          // Toggle: if the block is already this heading, revert to a normal
          // paragraph; otherwise apply (or switch to) the heading.
          const cur = _currentBlockTag(_rich);
          document.execCommand('formatBlock', false, (cur === action) ? 'p' : action);
        } else if (action === 'code') {
          if (!_toggleRichInlineCode(_rich)) return;
        } else if (action === 'codeblock') {
          const cur = _currentBlockTag(_rich);
          document.execCommand('formatBlock', false, (cur === 'pre') ? (mdVisual ? 'p' : 'div') : 'pre');
        } else if (action === 'quote') {
          const cur = _currentBlockTag(_rich);
          document.execCommand('formatBlock', false, (cur === 'blockquote') ? (mdVisual ? 'p' : 'div') : 'blockquote');
        }
      } catch (_) {}
      _syncEmailRichbody(_rich);
      _scheduleEmailRichbodySave();
      _scheduleDocumentHistoryControls();
      if (_rich._syncActive) _rich._syncActive();
      return;
    }
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    if (_savedFormatTextareaSelection && ta.selectionStart === ta.selectionEnd) {
      ta.selectionStart = _savedFormatTextareaSelection.start;
      ta.selectionEnd = _savedFormatTextareaSelection.end;
    }
    _savedFormatTextareaSelection = null;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const val = ta.value;
    const sel = val.substring(start, end);
    const before = val.substring(0, start);
    const after = val.substring(end);

    // Inline wrap toggles: bold, italic, strike, code
    const wrapMarks = { bold: '**', italic: '*', strike: '~~', code: '`' };
    if (wrapMarks[action]) {
      const m = wrapMarks[action];
      _applyWrapToggle(ta, before, sel, after, start, end, m, action);
      return;
    }

    // Numbered list — special handling for incrementing numbers
    if (action === 'ol') {
      _applyOrderedList(ta, start, end);
      return;
    }

    // Headings get their own toggle so applying the same level removes it and
    // a different level switches cleanly (rather than stacking # markers).
    if (action === 'paragraph' || /^h[1-6]$/.test(action)) {
      _applyHeadingToggle(ta, start, {
        paragraph: '', h1: '# ', h2: '## ', h3: '### ', h4: '#### ', h5: '##### ', h6: '###### ',
      }[action]);
      return;
    }

    // Line prefix toggles: quote, lists, checkbox
    const prefixMap = { quote: '> ', ul: '- ', check: '- [ ] ' };
    if (prefixMap[action]) {
      _applyLinePrefixToggle(ta, start, end, prefixMap[action]);
      return;
    }

    // Non-toggle actions
    let insert = '';
    let sS = start, sE = start;
    switch (action) {
      case 'link':
        if (sel) {
          insert = `[${sel}](url)`;
          sS = start + 1; sE = start + 1 + sel.length;
        } else {
          insert = '[text](url)';
          sS = start + 1; sE = start + 5;
        }
        break;
      case 'codeblock': {
        // Toggle: find if current line/selection is inside a ``` block
        const linesBefore = val.substring(0, start).split('\n');
        const linesAfter = val.substring(end).split('\n');
        // Look backward for opening ```
        let openIdx = -1;
        for (let i = linesBefore.length - 1; i >= 0; i--) {
          if (/^```/.test(linesBefore[i].trimEnd())) { openIdx = i; break; }
        }
        // Look forward for closing ```
        let closeIdx = -1;
        for (let i = 0; i < linesAfter.length; i++) {
          if (/^```\s*$/.test(linesAfter[i].trimEnd())) { closeIdx = i; break; }
        }
        if (openIdx >= 0 && closeIdx >= 0) {
          // Unwrap: remove the opening and closing fence lines
          const openLineStart = linesBefore.slice(0, openIdx).join('\n').length + (openIdx > 0 ? 1 : 0);
          const openLineEnd = openLineStart + linesBefore[openIdx].length + 1; // +1 for \n
          const closeLineStart = end + linesAfter.slice(0, closeIdx).join('\n').length + (closeIdx > 0 ? 1 : 0);
          const closeLineEnd = closeLineStart + linesAfter[closeIdx].length + (closeIdx < linesAfter.length - 1 ? 1 : 0);
          // Remove closing first (so indices stay valid), then opening
          _replaceRange(ta, closeLineStart, closeLineEnd, '');
          _replaceRange(ta, openLineStart, openLineEnd, '');
          const inner = val.substring(openLineEnd, closeLineStart);
          ta.selectionStart = openLineStart;
          ta.selectionEnd = openLineStart + inner.length;
          return;
        }
        // Wrap in code block
        const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
        insert = nl + '```\n' + (sel || '') + '\n```\n';
        sS = start + nl.length + 4;
        sE = sS + (sel ? sel.length : 0);
        break;
      }
      case 'hr': {
        const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
        insert = `${nl}---\n`;
        sE = sS = start + insert.length;
        break;
      }
      default: return;
    }
    _replaceRange(ta, start, end, insert);
    ta.selectionStart = sS;
    ta.selectionEnd = sE;
  }

function _applyWrapToggle(ta, before, sel, after, start, end, mark, action) {
    const mLen = mark.length;

    // Case 1: selection is wrapped inside — e.g. selected "**bold**" → unwrap to "bold"
    if (sel.startsWith(mark) && sel.endsWith(mark) && sel.length > mLen * 2) {
      const inner = sel.slice(mLen, -mLen);
      _replaceRange(ta, start, end, inner);
      ta.selectionStart = start;
      ta.selectionEnd = start + inner.length;
      return;
    }

    // Case 2: markers are outside selection — e.g. **|bold|** → unwrap
    if (before.endsWith(mark) && after.startsWith(mark)) {
      _replaceRange(ta, start - mLen, end + mLen, sel);
      ta.selectionStart = start - mLen;
      ta.selectionEnd = end - mLen;
      return;
    }

    // Case 3: wrap — add markers. With no selection, insert empty markers and
    // drop the cursor between them (don't inject the action name as text).
    const inner = sel;
    const wrapped = mark + inner + mark;
    _replaceRange(ta, start, end, wrapped);
    ta.selectionStart = start + mLen;
    ta.selectionEnd = start + mLen + inner.length;
  }

export function _currentBlockTag(root) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return '';
    let node = sel.getRangeAt(0).startContainer;
    if (node.nodeType === 3) node = node.parentNode;
    while (node && node !== root) {
      const tag = node.tagName && node.tagName.toLowerCase();
      if (tag && /^(h1|h2|h3|h4|h5|h6|p|div|pre|blockquote|li)$/.test(tag)) return tag;
      node = node.parentNode;
    }
    return '';
  }

function _applyHeadingToggle(ta, caret, prefix) {
    const val = ta.value;
    const lineStart = val.lastIndexOf('\n', caret - 1) + 1;
    const nlIdx = val.indexOf('\n', caret);
    const lineEnd = nlIdx === -1 ? val.length : nlIdx;
    const line = val.substring(lineStart, lineEnd);
    const m = line.match(/^(#{1,6}) /);
    let newLine;
    if (!prefix) {
      newLine = line.replace(/^#{1,6} /, '');
    } else if (m && m[1].length === prefix.trim().length) {
      newLine = line.slice(m[0].length);            // same level → toggle off
    } else if (m) {
      newLine = prefix + line.slice(m[0].length);   // different level → switch
    } else {
      newLine = prefix + line;                       // none → add
    }
    _replaceRange(ta, lineStart, lineEnd, newLine);
    const delta = newLine.length - line.length;
    const pos = Math.max(lineStart, caret + delta);
    ta.selectionStart = ta.selectionEnd = pos;
    ta.focus();
  }

function _applyLinePrefixToggle(ta, start, end, prefix) {
    const val = ta.value;
    const sel = val.substring(start, end);
    const lineStart = val.lastIndexOf('\n', start - 1) + 1;

    if (sel) {
      // Multi-line: toggle prefix on each line
      const lines = sel.split('\n');
      const nonEmpty = lines.filter(l => l.trim());
      const allPrefixed = nonEmpty.length > 0 && nonEmpty.every(l => l.startsWith(prefix));
      const result = allPrefixed
        ? lines.map(l => l.startsWith(prefix) ? l.slice(prefix.length) : l).join('\n')
        : lines.map(l => l.trim() ? prefix + l : l).join('\n');
      _replaceRange(ta, start, end, result);
      ta.selectionStart = start;
      ta.selectionEnd = start + result.length;
    } else {
      // No selection: toggle prefix on the current line
      const lineBefore = val.substring(lineStart, start);

      if (lineBefore.startsWith(prefix)) {
        // Remove prefix
        _replaceRange(ta, lineStart, lineStart + prefix.length, '');
      } else {
        // Add prefix at line start
        _replaceRange(ta, lineStart, lineStart, prefix);
      }
    }
  }

function _applyOrderedList(ta, start, end) {
    const val = ta.value;
    const sel = val.substring(start, end);
    const lineStart = val.lastIndexOf('\n', start - 1) + 1;

    if (sel) {
      const lines = sel.split('\n');
      const nonEmpty = lines.filter(l => l.trim());
      const allNumbered = nonEmpty.length > 0 && nonEmpty.every(l => /^\d+\.\s/.test(l));
      const result = allNumbered
        ? lines.map(l => l.replace(/^\d+\.\s/, '')).join('\n')
        : (() => { let n = 0; return lines.map(l => l.trim() ? `${++n}. ${l}` : l).join('\n'); })();
      _replaceRange(ta, start, end, result);
      ta.selectionStart = start;
      ta.selectionEnd = start + result.length;
    } else {
      const lineBefore = val.substring(lineStart, start);
      if (/^\d+\.\s/.test(lineBefore)) {
        const prefixLen = lineBefore.match(/^\d+\.\s/)[0].length;
        _replaceRange(ta, lineStart, lineStart + prefixLen, '');
      } else {
        // Find the previous numbered line to continue the sequence
        const prevText = val.substring(0, lineStart);
        const prevMatch = prevText.match(/(\d+)\.\s[^\n]*\n$/);
        const num = prevMatch ? parseInt(prevMatch[1]) + 1 : 1;
        _replaceRange(ta, lineStart, lineStart, `${num}. `);
      }
    }
  }

export function _normalizedRichMenuColor(value) {
    const probe = document.createElement('span');
    probe.style.color = String(value || '');
    return (probe.style.color || String(value || '')).replace(/\s+/g, '').toLowerCase();
  }

export function _richMenuColorHex(value, fallback) {
    const normalized = _normalizedRichMenuColor(value);
    const shortHex = normalized.match(/^#([0-9a-f]{3})$/i);
    if (shortHex) return `#${shortHex[1].split('').map(char => char + char).join('')}`.toLowerCase();
    if (/^#[0-9a-f]{6}$/i.test(normalized)) return normalized.toLowerCase();
    const rgb = normalized.match(/^rgba?\((\d+),(\d+),(\d+)/i);
    if (!rgb) return fallback;
    return `#${rgb.slice(1, 4).map(channel => Math.max(0, Math.min(255, Number(channel)))
      .toString(16).padStart(2, '0')).join('')}`;
  }

function _richContrastTextColor(background) {
    const hex = _richMenuColorHex(background, '#ffffff');
    const channels = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
    const luminance = channels
      .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
      .reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0);
    const darkContrast = (luminance + 0.05) / 0.0603;
    const lightContrast = 1.05 / (luminance + 0.05);
    return darkContrast >= lightContrast ? '#111827' : '#f9fafb';
  }

function _applyRichHighlight(rich, color, preservedRange = null) {
    const selection = window.getSelection?.();
    const range = preservedRange || (selection?.rangeCount ? selection.getRangeAt(0) : null);
    const clearHighlight = !color || color === 'transparent' || color === 'rgba(0,0,0,0)';
    if (!range || range.collapsed || !rich.contains(range.commonAncestorContainer)) {
      if (preservedRange && rich.contains(preservedRange.commonAncestorContainer)) {
        try {
          selection.removeAllRanges();
          selection.addRange(preservedRange.cloneRange());
        } catch (_) {}
      }
      const applied = document.execCommand('hiliteColor', false, clearHighlight ? 'transparent' : color)
        || document.execCommand('backColor', false, clearHighlight ? 'transparent' : color);
      if (!clearHighlight) document.execCommand('foreColor', false, _richContrastTextColor(color));
      else document.execCommand('foreColor', false, 'var(--fg)');
      return applied;
    }

    const selectedText = range.toString();
    const fragment = range.cloneContents();
    if (clearHighlight) {
      fragment.querySelectorAll?.('[data-rich-highlight-contrast="auto"]').forEach(element => {
        element.style.removeProperty('background');
        element.style.removeProperty('background-color');
        element.style.removeProperty('color');
        element.removeAttribute('data-rich-highlight-contrast');
        if (!element.getAttribute('style')) element.removeAttribute('style');
      });
    } else {
      // A highlight owns foreground contrast so nested manual colors cannot
      // leave text unreadable against the newly selected background.
      fragment.querySelectorAll?.('[style]').forEach(element => {
        element.style.removeProperty('color');
        if (!element.getAttribute('style')) element.removeAttribute('style');
      });
      fragment.querySelectorAll?.('font[color]').forEach(element => element.removeAttribute('color'));
    }

    const token = `rich-highlight-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const span = document.createElement('span');
    span.dataset.richHighlightToken = token;
    span.style.backgroundColor = clearHighlight ? 'transparent' : color;
    span.style.color = clearHighlight ? 'var(--fg)' : _richContrastTextColor(color);
    if (!clearHighlight) span.dataset.richHighlightContrast = 'auto';
    span.appendChild(fragment);
    const holder = document.createElement('div');
    holder.appendChild(span);
    const applyNativeHighlight = () => {
      const nativeColor = clearHighlight ? 'transparent' : color;
      const applied = document.execCommand('hiliteColor', false, nativeColor)
        || document.execCommand('backColor', false, nativeColor);
      document.execCommand('foreColor', false, clearHighlight ? 'var(--fg)' : _richContrastTextColor(color));
      return applied;
    };

    // insertHTML records background + contrast as one native undo step.
    try {
      selection.removeAllRanges();
      selection.addRange(range.cloneRange());
    } catch (_) {}
    rich.focus({ preventScroll: true });
    // Focusing a contenteditable can collapse the selection on mobile. Restore
    // it after focus, immediately before the native edit command.
    try {
      selection.removeAllRanges();
      selection.addRange(range.cloneRange());
    } catch (_) {}
    const insertedHtml = document.execCommand('insertHTML', false, holder.innerHTML);
    if (!insertedHtml) {
      // Some WebKit/contenteditable combinations reject insertHTML for a
      // selection that crosses inline nodes. Fall back to the native command
      // so the user still gets a visible edit instead of a silent no-op.
      return applyNativeHighlight();
    }
    const inserted = rich.querySelector(`[data-rich-highlight-token="${token}"]`);
    const insertedText = inserted?.textContent || '';
    if (!inserted || insertedText !== selectedText) return applyNativeHighlight();
    inserted.removeAttribute('data-rich-highlight-token');
    const selected = document.createRange();
    selected.selectNodeContents(inserted);
    selection.removeAllRanges();
    selection.addRange(selected);
    return true;
  }

function _applyRichDefaultTextColor(rich) {
    const selection = window.getSelection?.();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || range.collapsed || !rich.contains(range.commonAncestorContainer)) {
      return document.execCommand('foreColor', false, getComputedStyle(rich).color);
    }
    const fragment = range.cloneContents();
    // Remove only explicit foreground colors, leaving bold, links, sizes, and
    // block formatting intact. The wrapper stays tied to the active theme.
    fragment.querySelectorAll?.('[style]').forEach(element => {
      element.style.removeProperty('color');
      if (!element.getAttribute('style')) element.removeAttribute('style');
    });
    fragment.querySelectorAll?.('font[color]').forEach(element => element.removeAttribute('color'));
    const token = `rich-default-color-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const span = document.createElement('span');
    span.dataset.richDefaultColorToken = token;
    span.style.color = 'var(--fg)';
    span.appendChild(fragment);
    const holder = document.createElement('div');
    holder.appendChild(span);
    rich.focus({ preventScroll: true });
    if (!document.execCommand('insertHTML', false, holder.innerHTML)) {
      return document.execCommand('foreColor', false, getComputedStyle(rich).color);
    }
    const inserted = rich.querySelector(`[data-rich-default-color-token="${token}"]`);
    if (!inserted) return document.execCommand('foreColor', false, getComputedStyle(rich).color);
    inserted.removeAttribute('data-rich-default-color-token');
    const selected = document.createRange();
    selected.selectNodeContents(inserted);
    selection.removeAllRanges();
    selection.addRange(selected);
    return true;
  }

export function _saveFormatSelection(event) {
    const target = event.target.closest?.('[data-md], .md-dd-toggle');
    if (!target) return false;

    // A fresh pointer press starts a new formatting action. Clear a previous
    // saved range so an old dropdown selection cannot be reused accidentally.
    if (event.type === 'pointerdown') {
      _savedFormatTextareaSelection = null;
      _savedFormatRichRange = null;
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      _savedFormatTextareaSelection = {
        start: textarea.selectionStart,
        end: textarea.selectionEnd,
      };
    }

    const rich = _emailRichbodyActive();
    const selection = window.getSelection?.();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (rich && range && !range.collapsed && rich.contains(range.commonAncestorContainer)) {
      _savedFormatRichRange = range.cloneRange();
    }
    return true;
  }
