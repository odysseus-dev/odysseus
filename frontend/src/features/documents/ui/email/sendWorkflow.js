import { _cancelAutoSave } from '../../application/persistence.js';
import { _visibleDocIdsForCurrentSession } from '../documentFlows.js';
import { API_BASE, activeDocId, isOpen, docs, setActiveDocId, removeDocument, markDeleted } from "../../application/documentState.js";
import { closePanel, switchToDoc, renderTabs, _syncDocIndicator } from "../../application/documentCommands.js";

import { _accountCanSend, _ATTACH_RE } from "../../domain/emailMessage.js";

import { _emailReplyOwnText } from "./emailBody.js";

import { _clearEmailLocalDraft, _sanitizeOutgoingEmailBody } from "./localDraft.js";
import { _emailPlainTextToHtml, _emailRichbodyActive, _syncEmailRichbody } from "../richEditor/richBodySync.js";
import { _closeWithoutDeleting } from "../documentFlows.js";
import { closeLibrary, isLibraryOpen, loadEmailLibrary, spinnerModule, uiModule } from "../host.js";

function _bodyMentionsAttachment(text) {
    if (!text) return false;
    // WYSIWYG replies render quoted mail as a non-editable block, so the
    // plain-text mirror does not always retain a leading `>` or an `On …
    // wrote:` line. Reuse the full reply parser to exclude every quote form.
    return _ATTACH_RE.test(_emailReplyOwnText(text));
  }

function _clearMissingAttachmentWarnings() {
    document.querySelectorAll('.email-attachment-warning-modal').forEach(el => el.remove());
  }

function _confirmMissingAttachment() {
    return new Promise(resolve => {
      _clearMissingAttachmentWarnings();
      const overlay = document.createElement('div');
      overlay.className = 'modal email-attachment-warning-modal';
      overlay.style.display = 'flex';
      overlay.innerHTML = `
        <div class="modal-content" style="width:360px;max-width:90vw;">
          <div class="modal-header"><h4>No attachments found</h4></div>
          <div class="modal-body" style="padding:16px;font-size:13px;opacity:0.8;">
            Your message mentions an attachment, but nothing is attached. Send anyway?
          </div>
          <div class="modal-footer" style="display:flex;gap:8px;justify-content:flex-end;">
            <button class="memory-toolbar-btn" id="att-warn-cancel">Go back</button>
            <button class="memory-toolbar-btn" id="att-warn-send" style="background:var(--accent-primary,var(--red));color:#fff;border-color:var(--accent-primary,var(--red));">Send anyway</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const cleanup = (val) => { overlay.remove(); resolve(val); };
      overlay.querySelector('#att-warn-cancel').addEventListener('click', () => cleanup(false));
      overlay.querySelector('#att-warn-send').addEventListener('click', () => {
        _clearMissingAttachmentWarnings();
        resolve(true);
      });
      overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(false); });
    });
  }

export function _discardEmail() {
    if (!activeDocId) return;
    // Just close — the Draft button handles saving explicitly
    _closeWithoutDeleting(true);
  }

let _emailAccountsCache = null;

let _emailAccountsCacheAt = 0;

let _emailSendInFlight = false;

async function _getEmailAccountsCached() {
    const now = Date.now();
    if (_emailAccountsCache && (now - _emailAccountsCacheAt) < 30000) return _emailAccountsCache;
    try {
      const res = await fetch(`${API_BASE}/api/email/accounts`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('accounts failed');
      const data = await res.json();
      _emailAccountsCache = Array.isArray(data.accounts) ? data.accounts : [];
    } catch (_) {
      _emailAccountsCache = [];
    }
    _emailAccountsCacheAt = now;
    return _emailAccountsCache;
  }

export async function _markUnreadAndClose() {
    const docId = activeDocId;
    const doc = docs.get(docId);
    const sourceUid = document.getElementById('doc-email-source-uid')?.value || '';
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value || 'INBOX';
    if (sourceUid) {
      try {
        await fetch(`${API_BASE}/api/email/mark-unread/${sourceUid}?folder=${encodeURIComponent(sourceFolder)}`, { method: 'POST' });
      } catch (e) { console.error('Failed to mark unread:', e); }
    }
    if (activeDocId === docId && docs.get(docId) === doc) _discardEmail();
  }

async function _resolveComposeSendAccountId() {
    const activeAccountId = window.__odysseusActiveEmailAccount || null;
    if (!activeAccountId) return null;
    const accounts = await _getEmailAccountsCached();
    const activeAccount = accounts.find(a => String(a.id) === String(activeAccountId));
    if (!activeAccount || _accountCanSend(activeAccount)) return activeAccountId;
    if (uiModule) uiModule.showToast('Selected email account is receive-only; using your SMTP account.');
    return null;
  }

export async function _saveDraft() {
    const docId = activeDocId;
    if (!docId) return;
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return;
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const textarea = document.getElementById('doc-editor-textarea');
    const rawBody = (_rich ? (_rich.innerText || _rich.textContent || '') : (textarea?.value || '')).trim();
    const body = _sanitizeOutgoingEmailBody(rawBody, { plainText: Boolean(_rich) });
    let bodyHtml = _rich ? _rich.innerHTML : null;
    if (_rich && body !== rawBody) bodyHtml = _emailPlainTextToHtml(body);
    const btn = document.getElementById('doc-email-draft-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const res = await fetch(`${API_BASE}/api/email/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          to: to || '',
          cc: cc || null,
          bcc: bcc || null,
          subject: subject || '',
          body: body || '',
          body_html: bodyHtml,
          in_reply_to: inReplyTo || null,
          references: references || null,
          account_id: window.__odysseusActiveEmailAccount || null,
          attachments: (doc._composeAtts || []).map(a => a.token),
          draft_uid: doc._emailDraftUid || null,
          draft_folder: doc._emailDraftFolder || null,
        }),
      });
      const data = await res.json();
      if (data.success) {
        const savedDoc = docs.get(docId);
        if (savedDoc && savedDoc === doc) {
          savedDoc._emailDraftUid = data.draft_uid || null;
          savedDoc._emailDraftFolder = data.draft_folder || null;
        }
        if (uiModule) uiModule.showToast('Draft saved to mailbox');
      } else {
        if (uiModule) uiModule.showError(data.error || 'Failed to save draft');
      }
    } catch (e) {
      const timedOut = e && e.name === 'AbortError';
      if (uiModule) uiModule.showError(timedOut ? 'Saving draft timed out' : 'Failed to save draft');
    } finally {
      clearTimeout(timeout);
      if (btn) { btn.disabled = false; btn.textContent = 'Draft'; }
    }
  }

