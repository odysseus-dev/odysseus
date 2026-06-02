/**
 * emailLibrary.js — Email library popup modal.
 * Similar pattern to documentLibrary.js. Shows emails in a grid with search/filter.
 */

import spinnerModule from './spinner.js';
import { styledConfirm, showToast, emptyStateIcon } from './ui.js';
import { folderDisplayName, sortedFolders } from './emailInbox.js';
import settingsModule from './settings.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import {
  _esc, _escLinkify, _extractName, _parseTurnMeta,
  _formatBubbleDate, _formatRecipients, _senderColor, _initials,
  _sanitizeHtml,
  _TALON_WROTE, _TALON_FROM, _TALON_SENT, _TALON_SUBJ, _TALON_TO,
  _TALON_ORIG_RE, _SIG_BLOAT_MIN_CHARS,
} from './emailLibrary/utils.js';
import {
  _looksLikeSignature, _harvestAttribution, _extractTurnMetaFromBlockquote,
  _foldSummary, _extractQuoteMeta, _peelSigNameLine, _isBloatedSig,
  _tryFoldHintSig, _foldSignature, _SIG_ICON, _QUOTE_ICON,
} from './emailLibrary/signatureFold.js';
import { state } from './emailLibrary/state.js';

const API_BASE = window.location.origin;
let _emailUnreadChipClickWired = false;
let _libLoadSeq = 0;
let _libFolderSeq = 0;

function _syncEmailReadState(uid, isRead = true) {
  if (uid == null) return;
  const uidStr = String(uid);
  const read = !!isRead;
  const match = (state._libEmails || []).find(x => String(x.uid) === uidStr);
  if (match) match.is_read = read;

  document.querySelectorAll('.doclib-card[data-uid="' + CSS.escape(uidStr) + '"]').forEach(card => {
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
    const rightCluster = titleRow.querySelector('.email-card-header-menu')?.parentElement;
    if (done) done.insertAdjacentElement('afterend', dot);
    else if (rightCluster) titleRow.insertBefore(dot, rightCluster);
    else titleRow.appendChild(dot);
  });
}

// When a reply is sent (from the doc editor), the source email is marked
// \Answered server-side and an `email-answered` event fires. Reflect that live
// so the email shows as done without waiting for a manual refresh.
window.addEventListener('email-answered', (e) => {
  const uid = e.detail && e.detail.uid;
  if (uid == null) return;
  const em = (state._libEmails || []).find(x => String(x.uid) === String(uid));
  if (em) { em.is_answered = true; em.is_read = true; }
  _syncEmailReadState(uid, true);
  document.querySelectorAll('.doclib-card[data-uid="' + CSS.escape(String(uid)) + '"]').forEach(card => {
    card.classList.add('email-card-answered');
    card.classList.remove('email-card-unread');
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
  document.getElementById('email-undone-btn')?.classList.remove('active');
  document.getElementById('email-reminder-btn')?.classList.remove('active');
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

function _syncUnreadWindowGlow() {
  document.getElementById('email-lib-modal')?.classList.toggle('email-lib-unread-active', state._libFilter === 'unread');
}

function _syncReminderClearButton() {
  document.getElementById('email-reminders-clear-btn')?.classList.toggle('hidden', state._libFilter !== 'reminders');
}

function _renderAccountsLoading() {
  const strip = document.getElementById('email-lib-accounts');
  if (!strip) return;
  strip.style.display = 'flex';
  strip.innerHTML = '';
  try {
    const wp = spinnerModule.createWhirlpool(14);
    wp.element.classList.add('email-accounts-loading-whirlpool');
    const label = document.createElement('span');
    label.className = 'email-accounts-loading-label';
    label.textContent = 'Accounts';
    strip.appendChild(wp.element);
    strip.appendChild(label);
  } catch (_) {
    strip.textContent = 'Accounts...';
  }
}

function _syncEmailReminderBellVisibility(enabled) {
  const btn = document.getElementById('email-reminder-btn');
  const wrap = document.querySelector('#email-lib-modal .email-search-wrap');
  btn?.classList.toggle('hidden', !enabled);
  wrap?.classList.toggle('email-reminder-bell-hidden', !enabled);
}

async function _loadEmailReminderBellVisibility() {
  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const settings = await res.json();
    _syncEmailReminderBellVisibility(settings.reminder_channel === 'email');
  } catch (_) {
    _syncEmailReminderBellVisibility(false);
  }
}

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

function _hasDesktopRoomForEmailAndDocument(modal) {
  if (window.innerWidth <= 768) return false;
  if (window.innerWidth >= 1100) return true;
  const content = modal?.querySelector?.('.modal-content');
  const rect = content?.getBoundingClientRect?.();
  const isFullscreen = modal?.classList?.contains('email-lib-fullscreen')
    || modal?.classList?.contains('email-window-fullscreen');
  const emailWidth = isFullscreen
    ? Math.min(440, Math.max(360, Math.round(window.innerWidth * 0.30)))
    : Math.max(360, Math.round(rect?.width || 440));
  const docMinWidth = 560;
  const breathingRoom = 72;
  const leftEdge = isFullscreen ? _emailSplitLeftEdge() : Math.max(0, Math.round(rect?.left || _emailSplitLeftEdge()));
  return (window.innerWidth - leftEdge - emailWidth) >= (docMinWidth + breathingRoom);
}

function _prepareEmailWindowForDocument(modal) {
  if (window.innerWidth <= 768) return true;
  if (!modal) return false;
  if (!_hasDesktopRoomForEmailAndDocument(modal)) {
    _clearEmailDocumentSplit();
    return true;
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
  const wasExpanded = !!card?.classList?.contains('doclib-card-expanded');
  const sibling = wasExpanded
    ? (_findSiblingEmailCard(card, +1) || _findSiblingEmailCard(card, -1))
    : null;
  const nextUid = sibling ? sibling.dataset.uid : null;
  try {
    await fetch(`${API_BASE}/api/email/delete/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
  } catch (err) {
    console.error('Failed to delete email:', err);
    showToast('Failed to delete email');
    return;
  }
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

function _animateEmailCardRemoval(uids, opts = {}) {
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

function _acct() {
  return state._libAccountId ? `&account_id=${encodeURIComponent(state._libAccountId)}` : '';
}

// Per-(account, folder, filter, attachments) cache of the most recent
// first-page list response. Lets reopen-after-close paint the previous
// list instantly while the network refresh runs behind it — the modal
// used to wipe its DOM and spinner-from-empty on every open, even when
// the same view was just visible a second ago.
//
// Session-only (lives in module scope, cleared on hard reload). Search
// results and __scheduled__ are deliberately not cached.
const _libListCache = new Map();
const _LIB_CACHE_MAX = 24;
let _libPrewarmTimer = null;
let _libPrewarmPromise = null;
let _libLastPrewarmAt = 0;

function _libCacheKeyFor(accountId, folder, filter, hasAttachments) {
  return [
    accountId || '',
    folder || '',
    filter || '',
    hasAttachments ? 1 : 0,
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
function _libCacheGet(key) { return _libListCache.get(key) || null; }
function _libCachePut(key, value) {
  // Re-insert to bump LRU recency.
  _libListCache.delete(key);
  _libListCache.set(key, value);
  if (_libListCache.size > _LIB_CACHE_MAX) {
    const oldest = _libListCache.keys().next().value;
    _libListCache.delete(oldest);
  }
}

function _resetEmailListForFreshLoad() {
  state._libOffset = 0;
  state._libEmails = [];
  state._libTotal = 0;
  _libLoadSeq += 1;
  const grid = document.getElementById('email-lib-grid');
  if (grid) _renderEmailLoading(grid);
  const stats = document.getElementById('email-lib-stats');
  if (stats) stats.textContent = 'Loading...';
}

function _loadEmailsFresh() {
  _resetEmailListForFreshLoad();
  return _loadEmails({ force: true, useCache: false });
}

export function prewarmEmailLibrary({ delay = 2500 } = {}) {
  if (_libPrewarmTimer || _libPrewarmPromise) return;
  const elapsed = Date.now() - _libLastPrewarmAt;
  if (elapsed >= 0 && elapsed < 60000) return;
  _libPrewarmTimer = setTimeout(() => {
    _libPrewarmTimer = null;
    _libPrewarmPromise = _prewarmDefaultEmailView()
      .catch(() => {})
      .finally(() => { _libPrewarmPromise = null; });
  }, Math.max(0, Number(delay) || 0));
}

async function _prewarmDefaultEmailView() {
  if (state._libOpen) return;
  _libLastPrewarmAt = Date.now();
  const folder = 'INBOX';
  const filter = 'all';
  const accountId = state._libAccountId || '';
  const ck = _libCacheKeyFor(accountId, folder, filter, false);
  if (_libCacheGet(ck)) return;

  // The accounts request is cheap and warms the account strip for first open.
  // Then the list request warms both the client cache and the backend IMAP/read
  // cache. Failure stays silent: no configured mail should not nag on app boot.
  try {
    const accountsRes = await fetch(`${API_BASE}/api/email/accounts`, { credentials: 'same-origin' });
    if (accountsRes.ok) {
      const accountsData = await accountsRes.json().catch(() => ({}));
      if (Array.isArray(accountsData.accounts)) state._libAccounts = accountsData.accounts;
    }
  } catch (_) {}

  const accountQS = accountId ? `&account_id=${encodeURIComponent(accountId)}` : '';
  const res = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folder)}${accountQS}&limit=100&offset=0&filter=${filter}`, {
    credentials: 'same-origin',
  });
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data || data.error) return;
  _libCachePut(ck, { emails: data.emails || [], total: data.total || 0 });
}
function _libCacheWriteBack() {
  // After a local mutation that already updated state._libEmails
  // (delete / archive / bulk), sync the change into the cache so the
  // next reopen doesn't briefly show the pre-mutation state before the
  // refetch wins. Skipped during search (results aren't the real list)
  // and on the scheduled virtual folder.
  if (state._libSearch) return;
  if (state._libFolder === '__scheduled__') return;
  const ck = _libCacheKey();
  if (_libListCache.has(ck)) {
    _libCachePut(ck, { emails: state._libEmails.slice(), total: state._libTotal });
  }
}

// Expose the active account id to other modules (document.js uses this when sending).
// Simple global rather than cross-module import to keep coupling minimal.
function _publishActiveAccount() {
  try { window.__odysseusActiveEmailAccount = state._libAccountId || null; } catch (_) {}
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
  state._onEmailClick = config.onEmailClick;
}

export function isOpen() { return state._libOpen; }

export function openEmailLibrary(opts = {}) {
  // Force-clean any stale state from previous attempts
  const existing = document.getElementById('email-lib-modal');
  if (existing) existing.remove();
  if (state._libEscHandler) {
    document.removeEventListener('keydown', state._libEscHandler, true);
    state._libEscHandler = null;
  }
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
  state._libEmails = [];
  state._libOffset = 0;
  state._libSearch = '';
  state._libFilter = 'all';
  state._libHasAttachments = false;
  // Animate the very first card render with a domino cascade (same as the
  // sidebar section-domino-in keyframe). Reset by _renderGrid after the
  // animation is queued so subsequent filter/sort re-renders are instant.
  state._libJustOpened = true;
  if (Object.prototype.hasOwnProperty.call(opts, 'account_id')) {
    state._libAccountId = opts.account_id || null;
    _publishActiveAccount();
  }
  if (opts.folder) state._libFolder = opts.folder;
  state._libPendingExpandUid = opts.uid || null;

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'email-lib-modal';
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content" style="width:min(720px, 92vw);max-height:85vh;background:var(--bg);">
      <div class="modal-header">
        <h4>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;">
            <rect x="2" y="4" width="20" height="16" rx="2"/>
            <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
          </svg>
          Email
          <span id="email-lib-unread-badge" class="email-lib-unread-badge" role="button" tabindex="0" title="Show unread emails" style="display:none"></span>
          <span id="email-lib-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal;margin-left:8px;position:relative;top:-2px"></span>
        </h4>
        <div class="email-lib-header-actions" style="display:flex;align-items:center;gap:8px;">
          <button class="close-btn" id="email-lib-close">\u2716</button>
        </div>
      </div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:10px;overflow:hidden;">
        <div class="admin-card" style="flex:1;flex-direction:column;display:flex;overflow:hidden;">
          <p class="memory-desc doclib-desc">All emails. Click to open as a document.</p>
          <div class="email-accounts-row">
            <div id="email-lib-accounts" style="display:flex;gap:4px;flex-wrap:wrap;flex:1;"></div>
            <button class="memory-toolbar-btn email-compose-jiggle" id="email-lib-compose-btn">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:3px;"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
              New
            </button>
          </div>
          <div class="memory-toolbar">
            <div class="memory-category-filters">
              <select class="memory-sort-select" id="email-lib-folder" style="flex:1;min-width:0;text-overflow:ellipsis;">
                <option value="INBOX">Inbox</option>
              </select>
              <select class="memory-sort-select" id="email-lib-filter" style="flex:1;min-width:0;">
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
                  <option value="tag:spam">Spam</option>
                  <option value="tag:newsletter">Newsletter</option>
                  <option value="tag:marketing">Marketing</option>
                </optgroup>
              </select>
              <button class="memory-toolbar-btn email-filter-select-btn" id="email-lib-select-btn">Select</button>
              <button class="memory-toolbar-btn email-filter-refresh-btn" id="email-lib-refresh-btn" title="Refresh">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
              </button>
              <button class="memory-toolbar-btn email-reminders-clear-btn hidden" id="email-reminders-clear-btn" title="Permanently delete Odysseus reminder emails">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>
                Clear
              </button>
            </div>
            <div class="email-search-row" style="display:flex;gap:6px;align-items:flex-start;">
            <div class="email-search-wrap" style="position:relative;flex:1;min-width:140px;">
              <input type="text" id="email-lib-search" placeholder="Search emails\u2026" class="memory-search-input" style="width:100%;padding-right:96px;" />
              <button class="memory-toolbar-btn email-undone-toggle email-undone-toggle-inline" id="email-undone-btn" title="Show only emails not marked as done (undone)">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </button>
              <button class="memory-toolbar-btn email-reminder-toggle-inline hidden" id="email-reminder-btn" title="Show Odysseus reminder emails">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/></svg>
              </button>
              <button class="memory-toolbar-btn email-attach-toggle email-attach-toggle-inline" id="email-attach-btn" title="Show only emails with attachments">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              </button>
            </div>
            </div>
          </div>
          <div id="email-lib-bulk" class="memory-bulk-bar hidden" style="margin-bottom:5px;">
            <label class="memory-bulk-check-all" style="position:relative;top:2px;"><input type="checkbox" id="email-lib-select-all"> All</label>
            <span id="email-lib-selected-count" style="position:relative;top:1px;">0 Selected</span>
            <button class="memory-toolbar-btn" id="email-lib-bulk-actions" style="position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>Actions <span style="opacity:0.55;font-size:9px;">▼</span></button>
            <button class="memory-toolbar-btn" id="email-lib-bulk-delete" style="position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete</button>
            <button class="memory-toolbar-btn" id="email-lib-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div id="email-lib-grid" class="doclib-grid"></div>
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
    _toggleUnreadEmails();
  });
  unreadBadge?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    _toggleUnreadEmails();
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
    state._libFolder = e.target.value;
    _loadEmailsFresh();
  });
  document.getElementById('email-lib-filter').addEventListener('change', (e) => {
    state._libFilter = e.target.value;
    _syncUnreadWindowGlow();
    _syncReminderClearButton();
    _loadEmailsFresh();
    // Sync quick-toggle active states so they mirror the dropdown.
    document.getElementById('email-undone-btn')?.classList.toggle('active', state._libFilter === 'undone');
    document.getElementById('email-reminder-btn')?.classList.toggle('active', state._libFilter === 'reminders');
  });
  document.getElementById('email-attach-btn')?.addEventListener('click', () => {
    const btn = document.getElementById('email-attach-btn');
    state._libHasAttachments = !state._libHasAttachments;
    btn?.classList.toggle('active', state._libHasAttachments);
    _syncReminderClearButton();
    _loadEmailsFresh();
  });
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
      document.getElementById('email-reminder-btn')?.classList.remove('active');
      _syncReminderClearButton();
      _loadEmailsFresh();
    } catch (err) {
      console.error(err);
      showToast('Failed to clear reminder emails');
    }
  });
  document.getElementById('email-undone-btn')?.addEventListener('click', () => {
    const btn = document.getElementById('email-undone-btn');
    const filterEl = document.getElementById('email-lib-filter');
    if (state._libFilter === 'undone') {
      state._libFilter = 'all';
      filterEl.value = 'all';
      btn.classList.remove('active');
    } else {
      state._libFilter = 'undone';
      filterEl.value = 'undone';
      btn.classList.add('active');
      document.getElementById('email-reminder-btn')?.classList.remove('active');
    }
    _syncUnreadWindowGlow();
    _syncReminderClearButton();
    _loadEmailsFresh();
  });
  document.getElementById('email-reminder-btn')?.addEventListener('click', () => {
    const btn = document.getElementById('email-reminder-btn');
    const filterEl = document.getElementById('email-lib-filter');
    if (state._libFilter === 'reminders') {
      state._libFilter = 'all';
      filterEl.value = 'all';
      btn.classList.remove('active');
    } else {
      state._libFilter = 'reminders';
      filterEl.value = 'reminders';
      btn.classList.add('active');
      document.getElementById('email-undone-btn')?.classList.remove('active');
    }
    _syncUnreadWindowGlow();
    _syncReminderClearButton();
    _loadEmailsFresh();
  });
  // The old "sort" dropdown (Latest / Unread first / Favorites first) was merged
  // into the filter dropdown above — "Favorites" is now a filter (server-side
  // \Flagged search). _libSort stays at its 'recent' default so the grid keeps
  // the API's newest-first order.

  let searchTimer = null;
  document.getElementById('email-lib-search').addEventListener('input', (e) => {
    state._libSearch = e.target.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(_doSearch, 350);
  });

  document.getElementById('email-lib-refresh-btn').addEventListener('click', async () => {
    const btn = document.getElementById('email-lib-refresh-btn');
    btn?.classList.add('email-lib-refreshing');
    state._libOffset = 0;
    // Don't wipe state._libEmails — _loadEmails will paint the cached
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

  // Select mode toggle
  document.getElementById('email-lib-select-btn').addEventListener('click', () => {
    state._selectMode = !state._selectMode;
    state._selectedUids.clear();
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

  // ESC to close + Arrow nav + Delete on the selected / currently-expanded email.
  state._libEscHandler = (e) => {
    const modal = document.getElementById('email-lib-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      if (state._selectMode) {
        state._selectMode = false;
        state._selectedUids.clear();
        _updateBulkBar();
        _renderGrid();
        return;
      }
      closeEmailLibrary();
      return;
    }
    // Don't hijack arrows / delete while the user is typing somewhere.
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
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

  _renderAccountsLoading();
  _loadAccounts();
  _loadFolders();
  _loadEmailReminderBellVisibility();
  _loadEmails();
}

async function _loadAccounts() {
  try {
    const r = await fetch(`${API_BASE}/api/email/accounts`);
    if (!r.ok) return;
    const d = await r.json();
    state._libAccounts = d.accounts || [];
  } catch (_) { state._libAccounts = []; }
  _renderAccountsStrip();
}

function _renderAccountsStrip() {
  const strip = document.getElementById('email-lib-accounts');
  if (!strip) return;
  strip.style.display = 'flex';
  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const allActive = !state._libAccountId ? ' active' : '';
  let html = `<button class="memory-toolbar-btn gallery-chip${allActive}" data-acc-id="">All (default)</button>`;
  for (const a of state._libAccounts) {
    const active = state._libAccountId === a.id ? ' active' : '';
    const label = a.name || a.from_address || a.imap_user || 'account';
    html += `<button class="memory-toolbar-btn gallery-chip${active}" data-acc-id="${esc(a.id)}" title="${esc(a.from_address || a.imap_user || '')}${a.is_default ? ' (default)' : ''}">${esc(label)}</button>`;
  }
  strip.innerHTML = html;
  strip.querySelectorAll('button[data-acc-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      state._libAccountId = btn.dataset.accId || null;
      _publishActiveAccount();
      _resetEmailListForFreshLoad();
      _renderAccountsStrip();
      await _loadFolders({ resetMissing: true });
      _loadEmails({ force: true, useCache: false });
    });
  });
  _publishActiveAccount();
}

