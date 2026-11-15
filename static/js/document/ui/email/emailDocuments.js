import { _scheduleAutoSave, _markDocumentDirty } from '../../application/persistence.js';
import { getChatDocumentId, _autoCreateSession, injectFreshDoc } from '../documentFlows.js';
import { sessionModule, uiModule } from '../host.js';
import { _emailRichbodyActive } from '../richEditor/richBodySync.js';
import { API_BASE, activeDocId, docs, _lastSessionId } from "../../application/documentState.js";

import { _parseEmailHeader, _buildEmailContent, _buildEmailContentFromFields } from "../../domain/emailMessage.js";

import { _setEmailBodyText, _splitEmailReplyQuote, _emailBodyToHtml, _emailReplyOwnText } from "./emailBody.js";

import { _showEmailFields } from "./composerFields.js";

export async function ensureEmailDraftEnvelope(docId, freshContent) {
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return false;
    const current = _parseEmailHeader(doc.content || '');
    const fresh = _parseEmailHeader(freshContent || '');
    if (!fresh.to && !fresh.subject && !fresh.sourceUid) return false;

    const needsEnvelope = (
      (!current.to && !!fresh.to) ||
      (!current.subject && !!fresh.subject) ||
      (!current.inReplyTo && !!fresh.inReplyTo) ||
      (!current.references && !!fresh.references) ||
      (!current.sourceUid && !!fresh.sourceUid) ||
      (!current.sourceFolder && !!fresh.sourceFolder)
    );

    const currentSplit = _splitEmailReplyQuote(current.body || '');
    const freshSplit = _splitEmailReplyQuote(fresh.body || '');
    const currentOwnText = String(currentSplit.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const freshOwnText = String(freshSplit.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const currentBodyText = String(current.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const freshBodyText = String(fresh.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const needsBodyRepair = (
      !!freshBodyText &&
      (
        !currentBodyText ||
        (!currentOwnText && !!freshOwnText) ||
        (!currentSplit.quote && !!freshSplit.quote)
      )
    );
    if (!needsEnvelope && !needsBodyRepair) return false;

    let body = current.body || '';
    if (needsBodyRepair && (!currentBodyText || (!currentOwnText && !!freshOwnText))) {
      body = fresh.body || '';
    } else if (!String(body).trim()) {
      body = fresh.body || '';
    } else if (currentSplit.body && freshSplit.quote && !currentSplit.quote) {
      body = `${currentSplit.body}\n\n${freshSplit.quote}`;
    }

    const merged = {
      ...fresh,
      to: current.to || fresh.to || '',
      cc: current.cc || fresh.cc || '',
      bcc: current.bcc || fresh.bcc || '',
      subject: current.subject || fresh.subject || '',
      inReplyTo: current.inReplyTo || fresh.inReplyTo || '',
      references: current.references || fresh.references || '',
      sourceUid: current.sourceUid || fresh.sourceUid || '',
      sourceFolder: current.sourceFolder || fresh.sourceFolder || '',
      forwardAttachments: current.forwardAttachments || fresh.forwardAttachments || false,
      attachments: (current.attachments && current.attachments.length) ? current.attachments : (fresh.attachments || []),
    };

    doc.content = _buildEmailContentFromFields(merged, body);
    if (activeDocId === docId) {
      _showEmailFields(doc, { applyLocalDraft: false });
    }
    _markDocumentDirty(docId);
    _scheduleAutoSave(docId, 800);
    return true;
  }

export function findEmailDocId(uid, folder) {
    if (uid == null) return null;
    const wantUid = String(uid);
    const wantFolder = (folder || '').trim();
    for (const [id, d] of docs) {
      if (d.language !== 'email') continue;
      const fields = _parseEmailHeader(d.content || '');
      if (fields.sourceUid && String(fields.sourceUid) === wantUid &&
          (!wantFolder || (fields.sourceFolder || '').trim() === wantFolder)) {
        return id;
      }
    }
    return null;
  }

export function getActiveEmailComposerContext() {
    const docId = getChatDocumentId();
    if (!docId) return null;
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return null;
    const fields = _parseEmailHeader(doc.content || '');
    return {
      docId,
      sourceUid: fields.sourceUid || '',
      sourceFolder: fields.sourceFolder || 'INBOX',
      inReplyTo: fields.inReplyTo || '',
      to: fields.to || '',
      subject: fields.subject || '',
    };
  }

export async function openEmailDraft(data = {}) {
    let sessionId = _lastSessionId || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try { sessionId = await _autoCreateSession(); } catch (_) {}
    }
    if (!sessionId) throw new Error('Could not open draft: no session');
    const content = _buildEmailContent(
      data.to || '',
      data.subject || '',
      data.in_reply_to || data.inReplyTo || '',
      data.references || '',
      data.body || '',
      data.source_uid || data.sourceUid || '',
      data.source_folder || data.sourceFolder || '',
      data.cc || '',
      data.bcc || '',
    );
    const res = await fetch(`${API_BASE}/api/document`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        session_id: sessionId,
        title: data.subject || 'Email draft',
        content,
        language: 'email',
      }),
    });
    if (!res.ok) throw new Error(`Draft open failed: HTTP ${res.status}`);
    const doc = await res.json();
    if (!doc?.id) throw new Error('Draft open failed: missing document');
    doc._emailDraftUid = data.uid || data.draft_uid || null;
    doc._emailDraftFolder = data.folder || data.draft_folder || null;
    await injectFreshDoc(doc);
    return doc.id;
  }

export async function replaceEmailReplyBody(docId, replyText, { force = false } = {}) {
    const doc = docs.get(docId);
    if (!doc) return;
    const fields = _parseEmailHeader(doc.content || '');
    const oldSplit = _splitEmailReplyQuote(fields.body || '');
    const quote = oldSplit.quote;
    const ownText = _emailReplyOwnText(fields.body || '');
    const initialBody = fields.body || '';
    if (!force && ownText && !/^(\[AI reply draft will appear here\]|Drafting AI reply)/i.test(ownText)) {
      if (uiModule) uiModule.showToast('AI reply ready, but draft was edited');
      return;
    }
    const body = String(replyText || '').trim() + (quote ? `\n\n${quote}` : '');
    if (activeDocId === docId) {
      const textarea = document.getElementById('doc-editor-textarea');
      const rich = _emailRichbodyActive();
      if (textarea && (
        textarea.value !== initialBody ||
        (rich && rich.innerHTML !== _emailBodyToHtml(initialBody))
      )) {
        if (uiModule) uiModule.showToast('AI reply ready, but draft was edited');
        return;
      }
    }
    doc.content = _buildEmailContent(
      fields.to,
      fields.subject,
      fields.inReplyTo,
      fields.references,
      body,
      fields.sourceUid,
      fields.sourceFolder,
      fields.cc,
      fields.bcc,
    );
    if (activeDocId === docId) {
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) _setEmailBodyText(textarea, body);
    }
    _markDocumentDirty(docId);
    _scheduleAutoSave(docId, 800);
  }