async function _saveEmailDraftForRecovery({
    accountId, doc, to, cc, bcc, subject, body, bodyHtml, inReplyTo, references, attachments,
  }) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const res = await fetch(`${API_BASE}/api/email/draft`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          to: to || '',
          cc: cc || null,
          bcc: bcc || null,
          subject: subject || '',
          body: body || '',
          body_html: bodyHtml,
          in_reply_to: inReplyTo || null,
          references: references || null,
          account_id: accountId || null,
          attachments: attachments?.length ? attachments : null,
          draft_uid: doc?._emailDraftUid || null,
          draft_folder: doc?._emailDraftFolder || null,
        }),
      });
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok || !data?.success) {
        throw new Error(data?.error || `Draft save failed (${res.status})`);
      }
      if (doc) {
        doc._emailDraftUid = data.draft_uid || doc._emailDraftUid || null;
        doc._emailDraftFolder = data.draft_folder || doc._emailDraftFolder || null;
      }
      return data;
    } catch (e) {
      if (e?.name === 'AbortError') throw new Error('Saving draft timed out');
      throw e;
    } finally {
      clearTimeout(timeout);
    }
  }

export async function _scheduleSend(anchorEl = null) {
    const sendDocId = activeDocId;
    if (!sendDocId) return;
    const doc = docs.get(sendDocId);
    if (!doc || doc.language !== 'email') return;
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim();
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const rawBody = (_rich
      ? (_rich.innerText || _rich.textContent || '')
      : (document.getElementById('doc-editor-textarea')?.value || '')
    ).trim();
    const body = _sanitizeOutgoingEmailBody(rawBody, { plainText: Boolean(_rich) });
    const attachments = (doc._composeAtts || []).map(a => a.token);

    if (!to || !body) {
      if (uiModule) uiModule.showError('To and body are required');
      return;
    }
    if (inReplyTo && !_emailReplyOwnText(body)) {
      if (uiModule) uiModule.showError('Reply body is empty');
      return;
    }
    if (attachments.length === 0 && _bodyMentionsAttachment(body)) {
      const proceed = await _confirmMissingAttachment();
      if (!proceed) return;
      _clearMissingAttachmentWarnings();
    }

    // Create a small modal with datetime input and quick presets
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
      <div class="modal-content schedule-send-modal" style="width:400px;max-width:92vw;">
        <div class="modal-header">
          <h4>Schedule Send</h4>
          <button class="close-btn" id="sched-close" title="Close"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div class="modal-body schedule-send-body">
          <label class="schedule-send-label">Quick presets</label>
          <div class="schedule-send-presets">
            <button class="memory-toolbar-btn" data-preset="1h">In 1 hour</button>
            <button class="memory-toolbar-btn" data-preset="3h">In 3 hours</button>
            <button class="memory-toolbar-btn" data-preset="tomorrow">Tomorrow 9am</button>
            <button class="memory-toolbar-btn" data-preset="monday">Monday 9am</button>
          </div>
          <label class="schedule-send-label" for="sched-datetime">Or pick a specific time</label>
          <input type="datetime-local" id="sched-datetime" class="schedule-send-datetime" />
        </div>
        <div class="modal-footer schedule-send-footer">
          <button class="memory-toolbar-btn" id="sched-cancel">Cancel</button>
          <button class="memory-toolbar-btn schedule-send-confirm" id="sched-confirm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>Schedule</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const modalContent = overlay.querySelector('.schedule-send-modal');
    const anchor = anchorEl || document.getElementById('doc-email-send-caret') || document.getElementById('doc-email-send-btn');
    if (modalContent && anchor) {
      const rect = anchor.getBoundingClientRect();
      const gap = 8;
      const width = Math.min(400, Math.max(280, window.innerWidth - 16));
      modalContent.style.width = `${width}px`;
      modalContent.style.position = 'fixed';
      modalContent.style.margin = '0';
      modalContent.style.transform = 'none';
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
      const belowTop = rect.bottom + gap;
      const estimatedHeight = Math.min(320, window.innerHeight - 16);
      const top = belowTop + estimatedHeight <= window.innerHeight - 8
        ? belowTop
        : Math.max(8, rect.top - estimatedHeight - gap);
      modalContent.style.left = `${left}px`;
      modalContent.style.top = `${top}px`;
    }

    const dtInput = overlay.querySelector('#sched-datetime');
    // Default to 1 hour from now
    const now = new Date(Date.now() + 60 * 60 * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    dtInput.value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;

    const escHandler = (e) => { if (e.key === 'Escape') cleanup(); };
    const cleanup = () => {
      overlay.remove();
      document.removeEventListener('keydown', escHandler);
    };
    overlay.querySelector('#sched-close').addEventListener('click', cleanup);
    overlay.querySelector('#sched-cancel').addEventListener('click', cleanup);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(); });
    document.addEventListener('keydown', escHandler);

    overlay.querySelectorAll('[data-preset]').forEach(btn => {
      btn.addEventListener('click', () => {
        const preset = btn.getAttribute('data-preset');
        const d = new Date();
        if (preset === '1h') d.setHours(d.getHours() + 1);
        else if (preset === '3h') d.setHours(d.getHours() + 3);
        else if (preset === 'tomorrow') { d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); }
        else if (preset === 'monday') {
          const daysUntilMon = (8 - d.getDay()) % 7 || 7;
          d.setDate(d.getDate() + daysUntilMon);
          d.setHours(9, 0, 0, 0);
        }
        dtInput.value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
      });
    });

    overlay.querySelector('#sched-confirm').addEventListener('click', async () => {
      const localDt = dtInput.value;
      if (!localDt) { if (uiModule) uiModule.showError('Please pick a time'); return; }
      // Convert local datetime to UTC ISO
      const utcIso = new Date(localDt).toISOString();
      try {
        const activeAccountId = await _resolveComposeSendAccountId();
        const res = await fetch(`${API_BASE}/api/email/schedule`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            to, cc: cc || null, bcc: bcc || null, subject, body,
            in_reply_to: inReplyTo || null,
            references: references || null,
            attachments: attachments.length > 0 ? attachments : null,
            send_at: utcIso,
            account_id: activeAccountId,
          }),
        });
        const data = await res.json();
        if (data.success) {
          _clearMissingAttachmentWarnings();
          if (uiModule) uiModule.showToast(`Scheduled for ${new Date(localDt).toLocaleString()}`);
          if (sourceUid) {
            _clearEmailLocalDraft(sourceUid, sourceFolder, inReplyTo || '');
          }
          cleanup();
          // Close only the originating email document
          if (sendDocId && docs.get(sendDocId) === doc) {
            markDeleted(sendDocId);
            _cancelAutoSave(sendDocId);
            fetch(`${API_BASE}/api/document/${sendDocId}`, { method: 'DELETE' }).catch(() => {});
            const wasActiveSentDoc = isOpen && activeDocId === sendDocId;
            removeDocument(sendDocId);
            if (wasActiveSentDoc) {
              setActiveDocId(null);
              const nextId = _visibleDocIdsForCurrentSession().find(id => docs.has(id));
              if (nextId) switchToDoc(nextId);
              else closePanel();
            } else {
              renderTabs();
            }
            _syncDocIndicator();
          }
        } else {
          if (uiModule) uiModule.showError(data.error || 'Failed to schedule');
        }
      } catch (e) {
        if (uiModule) uiModule.showError('Failed to schedule');
      }
    });
  }

