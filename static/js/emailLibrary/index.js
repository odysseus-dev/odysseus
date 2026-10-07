/**
 * emailLibrary/index.js — Email library popup modal.
 * Similar pattern to documentLibrary.js. Shows emails in a grid with search/filter.
 *
 * `static/js/emailLibrary.js` re-exports this module's public surface so the
 * old import path keeps resolving; see the comment there.
 */

import spinnerModule from '../spinner.js';
import { styledConfirm, showToast, emptyStateIcon } from '../ui.js?v=20260916largetoolscroll1';
import { folderDisplayName, sortedFolders } from '../emailInbox.js?v=20260914aireply4';
import settingsModule from '../settings.js?v=20260912writingstyle3';
import * as Modals from '../modalManager.js';
import { makeWindowDraggable } from '../windowDrag.js';
import {
  _esc,
  _escLinkify,
  _extractName,
  _formatRecipients,
  _senderColor,
  _sanitizeHtml,
} from './utils.js';
import { state } from './state.js';
import { getSettings } from '../appConfig.js';
import { collapseSidebarToRail } from '../modalSnap.js';
import { emailApiUrl } from '../emailShared.js';
import {
  bindMenuDismiss,
  dismissTopMenu,
  bindExpandedCardDismiss,
  unbindExpandedCardDismiss,
} from '../escMenuStack.js';
import { _aiReplyIcon, _handleAiReplyButton, _hasMultipleRecipients } from './aiReply.js';
import {
  _buildAttsHtmlFor,
  _loadDeferredAttachmentsIntoReader,
  _wireEmailAttachmentWrap,
} from './attachments.js';
import { _safeRenderEmailBody, _wireEmailInlineImages } from './bodyRender.js';
import {
  _bulkAction,
  _showBulkActionsMenu,
  _showCardMenu,
  _showReaderMoreMenu,
  _updateBulkBar,
} from './menus.js';
import { _emailBodyTextForTranslate, _openEmailAsTab, _summarizeEmail } from './reader.js';
import {
  _EMAIL_SETTINGS_ICON,
  _bindEmailSettingsPageControls,
  _emailCleanupSettingsHtml,
  _emailDisplaySettingsHtml,
  _emailSettingsAccountSelectHtml,
  _emailSettingsFormHtml,
  _emailSettingsLoadingHtml,
  _emailWritingStyleHtml,
  _fetchEmailSettingsConfig,
  _fetchEmailWritingStyle,
  _hideEmailSettingsPage,
  _isAutoReplyActiveForCurrentAccount,
  _readEmailInlineImagesPreference,
  _showEmailSettingsPage,
  _syncAutoReplyCalendarEvent,
  _syncEmailAutoReplyTitle,
  _syncEmailSettingsAccountPicker,
} from './settingsPage.js';

const API_BASE = window.location.origin;
let _emailUnreadChipClickWired = false;
let _libLoadSeq = 0;
let _emailMailboxGeneration = 0;
let _emailCardOpenSeq = 0;
let _emailReadMutationSeq = 0;
const _emailReadMutations = new Map();
let _libFolderSeq = 0;
let _libSearchSeq = 0;
let _libSearchHadResults = false;
let _libSearchInFlight = false;
let _activeEmailReaderForSelectAll = null;
let _libAccountsLoadedAt = 0;
const _LIB_ACCOUNTS_TTL_MS = 5 * 60 * 1000;
let _accountUnreadSeq = 0;
let _accountUnreadState = new Map(); // account_id -> { unreadCount, maxUid }

function _hasActiveEmailSearchResults() {
  if (!_libSearchHadResults) return false;
  if (String(state._libSearch || '').trim()) return true;
  return (state._libSearchPills || []).some(p => p?.type === 'text' || p?.type === 'contact');
}


export function _normalizeEmailStateFlags(em) {
  if (!em || typeof em !== 'object') return em;
  const flags = String(em.flags || '');
  const answered = !!(
    em.is_answered ||
    em.is_done ||
    em.done ||
    em.answered ||
    flags.includes('\\Answered')
  );
  // Once the normalized field exists, it is the source of truth. Some
  // cached/fixture rows also retain legacy aliases such as `favorite`; OR-ing
  // those aliases on every render would resurrect a favorite immediately
  // after the user turns it off.
  const flagged = Object.prototype.hasOwnProperty.call(em, 'is_flagged')
    ? !!em.is_flagged
    : !!(
      em.is_favorite ||
      em.favorite ||
      em.flagged ||
      em.starred ||
      flags.includes('\\Flagged')
    );
  const read = !!(
    em.is_read ||
    em.read ||
    flags.includes('\\Seen')
  );
  em.is_answered = answered;
  em.is_done = answered;
  em.is_flagged = flagged;
  em.is_read = read;
  return em;
}

function _isEmailTypingTarget(t) {
  return !!(t && (
    t.tagName === 'INPUT' ||
    t.tagName === 'TEXTAREA' ||
    t.tagName === 'SELECT' ||
    t.isContentEditable
  ));
}

function _selectEmailReaderContents(reader) {
  if (!reader || !reader.isConnected) return false;
  const hiddenModal = reader.closest('.modal.hidden');
  if (hiddenModal) return false;
  const range = document.createRange();
  range.selectNodeContents(reader);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
  return true;
}

export function _markEmailReaderActive(reader) {
  if (!reader) return;
  _activeEmailReaderForSelectAll = reader;
  if (reader.dataset.selectAllWired === '1') return;
  reader.dataset.selectAllWired = '1';
  reader.addEventListener('pointerdown', () => { _activeEmailReaderForSelectAll = reader; }, true);
  reader.addEventListener('focusin', () => { _activeEmailReaderForSelectAll = reader; }, true);
}

function _emailReaderLoadErrorHtml(message) {
  return `
    <div class="email-reader-load-error">
      <div class="email-reader-load-error-title">Could not load email</div>
      <div class="email-reader-load-error-msg">${_esc(message || 'Failed to load email')}</div>
      <button type="button" class="memory-toolbar-btn email-reader-retry-btn">Retry</button>
    </div>`;
}

export function _showEmailReaderLoadError(reader, message, onRetry) {
  if (!reader) return;
  reader.classList.remove('email-card-reader-loading');
  reader.classList.add('email-card-reader-error');
  reader.style.minHeight = '';
  reader.innerHTML = _emailReaderLoadErrorHtml(message);
  const retry = reader.querySelector('.email-reader-retry-btn');
  retry?.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    onRetry?.();
  });
  _markEmailReaderActive(reader);
}

export function _openCalendarEventFromEmail(uid) {
  const target = String(uid || '').trim();
  if (!target) return;
  import('../calendar.js?v=20260914emailsource11').then(mod => {
    const open = mod.openCalendarTo || (mod.default && mod.default.openCalendarTo);
    if (open) open(target);
  }).catch(() => {});
}

function _applyTagFilterFromPill(tag) {
  const normalized = String(tag || '').trim().toLowerCase().replace(/_/g, '-');
  if (!normalized || normalized === 'calendar') return;
  const value = `filter:tag:${normalized}`;
  const existingIdx = Array.isArray(state._libSearchPills)
    ? state._libSearchPills.findIndex(p => p?.type === 'filter' && p.value === value)
    : -1;
  if (existingIdx >= 0) {
    _removeSearchPillAt(existingIdx);
    return;
  }
  _addSearchPill({
    type: 'filter',
    value,
    label: normalized.replace(/-/g, ' '),
  });
}

document.addEventListener('odysseus:email-filter-tag', (e) => {
  _applyTagFilterFromPill(e.detail?.tag);
});

function _emailTagPillHtml(tag, em) {
  const normalized = String(tag || '').trim().toLowerCase().replace(/_/g, '-');
  if (!normalized) return '';
  const eventUid = normalized === 'calendar' && Array.isArray(em?.calendar_event_uids)
    ? String(em.calendar_event_uids[0] || '').trim()
    : '';
  if (normalized === 'calendar') {
    if (!eventUid) return '';
    return `<button type="button" class="email-tag email-tag-${_esc(normalized)} email-tag-clickable" data-calendar-event-uid="${_esc(eventUid)}" title="Open calendar event">${_esc(normalized)}</button>`;
  }
  return `<button type="button" class="email-tag email-tag-${_esc(normalized)} email-tag-clickable" data-email-filter-tag="${_esc(normalized)}" title="Show ${_esc(normalized)} emails">${_esc(normalized)}</button>`;
}

function _emailTagGroupHtml(tags, em) {
  const visible = (Array.isArray(tags) ? tags : [])
    .map(t => _emailTagPillHtml(t, em))
    .filter(Boolean);
  if (!visible.length) return '';
  // A lone tag is already the most useful compact representation. Never
  // replace it with a misleading "+1" overflow control.
  if (visible.length === 1) return visible[0];
  // Two tags still fit as useful labels. Overflow starts at three so the UI
  // never renders a low-value "+1" control.
  if (visible.length === 2) return visible.join('');
  const extra = visible.slice(1).map(html => `<span class="email-tag-extra">${html}</span>`).join('');
  const additionalCount = visible.length - 1;
  const mini = `<button type="button" class="email-tags-more email-tags-more-single" data-email-tags-more aria-expanded="false" title="Show all tags"><span>+${additionalCount}</span></button>`;
  return `${visible[0]}${extra}<button type="button" class="email-tags-more" data-email-tags-more aria-expanded="false" title="Show all tags"><span data-email-tags-count-normal>+${additionalCount}</span><span data-email-tags-count-mini>+${additionalCount}</span><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg></button>${mini}`;
}

function _fitEmailCardTags(titleRow) {
  const tagWrap = titleRow?.querySelector('.email-card-tags');
  const title = titleRow?.querySelector('.memory-item-title');
  const status = titleRow?.querySelector('.email-card-status');
  if (!tagWrap || !title || !status) return;
  tagWrap.classList.remove('email-tags-mini');
  tagWrap.style.setProperty('--email-status-width', `${status.offsetWidth}px`);
  const minimumTitleWidth = Math.max(96, Math.min(160, titleRow.clientWidth * 0.45));
  const availableForTags = Math.max(0, titleRow.clientWidth - status.offsetWidth - minimumTitleWidth - 12);
  const hasOverflowControl = !!tagWrap.querySelector('[data-email-tags-more]');
  tagWrap.classList.toggle('email-tags-mini', hasOverflowControl && tagWrap.scrollWidth > availableForTags);
}

const _emailTagFitObserver = typeof ResizeObserver === 'function'
  ? new ResizeObserver(entries => {
      for (const entry of entries) _fitEmailCardTags(entry.target);
    })
  : null;

const _DONE_RESPONSE_TAGS = new Set(['urgent', 'reply-soon', 'action-needed']);

function _visibleEmailTagsForRender(em) {
  const tags = Array.isArray(em?.tags) ? em.tags : [];
  if (!em?.is_answered) return tags;
  return tags.filter(t => !_DONE_RESPONSE_TAGS.has(String(t || '').trim().toLowerCase().replace(/_/g, '-')));
}

export function _clearDoneResponseTagsLocal(em) {
  if (!em || !Array.isArray(em.tags)) return;
  em.tags = em.tags.filter(t => !_DONE_RESPONSE_TAGS.has(String(t || '').trim().toLowerCase().replace(/_/g, '-')));
}

export function _loadedEmailsHaveVisibleTags() {
  return (state._libEmails || []).some(em =>
    _visibleEmailTagsForRender(em).length > 0 || !!em?.is_spam_verdict
  );
}

export async function _openTasksForEmailTags() {
  try {
    const mod = await import('../tasks.js?v=20260914taskmodel1');
    const openTasks = mod.openTasks || mod.default?.openTasks;
    if (typeof openTasks === 'function') {
      openTasks(null, { filter: 'Email', focusAction: 'check_email_urgency' });
      return;
    }
  } catch (_) {}
  document.getElementById('tool-tasks-btn')?.click();
}

export function _notifyNoLoadedEmailTags() {
  const count = (state._libEmails || []).length;
  const msg = count
    ? `No tags found in the ${count} loaded emails.`
    : 'No email tags loaded yet.';
  showToast(`${msg} Turn on email tagging in Tasks.`, {
    duration: 6500,
    action: 'Open Tasks',
    onAction: _openTasksForEmailTags,
  });
}

// Stash the email identity (uid + folder + account) on the reader element
// so chat submits and other code paths can ask "what email is the user
// currently looking at?" without re-deriving from the DOM hierarchy.
export function _stampReaderContext(reader, em, folder, account) {
  if (!reader || !em) return;
  reader.dataset.emailUid = String(em.uid || '');
  reader.dataset.emailFolder = String(folder || state._libFolder || 'INBOX');
  reader.dataset.emailAccount = String(account || state._libAccountId || '');
  if (em.subject) reader.dataset.emailSubject = String(em.subject);
  if (em.from_address || em.from_name) {
    reader.dataset.emailFrom = String(em.from_address || em.from_name);
  }
}

// Returns { uid, folder, account, subject, from } for the email the user
// is most likely referring to — the last reader they interacted with, then
// any open reader-modal as a fallback. Returns null when no email reader
// is open. Exported below for chat.js to read on submit.
function _getActiveEmailContext() {
  const candidates = [];
  if (_activeEmailReaderForSelectAll && _activeEmailReaderForSelectAll.isConnected) {
    candidates.push(_activeEmailReaderForSelectAll);
  }
  // Visible reader-tab modals (popped-out windows).
  document.querySelectorAll('.modal[id^="email-reader-"]:not(.hidden):not(.modal-minimized) .email-card-reader').forEach(el => candidates.push(el));
  // Expanded inline reader in the library list.
  document.querySelectorAll('#email-lib-modal:not(.hidden) .doclib-card.email-card-expanded .email-card-reader').forEach(el => candidates.push(el));
  for (const r of candidates) {
    const uid = r?.dataset?.emailUid;
    if (uid) {
      return {
        uid,
        folder: r.dataset.emailFolder || 'INBOX',
        account: r.dataset.emailAccount || '',
        subject: r.dataset.emailSubject || '',
        from: r.dataset.emailFrom || '',
      };
    }
  }
  return null;
}

// Frontend reads via the global so chat.js doesn't need a separate import
// path (emailLibrary loads lazily in some entry points).
try { window.__odysseusGetActiveEmailContext = _getActiveEmailContext; } catch (_) {}

const _COPY_EMAIL_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

function _decodeAttrValue(v) {
  const tmp = document.createElement('textarea');
  tmp.innerHTML = v || '';
  return tmp.value;
}

function _emailAddressFromRecipientText(text) {
  const raw = String(text || '').trim();
  const angle = raw.match(/<\s*([^<>@\s]+@[^<>\s]+)\s*>/);
  if (angle) return angle[1].trim();
  const any = raw.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
  return any ? any[0].trim() : raw;
}

export function _splitRecipientList(raw) {
  const out = [];
  let cur = '';
  let quote = false;
  let angle = false;
  const s = String(raw || '');
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i];
    if (ch === '"' && s[i - 1] !== '\\') quote = !quote;
    else if (ch === '<' && !quote) angle = true;
    else if (ch === '>' && !quote) angle = false;

    if (ch === ',' && !quote && !angle) {
      const part = cur.trim();
      if (part) out.push(part);
      cur = '';
      continue;
    }
    cur += ch;
  }
  const tail = cur.trim();
  if (tail) out.push(tail);
  return out;
}

async function _copyTextToClipboard(text) {
  const value = String(text || '');
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (_) {}
  try {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return !!ok;
  } catch (_) {
    return false;
  }
}

export function _wireMetaToggle(root) {
  const toggle = root && root.querySelector('.email-reader-meta-toggle');
  const details = root && root.querySelector('.email-reader-meta-details');
  if (!toggle || !details) return;
  const meta = details.closest('.email-reader-meta');
  toggle.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const open = details.hasAttribute('hidden');
    if (open) details.removeAttribute('hidden');
    else details.setAttribute('hidden', '');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.classList.toggle('open', open);
    if (meta) meta.classList.toggle('email-reader-meta-expanded', open);
  });
}

export function _recipientMetaToggleHtml(data = {}) {
  const ccCount = _splitRecipientList(data.cc || '').length;
  if (ccCount) {
    return `<button class="email-reader-meta-toggle email-reader-cc-toggle" type="button" aria-expanded="false" title="Show Cc recipients"><span>Cc +${ccCount}</span><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>`;
  }
  if (!data.to) return '';
  return `<button class="email-reader-meta-toggle" type="button" aria-expanded="false" title="Show recipients"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>`;
}

export function _recipientChipHtml(full, label, extraClass = '') {
  const fullText = String(full || '').trim();
  const addr = _emailAddressFromRecipientText(fullText);
  const labelText = String(label || addr || fullText || '').trim();
  const cls = `recipient-chip${extraClass ? ` ${extraClass}` : ''}`;
  return `<span class="${cls}" data-full="${_esc(fullText || labelText)}" data-email="${_esc(addr)}" title="Click for details"><span class="recipient-chip-label">${_esc(labelText)}</span><button type="button" class="recipient-chip-copy" title="Copy email" aria-label="Copy email" hidden>${_COPY_EMAIL_ICON}</button></span>`;
}

let _recipientChipPopoverCtl = null;
function _closeRecipientChipPopover() {
  try { _recipientChipPopoverCtl?.abort(); } catch {}
  _recipientChipPopoverCtl = null;
  document.querySelector('.recipient-chip-popover')?.remove();
  document.querySelectorAll('.recipient-chip.popover-open').forEach(chip => {
    chip.classList.remove('popover-open');
  });
}

function _showRecipientChipPopover(chip) {
  if (!chip) return false;
  _closeRecipientChipPopover();
  const full = _decodeAttrValue(chip.dataset.full || '').trim();
  const email = chip.dataset.email || _emailAddressFromRecipientText(full);
  const name = chip.dataset.name || chip.querySelector('.recipient-chip-label')?.textContent?.trim() || '';
  const detail = full || email || name;
  if (!detail) return true;

  chip.classList.add('popover-open');
  const pop = document.createElement('div');
  pop.className = 'recipient-chip-popover';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Sender details');
  pop.innerHTML = `
    <div class="recipient-chip-popover-main">
      ${name && detail !== name ? `<div class="recipient-chip-popover-name">${_esc(name)}</div>` : ''}
      <div class="recipient-chip-popover-detail">${_esc(detail)}</div>
    </div>
    ${email ? `<button type="button" class="recipient-chip-popover-copy" title="Copy email" aria-label="Copy email">${_COPY_EMAIL_ICON}</button>` : ''}
  `;
  document.body.appendChild(pop);

  const rect = chip.getBoundingClientRect();
  const margin = 10;
  const maxLeft = Math.max(margin, window.innerWidth - pop.offsetWidth - margin);
  let left = Math.min(Math.max(margin, rect.left), maxLeft);
  let top = rect.bottom + 6;
  if (top + pop.offsetHeight + margin > window.innerHeight) {
    top = Math.max(margin, rect.top - pop.offsetHeight - 6);
  }
  pop.style.left = `${Math.round(left)}px`;
  pop.style.top = `${Math.round(top)}px`;

  const ctl = new AbortController();
  _recipientChipPopoverCtl = ctl;
  pop.querySelector('.recipient-chip-popover-copy')?.addEventListener('click', async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    try {
      const copied = await _copyTextToClipboard(email);
      if (!copied) throw new Error('copy failed');
      ev.currentTarget.classList.add('copied');
      showToast?.('Email copied');
      setTimeout(_closeRecipientChipPopover, 650);
    } catch (_) {
      showToast?.('Copy failed');
    }
  }, { signal: ctl.signal });
  setTimeout(() => {
    document.addEventListener('pointerdown', (ev) => {
      if (pop.contains(ev.target) || chip.contains(ev.target)) return;
      _closeRecipientChipPopover();
    }, { signal: ctl.signal, capture: true });
  }, 0);
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') _closeRecipientChipPopover();
  }, { signal: ctl.signal });
  window.addEventListener('resize', _closeRecipientChipPopover, { signal: ctl.signal });
  window.addEventListener('scroll', _closeRecipientChipPopover, { signal: ctl.signal, capture: true });
  return true;
}

export function _wireRecipientChips(root) {
  if (!root || root.dataset.recipientChipsWired === '1') return;
  root.dataset.recipientChipsWired = '1';
  root.addEventListener('click', async (ev) => {
    const copyBtn = ev.target.closest?.('.recipient-chip-copy');
    if (copyBtn && root.contains(copyBtn)) {
      ev.stopPropagation();
      ev.preventDefault();
      const chip = copyBtn.closest('.recipient-chip');
      const email = chip?.dataset.email || _emailAddressFromRecipientText(_decodeAttrValue(chip?.dataset.full || ''));
      if (!email) return;
      try {
        const copied = await _copyTextToClipboard(email);
        if (!copied) throw new Error('copy failed');
        copyBtn.classList.add('copied');
        copyBtn.title = 'Copied';
        showToast?.('Email copied');
        setTimeout(() => {
          copyBtn.classList.remove('copied');
          copyBtn.title = 'Copy email';
        }, 900);
      } catch (_) {
        showToast?.('Copy failed');
      }
      return;
    }

    const chip = ev.target.closest?.('.recipient-chip');
    if (!chip || !root.contains(chip)) return;
    ev.stopPropagation();
    ev.preventDefault();
    if (_showRecipientChipPopover(chip)) return;
    const label = chip.querySelector('.recipient-chip-label');
    const copy = chip.querySelector('.recipient-chip-copy');
    if (chip.classList.contains('expanded')) {
      chip.classList.remove('expanded');
      if (label) label.textContent = chip.dataset.name || label.textContent;
      if (copy) copy.hidden = true;
    } else {
      if (!chip.dataset.name && label) chip.dataset.name = label.textContent.trim();
      chip.classList.add('expanded');
      const expandedText = _decodeAttrValue(chip.dataset.full || '').trim()
        || chip.dataset.name
        || chip.dataset.email
        || label?.textContent?.trim()
        || '';
      if (label && expandedText) label.textContent = expandedText;
      if (copy) copy.hidden = false;
    }
  });
}

function _emailReaderForSelectAllTarget(target) {
  if (_isEmailTypingTarget(target)) return null;
  const direct = target?.closest?.('.email-card-reader, #email-lib-modal .doclib-card.doclib-card-expanded');
  if (direct) return direct.querySelector?.('.email-card-reader') || direct;
  const expanded = document.querySelector('#email-lib-modal:not(.hidden) .doclib-card.doclib-card-expanded .email-card-reader');
  if (expanded) return expanded;
  return _activeEmailReaderForSelectAll;
}

document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey) || String(e.key || '').toLowerCase() !== 'a') return;
  const reader = _emailReaderForSelectAllTarget(e.target);
  if (!_selectEmailReaderContents(reader)) return;
  e.preventDefault();
  e.stopPropagation();
  e.stopImmediatePropagation?.();
}, true);

function _emailReadContextKey(context) {
  return [context.accountId, context.folder, context.uid].map(value => String(value || '')).join('\u0000');
}

function _emailReadContextIsCurrent(context) {
  if (!context) return true;
  return (
    String(state._libAccountId || '') === context.accountId &&
    String(state._libFolder || 'INBOX') === context.libraryFolder &&
    _emailMailboxGeneration === context.mailboxGeneration
  );
}

function _emailMatchesReadContext(email, context) {
  if (String(email?.uid || '') !== context.uid) return false;
  const accountId = String(email?.account_id || context.accountId);
  const folder = String(email?.folder || context.folder);
  return accountId === context.accountId && folder === context.folder;
}

export function _syncEmailReadState(uid, isRead = true, context = null) {
  if (uid == null) return;
  const uidStr = String(uid);
  const read = !!isRead;
  if (context && (!_emailReadContextIsCurrent(context) || uidStr !== context.uid)) return;
  const match = (state._libEmails || []).find(x => (
    context ? _emailMatchesReadContext(x, context) : String(x.uid) === uidStr
  ));
  if (match) match.is_read = read;

  document.querySelectorAll('.doclib-card[data-uid="' + CSS.escape(uidStr) + '"]').forEach(card => {
    if (context && (
      String(card.dataset.emailAccount || '') !== context.accountId ||
      String(card.dataset.emailFolder || '') !== context.folder
    )) return;
    card.classList.toggle('email-card-unread', !read);
    const titleRow = card.querySelector('.email-card-titlerow');
    if (read) {
      card.querySelectorAll('.email-card-unread-dot, [data-unread-dot]').forEach(n => n.remove());
      if (titleRow) {
        titleRow.querySelectorAll('span').forEach(s => {
          const st = s.getAttribute('style') || '';
          if (/width:\s*6px/.test(st) && /border-radius:\s*50%/.test(st)) s.remove();
        });
      }
      return;
    }

    if (!titleRow || titleRow.querySelector('.email-card-unread-dot, [data-unread-dot]')) return;
    const isSentFolder = /sent/i.test(state._libFolder || '');
    if (isSentFolder) return;
    const senderName = match ? (match.from_name || match.from_address || '') : '';
    const dot = document.createElement('span');
    dot.className = 'email-card-unread-dot';
    dot.style.cssText = `width:6px;height:6px;border-radius:50%;background:${_senderColor(senderName)};flex-shrink:0;margin-left:2px;`;
    const done = titleRow.querySelector('.email-card-done');
    const navArrows = titleRow.querySelector('.email-card-nav-arrows');
    if (done) done.insertAdjacentElement('afterend', dot);
    else if (navArrows) titleRow.insertBefore(dot, navArrows);
    else titleRow.appendChild(dot);
  });
}

export function _syncEmailDoneState(uid, isDone = true, context = null) {
  if (uid == null) return;
  const uidStr = String(uid);
  const done = !!isDone;
  const match = (state._libEmails || []).find(x => (
    context ? _emailMatchesReadContext(x, context) : String(x.uid) === uidStr
  ));
  if (match) {
    match.is_answered = done;
    match.is_done = done;
  }
  document.querySelectorAll('.doclib-card[data-uid="' + CSS.escape(uidStr) + '"]').forEach(card => {
    if (context && (
      String(card.dataset.emailAccount || '') !== context.accountId ||
      String(card.dataset.emailFolder || '') !== context.folder
    )) return;
    card.classList.toggle('email-card-answered', done);
    const check = card.querySelector('.email-card-done');
    if (check) {
      check.classList.toggle('active', done);
      check.title = done ? 'Mark not done' : 'Mark done';
    }
    if (done) {
      card.querySelectorAll('.email-tag-urgent, .email-tag-reply-soon, .email-tag-action-needed').forEach(n => n.remove());
    }
  });
}

// When a reply is sent (from the doc editor), the source email is marked
// \Answered server-side and an `email-answered` event fires. Reflect that live
// so the email shows as done without waiting for a manual refresh.
window.addEventListener('email-answered', (e) => {
  const uid = e.detail && e.detail.uid;
  if (uid == null) return;
  const em = (state._libEmails || []).find(x => String(x.uid) === String(uid));
  if (em) {
    em.is_answered = true;
    em.is_done = true;
    em.is_read = true;
    _clearDoneResponseTagsLocal(em);
  }
  _syncEmailDoneState(uid, true);
  _syncEmailReadState(uid, true);
  document.querySelectorAll('.doclib-card[data-uid="' + CSS.escape(String(uid)) + '"]').forEach(card => {
    card.classList.add('email-card-answered');
    card.classList.remove('email-card-unread');
    card.querySelectorAll('.email-tag-urgent, .email-tag-reply-soon, .email-tag-action-needed').forEach(n => n.remove());
    const check = card.querySelector('.email-card-done');
    if (check) check.classList.add('active');
  });
});

function _toggleUnreadEmails() {
  if (state._libFolder === '__scheduled__') state._libFolder = 'INBOX';
  state._libFilter = state._libFilter === 'unread' ? 'all' : 'unread';
  _syncUnreadWindowGlow();
  const folderEl = document.getElementById('email-lib-folder');
  const filterEl = document.getElementById('email-lib-filter');
  if (folderEl) folderEl.value = state._libFolder || 'INBOX';
  if (filterEl) filterEl.value = state._libFilter;
  _syncSearchOptionsMenu();
  _renderSearchPills();
  _loadEmailsFresh();
}

function _syncUnreadTabBadge(count) {
  const label = count > 999 ? '999+ unread' : `${count} unread`;
  document.querySelectorAll('.minimized-dock-chip[data-modal-id="email-lib-modal"]').forEach(chip => {
    if (count > 0) {
      chip.dataset.emailUnreadLabel = label;
      chip.title = `Open ${label}`;
    } else {
      delete chip.dataset.emailUnreadLabel;
      chip.title = 'Restore Email';
    }
  });
}

function _syncCurrentAccountUnreadCount(count) {
  const accountId = String(state._libAccountId || '');
  if (!accountId) return;
  const nextCount = Math.max(0, Number(count) || 0);
  const prev = _accountUnreadState.get(accountId) || {};
  _accountUnreadState.set(accountId, {
    ...prev,
    unreadCount: nextCount,
  });
  _renderAccountsStrip();
}

function _syncUnreadWindowGlow() {
  document.getElementById('email-lib-modal')?.classList.toggle('email-lib-unread-active', state._libFilter === 'unread');
}

function _syncReminderClearButton() {
  document.getElementById('email-reminders-clear-btn')?.classList.toggle('hidden', state._libFilter !== 'reminders');
}

function _syncSearchOptionsMenu() {
  const btn = document.getElementById('email-search-options-btn');
  const menu = document.getElementById('email-search-options-menu');
  const hasDate = !!(state._libDateFrom || state._libDateTo);
  const hasActive = state._libFilter === 'undone' || state._libFilter === 'reminders' || !!state._libHasAttachments || hasDate;
  btn?.classList.toggle('active', hasActive);
  menu?.querySelector('[data-email-search-option="undone"]')?.classList.toggle('active', state._libFilter === 'undone');
  menu?.querySelector('[data-email-search-option="reminders"]')?.classList.toggle('active', state._libFilter === 'reminders');
  menu?.querySelector('[data-email-search-option="attachments"]')?.classList.toggle('active', !!state._libHasAttachments);
  menu?.querySelector('[data-email-search-option="date"]')?.classList.toggle('active', hasDate);
}

function _setEmailListFilter(value) {
  const filterEl = document.getElementById('email-lib-filter');
  state._libFilter = state._libFilter === value ? 'all' : value;
  if (filterEl) filterEl.value = state._libFilter;
  _syncUnreadWindowGlow();
  _syncReminderClearButton();
  _renderFilterPickerCurrent();
  _syncSearchOptionsMenu();
  _renderSearchPills();
  _loadEmailsFresh();
}

function _toggleEmailAttachmentFilter() {
  state._libHasAttachments = !state._libHasAttachments;
  _syncReminderClearButton();
  _syncSearchOptionsMenu();
  _renderSearchPills();
  _loadEmailsFresh();
}

function _renderAccountsLoading() {
  const strip = document.getElementById('email-lib-accounts');
  if (!strip) return;
  strip.style.display = 'flex';
  strip.innerHTML = '';
  try {
    const wp = spinnerModule.createWhirlpool(14);
    wp.element.classList.add('email-accounts-loading-whirlpool');
    strip.appendChild(wp.element);
  } catch (_) {}
}

function _syncEmailReminderBellVisibility(enabled) {
  const item = document.querySelector('#email-search-options-menu [data-email-search-option="reminders"]');
  item?.classList.toggle('hidden', !enabled);
  _syncSearchOptionsMenu();
}

async function _loadEmailReminderBellVisibility() {
  try {
    const settings = await getSettings();
    _syncEmailReminderBellVisibility(settings.reminder_channel === 'email');
  } catch (_) {
    _syncEmailReminderBellVisibility(false);
  }
}
// Live-update the bell when the reminder channel changes in Settings,
// so the user doesn't have to reopen Email to see the change apply.
window.addEventListener('odysseus-reminder-channel-changed', (e) => {
  const ch = e?.detail?.channel;
  _syncEmailReminderBellVisibility(ch === 'email');
});