export function closeEmailLibrary() {
  const modal = document.getElementById('email-lib-modal');
  if (modal) modal.remove();
  _clearEmailDocumentSplit();
  if (state._libEscHandler) {
    document.removeEventListener('keydown', state._libEscHandler, true);
    state._libEscHandler = null;
  }
  state._libOpen = false;
  // If the /email route collapsed the wide sidebar to make room for
  // the fullscreen modal, re-expand it now that the modal is gone.
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
}

// Make a modal draggable by its header. If `modal` and `fsClass` are
// provided, dragging to the top edge of the viewport snaps to fullscreen
// (Aero Snap). Dragging away from the top while fullscreen unsnaps.
function _makeDraggable(content, modal, fsClass) {
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
    onEnterFullscreen: fsClass ? enterFullscreen : null,
    onExitFullscreen: fsClass ? exitFullscreen : null,
  });
}

// When the user clicks Reply on a fullscreened email view, dock the email
// modal to the left as a narrow sidebar so the doc panel (which opens on
// the right side of the chat area) is visible side-by-side. Only triggers
// when the viewport is wide enough to make a true split worthwhile. Returns
// true if the snap was applied, false otherwise.
function _snapEmailModalToLeftSidebar(modal) {
  if (!modal) return false;
  if (window.innerWidth < 900) return false;
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

async function _loadFolders({ resetMissing = false } = {}) {
  const seq = ++_libFolderSeq;
  const accountAtStart = state._libAccountId || '';
  try {
    const res = await fetch(`${API_BASE}/api/email/folders?_=${Date.now()}${_acct()}`);
    const data = await res.json();
    if (seq !== _libFolderSeq || accountAtStart !== (state._libAccountId || '')) return;
    const sel = document.getElementById('email-lib-folder');
    if (!sel || !data.folders) return;
    state._libFolders = data.folders;
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
    const { priority, others } = sortedFolders(data.folders);
    for (const f of priority) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = folderDisplayName(f);
      if (f === state._libFolder) opt.selected = true;
      sel.appendChild(opt);
    }
    if (priority.length > 0 && others.length > 0) {
      const sep = document.createElement('option');
      sep.disabled = true;
      sep.textContent = '─────────';
      sel.appendChild(sep);
    }
    for (const f of others) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = folderDisplayName(f);
      if (f === state._libFolder) opt.selected = true;
      sel.appendChild(opt);
    }
    // Scheduled (special virtual folder)
    const sep2 = document.createElement('option');
    sep2.disabled = true;
    sep2.textContent = '─────────';
    sel.appendChild(sep2);
    const schedOpt = document.createElement('option');
    schedOpt.value = '__scheduled__';
    schedOpt.textContent = 'Scheduled';
    if (state._libFolder === '__scheduled__') schedOpt.selected = true;
    sel.appendChild(schedOpt);
    sel.value = state._libFolder;
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

async function _doSearch() {
  const q = state._libSearch.trim();
  if (q.length < 2) {
    // Empty or too short — show regular loaded emails
    _renderGrid();
    return;
  }
  const grid = document.getElementById('email-lib-grid');
  if (!grid) return;
  const sp = _renderEmailLoading(grid);

  try {
    const res = await fetch(`${API_BASE}/api/email/search?folder=${encodeURIComponent(state._libFolder)}${_acct()}&q=${encodeURIComponent(q)}&limit=100`);
    const data = await res.json();
    sp.destroy();

    const results = data.emails || [];
    state._libEmails = results;  // temporarily replace with search results
    _renderGrid();

    const stats = document.getElementById('email-lib-stats');
    if (stats) stats.textContent = `${data.total || results.length} match${(data.total || results.length) === 1 ? '' : 'es'}`;
  } catch (e) {
    sp.destroy();
    grid.innerHTML = '<div class="email-loading">Search failed</div>';
  }
}

function _renderEmailLoading(grid) {
  if (!grid) return null;
  grid.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'email-loading email-loading-with-label';
  let sp = null;
  try {
    sp = spinnerModule.createWhirlpool(28);
    wrap.appendChild(sp.element);
  } catch (_) {}
  const label = document.createElement('div');
  label.className = 'email-loading-label';
  label.textContent = 'Loading emails';
  wrap.appendChild(label);
  grid.appendChild(wrap);
  return sp;
}

// Refreshes the small accent-pill in the modal title with the unread count
// for the current folder. When the inbox is currently filtered to unread, the
// pill flips to show the total-emails count + "all" label, because clicking
// it would toggle the filter off — so the label needs to advertise the
// action, not the now-current view. Two tiny side-fetches (limit=1, total
// only); silent on failure — the badge just stays hidden if the request errors.
async function _refreshUnreadBadge() {
  const badge = document.getElementById('email-lib-unread-badge');
  if (!badge) return;
  try {
    const folder = state._libFolder || 'INBOX';
    if (folder === '__scheduled__') { badge.style.display = 'none'; return; }
    const res = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folder)}${_acct()}&limit=1&filter=unread`);
    const data = await res.json();
    const n = data.total || 0;
    _syncUnreadTabBadge(n);
    if (state._libFilter === 'unread') {
      // Currently viewing unread — show what the click will take you to.
      try {
        const allRes = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folder)}${_acct()}&limit=1&filter=all`);
        const allData = await allRes.json();
        const t = allData.total || 0;
        badge.textContent = `${t} all`;
        badge.title = 'Show all emails';
        badge.style.display = '';
      } catch (_) {
        badge.textContent = 'Show all';
        badge.title = 'Show all emails';
        badge.style.display = '';
      }
    } else if (n > 0) {
      badge.textContent = n > 999 ? '999+ unread' : `${n} unread`;
      badge.title = 'Show unread emails';
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  } catch (_) { _syncUnreadTabBadge(0); }
}

async function _loadEmails({ force = false, useCache = true } = {}) {
  const seq = ++_libLoadSeq;
  state._libLoading = true;
  const accountAtStart = state._libAccountId || '';
  const folderAtStart = state._libFolder;
  const filterAtStart = state._libFilter;
  const offsetAtStart = state._libOffset;
  const searchAtStart = state._libSearch;
  const hasAttachmentsAtStart = state._libHasAttachments;

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
  const cached = (useCache && cacheable) ? _libCacheGet(ck) : null;

  let sp = null;
  if (cached) {
    state._libEmails = cached.emails || [];
    state._libTotal = cached.total || 0;
    // Suppress the open-cascade animation when we're painting from
    // cache — the data was already on screen a moment ago, so sliding
    // each card in fresh feels janky. Also prevents the cascade from
    // re-firing when the bg refetch lands within the 900ms cleanup
    // window and appends new card nodes into the still-classed grid.
    state._libJustOpened = false;
    const grid2 = document.getElementById('email-lib-grid');
    if (grid2) grid2.classList.remove('email-lib-just-opened');
    _renderGrid();
    const stats = document.getElementById('email-lib-stats');
    if (stats) stats.textContent = `${state._libTotal} emails`;
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
      // `&_=Date.now()` bypasses the server's 8s list cache. Default
      // opens omit it so rapid close/reopen returns instantly; the
      // Refresh button passes `force: true` to add it back.
      const buster = force ? `&_=${Date.now()}` : '';
      const res = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(folderAtStart)}${accountQS}&limit=100&offset=${offsetAtStart}&filter=${filterAtStart}${attQS}${buster}`);
      const data = await res.json();
      if (seq !== _libLoadSeq || accountAtStart !== (state._libAccountId || '')) return;
      if (data.error) throw new Error(data.error);
      state._libEmails = data.emails || [];
      state._libTotal = data.total || 0;
      if (sp) sp.destroy();
      _renderGrid();
      const stats = document.getElementById('email-lib-stats');
      if (stats) stats.textContent = `${state._libTotal} emails`;
      _refreshUnreadBadge();
      if (cacheable) _libCachePut(ck, { emails: state._libEmails.slice(), total: state._libTotal });
    }
  } catch (e) {
    if (seq !== _libLoadSeq || accountAtStart !== (state._libAccountId || '')) return;
    if (sp) sp.destroy();
    // If we already painted the cached list, leave it on screen — beats
    // wiping it for "Failed to load" when there's still readable content.
    if (!cached) {
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
  sp.destroy();
  const items = data.scheduled || [];
  grid.innerHTML = '';
  const stats = document.getElementById('email-lib-stats');
  if (stats) stats.textContent = `${items.length} scheduled`;

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
      const { styledConfirm } = await import('./ui.js');
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

function _renderGrid() {
  const grid = document.getElementById('email-lib-grid');
  if (!grid) return;
  grid.innerHTML = '';

  let filtered = state._libEmails;

  // Apply sort
  if (state._libSort === 'unread') {
    filtered = [...filtered].sort((a, b) => Number(a.is_read) - Number(b.is_read));
  } else if (state._libSort === 'favorites') {
    filtered = [...filtered].sort((a, b) => Number(b.is_flagged) - Number(a.is_flagged));
  }
  // 'recent' is the default order from the API

  if (filtered.length === 0) {
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
  for (const em of filtered) {
    grid.appendChild(_createCard(em));
  }

  // If a deep-link asked us to expand a specific email, do it now and clear.
  if (state._libPendingExpandUid) {
    const target = filtered.find(e => String(e.uid) === String(state._libPendingExpandUid));
    const wantUid = state._libPendingExpandUid;
    state._libPendingExpandUid = null;
    if (target) {
      const cards = grid.querySelectorAll('.doclib-card');
      const targetCard = Array.from(cards).find(c => c.dataset.uid === String(wantUid));
      if (targetCard) {
        requestAnimationFrame(() => _toggleCardPreview(targetCard, target));
      }
    }
  }
}

function _createCard(em) {
  const card = document.createElement('div');
  let cls = 'doclib-card memory-item';
  if (em.is_answered) cls += ' email-card-answered';
  else if (!em.is_read) cls += ' email-card-unread';
  card.className = cls;
  card.dataset.uid = String(em.uid);
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

  // In Sent folder, show the recipient(s) — the sender is always you and
  // hides the actually useful info. Outside Sent, show the sender as before.
  const isSentFolderEarly = /sent/i.test(state._libFolder);
  let senderName;
  if (isSentFolderEarly) {
    senderName = _formatRecipients(em.to) || em.to || '(no recipient)';
  } else {
    senderName = em.from_name || em.from_address;
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

  if (em.has_attachments) {
    const att = document.createElement('span');
    att.title = 'Has attachments';
    att.style.cssText = 'opacity:0.6;flex-shrink:0;display:inline-flex;';
    att.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
    titleRow.appendChild(att);
  }

  // Done check + unread dot stay next to the subject on the left.
  const isSentFolder = /sent/i.test(state._libFolder);
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
        _syncEmailReadState(em.uid, true);
      }
      try {
        if (newState) {
          await fetch(`${API_BASE}/api/email/mark-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        } else {
          await fetch(`${API_BASE}/api/email/clear-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        }
      } catch (err) { console.error(err); }
    };
    doneCheck.addEventListener('click', _toggleDone);
    titleRow.appendChild(doneCheck);
    if (!em.is_read) {
      const dot = document.createElement('span');
      dot.className = 'email-card-unread-dot';
      dot.style.cssText = `width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;margin-left:2px;`;
      titleRow.appendChild(dot);
    }
  }

  if (em.is_flagged) {
    const star = document.createElement('span');
    star.title = 'Favorited';
    star.style.cssText = 'color:var(--accent, var(--red));opacity:0.85;flex-shrink:0;display:inline-flex;';
    star.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    titleRow.appendChild(star);
  }

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
  // Right cluster: expanded-only actions menu + nav arrows. The normal
  // `.memory-item-actions` menu is hidden while expanded, so this keeps
  // the same email actions available beside the previous/next controls.
  const rightCluster = document.createElement('span');
  rightCluster.style.cssText = 'margin-left:auto;display:inline-flex;align-items:center;gap:6px;';
  const headerMenuBtn = document.createElement('button');
  headerMenuBtn.type = 'button';
  headerMenuBtn.className = 'email-card-header-menu';
  headerMenuBtn.title = 'Actions';
  headerMenuBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
  headerMenuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    _showCardMenu(em, headerMenuBtn);
  });
  // The CSS rule on .email-card-nav-arrows still sets margin-left:auto
  // (needed when the arrows live alone in the title row). Inside this
  // wrapper, we want the cluster's gap to apply, so cancel that auto.
  navArrows.style.marginLeft = '0';
  rightCluster.appendChild(headerMenuBtn);
  rightCluster.appendChild(navArrows);
  titleRow.appendChild(rightCluster);

  content.appendChild(titleRow);

  const meta = document.createElement('div');
  meta.className = 'memory-item-meta';
  meta.style.cssText = 'font-size:10px;opacity:0.7;margin-top:2px;';
  const senderPrefix = isSentFolderEarly ? 'to ' : '';
  meta.innerHTML = `<span class="email-meta-sender"><span style="opacity:0.55">${senderPrefix}</span><span style="color:${color};font-weight:600">${_esc(senderName)}</span></span><span class="email-meta-sep"> · </span><span class="email-meta-date">${_esc(dateStr)}</span>`;
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
    await _toggleCardPreview(card, em);
  });

  return card;
}

function _findSiblingEmailCard(card, dir) {
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

const _emailReadPrefetching = new Set();

function _prefetchAdjacentEmails(card, count = 3) {
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
  for (const target of targets) {
    const uid = target.dataset.uid;
    if (!uid) continue;
    const key = `${state._libAccountId || ''}|${state._libFolder}|${uid}`;
    if (_emailReadPrefetching.has(key)) continue;
    _emailReadPrefetching.add(key);
    fetch(`${API_BASE}/api/email/read/${encodeURIComponent(uid)}?folder=${encodeURIComponent(state._libFolder)}${_acct()}&mark_seen=false`)
      .catch(() => {})
      .finally(() => _emailReadPrefetching.delete(key));
  }
}

async function _toggleCardPreview(card, em) {
  const accountAtStart = state._libAccountId || '';
  const folderAtStart = state._libFolder || 'INBOX';
  const uidAtStart = String(em?.uid || card?.dataset?.uid || '');
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
    card.classList.remove('email-card-expanded');
    card.classList.remove('doclib-card-expanded');
    card.style.minHeight = '';
    modal?.classList.remove('email-reading');
    modal?.style.removeProperty('--email-reading-modal-min-h');
    const reader = card.querySelector('.email-card-reader');
    if (reader) reader.remove();
    return;
  }

  // Collapse any other expanded card
  if (grid) {
    grid.querySelectorAll('.email-card-expanded').forEach(c => {
      c.classList.remove('email-card-expanded');
      c.classList.remove('doclib-card-expanded');
      c.style.minHeight = '';
      const r = c.querySelector('.email-card-reader');
      if (r) r.remove();
    });
  }

  card.classList.add('email-card-expanded');
  card.classList.add('doclib-card-expanded');
  card.style.minHeight = `${Math.round(stableOpenHeight)}px`;
  if (!em.is_read) {
    _syncEmailReadState(em.uid, true);
    fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(folderAtStart)}${_acct()}`, { method: 'POST' })
      .catch(err => console.error('Failed to mark email read:', err));
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
  const loadingWrap = document.createElement('div');
  loadingWrap.style.cssText = 'padding:20px;display:flex;justify-content:center;align-items:center;flex:1;';
  const sp = spinnerModule.createWhirlpool(28);
  loadingWrap.appendChild(sp.element);
  reader.appendChild(loadingWrap);
  card.appendChild(reader);

  try {
    const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(folderAtStart)}${_acct()}`);
    const data = await res.json();
    if (
      accountAtStart !== (state._libAccountId || '') ||
      folderAtStart !== (state._libFolder || 'INBOX') ||
      uidAtStart !== String(card?.dataset?.uid || '') ||
      !card.isConnected ||
      !card.classList.contains('email-card-expanded')
    ) {
      return;
    }
    if (data.error) {
      reader.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">Error: ${_esc(data.error)}</div>`;
      return;
    }

    // Mark as read locally
    _syncEmailReadState(em.uid, true);
    _prefetchAdjacentEmails(card);

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
      const addrs = str.split(',').map(s => s.trim()).filter(Boolean);
      if (addrs.length === 0) return '';
      return addrs.map(a => {
        const name = _extractName(a);
        return `<span class="recipient-chip" data-full="${_esc(a)}" title="Click for details">${_esc(name)}</span>`;
      }).join('');
    };

    // Build the From chip too — single chip with name, click reveals address
    const fromChip = `<span class="recipient-chip from-chip" data-full="${_esc(data.from_name)} &lt;${_esc(data.from_address)}&gt;" title="Click for details">${_esc(data.from_name || data.from_address)}</span>`;

    reader.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row"><strong>From:</strong><span class="recipient-chips">${fromChip}</span></div>
          ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${buildRecipients(data.to)}</span></div>` : ''}
          ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${buildRecipients(data.cc)}</span></div>` : ''}
        </div>
        <div class="email-reader-actions">
          <div class="email-reader-actions-row email-reader-actions-row-primary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="reply" title="Reply"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg><span class="reader-btn-label">Reply</span></button>
            ${_hasMultipleRecipients(data) ? `<button class="memory-toolbar-btn reader-icon-btn" data-act="reply-all" title="Reply All"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 17 2 12 7 7"/><polyline points="12 17 7 12 12 7"/><path d="M22 18v-2a4 4 0 0 0-4-4H7"/></svg><span class="reader-btn-label">Reply all</span></button>` : ''}
            <button class="memory-toolbar-btn reader-icon-btn" data-act="forward" title="Forward"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/></svg><span class="reader-btn-label">Forward</span></button>
          </div>
          <div class="email-reader-actions-row email-reader-actions-row-secondary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="ai-reply" title="${data.cached_ai_reply ? 'AI Reply (cached draft ready)' : 'AI Reply (suggest a draft)'}">${_aiReplyIcon(data)}<span class="reader-btn-label">AI reply</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="summarize" title="Summarize">${_summaryIcon(data)}<span class="reader-btn-label">Summary</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="from-sender" title="Search text in this thread"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span class="reader-btn-label">Search</span></button>
            <div class="email-reader-more-wrap" style="position:relative">
              <button class="memory-toolbar-btn reader-icon-btn" data-act="more" title="More actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg><span class="reader-btn-label">More</span></button>
            </div>
          </div>
        </div>
      </div>
      ${attsHtml}
      <div class="email-reader-body${data.body_html ? ' html-body' : ''}">${_safeRenderEmailBody(data)}</div>
    `;
    reader.classList.remove('email-card-reader-loading');
    reader.style.minHeight = '';

    // Attachment header click toggles fold/unfold (same UX as the summary).
    const attsWrap = reader.querySelector('.email-reader-atts-wrap');
    if (attsWrap) {
      const attsToggle = attsWrap.querySelector('.email-reader-atts-header');
      if (attsToggle) {
        attsToggle.addEventListener('click', (ev) => {
          ev.stopPropagation();
          attsWrap.classList.toggle('collapsed');
        });
        attsToggle.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            attsWrap.classList.toggle('collapsed');
          }
        });
      }
    }

    // Attachment chip clicks: works on both mobile (iOS Safari ignores
    // programmatic <a download> outside an actual <a> in the DOM) and desktop.
    // On mobile we open the URL in a new tab so the OS picks the action; on
    // desktop we fetch + blob-download so the filename is preserved and no
    // popup-blocker fires.
    const _isMobileUA = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

    _wireAttachmentHandlers(reader, state._libFolder);

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
    reader.querySelector('[data-act="close"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _toggleCardPreview(card, em);
    });
    reader.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _showReaderMoreMenu(em, card, reader, ev.currentTarget);
    });
    reader.querySelector('[data-act="summarize"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      await _summarizeEmail(reader, data, ev.currentTarget);
    });
    // from-sender / thread-search Search button is DISABLED for now —
    // the search + threaded sidebar UX is too buggy to ship. Physically
    // remove it from every reader render path. Re-enable by deleting
    // these .remove() lines + the CSS rule.
    reader.querySelector('[data-act="from-sender"]')?.remove();
    reader.querySelector('[data-act="from-sender"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      await _toggleFromSenderPanel(reader, data, ev.currentTarget);
    });

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

    // Event delegation for recipient chip clicks (toggle expand)
    reader.addEventListener('click', (ev) => {
      const chip = ev.target.closest('.recipient-chip');
      if (chip && reader.contains(chip)) {
        ev.stopPropagation();
        ev.preventDefault();
        const full = chip.getAttribute('data-full') || '';
        if (chip.classList.contains('expanded')) {
          chip.classList.remove('expanded');
          const name = chip.getAttribute('data-name');
          if (name != null) chip.textContent = name;
        } else {
          if (!chip.hasAttribute('data-name')) {
            chip.setAttribute('data-name', chip.textContent.trim());
          }
          chip.classList.add('expanded');
          // Decode HTML entities from the data-full attribute
          const tmp = document.createElement('textarea');
          tmp.innerHTML = full;
          chip.textContent = tmp.value;
        }
        return;
      }
      // Always stop bubbling so the card's click doesn't fire
      ev.stopPropagation();
    });
  } catch (e) {
    reader.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">Failed to load email</div>`;
  }
}

