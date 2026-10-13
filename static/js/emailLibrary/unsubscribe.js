// static/js/emailLibrary/unsubscribe.js
//
// The bulk-unsubscribe review flow: scan `List-Unsubscribe` headers, group
// candidates by sender, and run the mailto/URL/agent-browser unsubscribe plus
// the optional delete-after-unsubscribe cleanup.
//
// Reached from one place — the Clean action in the email-library header — and
// it owns its own localStorage keys (`odysseus.email.unsubscribe*`), its own
// icon set, and the `odysseus:agent-tool-output` listeners that watch an
// agent-browser run finish. Nothing else in the package reads any of that,
// which is why this is the one module with a single exported entry point.

import spinnerModule from '../spinner.js';
import { emailApiUrl } from '../emailShared.js';
import { showToast, styledConfirm } from '../ui.js?v=20260916largetoolscroll1';
import { state } from './state.js';
import { _esc } from './utils.js';
import { _libCacheWriteBack, _renderGrid, isOpen, refreshEmailLibrary } from './index.js';

function _unsubscribeMethodLabel(method) {
  if (!method) return 'No unsubscribe method';
  if (method.kind === 'mailto') return 'Request Unsubscribe';
  if (method.kind === 'url') return 'Link Unsubscribe';
  return method.target || method.kind || 'Unsubscribe';
}

function _unsubscribeFolderLabel(folder) {
  const value = String(folder || 'INBOX').trim();
  return value.toUpperCase() === 'INBOX' ? 'Inbox' : value;
}

function _setUnsubButtonBusy(btn, label) {
  if (!btn) return null;
  const previous = btn.innerHTML;
  const previousDisplay = btn.style.display;
  const previousAlignItems = btn.style.alignItems;
  const previousJustifyContent = btn.style.justifyContent;
  const previousGap = btn.style.gap;
  const previousWhiteSpace = btn.style.whiteSpace;
  btn.disabled = true;
  btn.style.display = 'inline-flex';
  btn.style.alignItems = 'center';
  btn.style.justifyContent = 'center';
  btn.style.gap = '5px';
  btn.style.whiteSpace = 'nowrap';
  btn.innerHTML = '';
  const sp = spinnerModule.createWhirlpool(14);
  sp.element.style.position = 'relative';
  sp.element.style.top = '-2px';
  sp.element.style.flexShrink = '0';
  sp.element.style.margin = '0';
  sp.element.style.display = 'inline-flex';
  btn.appendChild(sp.element);
  const text = document.createElement('span');
  text.textContent = label || 'Working';
  text.className = 'email-unsub-busy-label';
  text.style.whiteSpace = 'nowrap';
  text.style.display = 'inline-block';
  btn.appendChild(text);
  const restore = () => {
    btn.disabled = false;
    btn.innerHTML = previous;
    btn.style.display = previousDisplay;
    btn.style.alignItems = previousAlignItems;
    btn.style.justifyContent = previousJustifyContent;
    btn.style.gap = previousGap;
    btn.style.whiteSpace = previousWhiteSpace;
  };
  restore.setLabel = (next) => { text.textContent = next || label || 'Working'; };
  return restore;
}

const _UNSUB_EMAIL_ICON = '<svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>';
const _UNSUB_CHECK_ICON = '<svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
const _UNSUB_CLOSE_ICON = '<svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
const _UNSUB_AGENT_ICON = '<svg class="email-unsub-agent-icon email-unsub-accent-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>';
const _UNSUB_TRASH_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>';
const _UNSUB_REFRESH_ICON = '<svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15.3-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.3 6.4L3 16"/><path d="M3 21v-5h5"/></svg>';
const _UNSUB_EXTERNAL_ICON = '<svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

function _setUnsubStatusBusy(statusEl, label) {
  if (!statusEl) return null;
  statusEl.classList.remove('is-error');
  statusEl.classList.add('is-busy');
  statusEl.innerHTML = '';
  statusEl.style.display = 'inline-flex';
  statusEl.style.alignItems = 'center';
  statusEl.style.gap = '6px';
  statusEl.style.justifyContent = 'flex-end';
  statusEl.style.width = '100%';
  const text = document.createElement('span');
  text.textContent = label || 'Scanning…';
  statusEl.appendChild(text);
  const sp = spinnerModule.createWhirlpool(14);
  sp.element.style.position = 'relative';
  sp.element.style.top = '0';
  sp.element.style.flexShrink = '0';
  sp.element.style.margin = '0';
  sp.element.style.display = 'inline-flex';
  statusEl.appendChild(sp.element);
  return (next) => {
    statusEl.style.display = '';
    statusEl.style.alignItems = '';
    statusEl.style.gap = '';
    statusEl.style.justifyContent = '';
    statusEl.style.width = '';
    statusEl.classList.remove('is-busy');
    statusEl.textContent = next || '';
  };
}

function _askAgentToUnsubscribe(candidate) {
  const methods = Array.isArray(candidate?.methods) ? candidate.methods : [];
  const recommended = candidate?.recommended_method;
  const method = (recommended && typeof recommended === 'object')
    ? recommended
    : methods.find(m => m.kind === 'url') || methods[0] || null;
  const url = method?.kind === 'url' ? method.target : '';
  const methodIndex = Math.max(0, methods.findIndex(m => (
    m === method || (m?.kind === method?.kind && m?.target === method?.target)
  )));
  const uid = candidate?.uid || '';
  const reviewedUids = _unsubscribeCandidateUids(candidate);
  const folder = candidate?.folder || state._libFolder || 'INBOX';
  const account = state._libAccountId || '';
  const prompt = url
    ? `The user explicitly clicked Agent Unsubscribe for this reviewed email. Use the private_browser tool to open this exact unsubscribe URL and complete only the unsubscribe flow. Do not use web_search, web_fetch, bash, or scan_email_unsubscribes. After the page confirms a successful unsubscribe, use bulk_email action=delete on these exact reviewed UID(s) so they are not rediscovered. If the page is ambiguous or unsubscribe fails, stop and report that clearly so the user can choose Spam + delete; do not delete a different message.\n\nEmail UID(s): ${reviewedUids.join(', ') || uid}\nFolder: ${folder}\nAccount: ${account || '(default)'}\nExact unsubscribe URL: ${url}`
    : `The user explicitly clicked Agent Unsubscribe for this reviewed email. Use unsubscribe_email directly for the already-reviewed candidate below. Do not call scan_email_unsubscribes again.\n\nEmail UID: ${uid}\nFolder: ${folder}\nAccount: ${account || '(default)'}\nReviewed method_index: ${methodIndex}`;
  return _submitAgentUnsubscribePrompt(prompt);
}

function _submitAgentUnsubscribePrompt(prompt) {
  const input = document.getElementById('message') || document.getElementById('message-input');
  const form = document.getElementById('chat-form');
  if (!input || !form) {
    showToast?.('Chat composer not found');
    return false;
  }
  input.value = prompt;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  // Match the working slash-command path. The app-level submit guard can
  // still be settling when this is called from a modal button.
  setTimeout(() => {
    if (!document.body.contains(input) || !document.body.contains(form)) return;
    try {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    } catch (err) {
      console.error('agent unsubscribe submit failed', err);
      showToast?.('Could not start agent unsubscribe');
    }
  }, 350);
  return true;
}

