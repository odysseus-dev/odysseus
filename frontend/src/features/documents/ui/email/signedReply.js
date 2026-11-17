import { uiModule, sessionModule } from '../host.js';
import { loadDocument, _autoCreateSession } from '../documentFlows.js';
import { addDocToTabs } from '../../application/documentSession.js';
import { API_BASE, docs, _lastSessionId } from "../../application/documentState.js";

import { _renderComposeAttachments } from "./attachments.js";

export async function _sendSignedReply(docId) {
    const doc = docs.get(docId);
    if (!doc || !doc.sourceEmailUid) return;
    if (uiModule) uiModule.showToast('Preparing signed reply…');
    let result;
    try {
      const res = await fetch(`${API_BASE}/api/document/${encodeURIComponent(docId)}/prepare-signed-reply`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      result = await res.json().catch(() => ({}));
      if (!res.ok || !result.ok) {
        const msg = (result && result.error) || `HTTP ${res.status}`;
        if (uiModule) uiModule.showError(`Couldn't prepare signed reply: ${msg}`);
        return;
      }
    } catch (e) {
      console.error('prepare-signed-reply failed:', e);
      if (uiModule) uiModule.showError("Couldn't prepare signed reply");
      return;
    }

    const att = result.attachment;
    const reply = result.reply || {};
    const mid = reply.source_message_id || doc.sourceEmailMessageId || '';

    // 1) Already have a draft tab open for this source thread? Append.
    for (const [, d] of docs) {
      if (d.language === 'email' && d._draftForMessageId === mid && mid) {
        d._composeAtts = (d._composeAtts || []).concat([att]);
        await loadDocument(d.id);
        _renderComposeAttachments();
        if (uiModule) uiModule.showToast(`Added "${att.filename}" to the reply draft`);
        return;
      }
    }

    // 2) Otherwise create a fresh email draft.
    const headerLines = [
      `To: ${reply.to || ''}`,
      `Subject: ${reply.subject || ''}`,
      reply.in_reply_to ? `In-Reply-To: ${reply.in_reply_to}` : null,
      reply.references ? `References: ${reply.references}` : null,
      reply.source_uid ? `X-Source-UID: ${reply.source_uid}` : null,
      reply.source_folder ? `X-Source-Folder: ${reply.source_folder}` : null,
    ].filter(Boolean);
    const content = headerLines.join('\n') + '\n---\n\nHi' + (reply.to_name ? ' ' + reply.to_name.split(/\s+/)[0] : '') + ',\n\nPlease find the signed copy attached.\n\nBest,\n';

    let draftId = null;
    try {
      // Use the source PDF's session if available; else fall back to current.
      let sessionId = doc.sessionId
        || _lastSessionId
        || (sessionModule && sessionModule.getCurrentSessionId());
      if (!sessionId) {
        try { sessionId = await _autoCreateSession(); } catch (_) {}
      }
      const cRes = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          session_id: sessionId,
          title: reply.subject || 'Signed reply',
          language: 'email',
          content,
        }),
      });
      const created = await cRes.json();
      draftId = created && (created.id || created.doc_id);
      if (!draftId) throw new Error('No draft id returned');
    } catch (e) {
      console.error('Failed to create draft doc:', e);
      if (uiModule) uiModule.showError("Couldn't create reply draft");
      return;
    }

    // Tag the draft (in-memory only) with the thread message-id so future
    // signed PDFs from the same email get appended to this same draft.
    addDocToTabs({
      id: draftId,
      title: reply.subject || 'Signed reply',
      language: 'email',
      current_content: content,
      version_count: 1,
    }, doc.sessionId);
    const draft = docs.get(draftId);
    if (draft) {
      draft._composeAtts = [att];
      draft._draftForMessageId = mid;
      if (reply.account_id) draft._draftAccountId = reply.account_id;
    }

    await loadDocument(draftId);
    _renderComposeAttachments();
    if (uiModule) uiModule.showToast(`Reply draft ready — "${att.filename}" attached`);
  }
