import { _emailRichbodyActive, _emailPlainTextToHtml, _emailHtmlToPlainText } from '../richEditor/richBodySync.js';
import { markdownModule } from '../host.js';
import { syncHighlighting } from '../editor/sourceEditor.js';
import { _emailQuoteMarkerMatch } from "../../domain/emailMessage.js";

import { _persistEmailLocalDraftSoon } from "./localDraft.js";

export let _emailStreamAnimFrame = null;
export let _emailStreamRenderedBody = '';
export let _emailStreamTargetBody = '';

export function resetEmailStreamState(body = '') {
  if (_emailStreamAnimFrame) cancelAnimationFrame(_emailStreamAnimFrame);
  _emailStreamAnimFrame = null;
  _emailStreamRenderedBody = body;
  _emailStreamTargetBody = body;
}

export function _captureEmailBodyFocusState() {
    const rich = _emailRichbodyActive();
    const ta = document.getElementById('doc-editor-textarea');
    const active = document.activeElement;
    if (rich && (active === rich || rich.contains(active))) {
      const sel = window.getSelection();
      const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
      return {
        type: 'rich',
        range: range && rich.contains(range.commonAncestorContainer) ? range.cloneRange() : null,
      };
    }
    if (ta && active === ta) {
      return {
        type: 'textarea',
        start: ta.selectionStart,
        end: ta.selectionEnd,
      };
    }
    return null;
  }

function _emailBodyFragmentToHtml(text) {
    const t = (text || '').trim();
    if (!t) return '';
    // If it already contains a formatting/structural HTML tag, it's a saved
    // WYSIWYG body — sanitize it before rendering. (Checking a leading '<' isn't enough: a
    // rich body often starts with plain text, e.g. "Hi <b>there</b>".)
    if (/<\/?(b|i|u|s|strong|em|del|strike|a|p|div|br|ul|ol|li|h[1-3]|blockquote|span|code|pre)\b[^>]*>/i.test(t)) {
      return markdownModule.sanitizeAllowedHtml
        ? markdownModule.sanitizeAllowedHtml(t)
        : _emailPlainTextToHtml(t);
    }
    // Email body: keep author-typed `:shortcode:` text literal. Issue #345
    // (shortcode → emoji) is scoped to chat; do not rewrite colons in mail.
    try { return markdownModule.mdToHtml(text, { shortcodes: false }); }
    catch (_) { return _emailPlainTextToHtml(text); }
  }

export function _emailBodyToHtml(text) {
    const raw = String(text || '');
    const marker = _emailQuoteMarkerMatch(raw);
    if (!marker) {
      const t = raw.trim();
      if (/<\/?(b|i|u|s|strong|em|del|strike|a|p|div|br|ul|ol|li|h[1-3]|blockquote|span|code|pre)\b[^>]*>/i.test(t)) {
        return markdownModule.sanitizeAllowedHtml
          ? markdownModule.sanitizeAllowedHtml(t)
          : _emailPlainTextToHtml(t);
      }
      return _emailBodyFragmentToHtml(raw);
    }
    const replyPart = raw.slice(0, marker.index);
    const quotedPart = raw.slice(marker.index);
    const quotedText = _emailHtmlToPlainText(quotedPart)
      .replace(/\u00a0/g, ' ')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    const replyHtml = _emailBodyFragmentToHtml(replyPart);
    if (!quotedText) return replyHtml;
    const firstQuoted = quotedText
      .split(/\n\s*-{5,}\s*Previous message\s*-{5,}\s*\n/i)[0]
      .trim();
    const truncatedQuote = firstQuoted.length > 1800
      ? `${firstQuoted.slice(0, 1800).replace(/\s+\S*$/, '').trim()}\n\n[Quoted thread truncated]`
      : firstQuoted;
    return `${replyHtml}<div class="email-quoted-history" contenteditable="false">${_emailPlainTextToHtml(truncatedQuote)}</div>`;
  }

function _emailQuoteStartIndex(lines) {
    for (let i = 0; i < lines.length; i++) {
      const line = String(lines[i] || '').trim();
      if (
        /^[-_=–—\s]{3,}(previous|original|forwarded)\s+(message|email|mail)[-_=–—\s]{3,}$/i.test(line)
        || /^On .+ wrote:\s*$/i.test(line)
        || /^-{2,}\s*Original Message\s*-{2,}$/i.test(line)
      ) {
        return i;
      }
      // Some pasted/converted threads lose the separator and start directly
      // with mail headers. Treat that as quoted history only when the nearby
      // lines look like a real header block.
      if (/^From:\s+\S/i.test(line)) {
        const nearby = lines.slice(i + 1, i + 8).map(l => String(l || '').trim());
        if (nearby.some(l => /^To:\s+/i.test(l)) || nearby.some(l => /^Subject:\s+/i.test(l))) {
          return i;
        }
      }
    }
    return -1;
  }