function _readCssPx(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function _emailSplitLeftEdge() {
  return _readCssPx('--icon-rail-w') + _readCssPx('--sidebar-w');
}

function _setEmailDocumentSplit(leftEdge, emailWidth) {
  if (window.innerWidth <= 768) return;
  // Zero gap so the doc-pane sits flush against the email's right edge.
  // modalSnap.js's left-dock path publishes the same vars with 0 gap — both
  // systems agree on flush so handoffs between them don't cause the doc to
  // "jump" sideways. The 1px modal border on each side is the visual seam.
  const splitGap = 0;
  const left = Math.max(0, Math.round(leftEdge || 0));
  const width = Math.max(320, Math.round(emailWidth || 420));
  const x = left + width + splitGap;
  document.body.classList.add('email-doc-split-active');
  document.documentElement.style.setProperty('--email-doc-split-left-x', `${left}px`);
  document.documentElement.style.setProperty('--email-doc-split-email-w', `${width}px`);
  document.documentElement.style.setProperty('--email-doc-split-right-x', `${x}px`);
}

function _measureEmailDocumentSplit(modal) {
  if (window.innerWidth <= 768 || !document.body.classList.contains('email-doc-split-active')) return;
  const content = modal?.querySelector?.('.modal-content');
  const rect = content?.getBoundingClientRect?.();
  if (!rect || !rect.width) return;
  const splitGap = 0;
  document.documentElement.style.setProperty('--email-doc-split-right-x', `${Math.ceil(rect.right + splitGap)}px`);
  try {
    modal.style.setProperty('z-index', '150', 'important');
    if (content) {
      content.style.setProperty('position', 'absolute', 'important');
      content.style.setProperty('left', '0px', 'important');
      content.style.setProperty('right', 'auto', 'important');
      content.style.setProperty('width', `${Math.ceil(rect.width)}px`, 'important');
      content.style.setProperty('max-width', `${Math.ceil(rect.width)}px`, 'important');
    }
    const docPane = document.getElementById('doc-editor-pane');
    if (docPane) {
      docPane.style.setProperty('position', 'fixed', 'important');
      docPane.style.setProperty('left', `${Math.ceil(rect.right + splitGap)}px`, 'important');
      docPane.style.setProperty('right', '0px', 'important');
      docPane.style.setProperty('top', '0px', 'important');
      docPane.style.setProperty('bottom', '0px', 'important');
      docPane.style.setProperty('width', 'auto', 'important');
      docPane.style.setProperty('max-width', 'none', 'important');
      docPane.style.setProperty('height', '100vh', 'important');
      docPane.style.setProperty('z-index', '260', 'important');
    }
  } catch (_) {}
}

function _scheduleEmailDocumentSplitMeasure(modal) {
  requestAnimationFrame(() => {
    _measureEmailDocumentSplit(modal);
    requestAnimationFrame(() => _measureEmailDocumentSplit(modal));
  });
  setTimeout(() => _measureEmailDocumentSplit(modal), 260);
  setTimeout(() => _measureEmailDocumentSplit(modal), 700);
}

function _clearEmailDocumentSplit() {
  document.body.classList.remove('email-doc-split-active');
  document.documentElement.style.removeProperty('--email-doc-split-left-x');
  document.documentElement.style.removeProperty('--email-doc-split-email-w');
  document.documentElement.style.removeProperty('--email-doc-split-right-x');
  const docPane = document.getElementById('doc-editor-pane');
  if (!docPane) return;
  [
    'position', 'left', 'right', 'top', 'bottom', 'width', 'max-width',
    'height', 'z-index', 'transform',
  ].forEach(prop => docPane.style.removeProperty(prop));
}

// Compute the left-edge x assuming the wide sidebar has collapsed to the
// rail. Used by the "try collapsing the sidebar first" path so we can decide
// whether collapsing recovers enough room before minimizing email.
function _emailSplitLeftEdgeIfSidebarCollapsed() {
  return _readCssPx('--icon-rail-w');
}

function _hasDesktopRoomForEmailAndDocument(modal, opts = {}) {
  if (window.innerWidth <= 768) return false;
  if (window.innerWidth >= 1100) return true;
  const content = modal?.querySelector?.('.modal-content');
  const rect = content?.getBoundingClientRect?.();
  const isFullscreen = modal?.classList?.contains('email-lib-fullscreen')
    || modal?.classList?.contains('email-window-fullscreen');
  const emailWidth = isFullscreen
    ? Math.min(440, Math.max(360, Math.round(window.innerWidth * 0.30)))
    : Math.max(360, Math.round(rect?.width || 440));
  // Relaxed thresholds — the old 560 + 72 forced an unnecessary tab-down
  // on ~1200–1300px viewports where there was visually plenty of room.
  const docMinWidth = 460;
  const breathingRoom = 40;
  const leftEdgeNow = isFullscreen ? _emailSplitLeftEdge() : Math.max(0, Math.round(rect?.left || _emailSplitLeftEdge()));
  const leftEdge = opts.assumeSidebarCollapsed ? _emailSplitLeftEdgeIfSidebarCollapsed() : leftEdgeNow;
  return (window.innerWidth - leftEdge - emailWidth) >= (docMinWidth + breathingRoom);
}

export function _prepareEmailWindowForDocument(modal) {
  if (window.innerWidth <= 768) return true;
  if (!modal) return false;
  // Try to make breathing room by collapsing the wide sidebar to the rail
  // when there isn't enough horizontal space for both panes. The
  // route-collapse marker that collapseSidebarToRail() sets means the
  // sidebar will auto-restore when the doc closes. Crucially, we no
  // longer fall back to clearing the split when even that isn't enough —
  // the user opted out of auto-tab-down, so we proceed with the dock
  // even if it's cramped.
  if (!_hasDesktopRoomForEmailAndDocument(modal)) {
    const sidebar = document.getElementById('sidebar');
    const sidebarWasOpen = sidebar && !sidebar.classList.contains('hidden');
    if (sidebarWasOpen && _hasDesktopRoomForEmailAndDocument(modal, { assumeSidebarCollapsed: true })) {
      try { collapseSidebarToRail(); } catch (_) {}
    }
  }
  if (modal.classList.contains('modal-left-docked')) {
    const content = modal.querySelector('.modal-content');
    const rect = content?.getBoundingClientRect?.();
    if (content?._leftDockNavObs) {
      try { content._leftDockNavObs.navObs.disconnect(); } catch (_) {}
      try { content._leftDockNavObs.bodyObs && content._leftDockNavObs.bodyObs.disconnect(); } catch (_) {}
      try { content._leftDockNavObs.disconnectDocObs && content._leftDockNavObs.disconnectDocObs(); } catch (_) {}
      try { window.removeEventListener('resize', content._leftDockNavObs.reanchor); } catch (_) {}
      delete content._leftDockNavObs;
    }
    modal.classList.remove('modal-left-docked');
    modal.classList.add('email-snap-left');
    document.body.classList.remove('left-dock-active');
    document.documentElement.style.removeProperty('--left-dock-w');
    if (content) {
      delete content._dockSide;
      content.style.position = 'fixed';
      content.style.left = Math.round(rect?.left || _emailSplitLeftEdge()) + 'px';
      content.style.top = '0';
      content.style.right = 'auto';
      content.style.bottom = '0';
      content.style.width = Math.round(rect?.width || 440) + 'px';
      content.style.maxWidth = Math.round(rect?.width || 440) + 'px';
      content.style.height = '100vh';
      content.style.maxHeight = '100vh';
      content.style.borderRadius = '0';
      content.style.transform = 'none';
      content.style.margin = '0';
    }
  }
  if (modal.classList.contains('email-snap-left') || modal.classList.contains('modal-left-docked')) {
    const rect = modal.querySelector('.modal-content')?.getBoundingClientRect?.();
    _setEmailDocumentSplit(rect?.left || _emailSplitLeftEdge(), rect?.width || 420);
    _scheduleEmailDocumentSplitMeasure(modal);
    return false;
  }
  // If Email is fullscreen and there is room, park it left instead of
  // minimizing so the document/compose pane can open beside it.
  _snapEmailModalToLeftSidebar(modal);
  return false;
}

function _wireUnreadTabClick() {
  if (_emailUnreadChipClickWired) return;
  _emailUnreadChipClickWired = true;
  document.addEventListener('click', (e) => {
    const chip = e.target?.closest?.('.minimized-dock-chip[data-modal-id="email-lib-modal"][data-email-unread-label]');
    if (!chip || e.target?.classList?.contains('minimized-dock-x')) return;
    setTimeout(_toggleUnreadEmails, 0);
  });
}

async function _deleteEmailAndAdvance(em, card, opts = {}) {
  if (!em || em.uid == null) return;
  if (opts.confirm !== false) {
    const subject = em.subject || '(no subject)';
    const ok = await styledConfirm(`Delete "${subject}"?`, { confirmText: 'Delete', cancelText: 'Cancel', danger: true });
    if (!ok) return;
  }
  const busy = _showEmailDeleteOverlay(card);
  await busy?.ready;
  const wasExpanded = !!card?.classList?.contains('doclib-card-expanded');
  const sibling = wasExpanded
    ? (_findSiblingEmailCard(card, +1) || _findSiblingEmailCard(card, -1))
    : null;
  const nextUid = sibling ? sibling.dataset.uid : null;
  try {
    const response = await fetch(`${API_BASE}/api/email/delete/${encodeURIComponent(em.uid)}?${_emailMutationQuery(em)}`, { method: 'DELETE' });
    await _requireSuccessfulEmailMutation(response, 'Failed to delete email');
  } catch (err) {
    console.error('Failed to delete email:', err);
    busy?.remove?.();
    showToast('Failed to delete email');
    return;
  }
  busy?.remove?.();
  await _animateEmailCardRemoval([em.uid]);
  state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
  state._selectedUids.delete(em.uid);
  _updateBulkBar();
  _renderGrid();
  _libCacheWriteBack();
  showToast('Moved to Trash');
  if (!wasExpanded || !nextUid) return;
  const grid = document.getElementById('email-lib-grid');
  const nextCard = grid?.querySelector(`.doclib-card[data-uid="${CSS.escape(String(nextUid))}"]`);
  const nextEm = state._libEmails.find(e => String(e.uid) === String(nextUid));
  if (nextCard && nextEm) {
    await _toggleCardPreview(nextCard, nextEm);
    nextCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } else {
    document.getElementById('email-lib-modal')?.classList.remove('email-reading');
  }
}

export function _showEmailDeleteOverlay(target) {
  if (!target) return null;
  const wp = spinnerModule.createWhirlpool(18);
  const overlay = document.createElement('div');
  overlay.className = 'email-delete-overlay';
  overlay.appendChild(wp.element);
  const prevPos = target.style.position;
  const prevPointerEvents = target.style.pointerEvents;
  if (getComputedStyle(target).position === 'static') target.style.position = 'relative';
  target.style.pointerEvents = 'none';
  target.classList.add('email-delete-busy');
  target.appendChild(overlay);
  const ready = new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return {
    ready,
    remove() {
      try { wp.destroy?.(); } catch (_) {}
      overlay.remove();
      target.classList.remove('email-delete-busy');
      target.style.pointerEvents = prevPointerEvents;
      target.style.position = prevPos;
    }
  };
}

export function _animateEmailCardRemoval(uids, opts = {}) {
  const uidSet = new Set((uids || []).map(uid => String(uid)));
  if (!uidSet.size) return Promise.resolve();
  const grid = document.getElementById('email-lib-grid');
  if (!grid) return Promise.resolve();
  const cards = Array.from(grid.querySelectorAll('.doclib-card[data-uid]'))
    .filter(card => uidSet.has(String(card.dataset.uid)));
  if (!cards.length) return Promise.resolve();
  const duration = Number(opts.duration || 230);

  for (const card of cards) {
    const rect = card.getBoundingClientRect();
    card.style.setProperty('--email-remove-h', `${Math.max(rect.height, card.scrollHeight)}px`);
    card.style.maxHeight = 'var(--email-remove-h)';
    card.style.overflow = 'hidden';
    card.classList.add('email-card-removing');
  }

  return new Promise(resolve => {
    window.setTimeout(resolve, duration + 35);
  });
}


function _selectedEmailExportPayload() {
  const selected = new Set(Array.from(state._selectedUids || []).map(uid => String(uid)));
  return (state._libEmails || [])
    .filter(em => selected.has(String(em.uid)))
    .map(em => ({
      uid: String(em.uid || ''),
      folder: String(em.folder || state._libFolder || 'INBOX'),
      account_id: String(em.account_id || state._libAccountId || ''),
      subject: String(em.subject || ''),
      date: (() => {
        const parsed = em.date_epoch ? new Date(Number(em.date_epoch) * 1000) : new Date(em.date || '');
        return Number.isFinite(parsed.getTime()) ? parsed.toISOString().slice(0, 10) : '';
      })(),
    }))
    .filter(em => em.uid);
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'selected-email-attachments.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function _filenameFromContentDisposition(header) {
  const value = String(header || '');
  const utf = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf) {
    try { return decodeURIComponent(utf[1]); } catch (_) {}
  }
  const quoted = value.match(/filename="([^"]+)"/i);
  if (quoted) return quoted[1];
  const bare = value.match(/filename=([^;]+)/i);
  return bare ? bare[1].trim() : '';
}

export async function _exportSelectedAttachments() {
  const messages = _selectedEmailExportPayload();
  if (!messages.length) {
    showToast('Select emails first');
    return;
  }
  const actionsBtn = document.getElementById('email-lib-bulk-actions');
  const deleteBtn = document.getElementById('email-lib-bulk-delete');
  const cancelBtn = document.getElementById('email-lib-bulk-cancel');
  const selectAll = document.getElementById('email-lib-select-all');
  const countEl = document.getElementById('email-lib-selected-count');
  const originalActionsHtml = actionsBtn?.innerHTML || '';
  const originalCountText = countEl?.textContent || '';
  let busySpinner = null;
  try {
    if (actionsBtn) {
      actionsBtn.disabled = true;
      actionsBtn.classList.add('email-bulk-loading');
      actionsBtn.innerHTML = '<span class="email-bulk-loading-label">Exporting</span>';
      busySpinner = spinnerModule.create('', 'clean', 'whirlpool');
      const spEl = busySpinner.createElement();
      spEl.classList.add('email-bulk-whirlpool');
      actionsBtn.appendChild(spEl);
      busySpinner.start();
    }
    if (deleteBtn) deleteBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (selectAll) selectAll.disabled = true;
    if (countEl) countEl.textContent = `Exporting attachments from ${messages.length}…`;
    const res = await fetch(`${API_BASE}/api/email/attachments-download-bulk`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages,
        category: String(state._libFilter || '').replace(/^tag:/, ''),
      }),
    });
    if (!res.ok) {
      let detail = '';
      try {
        const data = await res.json();
        detail = data?.detail || data?.error || '';
      } catch (_) {}
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const filename = _filenameFromContentDisposition(res.headers.get('Content-Disposition')) || 'selected-email-attachments.zip';
    _downloadBlob(blob, filename);
    showToast(`Exported attachments from ${messages.length} selected email${messages.length === 1 ? '' : 's'}`);
  } catch (err) {
    console.error('Failed to export selected email attachments:', err);
    showToast(err?.message || 'Failed to export attachments');
  } finally {
    if (busySpinner) busySpinner.destroy();
    if (actionsBtn) {
      actionsBtn.disabled = false;
      actionsBtn.classList.remove('email-bulk-loading');
      actionsBtn.innerHTML = originalActionsHtml || actionsBtn.innerHTML;
    }
    if (deleteBtn) deleteBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = false;
    if (selectAll) selectAll.disabled = false;
    if (countEl) countEl.textContent = originalCountText;
    _updateBulkBar();
  }
}

// URL-suffix helper — appends &account_id=... when an account is actively selected.
// Every email route call in this file goes through here so switching accounts
// is a single-variable flip.
// Open the Settings modal and activate a specific tab. Used by empty-state
// "Set up at: Settings › X" links across email/calendar/etc.
function _openSettingsTab(tab) {
  if (tab === 'integrations' && window.adminModule && typeof window.adminModule.open === 'function') {
    window.adminModule.open('integrations');
    return;
  }
  if (settingsModule && typeof settingsModule.open === 'function') {
    settingsModule.open(tab || 'services');
    return;
  }
  const modal = document.getElementById('settings-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  const tabBtn = modal.querySelector(`[data-settings-tab="${tab || 'services'}"]`);
  if (tabBtn) tabBtn.click();
}

function _openGlobalEmailSettings(focus) {
  const host = document.getElementById('settings-email-preferences');
  if (host && focus) host.dataset.emailSettingsFocus = focus;
  _openSettingsTab('email');
  setTimeout(() => {
    const section = focus === 'show-tags'
      ? document.querySelector('#settings-email-preferences .email-settings-display-section')
      : document.querySelector(`#settings-email-preferences .email-settings-${focus || 'style'}-section`);
    if (section) {
      section.scrollIntoView?.({ block: 'nearest' });
    }
  }, 0);
}

function _emailSetupHintHtml() {
  return '<div style="margin-top:6px;opacity:0.72;font-size:11px;">' +
    'Setup: <a href="#" data-open-settings="integrations" style="color:var(--accent,var(--red));text-decoration:underline;">Settings &rsaquo; Integrations</a>' +
    '</div>';
}

function _wireEmailSetupHint(root) {
  root?.querySelectorAll?.('[data-open-settings]').forEach(link => {
    if (link.dataset.emailSetupBound === '1') return;
    link.dataset.emailSetupBound = '1';
    link.addEventListener('click', (e) => {
      e.preventDefault();
      _openSettingsTab(link.dataset.openSettings || 'integrations');
    });
  });
}

export function _acct() {
  return state._libAccountId ? `&account_id=${encodeURIComponent(state._libAccountId)}` : '';
}

export function _emailMutationQuery(em, fallbackFolder = state._libFolder) {
  const folder = String(em?.folder || fallbackFolder || 'INBOX');
  const accountId = em?.account_id || state._libAccountId || '';
  const params = new URLSearchParams({ folder });
  if (accountId) params.set('account_id', String(accountId));
  if (em?.message_id) params.set('message_id', String(em.message_id));
  return params.toString();
}

export async function _requireSuccessfulEmailMutation(response, fallback = 'Email operation failed') {
  const data = await response.json().catch(() => null);
  if (!response.ok || data?.success !== true) {
    throw new Error(data?.error || `${fallback} (${response.status})`);
  }
  return data;
}


function _rememberedEmailAccountId() {
  try {
    return String(localStorage.getItem(_LIB_LAST_ACCOUNT_KEY) || '').trim();
  } catch (_) {
    return '';
  }
}

// Per-(account, folder, filter, attachments) cache of the most recent
// first-page list response. Lets open-after-refresh paint the previous
// list instantly while the network refresh runs behind it. Search results
// and __scheduled__ are deliberately not cached.
const _libListCache = new Map();
const _LIB_CACHE_MAX = 24;
const _LIB_INITIAL_PAGE_SIZE = 100;
const _LIB_SESSION_CACHE_PREFIX = 'odysseus.email.list.';
const _LIB_SESSION_CACHE_TTL_MS = 10 * 60 * 1000;
const _LIB_PERSIST_CACHE_PREFIX = 'odysseus.email.list.v2.';
const _LIB_PERSIST_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const _LIB_PERSIST_CACHE_MAX = 12;
const _LIB_LAST_ACCOUNT_KEY = 'odysseus.email.lastAccountId';
const _LIB_PREWARM_COOLDOWN_MS = 5 * 60 * 1000;
let _libPrewarmDelayTimer = null;
let _libPrewarmIdleHandle = null;
let _libPrewarmPromise = null;
let _libPrewarmResolve = null;
let _libPrewarmAbortController = null;
let _libPrewarmDetachPriorityListeners = null;
let _libPrewarmGeneration = 0;
let _libLastPrewarmAt = 0;
let _libUnreadPrewarmKey = '';
let _libUnreadPrewarmAt = 0;
let _libRenderedViewKey = '';
let _libSyncStatus = {
  updatedAt: '',
  source: '',
  warming: false,
  loading: false,
};
let _libSyncTicker = null;

function _libSyncDateFrom(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isFinite(d.getTime()) ? d : null;
}

function _libRelativeTime(value) {
  const d = _libSyncDateFrom(value);
  if (!d) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (seconds < 45) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function _renderEmailSyncStatus() {
  const el = document.getElementById('email-lib-sync-status');
  if (!el) return;
  const parts = [];
  const rel = _libRelativeTime(_libSyncStatus.updatedAt);
  if (rel) parts.push(`Last: ${rel}`);
  if (_libSyncStatus.loading) {
    if (state._libEmails.length) parts.push('Updating...');
    el.textContent = parts.join(' · ');
    el.style.visibility = parts.length ? 'visible' : 'hidden';
    return;
  }
  el.textContent = parts.join(' · ');
  el.style.visibility = parts.length ? 'visible' : 'hidden';
}

function _setEmailSyncStatus(next = {}) {
  if (Object.prototype.hasOwnProperty.call(next, 'updatedAt')) {
    const updatedAt = next.updatedAt || '';
    if (!updatedAt) {
      _libSyncStatus.updatedAt = _libSyncStatus.updatedAt || '';
    } else if (_libSyncDateFrom(updatedAt)) {
      _libSyncStatus.updatedAt = updatedAt;
    }
  }
  if (Object.prototype.hasOwnProperty.call(next, 'source')) {
    _libSyncStatus.source = next.source || '';
  }
  if (Object.prototype.hasOwnProperty.call(next, 'warming')) {
    _libSyncStatus.warming = Boolean(next.warming);
  }
  if (Object.prototype.hasOwnProperty.call(next, 'loading')) {
    _libSyncStatus.loading = Boolean(next.loading);
  }
  _renderEmailSyncStatus();
}

function _libCacheKeyFor(accountId, folder, filter, hasAttachments) {
  return [
    accountId || '',
    folder || '',
    filter || '',
    hasAttachments ? 1 : 0,
    state._libDateFrom || '',
    state._libDateTo || '',
  ].join('|');
}
function _libCacheKey() {
  return _libCacheKeyFor(
    state._libAccountId || '',
    state._libFolder || '',
    state._libFilter || '',
    state._libHasAttachments
  );
}
function _libSessionCacheKey(key) {
  return _LIB_SESSION_CACHE_PREFIX + encodeURIComponent(String(key || ''));
}
function _libPersistCacheKey(key) {
  return _LIB_PERSIST_CACHE_PREFIX + encodeURIComponent(String(key || ''));
}
function _libSessionCacheGet(key) {
  try {
    const raw = sessionStorage.getItem(_libSessionCacheKey(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !parsed.savedAt || (Date.now() - parsed.savedAt) > _LIB_SESSION_CACHE_TTL_MS) {
      sessionStorage.removeItem(_libSessionCacheKey(key));
      return null;
    }
    return parsed.value || null;
  } catch (_) {
    return null;
  }
}
function _libPersistCacheGet(key) {
  try {
    const storageKey = _libPersistCacheKey(key);
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !parsed.savedAt || (Date.now() - parsed.savedAt) > _LIB_PERSIST_CACHE_TTL_MS) {
      localStorage.removeItem(storageKey);
      return null;
    }
    return parsed.value || null;
  } catch (_) {
    return null;
  }
}
function _libSessionCachePut(key, value) {
  try {
    sessionStorage.setItem(_libSessionCacheKey(key), JSON.stringify({ savedAt: Date.now(), value }));
  } catch (_) {}
}
function _libPrunePersistCache() {
  try {
    const entries = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(_LIB_PERSIST_CACHE_PREFIX)) continue;
      let savedAt = 0;
      try {
        savedAt = Number(JSON.parse(localStorage.getItem(key) || '{}')?.savedAt || 0);
      } catch (_) {}
      entries.push({ key, savedAt });
    }
    entries.sort((a, b) => b.savedAt - a.savedAt);
    for (const stale of entries.slice(_LIB_PERSIST_CACHE_MAX)) {
      localStorage.removeItem(stale.key);
    }
  } catch (_) {}
}
function _libPersistCachePut(key, value) {
  try {
    localStorage.setItem(_libPersistCacheKey(key), JSON.stringify({ savedAt: Date.now(), value }));
    _libPrunePersistCache();
  } catch (_) {
    try { localStorage.removeItem(_libPersistCacheKey(key)); } catch {}
  }
}
function _looksLikeFixtureEmailRow(row) {
  if (!row || typeof row !== 'object') return false;
  const account = String(row.account || row._account || row.account_id || row._account_id || '').toLowerCase();
  const from = `${row.from || ''} ${row.from_name || ''} ${row.from_address || ''}`.toLowerCase();
  const subject = String(row.subject || '').toLowerCase();
  const messageId = String(row.message_id || '').toLowerCase();
  return account.includes('fixture')
      || from.includes('fixture@example')
      || from.includes('older fixture')
      || subject === 'older inbox message'
      || messageId.includes('fixture-email-');
}
function _libCacheHasFixtureRows(value) {
  return Boolean(value && Array.isArray(value.emails) && value.emails.some(_looksLikeFixtureEmailRow));
}
function _libDropCacheKey(key) {
  _libListCache.delete(key);
  try { sessionStorage.removeItem(_libSessionCacheKey(key)); } catch (_) {}
  try { localStorage.removeItem(_libPersistCacheKey(key)); } catch (_) {}
}
function _libCacheGet(key) {
  const memory = _libListCache.get(key);
  if (memory) {
    if (_libCacheHasFixtureRows(memory)) {
      _libDropCacheKey(key);
      return null;
    }
    return memory;
  }
  const stored = _libSessionCacheGet(key) || _libPersistCacheGet(key);
  if (stored) {
    if (_libCacheHasFixtureRows(stored)) {
      _libDropCacheKey(key);
      return null;
    }
    _libListCache.set(key, stored);
    return stored;
  }
  return null;
}
function _libCachePut(key, value) {
  if (_libCacheHasFixtureRows(value)) {
    _libDropCacheKey(key);
    return;
  }
  // Re-insert to bump LRU recency.
  _libListCache.delete(key);
  _libListCache.set(key, value);
  _libSessionCachePut(key, value);
  _libPersistCachePut(key, value);
  if (_libListCache.size > _LIB_CACHE_MAX) {
    const oldest = _libListCache.keys().next().value;
    _libListCache.delete(oldest);
  }
}

function _resetBulkSelectionForContextChange({ rerender = false } = {}) {
  const hadSelection = !!(state._selectedUids && state._selectedUids.size);
  const wasSelectMode = !!state._selectMode;
  if (state._selectedUids) state._selectedUids.clear();
  state._selectMode = false;
  if (hadSelection || wasSelectMode) {
    _updateBulkBar();
    if (rerender) _renderGrid();
  }
}

export function _resetEmailListForFreshLoad({ useCache = true, refreshAfterCache = false } = {}) {
  _exitEmailReaderModeForList();
  _resetBulkSelectionForContextChange();
  state._libOffset = 0;
  _emailMailboxGeneration += 1;
  _libLoadSeq += 1;
  const ck = _libCacheKey();
  const cached = useCache ? _libCacheGet(ck) : null;
  if (cached && Array.isArray(cached.emails) && cached.emails.length) {
    state._libEmails = cached.emails.slice();
    state._libTotal = cached.total || state._libEmails.length;
    state._libJustOpened = false;
    _renderGrid();
    const stats = document.getElementById('email-lib-stats');
    if (stats) stats.textContent = `${state._libTotal} emails`;
    _setEmailSyncStatus({ updatedAt: cached.sync?.updated_at || '', source: cached.sync?.source || 'client_cache', loading: refreshAfterCache });
    _libRenderedViewKey = ck;
    return;
  }
  if (state._libEmails.length && _libRenderedViewKey === ck) {
    _renderGrid();
    _setEmailSyncStatus({ loading: false });
    return;
  }
  state._libEmails = [];
  state._libTotal = 0;
  _libRenderedViewKey = ck;
  const grid = document.getElementById('email-lib-grid');
  if (grid) _renderEmailLoading(grid);
  const stats = document.getElementById('email-lib-stats');
  if (stats) stats.textContent = 'Loading...';
  _setEmailSyncStatus({ loading: true });
}

function _exitEmailReaderModeForList() {
  const modal = document.getElementById('email-lib-modal');
  modal?.classList.remove('email-reading');
  modal?.style.removeProperty('--email-reading-modal-min-h');
  const grid = document.getElementById('email-lib-grid');
  grid?.querySelectorAll('.email-card-expanded, .doclib-card-expanded').forEach(card => {
    unbindExpandedCardDismiss(card);
    card.classList.remove('email-card-expanded');
    card.classList.remove('doclib-card-expanded');
    card.style.minHeight = '';
    card.querySelector('.email-card-reader')?.remove();
  });
}

export function _loadEmailsFresh({ force = true, useCache = true, refreshAfterCache = true, showRefreshSpinner = false } = {}) {
  const refreshBtn = showRefreshSpinner ? document.getElementById('email-lib-refresh-btn') : null;
  refreshBtn?.classList.add('email-lib-refreshing');
  const resolvedFolder = _resolveEmailFolderAlias(state._libFolder);
  if (resolvedFolder && resolvedFolder !== state._libFolder) {
    state._libFolder = resolvedFolder;
    const folderSel = document.getElementById('email-lib-folder');
    if (folderSel) folderSel.value = resolvedFolder;
    _renderFolderPicker();
  }
  _resetEmailListForFreshLoad({ useCache, refreshAfterCache });
  return _loadEmails({ force, useCache }).finally(() => {
    refreshBtn?.classList.remove('email-lib-refreshing');
  });
}

async function _refreshEmailLibraryFromUi(btn = null) {
  btn?.classList.add('email-lib-refreshing');
  state._libOffset = 0;
  // Don't wipe state._libEmails — _loadEmails will paint the current
  // list while the forced refetch runs, so the grid doesn't blank out
  // mid-refresh. `force: true` adds the cache-buster so the server's
  // 8s list cache is bypassed for an actually-fresh result.
  try {
    await _loadEmails({ force: true });
  } finally {
    btn?.classList.remove('email-lib-refreshing');
    // Flash a checkmark for ~900ms so the user gets a clear "done" cue.
    if (btn) {
      const orig = btn.innerHTML;
      btn.classList.add('email-lib-refresh-done');
      btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;"><polyline points="20 6 9 17 4 12"/></svg>';
      setTimeout(() => {
        if (btn.classList.contains('email-lib-refresh-done')) {
          btn.classList.remove('email-lib-refresh-done');
          btn.innerHTML = orig;
        }
      }, 900);
    }
  }
}

// Refresh the currently visible mailbox after another surface sends a message.
// Keep the user's folder, filters, and scroll context intact.
export async function refreshEmailLibrary() {
  if (!state._libOpen) return false;
  await _loadEmailsFresh({
    force: true,
    useCache: false,
    refreshAfterCache: false,
    showRefreshSpinner: true,
  });
  return true;
}