export async function _sendEmail() {
    if (_emailSendInFlight) {
      if (uiModule) uiModule.showToast('Already sending');
      return;
    }
    if (uiModule) uiModule.showToast('Preparing send', { duration: 1200 });
    const sendDocId = activeDocId;
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim();
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
    // WYSIWYG: the rich body's HTML becomes the email's HTML part (server
    // sanitizes it). `body` (plain text mirror) stays the text/plain fallback.
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const textarea = document.getElementById('doc-editor-textarea');
    const rawBody = (_rich ? (_rich.innerText || _rich.textContent || '') : (textarea?.value || '')).trim();
    const body = _sanitizeOutgoingEmailBody(rawBody, { plainText: Boolean(_rich) });
    let bodyHtml = _rich ? _rich.innerHTML : null;
    if (_rich && body !== rawBody) bodyHtml = _emailPlainTextToHtml(body);
    const doc = docs.get(activeDocId);
    const attachments = (doc?._composeAtts || []).map(a => a.token);
    if (!to || !body) {
      if (uiModule) uiModule.showError('To and body are required');
      return;
    }
    if (inReplyTo && !_emailReplyOwnText(body)) {
      if (uiModule) uiModule.showError('Reply body is empty');
      return;
    }
    // Warn if body mentions attachments but none are actually attached
    if (attachments.length === 0 && _bodyMentionsAttachment(body)) {
      const proceed = await _confirmMissingAttachment();
      if (!proceed) return;
      _clearMissingAttachmentWarnings();
    }
    const btn = Array.from(document.querySelectorAll('#doc-email-send-btn')).find((candidate) => candidate.offsetParent !== null) || document.getElementById('doc-email-send-btn');
    let sendSpinner = null;
    let origBtnHtml = '';
    let recoveryDraftSaved = Boolean(doc?._emailDraftUid);
    _emailSendInFlight = true;
    if (btn) {
      btn.disabled = true;
      origBtnHtml = btn.innerHTML;
      sendSpinner = spinnerModule.createWhirlpool(14);
      sendSpinner.element.style.cssText = 'display:inline-block;vertical-align:-2px;margin-right:6px;width:14px;height:14px;';
      btn.innerHTML = '';
      btn.appendChild(sendSpinner.element);
      btn.appendChild(document.createTextNode('Sending'));
    }
    try {
      if (uiModule) uiModule.showToast('Sending', { duration: 2200, leadingIcon: 'spinner', toastClass: 'toast-sending' });

      const activeAccountIdPromise = _resolveComposeSendAccountId();
      const activeAccountId = await activeAccountIdPromise;
      // Do this before SMTP can start. If saving the recovery copy fails, do
      // not send an email that the user cannot reopen after a disconnect.
      const recoveryDraft = await _saveEmailDraftForRecovery({
        accountId: activeAccountId,
        doc,
        to,
        cc,
        bcc,
        subject,
        body,
        bodyHtml,
        inReplyTo,
        references,
        attachments,
      });
      recoveryDraftSaved = true;
      const sendRequest = fetch(`${API_BASE}/api/email/send`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to, cc: cc || null, bcc: bcc || null, subject, body, body_html: bodyHtml,
          in_reply_to: inReplyTo || null, references: references || null,
          attachments: attachments.length > 0 ? attachments : null,
          account_id: activeAccountId,
          source_uid: sourceUid || null,
          source_folder: sourceFolder || null,
          draft_uid: recoveryDraft.draft_uid || doc?._emailDraftUid || null,
          draft_folder: recoveryDraft.draft_folder || doc?._emailDraftFolder || null,
          wait_for_delivery: true,
        }),
      });
      // Remote SMTP/IMAP confirmation can take several seconds. The request
      // is now committed, so close the composer while the captured document
      // remains available as the recovery draft until delivery is confirmed.
      if (isLibraryOpen()) closeLibrary();
      if (isOpen && activeDocId === sendDocId) closePanel();
      const res = await sendRequest;
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = { success: false, error: `Send failed (${res.status})` };
      }
      if (!res.ok && data && !data.error) data.error = `Send failed (${res.status})`;
      if (data.success) {
        _clearMissingAttachmentWarnings();
        // The send endpoint appends the message to Sent, but an already-open
        // email library needs an explicit fresh load to show it immediately.
        loadEmailLibrary().then(mod => {
          const refresh = mod.refreshEmailLibrary || (mod.default && mod.default.refreshEmailLibrary);
          if (refresh) return refresh();
          return undefined;
        }).catch(() => {});
        if (uiModule) {
          uiModule.showToast('Message sent', {
            duration: 7000,
            leadingIcon: 'check',
            toastClass: 'toast-message-sent',
            action: 'View Message',
            onAction: async () => {
              loadEmailLibrary().then(async mod => {
                const open = mod.openEmailLibrary || (mod.default && mod.default.openEmailLibrary);
                const refresh = mod.refreshEmailLibrary || (mod.default && mod.default.refreshEmailLibrary);
                if (open) open({
                  account_id: data.account_id || activeAccountId || null,
                  folder: data.sent_folder || 'Sent',
                  uid: data.sent_uid || null,
                });
                // The new message is not guaranteed to be in the cached Sent
                // snapshot used during open. Refresh after the modal has
                // mounted so the pending UID can expand when the server list
                // returns, without requiring a second manual refresh click.
                if (refresh) {
                  await new Promise(resolve => setTimeout(resolve, 0));
                  await refresh();
                }
              }).catch(() => {});
            },
          });
        }
        // Auto-save recipients to the configured contacts backend (CardDAV).
        // The compose fields accept plain emails and "Name <email>" chips.
        const _contactPieces = [to, cc, bcc].join(',').split(/[,;]/).map(s => s.trim()).filter(Boolean);
        const _seenContacts = new Set();
        for (const piece of _contactPieces) {
          const match = piece.match(/^(.*?)<([^>]+)>$/);
          const email = (match ? match[2] : piece).trim();
          const name = (match ? match[1] : '').replace(/^["']|["']$/g, '').trim();
          if (!email || !/@/.test(email)) continue;
          const key = email.toLowerCase();
          if (_seenContacts.has(key)) continue;
          _seenContacts.add(key);
          fetch(`${API_BASE}/api/contacts/add`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email }),
          }).catch(() => {});
        }
        // Mark the source email as answered if this was a reply
        if (sourceUid) {
          _clearEmailLocalDraft(sourceUid, sourceFolder, inReplyTo);
          const markParams = new URLSearchParams({ folder: sourceFolder });
          if (data.account_id || activeAccountId) markParams.set('account_id', data.account_id || activeAccountId);
          fetch(`${API_BASE}/api/email/mark-answered/${encodeURIComponent(sourceUid)}?${markParams.toString()}`, { method: 'POST' }).catch(() => {});
          // Tell the inbox to refresh so the answered state shows
          window.dispatchEvent(new CustomEvent('email-answered', { detail: { uid: sourceUid, folder: sourceFolder, account_id: data.account_id || activeAccountId || null } }));
        }
        // Delete the compose document after successful send.
        if (sendDocId) {
          markDeleted(sendDocId);
          _cancelAutoSave(sendDocId);
          fetch(`${API_BASE}/api/document/${sendDocId}`, { method: 'DELETE' }).catch(() => {});
          const wasActiveSentDoc = isOpen && activeDocId === sendDocId;
          removeDocument(sendDocId);
          if (wasActiveSentDoc) {
            setActiveDocId(null);
            const nextId = _visibleDocIdsForCurrentSession().find(id => docs.has(id));
            if (nextId) switchToDoc(nextId);
            else closePanel();
          } else {
            renderTabs();
          }
          _syncDocIndicator();
        }
      } else {
        if (uiModule) uiModule.showError(`${data.error || 'Failed to send'} Draft kept in Drafts.`);
      }
    } catch (e) {
      if (uiModule) {
        const reason = e?.message || 'connection lost';
        uiModule.showError(recoveryDraftSaved
          ? `Send failed: ${reason}. Draft kept in Drafts.`
          : `Send cancelled: ${reason}. Could not save a recovery draft.`);
      }
    } finally {
      _emailSendInFlight = false;
      if (sendSpinner) sendSpinner.destroy();
      if (btn) {
        btn.disabled = false;
        if (origBtnHtml) btn.innerHTML = origBtnHtml;
      }
    }
  }