function _emailQuoteStartOffset(text) {
    const original = String(text || '');
    if (!original) return -1;
    const boundary = String.raw`(?:^|\n|<br\s*\/?>|<\/(?:p|div|blockquote|li|tr|h[1-6])>)`;
    const patterns = [
      new RegExp(`${boundary}\\s*(?:[-_=–—\\s]|&nbsp;){3,}(?:previous|original|forwarded)\\s+(?:message|email|mail)(?:[-_=–—\\s]|&nbsp;){3,}`, 'i'),
      new RegExp(`${boundary}\\s*On\\s+.{1,700}?\\s+wrote:\\s*`, 'i'),
      new RegExp(`${boundary}\\s*-{2,}\\s*Original Message\\s*-{2,}`, 'i'),
    ];
    let best = -1;
    for (const re of patterns) {
      const m = re.exec(original);
      if (!m) continue;
      let idx = m.index;
      const prefix = m[0].match(/^(?:\n|<br\s*\/?>|<\/(?:p|div|blockquote|li|tr|h[1-6])>)/i);
      if (prefix) idx += prefix[0].length;
      if (best < 0 || idx < best) best = idx;
    }
    const fromRe = new RegExp(`${boundary}\\s*From:\\s*\\S`, 'i');
    const fromMatch = fromRe.exec(original);
    if (fromMatch) {
      let idx = fromMatch.index;
      const prefix = fromMatch[0].match(/^(?:\n|<br\s*\/?>|<\/(?:p|div|blockquote|li|tr|h[1-6])>)/i);
      if (prefix) idx += prefix[0].length;
      const nearby = original.slice(idx, idx + 1200);
      if (/(?:^|\n|<br\s*\/?>|<\/(?:p|div|blockquote|li|tr|h[1-6])>)\s*(?:To|Subject):\s*/i.test(nearby)) {
        if (best < 0 || idx < best) best = idx;
      }
    }
    return best;
  }

export function _emailReplyOwnText(text) {
    return _stripEmailReplyQuoteText(text).body;
  }

export function _focusEmailBodyEnd() {
    const target = _emailRichbodyActive() || document.getElementById('doc-editor-textarea');
    if (!target) return;
    target.focus();
    if (target.isContentEditable) {
      const range = document.createRange();
      const quote = target.querySelector('.email-quoted-history');
      if (quote) {
        let slot = quote.previousElementSibling;
        if (!slot || slot.classList.contains('email-quoted-history')) {
          slot = document.createElement('div');
          slot.className = 'email-reply-edit-slot';
          slot.innerHTML = '<br>';
          target.insertBefore(slot, quote);
        }
        range.selectNodeContents(slot);
        range.collapse(false);
      } else {
        range.selectNodeContents(target);
        range.collapse(false);
      }
      const sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } else if (typeof target.setSelectionRange === 'function') {
      const len = target.value.length;
      target.setSelectionRange(len, len);
    }
  }

export function _renderStreamingEmailBody(body, { immediate = false } = {}) {
    const rich = document.getElementById('doc-email-richbody');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!rich) return;

    _emailStreamTargetBody = body || '';
    if (!_emailStreamRenderedBody && textarea && textarea.value) {
      _emailStreamRenderedBody = textarea.value;
    }

    const applyBody = (value) => {
      if (textarea) {
        textarea.value = value;
        textarea.scrollTop = textarea.scrollHeight;
      }
      rich.innerHTML = _emailBodyToHtml(value);
      rich.scrollTop = rich.scrollHeight;
    };

    if (immediate) {
      if (_emailStreamAnimFrame) cancelAnimationFrame(_emailStreamAnimFrame);
      _emailStreamAnimFrame = null;
      _emailStreamRenderedBody = _emailStreamTargetBody;
      applyBody(_emailStreamRenderedBody);
      return;
    }

    if (_emailStreamTargetBody.length < _emailStreamRenderedBody.length ||
        !_emailStreamTargetBody.startsWith(_emailStreamRenderedBody)) {
      _emailStreamRenderedBody = '';
    }

    if (_emailStreamAnimFrame) return;
    const tick = () => {
      const remaining = _emailStreamTargetBody.length - _emailStreamRenderedBody.length;
      if (remaining <= 0) {
        _emailStreamAnimFrame = null;
        return;
      }
      const step = Math.max(1, Math.min(8, Math.ceil(remaining / 18)));
      _emailStreamRenderedBody = _emailStreamTargetBody.slice(0, _emailStreamRenderedBody.length + step);
      applyBody(_emailStreamRenderedBody);
      _emailStreamAnimFrame = requestAnimationFrame(tick);
    };
    _emailStreamAnimFrame = requestAnimationFrame(tick);
  }