function _initMobileEmailPullRefresh() {
  const grid = document.getElementById('email-lib-grid');
  const modal = document.getElementById('email-lib-modal');
  const host = modal?.querySelector('.admin-card');
  if (!grid || !host || grid.dataset.pullRefreshBound === '1') return;
  if (!('ontouchstart' in window || navigator.maxTouchPoints > 0)) return;
  grid.dataset.pullRefreshBound = '1';

  const THRESHOLD = 72;
  const MAX_PULL = 104;
  let startY = 0;
  let pullY = 0;
  let tracking = false;
  let refreshing = false;

  const indicator = document.createElement('div');
  indicator.className = 'chat-pull-refresh email-pull-refresh';
  indicator.setAttribute('aria-hidden', 'true');
  indicator.innerHTML = '<div class="chat-pull-refresh-spinner"></div>';
  host.prepend(indicator);
  const spinnerMount = indicator.querySelector('.chat-pull-refresh-spinner');
  try {
    const spinner = spinnerModule.createWhirlpool(18);
    spinnerMount.replaceChildren(spinner.element);
  } catch (_) {}

  function setPull(px, active = false) {
    pullY = Math.max(0, Math.min(MAX_PULL, px));
    const pct = Math.min(1, pullY / THRESHOLD);
    indicator.style.setProperty('--pull-refresh-y', `${pullY}px`);
    indicator.style.setProperty('--pull-refresh-progress', `${pct}`);
    indicator.classList.toggle('is-visible', active || refreshing || pullY > 2);
    indicator.classList.toggle('is-ready', pct >= 1 && !refreshing);
    indicator.classList.toggle('is-refreshing', refreshing);
  }

  async function runRefresh() {
    if (refreshing) return;
    refreshing = true;
    setPull(THRESHOLD, true);
    try {
      await _refreshEmailLibraryFromUi(document.getElementById('email-lib-refresh-btn'));
    } catch (err) {
      console.warn('email pull refresh failed:', err);
    } finally {
      refreshing = false;
      setPull(0, false);
    }
  }

  grid.addEventListener('touchstart', (e) => {
    if (refreshing || window.innerWidth > 768) return;
    if (grid.scrollTop > 0) return;
    if (e.target?.closest?.('button, input, textarea, select, a, .email-card-reader, .doclib-card-expanded')) return;
    tracking = true;
    startY = e.touches[0].clientY;
    setPull(0, false);
  }, { passive: true });

  grid.addEventListener('touchmove', (e) => {
    if (!tracking || refreshing) return;
    const dy = e.touches[0].clientY - startY;
    if (dy <= 0) {
      setPull(0, false);
      return;
    }
    if (grid.scrollTop <= 0) {
      e.preventDefault();
      setPull(dy * 0.62, true);
    }
  }, { passive: false });

  grid.addEventListener('touchend', () => {
    if (!tracking) return;
    tracking = false;
    if (pullY >= THRESHOLD) runRefresh();
    else setPull(0, false);
  }, { passive: true });

  grid.addEventListener('touchcancel', () => {
    tracking = false;
    if (!refreshing) setPull(0, false);
  }, { passive: true });
}

function _isChatInteractionBusy() {
  try {
    if (window.__odysseusChatBusy) return true;
    const until = Number(window.__odysseusChatBusyUntil || 0);
    return until > Date.now();
  } catch (_) {
    return false;
  }
}

function _canRunEmailPrewarm() {
  if (state._libOpen || state._libLoading || _libSearchInFlight) return false;
  if (document.visibilityState && document.visibilityState !== 'visible') return false;
  return !_isChatInteractionBusy();
}

function _isEmailPrewarmTemporarilyBlocked() {
  if (state._libOpen || state._libLoading || _libSearchInFlight) return false;
  if (document.visibilityState && document.visibilityState !== 'visible') return false;
  return _isChatInteractionBusy();
}

function _isEmailPrewarmCurrent(generation, signal) {
  return generation === _libPrewarmGeneration
    && !signal?.aborted
    && _canRunEmailPrewarm();
}

function _settleEmailPrewarm(generation, value = false) {
  if (generation !== _libPrewarmGeneration) return;
  const resolve = _libPrewarmResolve;
  const detachPriorityListeners = _libPrewarmDetachPriorityListeners;
  _libPrewarmDelayTimer = null;
  _libPrewarmIdleHandle = null;
  _libPrewarmPromise = null;
  _libPrewarmResolve = null;
  _libPrewarmAbortController = null;
  _libPrewarmDetachPriorityListeners = null;
  detachPriorityListeners?.();
  resolve?.(value);
}

function _cancelEmailPrewarm() {
  const resolve = _libPrewarmResolve;
  const detachPriorityListeners = _libPrewarmDetachPriorityListeners;
  _libPrewarmGeneration += 1;
  if (_libPrewarmDelayTimer !== null) {
    clearTimeout(_libPrewarmDelayTimer);
  }
  if (_libPrewarmIdleHandle !== null && typeof window.cancelIdleCallback === 'function') {
    try { window.cancelIdleCallback(_libPrewarmIdleHandle); } catch (_) {}
  }
  try { _libPrewarmAbortController?.abort(); } catch (_) {}
  _libPrewarmDelayTimer = null;
  _libPrewarmIdleHandle = null;
  _libPrewarmPromise = null;
  _libPrewarmResolve = null;
  _libPrewarmAbortController = null;
  _libPrewarmDetachPriorityListeners = null;
  detachPriorityListeners?.();
  resolve?.(false);
}

function _scheduleEmailPrewarm(task, { delay = 0 } = {}) {
  if (_libPrewarmPromise) return _libPrewarmPromise;
  // Do not disguise a timer as idle work. Browsers without the genuine idle
  // callback simply skip this optional optimization and load on demand.
  if (typeof window.requestIdleCallback !== 'function') return Promise.resolve(false);

  const generation = ++_libPrewarmGeneration;
  _libPrewarmPromise = new Promise(resolve => { _libPrewarmResolve = resolve; });
  const promise = _libPrewarmPromise;
  let attemptPending = false;
  let retryRequested = false;

  function clearScheduledAttempt() {
    if (_libPrewarmDelayTimer !== null) clearTimeout(_libPrewarmDelayTimer);
    if (_libPrewarmIdleHandle !== null && typeof window.cancelIdleCallback === 'function') {
      try { window.cancelIdleCallback(_libPrewarmIdleHandle); } catch (_) {}
    }
    _libPrewarmDelayTimer = null;
    _libPrewarmIdleHandle = null;
  }

  function scheduleIdleRetry(delay = 500) {
    if (generation !== _libPrewarmGeneration) return;
    retryRequested = true;
    if (attemptPending || _libPrewarmDelayTimer !== null || _libPrewarmIdleHandle !== null) return;
    if (document.visibilityState && document.visibilityState !== 'visible') return;
    _libPrewarmDelayTimer = setTimeout(requestIdle, Math.max(50, Number(delay) || 500));
  }

  function handlePriorityChange() {
    if (generation !== _libPrewarmGeneration) return;
    if (_canRunEmailPrewarm()) {
      scheduleIdleRetry(50);
      return;
    }

    const priorityBlocked = _isChatInteractionBusy()
      || (document.visibilityState && document.visibilityState !== 'visible');
    if (!priorityBlocked) return;

    retryRequested = true;
    clearScheduledAttempt();
    const controller = _libPrewarmAbortController;
    _libPrewarmAbortController = null;
    try { controller?.abort(); } catch (_) {}
    // A hidden page waits for visibilitychange. Chat priority also retains the
    // timer fallback for busy-until windows whose final transition has no event.
    if (!document.visibilityState || document.visibilityState === 'visible') {
      scheduleIdleRetry();
    }
  }

  window.addEventListener('odysseus:chat-busy-change', handlePriorityChange);
  document.addEventListener('visibilitychange', handlePriorityChange);
  _libPrewarmDetachPriorityListeners = () => {
    window.removeEventListener('odysseus:chat-busy-change', handlePriorityChange);
    document.removeEventListener('visibilitychange', handlePriorityChange);
  };

  function requestIdle() {
    if (generation !== _libPrewarmGeneration) return;
    _libPrewarmDelayTimer = null;
    try {
      _libPrewarmIdleHandle = window.requestIdleCallback((deadline) => {
        if (generation !== _libPrewarmGeneration) return;
        _libPrewarmIdleHandle = null;
        const hasIdleBudget = Boolean(
          deadline
          && !deadline.didTimeout
          && typeof deadline.timeRemaining === 'function'
          && deadline.timeRemaining() > 0
        );
        if (!_canRunEmailPrewarm()) {
          if (_isEmailPrewarmTemporarilyBlocked()) {
            scheduleIdleRetry();
          } else {
            _settleEmailPrewarm(generation, false);
          }
          return;
        }
        if (!hasIdleBudget) {
          scheduleIdleRetry();
          return;
        }
        if (generation !== _libPrewarmGeneration) {
          _settleEmailPrewarm(generation, false);
          return;
        }
        const controller = new AbortController();
        _libPrewarmAbortController = controller;
        attemptPending = true;
        retryRequested = false;
        Promise.resolve()
          .then(() => task({ signal: controller.signal, generation }))
          .then(value => {
            if (controller !== _libPrewarmAbortController || controller.signal.aborted) return;
            _settleEmailPrewarm(generation, Boolean(value));
          })
          .catch(() => {
            if (controller !== _libPrewarmAbortController || controller.signal.aborted) return;
            _settleEmailPrewarm(generation, false);
          })
          .finally(() => {
            attemptPending = false;
            if (generation !== _libPrewarmGeneration) return;
            if (retryRequested) scheduleIdleRetry();
          });
      });
    } catch (_) {
      _settleEmailPrewarm(generation, false);
    }
  }

  const wait = Math.max(0, Number(delay) || 0);
  if (wait > 0) _libPrewarmDelayTimer = setTimeout(requestIdle, wait);
  else requestIdle();
  return promise;
}

export function prewarmEmailLibrary({ delay = 2500 } = {}) {
  if (_libPrewarmPromise) return _libPrewarmPromise;
  const elapsed = Date.now() - _libLastPrewarmAt;
  if (elapsed >= 0 && elapsed < _LIB_PREWARM_COOLDOWN_MS) return Promise.resolve(false);
  return _scheduleEmailPrewarm(_prewarmEmailViews, { delay });
}

function _chooseEmailPrewarmAccountId(accounts) {
  const enabled = Array.isArray(accounts) ? accounts.filter(a => a && a.enabled !== false) : [];
  const remembered = _rememberedEmailAccountId();
  const current = String(state._libAccountId || '').trim();
  const chosen = enabled.find(a => String(a.id || '') === remembered)
    || enabled.find(a => String(a.id || '') === current)
    || enabled.find(a => a.is_default)
    || enabled[0]
    || null;
  return String(chosen?.id || '').trim();
}

async function _ensureEmailAccountsForPrewarm({ signal, generation } = {}) {
  if (!_isEmailPrewarmCurrent(generation, signal)) return null;
  const accountsFresh = _libAccountsLoadedAt && (Date.now() - _libAccountsLoadedAt) < _LIB_ACCOUNTS_TTL_MS;
  if (!(Array.isArray(state._libAccounts) && state._libAccounts.length && accountsFresh)) {
    try {
      const accountsRes = await fetch(`${API_BASE}/api/email/accounts`, {
        credentials: 'same-origin',
        signal,
      });
      if (!_isEmailPrewarmCurrent(generation, signal)) return null;
      if (accountsRes.ok) {
        const accountsData = await accountsRes.json().catch(() => ({}));
        if (!_isEmailPrewarmCurrent(generation, signal)) return null;
        if (Array.isArray(accountsData.accounts)) {
          state._libAccounts = accountsData.accounts;
          _libAccountsLoadedAt = Date.now();
        }
      }
    } catch (err) {
      if (err?.name === 'AbortError') return null;
    }
  }

  const accountId = _chooseEmailPrewarmAccountId(state._libAccounts);
  if (!_isEmailPrewarmCurrent(generation, signal)) return null;
  if (!accountId) return null;
  return accountId;
}

export function prewarmUnreadEmails({ limit = 8, maxUid = 0 } = {}) {
  return _scheduleEmailPrewarm(
    context => _prewarmUnreadEmailsNow({ limit, maxUid }, context),
    { delay: 0 }
  );
}

async function _prewarmUnreadEmailsNow({ limit = 8, maxUid = 0 } = {}, { signal, generation } = {}) {
  if (!_isEmailPrewarmCurrent(generation, signal)) return false;
  const accountId = await _ensureEmailAccountsForPrewarm({ signal, generation });
  if (accountId === null || !_isEmailPrewarmCurrent(generation, signal)) return false;
  const n = Math.max(1, Math.min(20, Number(limit) || 8));
  const key = `${accountId}|${maxUid || 0}|${n}`;
  if (_libUnreadPrewarmKey === key && (Date.now() - _libUnreadPrewarmAt) < 60 * 1000) return true;
  try {
    const folder = 'INBOX';
    const res = await fetch(emailApiUrl('/api/email/list', {
      folder,
      limit: n,
      offset: 0,
      filter: 'unread',
      account_id: accountId || undefined,
    }), {
      credentials: 'same-origin',
      signal,
    });
    if (!_isEmailPrewarmCurrent(generation, signal) || !res.ok) return false;
    const data = await res.json().catch(() => null);
    if (!_isEmailPrewarmCurrent(generation, signal)) return false;
    if (!data || data.error || !Array.isArray(data.emails) || !data.emails.length) return false;
    const sync = data.sync || {};
    _libCachePut(_libCacheKeyFor(accountId, folder, 'unread', false), {
      emails: data.emails,
      total: data.total || data.emails.length,
      sync,
    });
    _libUnreadPrewarmKey = key;
    _libUnreadPrewarmAt = Date.now();
    return true;
  } catch (_) {
    return false;
  }
}

async function _prewarmEmailViews({ signal, generation } = {}) {
  if (!_isEmailPrewarmCurrent(generation, signal)) return false;
  _setEmailSyncStatus({ warming: true });
  const folder = 'INBOX';
  const filter = 'all';
  try {
    const accountId = await _ensureEmailAccountsForPrewarm({ signal, generation });
    if (accountId === null || !_isEmailPrewarmCurrent(generation, signal)) return false;
    const ck = _libCacheKeyFor(accountId, folder, filter, false);
    if (_libCacheGet(ck)) {
      _libLastPrewarmAt = Date.now();
      return true;
    }

    // One optional first-page request only. Folder metadata, unread state, and
    // other accounts remain demand-driven so startup cannot fan out into IMAP.
    const res = await fetch(emailApiUrl('/api/email/list', {
      folder,
      limit: _LIB_INITIAL_PAGE_SIZE,
      offset: 0,
      filter,
      account_id: accountId || undefined,
    }), {
      credentials: 'same-origin',
      signal,
    });
    if (!_isEmailPrewarmCurrent(generation, signal) || !res.ok) return false;
    const data = await res.json().catch(() => null);
    if (!_isEmailPrewarmCurrent(generation, signal)) return false;
    if (!data || data.error || !Array.isArray(data.emails)) return false;
    const sync = data.sync || {};
    _libCachePut(ck, {
      emails: data.emails,
      total: data.total || 0,
      sync,
    });
    _libLastPrewarmAt = Date.now();
    _setEmailSyncStatus({
      updatedAt: sync.updated_at || new Date().toISOString(),
      source: sync.source || '',
      warming: true,
    });
    return true;
  } catch (_) {
    return false;
  } finally {
    _setEmailSyncStatus({ warming: false });
  }
}
export function _libCacheWriteBack() {
  // After a local mutation that already updated state._libEmails
  // (delete / archive / bulk), sync the change into the cache so the
  // next reopen doesn't briefly show the pre-mutation state before the
  // refetch wins. Skipped during search (results aren't the real list)
  // and on the scheduled virtual folder.
  if (state._libSearch) return;
  if (state._libFolder === '__scheduled__') return;
  const ck = _libCacheKey();
  if (_libListCache.has(ck)) {
    _libCachePut(ck, {
      emails: state._libEmails.slice(),
      total: state._libTotal,
      sync: {
        updated_at: _libSyncStatus.updatedAt || new Date().toISOString(),
        source: _libSyncStatus.source || 'local',
      },
    });
  }
}

// Expose the active account id to other modules (document.js uses this when sending).
// Simple global rather than cross-module import to keep coupling minimal.
export function _publishActiveAccount() {
  try { window.__odysseusActiveEmailAccount = state._libAccountId || null; } catch (_) {}
  try {
    if (state._libAccountId) localStorage.setItem(_LIB_LAST_ACCOUNT_KEY, state._libAccountId);
  } catch (_) {}
  // Publish the active account's own address so reply-all can exclude us from
  // the recipient list. This global was read in emailInbox.js but never set.
  try {
    const accts = state._libAccounts || [];
    const active = accts.find(a => a && a.id === state._libAccountId)
      || accts.find(a => a && a.is_default)
      || accts[0];
    window._myEmailAddress = (active && (active.from_address || active.imap_user)) || '';
    // Also publish every configured address so reply-all can exclude all of
    // the user's own mailboxes, not just the active one (multi-account users
    // were getting their other addresses added to Cc).
    const all = [];
    for (const a of accts) {
      if (a && a.from_address) all.push(a.from_address);
      if (a && a.imap_user) all.push(a.imap_user);
    }
    window._myEmailAddresses = all;
  } catch (_) {}
}

export function initEmailLibrary(config) {
  state._docModule = config.documentModule;
  const onEmailClick = config.onEmailClick;
  state._onEmailClick = typeof onEmailClick === 'function' ? (options = {}) => {
    const accountId = String(state._libAccountId || '');
    const libraryFolder = String(state._libFolder || 'INBOX');
    const messageFolder = String(options.email?.folder || libraryFolder);
    const mailboxGeneration = _emailMailboxGeneration;
    const mailboxContext = Object.freeze({
      accountId,
      libraryFolder,
      messageFolder,
      mailboxGeneration,
      isCurrent: () => (
        String(state._libAccountId || '') === accountId &&
        String(state._libFolder || 'INBOX') === libraryFolder &&
        _emailMailboxGeneration === mailboxGeneration
      ),
    });
    return onEmailClick({ ...options, mailboxContext });
  } : null;
}

export function isOpen() { return state._libOpen; }

