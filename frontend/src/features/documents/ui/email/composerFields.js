import { _hideRichSelectionToolbar } from '../richEditor/selectionToolbar.js';
import { _hideRichSlashMenu } from '../richEditor/slashMenu.js';
import { downloadBlob, spinnerModule, uiModule } from '../host.js';
import { loadDocument } from '../documentFlows.js';
import { syncHighlighting } from '../editor/sourceEditor.js';
import { resetEmailStreamState } from './emailBody.js';
import { _wireEmailRichbody } from '../richEditor/richBodyWiring.js';
import { API_BASE, activeDocId, docs } from "../../application/documentState.js";

import { _escHtml } from "../../domain/documentKinds.js";

import { _parseEmailHeader } from "../../domain/emailMessage.js";

import { _renderComposeAttachments, _stageForwardedSourceAttachments } from "./attachments.js";

import { _emailBodyToHtml, _focusEmailBodyEnd, _renderStreamingEmailBody } from "./emailBody.js";

import { _emailFieldsWithLocalDraft } from "./localDraft.js";

let _emailHeaderManualExpandUntil = 0;

export function _hideEmailFields() {
    _hideRichSelectionToolbar();
    _hideRichSlashMenu();
    const emailHeader = document.getElementById('doc-email-header');
    const emailActions = document.getElementById('doc-email-actions');
    if (emailHeader) delete emailHeader.dataset.activeDocumentId;
    if (emailHeader) emailHeader.style.display = 'none';
    if (emailActions) emailActions.style.display = 'none';
    // Restore toolbar items that were hidden for email (Code dropdown).
    document.querySelectorAll('.md-toolbar-email-hide').forEach(el => { el.style.display = ''; });
    // Re-hide email-only toolbar items (AI reply button).
    document.querySelectorAll('.md-toolbar-email-only').forEach(el => { el.style.display = 'none'; });
    // Restore the generic documents action bar + its bottom footer (Close /
    // Copy / Export) for non-email docs.
    const docActions = document.getElementById('doc-editor-actions');
    if (docActions) docActions.style.display = '';
    const docFooter = document.getElementById('doc-actions-footer');
    if (docFooter) docFooter.style.display = '';
    // Return the type picker to its non-email home (right before the
    // Copy/Export split) — _showEmailFields moved it into the email footer.
    if (docFooter) {
      const _lang = document.getElementById('doc-language-select');
      const _langPicker = document.getElementById('doc-langpicker-trigger');
      const _split = docFooter.querySelector('#doc-copy-export-split');
      if (_lang && _split) docFooter.insertBefore(_lang, _split);
      if (_langPicker && _split) {
        _langPicker.classList.remove('doc-langpicker-email-compact');
        _langPicker.title = 'Change document type';
        docFooter.insertBefore(_langPicker, _split);
      }
    }
    // Restore the source editor and hide the WYSIWYG email body.
    const _rich = document.getElementById('doc-email-richbody');
    if (_rich) {
      _rich.style.display = 'none';
      _rich.classList.remove('richtext-mode');
      _rich.setAttribute('aria-label', 'Email body');
    }
    const _srcWrap = document.getElementById('doc-editor-wrap');
    if (_srcWrap) _srcWrap.style.display = '';
    document.getElementById('doc-editor-textarea')?.style.removeProperty('display');
    // Drop the email-mode class so editors return to monospace monochrome
    document.getElementById('doc-editor-textarea')?.classList.remove('email-mode');
    document.getElementById('doc-editor-code')?.classList.remove('email-mode');
    document.getElementById('doc-editor-highlight')?.classList.remove('email-mode');
  }

export function _maybeAutoCollapseEmailHeader() {
    const doc = activeDocId && docs.get(activeDocId);
    if (!doc || doc.language !== 'email') return;
    if (Date.now() < _emailHeaderManualExpandUntil) return;
    if (document.activeElement?.closest?.('#doc-email-fields')) return;
    if (_shouldAutoCollapseEmailHeader()) _setEmailHeaderCollapsed(true, { manual: false });
  }