export function _restoreEmailBodyFocusState(state) {
    if (!state) return;
    requestAnimationFrame(() => {
      if (state.type === 'rich') {
        const rich = _emailRichbodyActive();
        if (!rich) return;
        rich.focus({ preventScroll: true });
        if (state.range) {
          const sel = window.getSelection();
          if (sel) {
            sel.removeAllRanges();
            sel.addRange(state.range);
          }
        }
      } else if (state.type === 'textarea') {
        const ta = document.getElementById('doc-editor-textarea');
        if (!ta) return;
        ta.focus({ preventScroll: true });
        if (Number.isFinite(state.start) && Number.isFinite(state.end)) {
          try { ta.setSelectionRange(state.start, state.end); } catch (_) {}
        }
      }
    });
  }

export function _setEmailBodyText(textarea, value) {
    if (!textarea) return;
    textarea.value = value || '';
    syncHighlighting();
    const rich = _emailRichbodyActive();
    if (rich) rich.innerHTML = _emailBodyToHtml(textarea.value);
    _persistEmailLocalDraftSoon();
  }

export function _splitEmailReplyQuote(text) {
    const original = String(text || '');
    if (!original) return { body: '', quote: '', stripped: false };
    const literal = '---------- Previous message ----------';
    const literalIdx = original.indexOf(literal);
    if (literalIdx >= 0) {
      return {
        body: original.slice(0, literalIdx).trim(),
        quote: original.slice(literalIdx).trim(),
        stripped: true,
      };
    }
    const htmlQuoteOffset = _emailQuoteStartOffset(original);
    if (htmlQuoteOffset >= 0) {
      const body = original.slice(0, htmlQuoteOffset).trim();
      const quote = original.slice(htmlQuoteOffset).trim();
      return { body, quote, stripped: true };
    }
    const lines = original.split('\n');
    const quoteIdx = _emailQuoteStartIndex(lines);
    if (quoteIdx < 0) return { body: original.trim(), quote: '', stripped: false };
    const body = lines.slice(0, quoteIdx).join('\n').trim();
    const quote = lines.slice(quoteIdx).join('\n').trim();
    return { body, quote, stripped: true };
  }

async function _streamEmailBodyText(textarea, value) {
    if (!textarea) return;
    const finalText = String(value || '');
    const maxFrames = 90;
    const chunk = Math.max(8, Math.ceil(finalText.length / maxFrames));
    textarea.value = '';
    const rich = _emailRichbodyActive();
    if (rich) rich.innerHTML = '';
    for (let i = 0; i < finalText.length; i += chunk) {
      const next = finalText.slice(0, i + chunk);
      textarea.value = next;
      if (rich) rich.innerHTML = _emailBodyToHtml(next);
      _persistEmailLocalDraftSoon();
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
    _setEmailBodyText(textarea, finalText);
  }

function _stripEmailReplyQuoteText(text) {
    const split = _splitEmailReplyQuote(text);
    return { body: split.body, stripped: split.stripped };
  }

export function focusEmailReplyBody() {
    if (window.innerWidth > 768) return false;
    const rich = _emailRichbodyActive();
    if (!rich) return false;
    const quote = rich.querySelector('.email-quoted-history');
    const replyBlock = quote?.previousElementSibling || rich.firstElementChild || rich;
    try { rich.focus({ preventScroll: true }); } catch (_) { rich.focus(); }
    const range = document.createRange();
    if (replyBlock && replyBlock !== quote) range.selectNodeContents(replyBlock);
    else range.selectNodeContents(rich);
    range.collapse(true);
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
      selection.addRange(range);
    }
    rich.scrollTop = 0;
    return true;
  }