export async function openEmailFromTool(target, isCurrent = () => true) {
  const response = await fetch(`${API_BASE}/api/email/accounts`, { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Could not load email accounts');
  const data = await response.json();
  const key = String(target.account || '').trim().toLowerCase();
  const matches = (data.accounts || []).filter(account =>
    [account.id, account.from_address, account.imap_user, account.name]
      .some(value => value && String(value).trim().toLowerCase() === key));
  if (matches.length !== 1) throw new Error('Could not uniquely identify the email account');
  if (!/^\d+$/.test(String(target.uid || ''))) throw new Error('Invalid email message ID');
  if (!isCurrent()) return;
  openEmailLibrary({ uid: String(target.uid), folder: target.folder || 'INBOX', account_id: matches[0].id });
}

export function openEmailLibrary(opts = {}) {
  // Foreground email always wins: cancel a delayed/idle callback and abort the
  // one optional request if it has already started. Generation checks make a
  // non-abortable response harmless if it races this transition.
  _cancelEmailPrewarm();
  // Preserve a valid inbox snapshot while rebuilding the modal. Reader
  // failures and mobile draft transitions can reopen this window before the
  // next list request has completed; clearing the snapshot here made that
  // normal recovery path look like an empty inbox.
  const previousLibraryWasOpen = state._libOpen;
  const previousAccountId = state._libAccountId || '';
  const previousFolder = state._libFolder || 'INBOX';
  const previousFilter = state._libFilter || 'all';
  const previousSearch = state._libSearch || '';
  const previousEmails = Array.isArray(state._libEmails) ? state._libEmails.slice() : [];

  // Force-clean any stale DOM state from previous attempts
  const existing = document.getElementById('email-lib-modal');
  if (existing) existing.remove();
  if (state._libEscHandler) {
    document.removeEventListener('keydown', state._libEscHandler, true);
    state._libEscHandler = null;
  }
  if (state._libInnerEscHandler) {
    window.removeEventListener('keydown', state._libInnerEscHandler, true);
    state._libInnerEscHandler = null;
  }
  _emailMailboxGeneration += 1;
  state._libOpen = true;
  // On mobile the sidebar overlays content — close it so the email view isn't
  // opened behind it (same pattern as session-switch/delete).
  if (window.innerWidth <= 768) {
    const _sb = document.getElementById('sidebar');
    if (_sb) _sb.classList.add('hidden');
    const _bd = document.getElementById('sidebar-backdrop');
    if (_bd) _bd.classList.remove('visible');
    // Email was opened last → bring the email windows IN FRONT of any open doc
    // (they alternate: whichever was opened last wins). The doc stays open
    // behind it; reopening the doc flips it back on top.
    document.body.classList.add('email-front');
  }
  state._libOffset = 0;
  state._libSearch = '';
  state._libSearchDraft = '';
  // Reset select-mode on each open so the toolbar Select button
  // never opens already-toggled-on after a previous session.
  state._selectMode = false;
  if (state._selectedUids) state._selectedUids.clear();
  state._libSearchPills = [];
  _libSuggestionCache = null;
  state._libFilter = 'all';
  state._libHasAttachments = false;
  // Animate the very first card render with a domino cascade (same as the
  // sidebar section-domino-in keyframe). Reset by _renderGrid after the
  // animation is queued so subsequent filter/sort re-renders are instant.
  state._libJustOpened = true;
  if (Object.prototype.hasOwnProperty.call(opts, 'account_id')) {
    state._libAccountId = opts.account_id || null;
    _publishActiveAccount();
  } else if (!state._libAccountId) {
    const rememberedAccount = _rememberedEmailAccountId();
    if (rememberedAccount) {
      state._libAccountId = rememberedAccount;
      _publishActiveAccount();
    }
  }
  state._libViewInlineImages = _readEmailInlineImagesPreference();
  if (opts.folder) state._libFolder = opts.folder;
  state._libPendingExpandUid = opts.uid || null;

  const sameMailbox = previousLibraryWasOpen
    && previousAccountId === (state._libAccountId || '')
    && previousFolder === (state._libFolder || 'INBOX')
    && previousFilter === 'all'
    && !previousSearch
    && previousEmails.length > 0;
  state._libEmails = sameMailbox ? previousEmails : [];
  state._libTotal = sameMailbox ? Math.max(state._libTotal || 0, previousEmails.length) : 0;

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'email-lib-modal';
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content" style="width:min(720px, 92vw);background:var(--bg);">
      <div class="modal-header">
        <h4>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;">
            <rect x="2" y="4" width="20" height="16" rx="2"/>
            <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
          </svg>
          <span id="email-lib-title-text">Email</span>
          <span id="email-lib-auto-reply-status-dot" class="email-lib-auto-reply-status-dot" aria-label="Auto Reply active" title="Auto Reply active" style="display:none"></span>
          <span id="email-lib-auto-reply-badge" class="email-lib-auto-reply-badge" role="button" tabindex="0" title="Auto Reply is active" style="display:none">Auto Reply</span>
          <span id="email-lib-unread-badge" class="email-lib-unread-badge" role="button" tabindex="0" title="Show unread emails" style="display:none"></span>
          <span id="email-lib-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal;margin-left:8px;position:relative;top:-2px"></span>
        </h4>
        <div class="email-lib-header-actions" style="display:flex;align-items:center;gap:8px;">
          <button class="email-settings-header-back" id="email-settings-header-back" type="button" title="Back" aria-label="Back" style="display:none;">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>
            <span>Back</span>
          </button>
          <button class="close-btn" id="email-lib-close">\u2716</button>
        </div>
      </div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:10px;overflow:hidden;">
        <div class="admin-card" style="flex:1;flex-direction:column;display:flex;overflow:hidden;">
          <div class="email-accounts-row">
            <div id="email-lib-accounts" style="display:flex;gap:4px;flex:1;min-width:0;"></div>
            <button class="memory-toolbar-btn email-compose-jiggle" id="email-lib-compose-btn">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:3px;"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
              New
            </button>
          </div>
          <div class="memory-toolbar">
            <div class="memory-category-filters">
              <div class="email-folder-picker" id="email-folder-picker" style="flex:1;min-width:0;position:relative;">
                <select class="memory-sort-select email-folder-select-control" id="email-lib-folder" style="display:none;">
                  <option value="INBOX">Inbox</option>
                </select>
                <button type="button" class="email-filter-btn email-folder-btn" id="email-folder-btn" aria-haspopup="listbox" aria-expanded="false">
                  <span class="email-filter-current"><span class="email-filter-icon email-folder-current-icon"></span><span class="email-filter-label email-folder-label">Inbox</span></span>
                  <svg class="email-filter-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                <div class="email-filter-menu email-folder-menu" id="email-folder-menu" role="listbox" hidden></div>
              </div>
              <!-- Hidden native select kept as the source of truth — all
                   existing change handlers still fire via the custom picker
                   dispatching 'change' on it. -->
              <select class="memory-sort-select" id="email-lib-filter" style="display:none;">
                <option value="all">All</option>
                <option value="unread">Unread</option>
                <option value="favorites">Favorites</option>
                <option value="undone">Undone</option>
                <option value="reminders">Reminders</option>
                <option value="unanswered">Unanswered</option>
                <option value="pending_30d">Pending · 30d</option>
                <option value="stale_30d">Stale · &gt;30d</option>
                <optgroup label="Tags">
                  <option value="tag:urgent">Urgent</option>
                  <option value="tag:reply-soon">Reply soon</option>
                  <option value="tag:bills">Bills</option>
                  <option value="tag:receipt">Receipt</option>
                  <option value="tag:travel">Travel</option>
                  <option value="tag:spam">Spam</option>
                </optgroup>
              </select>
              <div class="email-filter-picker" id="email-filter-picker" style="flex:1;min-width:0;position:relative;">
                <button type="button" class="email-filter-btn" id="email-filter-btn" aria-haspopup="listbox" aria-expanded="false">
                  <span class="email-filter-current"><span class="email-filter-icon"></span><span class="email-filter-label">All</span></span>
                  <svg class="email-filter-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                <div class="email-filter-menu" id="email-filter-menu" role="listbox" hidden></div>
              </div>
              <button class="memory-toolbar-btn email-filter-select-btn" id="email-lib-select-btn"><svg class="memory-select-btn-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/></svg>Select</button>
              <button class="memory-toolbar-btn email-filter-refresh-btn" id="email-lib-refresh-btn" title="Refresh">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
              </button>
              <button class="memory-toolbar-btn email-filter-settings-btn" id="email-lib-settings-btn" title="Email settings" aria-label="Email settings" aria-expanded="false">
                ${_EMAIL_SETTINGS_ICON}
              </button>
              <button class="memory-toolbar-btn email-reminders-clear-btn hidden" id="email-reminders-clear-btn" title="Permanently delete Odysseus reminder emails">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>
                Clear
              </button>
            </div>
            <div class="email-search-row" style="display:flex;gap:6px;align-items:flex-start;">
            <div class="email-search-wrap" style="position:relative;flex:1;min-width:140px;">
              <div class="email-lib-chip-bar memory-search-input" id="email-lib-chip-bar" style="width:100%;padding-right:36px;padding-left:26px;display:flex;align-items:center;flex-wrap:nowrap;gap:4px;cursor:text;min-height:30px;position:relative;">
                <svg class="email-lib-chip-bar-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="position:absolute;left:8px;top:50%;transform:translateY(-50%);pointer-events:none;color:var(--accent, var(--red));"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>
                <span id="email-lib-pills" style="display:flex;align-items:center;gap:4px;min-width:0;max-width:calc(100% - 70px);overflow-x:auto;overflow-y:hidden;scrollbar-width:none;flex:0 1 auto;"></span>
                <input type="text" id="email-lib-search" placeholder="Search by name or text" autocomplete="off" style="flex:1;min-width:80px;border:0;outline:none;background:transparent;color:inherit;font:inherit;padding:0;position:relative;top:-1px;" />
                <button type="button" class="memory-toolbar-btn email-search-options-btn" id="email-search-options-btn" title="Search options" aria-label="Search options" aria-expanded="false">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
                </button>
              </div>
              <div id="email-lib-suggest" style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;z-index:60;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:6px;box-shadow:0 6px 18px rgba(0,0,0,0.25);max-height:240px;overflow-y:auto;"></div>
              <div class="email-search-options-menu" id="email-search-options-menu" hidden>
                <div class="email-search-options-title">Filter by...</div>
                <button type="button" class="dropdown-item-compact" data-email-search-option="undone"><span class="dropdown-icon email-search-option-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></span><span>Not done</span></button>
                <button type="button" class="dropdown-item-compact hidden" data-email-search-option="reminders"><span class="dropdown-icon email-search-option-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/></svg></span><span>Reminders</span></button>
                <button type="button" class="dropdown-item-compact" data-email-search-option="attachments"><span class="dropdown-icon email-search-option-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83 2.83l8.49-8.48"/></svg></span><span>Attachments</span></button>
                <button type="button" class="dropdown-item-compact" data-email-search-option="date"><span class="dropdown-icon email-search-option-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></span><span>Date range</span></button>
                <div class="email-search-date-panel" hidden>
                  <label><span>From</span><input type="date" id="email-search-date-from"></label>
                  <label><span>To</span><input type="date" id="email-search-date-to"></label>
                  <div class="email-search-date-actions"><button type="button" data-email-date-clear>Clear</button><button type="button" data-email-date-apply>Apply</button></div>
                </div>
              </div>
            </div>
            </div>
          </div>
          <div id="email-lib-bulk" class="memory-bulk-bar hidden" style="margin-bottom:5px;">
            <label class="memory-bulk-check-all" style="position:relative;top:0px;" title="Select all emails currently loaded in this view"><input type="checkbox" id="email-lib-select-all"> All in view</label>
            <span id="email-lib-selected-count" style="position:relative;top:1px;">0 Selected</span>
            <button class="memory-toolbar-btn" id="email-lib-bulk-actions" style="position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>Actions <span style="opacity:0.55;font-size:9px;">▼</span></button>
            <button class="memory-toolbar-btn" id="email-lib-bulk-delete" style="position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete</button>
            <button class="memory-toolbar-btn" id="email-lib-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div id="email-lib-grid" class="doclib-grid"></div>
          <div id="email-lib-sync-status" class="email-lib-sync-status" aria-live="polite"></div>
          <div id="email-lib-settings-page" class="email-settings-page" hidden></div>
          <button class="email-lib-fab" id="email-lib-fab" type="button" aria-label="New email">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><path d="M3 6.5l9 6 9-6"/></svg>
            <span class="email-lib-fab-label">New</span>
          </button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(modal);
  modal.style.display = 'block';
  _syncEmailAutoReplyTitle(!!state._libAutoReplyActive);
  _refreshUnreadBadge({ syncAutoReplyTitle: true }).catch(() => {});
  document.getElementById('email-lib-grid')?.scrollTo(0, 0);
  modal.querySelector('.email-settings-body')?.scrollTo(0, 0);
  _renderEmailSyncStatus();
  if (_libSyncTicker) clearInterval(_libSyncTicker);
  _libSyncTicker = setInterval(_renderEmailSyncStatus, 30000);
  // Make modal background non-blocking so user can interact with rest of the app
  modal.style.cssText += 'pointer-events:none;background:transparent;';

  // Register so the chip carries the right label/icon. restoreFn left
  // empty — just unminimizing the modal is enough; whatever email was
  // expanded inside stays expanded.
  try {
    Modals.register('email-lib-modal', {
      label: 'Email',
      icon: 'M2 4h20v16H2zM22 7l-9.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7',
      closeFn: () => {
        const m = document.getElementById('email-lib-modal');
        if (m) m.classList.add('hidden');
      },
      restoreFn: () => {
        // Reopened last → bring the email windows in front of any open doc.
        document.body.classList.add('email-front');
        // Mobile: tapping the library chip chips down any open email
        // reader so the library is the only visible window. Pairs with
        // the per-reader restoreFn that chips the library down when a
        // reader is brought up.
        if (window.innerWidth <= 768) {
          document.querySelectorAll('.modal[id^="email-reader-"]').forEach(other => {
            try {
              if (Modals.isRegistered(other.id) && !Modals.isMinimized(other.id)) {
                Modals.minimize(other.id);
              }
            } catch {}
          });
        }
      },
    });
  } catch (_) {}
  _wireUnreadTabClick();
  const unreadBadge = document.getElementById('email-lib-unread-badge');
  unreadBadge?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (unreadBadge.dataset.mode === 'away') {
      _openGlobalEmailSettings('auto-reply');
      return;
    }
    _toggleUnreadEmails();
  });
  unreadBadge?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    if (unreadBadge.dataset.mode === 'away') {
      _openGlobalEmailSettings('auto-reply');
      return;
    }
    _toggleUnreadEmails();
  });
  const autoReplyBadge = document.getElementById('email-lib-auto-reply-badge');
  const openAutoReplySettings = (e) => {
    e.stopPropagation();
    _openGlobalEmailSettings('auto-reply');
  };
  autoReplyBadge?.addEventListener('click', openAutoReplySettings);
  autoReplyBadge?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    openAutoReplySettings(e);
  });
  const content = modal.querySelector('.modal-content');
  if (content) {
    const isMobile = window.innerWidth <= 768;
    if (isMobile) {
      // Bottom-anchored sheet on mobile
      content.style.position = 'fixed';
      content.style.pointerEvents = 'auto';
      content.style.left = '0';
      content.style.right = '0';
      content.style.bottom = '0';
      content.style.top = 'auto';
      content.style.transform = 'none';
    } else {
      // Center on screen using fixed positioning + computed offsets
      content.style.position = 'fixed';
      content.style.pointerEvents = 'auto';
      // Wait a frame for size to stabilize, then center. Center against the
      // modal's max-height (85vh) — NOT the live offsetHeight, which is tiny
      // while the email list is still loading and put the window ~1/3 down
      // (then it grew off the bottom as the list filled in).
      requestAnimationFrame(() => {
        const w = content.offsetWidth;
        const refH = window.innerHeight * 0.85;
        content.style.left = Math.max(20, (window.innerWidth - w) / 2) + 'px';
        content.style.top = Math.max(20, (window.innerHeight - refH) / 2) + 'px';
        content.style.transform = 'none';
      });
    }
  }

  // Wire events
  document.getElementById('email-lib-close').addEventListener('click', closeEmailLibrary);
  document.getElementById('email-settings-header-back')?.addEventListener('click', _hideEmailSettingsPage);

  // Clicking the modal header (anywhere except buttons/inputs) collapses
  // any currently-expanded email card and returns to the inbox list view.
  // Acts as a "back to email menu" gesture.
  const libHeader = modal.querySelector('.modal-header');
  if (libHeader) {
    libHeader.style.cursor = 'pointer';
    libHeader.addEventListener('click', (ev) => {
      if (ev.target.closest('button, input, select, a')) return;
      const g = document.getElementById('email-lib-grid');
      if (!g) return;
      g.querySelectorAll('.doclib-card.doclib-card-expanded').forEach(c => {
        const uid = c.dataset.uid;
        const liveEm = state._libEmails.find(e => String(e.uid) === String(uid));
        if (liveEm) _toggleCardPreview(c, liveEm);
      });
    });
  }

  // Drag-to-top edge → snap to fullscreen (Aero Snap). Dragging away from
  // the top edge while fullscreen unsnaps back to a centered window.
  _makeDraggable(content, modal, 'email-lib-fullscreen');

  document.getElementById('email-lib-folder').addEventListener('change', (e) => {
    state._libFolder = _resolveEmailFolderAlias(e.target.value);
    if (e.target.value !== state._libFolder) e.target.value = state._libFolder;
    _renderFolderPicker();
    _renderSearchPills();
    _loadEmailsFresh();
  });
  document.getElementById('email-lib-filter').addEventListener('change', (e) => {
    state._libFilter = e.target.value;
    _syncUnreadWindowGlow();
    _syncReminderClearButton();
    _renderSearchPills();
    _loadEmailsFresh();
    _syncSearchOptionsMenu();
    // Mirror the picker label/icon.
    _renderFilterPickerCurrent();
  });
  _initFilterPicker();
  _initFolderPicker();
  const searchOptionsBtn = document.getElementById('email-search-options-btn');
  const searchOptionsMenu = document.getElementById('email-search-options-menu');
  searchOptionsBtn?.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const shouldOpen = !!searchOptionsMenu?.hidden;
    if (searchOptionsMenu) searchOptionsMenu.hidden = !shouldOpen;
    searchOptionsBtn.setAttribute('aria-expanded', String(shouldOpen));
    _syncSearchOptionsMenu();
  });
  searchOptionsMenu?.addEventListener('click', (ev) => {
    const item = ev.target.closest('[data-email-search-option]');
    if (!item) return;
    ev.preventDefault();
    ev.stopPropagation();
    const opt = item.dataset.emailSearchOption;
    if (opt === 'attachments') _toggleEmailAttachmentFilter();
    else if (opt === 'undone' || opt === 'reminders') _setEmailListFilter(opt);
    else if (opt === 'date') {
      const panel = searchOptionsMenu.querySelector('.email-search-date-panel');
      panel.hidden = !panel.hidden;
      const from = panel.querySelector('#email-search-date-from');
      const to = panel.querySelector('#email-search-date-to');
      if (from) from.value = state._libDateFrom || '';
      if (to) to.value = state._libDateTo || '';
    }
    searchOptionsMenu.hidden = false;
    searchOptionsBtn?.setAttribute('aria-expanded', 'true');
  });
  searchOptionsMenu?.querySelector('[data-email-date-apply]')?.addEventListener('click', (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    const nextFrom = searchOptionsMenu.querySelector('#email-search-date-from')?.value || '';
    const nextTo = searchOptionsMenu.querySelector('#email-search-date-to')?.value || '';
    if (nextFrom && nextTo && nextFrom > nextTo) {
      showToast('From date must be before To date');
      return;
    }
    state._libDateFrom = nextFrom;
    state._libDateTo = nextTo;
    _syncSearchOptionsMenu();
    _renderSearchPills();
    _loadEmailsFresh({ useCache: false });
    searchOptionsMenu.querySelector('.email-search-date-panel').hidden = true;
  });
  searchOptionsMenu?.querySelector('[data-email-date-clear]')?.addEventListener('click', (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    state._libDateFrom = '';
    state._libDateTo = '';
    _syncSearchOptionsMenu();
    _renderSearchPills();
    _loadEmailsFresh({ useCache: false });
    searchOptionsMenu.querySelector('.email-search-date-panel').hidden = true;
  });
  if (state._emailSearchOptionsDismiss) {
    document.removeEventListener('click', state._emailSearchOptionsDismiss);
  }
  state._emailSearchOptionsDismiss = (ev) => {
    if (!searchOptionsMenu || searchOptionsMenu.hidden) return;
    if (ev.target.closest('#email-search-options-menu, #email-search-options-btn')) return;
    searchOptionsMenu.hidden = true;
    searchOptionsBtn?.setAttribute('aria-expanded', 'false');
  };
  document.addEventListener('click', state._emailSearchOptionsDismiss);
  _syncSearchOptionsMenu();
  document.getElementById('email-reminders-clear-btn')?.addEventListener('click', async () => {
    const ok = await styledConfirm('Permanently delete all Odysseus reminder emails?', {
      confirmText: 'Delete',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await fetch(`${API_BASE}/api/email/odysseus/reminders?permanent=1${_acct()}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      const data = await res.json().catch(() => ({}));
      showToast(`Deleted ${data.deleted || 0} reminder email${(data.deleted || 0) === 1 ? '' : 's'}`);
      if ((data.deleted || 0) > 0) {
        const visibleUids = Array.from(document.querySelectorAll('#email-lib-grid .doclib-card[data-uid]'))
          .map(card => card.dataset.uid)
          .filter(Boolean);
        await _animateEmailCardRemoval(visibleUids);
      }
      state._libFilter = 'all';
      const filterEl = document.getElementById('email-lib-filter');
      if (filterEl) filterEl.value = 'all';
      _renderFilterPickerCurrent();
      _syncSearchOptionsMenu();
      _syncReminderClearButton();
      _renderSearchPills();
      _loadEmailsFresh();
    } catch (err) {
      console.error(err);
      showToast('Failed to clear reminder emails');
    }
  });
  // The old "sort" dropdown (Latest / Unread first / Favorites first) was merged
  // into the filter dropdown above — "Favorites" is now a filter (server-side
  // \Flagged search). _libSort stays at its 'recent' default so the grid keeps
  // the API's newest-first order.

  // Chip-bar search: pills represent contact + free-text filters; the live
  // input below drives the autocomplete dropdown. Old behavior — instant
  // local filter on every keystroke + server-side IMAP search after 350ms
  // — is replaced by deterministic local filtering against the snapshot.
  _initEmailSearchChipBar();

  document.getElementById('email-lib-refresh-btn').addEventListener('click', async () => {
    await _refreshEmailLibraryFromUi(document.getElementById('email-lib-refresh-btn'));
  });
  _initMobileEmailPullRefresh();
  document.getElementById('email-lib-settings-btn')?.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    _openGlobalEmailSettings('show-tags');
  });


  const _composeNew = () => {
    // Desktop: keep Email open when there is enough room for it plus the
    // compose/document pane. Mobile still tabs down so the doc owns the screen.
    if (_prepareEmailWindowForDocument(document.getElementById('email-lib-modal'))) {
      if (!Modals.minimize('email-lib-modal')) closeEmailLibrary();
    }
    if (state._onEmailClick) state._onEmailClick({ compose: true });
    if (document.body.classList.contains('email-doc-split-active')) {
      _scheduleEmailDocumentSplitMeasure(document.getElementById('email-lib-modal'));
    }
  };
  document.getElementById('email-lib-compose-btn').addEventListener('click', _composeNew);

  // Mobile FAB: same action as the (desktop) New button, plus collapse-to-icon
  // while the list scrolls and spring back out to "New" when scrolling stops.
  const _fab = document.getElementById('email-lib-fab');
  if (_fab) {
    _fab.addEventListener('click', _composeNew);
    const _grid = document.getElementById('email-lib-grid');
    if (_grid) {
      let _fabIdle = null;
      _grid.addEventListener('scroll', () => {
        _fab.classList.add('collapsed');
        clearTimeout(_fabIdle);
        _fabIdle = setTimeout(() => _fab.classList.remove('collapsed'), 280);
        _positionFab();   // Firefox's toolbar shows/hides on scroll
      }, { passive: true });
    }

    // Keep the FAB above the browser's bottom toolbar. env(safe-area-inset)
    // doesn't cover Firefox-for-Android's URL bar, and its 100dvh handling is
    // unreliable, so measure how far the panel extends below the *visible*
    // (visualViewport) area and lift the button by that much.
    function _positionFab() {
      if (!_fab.isConnected) {       // modal was rebuilt/closed — stop listening
        window.visualViewport?.removeEventListener('resize', _positionFab);
        window.visualViewport?.removeEventListener('scroll', _positionFab);
        window.removeEventListener('resize', _positionFab);
        return;
      }
      const card = _fab.parentElement;            // .admin-card (positioned)
      const vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      const overflowBelow = card ? Math.max(0, Math.round(card.getBoundingClientRect().bottom - vh)) : 0;
      _fab.style.bottom = `calc(18px + env(safe-area-inset-bottom, 0px) + ${overflowBelow}px)`;
    }
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', _positionFab);
      window.visualViewport.addEventListener('scroll', _positionFab);
    }
    window.addEventListener('resize', _positionFab);
    // Run after layout settles (modal opens with an animation).
    requestAnimationFrame(() => requestAnimationFrame(_positionFab));
    setTimeout(_positionFab, 300);

    // Reveal the FAB with a scale-from-center pop only AFTER the email list has
    // rendered (the window is "fully loaded") — position it first while it's
    // still invisible so it never flashes at the top and slides down.
    let _revealed = false;
    const _revealFab = () => {
      if (_revealed || !_fab.isConnected) return;
      _revealed = true;
      _positionFab();
      // The FAB is an absolute child of .modal-content, which slides up on open
      // (sheet-enter). Wait until that entrance finishes before popping the FAB
      // in, otherwise it rides the slide ("swipes down with the window").
      const content = _fab.closest('.modal-content');
      const pop = () => { _positionFab(); requestAnimationFrame(() => _fab.classList.add('fab-revealed')); };
      if (!content || content.classList.contains('sheet-ready')) {
        pop();
      } else {
        let done = false;
        const onEnd = () => {
          if (done) return; done = true;
          content.removeEventListener('animationend', onEnd);
          pop();
        };
        content.addEventListener('animationend', onEnd);
        setTimeout(onEnd, 450);  // fallback if animationend doesn't fire
      }
    };
    if (_grid) {
      if (_grid.children.length) {
        _revealFab();
      } else {
        const _gobs = new MutationObserver(() => {
          if (_grid.children.length) { _gobs.disconnect(); _revealFab(); }
        });
        _gobs.observe(_grid, { childList: true });
        // Safety net — never leave the FAB hidden if the list stays empty.
        setTimeout(() => { _gobs.disconnect(); _revealFab(); }, 1600);
      }
    } else {
      setTimeout(_revealFab, 400);
    }
  }

  // Select mode toggle — icon + label swap matches the brain memories
  // select button (dot+Select ↔ X+Cancel).
  const _SELECT_BTN_DOT_SVG = '<svg class="memory-select-btn-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/></svg>';
  const _SELECT_BTN_X_SVG = '<svg class="memory-select-btn-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="vertical-align:-2px;margin-right:3px;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const _setSelectBtnState = (on) => {
    const btn = document.getElementById('email-lib-select-btn');
    if (!btn) return;
    if (on) { btn.classList.add('active'); btn.innerHTML = _SELECT_BTN_X_SVG + 'Cancel'; }
    else { btn.classList.remove('active'); btn.innerHTML = _SELECT_BTN_DOT_SVG + 'Select'; }
  };
  document.getElementById('email-lib-select-btn').addEventListener('click', () => {
    state._selectMode = !state._selectMode;
    state._selectedUids.clear();
    _setSelectBtnState(state._selectMode);
    _updateBulkBar();
    _renderGrid();
  });
  document.getElementById('email-lib-select-all').addEventListener('change', (e) => {
    if (e.target.checked) {
      state._libEmails.forEach(em => state._selectedUids.add(em.uid));
    } else {
      state._selectedUids.clear();
    }
    _updateBulkBar();
    _renderGrid();
  });

  // Bulk cancel — wired with the same teardown a fresh Cancel-via-toggle does.
  // Lets the global Esc handler (keyboard-shortcuts.js) close select mode by
  // clicking the visible [id$="-bulk-cancel"] button.
  document.getElementById('email-lib-bulk-cancel')?.addEventListener('click', () => {
    state._selectMode = false;
    state._selectedUids.clear();
    _setSelectBtnState(false);
    _updateBulkBar();
    _renderGrid();
  });

  // Bulk actions
  document.getElementById('email-lib-bulk-actions').addEventListener('click', (e) => {
    e.stopPropagation();
    if (state._selectedUids.size === 0) {
      showToast('Select emails first');
      return;
    }
    _showBulkActionsMenu(e.currentTarget);
  });
  document.getElementById('email-lib-bulk-delete')?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (state._selectedUids.size === 0) {
      showToast('Select emails first');
      return;
    }
    _bulkAction('delete');
  });

  const selectExpandedEmailText = () => {
    const expanded = document.querySelector('#email-lib-modal .doclib-card.doclib-card-expanded');
    const reader = expanded?.querySelector('.email-card-reader') || expanded;
    return _selectEmailReaderContents(reader);
  };

  // ESC to close + Arrow nav + Delete on the selected / currently-expanded email.
  state._libEscHandler = (e) => {
    const modal = document.getElementById('email-lib-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if ((e.ctrlKey || e.metaKey) && String(e.key || '').toLowerCase() === 'a') {
      const t = e.target;
      if (_isEmailTypingTarget(t)) return;
      if (selectExpandedEmailText()) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
      }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
	      if (modal.classList.contains('email-settings-mode')) {
	        _hideEmailSettingsPage();
	        return;
	      }
	      if (state._selectMode) {
        state._selectMode = false;
        state._selectedUids.clear();
        _setSelectBtnState(false);
        _updateBulkBar();
        _renderGrid();
        return;
      }
	      const expanded = modal.querySelector('.doclib-card.doclib-card-expanded');
	      if (expanded) {
	        _exitEmailReaderModeForList();
	        expanded.focus?.({ preventScroll: true });
	        return;
	      }
	      if (modal.classList.contains('email-reading')) {
	        _exitEmailReaderModeForList();
	        return;
	      }
      closeEmailLibrary();
      return;
    }
    // Don't hijack arrows / delete while the user is typing somewhere.
    const t = e.target;
    if (_isEmailTypingTarget(t)) return;
    const isDeleteKey = e.key === 'Delete' || e.key === 'Backspace';
    if (isDeleteKey && state._selectMode && state._selectedUids.size > 0) {
      e.preventDefault();
      _bulkAction('delete');
      return;
    }
    const expanded = document.querySelector('#email-lib-modal .doclib-card.doclib-card-expanded');
    if (!expanded) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const dir = e.key === 'ArrowLeft' ? '-1' : '1';
      const btn = expanded.querySelector(`.email-card-nav-btn[data-nav-dir="${dir}"]`);
      if (btn) { e.preventDefault(); btn.click(); }
    } else if (isDeleteKey) {
      const em = state._libEmails.find(x => String(x.uid) === String(expanded.dataset.uid));
      if (em) {
        e.preventDefault();
        _deleteEmailAndAdvance(em, expanded);
      }
    }
  };
  document.addEventListener('keydown', state._libEscHandler, true);

  // The global UI Escape arbiter is a document-level capture listener and can
  // close the hovered modal before this library handler sees the event. Give
  // email's inner layers a window-level first pass so Escape closes exactly
  // one inner state before the library itself is dismissed.
  state._libInnerEscHandler = (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('email-lib-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (dismissTopMenu()) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      return;
    }
    if (modal.classList.contains('email-settings-mode')) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      _hideEmailSettingsPage();
      return;
    }
    if (state._selectMode) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      state._selectMode = false;
      state._selectedUids.clear();
      _setSelectBtnState(false);
      _updateBulkBar();
      _renderGrid();
      return;
    }
    const expanded = modal.querySelector('.doclib-card.doclib-card-expanded');
    if (expanded || modal.classList.contains('email-reading')) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      _exitEmailReaderModeForList();
    }
  };
  window.addEventListener('keydown', state._libInnerEscHandler, true);

  const grid = document.getElementById('email-lib-grid');
  if (grid && !grid.children.length) _renderEmailLoading(grid);
  if (Array.isArray(state._libAccounts) && state._libAccounts.length) {
    _renderAccountsStrip();
  } else {
    _renderAccountsLoading();
  }
  const fastAccountAtOpen = state._libAccountId || '';
  if (fastAccountAtOpen) {
    _loadEmails({ useCache: true });
  }
  // If we already know the previous/default account, paint that inbox first
  // from the durable index and validate accounts in parallel. Cold refreshes
  // otherwise waited on `/accounts` before even trying the cheap indexed list.
  (async () => {
    await _loadAccounts();
    _refreshUnreadBadge().catch(() => {});
    _loadFolders();
    _loadEmailReminderBellVisibility();
    if (!fastAccountAtOpen || fastAccountAtOpen !== (state._libAccountId || '')) {
      _loadEmails({ useCache: true });
    }
  })();
}

export async function _loadAccounts({ force = false } = {}) {
  const hasCachedAccounts = Array.isArray(state._libAccounts) && state._libAccounts.length;
  const accountsFresh = _libAccountsLoadedAt && (Date.now() - _libAccountsLoadedAt) < _LIB_ACCOUNTS_TTL_MS;
  if (!force && hasCachedAccounts && accountsFresh) {
    if (!state._libAccountId) {
      const def = state._libAccounts.find(a => a.is_default) || state._libAccounts[0];
      state._libAccountId = def?.id || null;
      _publishActiveAccount();
    }
    state._libViewInlineImages = _readEmailInlineImagesPreference();
    _renderAccountsStrip();
    return;
  }
  try {
    const r = await fetch(`${API_BASE}/api/email/accounts`, { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    state._libAccounts = d.accounts || [];
    _libAccountsLoadedAt = Date.now();
  } catch (_) {
    if (!hasCachedAccounts) state._libAccounts = [];
  }
  // The 'Default' chip is gone — pick an explicit account so the email
  // list and any per-email actions (open in new tab, mark read, etc.)
  // always carry an account_id and can't desync from the server's
  // is_default state.
  if (state._libAccountId && state._libAccounts.length && !state._libAccounts.some(a => a && a.id === state._libAccountId)) {
    state._libAccountId = null;
  }
  if (!state._libAccountId && state._libAccounts.length) {
    const def = state._libAccounts.find(a => a.is_default) || state._libAccounts[0];
    state._libAccountId = def.id;
    _publishActiveAccount();
  }
  state._libViewInlineImages = _readEmailInlineImagesPreference();
  _renderAccountsStrip();
  _refreshAccountUnreadHighlights().catch(() => {});
}

export function _renderAccountsStrip() {
  const strip = document.getElementById('email-lib-accounts');
  if (!strip) return;
  strip.style.display = 'flex';
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  // The 'Default' chip caused desync bugs (changing the server-side
  // default via the dot while still on the cached 'default' view would
  // open the wrong account's emails). Each account renders as its own
  // chip; the active one is selected explicitly via _loadAccounts.
  let html = '';
  // Keep the server default first; unread status remains the only dot on a
  // mailbox chip, while the explicit default control lives in Settings.
  const accounts = (state._libAccounts || []).slice().sort((a, b) => Number(!!b.is_default) - Number(!!a.is_default));
  const nameCounts = new Map();
  accounts.forEach(a => {
    const name = String(a.name || '').trim();
    if (name) nameCounts.set(name, (nameCounts.get(name) || 0) + 1);
  });
  for (const a of accounts) {
    const active = state._libAccountId === a.id ? ' active' : '';
    const accountAddress = a.from_address || a.imap_user || '';
    const accountName = String(a.name || '').trim();
    const accountLabel = accountName && nameCounts.get(accountName) > 1 && accountAddress
      ? `${accountName} · ${accountAddress}`
      : (accountName || accountAddress || 'account');
    const unread = _accountUnreadState.get(String(a.id || '')) || {};
    const unreadCount = Number(unread.unreadCount || 0);
    const unreadClass = unreadCount > 0 ? ' email-account-has-unread' : '';
    const unreadTitle = unreadCount > 0 ? ` · ${unreadCount > 999 ? '999+' : unreadCount} unread` : '';
    const away = state._libAutoReplyActive && state._libAccountId === a.id;
    const label = away ? (accountAddress || accountLabel) : accountLabel;
    const awayLabel = away ? '<span class="email-account-away-label">(AWAY)</span>' : '';
  const unreadDot = unreadCount > 0
      ? `<span class="email-account-unread-dot" aria-hidden="true"></span>`
      : '';
    html += `<span class="gallery-chip-wrap" style="position:relative;display:inline-flex;align-items:center;">`
         + `<button class="memory-toolbar-btn gallery-chip email-account-chip${active}${unreadClass}" data-acc-id="${esc(a.id)}" title="${esc(a.from_address || a.imap_user || '')}${away ? ' · auto reply active' : ''}${unreadTitle}" style="padding-right:24px;">${unreadDot}${awayLabel}<span class="email-account-chip-label">${esc(label)}</span>${unreadCount > 0 ? `<span class="email-account-unread-count">${unreadCount > 999 ? '999+' : unreadCount}</span>` : ''}</button>`
         + `</span>`;
  }
  strip.innerHTML = html;
  strip.querySelectorAll('button[data-acc-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const nextAccountId = btn.dataset.accId || null;
      if ((state._libAccountId || null) === nextAccountId) return;
      // Tag filters belong to the current mailbox account. Keeping one while
      // switching accounts can show an empty or misleading result set.
      if (String(state._libFilter || '').startsWith('tag:')) {
        state._libFilter = 'all';
        const filterEl = document.getElementById('email-lib-filter');
        if (filterEl) filterEl.value = 'all';
      }
      state._libSearchPills = (state._libSearchPills || []).filter((pill) => {
        const value = String(pill?.value || pill?.text || '');
        return pill?.type !== 'filter' || !value.includes('tag:');
      });
      state._libAccountId = nextAccountId;
      state._libViewInlineImages = _readEmailInlineImagesPreference(nextAccountId);
      _publishActiveAccount();
      _renderAccountsStrip();
      _renderFilterPickerCurrent();
      _syncSearchOptionsMenu();
      _renderSearchPills();
      _loadEmailsFresh({ force: true, useCache: true, refreshAfterCache: true, showRefreshSpinner: true });
      _loadFolders({ resetMissing: true }).catch(() => {});
      _refreshUnreadBadge({ syncAutoReplyTitle: true }).catch(() => {});
      _refreshAccountUnreadHighlights().catch(() => {});
    });
  });
  // Idempotent — wire wheel + grab-drag scroll once per strip element.
  if (!strip._scrollWired) {
    strip._scrollWired = true;
    // Vertical wheel → horizontal scroll. Only intercept when there's
    // actually horizontal overflow to scroll through, otherwise let the
    // page do its normal vertical scroll.
    strip.addEventListener('wheel', (e) => {
      if (strip.scrollWidth <= strip.clientWidth) return;
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      e.preventDefault();
      strip.scrollLeft += e.deltaY;
    }, { passive: false });
    // Click-and-drag scroll. Track mousedown, then mousemove deltas
    // bump scrollLeft. Cancel a chip click if the user actually dragged
    // more than a few pixels.
    let dragging = false;
    let startX = 0;
    let startScroll = 0;
    let moved = 0;
    strip.style.cursor = 'grab';
    strip.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      dragging = true;
      moved = 0;
      startX = e.pageX;
      startScroll = strip.scrollLeft;
      strip.style.cursor = 'grabbing';
      strip.style.userSelect = 'none';
    });
    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const dx = e.pageX - startX;
      moved = Math.max(moved, Math.abs(dx));
      strip.scrollLeft = startScroll - dx;
    });
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      strip.style.cursor = 'grab';
      strip.style.userSelect = '';
    });
    // Swallow chip clicks fired after a real drag — the user meant to scroll,
    // not select.
    strip.addEventListener('click', (e) => {
      if (moved > 5) { e.stopPropagation(); e.preventDefault(); moved = 0; }
    }, true);
  }
  _publishActiveAccount();
}

async function _refreshAccountUnreadHighlights() {
  const accounts = Array.isArray(state._libAccounts) ? state._libAccounts.slice() : [];
  if (!accounts.length) return;
  const seq = ++_accountUnreadSeq;
  const next = new Map();
  await Promise.all(accounts.map(async (a) => {
    const id = String(a && a.id || '');
    if (!id) return;
    try {
      const res = await fetch(emailApiUrl('/api/email/unread-state', {
        folder: 'INBOX',
        account_id: id,
      }), { credentials: 'same-origin' });
      if (!res.ok) return;
      const data = await res.json().catch(() => ({}));
      next.set(id, {
        unreadCount: Number(data.unread_count || 0),
        maxUid: Number(data.max_uid || 0),
      });
    } catch (_) {}
  }));
  if (seq !== _accountUnreadSeq) return;
  _accountUnreadState = next;
  _renderAccountsStrip();
}

export async function mountEmailSettings(host) {
  if (!host) return;
  host.classList.add('email-settings-page');
  const head = host.querySelector('.email-settings-page-head');
  const body = host.querySelector('.email-settings-body');
  const defaultCard = document.getElementById('settings-email-default-card');
  const defaultHost = document.getElementById('settings-email-default-host');
  if (!head || !body) return;

  const render = async () => {
    body.innerHTML = _emailSettingsLoadingHtml();
    try {
      await _loadAccounts().catch(() => {});
      const multipleAccounts = (state._libAccounts || []).filter(a => a && a.enabled !== false).length > 1;
      if (defaultCard) defaultCard.hidden = !multipleAccounts;
      if (defaultHost) defaultHost.innerHTML = multipleAccounts ? _emailSettingsAccountSelectHtml() : '';
      head.innerHTML = '';
      const [cfg, writingStyle] = await Promise.all([
        _fetchEmailSettingsConfig(),
        _fetchEmailWritingStyle().catch(() => ''),
      ]);
      body.innerHTML = _emailWritingStyleHtml(writingStyle)
        + _emailDisplaySettingsHtml(cfg)
        + _emailCleanupSettingsHtml()
        + _emailSettingsFormHtml(cfg);
      state._libAutoReplyActive = _isAutoReplyActiveForCurrentAccount(cfg);
      state._libAutoReplyDraftActive = null;
      _syncEmailAutoReplyTitle(state._libAutoReplyActive);
      _syncAutoReplyCalendarEvent(cfg).catch((err) => console.warn('Failed to reconcile auto-reply calendar event:', err));
      const focus = host.dataset.emailSettingsFocus;
      if (focus) {
        const section = focus === 'show-tags'
          ? body.querySelector('.email-settings-display-section')
          : body.querySelector(`.email-settings-${focus}-section`);
        if (section) {
          section.scrollIntoView?.({ block: 'nearest' });
        }
        delete host.dataset.emailSettingsFocus;
      }
      _bindEmailSettingsPageControls(host, defaultHost);
      defaultHost?.querySelector('.email-settings-default')?.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const acctId = ev.currentTarget.dataset.emailDefaultId;
        const account = (state._libAccounts || []).find(a => a && a.id === acctId);
        if (!acctId || account?.is_default) return;
        try {
          const response = await fetch(`${API_BASE}/api/email/accounts/${encodeURIComponent(acctId)}/set-default`, {
            method: 'POST', credentials: 'same-origin',
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          for (const a of state._libAccounts) a.is_default = a.id === acctId;
          _syncEmailSettingsAccountPicker(defaultHost);
          _renderAccountsStrip();
        } catch (err) {
          console.error('Set default account failed:', err);
        }
      });
      defaultHost?.querySelector('.email-settings-default')?.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        ev.preventDefault();
        ev.currentTarget.click();
      });
      defaultHost?.querySelector('#email-settings-account-select')?.addEventListener('change', async (ev) => {
        state._libAccountId = ev.currentTarget.value || null;
        _publishActiveAccount();
        _renderAccountsStrip();
        await render();
      });
    } catch (_) {
      body.innerHTML = '<div class="email-settings-error">Could not load settings.</div>';
    }
  };
  await render();
}

export async function openEmailLibrarySettings() {
  openEmailLibrary();
  await _showEmailSettingsPage();
}

export function closeEmailLibrary() {
  _cancelEmailPrewarm();
  const modal = document.getElementById('email-lib-modal');
  if (modal) modal.remove();
  if (_libSyncTicker) {
    clearInterval(_libSyncTicker);
    _libSyncTicker = null;
  }
  _clearEmailDocumentSplit();
  if (state._libEscHandler) {
    document.removeEventListener('keydown', state._libEscHandler, true);
    state._libEscHandler = null;
  }
  if (state._libInnerEscHandler) {
    window.removeEventListener('keydown', state._libInnerEscHandler, true);
    state._libInnerEscHandler = null;
  }
  if (state._emailSearchOptionsDismiss) {
    document.removeEventListener('click', state._emailSearchOptionsDismiss);
    state._emailSearchOptionsDismiss = null;
  }
  state._libOpen = false;
  // If the /email route collapsed the wide sidebar to make room for
  // the fullscreen modal, re-expand it now that the modal is gone.
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
}

// Make a modal draggable by its header. If `modal` and `fsClass` are
// provided, dragging to the top edge of the viewport snaps to fullscreen
// (Aero Snap). Dragging away from the top while fullscreen unsnaps.
export function _makeDraggable(content, modal, fsClass) {
  if (!content) return;
  const header = content.querySelector('.modal-header');
  if (!header) return;
  // Per-modal fullscreen behavior — caller supplies fsClass, we apply
  // the same inline-style fullscreen pattern email-lib + email-window
  // both use. exitFullscreen restores the default windowed size
  // (min(720px, 92vw) × 85vh) and centers around the cursor.
  const enterFullscreen = () => {
    if (!fsClass || modal.classList.contains(fsClass)) return;
    modal.classList.add(fsClass);
    content.style.position = 'fixed';
    content.style.left = '0';
    content.style.top = '0';
    content.style.right = '0';
    content.style.bottom = '0';
    content.style.width = '100vw';
    content.style.maxWidth = '100vw';
    content.style.height = '100vh';
    content.style.maxHeight = '100vh';
    content.style.borderRadius = '0';
    content.style.transform = 'none';
  };
  const exitFullscreen = (cx, cy) => {
    if (!fsClass || !modal.classList.contains(fsClass)) return;
    modal.classList.remove(fsClass);
    content.style.width = 'min(720px, 92vw)';
    content.style.maxWidth = '';
    content.style.height = '';
    content.style.maxHeight = '85vh';
    content.style.borderRadius = '';
    content.style.right = '';
    content.style.bottom = '';
    const w = Math.min(720, window.innerWidth * 0.92);
    content.style.left = Math.max(8, cx - w / 2) + 'px';
    content.style.top = Math.max(8, cy - 20) + 'px';
  };
  makeWindowDraggable(modal, {
    content,
    header,
    fsClass,
    skipSelector: '.close-btn, .modal-close',
    enableLeftDock: true,  // park the email on the left while replying on the right
    onDragStart: ({ rect }) => {
      if (!modal.classList.contains('email-snap-left')) return;
      modal.classList.remove('email-snap-left');
      _clearEmailDocumentSplit();
      content.style.position = 'fixed';
      content.style.left = `${Math.round(rect.left)}px`;
      content.style.top = `${Math.round(rect.top)}px`;
      content.style.right = '';
      content.style.bottom = '';
      content.style.width = `${Math.max(420, Math.round(rect.width || 560))}px`;
      content.style.maxWidth = '';
      content.style.height = `${Math.max(320, Math.round(rect.height || 620))}px`;
      content.style.maxHeight = '85vh';
      content.style.borderRadius = '';
      content.style.transform = 'none';
      content.style.margin = '0';
    },
    onEnterFullscreen: fsClass ? enterFullscreen : null,
    onExitFullscreen: fsClass ? exitFullscreen : null,
  });
}

// When the user clicks Reply on a fullscreened email view, dock the email
// modal to the left as a narrow sidebar so the doc panel (which opens on
// the right side of the chat area) is visible side-by-side. Only triggers
// when the viewport is wide enough to make a true split worthwhile. Returns
// true if the snap was applied, false otherwise.
export function _snapEmailModalToLeftSidebar(modal) {
  if (!modal) return false;
  if (window.innerWidth < 900) return false;
  // "Open in new tab" reader modals (id="email-view-…") are explicitly
  // floating windows the user already positioned. Replying from one
  // shouldn't yank it to the left edge — leave it on top in its current
  // spot. Reply still opens the compose document; the user can drag the
  // reader away or close it themselves.
  if ((modal.id || '').startsWith('email-view-')) return false;
  const content = modal.querySelector('.modal-content');
  if (!content) return false;
  // Only dock if currently fullscreen — for a manually-sized window the
  // user already chose its layout; don't surprise them by snapping it.
  const wasLibFs = modal.classList.contains('email-lib-fullscreen');
  const wasWinFs = modal.classList.contains('email-window-fullscreen');
  if (!wasLibFs && !wasWinFs) return false;
  modal.classList.remove('email-lib-fullscreen');
  modal.classList.remove('email-window-fullscreen');
  modal.classList.add('email-snap-left');
  const W = Math.min(440, Math.max(360, Math.round(window.innerWidth * 0.30)));
  const left = _emailSplitLeftEdge();
  content.style.position = 'fixed';
  content.style.left = '0';
  content.style.top = '0';
  content.style.right = '';
  content.style.bottom = '0';
  content.style.width = W + 'px';
  content.style.maxWidth = W + 'px';
  content.style.height = '100vh';
  content.style.maxHeight = '100vh';
  content.style.borderRadius = '0';
  content.style.transform = 'none';
  content.style.margin = '0';
  _setEmailDocumentSplit(left, W);
  _scheduleEmailDocumentSplitMeasure(modal);
  return true;
}

export async function _loadFolders({ resetMissing = false, live = false } = {}) {
  const seq = ++_libFolderSeq;
  const accountAtStart = state._libAccountId || '';
  try {
    const res = await fetch(emailApiUrl('/api/email/folders', {
      account_id: accountAtStart || undefined,
      cached_only: live ? undefined : 1,
    }));
    let data = await res.json();
    if (seq !== _libFolderSeq || accountAtStart !== (state._libAccountId || '')) return;
    const sel = document.getElementById('email-lib-folder');
    if (!sel || !data.folders) return;
    state._libFolders = data.folders;
    const resolvedFolder = _resolveEmailFolderAlias(state._libFolder);
    if (resolvedFolder !== state._libFolder && data.folders.includes(resolvedFolder)) {
      state._libFolder = resolvedFolder;
    }
    if (resetMissing && state._libFolder !== '__scheduled__' && !data.folders.includes(state._libFolder)) {
      state._libFolder = data.folders.includes('INBOX') ? 'INBOX' : (data.folders[0] || 'INBOX');
      state._libFilter = 'all';
      state._libSearch = '';
      state._libHasAttachments = false;
      _libListCache.clear();
      const searchEl = document.getElementById('email-lib-search');
      const filterEl = document.getElementById('email-lib-filter');
      const attachEl = document.getElementById('email-attachments-btn');
      if (searchEl) searchEl.value = '';
      if (filterEl) filterEl.value = 'all';
      if (attachEl) attachEl.classList.remove('active');
      _syncUnreadWindowGlow();
      _syncReminderClearButton();
    }
    sel.innerHTML = '';
    const folderHeader = document.createElement('option');
    folderHeader.disabled = true;
    folderHeader.textContent = '─────────';
    sel.appendChild(folderHeader);
    const { priority, others } = sortedFolders(data.folders);
    for (const f of priority) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = folderDisplayName(f);
      if (f === state._libFolder) opt.selected = true;
      sel.appendChild(opt);
    }
    for (const f of others) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = folderDisplayName(f);
      if (f === state._libFolder) opt.selected = true;
      sel.appendChild(opt);
    }
    if (!data.folders.some(f => /trash|bin|deleted/i.test(String(f)))) {
      const trashOpt = document.createElement('option');
      trashOpt.value = 'Trash';
      trashOpt.textContent = 'Trash';
      if (String(state._libFolder).toLowerCase() === 'trash') trashOpt.selected = true;
      sel.appendChild(trashOpt);
    }
    // Some providers omit Drafts from the folder discovery response even
    // though IMAP APPEND can still write to the standard Drafts mailbox.
    // Keep it available beside the provider-discovered folders.
    const hasDraftsFolder = data.folders.some(f => String(f).toLowerCase().includes('draft'));
    if (!hasDraftsFolder) {
      const draftOpt = document.createElement('option');
      draftOpt.value = 'Drafts';
      draftOpt.textContent = 'Drafts';
      if (String(state._libFolder).toLowerCase() === 'drafts') draftOpt.selected = true;
      sel.appendChild(draftOpt);
    }
    // Scheduled (special virtual folder)
    const schedOpt = document.createElement('option');
    schedOpt.value = '__scheduled__';
    schedOpt.textContent = 'Scheduled';
    if (state._libFolder === '__scheduled__') schedOpt.selected = true;
    sel.appendChild(schedOpt);
	    sel.value = state._libFolder;
	    _renderFolderPicker();
	  } catch (e) {}
	}

function _crossFolderCandidates() {
  const available = Array.isArray(state._libFolders) ? state._libFolders.filter(Boolean) : [];
  const lower = new Map(available.map(f => [String(f).toLowerCase(), f]));
  const pick = (patterns, fallback) => {
    for (const p of patterns) {
      const direct = lower.get(String(p).toLowerCase());
      if (direct) return direct;
    }
    const match = available.find(f => patterns.some(p => String(f).toLowerCase().includes(String(p).toLowerCase())));
    return match || fallback;
  };
  const candidates = [
    pick(['INBOX'], 'INBOX'),
    pick(['[Gmail]/Sent Mail', 'Sent Mail', 'Sent Items', 'INBOX.Sent', 'Sent'], '[Gmail]/Sent Mail'),
    pick(['Archive', '[Gmail]/All Mail', 'All Mail'], '[Gmail]/All Mail'),
  ];
  return Array.from(new Set(candidates.filter(Boolean)));
}

function _findEmailFolder(patterns, fallback) {
  const available = Array.isArray(state._libFolders) ? state._libFolders.filter(Boolean) : [];
  const lower = new Map(available.map(f => [String(f).toLowerCase(), f]));
  for (const p of patterns) {
    const direct = lower.get(String(p).toLowerCase());
    if (direct) return direct;
  }
  return available.find(f => patterns.some(p => String(f).toLowerCase().includes(String(p).toLowerCase()))) || fallback;
}

function _resolveEmailFolderAlias(folder) {
  const raw = String(folder || '').trim();
  const key = raw.toLowerCase();
  if (!raw || key === 'inbox' || raw === '__scheduled__') return raw || 'INBOX';
  if (key === 'sent' || key === 'sent mail' || key === 'sent items') {
    return _findEmailFolder(['[Gmail]/Sent Mail', '[Google Mail]/Sent Mail', 'Sent Mail', 'Sent Items', 'INBOX.Sent', 'Sent'], raw);
  }
  if (key === 'archive' || key === 'archives' || key === 'all mail' || key === 'archive / all mail') {
    return _findEmailFolder(['[Gmail]/All Mail', '[Google Mail]/All Mail', 'All Mail', 'Archive', 'Archives'], raw);
  }
  if (key === 'starred' || key === 'favorites' || key === 'flagged') {
    return _findEmailFolder(['[Gmail]/Starred', '[Google Mail]/Starred', 'Starred', 'Flagged'], raw);
  }
  if (key === 'junk' || key === 'spam') {
    return _findEmailFolder(['[Gmail]/Spam', '[Google Mail]/Spam', 'Spam', 'Junk'], raw);
  }
  if (key === 'trash' || key === 'bin' || key === 'deleted') {
    return _findEmailFolder(['[Gmail]/Trash', '[Google Mail]/Trash', '[Gmail]/Bin', 'Trash', 'Bin', 'Deleted Messages', 'Deleted Items'], raw);
  }
  if (key === 'draft' || key === 'drafts') {
    return _findEmailFolder(['[Gmail]/Drafts', '[Google Mail]/Drafts', 'Drafts', 'Draft', 'INBOX.Drafts'], raw);
  }
  return raw;
}

function _sentFolderName() {
  return _findEmailFolder(['[Gmail]/Sent Mail', 'Sent Mail', 'Sent Items', 'INBOX.Sent', 'Sent'], 'Sent');
}

function _deriveSearchScope(rawQuery) {
  const original = String(rawQuery || '').trim();
  const tokens = original.split(/\s+/).filter(Boolean);
  let scope = 'all';
  const kept = [];
  let forced = '';
  for (const token of tokens) {
    const t = token.toLowerCase().replace(/^#+/, '').replace(/:$/, '');
    if (['sent', 'sentmail', 'sent-mail', 'outbox'].includes(t)) {
      forced = 'sent';
      continue;
    }
    if (['inbox'].includes(t)) {
      forced = 'inbox';
      continue;
    }
    kept.push(token);
  }
  if (forced) scope = forced;
  let folder = 'INBOX';
  let serverScope = 'all';
  if (scope === 'sent') {
    folder = _sentFolderName();
    serverScope = 'folder';
  } else if (scope === 'inbox') {
    folder = 'INBOX';
    serverScope = 'folder';
  } else if (scope === 'current') {
    folder = state._libFolder || 'INBOX';
    serverScope = 'folder';
  }
  return {
    scope,
    folder,
    serverScope,
    q: forced ? kept.join(' ').trim() : original,
    forced,
  };
}

// Snapshot of state._libEmails taken right before search starts so we
// can both filter locally and restore on clear without re-fetching.
let _libPreSearchEmails = null;
let _libPreSearchTotal = 0;
let _libServerSearchEmails = null;
let _libServerSearchTotal = 0;

// Cached contact suggestions for the chip-input autocomplete. Built on
// first focus / first keystroke from contacts + currently-loaded senders.
let _libSuggestionCache = null;
let _libSuggestionFocusIdx = 0;

async function _buildSuggestionSource() {
  // Combine the contacts list with senders/recipients visible in the
  // loaded email list. Dedup by lowercased email address; prefer
  // contact-supplied display names where present.
  const map = new Map();
  const _add = (name, email) => {
    const key = String(email || '').trim().toLowerCase();
    if (!key) return;
    const prev = map.get(key);
    if (!prev || (name && !prev.name)) {
      map.set(key, { name: (name || '').trim(), email: key });
    }
  };
  // 1) Senders / recipients already in the loaded grid.
  for (const em of (state._libEmails || [])) {
    _add(em.from_name, em.from_address);
    const _parse = (s) => String(s || '').split(',').forEach(seg => {
      const m = seg.match(/^\s*"?([^"<]*)"?\s*<?([^>]+)>?\s*$/);
      if (m) _add(m[1], m[2]);
    });
    _parse(em.to);
    _parse(em.cc);
  }
  // 2) Address book — best-effort.
  try {
    const r = await fetch(`${API_BASE}/api/contacts/list`, { credentials: 'same-origin' });
    if (r.ok) {
      const d = await r.json();
      for (const c of (d.contacts || [])) {
        const email = c.email || (c.emails && c.emails[0]) || '';
        _add(c.name || c.full_name, email);
      }
    }
  } catch (_) {}
  return Array.from(map.values()).filter(x => x.email);
}

function _scoreSuggestion(s, needle) {
  // Crude relevance: startsWith on name or email wins big; substring is fine.
  const n = (s.name || '').toLowerCase();
  const e = (s.email || '').toLowerCase();
  if (n.startsWith(needle) || e.startsWith(needle)) return 3;
  if (n.includes(needle) || e.includes(needle)) return 2;
  return 0;
}

function _formatEmailSuggestionDate(em) {
  let d = null;
  if (em && em.date) {
    const parsed = new Date(em.date);
    if (Number.isFinite(parsed.getTime())) d = parsed;
  }
  if (!d && em && em.date_epoch) {
    const parsed = new Date(Number(em.date_epoch) * 1000);
    if (Number.isFinite(parsed.getTime())) d = parsed;
  }
  if (!d) return '';
  const now = new Date();
  const opts = d.getFullYear() === now.getFullYear()
    ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' };
  return d.toLocaleDateString(undefined, opts);
}

// Filter / attachment suggestions surfaced inside the same chip-bar
// dropdown. Typing 'attachment', 'unread', 'urgent' etc. surfaces the
// corresponding filter row with its icon; picking it pins a filter
// pill that drives state._libFilter or the has-attachments toggle.
const _LIB_FILTER_OPTIONS = [
  { value: 'filter:has-attachments', label: 'Has attachments', keywords: ['attachment', 'attachments', 'has attachment', 'attach'] },
  { value: 'filter:unread',          label: 'Unread',          keywords: ['unread', 'new', 'unseen'] },
  { value: 'filter:favorites',       label: 'Favorites',       keywords: ['favorite', 'favorites', 'starred', 'star', 'flagged'] },
  { value: 'filter:undone',          label: 'Undone',          keywords: ['undone', 'pending', 'todo'] },
  { value: 'filter:reminders',       label: 'Reminders',       keywords: ['reminder', 'reminders'] },
  { value: 'filter:unanswered',      label: 'Unanswered',      keywords: ['unanswered', 'unreplied', 'no reply'] },
  { value: 'filter:pending_30d',     label: 'Pending · 30d',   keywords: ['pending 30d', 'pending', 'recent pending'] },
  { value: 'filter:stale_30d',       label: 'Stale · >30d',    keywords: ['stale', 'old', 'stale 30d'] },
  { value: 'filter:tag:urgent',      label: 'Urgent',          keywords: ['urgent', 'critical'] },
  { value: 'filter:tag:reply-soon',  label: 'Reply soon',      keywords: ['reply soon', 'reply', 'follow up'] },
  { value: 'filter:tag:bills',       label: 'Bills',           keywords: ['bill', 'bills', 'billing'] },
  { value: 'filter:tag:receipt',     label: 'Receipt',         keywords: ['receipt', 'receipts', 'purchase'] },
  { value: 'filter:tag:travel',      label: 'Travel',          keywords: ['travel', 'trip', 'booking'] },
  { value: 'filter:tag:spam',        label: 'Spam',            keywords: ['spam', 'junk'] },
];

function _libFilterIconFor(value) {
  // value is 'filter:<X>' — strip prefix and reuse the existing icon map.
  const v = String(value || '').replace(/^filter:/, '');
  if (v === 'has-attachments') return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
  if (v === 'date-range') return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
  return _EMAIL_FILTER_ICONS[v] || _EMAIL_FILTER_ICONS['all'];
}

function _scoreFilterOption(opt, needle) {
  for (const kw of opt.keywords) {
    if (kw === needle) return 4;
    if (kw.startsWith(needle)) return 3;
    if (kw.includes(needle)) return 2;
  }
  if (opt.label.toLowerCase().includes(needle)) return 2;
  return 0;
}

function _filterSuggestions(needle, limit = 10) {
  const n = String(needle || '').trim().toLowerCase();
  if (!n) return [];
  // Filter / attachment matches first — typing 'unread' should surface
  // the filter row before contact suggestions, since 'unread' isn't a
  // person.
  const filterMatches = _LIB_FILTER_OPTIONS
    .map(opt => ({ s: { kind: 'filter', value: opt.value, label: opt.label, icon: _libFilterIconFor(opt.value) }, score: _scoreFilterOption(opt, n) }))
    .filter(x => x.score > 0);
  const src = _libSuggestionCache || [];
  const contactMatches = src
    .map(s => ({ s: { kind: 'contact', ...s }, score: _scoreSuggestion(s, n) }))
    .filter(x => x.score > 0);
  // Email subject / sender-name matches — use the snapshot (unfiltered
  // list) when available so suggestions don't shrink as pills narrow the
  // visible grid. Cap to 4 so contacts + filters stay visible.
  const emails = _libPreSearchEmails || state._libEmails || [];
  const emailMatches = [];
  for (const em of emails) {
    const subj = String(em.subject || '').toLowerCase();
    const fromN = String(em.from_name || '').toLowerCase();
    let score = 0;
    if (subj.startsWith(n) || fromN.startsWith(n)) score = 3;
    else if (subj.includes(n) || fromN.includes(n)) score = 1;
    if (score > 0) {
      emailMatches.push({
        s: {
          kind: 'email',
          uid: em.uid,
          subject: em.subject || '(no subject)',
          from_name: em.from_name || em.from_address || '',
          date_label: _formatEmailSuggestionDate(em),
        },
        score,
      });
    }
    if (emailMatches.length >= 4) break;
  }
  return filterMatches.concat(contactMatches).concat(emailMatches)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(x => x.s);
}

function _exactTypedFilterSuggestion(value) {
  const needle = String(value || '').trim().toLowerCase();
  if (!needle) return null;
  // Keep automatic conversion deliberately exact. A query such as
  // "attachment invoice" should remain a text search, while the common
  // one-word shortcut should become the existing filter pill.
  const option = _LIB_FILTER_OPTIONS.find(opt => (
    opt.value === 'filter:has-attachments' && opt.keywords.includes(needle)
  ));
  return option
    ? { kind: 'filter', value: option.value, label: option.label, icon: _libFilterIconFor(option.value) }
    : null;
}

function _emailMatchesPill(em, pill) {
  if (!pill) return false;
  if (pill.type === 'contact') {
    const target = (pill.email || '').toLowerCase();
    if (!target) return false;
    if (String(em.from_address || '').toLowerCase() === target) return true;
    if (String(em.to || '').toLowerCase().includes(target)) return true;
    if (String(em.cc || '').toLowerCase().includes(target)) return true;
    return false;
  }
  if (pill.type === 'filter') {
    // Filter pills delegate to the server-side filter (state._libFilter)
    // or the has-attachments toggle. The list is already pre-filtered by
    // those when this runs, so the pill is effectively always-true here
    // — it lives in the pill bar purely as a visible affordance.
    return true;
  }
  // text pill — broad local-match
  const q = (pill.text || '').toLowerCase();
  if (!q) return true;
  return _matchesQuery(em, q);
}

function _matchesQuery(em, q) {
  const needle = q.toLowerCase();
  const dateNeedle = _formatEmailSuggestionDate(em).toLowerCase();
  const dateOnlyNeedle = dateNeedle.replace(/\s+\d{1,2}:\d{2}\s*(am|pm)?$/i, '');
  const rawDate = String(em.date || em.date_display || '').toLowerCase();
  return (
    String(em.subject || '').toLowerCase().includes(needle) ||
    String(em.from_name || '').toLowerCase().includes(needle) ||
    String(em.from_address || '').toLowerCase().includes(needle) ||
    String(em.to || '').toLowerCase().includes(needle) ||
    String(em.cc || '').toLowerCase().includes(needle) ||
    String(em.snippet || em.preview || '').toLowerCase().includes(needle) ||
    dateNeedle.includes(needle) ||
    dateOnlyNeedle.includes(needle) ||
    rawDate.includes(needle)
  );
}

// Apply the active pill filter to the snapshot. Each pill is OR-ed; an
// email shows up if ANY pill matches (a contact pill matches by from/to/cc
// equality, a text pill matches by the broad _matchesQuery substring).
function _applyPillFilter() {
  _exitEmailReaderModeForList();
  const pills = state._libSearchPills || [];
  const draft = (state._libSearchDraft || '').trim();
  const noPills = pills.length === 0;
  const noDraft = draft.length === 0;
  // First time we apply with anything active: snapshot the loaded list.
  if (!noPills || draft.length >= 1) {
    if (!_libPreSearchEmails) {
      _libPreSearchEmails = (state._libEmails || []).slice();
      _libPreSearchTotal = state._libTotal;
    }
  }
  if (noPills && noDraft) {
    if (_libPreSearchEmails) {
      state._libEmails = _libPreSearchEmails;
      state._libTotal = _libPreSearchTotal;
      _libPreSearchEmails = null;
      _libPreSearchTotal = 0;
    }
    _renderGrid();
    return;
  }
  const source = _libServerSearchEmails || _libPreSearchEmails || state._libEmails || [];
  // If the active server search covers a piece of text (either the live
  // draft OR an Enter-committed text pill), skip the local re-filter for
  // it — _emailMatchesPill only checks subject/from_name/from_address/
  // snippet (no BODY), so it was dropping legitimate server hits where
  // the match was in body text. Real pills (contact, filter chips) still
  // apply, and other text pills with different strings still apply.
  const libSearchLower = (_libSearchHadResults ? (state._libSearch || '').trim().toLowerCase() : '');
  const hasRefinementBase = !!(_libServerSearchEmails && pills.length > 1);
  const serverHandledDraft = !hasRefinementBase && !!(libSearchLower && draft && libSearchLower === draft.toLowerCase());
  const draftPill = (!serverHandledDraft && draft.length >= 1) ? { type: 'text', text: draft } : null;
  // Filter out text pills whose text matches the active server search —
  // those were the trigger for the IMAP query and don't need re-checking.
  const effectiveBasePills = (libSearchLower && !hasRefinementBase)
    ? pills.filter(p => !(p.type === 'text' && (p.text || '').toLowerCase() === libSearchLower))
    : pills;
  const effective = draftPill ? effectiveBasePills.concat([draftPill]) : effectiveBasePills;
  // AND across pills — "alice + bob" should mean both alice AND bob are
  // somewhere on the email (from/to/cc), not "from alice OR from bob".
  const filtered = source.filter(em => effective.every(p => _emailMatchesPill(em, p)));
  state._libEmails = filtered;
  _renderGrid();
}
// Back-compat shim: older call sites still expect _localSearchFilter.
function _localSearchFilter(query) {
  state._libSearchDraft = String(query || '');
  _applyPillFilter();
}

// Render the active pills inside the chip bar. Each pill carries a × to
// remove individually. Backspace on empty input also pops the last one.
function _emailFilterLabelFor(value) {
  const v = String(value || '');
  if (v === 'has-attachments') return 'Attachments';
  const sel = document.getElementById('email-lib-filter');
  const opt = sel?.querySelector?.(`option[value="${CSS.escape(v)}"]`);
  if (opt) return opt.textContent || v;
  const match = _LIB_FILTER_OPTIONS.find(o => o.value === `filter:${v}`);
  return match?.label || v;
}

function _activeEmailFilterPills() {
  const active = [];
  if (state._libHasAttachments) {
    active.push({ value: 'has-attachments', label: _emailFilterLabelFor('has-attachments') });
  }
  const filter = String(state._libFilter || 'all');
  if (filter && filter !== 'all') {
    active.push({ value: filter, label: _emailFilterLabelFor(filter) });
  }
  if (state._libDateFrom || state._libDateTo) {
    const range = state._libDateFrom && state._libDateTo
      ? `${state._libDateFrom} – ${state._libDateTo}`
      : state._libDateFrom ? `From ${state._libDateFrom}` : `Through ${state._libDateTo}`;
    active.push({ value: 'date-range', label: range });
  }
  return active;
}

function _clearActiveEmailFilterPill(value) {
  _resetBulkSelectionForContextChange({ rerender: true });
  const v = String(value || '');
  if (v === 'has-attachments') {
    state._libHasAttachments = false;
  } else if (v === 'date-range') {
    state._libDateFrom = '';
    state._libDateTo = '';
  } else if (state._libFilter === v) {
    state._libFilter = 'all';
    const sel = document.getElementById('email-lib-filter');
    if (sel) sel.value = 'all';
  }
  _syncUnreadWindowGlow();
  _syncReminderClearButton();
  _renderFilterPickerCurrent();
  _syncSearchOptionsMenu();
  _renderSearchPills();
  _loadEmailsFresh();
}

function _renderSearchPills() {
  const wrap = document.getElementById('email-lib-pills');
  if (!wrap) return;
  const pills = (state._libSearchPills || []).filter(p => p.type !== 'filter');
  state._libSearchPills = pills;
  const folderSelect = document.getElementById('email-lib-folder');
  const folder = String(folderSelect?.value || state._libFolder || 'INBOX');
  const showingFolderPill = folder && folder !== 'INBOX' && folder !== '__scheduled__';
  const filterPills = _activeEmailFilterPills();
  const chipBar = document.getElementById('email-lib-chip-bar');
  const input = document.getElementById('email-lib-search');
  const visiblePillCount = pills.length + filterPills.length + (showingFolderPill ? 1 : 0);
  if (chipBar) chipBar.classList.toggle('has-email-lib-pills', visiblePillCount > 0);
  if (input) input.placeholder = visiblePillCount > 0 ? '' : 'Search by name or text';
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const folderLabel = folderDisplayName(folder);
  const folderPill = showingFolderPill
    ? `<span class="email-lib-pill email-lib-folder-pill" data-folder-pill="${esc(folder)}" title="${esc(folderLabel)}" style="display:inline-flex;align-items:center;gap:3px;padding:0 5px 0 7px;border-radius:999px;background:color-mix(in srgb, var(--accent, var(--red)) 14%, transparent);color:var(--accent, var(--red));font-size:11px;line-height:20px;height:20px;font-weight:600;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;">
      <span style="overflow:hidden;text-overflow:ellipsis;">${esc(folderLabel)}</span>
      <button type="button" class="email-lib-folder-pill-x" title="Back to Inbox" style="background:transparent;border:0;color:inherit;cursor:pointer;font-size:12px;line-height:1;padding:0 2px;opacity:0.7;position:relative;top:-4px;">×</button>
    </span>`
    : '';
  const filterPillHtml = filterPills.map(p => {
    const titleAttr = esc(p.label);
    return `<span class="email-lib-pill email-lib-filter-pill" data-email-filter-pill="${esc(p.value)}" title="${titleAttr}" style="display:inline-flex;align-items:center;gap:3px;padding:0 5px 0 7px;border-radius:999px;background:color-mix(in srgb, var(--accent, var(--red)) 14%, transparent);color:var(--accent, var(--red));font-size:11px;line-height:20px;height:20px;font-weight:600;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;">
        <span class="email-lib-pill-icon" style="display:inline-flex;align-items:center;width:13px;height:13px;flex-shrink:0;">${_libFilterIconFor(`filter:${p.value}`)}</span>
        <span class="email-lib-filter-pill-label" style="overflow:hidden;text-overflow:ellipsis;">${esc(p.label)}</span>
        <button type="button" class="email-lib-filter-pill-x" data-email-filter-pill="${esc(p.value)}" title="Remove" style="background:transparent;border:0;color:inherit;cursor:pointer;font-size:12px;line-height:1;padding:0 2px;opacity:0.7;position:relative;top:-4px;">×</button>
      </span>`;
  }).join('');
  wrap.innerHTML = folderPill + filterPillHtml + pills.map((p, i) => {
    const label = p.type === 'contact' ? (p.name || p.email || '?') : (p.text || '');
    return `<span class="email-lib-pill" data-pill-idx="${i}" style="display:inline-flex;align-items:center;gap:3px;padding:0 5px 0 7px;border-radius:999px;background:color-mix(in srgb, var(--accent, var(--red)) 14%, transparent);color:var(--accent, var(--red));font-size:11px;line-height:20px;height:20px;font-weight:600;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;">
      <span style="overflow:hidden;text-overflow:ellipsis;">${esc(label)}</span>
      <button type="button" class="email-lib-pill-x" data-pill-idx="${i}" title="Remove" style="background:transparent;border:0;color:inherit;cursor:pointer;font-size:12px;line-height:1;padding:0 2px;opacity:0.7;position:relative;top:-4px;">×</button>
    </span>`;
  }).join('');
  requestAnimationFrame(() => {
    if (wrap.scrollWidth <= wrap.clientWidth) return;
    for (const value of ['has-attachments', 'reminders']) {
      const pill = wrap.querySelector(`.email-lib-filter-pill[data-email-filter-pill="${value}"]`);
      if (!pill) continue;
      pill.classList.add('email-lib-filter-pill-icon-only');
      if (wrap.scrollWidth <= wrap.clientWidth) break;
    }
  });
  wrap.querySelectorAll('.email-lib-filter-pill[data-email-filter-pill="date-range"]').forEach(pill => {
    pill.style.cursor = 'pointer';
    pill.addEventListener('click', (ev) => {
      if (ev.target.closest('.email-lib-filter-pill-x')) return;
      const menu = document.getElementById('email-search-options-menu');
      const trigger = document.getElementById('email-search-options-btn');
      const panel = menu?.querySelector('.email-search-date-panel');
      if (!menu || !panel) return;
      menu.hidden = false;
      panel.hidden = false;
      trigger?.setAttribute('aria-expanded', 'true');
      const from = panel.querySelector('#email-search-date-from');
      const to = panel.querySelector('#email-search-date-to');
      if (from) from.value = state._libDateFrom || '';
      if (to) to.value = state._libDateTo || '';
      from?.focus();
    });
  });
  wrap.querySelectorAll('.email-lib-pill-x').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.pillIdx);
      if (Number.isFinite(idx)) _removeSearchPillAt(idx);
    });
  });
  wrap.querySelectorAll('.email-lib-filter-pill-x').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _clearActiveEmailFilterPill(btn.dataset.emailFilterPill || '');
    });
  });
  wrap.querySelectorAll('.email-lib-folder-pill-x').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _resetBulkSelectionForContextChange({ rerender: true });
      const folders = Array.isArray(state._libFolders) ? state._libFolders : [];
      state._libFolder = folders.includes('INBOX') ? 'INBOX' : (folders.find(f => !/sent/i.test(String(f || ''))) || 'INBOX');
      const folderSel = document.getElementById('email-lib-folder');
      if (folderSel) folderSel.value = state._libFolder;
      _renderFolderPicker();
      _renderSearchPills();
      _loadEmailsFresh();
    });
  });
}

function _applyFilterPillSideEffect(pill) {
  // Filter pills drive the existing has-attachments toggle / filter
  // dropdown so the server returns the right list. Only one filter
  // pill is active at a time (see _addSearchPill).
  const sel = document.getElementById('email-lib-filter');
  if (pill.value === 'filter:has-attachments') {
    if (!state._libHasAttachments) {
      state._libHasAttachments = true;
      _syncSearchOptionsMenu();
    }
    if (sel && sel.value !== 'all') { sel.value = 'all'; sel.dispatchEvent(new Event('change')); }
    return;
  }
  // Any other filter pill — set the dropdown value, clear attachments
  if (state._libHasAttachments) {
    state._libHasAttachments = false;
    _syncSearchOptionsMenu();
  }
  if (sel) {
    const v = pill.value.replace(/^filter:/, '');
    if (sel.value !== v) { sel.value = v; sel.dispatchEvent(new Event('change')); }
  }
}

function _clearFilterPillSideEffect() {
  const sel = document.getElementById('email-lib-filter');
  if (state._libHasAttachments) {
    state._libHasAttachments = false;
    _syncSearchOptionsMenu();
  }
  if (sel && sel.value !== 'all') {
    sel.value = 'all'; sel.dispatchEvent(new Event('change'));
  }
}

function _addSearchPill(pill) {
  if (!pill) return;
  _resetBulkSelectionForContextChange({ rerender: true });
  if (!Array.isArray(state._libSearchPills)) state._libSearchPills = [];
  // Dedup by email (contact), text (text pill), or filter value.
  if (pill.type === 'contact') {
    const key = (pill.email || '').toLowerCase();
    if (!key) return;
    if (state._libSearchPills.some(p => p.type === 'contact' && (p.email || '').toLowerCase() === key)) return;
  } else if (pill.type === 'text') {
    const t = (pill.text || '').toLowerCase();
    if (!t) return;
    if (state._libSearchPills.some(p => p.type === 'text' && (p.text || '').toLowerCase() === t)) return;
  } else if (pill.type === 'filter') {
    // Filter state renders as a synthetic pill from state._libFilter /
    // state._libHasAttachments. Do not store a duplicate search pill.
    state._libSearchPills = state._libSearchPills.filter(p => p.type !== 'filter');
    _applyFilterPillSideEffect(pill);
    _renderSearchPills();
    return;
  }
  state._libSearchPills.push(pill);
  _renderSearchPills();
  _applyPillFilter();
}

function _searchQueryFromPills() {
  const parts = [];
  for (const p of state._libSearchPills || []) {
    if (p.type === 'text' && p.text) parts.push(String(p.text).trim());
    else if (p.type === 'contact' && (p.email || p.name)) parts.push(String(p.email || p.name).trim());
  }
  return parts.filter(Boolean).join(' ').trim();
}

function _removeSearchPillAt(idx) {
  if (!Array.isArray(state._libSearchPills)) return;
  _resetBulkSelectionForContextChange({ rerender: true });
  const removed = state._libSearchPills[idx];
  state._libSearchPills.splice(idx, 1);
  if (removed && removed.type === 'filter') _clearFilterPillSideEffect();
  _renderSearchPills();
  // Pill cleared all the way: if we got into search-result mode via the
  // IMAP search, the pre-search snapshot is now those results too (set
  // in _doSearch). Restoring from it would leave the user staring at
  // the same results with the pill bar empty. Re-fetch the real inbox
  // so removing the last pill genuinely "goes back".
  const noPillsLeft = (state._libSearchPills || []).length === 0
    && !(state._libSearchDraft || '').trim();
  if (noPillsLeft && _libSearchHadResults) {
    _libSearchHadResults = false;
    _libPreSearchEmails = null;
    _libPreSearchTotal = 0;
    _libServerSearchEmails = null;
    _libServerSearchTotal = 0;
    state._libSearch = '';
    state._libOffset = 0;
    const _searchInput = document.getElementById('email-lib-search');
    if (_searchInput) _searchInput.value = '';
    _loadEmails({ useCache: true });
    return;
  }
  const remainingQuery = _searchQueryFromPills();
  if (remainingQuery.length >= 2) {
    state._libSearch = remainingQuery;
    const _searchInput = document.getElementById('email-lib-search');
    if (_searchInput) _searchInput.value = '';
    state._libSearchDraft = '';
    _doSearch();
    return;
  }
  if ((state._libSearchPills || []).length && _libSearchHadResults) {
    _libSearchHadResults = false;
    _libPreSearchEmails = null;
    _libPreSearchTotal = 0;
    _libServerSearchEmails = null;
    _libServerSearchTotal = 0;
    state._libSearch = '';
    state._libOffset = 0;
    _loadEmails({ useCache: true });
    return;
  }
  _applyPillFilter();
}

// Render the autocomplete dropdown below the input. focusIdx highlights
// the active row; Tab autocompletes / Enter accepts that row.
function _renderSearchSuggestions(items) {
  const menu = document.getElementById('email-lib-suggest');
  if (!menu) return;
  if (!items.length) { menu.style.display = 'none'; menu.innerHTML = ''; return; }
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  menu.innerHTML = items.map((s, i) => {
    const highlight = i === _libSuggestionFocusIdx ? 'background:color-mix(in srgb, var(--fg) 8%, transparent);' : '';
    if (s.kind === 'filter') {
      return `<div class="email-lib-suggest-item" data-idx="${i}" style="display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;font-size:12px;${highlight}">
        <span style="display:inline-flex;align-items:center;width:13px;height:13px;color:var(--accent, var(--red));flex-shrink:0;">${s.icon}</span>
        <span style="font-weight:600;">${esc(s.label)}</span>
      </div>`;
    }
    if (s.kind === 'email') {
      return `<div class="email-lib-suggest-item" data-idx="${i}" style="display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;font-size:12px;${highlight}">
        <span style="display:inline-flex;align-items:center;width:13px;height:13px;color:var(--fg-muted, var(--fg));opacity:0.55;flex-shrink:0;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><polyline points="2 6 12 13 22 6"/></svg></span>
        <span style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.subject)}</span>
        ${s.from_name ? `<span style="opacity:0.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">— ${esc(s.from_name)}</span>` : ''}
        ${s.date_label ? `<span style="margin-left:auto;opacity:0.48;font-size:11px;white-space:nowrap;flex-shrink:0;">${esc(s.date_label)}</span>` : ''}
      </div>`;
    }
    return `<div class="email-lib-suggest-item" data-idx="${i}" style="display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;font-size:12px;${highlight}">
      <span style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.name || s.email)}</span>
      ${s.name ? `<span style="opacity:0.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.email)}</span>` : ''}
    </div>`;
  }).join('');
  menu.style.display = '';
  menu.querySelectorAll('.email-lib-suggest-item').forEach(row => {
    row.addEventListener('mousedown', (e) => {
      // mousedown (not click) so we beat the input blur handler that hides the menu.
      e.preventDefault();
      const idx = Number(row.dataset.idx);
      const item = items[idx];
      if (item) _acceptSuggestion(item);
    });
  });
}

function _hideSearchSuggestions() {
  const menu = document.getElementById('email-lib-suggest');
  if (menu) { menu.style.display = 'none'; menu.innerHTML = ''; }
  _libSuggestionFocusIdx = 0;
}

function _acceptSuggestion(s) {
  const input = document.getElementById('email-lib-search');
  if (s.kind === 'filter') {
    _addSearchPill({ type: 'filter', value: s.value, label: s.label });
  } else if (s.kind === 'email') {
    // Clear the draft + dropdown and open the matching card directly.
    if (input) input.value = '';
    state._libSearchDraft = '';
    _hideSearchSuggestions();
    _applyPillFilter();
    const grid = document.getElementById('email-lib-grid');
    const card = grid?.querySelector(`.doclib-card[data-uid="${CSS.escape(String(s.uid))}"]`);
    const em = (state._libEmails || []).find(x => String(x.uid) === String(s.uid))
            || (_libPreSearchEmails || []).find(x => String(x.uid) === String(s.uid));
    if (card && em) {
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      _toggleCardPreview(card, em);
    }
    return;
  } else {
    _addSearchPill({ type: 'contact', name: s.name, email: s.email });
    // Same as the text-pill path in the Enter handler: trigger the IMAP
    // search so unloaded emails (older than the current page) show up
    // when picking a contact. The local pill filter then narrows the
    // search results to that contact's address.
    const _q = (s.email || s.name || '').trim();
    if (_q && _q.length >= 2) {
      state._libSearch = _q;
      _doSearch();
    }
  }
  if (input) input.value = '';
  state._libSearchDraft = '';
  _hideSearchSuggestions();
  _applyPillFilter();
  if (input) input.focus();
}

async function _initEmailSearchChipBar() {
  const bar = document.getElementById('email-lib-chip-bar');
  const input = document.getElementById('email-lib-search');
  if (!bar || !input) return;
  state._libSearchPills = state._libSearchPills || [];
  state._libSearchDraft = '';
  _renderSearchPills();

  // Lazy-load suggestion source on first focus / keystroke.
  const _ensureSuggestionCache = async () => {
    if (_libSuggestionCache) return;
    _libSuggestionCache = await _buildSuggestionSource();
  };

  // Click anywhere in the bar lands the cursor in the input field.
  bar.addEventListener('click', (e) => {
    if (e.target.closest('.email-lib-pill-x')) return;
    input.focus();
  });

  let _itemsRef = [];
  const _refreshSuggestions = async () => {
    await _ensureSuggestionCache();
    _itemsRef = _filterSuggestions(input.value);
    // Default to no focused suggestion — text typing should feel like
    // regular search; the user has to ArrowDown / Tab explicitly to
    // pick a contact. Enter without a focused row commits as text.
    _libSuggestionFocusIdx = -1;
    _renderSearchSuggestions(_itemsRef);
  };

  input.addEventListener('focus', _refreshSuggestions);
  // Debounced IMAP search — fires ~500ms after the user stops typing so
  // searches for names/text not in the current inbox page actually surface
  // hits, instead of just locally filtering the visible window.
  //
  // Live local filtering on EVERY keystroke was clobbering server hits:
  // _emailMatchesPill / _matchesQuery check subject/from_name/from_address/
  // snippet but never body, so intermediate text like "sam" reduced the
  // 61 server results to whatever matched just those four fields (often
  // 0). User saw "no emails" while typing. So local filter is gone from
  // the typing path — debounced server search drives the grid. Pill
  // add/remove still re-runs the local filter through _applyPillFilter
  // directly.
  let _libSearchTypingTimer = null;
  input.addEventListener('input', async () => {
    _resetBulkSelectionForContextChange({ rerender: true });
    state._libSearchDraft = input.value;
    await _refreshSuggestions();
    if (_libSearchTypingTimer) clearTimeout(_libSearchTypingTimer);
    const v = input.value.trim();
    if (v.length >= 2) {
      _libSearchTypingTimer = setTimeout(() => {
        const cur = (input.value || '').trim();
        if (cur === v && cur.length >= 2) {
          state._libSearch = cur;
          _doSearch();
        }
      }, 500);
    } else if (!v && _libSearchHadResults) {
      // Cleared the input → restore the inbox the same way the pill-clear
      // path does. Otherwise the stale search results stayed up after the
      // user backspaced everything out.
      _libSearchHadResults = false;
      _libPreSearchEmails = null;
      _libPreSearchTotal = 0;
      state._libSearch = '';
      state._libOffset = 0;
      _loadEmails({ useCache: true });
    }
  });
  input.addEventListener('keydown', (e) => {
    const menu = document.getElementById('email-lib-suggest');
    const menuOpen = menu && menu.style.display !== 'none';
    if (e.key === 'Backspace' && !input.value && (state._libSearchPills || []).length) {
      e.preventDefault();
      _removeSearchPillAt(state._libSearchPills.length - 1);
      return;
    }
    if (e.key === 'ArrowDown' && menuOpen) {
      e.preventDefault();
      // -1 → 0 → 1 → … → length-1, then wraps back to -1 (no selection)
      const next = _libSuggestionFocusIdx + 1;
      _libSuggestionFocusIdx = next >= _itemsRef.length ? -1 : next;
      _renderSearchSuggestions(_itemsRef);
      return;
    }
    if (e.key === 'ArrowUp' && menuOpen) {
      e.preventDefault();
      // -1 → length-1 → length-2 → … → 0 → -1
      const next = _libSuggestionFocusIdx - 1;
      _libSuggestionFocusIdx = next < -1 ? _itemsRef.length - 1 : next;
      _renderSearchSuggestions(_itemsRef);
      return;
    }
    if (e.key === 'Tab' && menuOpen) {
      // Tab autocompletes the FIRST suggestion (most-relevant), regardless
      // of whether the user arrowed down yet — matches the user's mental
      // model of "type a name and tab to pick".
      const pick = _libSuggestionFocusIdx >= 0 ? _itemsRef[_libSuggestionFocusIdx] : _itemsRef[0];
      if (pick) { e.preventDefault(); _acceptSuggestion(pick); return; }
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      // Only commit a contact if the user explicitly focused one. Plain
      // Enter should default to a text pill so regular text search works
      // without forcing a contact pick.
      if (menuOpen && _libSuggestionFocusIdx >= 0 && _itemsRef[_libSuggestionFocusIdx]) {
        _acceptSuggestion(_itemsRef[_libSuggestionFocusIdx]);
        return;
      }
      const v = input.value.trim();
      if (v) {
        const typedFilter = _exactTypedFilterSuggestion(v);
        if (typedFilter) {
          _acceptSuggestion(typedFilter);
          return;
        }
        _addSearchPill({ type: 'text', text: v });
        input.value = '';
        state._libSearchDraft = '';
        _hideSearchSuggestions();
        // Pill-only filtering used to only check emails already loaded into
        // state._libEmails (the visible page of the inbox). Searches for
        // names/text that aren't in the current page returned "no emails"
        // even when matches existed on the server. Trigger the IMAP
        // search so state._libEmails is replaced with the actual hits,
        // then the pill filter narrows to matches.
        state._libSearch = v;
        _doSearch();
      }
      return;
    }
    if (e.key === 'Escape') {
      if (menuOpen) {
        // Just close the dropdown — let the modal Esc handler run on the
        // next Esc to actually dismiss the library.
        e.preventDefault();
        e.stopPropagation();
        _hideSearchSuggestions();
      } else {
        // Blur first so the modal Esc handler doesn't get suppressed by
        // any IME / typing-target check, and let the event propagate.
        try { input.blur(); } catch (_) {}
      }
    }
  });
}

// Click-to-add: clicking a recipient-chip in the email reader OR a
// .email-meta-sender in the library list drops the person into the
// library search as a contact pill so the user can pivot to "everything
// from / to this person" in one tap.
window.addEventListener('click', (e) => {
  const lib = document.getElementById('email-lib-modal');
  // 1) Recipient chips inside the email reader area
  const chip = e.target.closest && e.target.closest('.recipient-chip');
  if (chip && chip.closest('.email-reader-header, .email-card-reader, .email-reader-tab-modal')) {
    // Don't pivot to library search for chips in the From / To / Cc
    // meta — clicking those should just toggle the expanded address
    // view via the per-reader handler.
    if (chip.closest('.email-reader-meta')) return;
    const email = (chip.dataset && chip.dataset.email) || '';
    const name = (chip.dataset && chip.dataset.name) || (chip.textContent || '').trim();
    if (!email) return;
    e.preventDefault();
    e.stopPropagation();
    try { window.openEmailLibrary && window.openEmailLibrary(); } catch (_) {}
    _addSearchPill({ type: 'contact', name, email });
    return;
  }
  // 2) Sender name in a library list card row (only when the library is open)
  if (lib && !lib.classList.contains('hidden')) {
    const senderEl = e.target.closest && e.target.closest('.email-meta-sender');
    if (senderEl && senderEl.closest('#email-lib-grid')) {
      if (state._selectMode) return;
      const email = (senderEl.dataset && senderEl.dataset.email) || '';
      const name = (senderEl.dataset && senderEl.dataset.name) || (senderEl.textContent || '').trim();
      if (!email) return;
      e.preventDefault();
      e.stopPropagation();
      _addSearchPill({ type: 'contact', name, email });
    }
  }
}, true);

async function _doSearch() {
  _exitEmailReaderModeForList();
  _resetBulkSelectionForContextChange({ rerender: true });
  const seq = ++_libSearchSeq;
  const derived = _deriveSearchScope(state._libSearch);
  const q = derived.q;
  if (q.length < 2 && !derived.forced) {
    // Empty or too short — restore the normal folder if a previous search
    // had replaced the grid contents.
    if (_libSearchHadResults) {
      _libSearchHadResults = false;
      state._libOffset = 0;
      await _loadEmails({ useCache: true });
      return;
    }
    _renderGrid();
    return;
  }
  const accountAtStart = state._libAccountId || '';
  const folderAtStart = derived.folder || state._libFolder || 'INBOX';
  const serverScopeAtStart = derived.serverScope || 'all';
  // No grid-blanking spinner — the local filter already painted something
  // useful. Surface progress in the stats badge instead so the user knows
  // the server search is still grinding.
  const stats = document.getElementById('email-lib-stats');
  const originalStatsText = stats?.textContent || '';
  if (stats) stats.textContent = 'Searching…';
  _libSearchInFlight = true;
  _setEmailSyncStatus({ loading: true });
  // Force a re-render so the "Searching…" empty-state shows (and any
  // existing "No emails" gets replaced) while the fetch is in flight.
  _renderGrid();

  const stillCurrent = () => (
    seq === _libSearchSeq &&
    q === _deriveSearchScope(state._libSearch).q &&
    accountAtStart === (state._libAccountId || '') &&
    folderAtStart === (_deriveSearchScope(state._libSearch).folder || state._libFolder || 'INBOX') &&
    // A local/index result can be opened while the slower provider search is
    // still running. Do not let that second response re-render the grid and
    // destroy the reader the user just opened.
    !document.querySelector('#email-lib-grid .email-card-expanded')
  );
  const searchUrl = (localOnly = false) => {
    const params = new URLSearchParams({
      folder: folderAtStart,
      q,
      limit: '100',
      scope: serverScopeAtStart,
    });
    if (accountAtStart) params.set('account_id', accountAtStart);
    if (localOnly) params.set('local_only', '1');
    return `${API_BASE}/api/email/search?${params.toString()}`;
  };
  const folderListUrl = () => {
    const params = new URLSearchParams({
      folder: folderAtStart,
      limit: '100',
      offset: '0',
      filter: state._libFilter || 'all',
    });
    if (accountAtStart) params.set('account_id', accountAtStart);
    return `${API_BASE}/api/email/list?${params.toString()}`;
  };
  const mergeSearchResults = (painted, incoming) => {
    const byKey = new Map();
    const out = [];
    const add = (em) => {
      if (!em) return;
      const key = `${em.account_id || accountAtStart || ''}:${em.folder || folderAtStart || ''}:${em.uid || em.message_id || JSON.stringify(em)}`;
      if (byKey.has(key)) return;
      byKey.set(key, em);
      out.push(em);
    };
    (painted || []).forEach(add);
    const additions = [];
    const addIncoming = (em) => {
      if (!em) return;
      const key = `${em.account_id || accountAtStart || ''}:${em.folder || folderAtStart || ''}:${em.uid || em.message_id || JSON.stringify(em)}`;
      if (byKey.has(key)) return;
      byKey.set(key, em);
      additions.push(em);
    };
    (incoming || []).forEach(addIncoming);
    additions.sort((a, b) => {
      const ad = Number(a?.date_epoch || 0);
      const bd = Number(b?.date_epoch || 0);
      if (bd !== ad) return bd - ad;
      return String(b?.date || '').localeCompare(String(a?.date || ''));
    });
    return out.concat(additions);
  };
  let paintedInterimResults = false;
  const paintSearchData = (data, interim = false) => {
    if (!stillCurrent()) return false;
    if (data.error) throw new Error(data.error);
    let results = data.emails || [];
    if (!interim && paintedInterimResults) {
      results = mergeSearchResults(state._libEmails || [], results);
    }
    if (!interim && paintedInterimResults && results.length === 0) {
      if (stats) {
        const count = state._libTotal || (state._libEmails || []).length;
        stats.textContent = `${count} cached match${count === 1 ? '' : 'es'}`;
      }
      _setEmailSyncStatus({
        updatedAt: data.sync?.updated_at || '',
        source: data.sync?.source || data.source || '',
        loading: false,
      });
      return true;
    }
    _libSearchHadResults = true;
    const pills = state._libSearchPills || [];
    const preservingBase = !!(_libServerSearchEmails && pills.length > 1);
    if (!preservingBase) {
      _libServerSearchEmails = results.slice();
      _libServerSearchTotal = Math.max(Number(data.total || 0), results.length);
      _libPreSearchEmails = results.slice();
      _libPreSearchTotal = _libServerSearchTotal;
      state._libEmails = results;
      state._libTotal = _libServerSearchTotal;
    } else {
      state._libEmails = _libServerSearchEmails.slice();
      state._libTotal = _libServerSearchTotal;
    }
    if (pills.length) {
      _applyPillFilter();
      if (!(state._libEmails || []).length && !preservingBase) state._libEmails = results;
    }
    _renderGrid();
    const count = Math.max(Number(data.total || 0), results.length);
    if (stats) {
      if (interim) {
        stats.textContent = `${count} cached match${count === 1 ? '' : 'es'} · searching…`;
      } else {
        const source = data.source === 'index' ? ' cached' : '';
        stats.textContent = `${count}${source} match${count === 1 ? '' : 'es'}`;
      }
    }
    _setEmailSyncStatus({
      updatedAt: interim ? '' : (data.sync?.updated_at || ''),
      source: data.sync?.source || data.source || '',
      loading: interim,
    });
    if (interim && results.length) paintedInterimResults = true;
    return true;
  };

  try {
    if (q.length < 2 && derived.forced) {
      const res = await fetch(folderListUrl());
      const data = await res.json();
      if (!stillCurrent()) return;
      paintSearchData({
        emails: (data.emails || []).map(em => ({ ...em, folder: folderAtStart })),
        total: data.total || (data.emails || []).length,
        source: 'folder',
        sync: { source: 'folder' },
      }, false);
      return;
    }
    const fullSearchPromise = fetch(searchUrl(false)).then(res => res.json());
    const localSearchPromise = fetch(searchUrl(true)).then(res => res.json());
    try {
      const localData = await localSearchPromise;
      if (!stillCurrent()) return;
      if (!localData.error && (localData.emails || []).length) {
        paintSearchData(localData, true);
      }
    } catch (_) {
      if (!stillCurrent()) return;
    }

    const data = await fullSearchPromise;
    if (!stillCurrent()) return;
    paintSearchData(data, false);
  } catch (e) {
    if (stats) stats.textContent = originalStatsText || 'Search failed';
    try { console.error('[email-search] fetch failed:', e); } catch {}
  } finally {
    _libSearchInFlight = false;
    _setEmailSyncStatus({ loading: false });
    // If the full search was intentionally ignored because its result landed
    // after the user opened a reader, finish the progress label without
    // touching the grid or collapsing that reader.
    const openReader = document.querySelector('#email-lib-grid .email-card-expanded');
    if (openReader && stats && /searching/i.test(stats.textContent || '')) {
      const count = state._libTotal || (state._libEmails || []).length;
      stats.textContent = `${count} cached match${count === 1 ? '' : 'es'}`;
    }
  }
}

// Custom dropdown for the email filter (All/Unread/Favorites/...). Replaces
// the native <select> so each row can carry an SVG icon. The hidden
// <select id="email-lib-filter"> stays as the value source — clicking a
// menu item updates its value and dispatches 'change', so every existing
// listener keeps working.
const _EMAIL_FILTER_ICONS = {
  'all':           '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
  'unread':        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>',
  'favorites':     '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
  'undone':        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/></svg>',
  'reminders':     '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/></svg>',
  'unanswered':    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>',
  'pending_30d':   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  'stale_30d':     '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="10" y1="14" x2="14" y2="18"/><line x1="14" y1="14" x2="10" y2="18"/></svg>',
  'tag:urgent':    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  'tag:reply-soon':'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/><circle cx="18" cy="6" r="2" fill="currentColor" stroke="none"/></svg>',
  'tag:spam':      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>',
};

function _filterIcon(value) {
  return _EMAIL_FILTER_ICONS[value] || _EMAIL_FILTER_ICONS['all'];
}

function _folderIcon(value) {
  const v = String(value || '').toLowerCase();
  if (value === '__scheduled__') {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
  }
  if (v.includes('sent')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  }
  if (v.includes('star') || v.includes('favorite')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
  }
  if (v.includes('archive') || v.includes('all mail')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9"/><path d="M10 13h4"/></svg>';
  }
  if (v.includes('junk') || v.includes('spam')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>';
  }
  if (v.includes('trash') || v.includes('bin')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
  }
  if (v.includes('draft')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
  }
  if (v === 'inbox' || v.includes('inbox')) {
    return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/></svg>';
  }
  return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
}

function _renderFolderPicker() {
  const sel = document.getElementById('email-lib-folder');
  const btn = document.getElementById('email-folder-btn');
  const menu = document.getElementById('email-folder-menu');
  if (!sel || !btn || !menu) return;
  const value = sel.value || state._libFolder || 'INBOX';
  const selectedOpt = [...sel.options].find(o => o.value === value && !o.disabled);
  const label = selectedOpt?.textContent || folderDisplayName(value);
  const iconWrap = btn.querySelector('.email-folder-current-icon');
  const labelEl = btn.querySelector('.email-folder-label');
  if (iconWrap) iconWrap.innerHTML = _folderIcon(value);
  if (labelEl) labelEl.textContent = label;
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const rows = [];
  for (const opt of sel.children) {
    if (opt.disabled) {
      rows.push('<div class="email-filter-group email-folder-group">Folders</div>');
      continue;
    }
    rows.push(`<button type="button" role="option" class="email-filter-item email-folder-item" data-value="${esc(opt.value)}">
      <span class="email-filter-item-icon">${_folderIcon(opt.value)}</span>
      <span class="email-filter-item-label">${esc(opt.textContent || folderDisplayName(opt.value))}</span>
    </button>`);
  }
  menu.innerHTML = rows.join('');
}

function _initFolderPicker() {
  const sel = document.getElementById('email-lib-folder');
  const picker = document.getElementById('email-folder-picker');
  const btn = document.getElementById('email-folder-btn');
  const menu = document.getElementById('email-folder-menu');
  if (!sel || !picker || !btn || !menu || picker._wired) return;
  picker._wired = true;
  let dismiss = () => {};
  const finishClose = () => {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    dismiss = () => {};
  };
  const open = () => {
    const filterMenu = document.getElementById('email-filter-menu');
    const filterBtn = document.getElementById('email-filter-btn');
    filterMenu?._dismiss?.();
    if (filterMenu && !filterMenu.hidden) filterMenu.hidden = true;
    if (filterBtn) filterBtn.setAttribute('aria-expanded', 'false');
    _renderFolderPicker();
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    dismiss = bindMenuDismiss(menu, finishClose, e => !picker.contains(e.target));
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.hidden) open(); else dismiss();
  });
  menu.addEventListener('click', (e) => {
    const item = e.target.closest('.email-folder-item');
    if (!item) return;
    sel.value = item.dataset.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    dismiss();
  });
  document.addEventListener('click', (e) => {
    if (!menu.hidden && !picker.contains(e.target)) dismiss();
  });
  _renderFolderPicker();
}

function _renderFilterPickerCurrent() {
  const sel = document.getElementById('email-lib-filter');
  const btn = document.getElementById('email-filter-btn');
  if (!sel || !btn) return;
  const value = sel.value || 'all';
  const opt = sel.querySelector(`option[value="${CSS.escape(value)}"]`);
  const label = opt ? opt.textContent : value;
  const iconWrap = btn.querySelector('.email-filter-icon');
  const labelEl = btn.querySelector('.email-filter-label');
  if (iconWrap) iconWrap.innerHTML = _filterIcon(value);
  if (labelEl) labelEl.textContent = label;
}

function _initFilterPicker() {
  const sel = document.getElementById('email-lib-filter');
  const picker = document.getElementById('email-filter-picker');
  const btn = document.getElementById('email-filter-btn');
  const menu = document.getElementById('email-filter-menu');
  if (!sel || !picker || !btn || !menu || picker._wired) return;
  picker._wired = true;

  // Build menu from the hidden <select> contents (preserves optgroup labels).
  const items = [];
  for (const child of sel.children) {
    if (child.tagName === 'OPTGROUP') {
      items.push({ group: child.label });
      for (const o of child.children) {
        items.push({ value: o.value, label: o.textContent, group: child.label });
      }
    } else if (child.tagName === 'OPTION') {
      items.push({ value: child.value, label: child.textContent });
    }
  }
  menu.innerHTML = '<div class="email-filter-menu-title">Filter by...</div>' + items.map(it => {
    if (!it.value) {
      return `<div class="email-filter-group">${it.group}</div>`;
    }
    return `<button type="button" role="option" class="email-filter-item" data-value="${it.value}">
      <span class="email-filter-item-icon">${_filterIcon(it.value)}</span>
      <span class="email-filter-item-label">${it.label}</span>
    </button>`;
  }).join('');

  let dismiss = () => {};
  const finishClose = () => {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    dismiss = () => {};
  };
  const open = () => {
    const folderMenu = document.getElementById('email-folder-menu');
    const folderBtn = document.getElementById('email-folder-btn');
    folderMenu?._dismiss?.();
    if (folderMenu && !folderMenu.hidden) folderMenu.hidden = true;
    if (folderBtn) folderBtn.setAttribute('aria-expanded', 'false');
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    dismiss = bindMenuDismiss(menu, finishClose, e => !picker.contains(e.target));
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.hidden) open(); else dismiss();
  });
  menu.addEventListener('click', (e) => {
    const item = e.target.closest('.email-filter-item');
    if (!item) return;
    sel.value = item.dataset.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    dismiss();
  });
  document.addEventListener('click', (e) => {
    if (!menu.hidden && !picker.contains(e.target)) dismiss();
  });
  _renderFilterPickerCurrent();
}

function _renderEmailLoading(grid) {
  if (!grid) return null;
  grid.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'email-list-skeleton';
  wrap.setAttribute('aria-label', 'Loading emails');
  wrap.innerHTML = Array.from({ length: 8 }, (_, idx) => `
    <div class="email-skeleton-row${idx % 3 === 2 ? ' compact' : ''}">
      <span class="email-skeleton-dot"></span>
      <div class="email-skeleton-lines">
        <span class="email-skeleton-line title"></span>
        <span class="email-skeleton-line meta"></span>
      </div>
      <span class="email-skeleton-line date"></span>
    </div>
  `).join('');
  grid.appendChild(wrap);
  return null;
}

export function _emailReaderSkeletonHtml() {
  return `
    <div class="email-reader-skeleton" aria-label="Loading email">
      <div class="email-reader-skeleton-header">
        <span class="email-skeleton-line chip"></span>
        <span class="email-skeleton-line chip short"></span>
        <span class="email-skeleton-line action"></span>
        <span class="email-skeleton-line action"></span>
        <span class="email-skeleton-line action"></span>
      </div>
      <div class="email-reader-skeleton-atts">
        <span class="email-skeleton-line attachment"></span>
        <span class="email-skeleton-line attachment short"></span>
      </div>
      <div class="email-reader-skeleton-body">
        <span class="email-skeleton-line body wide"></span>
        <span class="email-skeleton-line body"></span>
        <span class="email-skeleton-line body medium"></span>
        <span class="email-skeleton-line body gap"></span>
        <span class="email-skeleton-line body wide"></span>
        <span class="email-skeleton-line body medium"></span>
        <span class="email-skeleton-line body"></span>
        <span class="email-skeleton-line body wide"></span>
        <span class="email-skeleton-line body medium"></span>
        <span class="email-skeleton-line body gap"></span>
        <span class="email-skeleton-line body"></span>
        <span class="email-skeleton-line body wide"></span>
        <span class="email-skeleton-line body short"></span>
      </div>
    </div>
  `;
}

function _appendEmailSearchProgressRow(grid) {
  if (!grid || !_libSearchInFlight || grid.querySelector('.email-search-progress-row')) return;
  const row = document.createElement('div');
  row.className = 'email-search-progress-row';
  row.innerHTML = `
    <span class="email-search-progress-dot"></span>
    <span>Searching more mail...</span>
  `;
  grid.appendChild(row);
}

// Refreshes the small accent-pill in the modal title with the unread count
// for the current folder. When the inbox is currently filtered to unread, the
// pill flips to show the total-emails count + "all" label, because clicking
// it would toggle the filter off — so the label needs to advertise the
// action, not the now-current view. Uses the cheap unread-state endpoint for
// the normal badge; silent on failure.
export async function _refreshUnreadBadge({ unreadCountOverride = null, preserveAutoReplyTitle = false, syncAutoReplyTitle = false } = {}) {
  const badge = document.getElementById('email-lib-unread-badge');
  if (!badge) return;
  const refreshSeq = ++state._autoReplyRefreshSeq;
  const accountAtStart = String(state._libAccountId || '');
  try {
    const folder = state._libFolder || 'INBOX';
    const autoReplyBadge = document.getElementById('email-lib-auto-reply-badge');
    if (folder === '__scheduled__') {
      badge.style.display = 'none';
      if (syncAutoReplyTitle && !preserveAutoReplyTitle) _syncEmailAutoReplyTitle(false);
      return;
    }
    const cfg = await _fetchEmailSettingsConfig().catch(() => null);
    // Several mailbox/account refreshes can overlap while the settings page
    // is saving. Do not let an older response restore the previous account's
    // Auto Reply state after the current account has already changed.
    if (refreshSeq !== state._autoReplyRefreshSeq || accountAtStart !== String(state._libAccountId || '')) return;
    if (!cfg) return;
    const awayActive = _isAutoReplyActiveForCurrentAccount(cfg);
    if (!preserveAutoReplyTitle) {
      const awayChanged = state._libAutoReplyActive !== awayActive;
      state._libAutoReplyActive = awayActive;
      if (awayChanged) _renderAccountsStrip();
      if (syncAutoReplyTitle && typeof state._libAutoReplyDraftActive !== 'boolean') {
        _syncEmailAutoReplyTitle(awayActive);
      }
    }
    if (autoReplyBadge) {
      autoReplyBadge.title = awayActive ? 'Auto Reply is active - open settings' : 'Auto Reply is off';
    }
    let n;
    if (unreadCountOverride !== null && unreadCountOverride !== undefined) {
      n = Math.max(0, Number(unreadCountOverride) || 0);
    } else {
      const res = await fetch(emailApiUrl('/api/email/unread-state', {
        folder,
        account_id: state._libAccountId || undefined,
      }));
      const data = await res.json();
      n = data.unread_count || 0;
    }
    _syncUnreadTabBadge(n);
    if (folder === 'INBOX') _syncCurrentAccountUnreadCount(n);
    badge.classList.remove('email-lib-away-badge');
    if (state._libFilter === 'unread') {
      // Currently viewing unread — show what the click will take you to.
      try {
        const allRes = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folder)}${_acct()}&limit=1&filter=all`);
        const allData = await allRes.json();
        const t = allData.total || 0;
        badge.textContent = `${t} all`;
        badge.title = 'Show all emails';
        badge.dataset.mode = 'unread';
        badge.style.display = '';
      } catch (_) {
        badge.textContent = 'Show all';
        badge.title = 'Show all emails';
        badge.dataset.mode = 'unread';
        badge.style.display = '';
      }
    } else if (n > 0) {
      badge.textContent = n > 999 ? '999+ unread' : `${n} unread`;
      badge.title = 'Show unread emails';
      badge.dataset.mode = 'unread';
      badge.style.display = '';
    } else {
      delete badge.dataset.mode;
      badge.style.display = 'none';
    }
  } catch (_) { _syncUnreadTabBadge(0); }
}