/**
 * Wrap a probable signature block in a collapsed <details> so it stops
 * eating the whole reader. We try, in priority order:
 *   1. Mail-client signature wrappers — Gmail's `gmail_signature` div is
 *      explicit, no guessing required. Same for Apple Mail's data-smartmail.
 *   2. The standard "-- " RFC 3676 sig delimiter.
 *   3. A common closing phrase ("Best regards", "Cheers", etc.) on its own
 *      line — fuzzier, but catches sigs without the dash marker.
 *   4. "Sent from my iPhone/Android" / "Get Outlook for ..." mobile-client
 *      boilerplate.
 * Anything matched gets wrapped from the marker through end-of-body.
 */
/**
 * Render the email body with sig/quote folds. If the backend has cached
 * LLM-detected boundary offsets (data.boundaries), use those for an exact
 * fold based on plain-text positions. Otherwise fall back to the regex
 * detectors. The plain-body branch is always preferred when boundaries
 * exist because the offsets are computed against plain text.
 */
// Global escape hatch — when the server's thread parser misfires (it
// occasionally splits a single reply into two bogus "turns" by treating a
// signature/disclaimer as its own message), the user can flip this off to
// fall back to plain rendering. Survives reloads.
const _BUBBLES_DISABLED_KEY = 'odysseus.email.bubblesDisabled';
// Threaded chat-bubble email view is DISABLED for now — too buggy to
// ship. Force plain-text rendering everywhere by always returning true.
// Re-enable by restoring the localStorage-backed body + the toggle
// menu item in the reader's More menu.
function _bubblesDisabled() {
  return true;
}
function _setBubblesDisabled(v) {
  try { localStorage.setItem(_BUBBLES_DISABLED_KEY, v ? '1' : '0'); } catch {}
}

function _renderEmailBody(data) {
  const plain = (typeof data?.body === 'string' && data.body.length) ? data.body : '';
  const folder = String(data?.folder || '').toLowerCase();
  const isSentFolder = folder.includes('sent');
  const fromAddr = String(data?.from_address || '').toLowerCase().trim();
  const isMine = !!fromAddr && _meEmailAddrs().has(fromAddr);

  // Messages authored by the user (Sent folder or self-sent copies in INBOX)
  // are current authored text. Do not let cached boundaries or HTML
  // blockquote parsing hide the whole thing behind "Earlier reply".
  if ((isSentFolder || isMine) && plain) {
    const plainTurns = _renderPlaintextThread(plain);
    if (plainTurns && !/^\s*<details\b/i.test(plainTurns.trim())) {
      return _foldSignature(plainTurns, null);
    }
    return _foldSignature(_escLinkify(plain).replace(/\n/g, '<br>'), null);
  }

  // Prefer the server-cached thread parse — that's the richest structure
  // and the one the chat-bubble layout is built around. Skip when the user
  // has manually disabled bubble rendering.
  if (!_bubblesDisabled() && Array.isArray(data && data.thread_turns) && data.thread_turns.length) {
    return _foldSignature(
      _renderTurnsAsBubbles(data.thread_turns, data),
      data && data.sender_signature || null,
    );
  }
  const b = data && data.boundaries;
  // Use cached boundaries when present AND we have plain-text body to slice
  if (b && plain && (b.sig_start >= 0 || b.quote_start >= 0)) {
    // Pick the EARLIER of the two as the cut for "everything below this is
    // foldable", but render sig and quote with their own labels.
    let sig = (typeof b.sig_start === 'number' && b.sig_start >= 0) ? b.sig_start : -1;
    let quote = (typeof b.quote_start === 'number' && b.quote_start >= 0) ? b.quote_start : -1;
    // Clamp
    if (sig >= plain.length) sig = -1;
    if (quote >= plain.length) quote = -1;
    let head = plain;
    let sigSection = '';
    let quoteSection = '';
    if (sig >= 0 && quote >= 0) {
      const earlier = Math.min(sig, quote);
      head = plain.slice(0, earlier);
      if (sig < quote) {
        sigSection = plain.slice(sig, quote);
        quoteSection = plain.slice(quote);
      } else {
        quoteSection = plain.slice(quote, sig);
        sigSection = plain.slice(sig);
      }
    } else if (sig >= 0) {
      head = plain.slice(0, sig);
      sigSection = plain.slice(sig);
    } else {
      head = plain.slice(0, quote);
      quoteSection = plain.slice(quote);
    }
    const fmt = (s) => _escLinkify(s).replace(/\n/g, '<br>');
    let out = fmt(head);
    if (quoteSection) {
      out += '<details class="email-quote-fold">'
           + _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(quoteSection))
           + fmt(quoteSection) + '</details>';
    }
    if (sigSection) {
      const sigHtml = fmt(sigSection);
      if (_isBloatedSig(sigHtml)) {
        out += '<details class="email-sig-fold">' + _foldSummary('Signature', _SIG_ICON)
             + sigHtml + '</details>';
      } else {
        // Short closing — leave inline; folding would just add chrome.
        out += sigHtml;
      }
    }
    return out;
  }
  // Fallback: client-side parse (HTML or plaintext).
  const hintSig = (data && data.sender_signature) || null;
  const isHtml = !!data.body_html;
  let rendered;
  if (isHtml) {
    rendered = _sanitizeHtml(data.body_html);
  } else {
    const plainTurns = _renderPlaintextThread(data.body || '');
    if (plainTurns) return _foldSignature(plainTurns, hintSig);
    rendered = _escLinkify(data.body || '').replace(/\n/g, '<br>');
  }
  const threaded = _renderThreadStructure(rendered);
  if (threaded) return _foldSignature(threaded, hintSig);
  return _foldSignature(_foldQuotedReplies(rendered), hintSig);
}

function _safeRenderEmailBody(data) {
  try {
    return _renderEmailBody(data);
  } catch (e) {
    console.error('email body render failed:', e);
    const plain = (typeof data?.body === 'string') ? data.body : '';
    if (plain) return _escLinkify(plain).replace(/\n/g, '<br>');
    if (data?.body_html) return _sanitizeHtml(data.body_html);
    return '<span style="opacity:.65">No body</span>';
  }
}

// ── Chat-bubble rendering for email threads ──
// Each parsed turn renders as a chat bubble. Bubbles for the active
// account's outgoing replies align right; everyone else aligns left.
// Order is reversed so the oldest message sits at the top of the
// conversation and the newest (the message currently being read) sits
// at the bottom — matches the mental model people have from chat.

function _meEmailAddrs() {
  const set = new Set();
  for (const a of (state._libAccounts || [])) {
    if (a && a.from_address) set.add(String(a.from_address).toLowerCase().trim());
    if (a && a.imap_user) set.add(String(a.imap_user).toLowerCase().trim());
  }
  return set;
}

// _parseTurnMeta / _formatBubbleDate / _formatRecipients / _senderColor /
// _initials live in ./emailLibrary/utils.js

function _renderTurnsAsBubbles(turns, data) {
  if (!Array.isArray(turns) || !turns.length) return '';
  const mineSet = _meEmailAddrs();
  const lvl0Email = String(data && data.from_address || '').toLowerCase().trim();
  const lvl0Mine = !!lvl0Email && mineSet.has(lvl0Email);
  const lvl0Author = (data && (data.from_name || data.from_address)) || '';
  const lvl0Date = _formatBubbleDate(data && data.date);

  // Newest reply on top, older history below. Turns come ordered shallow→deep
  // (level 0 = current reply, deeper levels = older quoted material) so we
  // render in source order without reversing.
  const ordered = turns.slice();

  // Gather per-turn sender identity + frequency for the no-self case below.
  const turnIdentity = ordered.map((t) => {
    if (t.level === 0) {
      return { email: lvl0Email, author: lvl0Author };
    }
    const p = _parseTurnMeta(t.meta || '');
    return { email: p.email, author: p.author };
  });
  const anyMine = turnIdentity.some(x => x.email && mineSet.has(x.email));
  // When the user isn't a participant in this thread (forwarded chains,
  // historical archives, etc.), assign the two most frequent senders to
  // opposite sides so the conversation still reads side-to-side. Third+
  // parties fall back to hash mod 2.
  const sideForKey = (() => {
    if (anyMine) return null;
    const freq = new Map();
    const firstSeen = new Map();
    turnIdentity.forEach((x, i) => {
      const key = (x.email || x.author || '').toLowerCase();
      if (!key) return;
      freq.set(key, (freq.get(key) || 0) + 1);
      if (!firstSeen.has(key)) firstSeen.set(key, i);
    });
    const sorted = [...freq.entries()]
      .sort((a, b) => (b[1] - a[1]) || (firstSeen.get(a[0]) - firstSeen.get(b[0])));
    const leftKey  = sorted[0] && sorted[0][0];
    const rightKey = sorted[1] && sorted[1][0];
    return (key) => {
      if (!key) return 'theirs';
      if (key === leftKey)  return 'theirs';
      if (key === rightKey) return 'mine';
      // Stable hash for 3rd+ parties.
      let h = 0;
      for (let i = 0; i < key.length; i++) h = ((h << 5) - h + key.charCodeAt(i)) | 0;
      return (h & 1) ? 'mine' : 'theirs';
    };
  })();

  const rows = ordered.map((t, i) => {
    let isMine, author, date;
    if (t.level === 0) {
      isMine = lvl0Mine;
      author = lvl0Author || 'Me';
      date = lvl0Date;
    } else {
      const p = _parseTurnMeta(t.meta || '');
      isMine = !!p.email && mineSet.has(p.email);
      author = p.author || (t.meta || 'Earlier reply');
      date = p.date;
    }
    // No-self fallback: route by per-sender side mapping.
    if (sideForKey) {
      const id = turnIdentity[i];
      const key = (id.email || id.author || '').toLowerCase();
      isMine = sideForKey(key) === 'mine';
    }
    const side = isMine ? 'mine' : 'theirs';
    const initials = _initials(author);
    const color = _senderColor(author || (t.level === 0 ? lvl0Email : ''));
    const head =
      `<div class="email-bubble-head">`
      + `<span class="email-bubble-author" style="color:${color}">${_esc(author)}</span>`
      + (date ? `<span class="email-bubble-date">${_esc(date)}</span>` : '')
      + `</div>`;
    const avatar = `<div class="email-bubble-avatar" aria-hidden="true" style="background:${color}">${_esc(initials)}</div>`;
    return (
      `<div class="email-bubble-row email-bubble-${side}" style="--bubble-accent:${color}">`
      + (isMine ? '' : avatar)
      + `<div class="email-bubble">`
      +   head
      +   `<div class="email-bubble-body">${_sanitizeHtml(t.body_html || '')}</div>`
      + `</div>`
      + (isMine ? avatar : '')
      + `</div>`
    );
  });
  return `<div class="email-bubbles">${rows.join('')}</div>`;
}

/**
 * Render server-cached thread turns (list of {level, body_html, meta})
 * into the same nested-card structure the client-side parser produces.
 */
function _renderTurnsFromServer(turns) {
  if (!Array.isArray(turns) || !turns.length) return '';
  let out = '';
  const stack = []; // [{ level, html }]
  const wrap = (t) =>
    `<details class="email-thread-turn email-quote-fold" open>`
    + _foldSummary('Earlier reply', _QUOTE_ICON, t.meta || '')
    + `<div class="email-thread-turn-body">${t.html}</div>`
    + '</details>';

  for (const t of turns) {
    if (t.level === 0) {
      while (stack.length) {
        const top = stack.pop();
        const w = wrap(top);
        if (stack.length) stack[stack.length - 1].html += w; else out += w;
      }
      out += _sanitizeHtml(t.body_html || '');
    } else {
      while (stack.length && stack[stack.length - 1].level > t.level) {
        const top = stack.pop();
        const w = wrap(top);
        if (stack.length) stack[stack.length - 1].html += w; else out += w;
      }
      if (!stack.length || stack[stack.length - 1].level < t.level) {
        stack.push({ level: t.level, meta: t.meta, html: _sanitizeHtml(t.body_html || '') });
      } else {
        stack[stack.length - 1].html += _sanitizeHtml(t.body_html || '');
        if (t.meta && !stack[stack.length - 1].meta) {
          stack[stack.length - 1].meta = t.meta;
        }
      }
    }
  }
  while (stack.length) {
    const top = stack.pop();
    const w = wrap(top);
    if (stack.length) stack[stack.length - 1].html += w; else out += w;
  }
  // Mark the bottom-most fold for rounded corners.
  const lastIdx = out.lastIndexOf('<details class="email-thread-turn email-quote-fold"');
  if (lastIdx >= 0) {
    out = out.slice(0, lastIdx)
        + out.slice(lastIdx).replace(
            'email-thread-turn email-quote-fold"',
            'email-thread-turn email-quote-fold last-fold"'
          );
  }
  return out;
}

/**
 * Parse an email body's reply chain into a stack of turn-cards.
 * Each turn = { author, date, bodyHtml, nested[] } where the body is
 * everything UP TO the next quote boundary, and `nested` is the sub-thread
 * inside (recursively parsed). Returns null if the email has no quoted
 * thread to parse (single message, no folds needed).
 */