function _agentUnsubscribeGroups(candidates) {
  const groups = new Map();
  (candidates || []).forEach(candidate => {
    const urlMethod = (candidate?.methods || []).find(method => method?.kind === 'url');
    const sender = String(candidate?.from_address || '').trim();
    const key = sender ? `sender:${sender.toLowerCase()}` : `uid:${_unsubscribeCandidateUids(candidate).join(',')}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        subject: String(candidate?.subject || '(no subject)'),
        sender,
        uids: [],
        url: String(urlMethod?.target || ''),
        subjects: [],
      };
      groups.set(key, group);
    }
    _unsubscribeCandidateUids(candidate).forEach(uid => {
      if (!group.uids.includes(uid)) group.uids.push(uid);
    });
    const subject = String(candidate?.subject || '').trim();
    if (subject && !group.subjects.includes(subject)) group.subjects.push(subject);
    if (!group.url && urlMethod?.target) group.url = String(urlMethod.target);
  });
  return Array.from(groups.values());
}

function _askAgentToUnsubscribeAll(candidates) {
  const reviewed = _agentUnsubscribeGroups(candidates)
    .filter(item => item.uids.length || item.url);
  if (!reviewed.length) {
    showToast?.('No remaining unsubscribe candidates');
    return false;
  }
  const account = state._libAccountId || '';
  const lines = reviewed.map((item, index) => (
    `${index + 1}. ${item.sender || '(unknown sender)'} | ${item.subjects.slice(0, 3).join(' / ') || item.subject} | UID(s): ${item.uids.join(', ') || '(none)'} | URL: ${item.url || '(none)'}`
  )).join('\n');
  const prompt = `The user explicitly chose Agent Unsubscribe All for these already-reviewed unsubscribe candidates. They are grouped by sender: open at most one unsubscribe flow per sender and never repeat an unsubscribe attempt for the same sender. Use the private_browser tool for each exact unsubscribe URL. Do not use web_search, web_fetch, bash, or scan_email_unsubscribes. If a page is ambiguous or unsubscribe fails, skip that sender and report it. After a successful unsubscribe, use bulk_email action=delete only for that sender group's exact reviewed UID(s), so those messages are not rediscovered. Do not delete any other messages. Report successful, skipped, and failed senders clearly.\n\nFolder: ${state._libFolder || 'INBOX'}\nAccount: ${account || '(default)'}\n\nReviewed sender groups (${reviewed.length}):\n${lines}`;
  return _submitAgentUnsubscribePrompt(prompt);
}

function _unsubscribeCandidateUids(candidate) {
  const out = [];
  const seen = new Set();
  const add = (uid) => {
    const val = String(uid || '').trim();
    if (!val || seen.has(val)) return;
    seen.add(val);
    out.push(val);
  };
  add(candidate?.uid);
  (candidate?.duplicate_uids || []).forEach(add);
  return out;
}

function _unsubscribeHandledStorageKey() {
  return `odysseus.email.unsubscribeHandled.${String(state._libAccountId || 'default')}`;
}

function _unsubscribeScanStorageKey() {
  const account = String(state._libAccountId || 'default');
  const folder = String(state._libFolder || 'INBOX');
  return `odysseus.email.unsubscribeScan.v2.${account}.${folder}`;
}

function _readUnsubscribeScanCache() {
  try {
    const parsed = JSON.parse(localStorage.getItem(_unsubscribeScanStorageKey()) || 'null');
    return parsed && Array.isArray(parsed.candidates) ? parsed : null;
  } catch (_) {
    return null;
  }
}

function _writeUnsubscribeScanCache(data, scanLimit) {
  if (!data || !Array.isArray(data.candidates)) return;
  try {
    localStorage.setItem(_unsubscribeScanStorageKey(), JSON.stringify({
      candidates: data.candidates,
      scanned: Number(data.scanned || 0),
      raw_total: Number(data.raw_total || data.candidates.length || 0),
      scanLimit: scanLimit == null ? 180 : Number(scanLimit),
      scanMode: Number(scanLimit || 0) > 0 ? 'limited' : 'whole-folder',
      savedAt: Date.now(),
    }));
  } catch (_) {}
}

function _readHandledUnsubscribeState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(_unsubscribeHandledStorageKey()) || '{}');
    return {
      uids: new Set(Array.isArray(parsed.uids) ? parsed.uids.map(String) : []),
      senders: new Set(Array.isArray(parsed.senders) ? parsed.senders.map(v => String(v).toLowerCase()) : []),
      ignoredUids: new Set(Array.isArray(parsed.ignoredUids) ? parsed.ignoredUids.map(String) : []),
      ignoredSenders: new Set(Array.isArray(parsed.ignoredSenders) ? parsed.ignoredSenders.map(v => String(v).toLowerCase()) : []),
    };
  } catch (_) {
    return { uids: new Set(), senders: new Set(), ignoredUids: new Set(), ignoredSenders: new Set() };
  }
}

function _rememberUnsubscribeHandled(candidates) {
  const current = _readHandledUnsubscribeState();
  (candidates || []).forEach(candidate => {
    _unsubscribeCandidateUids(candidate).forEach(uid => current.uids.add(String(uid)));
    const sender = String(candidate?.from_address || '').trim().toLowerCase();
    if (sender) current.senders.add(sender);
  });
  try {
    localStorage.setItem(_unsubscribeHandledStorageKey(), JSON.stringify({
      uids: Array.from(current.uids).slice(-2000),
      senders: Array.from(current.senders).slice(-500),
      ignoredUids: Array.from(current.ignoredUids).slice(-2000),
      ignoredSenders: Array.from(current.ignoredSenders).slice(-500),
    }));
  } catch (_) {}
}

function _rememberUnsubscribeIgnored(candidate) {
  const current = _readHandledUnsubscribeState();
  _unsubscribeCandidateUids(candidate).forEach(uid => current.ignoredUids.add(String(uid)));
  const sender = String(candidate?.from_address || '').trim().toLowerCase();
  if (sender) current.ignoredSenders.add(sender);
  try {
    localStorage.setItem(_unsubscribeHandledStorageKey(), JSON.stringify({
      uids: Array.from(current.uids).slice(-2000),
      senders: Array.from(current.senders).slice(-500),
      ignoredUids: Array.from(current.ignoredUids).slice(-2000),
      ignoredSenders: Array.from(current.ignoredSenders).slice(-500),
    }));
  } catch (_) {}
}

function _filterHandledUnsubscribeCandidates(candidates) {
  const handled = _readHandledUnsubscribeState();
  return (candidates || []).flatMap(candidate => {
    const sender = String(candidate?.from_address || '').trim().toLowerCase();
    if (sender && (handled.senders.has(sender) || handled.ignoredSenders.has(sender))) return [];
    const remainingUids = _unsubscribeCandidateUids(candidate)
      .filter(uid => !handled.uids.has(String(uid)) && !handled.ignoredUids.has(String(uid)));
    if (!remainingUids.length) return [];
    if (remainingUids.length === _unsubscribeCandidateUids(candidate).length) return [candidate];
    return [{
      ...candidate,
      uid: remainingUids[0],
      duplicate_uids: remainingUids.slice(1),
      duplicate_count: remainingUids.length,
    }];
  });
}

function _dedupeUnsubscribeCandidatesForDisplay(candidates) {
  const out = [];
  const seen = new Map();
  (candidates || []).forEach(c => {
    const method = c?.recommended_method || {};
    const urlMethod = (c?.methods || []).find(m => m?.kind === 'url');
    // One sender address should produce one review action, even when every
    // message carries a different tokenized unsubscribe URL.
    const sender = String(c?.from_address || '').trim().toLowerCase();
    const key = sender || String(c?.list_id || urlMethod?.target || method.target || c?.uid || '').trim().toLowerCase();
    if (!key || !seen.has(key)) {
      if (key) seen.set(key, c);
      out.push(c);
      return;
    }
    const existing = seen.get(key);
    existing.duplicate_count = Number(existing.duplicate_count || 1) + Number(c.duplicate_count || 1);
    const uidSet = new Set(_unsubscribeCandidateUids(existing));
    _unsubscribeCandidateUids(c).forEach(uid => uidSet.add(uid));
    existing.duplicate_uids = Array.from(uidSet);
  });
  return out;
}

