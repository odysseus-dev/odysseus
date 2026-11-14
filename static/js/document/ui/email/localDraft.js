import { activeDocId, docs } from "../../application/documentState.js";

import {
  _decodeBase64EmailWrapper,
  _looksLikeWrappedEmailContent,
  _parseEmailHeader
} from "../../domain/emailMessage.js";

const _EMAIL_LOCAL_DRAFT_PREFIX = 'odysseus.email.replyDraft.v1:';

export function _clearCurrentEmailLocalDraft() {
    _clearEmailLocalDraft(
      document.getElementById('doc-email-source-uid')?.value || '',
      document.getElementById('doc-email-source-folder')?.value || 'INBOX',
      document.getElementById('doc-email-in-reply-to')?.value || '',
    );
  }

export function _clearEmailLocalDraft(sourceUid, sourceFolder, inReplyTo) {
    const key = _emailLocalDraftKey(sourceUid, sourceFolder, inReplyTo);
    if (!key) return;
    try { localStorage.removeItem(key); } catch (_) {}
  }

export function _emailFieldsWithLocalDraft(fields) {
    const draft = _loadEmailLocalDraft(fields);
    if (!draft) return fields;
    const keepRealField = (draftValue, fieldValue) => {
      const d = draftValue == null ? '' : String(draftValue);
      return d.trim() ? d : (fieldValue || '');
    };
    return {
      ...fields,
      to: keepRealField(draft.to, fields.to),
      cc: keepRealField(draft.cc, fields.cc),
      bcc: keepRealField(draft.bcc, fields.bcc),
      subject: keepRealField(draft.subject, fields.subject),
      inReplyTo: keepRealField(draft.inReplyTo, fields.inReplyTo),
      references: keepRealField(draft.references, fields.references),
      sourceUid: keepRealField(draft.sourceUid, fields.sourceUid),
      sourceFolder: keepRealField(draft.sourceFolder, fields.sourceFolder),
      body: _sanitizeOutgoingEmailBody(draft.body ?? fields.body),
    };
  }

export let _emailLocalDraftDebounce = null;

function _emailLocalDraftKey(sourceUid, sourceFolder, inReplyTo) {
    const uid = String(sourceUid || '').trim();
    if (!uid) return '';
    const folder = String(sourceFolder || 'INBOX').trim() || 'INBOX';
    const msg = String(inReplyTo || '').trim();
    return _EMAIL_LOCAL_DRAFT_PREFIX + encodeURIComponent(`${folder}|${uid}|${msg}`);
  }

function _loadEmailLocalDraft(fields) {
    const key = _emailLocalDraftKey(fields?.sourceUid, fields?.sourceFolder, fields?.inReplyTo);
    if (!key) return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const draft = JSON.parse(raw);
      if (!draft || typeof draft !== 'object') return null;
      const updatedAt = Number(draft.updatedAt || 0);
      if (updatedAt && Date.now() - updatedAt > 45 * 24 * 60 * 60 * 1000) {
        localStorage.removeItem(key);
        return null;
      }
      return draft;
    } catch (_) {
      return null;
    }
  }

export function _persistEmailLocalDraftNow() {
    const doc = activeDocId && docs.get(activeDocId);
    if (!doc || doc.language !== 'email') return;
    const sourceUid = document.getElementById('doc-email-source-uid')?.value || '';
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value || 'INBOX';
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value || '';
    const key = _emailLocalDraftKey(sourceUid, sourceFolder, inReplyTo);
    if (!key) return;
    const rich = document.getElementById('doc-email-richbody');
    const textarea = document.getElementById('doc-editor-textarea');
    const body = (rich && rich.style.display !== 'none') ? rich.innerHTML : (textarea?.value || '');
    const payload = {
      to: document.getElementById('doc-email-to')?.value || '',
      cc: document.getElementById('doc-email-cc')?.value || '',
      bcc: document.getElementById('doc-email-bcc')?.value || '',
      subject: document.getElementById('doc-email-subject')?.value || '',
      inReplyTo,
      references: document.getElementById('doc-email-references')?.value || '',
      sourceUid,
      sourceFolder,
      body,
      updatedAt: Date.now(),
    };
    try { localStorage.setItem(key, JSON.stringify(payload)); } catch (_) {}
  }

export function _persistEmailLocalDraftSoon() {
    clearTimeout(_emailLocalDraftDebounce);
    _emailLocalDraftDebounce = setTimeout(_persistEmailLocalDraftNow, 800);
  }

export function _sanitizeOutgoingEmailBody(raw) {
    let text = String(raw || '').replace(/\r\n/g, '\n');
    const trimmed = text.trim();
    const decodedWhole = _decodeBase64EmailWrapper(trimmed);
    if (decodedWhole) text = _parseEmailHeader(decodedWhole).body || '';
    else if (_looksLikeWrappedEmailContent(trimmed)) text = _parseEmailHeader(trimmed).body || '';

    const parts = text.split(/(\n{2,})/);
    let changed = false;
    const clean = parts.map(part => {
      if (/^\n+$/.test(part)) return part;
      const decoded = _decodeBase64EmailWrapper(part);
      if (!decoded) return part;
      changed = true;
      return _parseEmailHeader(decoded).body || '';
    }).join('');

    if (!changed && /<[^>]+>/.test(text) && typeof document !== 'undefined') {
      const probe = document.createElement('template');
      probe.innerHTML = text;
      const plain = (probe.content.textContent || '').trim();
      const plainClean = plain ? _sanitizeOutgoingEmailBody(plain) : plain;
      if (plainClean !== plain) return plainClean;
    }

    return (changed ? clean : text)
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