// ── Talon-inspired multilingual quote-detection patterns ──
// Sources:
//   github.com/mailgun/talon (HTML/text quote detection)
//   github.com/crisp-oss/email-reply-parser (locale list)
//
// _TALON_* / _SIG_BLOAT_MIN_CHARS live in ./emailLibrary/utils.js
// _SIG_ICON / _QUOTE_ICON live in ./emailLibrary/signatureFold.js

function _renderThreadStructure(html) {
  if (!html || typeof html !== 'string' || html.length > 200000) return null;
  let doc;
  try { doc = new DOMParser().parseFromString(`<div id="__t">${html}</div>`, 'text/html'); }
  catch { return null; }
  const root = doc.getElementById('__t');
  if (!root) return null;

  // Find top-level blockquotes (not nested inside another blockquote).
  const tops = Array.from(root.querySelectorAll('blockquote')).filter(b =>
    !b.parentElement.closest('blockquote')
  );
  if (!tops.length) return null;

  // Build the current-message body: everything in root up to the first
  // top-level blockquote, minus the "On <date>, <author> wrote:" attribution
  // line that introduces it.
  const head = doc.createElement('div');
  let cursor = root.firstChild;
  while (cursor && cursor !== tops[0]) {
    const next = cursor.nextSibling;
    head.appendChild(cursor);
    cursor = next;
  }
  // Strip trailing "On <date>, <name> wrote:" / Outlook-style attribution
  // from `head` since the same info will appear in the turn header.
  let attribution = _harvestAttribution(head);

  // Recursively parse each top-level blockquote into a turn (and its nested chain).
  const turnsHtml = [];
  for (let i = 0; i < tops.length; i++) {
    const bq = tops[i];
    // The blockquote may have an Outlook-style "From: / Sent: / Subject:"
    // header inside as the first text. Extract that as the turn meta.
    const meta = _extractTurnMetaFromBlockquote(bq) || attribution || _extractQuoteMeta(bq.innerHTML);
    const innerHtml = bq.innerHTML;

    // Heuristic: if a blockquote has no detectable attribution (no "From:",
    // no "On <date>... wrote:") AND its content matches signature-style
    // patterns (corporate disclaimer, "registered in", legal notices, just
    // a name + title), treat it as a Signature fold instead of an Earlier
    // Reply. This stops mail clients that wrap signatures in <blockquote>
    // from making the signature appear as a phantom prior email.
    if (!meta && _looksLikeSignature(innerHtml)) {
      turnsHtml.push(
        '<details class="email-sig-fold">'
        + _foldSummary('Signature', _SIG_ICON)
        + `<div class="email-sig-body">${innerHtml}</div>`
        + '</details>'
      );
      attribution = null;
      continue;
    }

    // Recursively render the inside of this blockquote (which may contain
    // its own nested blockquotes representing earlier replies).
    const nested = _renderThreadStructure(innerHtml);
    const bodyHtml = nested || innerHtml;
    const isLast = i === tops.length - 1;
    turnsHtml.push(
      `<details class="email-thread-turn email-quote-fold${isLast ? ' last-fold' : ''}" ${i === 0 ? '' : 'open'}>`
        + _foldSummary('Earlier reply', _QUOTE_ICON, meta || '')
        + `<div class="email-thread-turn-body">${bodyHtml}</div>`
      + '</details>'
    );
    // Only the first turn uses the harvested attribution; deeper turns
    // get their own from inside the blockquote.
    attribution = null;
  }

  return head.innerHTML + turnsHtml.join('');
}

// Looks like a signature / corporate disclaimer rather than a quoted email.
// Used to demote attribution-less blockquotes that some senders wrap their
// sig+disclaimer in (Outlook, EY, big firms) from "Earlier reply" to a
// proper Signature fold. Conservative — only fires when there's no quoted
// reply markers AND it matches strong corporate-noise phrases.
// _looksLikeSignature / _harvestAttribution / _extractTurnMetaFromBlockquote
// live in ./emailLibrary/signatureFold.js

/**
 * Wrap any quoted reply chain in a collapsed <details> so deep email threads
 * don't dominate the reader. Detects:
 *   - <blockquote> tags (Gmail / native quoted replies)
 *   - Outlook-style "From: ... Sent: ... To: ... Subject: ..." headers
 * Each gets its own "Earlier thread" toggle.
 */
/**
 * Parse a plaintext email body into stacked turn-cards by walking
 * `> ` quote-prefix levels and Outlook-style "On X wrote:" / Original-Message
 * boundaries. Returns rendered HTML, or null when there's no quoted content
 * (caller falls back to flat rendering).
 *
 * Mirrors talon's `extract_from_plain` and email-reply-parser fragments:
 *   1. Lines starting with one or more `>` chars are quoted (level = count of >).
 *   2. Increasing the level opens a deeper turn (nested reply).
 *   3. `-----Original Message-----` and `On <date>, <name> wrote:` start a
 *      new turn even without `>`.
 *   4. The leading non-quoted segment is the current message.
 */
function _renderPlaintextThread(text) {
  if (!text || typeof text !== 'string' || text.length > 200000) return null;
  const lines = text.split(/\r?\n/);
  const levels = lines.map(l => {
    const m = l.match(/^((?:>\s?)+)/);
    return m ? (m[1].match(/>/g) || []).length : 0;
  });
  const hasQuotes = levels.some(l => l > 0);
  const attribLineRe = new RegExp(`(?:^|\\n)\\s*On\\s.+?\\s${_TALON_WROTE}\\s*:\\s*$`, 'im');
  const hasAttrib = attribLineRe.test(text) || _TALON_ORIG_RE.test(text);
  if (!hasQuotes && !hasAttrib) return null;

  const turns = [];
  let buf = [];
  let curLevel = 0;
  let pendingMeta = null;
  const flush = () => {
    if (!buf.length) return;
    const t = buf.join('\n').trimEnd();
    if (t || curLevel > 0) turns.push({ level: curLevel, text: t, meta: pendingMeta });
    buf = [];
    pendingMeta = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const lvl = levels[i];
    const raw = lines[i];
    const stripped = lvl > 0 ? raw.replace(/^(?:>\s?)+/, '') : raw;
    const isSeparatorLine = lvl === 0 && /^-{5,}\s*Previous message\s*-{5,}$/i.test(raw.trim());
    const isAttribLine = lvl === 0
      && (new RegExp(`^\\s*On\\s.+?\\s${_TALON_WROTE}\\s*:\\s*$`, 'i').test(raw)
          || _TALON_ORIG_RE.test('\n' + raw));
    if (isSeparatorLine || isAttribLine) {
      flush();
      pendingMeta = isSeparatorLine ? null : (_extractQuoteMeta(raw) || raw.trim());
      curLevel = 1;
      continue;
    }
    if (lvl !== curLevel) {
      flush();
      curLevel = lvl;
    }
    buf.push(stripped);
  }
  flush();

  if (!turns.length || (turns.length === 1 && turns[0].level === 0)) return null;

  const fmt = s => _escLinkify(s).replace(/\n/g, '<br>');
  let out = '';
  const stack = [];
  const wrapTurn = (t) =>
    `<details class="email-thread-turn email-quote-fold" open>`
    + _foldSummary('Earlier reply', _QUOTE_ICON, t.meta || '')
    + `<div class="email-thread-turn-body">${t.html}</div>`
    + '</details>';

  for (const t of turns) {
    if (t.level === 0) {
      while (stack.length) {
        const top = stack.pop();
        const wrapped = wrapTurn(top);
        if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
      }
      out += fmt(t.text);
    } else {
      while (stack.length && stack[stack.length - 1].level > t.level) {
        const top = stack.pop();
        const wrapped = wrapTurn(top);
        if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
      }
      if (!stack.length || stack[stack.length - 1].level < t.level) {
        stack.push({ level: t.level, meta: t.meta, html: fmt(t.text) });
      } else {
        stack[stack.length - 1].html += '<br>' + fmt(t.text);
        if (t.meta && !stack[stack.length - 1].meta) stack[stack.length - 1].meta = t.meta;
      }
    }
  }
  while (stack.length) {
    const top = stack.pop();
    const wrapped = wrapTurn(top);
    if (stack.length) stack[stack.length - 1].html += wrapped; else out += wrapped;
  }
  const lastIdx = out.lastIndexOf('<details class="email-thread-turn email-quote-fold"');
  if (lastIdx >= 0) {
    out = out.slice(0, lastIdx)
        + out.slice(lastIdx).replace(
            'email-thread-turn email-quote-fold"',
            'email-thread-turn email-quote-fold last-fold"'
          );
  }
  return out;
}

// _foldSummary / _extractQuoteMeta / _SIG_ICON / _QUOTE_ICON
// live in ./emailLibrary/signatureFold.js

function _foldQuotedReplies(html) {
  if (!html || typeof html !== 'string') return html;
  if (html.length > 200000) return html;
  const before = html;
  // Use DOMParser for proper nested-blockquote handling. Regex against HTML
  // mishandles nesting and leaves orphan close tags that the browser
  // re-balances, producing two visually inconsistent fold styles.
  try {
    const doc = new DOMParser().parseFromString(`<div id="__r">${html}</div>`, 'text/html');
    const root = doc.getElementById('__r');
    if (root) {
      // Only fold TOP-LEVEL blockquotes (children of the root that are not
      // already inside another blockquote). The inner blockquote chain stays
      // intact inside the fold and renders with the existing
      // .email-quote-fold blockquote styles, so everything matches.
      const tops = Array.from(root.querySelectorAll('blockquote')).filter(b =>
        !b.parentElement.closest('blockquote')
      );
      if (tops.length) {
        for (const bq of tops) {
          const det = doc.createElement('details');
          det.className = 'email-quote-fold';
          // Build the summary as raw HTML — easier than building DOM by hand.
          const summary = _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(bq.innerHTML));
          det.innerHTML = summary;
          bq.parentNode.insertBefore(det, bq);
          det.appendChild(bq); // move the original blockquote (and any nested ones) into the details
        }
        // Tag only the last fold so CSS can give it rounded bottom corners.
        const allFolds = root.querySelectorAll('.email-quote-fold');
        if (allFolds.length) allFolds[allFolds.length - 1].classList.add('last-fold');
        return root.innerHTML;
      }
    }
  } catch (e) {
    // Fall through to the legacy regex path below if DOMParser fails
  }
  // If DOM-pass already wrapped something, we returned above. Otherwise no
  // blockquotes were found — try the Outlook-header heuristic.
  if (html !== before) return html;
  // Outlook-style quoted-reply header — multilingual. Fold from the first
  // "From: ... Sent: ... Subject: ..." block through end-of-body so all
  // prior thread levels collapse together.
  const FROM = '(?:From|Från|Von|De|De\\s|Da|От|Od|Van)';
  const SENT = '(?:Sent|Skickat|Gesendet|Envoyé|Inviato|Enviado|Verzonden|Отправлено|Wysłane)';
  const SUBJ = '(?:Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat)';
  const outlookRe = new RegExp(
    `(<br\\s*/?>|</p>|</div>|<p[^>]*>|<div[^>]*>|\\n)\\s*((?:<[^>]+>\\s*)*${FROM}\\s*:\\s*[^<\\n]+(?:<[^>]+>\\s*|\\s)*${SENT}\\s*:[\\s\\S]+?${SUBJ}\\s*:[\\s\\S]+)$`,
    'i'
  );
  const m = html.match(outlookRe);
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    // Outlook fallback only ever produces ONE fold, so tag it as last.
    html = html.slice(0, idx) + m[1]
      + '<details class="email-quote-fold last-fold">'
      + _foldSummary('Earlier thread', _QUOTE_ICON, _extractQuoteMeta(m[2]))
      + m[2] + '</details>';
  }
  return html;
}