function _markUnsubscribeCardDone(modal, idx, label = 'Unsubscribed') {
  const card = modal?.querySelector?.(`.email-unsub-card[data-idx="${idx}"]`);
  if (!card) return;
  card.classList.add('is-unsubscribed');
  if (!card.querySelector('.email-unsub-done-badge')) {
    const badgeHost = card.querySelector('.email-unsub-subject-line') || card.querySelector('.email-unsub-card-top');
    badgeHost?.insertAdjacentHTML('beforeend', `<span class="email-tag email-unsub-done-badge">${_UNSUB_CHECK_ICON}<span>${_esc(label)}</span></span>`);
  }
  card.querySelectorAll('.email-unsub-link-btn, .email-unsub-agent-btn, .email-unsub-send-btn, .email-unsub-done-btn, .email-unsub-ignore-btn').forEach(el => {
    if (el.tagName === 'A') {
      el.setAttribute('aria-disabled', 'true');
      el.style.pointerEvents = 'none';
    } else {
      el.disabled = true;
    }
    el.style.opacity = '0.55';
  });
}

function _agentToolArgs(data) {
  const candidates = [data?.args, data?.tool_args, data?.command];
  for (const value of candidates) {
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
    if (typeof value !== 'string' || !value.trim()) continue;
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch (_) {
      // Some tool cards prefix the JSON with a display label. Recover the
      // object so email mutations still reconcile from the streamed event.
      const start = value.indexOf('{');
      const end = value.lastIndexOf('}');
      if (start >= 0 && end > start) {
        try {
          const parsed = JSON.parse(value.slice(start, end + 1));
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
        } catch (_) {}
      }
    }
  }
  return {};
}

