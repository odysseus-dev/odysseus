// frontend/src/features/documents/ui/richEditor/richBodySync.js
/**
 * Document Editor Rich-Text Subsystem: richBodySync
 * Conforms to 087e6c88 baseline AST and Appendix A specifications.
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _isRichTextLang } from '../../domain/documentKinds.js';
import { _normalizeRichLinkUrl, _isRichInlineCodeMarker } from '../../domain/richTextRules.js';
import { _buildEmailContent, _parseEmailHeader } from '../../domain/emailMessage.js';

export function _emailPlainTextToHtml(text) {
    const d = document.createElement('div');
    d.textContent = text == null ? '' : String(text);
    return d.innerHTML.replace(/\n/g, '<br>');
  }

export function _emailHtmlToPlainText(html) {
    if (typeof document === 'undefined') return String(html || '');
    const d = document.createElement('template');
    d.innerHTML = String(html || '');
    return d.content.textContent || '';
  }

export function _sanitizedRichTextHtml(rich) {
    if (!rich) return '';
    const text = (rich.innerText || rich.textContent || '').replace(/\u00a0/g, ' ').trim();
    if (!text && !rich.querySelector('img, hr, table')) return '';
    const html = rich.innerHTML || '';
    const sanitized = markdownModule.sanitizeAllowedHtml
      ? markdownModule.sanitizeAllowedHtml(html)
      : html;
    const tpl = document.createElement('template');
    tpl.innerHTML = sanitized;
    tpl.content.querySelectorAll('a[href]').forEach(link => {
      const safeUrl = _normalizeRichLinkUrl(link.getAttribute('href'));
      if (!safeUrl) {
        link.replaceWith(...Array.from(link.childNodes));
        return;
      }
      link.setAttribute('href', safeUrl);
      link.setAttribute('rel', 'noopener noreferrer');
    });
    tpl.content.querySelectorAll('span').forEach(span => {
      if (!_isRichInlineCodeMarker(span)) return;
      const code = document.createElement('code');
      code.innerHTML = span.innerHTML;
      const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) node.nodeValue = node.nodeValue.replace(/\u200b/g, '');
      span.replaceWith(code);
    });
    return tpl.innerHTML
      .replace(/\sdata-editor-(?:table|checklist|image|inline-code|link)-token="[^"]*"/gi, '')
      .replace(/\sdata-editor-image-selected(?:="[^"]*")?/gi, '');
  }

export function _richTextContentToHtml(content) {
    const raw = String(content || '');
    if (!raw.trim()) return '';
    if (/<\/?[a-z][^>]*>/i.test(raw)) {
      return markdownModule.sanitizeAllowedHtml
        ? markdownModule.sanitizeAllowedHtml(raw)
        : raw;
    }
    try { return markdownModule.mdToHtml(raw, { shortcodes: false }); }
    catch (_) { return _emailPlainTextToHtml(raw); }
  }

export function _syncEmailRichbody(rich) {
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    const doc = activeDocId && docs.get(activeDocId);
    if (doc && _isRichTextLang(doc.language)) {
      const html = _sanitizedRichTextHtml(rich);
      ta.value = html;
      doc.content = html;
      _syncRichEmptyImport(rich);
      return;
    }
    ta.value = rich.innerText;
    if (doc && doc.language === 'email') {
      const fields = _parseEmailHeader(doc.content || '');
      doc.content = _buildEmailContent(
        document.getElementById('doc-email-to')?.value || fields.to || '',
        document.getElementById('doc-email-subject')?.value || fields.subject || '',
        document.getElementById('doc-email-in-reply-to')?.value || fields.inReplyTo || '',
        document.getElementById('doc-email-references')?.value || fields.references || '',
        rich.innerHTML,
        document.getElementById('doc-email-source-uid')?.value || fields.sourceUid || '',
        document.getElementById('doc-email-source-folder')?.value || fields.sourceFolder || '',
        document.getElementById('doc-email-cc')?.value || fields.cc || '',
        document.getElementById('doc-email-bcc')?.value || fields.bcc || '',
      );
    }
  }

export function _syncRichEmptyImport(rich = document.getElementById('doc-email-richbody')) {
    const action = document.getElementById('doc-rich-empty-import');
    if (!action) return;
    const doc = activeDocId && docs.get(activeDocId);
    const empty = !!(
      rich &&
      rich.style.display !== 'none' &&
      doc && _isRichTextLang(doc.language) &&
      !rich.textContent.trim() &&
      !rich.querySelector('img, table, hr, iframe')
    );
    action.style.display = empty ? 'flex' : 'none';
  }

export function _scheduleEmailRichbodySave() {
    const doc = activeDocId && docs.get(activeDocId);
    if (doc) _markDocumentDirty(activeDocId);
    _persistEmailLocalDraftSoon();
    _scheduleAutoSave(activeDocId, 2500, { isEmailRichbody: true });
  }

export function _emailRichbodyActive() {
    const r = document.getElementById('doc-email-richbody');
    return r && r.style.display !== 'none' ? r : null;
  }