// Global preference: AI summary panels stay collapsed across every email
// once the user folds one, and stay expanded once they unfold. Stored in
// localStorage so the choice survives reloads.
const _SUMMARY_COLLAPSED_KEY = 'odysseus.email.summaryCollapsed';
function _summaryCollapsedPref() {
  try { return localStorage.getItem(_SUMMARY_COLLAPSED_KEY) === '1'; } catch { return false; }
}
function _setSummaryCollapsedPref(v) {
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

// "Other from this sender" — slide-out panel inside the reader listing
// recent emails from the same address. Click an item to load it in place.
async function _toggleFromSenderPanel(reader, data, btn) {
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;

  // Recenter the modal after its size changes (CSS widens + heightens the
  // modal-content when the from-sender panel is mounted/unmounted). Without
  // this the modal grows only to the right/down and can overflow the
  // viewport on narrow / short windows.
  const _recenterModal = () => {
    const modal = document.getElementById('email-lib-modal');
    const content = modal?.querySelector('.modal-content');
    if (!content) return;
    requestAnimationFrame(() => {
      const w = content.offsetWidth;
      const h = content.offsetHeight;
      const newLeft = Math.max(20, (window.innerWidth - w) / 2);
      const newTop  = Math.max(20, (window.innerHeight - h) / 2);
      content.style.left = newLeft + 'px';
      content.style.top  = newTop + 'px';
    });
  };

  // Already open? Close it.
  const existing = reader.querySelector('.from-sender-panel');
  if (existing) {
    existing.remove();
    reader.classList.remove('from-sender-open');
    if (btn) btn.classList.remove('active');
    _recenterModal();
    return;
  }

  const fromAddr = String(data.from_address || '').trim();
  if (!fromAddr) {
    if (typeof showError === 'function') showError('No sender address available');
    return;
  }

  const panel = document.createElement('div');
  panel.className = 'from-sender-panel';
  const displayName = (data.from_name && data.from_name.trim()) || fromAddr;
  const firstName = displayName.split(' ')[0] || displayName;
  panel.innerHTML = `
    <div class="from-sender-header">
      <span class="from-sender-chips"></span>
      <span class="from-sender-header-empty" hidden>All senders</span>
      <button type="button" class="from-sender-toggle" data-toggle="attachments" title="Show only emails with attachments" aria-pressed="false">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <button type="button" class="from-sender-close" title="Close" aria-label="Close sender panel">&times;</button>
    </div>
    <div class="from-sender-search-wrap">
      <input type="text" class="from-sender-search" placeholder="Search ${_esc(firstName)}…" autocomplete="off" />
      <div class="from-sender-suggest" hidden></div>
    </div>
    <div class="from-sender-list">
      <div class="from-sender-loading"></div>
    </div>
  `;
  reader.appendChild(panel);
  reader.classList.add('from-sender-open');
  if (btn) btn.classList.add('active');
  _recenterModal();

  // Header close — same as the toolbar funnel button so the close path
  // stays single-sourced (panel removal + active class drop).
  const headerClose = panel.querySelector('.from-sender-close');
  if (headerClose) {
    headerClose.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const toolbarBtn = reader.querySelector('[data-act="from-sender"]');
      if (toolbarBtn) toolbarBtn.click();
      else { panel.remove(); reader.classList.remove('from-sender-open'); }
    });
  }

  const listEl = panel.querySelector('.from-sender-list');
  // Hoisted so panel._originalEmails (assigned later, outside the try) can see it.
  let emails = [];

  // Multi-tag model — the header is now a list of {name, address} chips.
  // Filter logic: an email matches when EVERY tag's address appears in
  // from/to/cc (case-insensitive substring on the joined header strings).
  panel._tags = [{ name: displayName, address: fromAddr }];
  panel._attachmentsOnly = false;
  const searchEl = panel.querySelector('.from-sender-search');
  const chipsContainer = panel.querySelector('.from-sender-chips');
  const emptyLabel = panel.querySelector('.from-sender-header-empty');
  const suggestEl = panel.querySelector('.from-sender-suggest');
  const attToggle = panel.querySelector('[data-toggle="attachments"]');

  const _renderChips = () => {
    chipsContainer.innerHTML = panel._tags.map((t, i) => `
      <span class="from-sender-chip" title="${_esc(t.address)}" data-tag-index="${i}">
        <span class="from-sender-chip-name">${_esc(t.name || t.address)}</span>
        <button class="from-sender-chip-x" type="button" title="Remove" aria-label="Remove ${_esc(t.name || t.address)}">&times;</button>
      </span>
    `).join('');
    if (emptyLabel) emptyLabel.hidden = panel._tags.length > 0;
    chipsContainer.querySelectorAll('.from-sender-chip-x').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const idx = Number(btn.closest('.from-sender-chip')?.dataset.tagIndex || -1);
        if (idx < 0) return;
        panel._tags.splice(idx, 1);
        _renderChips();
        _refreshList();
      });
    });
  };
  // Filter loaded emails (or recents) by every active tag.
  const _matchesTags = (em) => {
    if (!panel._tags.length) return true;
    const haystack = [
      String(em.from_address || ''),
      String(em.to || ''),
      String(em.cc || ''),
    ].join(' ').toLowerCase();
    return panel._tags.every(t => haystack.includes(String(t.address || '').toLowerCase()));
  };
  const _applyToggles = () => {
    const base = panel._lastResults || [];
    let view = base.filter(_matchesTags);
    if (panel._attachmentsOnly) view = view.filter(e => e.has_attachments);
    if (!view.length) {
      const why = panel._attachmentsOnly
        ? 'No emails with attachments in this view.'
        : (panel._tags.length > 1 ? 'No emails involve all those people.' : 'No matches.');
      listEl.innerHTML = `<div class="from-sender-empty">${why}</div>`;
    } else {
      _renderFromSenderRows(view, listEl, reader, { showFolder: !!panel._lastShowFolder });
    }
  };
  panel._setResults = (rows, opts = {}) => {
    panel._lastResults = rows || [];
    panel._lastShowFolder = !!opts.showFolder;
    _applyToggles();
  };
  // Re-runs the appropriate fetch path for the current tag set / query.
  // Declared early so chip-removal handlers above can call it.
  let _refreshList = () => {};
  if (attToggle) {
    attToggle.addEventListener('click', (ev) => {
      ev.stopPropagation();
      panel._attachmentsOnly = !panel._attachmentsOnly;
      attToggle.classList.toggle('is-active', panel._attachmentsOnly);
      attToggle.setAttribute('aria-pressed', panel._attachmentsOnly ? 'true' : 'false');
      _applyToggles();
    });
  }

  try {
    const sp = spinnerModule.createWhirlpool(20);
    const loading = panel.querySelector('.from-sender-loading');
    loading.appendChild(sp.element);

    const params = new URLSearchParams({
      q: fromAddr,
      folder: state._libFolder || 'INBOX',
      limit: '25',
    });
    const acct = _acct();
    const acctSuffix = acct ? acct.replace(/^&?/, '&') : '';
    const res = await fetch(`${API_BASE}/api/email/search?${params.toString()}${acctSuffix}`);
    const j = await res.json();
    let raw = Array.isArray(j.emails) ? j.emails : [];
    const target = fromAddr.toLowerCase();
    raw = raw.filter(e => String(e.from_address || '').toLowerCase() === target);
    raw = raw.filter(e => String(e.uid) !== String(data.uid));
    emails = raw;

    if (!emails.length) {
      listEl.innerHTML = `<div class="from-sender-empty">No other emails from this sender in ${_esc(state._libFolder || 'INBOX')}.</div>`;
    } else {
      panel._setResults(emails, { showFolder: false });
    }
  } catch (err) {
    listEl.innerHTML = `<div class="from-sender-empty" style="color:var(--red, #e55)">Failed to load: ${_esc(String(err))}</div>`;
  }
  const updatePlaceholder = () => {
    if (!searchEl) return;
    searchEl.placeholder = panel._tags.length
      ? 'Add another person…'
      : 'Search people or emails…';
  };
  updatePlaceholder();
  _renderChips();

  // Used both when chips change AND when the user clears their query.
  // Pulls the most-recent emails across the common folders so the user
  // lands on something useful, then _applyToggles narrows by tags.
  let _recentToken = 0;
  const _loadRecentAcross = async () => {
    const myToken = ++_recentToken;
    const folders = _crossFolderCandidates();
    const acct = _acct();
    const acctSuffix = acct ? acct.replace(/^&?/, '&') : '';
    listEl.innerHTML = `<div class="from-sender-loading"></div>`;
    try {
      const sp = spinnerModule.createWhirlpool(18);
      listEl.querySelector('.from-sender-loading')?.appendChild(sp.element);
      const results = await Promise.all(folders.map(async (f) => {
        const params = new URLSearchParams({ folder: f, limit: '40', offset: '0', filter: 'all' });
        const res = await fetch(`${API_BASE}/api/email/list?${params.toString()}${acctSuffix}`);
        const j = await res.json();
        return (j.emails || []).map(em => ({ ...em, _folder: f }));
      }));
      if (myToken !== _recentToken) return;
      let merged = [].concat(...results);
      merged.sort((a, b) => {
        const da = a.date ? Date.parse(a.date) : 0;
        const db = b.date ? Date.parse(b.date) : 0;
        return db - da;
      });
      // Take a wider slice up front; tag/attachment filters trim it.
      merged = merged.slice(0, 80);
      panel._setResults(merged, { showFolder: true });
      updatePlaceholder();
    } catch (err) {
      if (myToken !== _recentToken) return;
      listEl.innerHTML = `<div class="from-sender-empty" style="color:var(--red, #e55)">Failed to load: ${_esc(String(err))}</div>`;
    }
  };

  // Adds a contact as a tag, clears input, refreshes the list.
  const _addTag = (contact) => {
    if (!contact || !contact.address) return;
    const addr = String(contact.address).toLowerCase();
    if (panel._tags.some(t => String(t.address).toLowerCase() === addr)) return;
    panel._tags.push({ name: contact.name || contact.address, address: contact.address });
    _renderChips();
    if (searchEl) { searchEl.value = ''; }
    if (suggestEl) { suggestEl.hidden = true; suggestEl.innerHTML = ''; }
    updatePlaceholder();
    _refreshList();
  };

  // Cross-folder search — when the user types, also honor the sender chip if
  // it's still active. Empty input with chip active restores the original
  // "from this sender" view; empty input with chip removed shows the prompt.
  if (searchEl) {
    let searchToken = 0;
    let debounceTimer = null;
    let suggestToken = 0;
    let highlightedIdx = -1;

    // Free-text email search across folders. Tag filter is applied via
    // _applyToggles inside panel._setResults.
    const runSearch = async (q) => {
      const myToken = ++searchToken;
      const folders = _crossFolderCandidates();
      const acct = _acct();
      const acctSuffix = acct ? acct.replace(/^&?/, '&') : '';
      try {
        const results = await Promise.all(folders.map(async (f) => {
          const params = new URLSearchParams({ q, folder: f, limit: '15' });
          const res = await fetch(`${API_BASE}/api/email/search?${params.toString()}${acctSuffix}`);
          const j = await res.json();
          return (j.emails || []).map(em => ({ ...em, _folder: f }));
        }));
        if (myToken !== searchToken) return;
        let merged = [].concat(...results);
        merged.sort((a, b) => {
          const da = a.date ? Date.parse(a.date) : 0;
          const db = b.date ? Date.parse(b.date) : 0;
          return db - da;
        });
        if (!merged.length) {
          listEl.innerHTML = `<div class="from-sender-empty">No matches for "${_esc(q)}".</div>`;
          return;
        }
        panel._setResults(merged, { showFolder: true });
      } catch (err) {
        if (myToken !== searchToken) return;
        listEl.innerHTML = `<div class="from-sender-empty" style="color:var(--red, #e55)">Search failed: ${_esc(String(err))}</div>`;
      }
    };

    // Hook up _refreshList so chip removal / tag add can rerun whichever
    // path matches the current input state.
    _refreshList = () => {
      const q = (searchEl.value || '').trim();
      if (q.length >= 2) runSearch(q);
      else _loadRecentAcross();
    };

    // Contact suggestions — fetched from /api/email/contacts. Renders a
    // small absolutely-positioned dropdown under the input. Up/Down/Enter/
    // Esc handled in the keydown listener below.
    const _renderSuggestions = (items) => {
      if (!suggestEl) return;
      if (!items || !items.length) {
        suggestEl.hidden = true;
        suggestEl.innerHTML = '';
        highlightedIdx = -1;
        return;
      }
      highlightedIdx = 0;
      suggestEl.innerHTML = items.map((c, i) => `
        <div class="from-sender-suggest-item${i === 0 ? ' active' : ''}" data-idx="${i}" data-addr="${_esc(c.address)}" data-name="${_esc(c.name || c.address)}">
          <span class="suggest-name">${_esc(c.name || c.address)}</span>
          <span class="suggest-addr">${_esc(c.address)}</span>
        </div>
      `).join('');
      suggestEl.hidden = false;
      suggestEl.querySelectorAll('.from-sender-suggest-item').forEach(item => {
        item.addEventListener('mouseenter', () => {
          suggestEl.querySelectorAll('.from-sender-suggest-item').forEach(n => n.classList.remove('active'));
          item.classList.add('active');
          highlightedIdx = Number(item.dataset.idx);
        });
        item.addEventListener('mousedown', (ev) => {
          // mousedown so we add the chip BEFORE blur takes the focus away
          ev.preventDefault();
          _addTag({ name: item.dataset.name, address: item.dataset.addr });
        });
      });
    };
    const _fetchSuggestions = async (q) => {
      const myToken = ++suggestToken;
      try {
        // Use the same contact source as the email composer's To/Cc fields
        // (/api/contacts/search → {results: [{name, emails:[...]}]}). Flatten
        // to {name, address} pairs and drop any already-tagged address.
        const res = await fetch(`${API_BASE}/api/contacts/search?q=${encodeURIComponent(q)}`);
        const j = await res.json();
        if (myToken !== suggestToken) return;
        const tagged = new Set(panel._tags.map(t => String(t.address).toLowerCase()));
        const items = [];
        for (const c of (j.results || [])) {
          for (const addr of (c.emails || [])) {
            if (tagged.has(String(addr).toLowerCase())) continue;
            items.push({ name: c.name || addr, address: addr });
            if (items.length >= 8) break;
          }
          if (items.length >= 8) break;
        }
        _renderSuggestions(items);
      } catch {}
    };

    searchEl.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = searchEl.value.trim();
      if (q.length < 2) {
        searchToken++;
        suggestToken++;
        if (suggestEl) { suggestEl.hidden = true; suggestEl.innerHTML = ''; }
        _loadRecentAcross();
        return;
      }
      // Fire suggestions immediately (cheap SQL) and defer the email search.
      _fetchSuggestions(q);
      debounceTimer = setTimeout(() => runSearch(q), 220);
    });

    searchEl.addEventListener('keydown', (ev) => {
      const items = suggestEl && !suggestEl.hidden
        ? [...suggestEl.querySelectorAll('.from-sender-suggest-item')]
        : [];
      if (ev.key === 'ArrowDown' && items.length) {
        ev.preventDefault();
        highlightedIdx = (highlightedIdx + 1) % items.length;
        items.forEach((n, i) => n.classList.toggle('active', i === highlightedIdx));
      } else if (ev.key === 'ArrowUp' && items.length) {
        ev.preventDefault();
        highlightedIdx = (highlightedIdx - 1 + items.length) % items.length;
        items.forEach((n, i) => n.classList.toggle('active', i === highlightedIdx));
      } else if (ev.key === 'Enter') {
        if (items.length && highlightedIdx >= 0) {
          ev.preventDefault();
          const item = items[highlightedIdx];
          _addTag({ name: item.dataset.name, address: item.dataset.addr });
        }
      } else if (ev.key === 'Escape') {
        if (suggestEl && !suggestEl.hidden) {
          ev.preventDefault();
          suggestEl.hidden = true;
        }
      } else if (ev.key === 'Backspace' && searchEl.value === '' && panel._tags.length) {
        // Empty input + Backspace pops the rightmost chip — common chip-input idiom.
        ev.preventDefault();
        panel._tags.pop();
        _renderChips();
        _refreshList();
      }
    });

    searchEl.addEventListener('blur', () => {
      // Hide suggestions on blur, with a tiny delay so click-on-suggestion
      // gets a chance to fire (mousedown-add covers most cases anyway).
      setTimeout(() => { if (suggestEl) suggestEl.hidden = true; }, 120);
    });
  }
  // Stash the sender's emails for restoring after a search is cleared.
  panel._originalEmails = (typeof emails !== 'undefined') ? emails : [];
}

const _ATT_ICON = '<svg class="from-sender-att" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Has attachments"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';

function _renderFromSenderRows(emails, listEl, reader, opts = {}) {
  const { showFolder = false } = opts;
  listEl.innerHTML = emails.map(em => {
    const subj = em.subject || '(no subject)';
    const date = em.date ? new Date(em.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : (em.date_display || '');
    const unread = em.is_read ? '' : ' from-sender-unread';
    const att = em.has_attachments ? _ATT_ICON : '';
    const folder = em._folder || state._libFolder || 'INBOX';
    const folderChip = showFolder ? `<span class="from-sender-folder">${_esc(folder)}</span>` : '';
    return `<div class="from-sender-row${unread}" data-uid="${_esc(em.uid)}" data-folder="${_esc(folder)}">
      <button class="from-sender-row-main" type="button">
        <span class="from-sender-row-top">
          <span class="from-sender-subj">${_esc(subj)}</span>
          ${att}
        </span>
        <span class="from-sender-row-bottom">
          <span class="from-sender-date">${_esc(date)}</span>
          ${folderChip}
        </span>
      </button>
      <button class="from-sender-row-more" type="button" title="More actions" aria-label="More actions">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
      </button>
    </div>`;
  }).join('');
  listEl.querySelectorAll('.from-sender-row').forEach(row => {
    const main = row.querySelector('.from-sender-row-main');
    const more = row.querySelector('.from-sender-row-more');
    main?.addEventListener('click', async () => {
      const uid = row.dataset.uid;
      const folder = row.dataset.folder || state._libFolder;
      if (!uid) return;
      await _swapReaderToUid(reader, uid, folder);
    });
    more?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const uid = row.dataset.uid;
      const folder = row.dataset.folder || state._libFolder;
      if (!uid) return;
      // Look up the row's email in any cache we know about; the menu just
      // needs uid + subject + folder for its actions.
      const em = (typeof emails !== 'undefined' ? emails : []).find(e => String(e.uid) === String(uid))
        || state._libEmails.find(e => String(e.uid) === String(uid))
        || { uid, subject: row.querySelector('.from-sender-subj')?.textContent || '' };
      const card = reader.closest('.doclib-card');
      if (card) _showReaderMoreMenu(em, card, reader, more);
    });
  });
}

// Wire click handlers for attachment chips + "open in editor" sub-buttons
// inside a reader. Safe to call multiple times — uses dataset.wired flag to
// skip nodes that already have listeners.
function _wireAttachmentHandlers(reader, folder) {
  const useFolder = folder || state._libFolder;
  // Detect mobile here so the attachment-chip handler doesn't blow up with
  // a ReferenceError when this fn is called from contexts that don't have
  // _isMobileUA in scope (e.g. _openEmailAsTab, _openEmailWindow).
  const _isMobileUA = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  reader.querySelectorAll('.email-attachment-open').forEach(openBtn => {
    if (openBtn.dataset.wired === '1') return;
    openBtn.dataset.wired = '1';
    openBtn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      const uid = openBtn.dataset.openUid;
      const index = openBtn.dataset.openIndex;
      const name = openBtn.dataset.openName || `attachment-${index}`;
      if (!uid || index == null) return;
      const orig = openBtn.style.opacity;
      openBtn.style.opacity = '0.4';
      try {
        const folderQs = encodeURIComponent(useFolder);
        const res = await fetch(
          `${API_BASE}/api/email/attachment-as-doc/${encodeURIComponent(uid)}/${encodeURIComponent(index)}?folder=${folderQs}${_acct()}`,
          { method: 'POST', credentials: 'same-origin' }
        );
        const json = await res.json().catch(() => ({}));
        if (!res.ok || !json.doc_id) {
          const msg = (json && json.error) || `HTTP ${res.status}`;
          try { const { showError } = await import('./ui.js'); showError(`Couldn't open ${name}: ${msg}`); } catch (_) { alert(`Couldn't open ${name}: ${msg}`); }
          return;
        }
        try {
          // Tab the email modal down only when the viewport cannot fit both
          // Email and the document pane. Desktop keeps a side-by-side layout
          // when there is room; mobile still gives the document the screen.
          const ownerModal = openBtn.closest('.modal');
          if (ownerModal && ownerModal.id && _prepareEmailWindowForDocument(ownerModal)) {
            try {
              const ok = Modals.minimize(ownerModal.id);
              if (!ok) ownerModal.classList.add('hidden');
            } catch (_) {
              ownerModal.classList.add('hidden');
            }
          }
          const docMod = await import('./document.js');
          const load = (docMod && docMod.loadDocument) || (docMod && docMod.default && docMod.default.loadDocument);
          if (typeof load === 'function') {
            await load(json.doc_id);
          } else {
            location.href = `/?doc=${encodeURIComponent(json.doc_id)}`;
          }
        } catch (e) {
          console.error('Open document failed:', e);
          try { const { showError } = await import('./ui.js'); showError('Document opened but panel could not mount'); } catch (_) {}
        }
      } catch (e) {
        console.error('attachment-as-doc error', e);
        try { const { showError } = await import('./ui.js'); showError(`Couldn't open ${name}`); } catch (_) {}
      } finally {
        openBtn.style.opacity = orig;
      }
    });
  });

  reader.querySelectorAll('.email-attachment-chip').forEach(chip => {
    if (chip.dataset.wired === '1') return;
    chip.dataset.wired = '1';
    chip.addEventListener('click', async (ev) => {
      if (ev.target.closest('.email-attachment-open')) return;
      ev.stopPropagation();
      ev.preventDefault();
      const uid = chip.dataset.attUid;
      const index = chip.dataset.attIndex;
      const name = chip.dataset.attName || `attachment-${index}`;
      if (!uid || index == null) return;
      const url = `${API_BASE}/api/email/attachment/${encodeURIComponent(uid)}/${encodeURIComponent(index)}?folder=${encodeURIComponent(useFolder)}${_acct()}`;
      if (_isMobileUA) {
        window.open(url, '_blank');
        return;
      }
      const orig = chip.style.opacity;
      chip.style.opacity = '0.6';
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok) {
          console.error('attachment download failed', res.status, await res.text().catch(() => ''));
          location.href = url;
          return;
        }
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      } catch (e) {
        console.error('attachment download error', e);
        location.href = url;
      } finally {
        chip.style.opacity = orig;
      }
    });
  });
}

// Heuristic: skip "attachments" that are clearly inline images used by
// signatures / quoted-reply headers (small image files, Outlook-style
// image001.png placeholders, logo*.png, etc.). They aren't real user-
// shared attachments and adding them to the chips makes every email look
// like it has content the user needs to act on.
function _isLikelySignatureImage(a) {
  if (!a || !a.filename) return false;
  const name = String(a.filename).toLowerCase();
  const isImage = /\.(png|jpe?g|gif|bmp|svg|webp)$/i.test(name);
  if (!isImage) return false;
  const size = Number(a.size) || 0;
  // Outlook / Gmail inline image placeholders always look like this.
  if (/^image\d{3,}\.(png|jpe?g|gif)$/i.test(name)) return true;
  if (/^(signature|logo|sig|footer|banner)[-_\d]*\.(png|jpe?g|gif|svg)$/i.test(name)) return true;
  // Most signature logos / inline thumbnails are < 30 KB. Real user-
  // shared images (screenshots, photos) are typically 50 KB+.
  if (size > 0 && size < 30 * 1024) return true;
  return false;
}

// Build the attachments header+chips HTML for an email read response. Pulled
// out so both the initial-open and the swap-reader paths can render it.
function _buildAttsHtmlFor(uid, data) {
  if (!data || !data.attachments || !data.attachments.length) return '';
  const _OPENABLE_RE = /\.(pdf|docx|txt|md|markdown)$/i;
  const visible = data.attachments.filter(a => !_isLikelySignatureImage(a));
  if (!visible.length) return '';
  const chips = visible.map(a => {
    const openable = _OPENABLE_RE.test(a.filename || '');
    const openBtn = openable
      ? `<span class="email-attachment-open" title="Open in document editor" data-open-uid="${_esc(uid)}" data-open-index="${a.index}" data-open-name="${_esc(a.filename)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/></svg><span class="email-attachment-open-label">Open</span></span>`
      : '';
    return `<button type="button" class="email-attachment-chip" data-att-uid="${_esc(uid)}" data-att-index="${a.index}" data-att-name="${_esc(a.filename)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><span>${_esc(a.filename)}</span><span class="att-size">${Math.round((a.size||0)/1024)} KB</span>${openBtn}</button>`;
  }).join('');
  return (
    '<div class="email-reader-atts-wrap collapsed">'
    +   '<div class="email-reader-atts-header email-summary-toggle" role="button" tabindex="0">'
    +     '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
    +     `<span>Attachments (${data.attachments.length})</span>`
    +     '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    +   '</div>'
    +   '<div class="email-reader-atts">' + chips + '</div>'
    + '</div>'
  );
}