function _agentBrowserUnsubscribeSucceeded(data) {
  const tool = String(data?.tool || '').toLowerCase();
  if (!tool.includes('private_browser')) return false;
  const output = String(data?.output || '');
  if (/\b(?:not|never|failed|unable|could not|cannot|can't)\b[^.!?\n]{0,50}\bunsubscrib/i.test(output)) {
    return false;
  }
  return /\b(?:already\s+unsubscribed|you(?:'re|\s+are)\s+(?:now\s+)?unsubscribed|successfully\s+unsubscribed|unsubscribed\s+(?:successfully|from)|unsubscription\s+(?:successful|complete)|removed\s+from\s+(?:the\s+)?(?:mailing|email)\s+list)\b/i.test(output);
}

function _agentBrowserCandidate(run, args) {
  const url = String(args?.url || '').trim();
  if (url && Array.isArray(run?.candidates)) {
    const matched = run.candidates.find(candidate => (
      (candidate?.methods || []).some(method => method?.kind === 'url' && String(method.target || '') === url)
    ));
    if (matched) return matched;
  }
  return run?.activeCandidate || null;
}

function _trackAgentBrowserUnsubscribe(data) {
  const modal = document.getElementById('email-unsubscribe-review-modal');
  const run = modal?._agentUnsubscribeRun;
  if (!run) return;
  const args = _agentToolArgs(data);
  const action = String(args.action || data?.action || '').toLowerCase();
  const candidate = _agentBrowserCandidate(run, args);
  if (action === 'open' && candidate) run.activeCandidate = candidate;
  if (!candidate || !_agentBrowserUnsubscribeSucceeded(data)) return;
  run.cleanupInFlight ||= new Set();
  if (run.cleanupInFlight.has(candidate) || run.completed.has(candidate)) return;
  run.cleanupInFlight.add(candidate);
  _deleteAfterUnsubscribe([candidate]).then(result => {
    if (Number(result?.failed || 0) > 0 || Number(result?.changed || 0) <= 0) {
      run.failed ||= new Set();
      run.failed.add(candidate);
      run.restoreBusy?.setLabel?.(`Unsubscribed, delete failed (${run.completed.size} / ${run.total})`);
      modal.querySelector('.email-unsub-status')?.replaceChildren(document.createTextNode(
        'Unsubscribe succeeded, but the matching email could not be moved to Trash. Review it below.',
      ));
      return;
    }
    const idx = run.candidates.indexOf(candidate);
    run.completed.add(candidate);
    _markUnsubscribeCardDone(modal, idx, 'Unsubscribed');
    run.restoreBusy?.setLabel?.(`Unsubscribed ${run.completed.size} / ${run.total}`);
  }).catch(error => {
    run.failed ||= new Set();
    run.failed.add(candidate);
    run.restoreBusy?.setLabel?.(`Unsubscribed, delete failed (${run.completed.size} / ${run.total})`);
    modal.querySelector('.email-unsub-status')?.replaceChildren(document.createTextNode(
      `Unsubscribe succeeded, but delete failed: ${error?.message || 'mail server error'}`,
    ));
  });
}

function _agentDeletedEmailUids(data) {
  const tool = String(data?.tool || '').toLowerCase();
  const args = _agentToolArgs(data);
  const action = String(args.action || data.action || '').toLowerCase();
  const output = String(data?.output || '').toLowerCase();
  const failed = /\b(?:failed|error|could not|unable|not found)\b/.test(output);
  const isBulkDelete = tool.includes('bulk_email')
    && ['delete', 'trash', 'move_to_trash'].includes(action);
  const isSingleDelete = tool.endsWith('delete_email')
    && !tool.includes('bulk_email');
  const isSuccessfulMailtoUnsubscribe = tool.includes('unsubscribe_email')
    && !failed
    && /(?:source email moved to trash|deleted|unsubscribe email sent)/.test(output);
  if (!isBulkDelete && !isSingleDelete && !isSuccessfulMailtoUnsubscribe) return [];
  if ((isBulkDelete || isSingleDelete) && failed) return [];
  const values = [
    ...(Array.isArray(args.uids) ? args.uids : []),
    ...(Array.isArray(data.deleted_uids) ? data.deleted_uids : []),
    ...(Array.isArray(data.cleaned_uids) ? data.cleaned_uids : []),
  ];
  if (args.uid != null) values.push(args.uid);
  if (data.uid != null) values.push(data.uid);
  return Array.from(new Set(values.map(uid => String(uid || '').trim()).filter(Boolean)));
}

function _handleAgentEmailToolOutput(event) {
  const data = event?.detail || {};
  _trackAgentBrowserUnsubscribe(data);
  const ok = data.exit_code == null ? data.success !== false : data.exit_code === 0;
  if (!ok) return;
  const deletedUids = new Set(_agentDeletedEmailUids(data));
  if (!deletedUids.size) return;

  state._libEmails = (state._libEmails || []).filter(email => !deletedUids.has(String(email?.uid || '')));
  try { _libCacheWriteBack(); } catch (_) {}
  try { _renderGrid(); } catch (_) {}

  const modal = document.getElementById('email-unsubscribe-review-modal');
  const completed = [];
  (modal?._unsubscribeCandidates || []).forEach((candidate, idx) => {
    const remaining = _unsubscribeCandidateUids(candidate)
      .filter(uid => !deletedUids.has(String(uid)));
    if (remaining.length === _unsubscribeCandidateUids(candidate).length) return;
    if (!remaining.length) {
      completed.push(candidate);
      _markUnsubscribeCardDone(modal, idx, 'Agent done');
      return;
    }
    candidate.uid = remaining[0];
    candidate.duplicate_uids = remaining.slice(1);
    candidate.duplicate_count = remaining.length;
  });
  const run = modal?._agentUnsubscribeRun;
  if (run && completed.length) {
    completed.forEach(candidate => run.completed.add(candidate));
    run.restoreBusy?.setLabel?.(`Unsubscribed ${run.completed.size} / ${run.total}`);
  }
  if (completed.length) _rememberUnsubscribeHandled(completed);
  const visible = (modal?._unsubscribeCandidates || []).filter(candidate => (
    _unsubscribeCandidateUids(candidate).some(uid => !deletedUids.has(String(uid)))
  ));
  if (modal && !visible.length) {
    if (run) {
      run.restoreBusy?.setLabel?.(`Unsubscribed ${run.total} / ${run.total}`);
      run.restoreBusy = null;
      modal._agentUnsubscribeRun = null;
    }
    modal.querySelector('.email-unsub-list')?.style.setProperty('display', 'none');
    modal.querySelector('.email-unsub-followup')?.remove();
    modal.querySelector('.email-unsub-status')?.replaceChildren(document.createTextNode('Agent unsubscribe cleanup completed.'));
  }
}

window.addEventListener('odysseus:agent-tool-output', _handleAgentEmailToolOutput);

function _handleAgentUnsubscribeTerminal(event) {
  const modal = document.getElementById('email-unsubscribe-review-modal');
  const run = modal?._agentUnsubscribeRun;
  if (!run) return;
  const detail = event?.detail?.data || {};
  (Array.isArray(detail.tool_events) ? detail.tool_events : []).forEach(_trackAgentBrowserUnsubscribe);
  const completed = run.completed.size;
  const remaining = Math.max(0, run.total - completed);
  const failure = detail.failure || (detail.failed ? { message: 'The agent run failed before all unsubscribe flows completed.' } : null);
  run.restoreBusy?.();
  modal._agentUnsubscribeRun = null;
  modal.querySelector('.email-unsub-status')?.replaceChildren(document.createTextNode(
    failure
      ? `Agent unsubscribe failed: ${completed} / ${run.total} completed. ${remaining} still need review. ${failure.message || ''}`.trim()
      : remaining
        ? `Agent unsubscribe finished: ${completed} / ${run.total} completed. ${remaining} still need review.`
        : `Agent unsubscribe finished: ${completed} / ${run.total} completed.`,
  ));
  // A tool result can arrive just after the final browser step. Re-fetch once
  // the run is over so an old mailbox page or server-side list cache cannot
  // leave deleted messages visible until the next manual refresh.
  if (!failure && typeof refreshEmailLibrary === 'function') {
    setTimeout(() => refreshEmailLibrary().catch(() => {}), 0);
  }
}

window.addEventListener('odysseus:agent-terminal', _handleAgentUnsubscribeTerminal);

async function _runUnsubscribeCleanup(modal, candidates, action, btn) {
  const uids = [];
  const seen = new Set();
  (candidates || []).forEach(c => {
    _unsubscribeCandidateUids(c).forEach(uid => {
      if (seen.has(uid)) return;
      seen.add(uid);
      uids.push(uid);
    });
  });
  if (!uids.length) {
    showToast?.('No emails to update');
    return;
  }
  const busyLabel = action === 'junk'
    ? 'Marking spam'
    : action === 'junk_delete' ? 'Spam + delete' : 'Deleting';
  const restoreBusy = _setUnsubButtonBusy(btn, busyLabel);
  try {
    const r = await fetch(emailApiUrl('/api/email/unsubscribe/cleanup', {
      account_id: state._libAccountId || undefined,
    }), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        uids,
        folder: state._libFolder || 'INBOX',
        account_id: state._libAccountId || '',
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!d.success) throw new Error(d.error || 'Cleanup failed');
    const removed = new Set(uids.map(uid => String(uid)));
    state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
    try { _libCacheWriteBack(); } catch (_) {}
    try { _renderGrid(); } catch (_) {}
    _rememberUnsubscribeHandled(candidates);
    modal.querySelector('.email-unsub-followup')?.remove();
    showToast?.(action === 'junk'
      ? `Marked ${d.changed || 0} email${Number(d.changed || 0) === 1 ? '' : 's'} as spam`
      : action === 'junk_delete'
        ? `Marked ${d.changed || 0} email${Number(d.changed || 0) === 1 ? '' : 's'} as spam and deleted ${d.changed || 0}`
        : `Deleted ${d.changed || 0} email${Number(d.changed || 0) === 1 ? '' : 's'}`);
  } catch (err) {
    console.error(err);
    showToast?.(err?.message || 'Cleanup failed');
  } finally {
    restoreBusy?.();
  }
}

async function _deleteAfterUnsubscribe(candidates) {
  const groups = new Map();
  (candidates || []).forEach(c => {
    const sender = String(c?.from_address || '').trim();
    const key = sender.toLowerCase() || `uids:${_unsubscribeCandidateUids(c).join(',')}`;
    if (!groups.has(key)) groups.set(key, { sender, uids: [] });
    const group = groups.get(key);
    const seen = new Set(group.uids);
    _unsubscribeCandidateUids(c).forEach(uid => {
      if (!seen.has(uid)) {
        seen.add(uid);
        group.uids.push(uid);
      }
    });
  });
  if (!groups.size) return { success: true, changed: 0, failed: 0, cleaned_uids: [] };
  const result = { success: true, changed: 0, failed: 0, cleaned_uids: [] };
  for (const group of groups.values()) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    let r;
    try {
      r = await fetch(emailApiUrl('/api/email/unsubscribe/cleanup', {
        account_id: state._libAccountId || undefined,
      }), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          action: 'delete',
          scope: group.sender ? 'sender_unsubscribe' : '',
          sender: group.sender,
          uids: group.sender ? [] : group.uids,
          folder: state._libFolder || 'INBOX',
          account_id: state._libAccountId || '',
        }),
      });
    } catch (err) {
      if (err?.name === 'AbortError') throw new Error('Deleting timed out; the mail server did not respond');
      throw err;
    } finally {
      clearTimeout(timeout);
    }
    const d = await r.json().catch(() => ({}));
    if (!d.success) throw new Error(d.error || 'Could not move unsubscribed emails to Trash');
    result.changed += Number(d.changed || 0);
    result.failed += Number(d.failed || 0);
    result.cleaned_uids.push(...(d.cleaned_uids || []));
  }
  const removed = new Set(result.cleaned_uids.map(uid => String(uid)));
  state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
  try { _libCacheWriteBack(); } catch (_) {}
  try { _renderGrid(); } catch (_) {}
  _rememberUnsubscribeHandled(candidates);
  return result;
}