export async function _loadEmails({ force = false, useCache = true } = {}) {
  const seq = ++_libLoadSeq;
  state._libLoading = true;
  const accountAtStart = state._libAccountId || '';
  const folderAtStart = state._libFolder;
  const filterAtStart = state._libFilter;
  const offsetAtStart = state._libOffset;
  const searchAtStart = state._libSearch;
  const hasAttachmentsAtStart = state._libHasAttachments;
  const dateFromAtStart = state._libDateFrom || '';
  const dateToAtStart = state._libDateTo || '';

  const grid = document.getElementById('email-lib-grid');
  if (!grid) { if (seq === _libLoadSeq) state._libLoading = false; return; }

  // SWR: when loading the first page of a real folder with no search,
  // paint the cached list immediately (no spinner, no blank grid) and
  // then quietly refetch behind it. Pagination, search, and the
  // scheduled virtual folder skip the cache and use the old spinner
  // path. `force` (Refresh button) can still consult the cache for
  // perceptual continuity, but adds a cache-buster so the server's 8s
  // list cache is bypassed too. Account/folder/filter changes pass
  // `useCache: false` so stale rows from the previous view never flash.
  const cacheable =
    offsetAtStart === 0 &&
    !searchAtStart &&
    folderAtStart !== '__scheduled__';
  const ck = cacheable ? _libCacheKey() : null;
  const cachedCandidate = (useCache && cacheable) ? _libCacheGet(ck) : null;
  // Do not paint a cached empty result while a forced refresh is already
  // underway. A transient provider failure can cache an empty tag filter;
  // rendering it first flashes "No emails" before the fresh rows arrive.
  const cached = cachedCandidate
    && Array.isArray(cachedCandidate.emails)
    && cachedCandidate.emails.length
    ? cachedCandidate
    : null;
  let paintedExisting = Boolean(cached || state._libEmails.length);
  const paintData = (data, { cacheSource = false } = {}) => {
    state._libEmails = data.emails || [];
    state._libTotal = data.total || 0;
    _libRenderedViewKey = ck || '';
    const sync = data.sync || {};
    const _activePills = (state._libSearchPills || []).length > 0
                     || (state._libSearchDraft || '').length > 0;
    if (_activePills) {
      _libPreSearchEmails = state._libEmails.slice();
      _libPreSearchTotal = state._libTotal;
      _applyPillFilter();
    } else {
      _renderGrid();
    }
    const stats = document.getElementById('email-lib-stats');
    if (stats) stats.textContent = `${state._libTotal} emails`;
    _setEmailSyncStatus({
      updatedAt: sync.updated_at || '',
      source: sync.source || (cacheSource ? 'client_cache' : ''),
      loading: false,
    });
    if (cacheable && !cacheSource) {
      _libCachePut(ck, { emails: state._libEmails.slice(), total: state._libTotal, sync });
    }
  };

  let sp = null;
  if (cached) {
    // Suppress the open-cascade animation when we're painting from
    // cache — the data was already on screen a moment ago, so sliding
    // each card in fresh feels janky. Also prevents the cascade from
    // re-firing when the bg refetch lands within the 900ms cleanup
    // window and appends new card nodes into the still-classed grid.
    state._libJustOpened = false;
    const grid2 = document.getElementById('email-lib-grid');
    if (grid2) grid2.classList.remove('email-lib-just-opened');
    paintData(cached, { cacheSource: true });
    paintedExisting = true;
    if (force) _setEmailSyncStatus({ loading: true });
    if (!force) return;
  } else if (state._libEmails.length && cacheable) {
    _renderGrid();
    _setEmailSyncStatus({ loading: true });
    paintedExisting = true;
  } else {
    sp = _renderEmailLoading(grid);
  }

  try {
    _syncUnreadWindowGlow();
    if (folderAtStart === '__scheduled__') {
      await _loadScheduled(grid, sp);
    } else {
      const accountQS = accountAtStart ? `&account_id=${encodeURIComponent(accountAtStart)}` : '';
      const attQS = hasAttachmentsAtStart ? '&has_attachments=1' : '';
      const dateQS = `${dateFromAtStart ? `&date_from=${encodeURIComponent(dateFromAtStart)}` : ''}${dateToAtStart ? `&date_to=${encodeURIComponent(dateToAtStart)}` : ''}`;
      if (!cached && cacheable && !force) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 450);
        try {
          const fastRes = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folderAtStart)}${accountQS}&limit=${_LIB_INITIAL_PAGE_SIZE}&offset=${offsetAtStart}&filter=${filterAtStart}${attQS}&cached_only=1`, {
            signal: ctrl.signal,
          });
          const fastData = await fastRes.json().catch(() => null);
          if (seq === _libLoadSeq
              && accountAtStart === (state._libAccountId || '')
              && fastData
              && !fastData.error
              && Array.isArray(fastData.emails)
              && fastData.emails.length) {
            if (sp) { sp.destroy(); sp = null; }
            state._libJustOpened = false;
            grid.classList.remove('email-lib-just-opened');
            paintData(fastData);
            paintedExisting = true;
            if (!force) return;
          }
        } catch (_) {
          // Cold index miss/timeout: leave the spinner and continue to IMAP.
        } finally {
          clearTimeout(timer);
        }
      }
      // `refresh=1` is the explicit manual-refresh contract: the server
      // evicts its list cache, drops the pooled IMAP handle, and refetches
      // visible rows instead of trusting the durable index. `&_=...` remains
      // as a browser/proxy cache-buster.
      const buster = force ? `&refresh=1&_=${Date.now()}` : '';
      const res = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folderAtStart)}${accountQS}&limit=${_LIB_INITIAL_PAGE_SIZE}&offset=${offsetAtStart}&filter=${filterAtStart}${attQS}${dateQS}${buster}`);
      const data = await res.json();
      if (seq !== _libLoadSeq || accountAtStart !== (state._libAccountId || '')) return;
      if (data.error) throw new Error(data.error);
      const sync = data.sync || {};
      if (sp) sp.destroy();
      paintData({ emails: data.emails || [], total: data.total || 0, sync });
      if (filterAtStart === 'unread') {
        _refreshUnreadBadge({ unreadCountOverride: data.total || 0 });
      } else {
        _refreshUnreadBadge();
        _refreshAccountUnreadHighlights().catch(() => {});
      }
    }
  } catch (e) {
    if (seq !== _libLoadSeq || accountAtStart !== (state._libAccountId || '')) return;
    if (sp) sp.destroy();
    // If we already painted the cached list, leave it on screen — beats
    // wiping it for "Failed to load" when there's still readable content.
    if (!paintedExisting) {
      const msg = e && e.message ? `Failed to load: ${e.message}` : 'Failed to load';
      grid.innerHTML = `<div class="email-loading">${_esc(msg)}${_emailSetupHintHtml()}</div>`;
      _wireEmailSetupHint(grid);
    }
  } finally {
    if (seq === _libLoadSeq) state._libLoading = false;
  }
}