// "Open in new tab" — the email opens in the library (expanded inline)
// AND a separate floating "email viewer" overlay modal is created. The
// overlay starts minimized as a chip in the dock; tapping the chip
// brings the viewer up over the library. Multiple tabs = multiple
// overlay modals + chips, each independent.
const _EMAIL_ICON_PATH = 'M2 4h20v16H2zM22 7l-9.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7';
let _emailTabSeq = 0;
// Persistent slot numbers per reader modalId. Once a reader is "tab 2"
// it stays "tab 2" until it's closed — even if tab 1 closes first, the
// remaining reader doesn't renumber down to 1. New tabs claim the
// lowest unused slot.
const _emailReaderSlots = new Map(); // modalId -> slot (1, 2, 3, ...)
function _allocReaderSlot(modalId) {
  if (_emailReaderSlots.has(modalId)) return _emailReaderSlots.get(modalId);
  const used = new Set(_emailReaderSlots.values());
  let n = 1;
  while (used.has(n)) n++;
  _emailReaderSlots.set(modalId, n);
  return n;
}
function _freeReaderSlot(modalId) {
  _emailReaderSlots.delete(modalId);
}

// JS-driven gate: sets [data-email-tabs="N"] on <body> so CSS can show
// the per-chip number badge only when 2+ tabs exist.
function _syncEmailTabsCount() {
  const tabs = document.querySelectorAll('.minimized-dock-chip[data-modal-id^="email-view-"]');
  document.body.dataset.emailTabs = String(tabs.length);
}

// Recompute the email menu chip's tab-count whenever the dock contents
// change. Counts "email-view-*" chips both inside #minimized-dock and
// at body level (free-positioned chips on mobile). Result is written to
// the email-lib-modal chip's data-tab-count attribute; CSS reads it via
// attr() to render the badge.
function _syncEmailTabBadge() {
  const readers = document.querySelectorAll('.minimized-dock-chip[data-modal-id^="email-reader-"]');
  document.body.dataset.emailReaders = String(readers.length);
  // Stamp each chip with its persistent slot number. CSS reads
  // data-tab-num via attr() instead of using a counter so the number
  // stays stable when other tabs close.
  readers.forEach(chip => {
    const slot = _emailReaderSlots.get(chip.dataset.modalId);
    if (slot) chip.dataset.tabNum = String(slot);
  });
}
let _emailTabObserverWired = false;
let _badgeSyncScheduled = false;
function _ensureEmailTabObserver() {
  if (_emailTabObserverWired) return;
  _emailTabObserverWired = true;
  // Debounce so a burst of mutations (e.g. _renderDock rebuilding the
  // whole dock in one pass) collapses to a single sync per animation
  // frame. Without this the chip badge could flicker as the observer
  // fires repeatedly during dock rerenders.
  const handler = () => {
    if (_badgeSyncScheduled) return;
    _badgeSyncScheduled = true;
    requestAnimationFrame(() => {
      _badgeSyncScheduled = false;
      _syncEmailTabBadge();
    });
  };
  const tryWire = () => {
    const dock = document.getElementById('minimized-dock');
    if (!dock) { setTimeout(tryWire, 200); return; }
    // Only watch what we care about: chip add/remove in the dock.
    const obs = new MutationObserver(handler);
    obs.observe(dock, { childList: true });
    // Watch the library grid so toggling a card expanded/collapsed
    // updates the lib chip's "has-expanded" badge in real time.
    const wireGridObs = () => {
      const grid = document.getElementById('email-lib-grid');
      if (!grid) { setTimeout(wireGridObs, 500); return; }
      const gridObs = new MutationObserver(handler);
      gridObs.observe(grid, { subtree: true, attributes: true, attributeFilter: ['class'] });
    };
    wireGridObs();
    handler();
  };
  tryWire();
}
// Hybrid model:
//   - email-lib-modal (the inbox library) is unique. Its chip just
//     restores it.
//   - Each "Open in new tab" creates a separate per-email reader modal
//     (id "email-reader-{uid}-{seq}") with the SAME structure & classes
//     as the library's inline reader, so they look identical. Each
//     reader registers its own dock chip with a number badge.
async function _openEmailAsTab(em, folder) {
  const useFolder = folder || state._libFolder || 'INBOX';
  _emailTabSeq += 1;
  const modalId = `email-reader-${em.uid}-${_emailTabSeq}`;
  _allocReaderSlot(modalId);

  // Build the modal shell. Uses the same doclib-modal-content sizing
  // as the email library so it feels like a sibling window. The reader
  // body inside uses the exact same email-card-reader / email-reader-*
  // classes the inline reader uses → identical styling.
  const modal = document.createElement('div');
  modal.className = 'modal email-reader-tab-modal';
  modal.id = modalId;
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content email-reader-tab-content" style="background:var(--bg);width:min(720px, 92vw);max-height:85vh;display:flex;flex-direction:column;">
      <div class="modal-header">
        <h4 style="display:flex;align-items:center;gap:6px;min-width:0;flex:1;">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-left:8px;">${_esc(em.subject || '(no subject)')}</span>
        </h4>
        <button class="minimize-btn" type="button" title="Minimize">_</button>
        <button class="close-btn" type="button" title="Close">&#x2716;</button>
      </div>
      <div class="modal-body email-reader-tab-body" style="display:flex;flex-direction:column;overflow:hidden;flex:1;min-height:0;padding:0;">
        <div class="email-card-reader email-card-expanded" style="flex:1;min-height:0;display:flex;flex-direction:column;">
          <div class="email-reader-tab-loading" style="padding:24px;display:flex;justify-content:center;"></div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  // Inherit display from .modal (flex-center). z-index above the library
  // (which uses default .modal z-index 250) so the new tab sits on top.
  modal.style.zIndex = '270';
  // Opened last → email windows in front of any open doc (alternation flag).
  document.body.classList.add('email-front');

  Modals.register(modalId, {
    label: 'Email',
    icon: _EMAIL_ICON_PATH,
    closeFn: () => {
      modal.remove();
      _freeReaderSlot(modalId);
      Promise.resolve().then(_syncEmailTabBadge);
    },
    restoreFn: () => {
      // Reopened last → bring the email windows in front of any open doc.
      document.body.classList.add('email-front');
      // Mobile: only one email window visible at a time. Tapping this
      // chip chips down the library + any other reader, so the user
      // toggles between them via the dock instead of stacking.
      if (window.innerWidth <= 768) {
        try {
          if (Modals.isRegistered('email-lib-modal') && !Modals.isMinimized('email-lib-modal')) {
            Modals.minimize('email-lib-modal');
          }
        } catch {}
        document.querySelectorAll('.modal[id^="email-reader-"]').forEach(other => {
          if (other.id === modalId) return;
          try {
            if (Modals.isRegistered(other.id) && !Modals.isMinimized(other.id)) {
              Modals.minimize(other.id);
            }
          } catch {}
        });
      }
    },
  });
  // Wire the `_` minimize button via modalManager (it sees our .minimize-btn
  // already exists and just binds the click handler).
  try { Modals.injectMinimizeButton(modal, modalId); } catch {}
  // X button fully closes the tab (tears down and unregisters).
  modal.querySelector('.close-btn')?.addEventListener('click', (ev) => {
    ev.stopPropagation();
    Modals.close(modalId);
  });

  // Wire dragging on the header (desktop only). Matches the global pattern
  // in app.js initUIVisibility, but that runs once at boot and doesn't see
  // dynamically-created modals — so we replicate it here.
  const content = modal.querySelector('.modal-content');
  const mh = modal.querySelector('.modal-header');
  if (mh && content) {
    let dragX = 0, dragY = 0, startLeft = 0, startTop = 0, dragging = false;
    const startDrag = (clientX, clientY) => {
      dragging = true;
      const rect = content.getBoundingClientRect();
      dragX = clientX; dragY = clientY;
      startLeft = rect.left; startTop = rect.top;
      content.style.position = 'fixed';
      content.style.left = startLeft + 'px';
      content.style.top = startTop + 'px';
      content.style.margin = '0';
    };
    const onDrag = (e) => {
      if (!dragging) return;
      content.style.left = (startLeft + e.clientX - dragX) + 'px';
      content.style.top = (startTop + e.clientY - dragY) + 'px';
    };
    const stopDrag = () => {
      dragging = false;
      document.removeEventListener('mousemove', onDrag);
      document.removeEventListener('mouseup', stopDrag);
    };
    mh.addEventListener('mousedown', (e) => {
      if (e.target.closest('.close-btn, .minimize-btn, .modal-minimize-btn')) return;
      e.preventDefault();
      startDrag(e.clientX, e.clientY);
      document.addEventListener('mousemove', onDrag);
      document.addEventListener('mouseup', stopDrag);
    });
  }

  // Open the new tab in front, on top of the email library. The user
  // can tap `_` to tab it down to a chip when they're done reading.
  //
  // Mobile: bottom-sheet windows fill the viewport, so stacking multiple
  // readers on top of each other is confusing — only one window can be
  // meaningfully visible at a time. So when the new tab opens, chip down
  // the library AND any other email-reader-* tab that's currently up.
  // The user gets a stack of mini chips to toggle between them.
  if (window.innerWidth <= 768) {
    try {
      if (Modals.isRegistered('email-lib-modal') && !Modals.isMinimized('email-lib-modal')) {
        Modals.minimize('email-lib-modal');
      }
    } catch {}
    document.querySelectorAll('.modal[id^="email-reader-"]').forEach(other => {
      if (other.id === modalId) return;
      try {
        if (Modals.isRegistered(other.id) && !Modals.isMinimized(other.id)) {
          Modals.minimize(other.id);
        }
      } catch {}
    });
  }
  _ensureEmailTabObserver();
  _syncEmailTabBadge();

  // Fetch + render the email body using the exact same template as
  // _toggleCardPreview so the visuals match perfectly.
  const reader = modal.querySelector('.email-card-reader');
  const sp = spinnerModule.createWhirlpool(28);
  const loading = modal.querySelector('.email-reader-tab-loading');
  if (loading) loading.appendChild(sp.element);
  try {
    const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(useFolder)}${_acct()}`);
    const data = await res.json();
    if (data.error) {
      reader.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">Error: ${_esc(data.error)}</div>`;
      return;
    }
    _syncEmailReadState(em.uid, true);
    const buildChips = (str) => {
      if (!str) return '';
      return str.split(',').map(s => s.trim()).filter(Boolean).map(a => {
        const name = _extractName(a);
        return `<span class="recipient-chip" data-full="${_esc(a)}" title="Click for details">${_esc(name)}</span>`;
      }).join('');
    };
    const fromChip = `<span class="recipient-chip from-chip" data-full="${_esc(data.from_name)} &lt;${_esc(data.from_address)}&gt;" title="Click for details">${_esc(data.from_name || data.from_address)}</span>`;
    let attsHtml = '';
    try { attsHtml = _buildAttsHtmlFor(em.uid, data); } catch {}
    reader.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row"><strong>From:</strong><span class="recipient-chips">${fromChip}</span></div>
          ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${buildChips(data.to)}</span></div>` : ''}
          ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${buildChips(data.cc)}</span></div>` : ''}
        </div>
        <div class="email-reader-actions">
          <div class="email-reader-actions-row email-reader-actions-row-primary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="reply" title="Reply"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg><span class="reader-btn-label">Reply</span></button>
            ${_hasMultipleRecipients(data) ? `<button class="memory-toolbar-btn reader-icon-btn" data-act="reply-all" title="Reply All"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 17 2 12 7 7"/><polyline points="12 17 7 12 12 7"/><path d="M22 18v-2a4 4 0 0 0-4-4H7"/></svg><span class="reader-btn-label">Reply all</span></button>` : ''}
            <button class="memory-toolbar-btn reader-icon-btn" data-act="forward" title="Forward"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/></svg><span class="reader-btn-label">Forward</span></button>
          </div>
          <div class="email-reader-actions-row email-reader-actions-row-secondary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="ai-reply" title="${data.cached_ai_reply ? 'AI Reply (cached draft ready)' : 'AI Reply'}">${_aiReplyIcon(data)}<span class="reader-btn-label">AI reply</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="summarize" title="Summarize">${_summaryIcon(data)}<span class="reader-btn-label">Summary</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="from-sender" title="Search text in this thread"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span class="reader-btn-label">Search</span></button>
            <div class="email-reader-more-wrap" style="position:relative">
              <button class="memory-toolbar-btn reader-icon-btn" data-act="more" title="More actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg><span class="reader-btn-label">More</span></button>
            </div>
          </div>
        </div>
      </div>
      ${attsHtml}
      <div class="email-reader-body${data.body_html ? ' html-body' : ''}">${_safeRenderEmailBody(data)}</div>
    `;
    try { _wireAttachmentHandlers(reader, useFolder); } catch {}
    const attsWrap = reader.querySelector('.email-reader-atts-wrap');
    if (attsWrap) {
      const attsToggle = attsWrap.querySelector('.email-reader-atts-header');
      if (attsToggle) attsToggle.addEventListener('click', (ev) => { ev.stopPropagation(); attsWrap.classList.toggle('collapsed'); });
    }
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
    reader.querySelector('[data-act="summarize"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await _summarizeEmail(reader, data, ev.currentTarget); } catch {}
    });
    reader.querySelector('[data-act="from-sender"]')?.remove();
    reader.querySelector('[data-act="from-sender"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await _toggleFromSenderPanel(reader, data, ev.currentTarget); } catch {}
    });
    reader.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      try { _showReaderMoreMenu(em, modal, reader, ev.currentTarget); } catch {}
    });
  } catch (err) {
    reader.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">Failed to load: ${_esc(String(err))}</div>`;
  }
}