function _showUnsubscribeCleanupPrompt(modal, candidates, options = {}) {
  if (!modal || !Array.isArray(candidates) || !candidates.length) return;
  modal.querySelector('.email-unsub-followup')?.remove();
  const count = candidates.reduce((sum, c) => sum + _unsubscribeCandidateUids(c).length, 0);
  const heading = options.heading || `Done. Mark all ${count} of these email${count === 1 ? '' : 's'} as spam?`;
  const rows = candidates.map(c => {
    const uids = _unsubscribeCandidateUids(c);
    return `
      <div style="display:flex;gap:8px;align-items:center;min-width:0;">
        <div style="min-width:0;flex:1;">
          <div style="font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(c.subject || '(no subject)')}</div>
          <div style="font-size:11px;color:color-mix(in srgb,var(--fg) 62%,transparent);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(c.from_name || c.from_address || '')} · ${_esc(c.from_address || '')}</div>
        </div>
        ${uids.length > 1 ? `<span class="email-tag">x${uids.length}</span>` : ''}
      </div>`;
  }).join('');
  const box = document.createElement('div');
  box.className = 'email-unsub-followup';
  box.style.cssText = 'border:1px solid color-mix(in srgb,var(--accent,var(--red)) 35%,var(--border));border-radius:8px;padding:10px;background:color-mix(in srgb,var(--accent,var(--red)) 8%,transparent);display:flex;flex-direction:column;gap:8px;';
  box.innerHTML = `
    <div style="display:flex;gap:8px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;">
      <div style="font-size:12px;font-weight:700;">${_esc(heading)}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button type="button" class="memory-toolbar-btn email-unsub-clean-spam" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_CHECK_ICON}<span>Mark as spam</span></button>
        <button type="button" class="memory-toolbar-btn email-unsub-clean-junk-delete" style="display:inline-flex;align-items:center;gap:4px;color:var(--red);">${_UNSUB_TRASH_ICON}<span>Spam + delete</span></button>
        <button type="button" class="memory-toolbar-btn email-unsub-clean-delete" style="display:inline-flex;align-items:center;gap:4px;color:var(--red);">${_UNSUB_TRASH_ICON}<span>Delete</span></button>
        <button type="button" class="memory-toolbar-btn email-unsub-clean-keep" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_CHECK_ICON}<span>Keep</span></button>
        ${options.scanFurther ? '<button type="button" class="memory-toolbar-btn email-unsub-scan-further">Scan further</button>' : ''}
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:5px;font-size:12px;">${rows}</div>`;
  const body = modal.querySelector('.modal-body');
  const list = modal.querySelector('.email-unsub-list');
  body?.insertBefore(box, list || null);
  box.querySelector('.email-unsub-clean-spam')?.addEventListener('click', (e) => {
    _runUnsubscribeCleanup(modal, candidates, 'junk', e.currentTarget);
  });
  box.querySelector('.email-unsub-clean-junk-delete')?.addEventListener('click', async (e) => {
    const ok = await styledConfirm(`Mark ${count} reviewed email${count === 1 ? '' : 's'} as spam and delete ${count === 1 ? 'it' : 'them'}?`, {
      confirmText: 'Spam + delete',
      cancelText: 'Cancel',
      danger: true,
    });
    if (ok) _runUnsubscribeCleanup(modal, candidates, 'junk_delete', e.currentTarget);
  });
  box.querySelector('.email-unsub-clean-delete')?.addEventListener('click', async (e) => {
    const ok = await styledConfirm(`Delete ${count} reviewed email${count === 1 ? '' : 's'}?`, {
      confirmText: 'Delete',
      cancelText: 'Cancel',
      danger: true,
    });
    if (ok) _runUnsubscribeCleanup(modal, candidates, 'delete', e.currentTarget);
  });
  box.querySelector('.email-unsub-clean-keep')?.addEventListener('click', () => box.remove());
  box.querySelector('.email-unsub-scan-further')?.addEventListener('click', () => {
    const nextAnchor = options.scanAnchor;
    modal.remove();
    if (nextAnchor) setTimeout(() => _openUnsubscribeReviewModal(nextAnchor), 0);
  });
}

function _showUnsubscribeScanFurtherPrompt(modal, anchor, heading) {
  if (!modal || !anchor) return;
  modal.querySelector('.email-unsub-followup')?.remove();
  const box = document.createElement('div');
  box.className = 'email-unsub-followup';
  box.style.cssText = 'border:1px solid color-mix(in srgb,var(--accent,var(--red)) 35%,var(--border));border-radius:8px;padding:10px;background:color-mix(in srgb,var(--accent,var(--red)) 8%,transparent);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;';
  box.innerHTML = `<span style="font-size:12px;font-weight:700;">${_esc(heading)}</span><button type="button" class="memory-toolbar-btn email-unsub-scan-further">Scan further</button>`;
  const body = modal.querySelector('.modal-body');
  const list = modal.querySelector('.email-unsub-list');
  body?.insertBefore(box, list || null);
  box.querySelector('.email-unsub-scan-further')?.addEventListener('click', () => {
    modal.remove();
    setTimeout(() => _openUnsubscribeReviewModal(anchor), 0);
  });
}

function _showUnsubscribeRemainingPrompt(modal, candidates) {
  if (!modal || !Array.isArray(candidates) || !candidates.length) return;
  modal.querySelector('.email-unsub-followup')?.remove();
  const remaining = new Set(candidates);
  modal.querySelectorAll('.email-unsub-card').forEach(card => {
    const idx = Number(card.dataset.idx || -1);
    card.hidden = !remaining.has(modal._unsubscribeCandidates?.[idx]);
  });
  const box = document.createElement('div');
  box.className = 'email-unsub-followup email-unsub-remaining-followup';
  box.style.cssText = 'border:1px solid color-mix(in srgb,var(--accent,var(--red)) 35%,var(--border));border-radius:8px;padding:10px;background:color-mix(in srgb,var(--accent,var(--red)) 8%,transparent);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;';
  box.innerHTML = `
    <span style="font-size:12px;font-weight:700;">${candidates.length} unsubscribe${candidates.length === 1 ? '' : 's'} still need review</span>
    <span class="email-unsub-remaining-actions">
      <button type="button" class="memory-toolbar-btn email-unsub-clean-remaining-btn">${_UNSUB_TRASH_ICON}<span>Spam + delete remaining</span></button>
      <button type="button" class="memory-toolbar-btn email-unsub-agent-all-btn">${_UNSUB_AGENT_ICON}<span>Agent Unsubscribe All</span></button>
    </span>`;
  const body = modal.querySelector('.modal-body');
  const list = modal.querySelector('.email-unsub-list');
  body?.insertBefore(box, list || null);
  box.querySelector('.email-unsub-clean-remaining-btn')?.addEventListener('click', async (event) => {
    const count = candidates.reduce((sum, candidate) => sum + _unsubscribeCandidateUids(candidate).length, 0);
    const ok = await styledConfirm(
      `Mark ${count} remaining email${count === 1 ? '' : 's'} as spam and delete ${count === 1 ? 'it' : 'them'}?`,
      { confirmText: 'Spam + delete', cancelText: 'Cancel', danger: true },
    );
    if (ok) _runUnsubscribeCleanup(modal, candidates, 'junk_delete', event.currentTarget);
  });
  box.querySelector('.email-unsub-agent-all-btn')?.addEventListener('click', (event) => {
    const button = event.currentTarget;
    if (modal._agentUnsubscribeRun) return;
    const total = candidates.length;
    const restoreBusy = _setUnsubButtonBusy(button, `Completed 0 / ${total}`);
    modal._agentUnsubscribeRun = {
      total,
      candidates: candidates.slice(),
      completed: new Set(),
      cleanupInFlight: new Set(),
      restoreBusy,
    };
    if (!_askAgentToUnsubscribeAll(candidates)) {
      restoreBusy?.();
      modal._agentUnsubscribeRun = null;
    }
  });
}