export function _setEmailHeaderCollapsed(collapsed, { manual = true } = {}) {
    const header = document.getElementById('doc-email-header');
    const btn = document.getElementById('doc-email-collapse-btn');
    if (!header) return;
    if (window.innerWidth > 768) collapsed = false;
    header.classList.toggle('doc-email-header-collapsed', !!collapsed);
    if (btn) {
      btn.setAttribute('aria-expanded', String(!collapsed));
      btn.title = collapsed ? 'Show email fields' : 'Hide email fields';
    }
    const doc = activeDocId && docs.get(activeDocId);
    if (doc && manual) doc._emailHeaderCollapsed = !!collapsed;
    if (manual && !collapsed) _emailHeaderManualExpandUntil = Date.now() + 1400;
    _syncEmailHeaderSummary();
  }

function _setEmailHeaderInputValue(id, value, { preserveFocused = true, preserveNonEmpty = false } = {}) {
    const el = document.getElementById(id);
    if (!el) return;
    const next = value || '';
    if (preserveFocused && document.activeElement === el) return;
    if (preserveNonEmpty && !next && el.value) return;
    if (el.value !== next) el.value = next;
  }

function _shouldAutoCollapseEmailHeader() {
    return window.innerWidth <= 768;
  }

export function _showEmailFields(doc, { applyLocalDraft = true, forceHeaderFields = false } = {}) {
    const emailHeader = document.getElementById('doc-email-header');
    const emailActions = document.getElementById('doc-email-actions');
    // The pane owner may already have changed before this shared header renders.
    // Only the document that populated these inputs may preserve their edits.
    forceHeaderFields = forceHeaderFields || !doc?.id || emailHeader?.dataset.activeDocumentId !== doc.id;
    if (emailHeader) emailHeader.dataset.activeDocumentId = doc?.id || '';
    document.getElementById('doc-email-richbody')?.classList.remove('richtext-mode');
    // Show MD toolbar for email too (B, I, etc.)
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar) {
      mdToolbar.style.display = '';
      if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
    }
    // Hide toolbar items that have no clean WYSIWYG equivalent in email (Code).
    document.querySelectorAll('.md-toolbar-email-hide').forEach(el => { el.style.display = 'none'; });
    // Show email-only toolbar items (AI reply button).
    document.querySelectorAll('.md-toolbar-email-only').forEach(el => { el.style.display = 'inline-flex'; });
    if (emailHeader) emailHeader.style.display = '';
    if (emailActions) emailActions.style.display = '';
    // Emails have their own complete footer (Close / More / Send), so hide the
    // generic documents action bar AND the generic bottom footer. The TYPE
    // picker is the exception — relocate it into the email footer so the
    // type-switching affordance is in the same footer slot across all docs.
    const docActions = document.getElementById('doc-editor-actions');
    if (docActions) docActions.style.display = 'none';
    const docFooter = document.getElementById('doc-actions-footer');
    if (docFooter) docFooter.style.display = 'none';
    if (emailActions) {
      const _lang = document.getElementById('doc-language-select');
      const _langPicker = document.getElementById('doc-langpicker-trigger');
      const _sendSplit = emailActions.querySelector('.email-send-split');
      if (_lang && _sendSplit) emailActions.insertBefore(_lang, _sendSplit);
      if (_langPicker && _sendSplit) {
        _langPicker.classList.add('doc-langpicker-email-compact');
        _langPicker.title = 'Change document type';
        emailActions.insertBefore(_langPicker, _sendSplit);
      }
    }
    // Colored system-emoji font for email compose
    document.getElementById('doc-editor-textarea')?.classList.add('email-mode');
    document.getElementById('doc-editor-code')?.classList.add('email-mode');
    document.getElementById('doc-editor-highlight')?.classList.add('email-mode');
    let fields = _parseEmailHeader(doc.content || '');
    if (applyLocalDraft) fields = _emailFieldsWithLocalDraft(fields);
    const preserveEmailHeader = !!(fields.sourceUid || fields.inReplyTo || fields.references);
    const subjectInput = document.getElementById('doc-email-subject');
    const textarea = document.getElementById('doc-editor-textarea');
    _setEmailHeaderInputValue('doc-email-to', fields.to, { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderInputValue('doc-email-subject', fields.subject, { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderCollapsed(!!(doc && doc._emailHeaderCollapsed), { manual: false });
    if (subjectInput && !subjectInput._emailTabBodyBound) {
      subjectInput._emailTabBodyBound = true;
      subjectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Tab' && !e.shiftKey) {
          e.preventDefault();
          _focusEmailBodyEnd();
        }
      });
    }
    _setEmailHeaderInputValue('doc-email-in-reply-to', fields.inReplyTo, { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderInputValue('doc-email-references', fields.references, { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderInputValue('doc-email-source-uid', fields.sourceUid || '', { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderInputValue('doc-email-source-folder', fields.sourceFolder || '', { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    // Show/hide unread button only if we have a source UID (came from inbox)
    const unreadBtn = document.getElementById('doc-email-unread-btn');
    if (unreadBtn) unreadBtn.style.display = fields.sourceUid ? '' : 'none';
    // Render attachment chips
    const attDiv = document.getElementById('doc-email-attachments');
    if (attDiv) {
      attDiv.innerHTML = '';
      if (fields.attachments && fields.attachments.length > 0 && fields.sourceUid) {
        attDiv.style.display = '';
        for (const att of fields.attachments) {
          const isPdf = (att.filename || '').toLowerCase().endsWith('.pdf');
          const sizeKb = att.size > 0 ? `${Math.round(att.size / 1024)} KB` : '';
          const chipHtml = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><span>${_escHtml(att.filename)}</span><span class="att-size">${sizeKb}</span>`;
          // Helper: swap chip content for a whirlpool spinner while busy.
          const _withSpinner = async (chip, fn) => {
            if (chip.dataset.loading === '1') return;
            chip.dataset.loading = '1';
            const orig = chip.innerHTML;
            chip.innerHTML = '';
            const sp = spinnerModule.createWhirlpool(14);
            sp.style.marginRight = '6px';
            chip.appendChild(sp);
            const lbl = document.createElement('span');
            lbl.textContent = att.filename;
            chip.appendChild(lbl);
            try { await fn(); }
            finally { chip.dataset.loading = ''; chip.innerHTML = orig; }
          };
          if (isPdf) {
            // PDF: open in the in-app PDF viewer as a new doc tab
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'email-attachment-chip email-attachment-chip-pdf';
            // Full filename on hover — chip ellipsis-truncates long names.
            chip.title = att.filename;
            chip.innerHTML = chipHtml;
            chip.addEventListener('click', () => _withSpinner(chip, async () => {
              try {
                const folderQs = encodeURIComponent(fields.sourceFolder || 'INBOX');
                const res = await fetch(`${API_BASE}/api/email/attachment-as-doc/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`, { method: 'POST' });
                const data = await res.json();
                if (data.doc_id) {
                  await loadDocument(data.doc_id);
                } else if (uiModule) {
                  uiModule.showError(data.error || 'Failed to open PDF');
                  window.open(`${API_BASE}/api/email/attachment/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`, '_blank');
                }
              } catch (e) {
                console.error('Open PDF attachment failed:', e);
                if (uiModule) uiModule.showError('Failed to open PDF');
              }
            }));
            attDiv.appendChild(chip);
          } else {
            // Non-PDF: download via fetch+blob+anchor — browser-native download
            // with target=_blank was unreliable in some browsers (the click did
            // nothing). The blob path forces a real Save dialog every time.
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'email-attachment-chip';
            // Full filename on hover for the chip ellipsis-truncated label.
            chip.title = `Download ${att.filename}`;
            chip.innerHTML = chipHtml;
            chip.addEventListener('click', () => _withSpinner(chip, async () => {
              try {
                const folderQs = encodeURIComponent(fields.sourceFolder || 'INBOX');
                const res = await fetch(`${API_BASE}/api/email/attachment/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const blob = await res.blob();
                downloadBlob(blob, att.filename);
              } catch (e) {
                console.error('Download attachment failed:', e);
                if (uiModule) uiModule.showError('Download failed: ' + e.message);
              }
            }));
            attDiv.appendChild(chip);
          }
        }
      } else {
        attDiv.style.display = 'none';
      }
    }
    if (textarea) {
      textarea.value = fields.body;
      // The textarea remains the plain-text mirror for email send/draft
      // handling; the visible editing surface is the rich body only.
      textarea.style.display = 'none';
      // Store original body for change detection on close
      if (doc) doc._originalBody = fields.body;
      syncHighlighting();
    }
    // WYSIWYG: swap the source editor for the rich body and render the markdown.
    // The textarea above stays as the plain-text mirror (kept in sync below) so
    // send / draft / change-detection still read it.
    const _rich = document.getElementById('doc-email-richbody');
    const _srcWrap = document.getElementById('doc-editor-wrap');
    if (_rich && _srcWrap) {
      _srcWrap.style.display = 'none';
      if (textarea) textarea.style.display = 'none';
      _rich.style.display = '';
      resetEmailStreamState(fields.body || '');
      _rich.innerHTML = _emailBodyToHtml(fields.body);
      _wireEmailRichbody(_rich);
      const openedDocId = activeDocId;
      const focusAtOpen = document.activeElement;
      setTimeout(() => {
        if (!_rich.isConnected || activeDocId !== openedDocId) return;
        const focus = document.activeElement;
        if (focus !== focusAtOpen || focus?.closest('#doc-email-fields') || focus === _rich || _rich.contains(focus)) return;
        try {
          const _isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0;
          if (!_isTouch) _focusEmailBodyEnd();
          _rich.scrollTop = 0;
        } catch (_) {}
      }, 50);
    }
    // Render compose attachments (if any uploaded for this doc)
    _renderComposeAttachments();
    // Populate CC/BCC from parsed header, show rows if populated
    const ccRow = document.getElementById('doc-email-cc-row');
    const bccRow = document.getElementById('doc-email-bcc-row');
    const ccToggle = document.getElementById('doc-email-show-cc');
    _setEmailHeaderInputValue('doc-email-cc', fields.cc || '', { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    _setEmailHeaderInputValue('doc-email-bcc', fields.bcc || '', { preserveFocused: !forceHeaderFields, preserveNonEmpty: preserveEmailHeader && !forceHeaderFields });
    const hasCcBcc = !!(
      fields.cc ||
      fields.bcc ||
      document.getElementById('doc-email-cc')?.value ||
      document.getElementById('doc-email-bcc')?.value
    );
    if (ccRow) ccRow.style.display = hasCcBcc ? '' : 'none';
    if (bccRow) bccRow.style.display = hasCcBcc ? '' : 'none';
    if (ccToggle) ccToggle.style.display = hasCcBcc ? 'none' : '';
    _syncEmailHeaderSummary();
    _stageForwardedSourceAttachments(fields).catch(err => console.error('Forward attachment staging failed:', err));
  }

export function _syncEmailHeaderSummary() {
    const to = document.getElementById('doc-email-to')?.value?.trim() || 'No recipient';
    const subject = document.getElementById('doc-email-subject')?.value?.trim() || 'No subject';
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const summary = document.getElementById('doc-email-collapse-summary');
    if (!summary) return;
    const extras = [];
    if (cc) extras.push('Cc');
    if (bcc) extras.push('Bcc');
    summary.textContent = `${to} · ${subject}${extras.length ? ` · ${extras.join('/')}` : ''}`;
    summary.title = summary.textContent;
  }

export function _syncStreamingEmailFields(doc) {
    if (!doc) return;
    const fields = _parseEmailHeader(doc.content || '');
    const rich = document.getElementById('doc-email-richbody');
    const srcWrap = document.getElementById('doc-editor-wrap');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!rich || rich.style.display === 'none'
        || document.getElementById('doc-email-header')?.dataset.activeDocumentId !== doc.id) {
      _showEmailFields(doc);
      return;
    }

    _setEmailHeaderInputValue('doc-email-to', fields.to, { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-subject', fields.subject, { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-in-reply-to', fields.inReplyTo, { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-references', fields.references, { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-source-uid', fields.sourceUid || '', { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-source-folder', fields.sourceFolder || '', { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-cc', fields.cc || '', { preserveNonEmpty: true });
    _setEmailHeaderInputValue('doc-email-bcc', fields.bcc || '', { preserveNonEmpty: true });

    const unreadBtn = document.getElementById('doc-email-unread-btn');
    if (unreadBtn) unreadBtn.style.display = fields.sourceUid ? '' : 'none';
    const ccRow = document.getElementById('doc-email-cc-row');
    const bccRow = document.getElementById('doc-email-bcc-row');
    const ccToggle = document.getElementById('doc-email-show-cc');
    const hasCcBcc = !!(fields.cc || fields.bcc);
    if (ccRow) ccRow.style.display = hasCcBcc ? '' : 'none';
    if (bccRow) bccRow.style.display = hasCcBcc ? '' : 'none';
    if (ccToggle) ccToggle.style.display = hasCcBcc ? 'none' : '';

    if (srcWrap) srcWrap.style.display = 'none';
    rich.style.display = '';
    _renderStreamingEmailBody(fields.body || '');
    if (doc._originalBody == null) doc._originalBody = fields.body || '';
    _syncEmailHeaderSummary();
  }