// "Open in new window" — spawns a floating draggable modal that shows just
// the email content. Multiple windows can stack; each has its own DOM id
// and close button. Uses `_makeDraggable` so dragging the header pans the
// window around. Renders the body via _renderEmailBody for parity with the
// expanded reader.
let _emailWindowSeq = 0;
async function _openEmailWindow(em, folder) {
  const useFolder = folder || state._libFolder || 'INBOX';
  _emailWindowSeq += 1;
  const winId = `email-window-${em.uid}-${_emailWindowSeq}`;
  const modal = document.createElement('div');
  modal.className = 'modal email-window-modal';
  modal.id = winId;
  modal.style.cssText = 'pointer-events:none;background:transparent;';
  modal.innerHTML = `
    <div class="modal-content email-window-content" style="width:min(640px, 92vw);max-height:80vh;display:flex;flex-direction:column;background:var(--bg);">
      <div class="modal-header">
        <h4 style="display:flex;align-items:center;gap:6px;min-width:0;flex:1;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
          <span class="email-window-subject" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(em.subject || '(no subject)')}</span>
        </h4>
        <button class="close-btn" type="button" title="Close">&#x2716;</button>
      </div>
      <div class="modal-body email-window-body" style="overflow:auto;padding:14px 16px;flex:1;min-height:0;">
        <div class="email-window-loading" style="display:flex;justify-content:center;padding:24px;"></div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'block';
  const content = modal.querySelector('.modal-content');
  // Position offset from screen center so successive windows cascade.
  const isMobile = window.innerWidth <= 768;
  if (isMobile) {
    content.style.position = 'fixed';
    content.style.pointerEvents = 'auto';
    content.style.left = '0';
    content.style.right = '0';
    content.style.bottom = '0';
    content.style.top = 'auto';
  } else {
    content.style.position = 'fixed';
    content.style.pointerEvents = 'auto';
    requestAnimationFrame(() => {
      const w = content.offsetWidth, h = content.offsetHeight;
      const off = (_emailWindowSeq % 6) * 28;
      content.style.left = Math.max(20, (window.innerWidth  - w) / 2 + off) + 'px';
      content.style.top  = Math.max(20, (window.innerHeight - h) / 3 + off) + 'px';
    });
  }
  modal.querySelector('.close-btn')?.addEventListener('click', () => modal.remove());
  try { _makeDraggable(content, modal, 'email-window-fullscreen'); } catch {}

  // Load + render
  const bodyEl = modal.querySelector('.email-window-body');
  const loading = modal.querySelector('.email-window-loading');
  try {
    const sp = spinnerModule.createWhirlpool(24);
    loading.appendChild(sp.element);
    const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(useFolder)}${_acct()}`);
    const data = await res.json();
    if (data.error) {
      bodyEl.innerHTML = `<div style="color:var(--red,#e55);padding:16px;">${_esc(data.error)}</div>`;
      return;
    }
    _syncEmailReadState(em.uid, true);
    const subjEl = modal.querySelector('.email-window-subject');
    if (subjEl && data.subject) subjEl.textContent = data.subject;
    // Build recipient chips the same way the inline reader does so the
    // standalone viewer looks/feels exactly like a real email view.
    const _chipsFor = (addrs) => {
      if (!addrs) return '';
      const list = addrs.split(',').map(s => s.trim()).filter(Boolean);
      return list.map(a => {
        const name = _extractName(a);
        return `<span class="recipient-chip" data-full="${_esc(a)}" title="Click for details">${_esc(name)}</span>`;
      }).join('');
    };
    const fromChip = `<span class="recipient-chip from-chip" data-full="${_esc(data.from_name)} &lt;${_esc(data.from_address)}&gt;" title="Click for details">${_esc(data.from_name || data.from_address)}</span>`;
    let attsHtml = '';
    try { attsHtml = _buildAttsHtmlFor(em.uid, data); } catch {}
    // Repurpose bodyEl as a full email-card-reader so the inline reader's
    // CSS applies (sized header, action buttons in two rows, etc.).
    bodyEl.classList.add('email-card-reader');
    bodyEl.style.padding = '0';
    bodyEl.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row"><strong>From:</strong><span class="recipient-chips">${fromChip}</span></div>
          ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${_chipsFor(data.to)}</span></div>` : ''}
          ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${_chipsFor(data.cc)}</span></div>` : ''}
        </div>
        <div class="email-reader-actions">
          <div class="email-reader-actions-row email-reader-actions-row-primary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="reply" title="Reply"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg><span class="reader-btn-label">Reply</span></button>
            ${_hasMultipleRecipients(data) ? `<button class="memory-toolbar-btn reader-icon-btn" data-act="reply-all" title="Reply All"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 17 2 12 7 7"/><polyline points="12 17 7 12 12 7"/><path d="M22 18v-2a4 4 0 0 0-4-4H7"/></svg><span class="reader-btn-label">Reply all</span></button>` : ''}
            <button class="memory-toolbar-btn reader-icon-btn" data-act="forward" title="Forward"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/></svg><span class="reader-btn-label">Forward</span></button>
          </div>
          <div class="email-reader-actions-row email-reader-actions-row-secondary">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="ai-reply" title="${data.cached_ai_reply ? 'AI Reply (cached draft ready)' : 'AI Reply (suggest a draft)'}">${_aiReplyIcon(data)}<span class="reader-btn-label">AI reply</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="summarize" title="Summarize">${_summaryIcon(data)}<span class="reader-btn-label">Summary</span></button>
            <button class="memory-toolbar-btn reader-icon-btn" data-act="from-sender" title="Search text in this thread"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span class="reader-btn-label">Search</span></button>
            <div class="email-reader-more-wrap" style="position:relative">
              <button class="memory-toolbar-btn reader-icon-btn" data-act="more" title="More actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg><span class="reader-btn-label">More</span></button>
            </div>
          </div>
        </div>
      </div>
      ${attsHtml}
      <div class="email-reader-body${data.body_html ? ' html-body' : ''}">${_safeRenderEmailBody(data)}</div>
    `;
    // Wire all the same action handlers the inline reader has.
    try { _wireAttachmentHandlers(bodyEl, useFolder); } catch {}
    const attsWrap = bodyEl.querySelector('.email-reader-atts-wrap');
    if (attsWrap) {
      const attsToggle = attsWrap.querySelector('.email-reader-atts-header');
      if (attsToggle) attsToggle.addEventListener('click', (ev) => { ev.stopPropagation(); attsWrap.classList.toggle('collapsed'); });
    }
    bodyEl.querySelector('[data-act="reply"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      _snapEmailModalToLeftSidebar(ev.currentTarget.closest('.modal'));
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'reply' });
    });
    bodyEl.querySelector('[data-act="reply-all"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      _snapEmailModalToLeftSidebar(ev.currentTarget.closest('.modal'));
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'reply-all' });
    });
    bodyEl.querySelector('[data-act="ai-reply"]')?.addEventListener('click', (ev) => _handleAiReplyButton(ev, em, data));
    bodyEl.querySelector('[data-act="forward"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode: 'forward' });
    });
    bodyEl.querySelector('[data-act="summarize"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await _summarizeEmail(bodyEl, data, ev.currentTarget); } catch {}
    });
    bodyEl.querySelector('[data-act="from-sender"]')?.remove();
    bodyEl.querySelector('[data-act="from-sender"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await _toggleFromSenderPanel(bodyEl, data, ev.currentTarget); } catch {}
    });
    bodyEl.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      // Use a synthetic "card" — the more-menu only needs the anchor
      // element and the email data. The card param is mostly used to find
      // the next sibling; the standalone window has none so we just pass
      // bodyEl as a stand-in.
      try { _showReaderMoreMenu(em, modal, bodyEl, ev.currentTarget); } catch {}
    });
  } catch (err) {
    bodyEl.innerHTML = `<div style="color:var(--red,#e55);padding:16px;">Failed to load: ${_esc(String(err))}</div>`;
  }
}

// Fetch a new email's content and replace the current reader body with it
// (preserving the from-sender panel). Used for in-place navigation between
// emails of the same sender — `folder` defaults to the library's current
// folder but is overridable so cross-folder search results can open the
// correct one.
async function _swapReaderToUid(reader, uid, folder) {
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;
  body.innerHTML = '';
  const sp = spinnerModule.createWhirlpool(24);
  const wrap = document.createElement('div');
  wrap.style.cssText = 'padding:20px;display:flex;justify-content:center';
  wrap.appendChild(sp.element);
  body.appendChild(wrap);
  const useFolder = folder || state._libFolder;
  try {
    const res = await fetch(`${API_BASE}/api/email/read/${uid}?folder=${encodeURIComponent(useFolder)}${_acct()}`);
    const data = await res.json();
    if (data.error) {
      body.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">${_esc(data.error)}</div>`;
      return;
    }
    _syncEmailReadState(uid, true);
    // Update the header meta (From/To/Subject) so it matches the new email.
    const headerMeta = reader.querySelector('.email-reader-meta');
    if (headerMeta) {
      const subj = data.subject || '(no subject)';
      const date = data.date ? new Date(data.date).toLocaleString() : '';
      headerMeta.innerHTML = `
        <div class="email-reader-meta-row"><strong>Subject:</strong> ${_esc(subj)}</div>
        <div class="email-reader-meta-row"><strong>From:</strong> ${_esc(data.from_name || data.from_address)} &lt;${_esc(data.from_address)}&gt;</div>
        ${date ? `<div class="email-reader-meta-row"><strong>Date:</strong> ${_esc(date)}</div>` : ''}
      `;
    }
    // Refresh the attachments block to match the new email. Build fresh HTML
    // and either replace the existing block, remove it (if the new email has
    // none), or insert one before the body (if the previous email had none
    // but the new one does).
    const newAttsHtml = _buildAttsHtmlFor(uid, data);
    const oldAtts = reader.querySelector('.email-reader-atts-wrap');
    if (newAttsHtml) {
      if (oldAtts) {
        const tmp = document.createElement('div');
        tmp.innerHTML = newAttsHtml;
        oldAtts.replaceWith(tmp.firstChild);
      } else {
        body.insertAdjacentHTML('beforebegin', newAttsHtml);
      }
      const newWrap = reader.querySelector('.email-reader-atts-wrap');
      if (newWrap) {
        const hdr = newWrap.querySelector('.email-reader-atts-header');
        if (hdr) {
          hdr.addEventListener('click', (ev) => {
            ev.stopPropagation();
            newWrap.classList.toggle('collapsed');
          });
          hdr.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
              ev.preventDefault();
              newWrap.classList.toggle('collapsed');
            }
          });
        }
      }
    } else if (oldAtts) {
      oldAtts.remove();
    }
    body.innerHTML = _safeRenderEmailBody(data);
    body.classList.toggle('html-body', !!data.body_html);
    // Wire click handlers for the newly-rendered attachment chips. Without
    // this, after swapping to a different email via the sidebar, clicking
    // an attachment chip would do nothing.
    _wireAttachmentHandlers(reader, useFolder);
  } catch (err) {
    body.innerHTML = `<div style="padding:20px;color:var(--red,#e55)">${_esc(String(err))}</div>`;
  }
}

async function _summarizeEmail(reader, data, btn) {
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;

  // If a summary panel already exists, toggle: hide/show
  const existing = body.querySelector('.email-summary-panel');
  if (existing) {
    if (existing.style.display === 'none') {
      existing.style.display = '';
      if (btn) {
        btn.classList.add('active');
        btn.querySelector('.btn-label').textContent = 'Summary';
      }
    } else {
      existing.style.display = 'none';
      if (btn) {
        btn.classList.remove('active');
        btn.querySelector('.btn-label').textContent = 'Summary';
      }
    }
    return;
  }

  // No panel yet. If the email has no cached AI summary, show a placeholder
  // "not generated — create now?" prompt instead of firing the LLM immediately.
  // This avoids accidental LLM spend and makes the state explicit to the user.
  if (!data.cached_summary) {
    const prompt = document.createElement('div');
    prompt.className = 'email-summary-panel';
    prompt.innerHTML = `
      <div class="email-summary-header">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
        <span>Summary</span>
      </div>
      <div class="email-summary-content" style="white-space:normal;display:flex;align-items:center;flex-wrap:wrap;gap:6px;"><span style="opacity:0.65">No AI summary generated.</span><button class="memory-toolbar-btn" data-act="summary-generate" style="font-size:10px;margin-left:auto;">Generate now</button></div>`;
    body.insertBefore(prompt, body.firstChild);
    if (btn) {
      btn.classList.add('active');
      const label = btn.querySelector('.btn-label');
      if (label) label.textContent = 'Summary';
    }
    // No Cancel button — toggling the Summary button again hides this panel
    // (handled by the existing-panel branch above), so it'd be redundant.
    prompt.querySelector('[data-act="summary-generate"]').addEventListener('click', async (ev) => {
      ev.stopPropagation();
      prompt.remove();
      await _generateSummary(reader, data, btn);
    });
    return;
  }

  // Cached summary exists — show it immediately.
  await _generateSummary(reader, data, btn);
}

async function _generateSummary(reader, data, btn) {
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;

  const panel = document.createElement('div');
  panel.className = 'email-summary-panel';
  panel.innerHTML =
    '<div class="email-summary-header email-summary-toggle" role="button" tabindex="0">'
    +   '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>'
    +   '<span>Summary</span>'
    +   '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    + '</div>'
    + '<div class="email-summary-content"></div>';
  if (_summaryCollapsedPref()) panel.classList.add('collapsed');
  body.insertBefore(panel, body.firstChild);
  const _genToggle = panel.querySelector('.email-summary-toggle');
  if (_genToggle) {
    const _genFlip = () => {
      panel.classList.toggle('collapsed');
      _setSummaryCollapsedPref(panel.classList.contains('collapsed'));
    };
    _genToggle.addEventListener('click', (ev) => { ev.stopPropagation(); _genFlip(); });
    _genToggle.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); _genFlip(); }
    });
  }

  const sp = spinnerModule.createWhirlpool(18);
  const content = panel.querySelector('.email-summary-content');
  content.appendChild(sp.element);

  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/api/email/summarize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body: data.body,
        subject: data.subject,
        from: `${data.from_name} <${data.from_address}>`,
        // Send identifiers so the backend can fetch the raw message and
        // pull attachment text for the summary (PDFs, invoices, etc.).
        uid: data.uid || '',
        folder: state._libFolder || 'INBOX',
        message_id: data.message_id || '',
        account_id: data.account_id || '',
      }),
    });
    const result = await res.json();
    sp.destroy();
    content.innerHTML = '';
    if (result.success && result.summary) {
      content.textContent = result.summary;
      if (btn) {
        btn.classList.add('active');
        const label = btn.querySelector('.btn-label');
        if (label) label.textContent = 'Summary';
      }
    } else {
      content.innerHTML = `<span style="color:var(--red)">${_esc(result.error || 'Failed to summarize')}</span>`;
      panel.remove();
    }
  } catch (e) {
    sp.destroy();
    panel.remove();
    if (uiModule) uiModule.showError?.('Failed to summarize');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Keep an email ⋮ dropdown inside the viewport: when it would spill past the
// bottom (e.g. an email low on a phone screen), flip it above the anchor if
// there's more room up there, and cap height + scroll if it still overflows.
function _fitEmailDropdown(dropdown, rect) {
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

function _showReaderMoreMenu(em, card, reader, anchor) {
  // Toggle: if a dropdown for THIS anchor is already open, close it.
  const existing = document.querySelector('.email-card-dropdown');
  if (existing && existing._anchor === anchor) {
    existing.remove();
    anchor.classList.remove('reader-more-active');
    return;
  }
  // Otherwise close any other open dropdown (and clear its anchor's active
  // state) before opening a fresh one.
  document.querySelectorAll('.email-card-dropdown').forEach(d => {
    if (d._anchor) d._anchor.classList.remove('reader-more-active');
    d.remove();
  });

  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown';
  dropdown._anchor = anchor;
  anchor.classList.add('reader-more-active');
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:10001;min-width:180px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;`;

  const _icon = (svg) => `<span class="dropdown-icon">${svg}</span>`;
  const _unreadIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  const _archIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
  const _spamIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  const _trashIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
  const _deleteForeverIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="14" y2="15"/><line x1="14" y1="11" x2="10" y2="15"/></svg>';
  const _bellIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  const _newTabIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

  const closeAndRemove = async () => {
    // Pick the next neighbour BEFORE we re-render so we know which email to
    // jump to. Prefer the next card; fall back to the previous one if this
    // was the last card.
    const sibling = _findSiblingEmailCard(card, +1) || _findSiblingEmailCard(card, -1);
    const nextUid = sibling ? sibling.dataset.uid : null;
    await _animateEmailCardRemoval([em.uid]);
    state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
    _renderGrid();
    _libCacheWriteBack();
    if (!nextUid) return;
    // After _renderGrid, the card nodes are fresh — re-resolve and expand.
    const grid = document.getElementById('email-lib-grid');
    const nextCard = grid?.querySelector(`.doclib-card[data-uid="${CSS.escape(String(nextUid))}"]`);
    const nextEm = state._libEmails.find(e => String(e.uid) === String(nextUid));
    if (nextCard && nextEm) {
      _toggleCardPreview(nextCard, nextEm);
      nextCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  const _bubblesIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const _contactIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>';
  const actions = [
    {
      label: 'Open in new tab',
      icon: _newTabIcon,
      action: async () => {
        const folder = state._libFolder || 'INBOX';
        await _openEmailAsTab(em, folder);
      },
    },
    {
      // Save the sender to CardDAV contacts. Pulls name + address off the
      // list-item (em); falls back to splitting the local-part for a name.
      label: 'Save sender to contacts',
      icon: _contactIcon,
      action: async () => {
        const email = (em.from_address || em.from || '').trim();
        if (!email) {
          import('./ui.js').then(m => m.showError && m.showError('No sender address')).catch(() => {});
          return;
        }
        const name = (em.from_name || '').trim() || email.split('@')[0];
        try {
          const r = await fetch(`${API_BASE}/api/contacts/add`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email }),
          });
          const d = await r.json();
          import('./ui.js').then(m => {
            if (!m.showToast) return;
            if (d.success && d.message === 'Already exists') m.showToast('Already in contacts');
            else if (d.success) m.showToast('Saved to contacts');
            else m.showError && m.showError('Failed to save contact');
          }).catch(() => {});
        } catch (_) {
          import('./ui.js').then(m => m.showError && m.showError('Failed to save contact')).catch(() => {});
        }
      },
    },
    // Threaded ⇄ Plain-text view toggle removed — threaded view disabled
    // for now (too buggy). Emails always render plain text. Restore this
    // menu item + _bubblesDisabled() localStorage logic to bring it back.
    {
      label: em.is_read ? 'Mark Unread' : 'Mark Read',
      icon: _unreadIcon,
      action: async () => {
        const newRead = !em.is_read;
        _syncEmailReadState(em.uid, newRead);
        try {
          if (newRead) {
            await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          } else {
            await fetch(`${API_BASE}/api/email/mark-unread/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          }
        } catch (e) { console.error(e); }
        _renderGrid();
      },
    },
    {
      label: 'Archive',
      icon: _archIcon,
      action: async () => {
        try {
          await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        } catch (e) { console.error(e); }
        await closeAndRemove();
      },
    },
    {
      label: 'Remind to reply',
      icon: _bellIcon,
      submenu: 'remind',
    },
    {
      label: 'Move to Spam',
      icon: _spamIcon,
      action: async () => {
        try {
          await fetch(`${API_BASE}/api/email/move/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}&dest=Junk`, { method: 'POST' });
        } catch (e) { console.error(e); }
        await closeAndRemove();
      },
    },
    {
      label: 'Move to Trash',
      icon: _trashIcon,
      action: async () => {
        try {
          await fetch(`${API_BASE}/api/email/delete/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
        } catch (e) { console.error(e); }
        await closeAndRemove();
      },
    },
    {
      label: 'Delete Permanently',
      icon: _deleteForeverIcon,
      danger: true,
      action: async () => {
        const subject = em.subject || '(no subject)';
        const ok = await styledConfirm(
          `Permanently delete "${subject}"? This cannot be undone.`,
          { confirmText: 'Delete', cancelText: 'Cancel', danger: true }
        );
        if (!ok) return;
        try {
          await fetch(`${API_BASE}/api/email/delete-permanent/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
        } catch (e) { console.error(e); }
        await closeAndRemove();
      },
    },
  ];

  for (const a of actions) {
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    const arrow = a.submenu ? '<span style="margin-left:auto;opacity:0.5;">›</span>' : '';
    item.innerHTML = _icon(a.icon) + `<span>${a.label}</span>${arrow}`;
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      if (a.submenu === 'remind') {
        _showLibRemindSubmenu(em, dropdown);
        return;
      }
      dropdown.remove();
      anchor.classList.remove('reader-more-active');
      a.action();
    });
    dropdown.appendChild(item);
  }
  // Mobile-only Cancel item — explicit close for touch users. CSS hides it
  // on desktop where outside-click already dismisses cleanly.
  const _cancelIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const cancelItem = document.createElement('div');
  cancelItem.className = 'dropdown-item-compact dropdown-cancel-mobile';
  cancelItem.innerHTML = _icon(_cancelIco) + '<span>Cancel</span>';
  cancelItem.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.remove();
    anchor.classList.remove('reader-more-active');
  });
  dropdown.appendChild(cancelItem);

  document.body.appendChild(dropdown);
  _fitEmailDropdown(dropdown, rect);
  const close = (ev) => {
    if (!dropdown.contains(ev.target) && ev.target !== anchor) {
      dropdown.remove();
      anchor.classList.remove('reader-more-active');
      document.removeEventListener('click', close, true);
    }
  };
  setTimeout(() => document.addEventListener('click', close, true), 10);
}