export async function _openUnsubscribeReviewModal(anchor, options = {}) {
  const existing = document.getElementById('email-unsubscribe-review-modal');
  if (existing) {
    existing.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    existing.querySelector('.email-unsub-status')?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    return;
  }
  const modal = document.createElement('div');
  const scanFolderLabel = _unsubscribeFolderLabel(state._libFolder);
  modal.className = 'modal';
  modal.id = 'email-unsubscribe-review-modal';
  const settingsPage = anchor?.closest?.('#email-lib-settings-page, .email-settings-page-host');
  const inlineHost = settingsPage?.querySelector?.('.email-settings-cleanup-section');
  const inlineMode = !!inlineHost;
  if (inlineMode) {
    modal.className = 'email-unsubscribe-inline-panel';
    modal.style.cssText = 'display:none;margin-top:10px;';
    modal.innerHTML = `
      <div class="modal-body" style="display:flex;flex-direction:column;gap:10px;overflow:visible;">
          <div class="email-unsub-actions" style="display:none;gap:7px;justify-content:flex-end;align-items:center;flex-wrap:wrap;">
            <span class="email-unsub-panel-status" aria-live="polite"></span>
            <button type="button" class="memory-toolbar-btn email-unsub-rescan-btn" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_REFRESH_ICON}<span>Rescan</span></button>
            <button type="button" class="memory-toolbar-btn email-unsub-delete-all-btn" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_TRASH_ICON}<span>Delete all</span></button>
            <button type="button" class="memory-toolbar-btn email-unsub-auto-safe-btn" style="display:none;align-items:center;gap:4px;">${_UNSUB_CHECK_ICON}<span>Auto Unsubscribe All</span></button>
          </div>
          <div class="email-unsub-list" style="display:none;flex-direction:column;gap:8px;"></div>
      </div>`;
    inlineHost.appendChild(modal);
  } else {
    modal.style.display = 'block';
    modal.innerHTML = `
      <div class="modal-content doclib-modal-content" style="width:min(680px,92vw);background:var(--bg);">
        <div class="modal-header">
          <h4>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
            Unsubscribe review
          </h4>
        </div>
        <div class="modal-body" style="display:flex;flex-direction:column;gap:10px;max-height:min(72vh,620px);overflow:auto;">
        <div class="email-unsub-status" style="font-size:10.5px;color:color-mix(in srgb,var(--fg) 68%,transparent);">Scanning ${_esc(scanFolderLabel)} headers…</div>
        <div class="email-unsub-actions" style="display:none;gap:7px;justify-content:flex-end;align-items:center;flex-wrap:wrap;">
          <span class="email-unsub-panel-status" aria-live="polite"></span>
          <button type="button" class="memory-toolbar-btn email-unsub-rescan-btn" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_REFRESH_ICON}<span>Rescan</span></button>
          <button type="button" class="memory-toolbar-btn email-unsub-delete-all-btn" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_TRASH_ICON}<span>Delete all</span></button>
          <button type="button" class="memory-toolbar-btn email-unsub-auto-safe-btn" style="display:none;align-items:center;gap:4px;">${_UNSUB_CHECK_ICON}<span>Auto Unsubscribe All</span></button>
        </div>
        <div class="email-unsub-list" style="display:none;flex-direction:column;gap:8px;"></div>
      </div>
    </div>`;
    document.body.appendChild(modal);
  }
  const close = () => modal.remove();
  if (!inlineMode) modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  const statusEl = inlineMode
    ? inlineHost.querySelector('.email-settings-clean-status')
    : modal.querySelector('.email-unsub-status');
  const panelStatusEl = modal.querySelector('.email-unsub-panel-status');
  const listEl = modal.querySelector('.email-unsub-list');
  const cachedScan = options.forceRescan ? null : _readUnsubscribeScanCache();
  const usingCachedScan = !!cachedScan;
  let finishScanStatus = _setUnsubStatusBusy(
    statusEl,
    usingCachedScan ? 'Loading last unsubscribe scan…' : `Scanning ${scanFolderLabel} headers…`,
  );
  const showFinalStatus = (message, isError = false) => {
    const actionsEl = modal.querySelector('.email-unsub-actions');
    if (actionsEl) actionsEl.style.display = 'flex';
    if (inlineMode && panelStatusEl) {
      finishScanStatus?.('');
      panelStatusEl.classList.toggle('is-error', isError);
      panelStatusEl.textContent = message || '';
      modal.style.display = 'block';
      return;
    }
    statusEl?.classList.toggle('is-error', isError);
    finishScanStatus?.(message);
  };
  const finishScanError = (message) => {
    showFinalStatus(message || 'Failed to scan email headers', true);
  };
  try {
    const scanUnsubscribeHeaders = (maxScan) => fetch(emailApiUrl('/api/email/unsubscribe/scan', {
      folder: state._libFolder || 'INBOX',
      limit: 500,
      max_scan: maxScan,
      account_id: state._libAccountId || undefined,
    }), { credentials: 'same-origin' });
    const readScanResponse = async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload?.error || payload?.detail || `Email scan failed (HTTP ${response.status})`,
        );
      }
      return payload;
    };
    // Keep the review request below the app's 45s hard deadline. The old
    // whole-folder request made Gmail scans time out before any cards loaded.
    let scanLimit = 500;
    let res;
    let data = cachedScan;
    if (!data) {
      res = await scanUnsubscribeHeaders(scanLimit);
      data = await readScanResponse(res);
    } else {
      scanLimit = Number(data.scanLimit || scanLimit);
    }
    if (!data || (!data.success && !usingCachedScan)) {
      finishScanError(data?.error || 'Failed to scan email headers');
      return;
    }
    let candidates = _filterHandledUnsubscribeCandidates(
      _dedupeUnsubscribeCandidatesForDisplay(data.candidates || []),
    );
    if (!usingCachedScan && !candidates.length && scanLimit > 0 && Number(data.scanned || 0) >= scanLimit) {
      const searchMore = await styledConfirm(
        `No unsubscribe candidates found in ${scanLimit} recent emails. Search more?`,
        { title: 'Search more?', confirmText: 'Search more', cancelText: 'No' },
      );
      if (searchMore) {
        scanLimit = 0;
        finishScanStatus = _setUnsubStatusBusy(statusEl, `Scanning ${scanLimit} recent ${scanFolderLabel} headers…`);
        res = await scanUnsubscribeHeaders(scanLimit);
        data = await readScanResponse(res);
        if (!data.success) {
          finishScanError(data.error || 'Failed to scan email headers');
          return;
        }
        candidates = _filterHandledUnsubscribeCandidates(
          _dedupeUnsubscribeCandidatesForDisplay(data.candidates || []),
        );
      }
    }
    if (!usingCachedScan) _writeUnsubscribeScanCache(data, scanLimit);
    const rawTotal = Number(data.raw_total || candidates.length || 0);
    const scanPrefix = usingCachedScan ? 'Last scan: ' : '';
    const scanSuffix = usingCachedScan ? ' Rescan for fresh results.' : '';
    const scanScope = scanLimit > 0 ? `${data.scanned || 0} recent emails` : `all ${data.scanned || 0} emails`;
    const finalStatus = candidates.length
      ? `${scanPrefix}Found ${candidates.length} unsubscribe target${candidates.length === 1 ? '' : 's'} from ${scanScope}${rawTotal > candidates.length ? `, collapsed from ${rawTotal} matching emails` : ''}.${scanSuffix} Review before sending unsubscribe.`
      : `${scanPrefix}No unsubscribe candidates found in ${scanScope}.${scanSuffix}`;
    showFinalStatus(finalStatus);
    modal.querySelector('.email-unsub-actions').style.display = 'flex';
    modal.querySelector('.email-unsub-delete-all-btn').style.display = candidates.length ? 'inline-flex' : 'none';
    modal.querySelector('.email-unsub-auto-safe-btn').style.display = candidates.length ? 'inline-flex' : 'none';
    listEl.style.display = candidates.length ? 'flex' : 'none';
    listEl.innerHTML = candidates.map((c, idx) => {
      const method = c.recommended_method || null;
      const reasons = (c.reasons || []).map(r => `<span class="email-tag">${_esc(r)}</span>`).join('');
      const urlMethod = (c.methods || []).find(m => m.kind === 'url');
      const duplicateCount = Number(c.duplicate_count || 1);
      const duplicateBadge = duplicateCount > 1 ? `<span class="email-tag email-unsub-duplicate-badge">x${duplicateCount}</span>` : '';
      const deleteLabel = duplicateCount > 1 ? 'Delete all' : 'Delete';
      const deleteTitle = duplicateCount > 1
        ? 'Delete all unsubscribe emails from this sender'
        : 'Delete matching unsubscribe emails from this sender';
      return `
        <div class="email-unsub-card" data-idx="${idx}" style="border:1px solid var(--border);border-radius:8px;padding:10px;background:color-mix(in srgb,var(--fg) 3%,transparent);display:flex;flex-direction:column;gap:7px;">
          <div class="email-unsub-card-top" style="display:flex;gap:8px;align-items:center;padding-right:0;">
            <div class="email-unsub-card-summary" style="min-width:0;flex:1;">
              <div class="email-unsub-subject-line" style="display:flex;align-items:center;gap:6px;min-width:0;">
                <div style="font-weight:700;font-size:13px;min-width:0;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(c.subject || '(no subject)')}</div>
              </div>
              <div class="email-unsub-card-subtitle" style="font-size:11px;color:color-mix(in srgb,var(--fg) 62%,transparent);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(c.from_name || c.from_address || '')} · ${_esc(c.from_address || '')}</div>
            </div>
            <button type="button" class="email-unsub-card-toggle" data-idx="${idx}" aria-expanded="false" aria-controls="email-unsub-details-${idx}" title="Show unsubscribe details" aria-label="Show unsubscribe details">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </button>
          </div>
          <div id="email-unsub-details-${idx}" class="email-unsub-card-details" hidden>
            <div class="email-unsub-detail-head" style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;">
              <span class="email-tag email-tag-spam" style="flex:0 0 auto;">score ${_esc(c.score || 0)}</span>
              ${duplicateBadge}
              <span style="flex:1 1 auto;min-width:8px;"></span>
              <button type="button" class="memory-toolbar-btn email-unsub-done-btn" data-idx="${idx}" title="${deleteTitle}" aria-label="${deleteLabel} unsubscribe emails">${_UNSUB_TRASH_ICON}<span>${deleteLabel}</span></button>
              <button type="button" class="memory-toolbar-btn email-unsub-ignore-btn" data-idx="${idx}" title="Ignore this unsubscribe candidate" aria-label="Ignore this unsubscribe candidate">${_UNSUB_CLOSE_ICON}</button>
            </div>
            <div class="email-unsub-reasons" style="display:flex;flex-wrap:wrap;gap:4px;">${reasons}</div>
            <div class="email-unsub-actions-row" style="display:flex;gap:7px;align-items:center;justify-content:flex-end;flex-wrap:wrap;">
              ${c.can_execute ? `<button type="button" class="memory-toolbar-btn email-unsub-send-btn" data-idx="${idx}" style="display:inline-flex;align-items:center;gap:4px;">${_UNSUB_EMAIL_ICON}<span>${_esc(_unsubscribeMethodLabel(method))}</span></button>` : ''}
              ${urlMethod ? `<button type="button" class="memory-toolbar-btn email-unsub-agent-btn" data-idx="${idx}" style="display:inline-flex;align-items:center;gap:6px;"><svg class="email-unsub-agent-icon email-unsub-accent-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg><span>Agent Unsubscribe</span></button>` : ''}
              ${urlMethod ? `<a class="memory-toolbar-btn email-unsub-link-btn" href="${_esc(urlMethod.target)}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;">${_UNSUB_EXTERNAL_ICON}<span>Link Unsubscribe</span></a>` : ''}
            </div>
          </div>
        </div>`;
    }).join('');
    modal._unsubscribeCandidates = candidates;
    listEl.querySelectorAll('.email-unsub-card-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const card = btn.closest('.email-unsub-card');
        const details = card?.querySelector('.email-unsub-card-details');
        if (!details) return;
        const isOpen = !details.hidden;
        details.hidden = isOpen;
        card.classList.toggle('is-expanded', !isOpen);
        btn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        btn.setAttribute('aria-label', isOpen ? 'Show unsubscribe details' : 'Hide unsubscribe details');
        btn.title = isOpen ? 'Show unsubscribe details' : 'Hide unsubscribe details';
      });
    });
    modal.querySelector('.email-unsub-rescan-btn')?.addEventListener('click', () => {
      modal.remove();
      setTimeout(() => _openUnsubscribeReviewModal(anchor, { forceRescan: true }), 0);
    });
    modal.querySelector('.email-unsub-delete-all-btn')?.addEventListener('click', async (event) => {
      const allCandidates = modal._unsubscribeCandidates || [];
      const count = allCandidates.reduce((sum, candidate) => sum + _unsubscribeCandidateUids(candidate).length, 0);
      if (!count) {
        finishScanStatus?.('No unsubscribe emails to delete.');
        return;
      }
      const ok = await styledConfirm(
        `Delete all ${count} reviewed unsubscribe email${count === 1 ? '' : 's'}?`,
        { confirmText: 'Delete all', cancelText: 'Cancel', danger: true },
      );
      if (!ok) return;
      const restoreBusy = _setUnsubButtonBusy(event.currentTarget, 'Deleting all');
      try {
        const result = await _deleteAfterUnsubscribe(allCandidates);
        if (Number(result?.failed || 0) > 0 || Number(result?.changed || 0) <= 0) {
          throw new Error('Could not move the reviewed emails to Trash');
        }
        allCandidates.forEach((candidate, idx) => _markUnsubscribeCardDone(modal, idx, 'Deleted'));
        modal.querySelector('.email-unsub-list')?.style.setProperty('display', 'none');
        finishScanStatus?.(`Deleted ${result.changed} reviewed email${result.changed === 1 ? '' : 's'}`);
      } catch (error) {
        finishScanStatus?.(error?.message || 'Delete all failed');
      } finally {
        restoreBusy?.();
      }
    });
    modal.querySelector('.email-unsub-auto-safe-btn')?.addEventListener('click', async () => {
      const safeCandidates = (modal._unsubscribeCandidates || [])
        .map((c, idx) => ({ c, idx }))
        .filter(item => item.c && item.c.can_execute);
      if (!safeCandidates.length) {
        finishScanStatus?.(`No automatic unsubscribe actions found. ${modal._unsubscribeCandidates?.length || 0} link${(modal._unsubscribeCandidates?.length || 0) === 1 ? '' : 's'} still need review.`);
        _showUnsubscribeRemainingPrompt(modal, modal._unsubscribeCandidates || []);
        return;
      }
      const ok = await styledConfirm(`Send unsubscribe emails for ${safeCandidates.length} target${safeCandidates.length === 1 ? '' : 's'}?`, {
        confirmText: 'Auto Unsubscribe All',
        cancelText: 'Cancel',
      });
      if (!ok) return;
      const btn = modal.querySelector('.email-unsub-auto-safe-btn');
      const restoreBusy = _setUnsubButtonBusy(btn, `Unsubscribing 0/${safeCandidates.length}`);
      let sent = 0;
      let failed = 0;
      const sentCandidates = [];
      const failedCandidates = [];
      try {
        for (const item of safeCandidates) {
          const c = item.c;
          try {
            restoreBusy?.setLabel?.(`Unsubscribing ${sent + failed + 1}/${safeCandidates.length}`);
            const r = await fetch(emailApiUrl('/api/email/unsubscribe/execute', {
              account_id: state._libAccountId || undefined,
            }), {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                uid: c.uid,
                folder: c.folder || state._libFolder || 'INBOX',
                account_id: state._libAccountId || '',
                method_index: 0,
                move_to_spam: false,
              }),
            });
            const d = await r.json().catch(() => ({}));
            if (!d.success) throw new Error(d.error || 'Unsubscribe failed');
            sent += 1;
            sentCandidates.push(c);
            const rowBtn = modal.querySelector(`.email-unsub-send-btn[data-idx="${item.idx}"]`);
            if (rowBtn) {
              rowBtn.disabled = true;
              rowBtn.innerHTML = `${_UNSUB_CHECK_ICON}<span>Sent</span>`;
              rowBtn.style.display = 'inline-flex';
              rowBtn.style.alignItems = 'center';
              rowBtn.style.gap = '4px';
            }
            _markUnsubscribeCardDone(modal, item.idx);
          } catch (err) {
            failed += 1;
            failedCandidates.push(c);
            console.error(err);
          }
        }
      } finally {
        restoreBusy?.();
      }
      let deleteFailed = false;
      let cleanupResult = { success: true, changed: 0, failed: 0, cleaned_uids: [] };
      if (sentCandidates.length) {
        try {
          cleanupResult = await _deleteAfterUnsubscribe(sentCandidates);
          deleteFailed = Number(cleanupResult.failed || 0) > 0;
          const cleaned = new Set((cleanupResult.cleaned_uids || []).map(uid => String(uid)));
          (modal._unsubscribeCandidates || []).forEach((candidate, candidateIdx) => {
            if (_unsubscribeCandidateUids(candidate).some(uid => cleaned.has(String(uid)))) {
              _markUnsubscribeCardDone(modal, candidateIdx);
            }
          });
        } catch (err) {
          deleteFailed = true;
          console.error(err);
        }
      }
      const handled = new Set();
      sentCandidates.forEach(c => _unsubscribeCandidateUids(c).forEach(uid => handled.add(String(uid))));
      (cleanupResult.cleaned_uids || []).forEach(uid => handled.add(String(uid)));
      const remainingCandidates = (modal._unsubscribeCandidates || []).filter(c => (
        !_unsubscribeCandidateUids(c).some(uid => handled.has(String(uid)))
      ));
      finishScanStatus?.(
        `Auto unsubscribe finished: ${sent} unsubscribed${failed ? `, ${failed} failed` : ''}${remainingCandidates.length ? `, ${remainingCandidates.length} still need review` : ''}.`,
      );
      if (sent && !failed && !deleteFailed) {
        showToast?.(`Unsubscribed and deleted ${sent} email${sent === 1 ? '' : 's'}`);
      } else if (sent || failed) {
        showToast?.(deleteFailed
          ? `Unsubscribed ${sent}, but some emails could not be deleted`
          : `Sent ${sent}, failed ${failed}`);
      }
      if (remainingCandidates.length) {
        _showUnsubscribeRemainingPrompt(modal, remainingCandidates);
      } else {
        _showUnsubscribeScanFurtherPrompt(
          modal,
          anchor,
          `Finished this batch: ${sent} unsubscribed. Search older emails?`,
        );
      }
    });
    listEl.querySelectorAll('.email-unsub-done-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = Number(btn.dataset.idx || 0);
        const c = modal._unsubscribeCandidates?.[idx];
        if (!c) return;
        const sender = c.from_name || c.from_address || 'this sender';
        const ok = await styledConfirm(
          `Delete matching unsubscribe emails from ${sender}?`,
          { confirmText: 'Delete', cancelText: 'Cancel', danger: true },
        );
        if (!ok) return;
        const restoreBusy = _setUnsubButtonBusy(btn, 'Deleting');
        try {
          await _deleteAfterUnsubscribe([c]);
          restoreBusy?.();
          _markUnsubscribeCardDone(modal, idx, 'Deleted');
          showToast?.('Matching emails deleted');
        } catch (err) {
          restoreBusy?.();
          showToast?.(err?.message || 'Could not delete matching emails');
        }
      });
    });
    listEl.querySelectorAll('.email-unsub-ignore-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.idx || 0);
        const c = modal._unsubscribeCandidates?.[idx];
        if (!c) return;
        _rememberUnsubscribeIgnored(c);
        btn.closest('.email-unsub-card')?.remove();
        const remaining = listEl.querySelectorAll('.email-unsub-card:not([hidden])').length;
        if (!remaining) {
          listEl.style.display = 'none';
          modal.querySelector('.email-unsub-auto-safe-btn').style.display = 'none';
          finishScanStatus?.('No unsubscribe candidates left in this review.');
        } else {
          finishScanStatus?.(`${remaining} unsubscribe candidate${remaining === 1 ? '' : 's'} still need review.`);
        }
      });
    });
    listEl.querySelectorAll('.email-unsub-agent-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.idx || 0);
        const c = modal._unsubscribeCandidates?.[idx];
        if (!c) return;
        if (modal._agentUnsubscribeRun) return;
        const restoreBusy = _setUnsubButtonBusy(btn, 'Starting');
        modal._agentUnsubscribeRun = {
          total: 1,
          candidates: [c],
          completed: new Set(),
          cleanupInFlight: new Set(),
          restoreBusy,
        };
        if (!_askAgentToUnsubscribe(c)) {
          restoreBusy?.();
          modal._agentUnsubscribeRun = null;
        }
      });
    });
    listEl.querySelectorAll('.email-unsub-send-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = Number(btn.dataset.idx || 0);
        const c = modal._unsubscribeCandidates?.[idx];
        if (!c) return;
        const ok = await styledConfirm(`Send unsubscribe email to ${_unsubscribeMethodLabel(c.recommended_method)}?`, {
          confirmText: 'Unsubscribe',
          cancelText: 'Cancel',
        });
        if (!ok) return;
        btn.disabled = true;
        btn.textContent = 'Sending…';
        try {
          const r = await fetch(emailApiUrl('/api/email/unsubscribe/execute', {
            account_id: state._libAccountId || undefined,
          }), {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              uid: c.uid,
              folder: c.folder || state._libFolder || 'INBOX',
              account_id: state._libAccountId || '',
              method_index: 0,
              move_to_spam: false,
            }),
          });
            const d = await r.json().catch(() => ({}));
            if (!d.success) throw new Error(d.error || 'Unsubscribe failed');
            btn.innerHTML = `${_UNSUB_CHECK_ICON}<span>Sent</span>`;
          btn.style.display = 'inline-flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '4px';
            _markUnsubscribeCardDone(modal, idx);
          try {
            await _deleteAfterUnsubscribe([c]);
            showToast('Unsubscribed and deleted email');
          } catch (deleteErr) {
            console.error(deleteErr);
            showToast('Unsubscribed, but email could not be deleted');
            _showUnsubscribeCleanupPrompt(modal, [c], {
              heading: 'The unsubscribe request was sent. Mark this email as spam and delete it?',
            });
          }
        } catch (err) {
          console.error(err);
          btn.disabled = false;
          btn.innerHTML = `${_UNSUB_EMAIL_ICON}<span>${_esc(_unsubscribeMethodLabel(c.recommended_method))}</span>`;
          btn.style.display = 'inline-flex';
          btn.style.alignItems = 'center';
          btn.style.gap = '4px';
          showToast(err?.message || 'Unsubscribe failed');
        }
      });
    });
  } catch (err) {
    console.error(err);
    finishScanError('Failed to scan email headers');
  }
}