async function _loadScheduled(grid, sp) {
  const res = await fetch(`${API_BASE}/api/email/scheduled`);
  const data = await res.json();
  if (sp) sp.destroy();
  const items = data.scheduled || [];
  grid.innerHTML = '';
  const stats = document.getElementById('email-lib-stats');
  if (stats) stats.textContent = `${items.length} scheduled`;
  _setEmailSyncStatus({
    updatedAt: new Date().toISOString(),
    source: 'local',
    loading: false,
  });

  if (items.length === 0) {
    grid.innerHTML = '<div class="email-loading">No scheduled emails</div>';
    return;
  }

  for (const it of items) {
    const card = document.createElement('div');
    card.className = 'doclib-card memory-item';

    const sendDate = new Date(it.send_at);
    const dateStr = sendDate.toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });

    const content = document.createElement('div');
    content.style.cssText = 'flex:1;min-width:0;';
    const subject = it.subject || '(no subject)';
    const toDisplay = it.to || '(no recipient)';

    content.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="memory-item-title">${_esc(subject)}</span>
        ${it.status === 'failed' ? '<span style="font-size:9px;color:var(--red);border:1px solid var(--red);padding:1px 4px;border-radius:4px;">FAILED</span>' : '<span style="font-size:9px;opacity:0.6;border:1px solid var(--border);padding:1px 4px;border-radius:4px;">PENDING</span>'}
      </div>
      <div style="font-size:10px;opacity:0.7;margin-top:2px;">
        To: ${_esc(toDisplay)} · Sends ${_esc(dateStr)}
      </div>
      ${it.error ? `<div style="font-size:10px;color:var(--red);margin-top:2px;">${_esc(it.error)}</div>` : ''}
    `;
    card.appendChild(content);

    // Cancel button
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'memory-item-btn';
    cancelBtn.title = 'Cancel scheduled send';
    cancelBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    cancelBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const { styledConfirm } = await import('../ui.js?v=20260916largetoolscroll1');
      const ok = await styledConfirm(`Cancel scheduled email "${subject}"?`, { confirmText: 'Cancel Send', cancelText: 'Keep', danger: true });
      if (!ok) return;
      try {
        await fetch(`${API_BASE}/api/email/scheduled/${it.id}`, { method: 'DELETE' });
        _loadEmails();
      } catch (err) { console.error(err); }
    });
    const actionsWrap = document.createElement('div');
    actionsWrap.className = 'memory-item-actions';
    actionsWrap.appendChild(cancelBtn);
    card.appendChild(actionsWrap);

    grid.appendChild(card);
  }
}

function _emailDateBucketLabel(value) {
  if (!value) return 'Older';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Older';
  const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const now = new Date();
  const today = dayStart(now);
  const day = dayStart(d);
  const diff = Math.round((today - day) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  if (diff > 1 && diff < 7) return d.toLocaleDateString([], { weekday: 'long' });
  if (diff >= 365) {
    const years = Math.floor(diff / 365);
    return `${years} ${years === 1 ? 'year' : 'years'} ago`;
  }
  if (diff >= 180) return '6 months ago';
  if (diff >= 30) return `${Math.floor(diff / 30) * 30} days ago`;
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], sameYear ? { month: 'long', day: 'numeric' } : { month: 'long', day: 'numeric', year: 'numeric' });
}

function _createEmailDateHeader(label, timelineBreak = false) {
  const el = document.createElement('div');
  el.className = 'date-section-header email-date-section-header' + (timelineBreak ? ' email-date-gap-break' : '');
  el.textContent = label;
  return el;
}

// Add breathing room only for an unexpected hole in an otherwise dense
// timeline. A uniformly sparse archive should remain compact, even when its
// dates are months apart.
function _emailTimelineGapThreshold(items) {
  const dates = items.map(em => new Date(em?.date).getTime()).filter(Number.isFinite);
  const gaps = [];
  for (let i = 1; i < dates.length; i++) {
    const gap = Math.abs(dates[i - 1] - dates[i]) / 86400000;
    if (gap > 0) gaps.push(gap);
  }
  if (!gaps.length) return 90;
  gaps.sort((a, b) => a - b);
  const middle = Math.floor(gaps.length / 2);
  const median = gaps.length % 2 ? gaps[middle] : (gaps[middle - 1] + gaps[middle]) / 2;
  return Math.max(90, median * 2.5);
}

function _dateGroupEmailsWithPinned(items, mode = 'recent') {
  const ordered = [];
  let groupLabel = null;
  let groupItems = [];
  const priority = (em) => {
    if (mode === 'unread') return Number(!em?.is_read);
    return Number(!!em?.is_flagged);
  };
  const flushGroup = () => {
    if (!groupItems.length) return;
    ordered.push(...groupItems.map((em, idx) => ({ em, idx }))
      .sort((a, b) => priority(b.em) - priority(a.em) || a.idx - b.idx)
      .map(item => item.em));
    groupItems = [];
  };
  for (const em of items) {
    const label = _emailDateBucketLabel(em?.date);
    if (label !== groupLabel) {
      flushGroup();
      groupLabel = label;
    }
    groupItems.push(em);
  }
  flushGroup();
  return ordered;
}

export function _renderGrid() {
  const grid = document.getElementById('email-lib-grid');
  if (!grid) return;
  const modal = document.getElementById('email-lib-modal');
  // A background mailbox refresh must not destroy the reader currently being
  // viewed. Clearing the grid here removes the expanded card while leaving
  // `.email-reading` on the modal, which produces a headerless full email
  // list. Explicit folder/account/search changes call the reset helper first.
  if (grid.querySelector('.doclib-card-expanded, .email-card-expanded')) return;
  if (modal?.classList.contains('email-reading')) {
    modal.classList.remove('email-reading');
    modal.style.removeProperty('--email-reading-modal-min-h');
  }
  grid.innerHTML = '';

  let filtered = state._libEmails;
  try { console.log('[email-search] _renderGrid: state._libEmails.length=', (state._libEmails || []).length, 'pills=', (state._libSearchPills || []).length, 'draft=', JSON.stringify(state._libSearchDraft || ''), 'libSearch=', JSON.stringify(state._libSearch || '')); } catch {}
  _syncSearchOptionsMenu();

  // 'recent' is the default order from the API. Date stays the primary
  // grouping; unread/favorite priorities float inside each date section.
  filtered = _dateGroupEmailsWithPinned([...filtered], state._libSort);

  if (filtered.length === 0) {
    // Active search — don't flash "No emails": the IMAP fetch is still
    // running. Show a "Searching…" placeholder until _doSearch resolves
    // and renders again. Without this the user saw an empty state
    // smiley for ~500ms between the optimistic pill-filter clear and
    // the server response landing.
    if (_libSearchInFlight) {
      _renderEmailLoading(grid);
      return;
    }
    // Inbox-zero is a win — pair the message with a small smiley so the
    // empty state reads as "all caught up", not "something's broken".
    const _smileyIco = '<span style="vertical-align:-3px;margin-left:6px;">' + emptyStateIcon('smiley') + '</span>';
    // Only show the "Set up at Settings › Integrations" hint when the inbox
    // is TRULY empty — no filter, no search, no source emails. A sub-filter
    // (reminders, unread, etc.) that happens to be empty isn't a setup
    // problem; the link there reads as nonsense.
    const _isTrulyEmpty = (
      state._libEmails.length === 0
      && (!state._libFilter || state._libFilter === 'all')
      && !(state._libSearch || '').trim()
    );
    if (_isTrulyEmpty) {
      grid.innerHTML =
        '<div class="email-loading" style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;text-align:center;">' +
          '<span>No emails' + _smileyIco + '</span>' +
          '<span style="opacity:0.7;font-size:11px;">' +
            'Set up at: <a href="#" data-open-settings="integrations" style="color:var(--accent,var(--red));text-decoration:underline;">Settings &rsaquo; Integrations</a>' +
          '</span>' +
        '</div>';
      const _link = grid.querySelector('[data-open-settings]');
      if (_link) _link.addEventListener('click', (e) => {
        e.preventDefault();
        _openSettingsTab(_link.dataset.openSettings || 'integrations');
      });
    } else {
      grid.innerHTML =
        '<div class="email-loading" style="display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;">' +
          '<span>No emails' + _smileyIco + '</span>' +
        '</div>';
    }
    return;
  }

  // Cascade-on-open: fire the same domino-in animation the sidebar
   // section uses. Only on the FIRST grid render after the library is
   // opened — subsequent re-renders (filter/sort/search) need to be
   // instant.
  if (state._libJustOpened) {
    grid.classList.add('email-lib-just-opened');
    state._libJustOpened = false;
    // Strip the class after the cascade so it doesn't restrict later
    // animations (e.g. the FLIP reflow when archiving). Worst-case
    // duration matches the longest delay in the keyframe set below.
    setTimeout(() => grid.classList.remove('email-lib-just-opened'), 900);
  }
  let lastDateLabel = null;
  const timelineGapThreshold = _emailTimelineGapThreshold(filtered);
  let previousEmailTime = null;
  for (const em of filtered) {
    const dateLabel = _emailDateBucketLabel(em?.date);
    if (dateLabel !== lastDateLabel) {
      const emailTime = new Date(em?.date).getTime();
      const gapDays = Number.isFinite(previousEmailTime) && Number.isFinite(emailTime)
        ? Math.abs(previousEmailTime - emailTime) / 86400000
        : 0;
      const timelineBreak = gapDays > 90 && gapDays > timelineGapThreshold;
      grid.appendChild(_createEmailDateHeader(dateLabel, timelineBreak));
      lastDateLabel = dateLabel;
    }
    grid.appendChild(_createCard(em));
    const emailTime = new Date(em?.date).getTime();
    if (Number.isFinite(emailTime)) previousEmailTime = emailTime;
  }
  _appendEmailSearchProgressRow(grid);

  // If a deep-link asked us to expand a specific email, do it when the card
  // exists. Keep the UID through cached paints so a later fresh load can
  // still expand a newly-sent message that was absent from the cache.
  if (state._libPendingExpandUid) {
    const target = filtered.find(e => String(e.uid) === String(state._libPendingExpandUid));
    const wantUid = state._libPendingExpandUid;
    if (target) {
      const cards = grid.querySelectorAll('.doclib-card');
      const targetCard = Array.from(cards).find(c => c.dataset.uid === String(wantUid));
      if (targetCard) {
        state._libPendingExpandUid = null;
        requestAnimationFrame(() => _toggleCardPreview(targetCard, target));
      }
    }
  }
}

function _createCard(em) {
  _normalizeEmailStateFlags(em);
  const card = document.createElement('div');
  let cls = 'doclib-card memory-item';
  if (em.is_answered) cls += ' email-card-answered';
  else if (!em.is_read) cls += ' email-card-unread';
  card.className = cls;
  card.dataset.uid = String(em.uid);
  card.dataset.emailAccount = String(em.account_id || state._libAccountId || '');
  const cardFolder = _hasActiveEmailSearchResults()
    ? (em.folder || state._libFolder || 'INBOX')
    : (state._libFolder || em.folder || 'INBOX');
  card.dataset.emailFolder = String(cardFolder);
  if (state._selectMode && state._selectedUids.has(em.uid)) card.classList.add('selected');

  // Checkbox in select mode
  if (state._selectMode) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'memory-select-cb';
    cb.checked = state._selectedUids.has(em.uid);
    cb.addEventListener('click', e => e.stopPropagation());
    cb.addEventListener('change', () => {
      if (cb.checked) state._selectedUids.add(em.uid);
      else state._selectedUids.delete(em.uid);
      card.classList.toggle('selected', cb.checked);
      _updateBulkBar();
    });
    card.appendChild(cb);
  }

  // In Sent results, show the recipient(s) — the sender is always you and
  // hides the actually useful info. Search results can be stamped with their
  // real folder while the visible folder selector still says INBOX, so the
  // selected mailbox remains authoritative for normal folder views.
  const isSentFolderEarly = /sent/i.test(cardFolder);
  let senderName;
  let senderAddress;
  if (isSentFolderEarly) {
    senderName = _formatRecipients(em.to) || em.to || '(no recipient)';
    // First address out of em.to for click-to-pill targeting.
    const _firstTo = String(em.to || '').split(',')[0] || '';
    const _m = _firstTo.match(/<([^>]+)>/);
    senderAddress = (_m ? _m[1] : _firstTo).trim();
  } else {
    senderName = em.from_name || em.from_address;
    senderAddress = em.from_address || '';
  }
  const color = _senderColor(senderName);

  let dateStr = '';
  if (em.date) {
    try {
      const d = new Date(em.date);
      const now = new Date();
      const sameYear = d.getFullYear() === now.getFullYear();
      const dateOpts = sameYear
        ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
        : { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
      dateStr = d.toLocaleString([], dateOpts);
    } catch (_) {}
  }

  const content = document.createElement('div');
  content.style.cssText = 'flex:1;min-width:0;';

  const titleRow = document.createElement('div');
  titleRow.className = 'email-card-titlerow';
  titleRow.style.cssText = 'display:flex;align-items:center;gap:6px;';

  const titleEl = document.createElement('span');
  titleEl.className = 'memory-item-title';
  titleEl.textContent = em.subject || '(no subject)';
  // Hover preview: surface the cached AI summary directly on the title via
  // a native browser tooltip — no need to open the email to skim it.
  if (em.cached_summary) {
    titleEl.title = em.cached_summary;
    titleEl.classList.add('email-card-has-summary');
  }
  titleRow.appendChild(titleEl);
  const cardChevron = document.createElement('span');
  cardChevron.className = 'doclib-card-chevron';
  cardChevron.setAttribute('aria-hidden', 'true');
  cardChevron.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

  const isSentFolder = /sent/i.test(cardFolder);
  const statusCluster = document.createElement('span');
  statusCluster.className = 'email-card-status';
  statusCluster.setAttribute('aria-label', 'Email status');

  if (em.has_attachments) {
    const att = document.createElement('span');
    att.className = 'email-card-attachment';
    att.title = 'Has attachments';
    att.style.cssText = 'opacity:0.6;flex-shrink:0;display:inline-flex;';
    att.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
    statusCluster.appendChild(att);
  }

  const tags = state._libShowTags ? _visibleEmailTagsForRender(em) : [];
  if (state._libShowTags && (tags.length || em.is_spam_verdict)) {
    const tagWrap = document.createElement('span');
    tagWrap.className = 'email-tags email-card-tags' + (tags.length > 2 ? ' email-tags-collapsed' : '');
    tagWrap.innerHTML = _emailTagGroupHtml(tags, em);
    if (em.is_spam_verdict) {
      tagWrap.insertAdjacentHTML('beforeend', '<span class="email-tag email-tag-spam">spam</span>');
    }
    let moreBtn = tagWrap.querySelector('[data-email-tags-more]');
    if (moreBtn) {
      const tintSource = tagWrap.querySelector('.email-tag');
      requestAnimationFrame(() => {
        if (tintSource?.isConnected) moreBtn.style.setProperty('--email-tags-more-color', getComputedStyle(tintSource).color);
      });
    }
    tagWrap.addEventListener('click', (ev) => {
      if (state._selectMode) return;
      const calBtn = ev.target.closest('[data-calendar-event-uid]');
      const tagBtn = ev.target.closest('[data-email-filter-tag]');
      const moreBtn = ev.target.closest('[data-email-tags-more]');
      if (!calBtn && !tagBtn && !moreBtn) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (moreBtn) {
        const expanded = tagWrap.classList.toggle('email-tags-expanded');
        moreBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        const pills = tagWrap.querySelectorAll(':scope > .email-tag, :scope > .email-tag-extra');
        if (expanded) {
          tagWrap.style.flexWrap = 'wrap';
          pills.forEach(pill => { pill.style.display = 'inline-flex'; });
          moreBtn.style.display = 'inline-flex';
        } else {
          tagWrap.style.flexWrap = '';
          pills.forEach(pill => { pill.style.display = ''; });
          moreBtn.style.display = '';
          requestAnimationFrame(() => _fitEmailCardTags(titleRow));
        }
      } else if (calBtn) _openCalendarEventFromEmail(calBtn.dataset.calendarEventUid);
      else _applyTagFilterFromPill(tagBtn.dataset.emailFilterTag);
    });
    titleRow.appendChild(tagWrap);
  }

  // Keep the status controls in one right-aligned cluster. Favorite is always
  // present as an outline so it is discoverable before activation.
  if (!isSentFolder) {
    const doneCheck = document.createElement('span');
    doneCheck.className = 'email-card-done' + (em.is_answered ? ' active' : '');
    doneCheck.title = em.is_answered ? 'Mark not done' : 'Mark done';
    doneCheck.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    const _toggleDone = async (e) => {
      if (e) e.stopPropagation();
      // Use the visible class as source of truth — em.is_answered could
      // be stale from a background sync, which would leave the user
      // clicking and seeing no UI change.
      const wasActive = doneCheck.classList.contains('active');
      const newState = !wasActive;
      em.is_answered = newState;
      doneCheck.classList.toggle('active', newState);
      doneCheck.title = newState ? 'Mark not done' : 'Mark done';
      // Animate in both directions so the user gets explicit feedback when
      // un-checking too — without this the hover state and the active state
      // look identical, so the click felt like a no-op.
      doneCheck.classList.remove('just-checked', 'just-unchecked');
      void doneCheck.offsetWidth; // restart animation
      doneCheck.classList.add(newState ? 'just-checked' : 'just-unchecked');
      setTimeout(() => doneCheck.classList.remove('just-checked', 'just-unchecked'), 500);
      if (newState) {
        _clearDoneResponseTagsLocal(em);
        titleRow.querySelectorAll('.email-tag-urgent, .email-tag-reply-soon, .email-tag-action-needed').forEach(n => n.remove());
        _syncEmailReadState(em.uid, true);
      }
      try {
        if (newState) {
          await fetch(`${API_BASE}/api/email/mark-answered/${em.uid}?folder=${encodeURIComponent(cardFolder)}${_acct()}`, { method: 'POST' });
          await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(cardFolder)}${_acct()}`, { method: 'POST' });
        } else {
          await fetch(`${API_BASE}/api/email/clear-answered/${em.uid}?folder=${encodeURIComponent(cardFolder)}${_acct()}`, { method: 'POST' });
        }
      } catch (err) { console.error(err); }
    };
    doneCheck.addEventListener('click', _toggleDone);
    statusCluster.appendChild(doneCheck);
  }

  const favoriteToggle = document.createElement('button');
  favoriteToggle.type = 'button';
  favoriteToggle.className = 'email-card-favorite' + (em.is_flagged ? ' active' : '');
  favoriteToggle.title = em.is_flagged ? 'Unfavorite' : 'Favorite';
  favoriteToggle.setAttribute('aria-label', favoriteToggle.title);
  favoriteToggle.setAttribute('aria-pressed', em.is_flagged ? 'true' : 'false');
  favoriteToggle.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="' + (em.is_flagged ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
  favoriteToggle.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const next = !em.is_flagged;
    const favoritesView = state._libFilter === 'favorites';
    const previousIndex = state._libEmails.findIndex(item => item === em);
    em.is_flagged = next;
    // The Favorites mailbox is already a server-filtered snapshot. Remove an
    // unfavorited row locally so the click has immediate, visible feedback.
    if (favoritesView && !next) {
      state._libEmails = state._libEmails.filter(item => item !== em);
    }
    _renderGrid();
    try {
      const res = await fetch(`${API_BASE}/api/email/flag/${em.uid}?folder=${encodeURIComponent(cardFolder)}${_acct()}&on=${next ? 'true' : 'false'}`, { method: 'POST' });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      _libCacheWriteBack();
    } catch (err) {
      em.is_flagged = !next;
      if (favoritesView && !next && previousIndex >= 0) {
        state._libEmails.splice(previousIndex, 0, em);
      }
      _renderGrid();
      console.error('Failed to toggle favorite:', err);
    }
  });
  const doneControl = statusCluster.querySelector('.email-card-done');
  if (doneControl) statusCluster.insertBefore(favoriteToggle, doneControl);
  else statusCluster.appendChild(favoriteToggle);

  if (!isSentFolder && !em.is_read) {
    const dot = document.createElement('span');
    dot.className = 'email-card-unread-dot';
    dot.style.cssText = `width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;margin-left:2px;`;
    statusCluster.appendChild(dot);
  }
  titleRow.appendChild(statusCluster);
  requestAnimationFrame(() => _fitEmailCardTags(titleRow));
  _emailTagFitObserver?.observe(titleRow);

  // Prev/next arrows — visible only when this card is the expanded one
  // (CSS-gated so collapsed cards stay clean). Click navigates by collapsing
  // this card and expanding the neighbour.
  const navArrows = document.createElement('span');
  navArrows.className = 'email-card-nav-arrows';
  navArrows.innerHTML = `
    <button type="button" class="email-card-nav-btn" data-nav-dir="-1" title="Previous email"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
    <button type="button" class="email-card-nav-btn" data-nav-dir="1" title="Next email"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
  `;
  navArrows.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.email-card-nav-btn');
    if (!btn || btn.disabled) return;
    ev.stopPropagation();
    const card = navArrows.closest('.doclib-card');
    if (!card) return;
    const dir = parseInt(btn.dataset.navDir, 10);
    const sibling = _findSiblingEmailCard(card, dir);
    if (!sibling) return;
    const nextEm = state._libEmails.find(e => String(e.uid) === String(sibling.dataset.uid));
    if (!nextEm) return;
    await _toggleCardPreview(card, em);
    await _toggleCardPreview(sibling, nextEm);
    sibling.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  // Keep navigation in the same status row as Favorite and Done. The
  // per-card `.memory-item-actions` menu
  // at the bottom of the card stays visible while expanded (see the CSS
  // override below), so duplicating it in the header was redundant.
  statusCluster.appendChild(navArrows);

  content.appendChild(titleRow);

  const meta = document.createElement('div');
  meta.className = 'memory-item-meta';
  meta.style.cssText = 'font-size:10px;opacity:0.7;margin-top:2px;';
  const showFolderChip = !!(_libSearchHadResults && cardFolder);
  const prettyFolder = folderDisplayName(cardFolder);
  const sentChip = isSentFolderEarly ? '<span class="email-sent-chip" title="Sent email">Sent</span>' : '';
  const folderChip = showFolderChip && !isSentFolderEarly
    ? `<span class="email-folder-chip" title="${_esc(cardFolder)}">${_esc(prettyFolder)}</span>`
    : '';
  const senderPrefix = isSentFolderEarly ? 'to ' : '';
  meta.innerHTML = `${sentChip}<span class="email-meta-sender" data-email="${_esc(senderAddress || '')}" data-name="${_esc(senderName || '')}"><span style="opacity:0.55">${senderPrefix}</span><span style="color:${color};font-weight:600">${_esc(senderName)}</span></span><span class="email-meta-sep"> · </span><span class="email-meta-date-group"><span class="email-meta-date">${_esc(dateStr)}</span>${folderChip}</span>`;
  content.appendChild(meta);

  card.appendChild(content);

  // Per-card menu button (... menu)
  if (!state._selectMode) {
    const actionsWrap = document.createElement('div');
    actionsWrap.className = 'memory-item-actions';
    const menuBtn = document.createElement('button');
    menuBtn.className = 'memory-item-btn';
    menuBtn.title = 'Actions';
    menuBtn.style.position = 'relative';
    menuBtn.style.top = '-1px';
    menuBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _showCardMenu(em, menuBtn);
    });
    actionsWrap.appendChild(menuBtn);
    card.appendChild(actionsWrap);
    card.appendChild(cardChevron);

    // Long-press anywhere on the row opens the same actions menu — matches
    // the chats / archive / research / documents tabs' long-press UX.
    let _hold = null, _holdStart = null;
    const _cancelHold = () => { if (_hold) { clearTimeout(_hold); _hold = null; } _holdStart = null; };
    card.addEventListener('pointerdown', (e) => {
      if (card.classList.contains('email-card-expanded') || card.classList.contains('doclib-card-expanded')) return;
      if (e.target.closest('button, .email-card-done, .recipient-chip, .memory-select-cb, .email-card-nav-btn')) return;
      _holdStart = { x: e.clientX, y: e.clientY };
      _hold = setTimeout(() => {
        _hold = null;
        if (card.classList.contains('email-card-expanded') || card.classList.contains('doclib-card-expanded')) return;
        card._suppressNextClick = true;
        setTimeout(() => { card._suppressNextClick = false; }, 400);
        if (navigator.vibrate) try { navigator.vibrate(15); } catch {}
        if (window.innerWidth <= 768 && ('ontouchstart' in window || navigator.maxTouchPoints > 0)) {
          const selectBtn = document.getElementById('email-lib-select-btn');
          if (selectBtn) {
            if (!selectBtn.classList.contains('active')) selectBtn.click();
            setTimeout(() => document.querySelector(`#email-lib-grid .doclib-card[data-uid="${CSS.escape(String(em.uid))}"] .memory-select-cb`)?.click(), 40);
            return;
          }
        }
        _showCardMenu(em, menuBtn);
      }, 500);
    });
    card.addEventListener('pointermove', (e) => {
      if (!_holdStart) return;
      if (Math.hypot(e.clientX - _holdStart.x, e.clientY - _holdStart.y) > 10) _cancelHold();
    });
    card.addEventListener('pointerup', _cancelHold);
    card.addEventListener('pointercancel', _cancelHold);
  }

  // Click handler — toggle preview expansion
  card.addEventListener('click', async (e) => {
    if (card._suppressNextClick) { card._suppressNextClick = false; return; }
    if (state._selectMode) {
      if (state._selectedUids.has(em.uid)) state._selectedUids.delete(em.uid);
      else state._selectedUids.add(em.uid);
      card.classList.toggle('selected', state._selectedUids.has(em.uid));
      const cb = card.querySelector('.memory-select-cb');
      if (cb) cb.checked = state._selectedUids.has(em.uid);
      _updateBulkBar();
      return;
    }
    if (e.shiftKey) {
      e.preventDefault();
      await _openEmailAsTab(em, cardFolder);
      return;
    }
    if (/draft/i.test(String(state._libFolder || '')) && state._docModule?.openEmailDraft) {
      try {
        await _openDraftInComposer(em);
      } catch (err) {
        console.error('Failed to open email draft:', err);
        showToast(`Could not open draft: ${err.message || err}`);
      }
      return;
    }
    await _toggleCardPreview(card, em);
  });

  return card;
}