function _showCardMenu(em, anchor) {
  document.querySelectorAll('.email-card-dropdown').forEach(d => d.remove());

  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown';
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:10001;min-width:140px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;`;

  const _icon = (svg) => `<span class="dropdown-icon">${svg}</span>`;
  const _replyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';
  const _archIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
  const _delIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
  const _unreadIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  const _checkIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  const _cardBellIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';

  const isSentFolder = /sent/i.test(state._libFolder);

  const _newTabIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
  const actions = [
    { label: 'Open', icon: _replyIcon, action: async () => {
      // Just expand inline (same as tapping the row).
      const card = anchor.closest('.doclib-card');
      if (card && !card.classList.contains('doclib-card-expanded')) {
        await _toggleCardPreview(card, em);
      }
    }},
    { label: 'Open in new tab', icon: _newTabIcon, action: async () => {
      // Open this email as its own in-app modal that registers a dock
      // chip — multiple emails can be opened simultaneously, each gets
      // its own chip in the minimized dock.
      const folder = state._libFolder || 'INBOX';
      await _openEmailAsTab(em, folder);
    }},
    { label: 'Remind to reply', icon: _cardBellIcon, submenu: 'remind' },
  ];

  if (!isSentFolder) {
    // Source of truth = the visible "active" class on the card's done
    // check, so the menu label and the actual toggle behaviour can't
    // disagree with what the user sees.
    const _cardForLabel = anchor.closest('.doclib-card');
    const _checkForLabel = _cardForLabel ? _cardForLabel.querySelector('.email-card-done') : null;
    const _currentlyDone = _checkForLabel ? _checkForLabel.classList.contains('active') : !!em.is_answered;
    actions.push({
      label: _currentlyDone ? 'Mark Not Done' : 'Mark Done',
      icon: _checkIcon,
      action: async () => {
        const card = anchor.closest('.doclib-card');
        const check = card ? card.querySelector('.email-card-done') : null;
        const wasActive = check ? check.classList.contains('active') : !!em.is_answered;
        const newState = !wasActive;
        em.is_answered = newState;
        if (newState) _syncEmailReadState(em.uid, true); // mark-done implies mark-read
        try {
          if (newState) {
            await fetch(`${API_BASE}/api/email/mark-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
            await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          } else {
            await fetch(`${API_BASE}/api/email/clear-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          }
        } catch (e) { console.error('Failed to toggle done:', e); }
        if (card) {
          if (check) check.classList.toggle('active', newState);
          if (newState) _syncEmailReadState(em.uid, true);
        }
      },
    });
    actions.push({
      label: 'Archive',
      icon: _archIcon,
      action: async () => {
        await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        await _animateEmailCardRemoval([em.uid]);
        state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
        _renderGrid();
        _libCacheWriteBack();
      },
    });
  } else {
    actions.push({
      label: 'Archive',
      icon: _archIcon,
      action: async () => {
        await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        await _animateEmailCardRemoval([em.uid]);
        state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
        _renderGrid();
        _libCacheWriteBack();
      },
    });
  }

  // "Select" — switch to multi-select mode with THIS email pre-selected so
  // the user can quickly fan-out to neighbours with the bulk bar.
  // Match the chat-sidebar Select icon — a thick bullet character reads
  // much heavier than a small SVG circle. Nudged up 2px so its visual
  // center lines up with the SVG icons above (which sit a bit higher).
  const _selectIcon = '<span style="font-size:16px;line-height:1;position:relative;top:-2px;">●</span>';
  actions.push({
    label: 'Select',
    icon: _selectIcon,
    action: () => {
      state._selectMode = true;
      state._selectedUids.add(em.uid);
      _updateBulkBar();
      _renderGrid();
    },
  });

  actions.push(
    { label: 'Delete', icon: _delIcon, danger: true, action: async () => {
      const subject = em.subject || '(no subject)';
      const ok = await styledConfirm(`Delete "${subject}"?`, { confirmText: 'Delete', cancelText: 'Cancel', danger: true });
      if (!ok) return;
      await fetch(`${API_BASE}/api/email/delete/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
      await _animateEmailCardRemoval([em.uid]);
      state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
      _renderGrid();
      _libCacheWriteBack();
    }},
  );

  for (const a of actions) {
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    const arrow = a.submenu ? '<span style="margin-left:auto;opacity:0.5;">›</span>' : '';
    item.innerHTML = _icon(a.icon) + `<span>${a.label}</span>${arrow}`;
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      if (a.submenu === 'remind') {
        _showLibRemindSubmenu(em, dropdown);
        return;
      }
      dropdown.remove();
      anchor.classList.remove('reader-more-active');
      a.action();
    });
    dropdown.appendChild(item);
  }
  // Mobile-only Cancel item — explicit close for touch users. CSS hides it
  // on desktop where outside-click already dismisses cleanly.
  const _cancelIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const cancelItem = document.createElement('div');
  cancelItem.className = 'dropdown-item-compact dropdown-cancel-mobile';
  cancelItem.innerHTML = _icon(_cancelIco) + '<span>Cancel</span>';
  cancelItem.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.remove();
    anchor.classList.remove('reader-more-active');
  });
  dropdown.appendChild(cancelItem);

  document.body.appendChild(dropdown);
  _fitEmailDropdown(dropdown, rect);
  const close = (ev) => {
    if (!dropdown.contains(ev.target) && ev.target !== anchor) {
      dropdown.remove();
      anchor.classList.remove('reader-more-active');
      document.removeEventListener('click', close, true);
    }
  };
  setTimeout(() => document.addEventListener('click', close, true), 10);
}

// Bulk "Actions" dropdown for select mode — Delete is a separate visible button.
function _showBulkActionsMenu(anchor) {
  document.querySelectorAll('.email-card-dropdown').forEach(d => d.remove());
  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown email-bulk-menu';
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:10001;min-width:160px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;left:${rect.left}px;`;
  const _readIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>';
  const _unreadIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  const items = [
    { label: 'Mark Read', icon: _readIco, action: () => _bulkAction('read') },
    { label: 'Mark Unread', icon: _unreadIco, action: () => _bulkAction('unread') },
  ];
  for (const a of items) {
    const it = document.createElement('div');
    it.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    it.innerHTML = `<span class="dropdown-icon">${a.icon}</span><span>${a.label}</span>`;
    it.addEventListener('click', (e) => { e.stopPropagation(); dropdown.remove(); a.action(); });
    dropdown.appendChild(it);
  }
  // Mobile-only Cancel — matches the per-card and sidebar dropdowns.
  const _cancelIco2 = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const cancelIt = document.createElement('div');
  cancelIt.className = 'dropdown-item-compact dropdown-cancel-mobile';
  cancelIt.innerHTML = `<span class="dropdown-icon">${_cancelIco2}</span><span>Cancel</span>`;
  cancelIt.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.remove();
    // Cancel inside the bulk-Actions menu also exits select mode — matches the
    // documents bulk dropdown.
    state._selectMode = false;
    state._selectedUids.clear();
    _updateBulkBar();
    _renderGrid();
  });
  dropdown.appendChild(cancelIt);
  document.body.appendChild(dropdown);
  _fitEmailDropdown(dropdown, rect);
  const close = (ev) => {
    if (!dropdown.contains(ev.target) && ev.target !== anchor) {
      dropdown.remove();
      document.removeEventListener('click', close, true);
    }
  };
  setTimeout(() => document.addEventListener('click', close, true), 10);
}

function _updateBulkBar() {
  const bar = document.getElementById('email-lib-bulk');
  const selectBtn = document.getElementById('email-lib-select-btn');
  if (bar) bar.classList.toggle('hidden', !state._selectMode);
  if (selectBtn) {
    selectBtn.textContent = state._selectMode ? 'Cancel' : 'Select';
    selectBtn.classList.toggle('active', state._selectMode);
  }
  const count = document.getElementById('email-lib-selected-count');
  if (count) count.textContent = `${state._selectedUids.size} Selected`;
  const all = document.getElementById('email-lib-select-all');
  if (all) all.checked = state._libEmails.length > 0 && state._libEmails.every(e => state._selectedUids.has(e.uid));
  // When something's selected, brighten Actions to the same full --fg color as
  // the "N Selected" count (the button is a dimmer 60% --fg by default).
  const actions = document.getElementById('email-lib-bulk-actions');
  if (actions) actions.style.color = state._selectedUids.size > 0 ? 'var(--fg)' : '';
  const deleteBtn = document.getElementById('email-lib-bulk-delete');
  if (deleteBtn) deleteBtn.style.color = state._selectedUids.size > 0 ? 'var(--red)' : '';
}

async function _bulkAction(action) {
  const uids = Array.from(state._selectedUids);
  if (uids.length === 0) return;
  let failedReadSync = 0;
  if (action === 'delete') {
    const ok = await styledConfirm(
      `Delete ${uids.length} selected email${uids.length === 1 ? '' : 's'}?`,
      { confirmText: 'Delete', cancelText: 'Cancel', danger: true },
    );
    if (!ok) return;
  }

  for (const uid of uids) {
    try {
      if (action === 'archive') {
        await fetch(`${API_BASE}/api/email/archive/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
      } else if (action === 'delete') {
        await fetch(`${API_BASE}/api/email/delete/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
      } else if (action === 'read' || action === 'unread') {
        const endpoint = action === 'read' ? 'mark-read' : 'mark-unread';
        const res = await fetch(`${API_BASE}/api/email/${endpoint}/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        let data = null;
        try { data = await res.json(); } catch (_) {}
        if (!res.ok || data?.success === false) {
          throw new Error(data?.error || `HTTP ${res.status}`);
        }
        _syncEmailReadState(uid, action === 'read');
      }
    } catch (e) {
      if (action === 'read' || action === 'unread') failedReadSync += 1;
      console.error(`Failed to ${action} ${uid}:`, e);
    }
  }

  if (action === 'archive' || action === 'delete') {
    await _animateEmailCardRemoval(uids);
    const removed = new Set(uids.map(uid => String(uid)));
    state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
  }
  state._selectedUids.clear();
  state._selectMode = false;
  _updateBulkBar();
  _renderGrid();
  if (failedReadSync > 0) {
    showToast(`Failed to update ${failedReadSync} email${failedReadSync === 1 ? '' : 's'}`);
  }
  // Sync successful local mutations into the SWR cache so reopen doesn't
  // briefly show the pre-bulk state.
  _libCacheWriteBack();
}

// _extractName lives in ./emailLibrary/utils.js

function _aiReplyIcon(data) {
  const cachedSpark = data?.cached_ai_reply
    ? '<path d="M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" fill="var(--accent-primary, var(--red))" stroke="none" transform="translate(2 0)"/>'
    : '';
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>${cachedSpark}</svg>`;
}

function _summaryIcon(data) {
  const fill = data?.cached_summary ? 'var(--accent-primary, var(--red))' : 'currentColor';
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="${fill}"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>`;
}

async function _runAiReplyFromButton(btn, em, data, mode) {
  _snapEmailModalToLeftSidebar(btn.closest('.modal'));
  btn.disabled = true;
  const orig = btn.innerHTML;
  let wp = null;
  try {
    wp = spinnerModule.createWhirlpool(14);
    wp.element.style.cssText = 'width:14px;height:14px;display:inline-block;vertical-align:middle;position:relative;top:-2px;';
    btn.innerHTML = '';
    btn.appendChild(wp.element);
  } catch (_) {}
  try {
    if (state._onEmailClick) await state._onEmailClick({ email: em, emailData: data, mode });
  } finally {
    try { wp && wp.stop(); } catch (_) {}
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

function _closeAiReplyChoice() {
  document.querySelectorAll('.email-ai-reply-choice').forEach(el => el.remove());
  document.removeEventListener('click', _closeAiReplyChoice, true);
}

function _showAiReplyChoice(btn, em, data) {
  _closeAiReplyChoice();
  const rect = btn.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'email-ai-reply-choice';
  menu.style.cssText = [
    'position:fixed',
    `left:${Math.max(8, Math.min(rect.left, window.innerWidth - 190))}px`,
    `top:${Math.min(window.innerHeight - 96, rect.bottom + 6)}px`,
    'z-index:10060',
    'display:flex',
    'gap:6px',
    'padding:6px',
    'background:var(--bg,#111)',
    'border:1px solid var(--border,#333)',
    'border-radius:7px',
    'box-shadow:0 8px 24px rgba(0,0,0,.28)',
  ].join(';');
  menu.innerHTML = `
    <button class="memory-toolbar-btn" data-mode="ai-reply-fast" title="Shorter, faster draft">Fast</button>
    <button class="memory-toolbar-btn" data-mode="ai-reply-full" title="Uses the fuller reply context">Full</button>
  `;
  menu.addEventListener('click', async (ev) => {
    const choice = ev.target.closest('[data-mode]');
    if (!choice) return;
    ev.preventDefault();
    ev.stopPropagation();
    const mode = choice.getAttribute('data-mode') || 'ai-reply';
    _closeAiReplyChoice();
    await _runAiReplyFromButton(btn, em, data, mode);
  });
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', _closeAiReplyChoice, true), 0);
}

function _handleAiReplyButton(ev, em, data) {
  ev.stopPropagation();
  const btn = ev.currentTarget;
  if (data?.cached_ai_reply) {
    _runAiReplyFromButton(btn, em, data, 'ai-reply');
    return;
  }
  _showAiReplyChoice(btn, em, data);
}

function _hasMultipleRecipients(data) {
  // Count distinct addresses in To + Cc (minus the current user). Empty
  // fallback when the user's address isn't yet known — no exclusion.
  const myAddress = (window._myEmailAddress || '').toLowerCase();
  const extractEmails = (str) => {
    if (!str) return [];
    return str.split(',')
      .map(s => {
        const m = s.match(/<([^>]+)>/);
        return (m ? m[1] : s).trim().toLowerCase();
      })
      .filter(e => e && e !== myAddress);
  };
  const recipients = new Set([
    ...extractEmails(data.to),
    ...extractEmails(data.cc),
  ]);
  // Sender counts as one other person too
  if (data.from_address && data.from_address.toLowerCase() !== myAddress) {
    recipients.add(data.from_address.toLowerCase());
  }
  return recipients.size > 1;
}

// _esc lives in ./emailLibrary/utils.js

// ---- Reminder submenu (used by both email menus) ----
function _showLibRemindSubmenu(em, parentDropdown) {
  parentDropdown.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'dropdown-item-compact';
  header.style.cssText = 'opacity:0.5;font-size:10px;pointer-events:none;text-transform:uppercase;letter-spacing:0.5px;padding-top:6px;';
  header.innerHTML = '<span>Remind me</span>';
  parentDropdown.appendChild(header);

  const now = new Date();
  const laterToday = new Date(now);
  const sixPm = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0);
  if (sixPm - now < 60*60*1000) laterToday.setTime(now.getTime() + 3*60*60*1000);
  else laterToday.setTime(sixPm.getTime());
  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate()+1); tomorrow.setHours(8,0,0,0);
  const daysUntilMon = (8 - now.getDay()) % 7 || 7;
  const nextWeek = new Date(now); nextWeek.setDate(now.getDate()+daysUntilMon); nextWeek.setHours(8,0,0,0);

  const presets = [
    { label: 'Later today', sub: laterToday.toLocaleTimeString([], { hour:'numeric', minute:'2-digit' }), date: laterToday },
    { label: 'Tomorrow', sub: tomorrow.toLocaleTimeString([], { hour:'numeric', minute:'2-digit' }), date: tomorrow },
    { label: 'Next week', sub: nextWeek.toLocaleDateString([], { weekday:'short' }) + ' ' + nextWeek.toLocaleTimeString([], { hour:'numeric', minute:'2-digit' }), date: nextWeek },
  ];
  for (const p of presets) {
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact';
    item.innerHTML = `<span>${p.label}</span><span style="margin-left:auto;opacity:0.5;font-size:10px;">${p.sub}</span>`;
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      parentDropdown.remove();
      await _createEmailReplyReminder(em, p.date);
    });
    parentDropdown.appendChild(item);
  }
  const customItem = document.createElement('div');
  customItem.className = 'dropdown-item-compact';
  customItem.innerHTML = '<span>Pick date and time…</span>';
  customItem.addEventListener('click', (e) => {
    e.stopPropagation();
    parentDropdown.remove();
    const tmp = document.createElement('input');
    tmp.type = 'datetime-local';
    const def = new Date(tomorrow);
    const pad = n => String(n).padStart(2,'0');
    tmp.value = `${def.getFullYear()}-${pad(def.getMonth()+1)}-${pad(def.getDate())}T${pad(def.getHours())}:${pad(def.getMinutes())}`;
    tmp.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:99999;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;';
    document.body.appendChild(tmp);
    tmp.focus();
    if (typeof tmp.showPicker === 'function') { try { tmp.showPicker(); } catch {} }
    tmp.addEventListener('change', async () => {
      if (tmp.value) await _createEmailReplyReminder(em, new Date(tmp.value));
      tmp.remove();
    });
    tmp.addEventListener('blur', () => setTimeout(() => tmp.remove(), 200));
  });
  parentDropdown.appendChild(customItem);
}

async function _createEmailReplyReminder(em, dueDate) {
  const pad = n => String(n).padStart(2,'0');
  const iso = `${dueDate.getFullYear()}-${pad(dueDate.getMonth()+1)}-${pad(dueDate.getDate())}T${pad(dueDate.getHours())}:${pad(dueDate.getMinutes())}`;
  const fullFrom = em.from || em.sender || '';
  // Extract just the first name from "First Last <email@x>" or fall back to email local part
  let from = 'someone';
  if (fullFrom) {
    const fullName = _extractName(fullFrom);
    if (fullName) {
      // Strip quotes, take the first whitespace-separated word, capitalize
      const first = fullName.replace(/^["']|["']$/g, '').trim().split(/[\s,]+/)[0] || '';
      if (first) from = first.charAt(0).toUpperCase() + first.slice(1);
    }
  }
  const subject = em.subject || '(no subject)';
  const folder = state._libFolder || 'INBOX';
  const deepLink = `${window.location.origin}/#email=${encodeURIComponent(folder)}:${em.uid}`;
  const payload = {
    title: `Reply: ${subject}`,
    note_type: 'todo',
    items: [
      { text: `Reply to ${from}: ${subject}`, checked: false },
    ],
    content: `Open email: ${deepLink}`,
    label: 'email reminder',
    due_date: iso,
    source: 'email',
  };
  try {
    const res = await fetch(`${API_BASE}/api/notes`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Failed');
    const { showToast } = await import('./ui.js');
    const fmt = dueDate.toLocaleString([], { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
    showToast(`Todo reminder set for ${fmt}`);
    if ('Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch {}
    }
  } catch (e) {
    const { showError } = await import('./ui.js');
    showError('Failed to create reminder');
  }
}

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