export function _findSiblingEmailCard(card, dir) {
  const grid = card.closest('.doclib-grid');
  if (!grid) return null;
  const cards = [...grid.querySelectorAll('.doclib-card[data-uid]')];
  const idx = cards.indexOf(card);
  if (idx === -1) return null;
  return cards[idx + dir] || null;
}

function _syncCardNavArrows(card) {
  const prev = card.querySelector('.email-card-nav-btn[data-nav-dir="-1"]');
  const next = card.querySelector('.email-card-nav-btn[data-nav-dir="1"]');
  if (prev) prev.disabled = !_findSiblingEmailCard(card, -1);
  if (next) next.disabled = !_findSiblingEmailCard(card, 1);
}

async function _openDraftInComposer(em) {
  if (!state._docModule?.openEmailDraft) return false;
  const folder = em?.folder || state._libFolder || 'Drafts';
  const account = state._libAccountId ? `&account_id=${encodeURIComponent(state._libAccountId)}` : '';
  const res = await fetch(`${API_BASE}/api/email/read/${encodeURIComponent(em.uid)}?folder=${encodeURIComponent(folder)}${account}&mark_seen=false`);
  if (!res.ok) throw new Error(`Draft load failed: HTTP ${res.status}`);
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  await state._docModule.openEmailDraft({ ...em, ...data });
  return true;
}

const _emailReadPrefetching = new Set();
let _emailReadPrefetchTimer = null;

function _prefetchAdjacentEmails(card, count = 1) {
  if (!card || state._libFolder === '__scheduled__') return;
  const grid = card.closest('.doclib-grid');
  if (!grid) return;
  const cards = [...grid.querySelectorAll('.doclib-card[data-uid]')];
  const idx = cards.indexOf(card);
  if (idx === -1) return;
  const targets = [];
  for (let i = 1; i <= count; i++) {
    if (cards[idx + i]) targets.push(cards[idx + i]);
  }
  if (targets.length < count) {
    for (let i = 1; targets.length < count && cards[idx - i]; i++) targets.push(cards[idx - i]);
  }
  const target = targets.find(t => t?.dataset?.uid);
  const uid = target?.dataset?.uid;
  if (!uid) return;
  // Use the email's actual folder when it was stamped by the search
  // endpoint; otherwise default to the currently-selected folder.
  const _emFold = (() => {
    const emObj = (state._libEmails || []).find(e => String(e.uid) === String(uid));
    return (emObj && emObj.folder) || state._libFolder || 'INBOX';
  })();
  const key = `${state._libAccountId || ''}|${_emFold}|${uid}`;
  if (_emailReadPrefetching.has(key) || _emailReadPrefetching.size > 0) return;
  if (_emailReadPrefetchTimer) clearTimeout(_emailReadPrefetchTimer);
  _emailReadPrefetchTimer = setTimeout(() => {
    _emailReadPrefetchTimer = null;
    if (document.hidden) return;
    _emailReadPrefetching.add(key);
    fetch(`${API_BASE}/api/email/read/${encodeURIComponent(uid)}?folder=${encodeURIComponent(_emFold)}${_acct()}&mark_seen=false`)
      .catch(() => {})
      .finally(() => _emailReadPrefetching.delete(key));
  }, 2500);
}

export async function _toggleCardPreview(card, em) {
  _normalizeEmailStateFlags(em);
  const accountAtStart = state._libAccountId || '';
  const libraryFolderAtStart = state._libFolder || 'INBOX';
  // Prefer the per-email folder stamped by the search endpoint (results
  // from "All Mail" carry folder="[Gmail]/All Mail"). Falls back to the
  // currently-selected folder for normal inbox cards.
  const folderAtStart = _hasActiveEmailSearchResults()
    ? ((em && em.folder) || libraryFolderAtStart)
    : libraryFolderAtStart;
  const uidAtStart = String(em?.uid || card?.dataset?.uid || '');
  const wasReadAtStart = !!em?.is_read;
  const openGeneration = ++_emailCardOpenSeq;
  const readContext = Object.freeze({
    accountId: String(accountAtStart),
    libraryFolder: String(libraryFolderAtStart),
    folder: String(folderAtStart),
    uid: uidAtStart,
    mailboxGeneration: _emailMailboxGeneration,
  });
  const readContextKey = _emailReadContextKey(readContext);
  const isCurrentOpen = () => (
    openGeneration === _emailCardOpenSeq &&
    _emailReadContextIsCurrent(readContext) &&
    accountAtStart === (state._libAccountId || '') &&
    libraryFolderAtStart === (state._libFolder || 'INBOX') &&
    uidAtStart === String(card?.dataset?.uid || '') &&
    card.isConnected &&
    card.classList.contains('email-card-expanded')
  );
  const grid = card.closest('.doclib-grid');
  const gridRect = grid?.getBoundingClientRect?.();
  const modal = document.getElementById('email-lib-modal');
  const modalContent = card.closest('.modal-content');
  const modalRect = modalContent?.getBoundingClientRect?.();
  const currentRect = card.getBoundingClientRect();
  const stableOpenHeight = Math.max(
    currentRect.height || 0,
    (modalRect?.height || 0) - 84,
    Math.min(Math.max(260, window.innerHeight * 0.56), gridRect?.height || window.innerHeight)
  );

  // Already expanded — collapse
  if (card.classList.contains('email-card-expanded')) {
    unbindExpandedCardDismiss(card);
    card.classList.remove('email-card-expanded');
    card.classList.remove('doclib-card-expanded');
    card.style.minHeight = '';
    modal?.classList.remove('email-reading');
    modal?.style.removeProperty('--email-reading-modal-min-h');
    const reader = card.querySelector('.email-card-reader');
    if (reader) reader.remove();
    return;
  }

  // Every authoritative open supersedes any older optimistic mutation for the
  // same immutable mailbox identity. Carry the original unread state forward
  // so a close/reopen followed by failure still rolls back exactly once, while
  // a late failure from the superseded request cannot undo a newer success.
  const previousMutation = _emailReadMutations.get(readContextKey);
  const readMutation = {
    generation: ++_emailReadMutationSeq,
    rollbackUnread: !wasReadAtStart || !!previousMutation?.rollbackUnread,
  };
  _emailReadMutations.set(readContextKey, readMutation);
  const restoreUnreadState = () => {
    if (_emailReadMutations.get(readContextKey)?.generation !== readMutation.generation) return;
    _emailReadMutations.delete(readContextKey);
    if (readMutation.rollbackUnread) _syncEmailReadState(uidAtStart, false, readContext);
  };
  const commitReadState = () => {
    // A successful STORE/mark_seen is authoritative for this immutable
    // mailbox identity even when a newer open is still pending. Retire that
    // newer rollback token too, otherwise its later failure could restore an
    // unread state that no longer exists at the provider.
    _emailReadMutations.delete(readContextKey);
    _syncEmailReadState(uidAtStart, true, readContext);
  };

  // Collapse any other expanded card
  if (grid) {
    grid.querySelectorAll('.email-card-expanded').forEach(c => {
      unbindExpandedCardDismiss(c);
      c.classList.remove('email-card-expanded');
      c.classList.remove('doclib-card-expanded');
      c.style.minHeight = '';
      const r = c.querySelector('.email-card-reader');
      if (r) r.remove();
    });
  }

  card.classList.add('email-card-expanded');
  card.classList.add('doclib-card-expanded');
  bindExpandedCardDismiss(card, () => _toggleCardPreview(card, em));
  card.style.minHeight = `${Math.round(stableOpenHeight)}px`;
  // Pull the card into view in case the user clicked an email further up
  // the list whose top is partially scrolled off the viewport. Wait for
  // the layout to settle (minHeight just changed) before scrolling so
  // the browser scrolls toward the post-expansion position.
  requestAnimationFrame(() => {
    try { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
  });
  if (!wasReadAtStart) {
    // Keep the current optimistic visual update, but let the read request below
    // own the provider-side \Seen transition. A failure restores unread state.
    _syncEmailReadState(uidAtStart, true, readContext);
  }
  // Class hook on the modal so the header-hide / padding rules work on
  // browsers without :has() support (Firefox mobile) — the :has() versions
  // below stay as the desktop path.
  if (modal && modalRect?.height) {
    modal.style.setProperty('--email-reading-modal-min-h', `${Math.round(modalRect.height)}px`);
  }
  modal?.classList.add('email-reading');

  // Show loading reader with whirlpool spinner
  const reader = document.createElement('div');
  reader.className = 'email-card-reader email-card-reader-loading';
  reader.style.minHeight = `${Math.max(180, Math.round(stableOpenHeight - 70))}px`;
  reader.innerHTML = _emailReaderSkeletonHtml();
  card.appendChild(reader);
  _markEmailReaderActive(reader);
  const showFailedReader = (message) => {
    try {
      _showEmailReaderLoadError(reader, message, () => {
        try { reader.remove(); } catch (_) {}
        card.classList.remove('email-card-expanded', 'doclib-card-expanded');
        card.style.minHeight = '';
        setTimeout(() => { _toggleCardPreview(card, em); }, 0);
      });
      showToast(message || 'Failed to load email');
    } catch (_) {}
  };

  let authoritativeReadSucceeded = false;
  try {
    const accountQueryAtStart = accountAtStart ? `&account_id=${encodeURIComponent(accountAtStart)}` : '';
    const res = await fetch(`${API_BASE}/api/email/read/${encodeURIComponent(uidAtStart)}?folder=${encodeURIComponent(folderAtStart)}${accountQueryAtStart}&mark_seen=true`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    let data = await res.json();
    if (data.error) {
      restoreUnreadState();
      if (isCurrentOpen()) showFailedReader(`Failed to load email: ${data.error}`);
      return;
    }
    data = _normalizeEmailStateFlags({ ...em, ...data });
    Object.assign(em, data);
    _syncEmailDoneState(uidAtStart, data.is_answered, readContext);
    if (data.mark_seen_failed) {
      // The body is authoritative even when the provider refused the \Seen
      // transition. Render the message and roll the unread marker back so the
      // list keeps telling the truth, rather than refusing to open a message
      // we successfully read.
      restoreUnreadState();
    } else {
      authoritativeReadSucceeded = true;
      commitReadState();
    }
    if (!isCurrentOpen()) return;
    _stampReaderContext(reader, { ...em, ...data }, state._libFolder, state._libAccountId);

    // Build the attachments wrap using the shared helper so the signature-
    // image filter (small inline PNGs/JPGs, Outlook image001 placeholders,
    // logo/banner files) is applied here too. Falls back to '' when every
    // attachment is filtered out.
    const attsHtml = _buildAttsHtmlFor(em.uid, data);

    // Format date nicely (compact): "Mar 21, 2026 14:32"
    let dateDisplay = data.date || '';
    try {
      if (data.date) {
        const d = new Date(data.date);
        if (!isNaN(d.getTime())) {
          dateDisplay = d.toLocaleString([], {
            month: 'short', day: 'numeric', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
          });
        }
      }
    } catch (_) {}

    // Build recipient chip group from a comma-separated address list
    const buildRecipients = (str) => {
      if (!str) return '';
      const addrs = _splitRecipientList(str);
      if (addrs.length === 0) return '';
      return addrs.map(a => {
        const name = _extractName(a);
        return _recipientChipHtml(a, name);
      }).join('');
    };

    // Build the From chip too — single chip with name, click reveals address
    const fromChip = _recipientChipHtml(`${data.from_name || ''} <${data.from_address || ''}>`, data.from_name || data.from_address, 'from-chip');

    reader.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row email-reader-meta-from">
            <strong>From:</strong>
            <span class="recipient-chips">${fromChip}${_recipientMetaToggleHtml(data)}</span>
          </div>
          ${(data.to || data.cc) ? `<div class="email-reader-meta-details" hidden>
            ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${buildRecipients(data.to)}</span></div>` : ''}
            ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${buildRecipients(data.cc)}</span></div>` : ''}
          </div>` : ''}
          <div class="email-reader-actions-inline">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="ai-reply" title="${data.cached_ai_reply ? 'AI Reply (cached draft ready)' : 'AI Reply (suggest a draft)'}">${_aiReplyIcon(data)}<span class="reader-btn-label">AI reply</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="reply" title="Reply"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg><span class="reader-btn-label">Reply</span></button>
            ${_hasMultipleRecipients(data) ? `<button class="memory-toolbar-btn reader-icon-btn" data-act="reply-all" title="Reply All"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 17 2 12 7 7"/><polyline points="12 17 7 12 12 7"/><path d="M22 18v-2a4 4 0 0 0-4-4H7"/></svg><span class="reader-btn-label">Reply all</span></button>` : ''}
            <button class="memory-toolbar-btn reader-icon-btn" data-act="forward" title="Forward"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/></svg><span class="reader-btn-label">Forward</span></button>
            <div class="email-reader-more-wrap" style="position:relative">
              <button class="memory-toolbar-btn reader-icon-btn" data-act="more" title="More actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg><span class="reader-btn-label">More</span></button>
            </div>
          </div>
        </div>
      </div>
      ${attsHtml}
      <div class="email-reader-body${data.body_html ? ' html-body' : ''}">${_safeRenderEmailBody(data)}</div>
    `;
    _markEmailReaderActive(reader);
    reader.classList.remove('email-card-reader-loading');
    reader.style.minHeight = '';

    _wireEmailAttachmentWrap(reader, folderAtStart);
    _wireEmailInlineImages(reader);
    _loadDeferredAttachmentsIntoReader(reader, em.uid, folderAtStart, data, !!em.has_attachments);
    _maybeAutoTranslateEmail(reader);

    reader.querySelector('[data-act="reply"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      _snapEmailModalToLeftSidebar(ev.currentTarget.closest('.modal'));
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'reply' });
    });
    reader.querySelector('[data-act="reply-all"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      _snapEmailModalToLeftSidebar(ev.currentTarget.closest('.modal'));
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'reply-all' });
    });
    reader.querySelector('[data-act="ai-reply"]')?.addEventListener('click', (ev) => _handleAiReplyButton(ev, em, data));
    reader.querySelector('[data-act="forward"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'forward' });
    });
    reader.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _showReaderMoreMenu(em, card, reader, ev.currentTarget, data);
    });
    reader.querySelector('[data-act="summarize"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      await _summarizeEmail(reader, data, ev.currentTarget);
    });
    _wireMetaToggle(reader);
    _wireReaderActionOverflow(reader);
    // Refresh the title-row prev/next arrows for this newly-expanded card.
    _syncCardNavArrows(card);

    // Horizontal swipe on the reader switches to prev/next email — but
    // only when the underlying content can't scroll further in the swipe
    // direction. If the email body is wider than the viewport (HTML emails
    // with tables, embedded images), normal horizontal scroll wins; nav
    // only fires once the user has reached an edge.
    {
      let _sx = 0, _sy = 0, _swiping = false, _intent = null;
      let _scrollEl = null;
      let _startScrollLeft = 0;
      const SWIPE_THRESHOLD = 60;
      const VERT_ABORT = 14;
      const findHScroller = (el) => {
        while (el && el !== reader) {
          if (el.scrollWidth - el.clientWidth > 2) return el;
          el = el.parentElement;
        }
        return null;
      };
      reader.addEventListener('touchstart', (ev) => {
        if (ev.touches.length !== 1) { _swiping = false; return; }
        if (ev.target.closest('button, a, .recipient-chip, .email-attachment-chip, .email-reader-more-wrap')) { _swiping = false; return; }
        _sx = ev.touches[0].clientX;
        _sy = ev.touches[0].clientY;
        _scrollEl = findHScroller(ev.target);
        _startScrollLeft = _scrollEl ? _scrollEl.scrollLeft : 0;
        _swiping = true;
        _intent = null;
      }, { passive: true });
      reader.addEventListener('touchmove', (ev) => {
        if (!_swiping) return;
        const dx = ev.touches[0].clientX - _sx;
        const dy = ev.touches[0].clientY - _sy;
        if (!_intent) {
          if (Math.abs(dy) > VERT_ABORT && Math.abs(dy) > Math.abs(dx)) {
            _intent = 'scroll';
            _swiping = false;
            return;
          }
          if (Math.abs(dx) > 12) _intent = 'swipe';
        }
      }, { passive: true });
      reader.addEventListener('touchend', (ev) => {
        if (!_swiping) return;
        _swiping = false;
        const t = (ev.changedTouches && ev.changedTouches[0]) || null;
        if (!t || _intent !== 'swipe') return;
        const dx = t.clientX - _sx;
        const dy = t.clientY - _sy;
        if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dy) > Math.abs(dx)) return;
        // If a horizontally-scrollable element captured the swipe, let it
        // scroll instead of changing email — UNLESS the user was already
        // at the edge (scrollLeft can't move further in that direction).
        if (_scrollEl) {
          const max = _scrollEl.scrollWidth - _scrollEl.clientWidth;
          const atLeftEdge = _scrollEl.scrollLeft <= 2;
          const atRightEdge = _scrollEl.scrollLeft >= max - 2;
          // Swiping LEFT (dx<0) reveals content to the right → if not at
          // right edge, that's a scroll, not a nav.
          if (dx < 0 && !atRightEdge) return;
          // Swiping RIGHT (dx>0) reveals content to the left → if not at
          // left edge, that's a scroll, not a nav.
          if (dx > 0 && !atLeftEdge) return;
          // If the browser already scrolled during this gesture, treat as
          // scroll regardless (the user clearly wanted to pan).
          if (_scrollEl.scrollLeft !== _startScrollLeft) return;
        }
        const dir = dx < 0 ? 1 : -1;
        const navBtn = card.querySelector(`.email-card-nav-btn[data-nav-dir="${dir}"]`);
        if (navBtn && !navBtn.disabled) navBtn.click();
      }, { passive: true });
    }

    // If the email has a pre-cached summary, show it immediately. Fold
    // state is persisted via _summaryCollapsedPref inside the renderer.
    if (data.cached_summary) {
      const sumBtn = reader.querySelector('[data-act="summarize"]');
      _showCachedSummary(reader, data.cached_summary, sumBtn);
    }

    _wireRecipientChips(reader);
    // Always stop bubbling so the card's click doesn't fire while reading.
    reader.addEventListener('click', (ev) => { ev.stopPropagation(); });
  } catch (e) {
    if (!authoritativeReadSucceeded) restoreUnreadState();
    if (isCurrentOpen()) {
      showFailedReader(e?.message ? `Failed to load email: ${e.message}` : 'Failed to load email');
    }
  }
}


// Global preference: AI summary panels stay collapsed across every email
// once the user folds one, and stay expanded once they unfold. Stored in
// localStorage so the choice survives reloads.
const _SUMMARY_COLLAPSED_KEY = 'odysseus.email.summaryCollapsed';
export function _summaryCollapsedPref() {
  try { return localStorage.getItem(_SUMMARY_COLLAPSED_KEY) === '1'; } catch { return false; }
}
export function _setSummaryCollapsedPref(v) {
  try { localStorage.setItem(_SUMMARY_COLLAPSED_KEY, v ? '1' : '0'); } catch {}
}

function _showCachedSummary(reader, summary, btn) {
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;
  if (body.querySelector('.email-summary-panel')) return;
  const panel = document.createElement('div');
  panel.className = 'email-summary-panel';
  if (_summaryCollapsedPref()) panel.classList.add('collapsed');
  panel.innerHTML =
    '<div class="email-summary-header email-summary-toggle" role="button" tabindex="0">'
    +   '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>'
    +   '<span>Summary</span>'
    +   '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    + '</div>'
    + '<div class="email-summary-content"></div>';
  panel.querySelector('.email-summary-content').textContent = summary;
  body.insertBefore(panel, body.firstChild);
  const toggle = panel.querySelector('.email-summary-toggle');
  // Header click folds/unfolds. Persists so the next email opens in the
  // same state.
  const _flip = () => {
    panel.classList.toggle('collapsed');
    _setSummaryCollapsedPref(panel.classList.contains('collapsed'));
  };
  if (toggle) {
    toggle.addEventListener('click', (ev) => { ev.stopPropagation(); _flip(); });
    toggle.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); _flip(); }
    });
  }
  if (btn) {
    btn.classList.add('active');
    const label = btn.querySelector('.btn-label');
    if (label) label.textContent = 'Summary';
  }
}


export async function _translateEmail(reader, language, opts = {}) {
  const body = reader?.querySelector?.('.email-reader-body');
  if (!body) return;
  const existing = body.querySelector('.email-translation-panel');
  if (existing) {
    if (opts.auto && !opts.force) return;
    existing.remove();
  }
  const targetLanguage = language || 'English';
  const sourceText = _emailBodyTextForTranslate(reader);
  if (!sourceText) {
    try { uiModule?.showError?.('No email body to translate'); } catch {}
    return;
  }

  body.querySelectorAll('.email-translation-panel').forEach(p => p.remove());
  const panel = document.createElement('div');
  panel.className = 'email-summary-panel email-translation-panel';
  panel.innerHTML =
    '<div class="email-summary-header email-summary-toggle" role="button" tabindex="0">'
    +   '<svg class="email-translation-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/></svg>'
    +   `<span>Translation · ${_esc(targetLanguage)}</span>`
    +   '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    + '</div>'
    + '<div class="email-summary-content email-translation-loading"><span class="email-translation-busy"><span class="email-translation-spinner"></span><span class="email-translation-loading-text">Translating...</span></span></div>';
  body.insertBefore(panel, body.firstChild);
  const translationToggle = panel.querySelector('.email-summary-toggle');
  if (translationToggle) {
    const flip = () => panel.classList.toggle('collapsed');
    translationToggle.addEventListener('click', (ev) => { ev.stopPropagation(); flip(); });
    translationToggle.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); flip(); }
    });
  }

  const content = panel.querySelector('.email-summary-content');
  const sp = spinnerModule.createWhirlpool(18);
  content.querySelector('.email-translation-spinner')?.appendChild(sp.element);
  try {
    const res = await fetch(`${API_BASE}/api/email/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body: sourceText,
        subject: reader.dataset.emailSubject || '',
        from: reader.dataset.emailFrom || '',
        target_language: targetLanguage,
        auto: !!opts.auto,
      }),
    });
    const result = await res.json().catch(() => ({}));
    sp.destroy();
    content.innerHTML = '';
    if (res.ok && result.success && result.same_language) {
      panel.remove();
    } else if (res.ok && result.success && result.translation) {
      content.textContent = String(result.translation || '')
        .replace(/^\s*<<<TRANSLATION>>>\s*/i, '')
        .replace(/\s*<<<END>>>\s*$/i, '')
        .trim();
    } else {
      panel.remove();
      try { uiModule?.showError?.(result.error || 'Failed to translate'); } catch {}
    }
  } catch (_) {
    sp.destroy();
    panel.remove();
    try { uiModule?.showError?.('Failed to translate'); } catch {}
  }
}

export async function _maybeAutoTranslateEmail(reader) {
  if (reader) reader.dataset.autoTranslateChecked = '1';
}

// Keep an email ⋮ dropdown inside the viewport: when it would spill past the
// bottom (e.g. an email low on a phone screen), flip it above the anchor if
// there's more room up there, and cap height + scroll if it still overflows.
export function _fitEmailDropdown(dropdown, rect) {
  requestAnimationFrame(() => {
    const margin = 8;
    // Horizontal clamp — keep the dropdown inside the viewport regardless of
    // whether it was anchored via left or right. Needed now that some
    // triggers (e.g. the right-aligned bulk "Actions" button) sit close to
    // the right edge, where a left-anchored menu would spill off-screen.
    const dw = dropdown.offsetWidth;
    const curLeft = dropdown.getBoundingClientRect().left;
    if (curLeft + dw > window.innerWidth - margin) {
      dropdown.style.left = Math.max(margin, window.innerWidth - margin - dw) + 'px';
      dropdown.style.right = 'auto';
    } else if (curLeft < margin) {
      dropdown.style.left = margin + 'px';
      dropdown.style.right = 'auto';
    }
    // Vertical fit — flip up or cap+scroll if it doesn't fit below.
    const dh = dropdown.offsetHeight;
    const below = window.innerHeight - rect.bottom - margin;
    const above = rect.top - margin;
    if (dh <= below) return;                 // fits below as-is
    if (above > below) {                     // flip upward
      dropdown.style.top = 'auto';
      dropdown.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
      if (dh > above) { dropdown.style.maxHeight = above + 'px'; dropdown.style.overflowY = 'auto'; }
    } else {                                 // keep below, cap + scroll
      dropdown.style.maxHeight = below + 'px';
      dropdown.style.overflowY = 'auto';
    }
  });
}

function _fitReaderActions(meta) {
  const row = meta?.querySelector(':scope > .email-reader-actions-inline');
  if (!row) return;
  const candidates = row.querySelectorAll(':scope > .reader-icon-btn:not([data-act="more"]), :scope > :not(.email-reader-more-wrap) > .reader-icon-btn');
  candidates.forEach(button => button.classList.remove('reader-action-overflowed'));
  row.classList.remove('email-reader-actions-compact');
  const buttonsWidth = Array.from(row.children).reduce((sum, child) => sum + child.getBoundingClientRect().width, 0)
    + Math.max(0, row.children.length - 1) * 4;
  const compact = buttonsWidth > Math.max(190, meta.clientWidth - 150);
  row.classList.toggle('email-reader-actions-compact', compact);
  if (compact) {
    // Keep the two common drafting actions visible on narrow screens. Put
    // the less frequent recipient variants in More first.
    row.querySelectorAll('[data-act="reply-all"], [data-act="forward"]')
      .forEach(button => button.classList.add('reader-action-overflowed'));
  }
}

const _readerActionFitObserver = typeof ResizeObserver === 'function'
  ? new ResizeObserver(entries => {
      for (const entry of entries) _fitReaderActions(entry.target);
    })
  : null;

export function _wireReaderActionOverflow(reader) {
  const meta = reader?.querySelector('.email-reader-meta');
  if (!meta) return;
  requestAnimationFrame(() => _fitReaderActions(meta));
  _readerActionFitObserver?.observe(meta);
}


// _extractName lives in ./emailLibrary/utils.js


// Sanitize untrusted HTML email bodies before injecting via innerHTML.
//
// Denylist sanitizer — has to block every well-known XSS sink:
//   - <script>, <iframe>, <object>, <embed>, <form>, <style>, <link>
//   - SVG entirely (event handlers, <use href="javascript:">, <foreignObject>,
//     <animate>, <set>, etc.). Email clients don't need SVG.
//   - <math> (MathML can carry handlers).
//   - <base href="...">, <meta http-equiv="refresh">, <noscript>, <frame>,
//     <frameset>, <applet>, <portal>.
//   - on* attributes; javascript:/vbscript:/data: URLs in href/src/srcset/
//     formaction/action/background/poster/data attributes.
//   - srcdoc (defensive — iframe is already nuked).
//   - inline `style` declarations containing javascript: or expression().
// _sanitizeHtml / _escLinkify live in ./emailLibrary/utils.js
