/**
 * emailInbox.js — Email inbox list in sidebar.
 * Follows the session list pattern: list items, click to open as document, archive, etc.
 */

import spinnerModule from './spinner.js';
import sessionModule from './sessions.js';
import { initEmailLibrary, openEmailLibrary, closeEmailLibrary, isOpen as isLibOpen, prewarmEmailLibrary } from './emailLibrary.js';
import * as Modals from './modalManager.js';
import { applyEdgeDock } from './modalSnap.js';
import { buildReplyAllCc } from './emailLibrary/replyRecipients.js';

const API_BASE = window.location.origin;
const _acct = () => window.__odysseusActiveEmailAccount
  ? `&account_id=${encodeURIComponent(window.__odysseusActiveEmailAccount)}`
  : '';

const _emailSetupHint = () => '<div style="margin-top:6px;opacity:0.72;font-size:11px;">Setup: <span style="color:var(--accent,var(--red));">Settings &rsaquo; Integrations</span></div>';

// SVG icons matching sessions.js dropdown style
const _replyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';
const _archiveIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
const _deleteIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
const _unreadIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
const _starIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
const _starFilledIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
const _bellIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
const _icon = (svg) => `<span class="dropdown-icon">${svg}</span>`;
const _replySeparator = '---------- Previous message ----------';

function _cleanAiReplyText(text) {
  if (!text) return '';
  let t = String(text);
  const open = /<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/i;
  const close = /<<<\s*END\s*>>+/i;
  const m = open.exec(t);
  if (m) {
    const rest = t.slice(m.index + m[0].length);
    const c = close.exec(rest);
    t = c ? rest.slice(0, c.index) : rest;
  }
  return t
    .replace(/<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/gi, '')
    .replace(/<<<\s*END\s*>>+/gi, '')
    .trim();
}

function _shouldUseFastAiReply(data) {
  const body = String(data?.body || data?.body_html || '');
  const subject = String(data?.subject || '');
  const atts = Array.isArray(data?.attachments) ? data.attachments : [];
  if (atts.length > 0) return false;
  const text = `${subject}\n${body}`.toLowerCase();
  if (/\b(attach(?:ed|ment)?|pdf|document|contract|invoice|receipt|quote|estimate|proposal|question|questions|details|schedule|booking|reservation|meeting|calendar|availability|confirm|confirmation|review|sign|signature)\b/.test(text)) {
    return false;
  }
  return body.length < 2500;
}

let _emails = [];
let _currentFolder = 'INBOX';
let _offset = 0;
let _total = 0;

// Replying to an email marks the source \Answered server-side and fires
// `email-answered`. Reflect it live in the inbox list so it shows as done
// immediately (no manual refresh needed).
window.addEventListener('email-answered', (e) => {
  const uid = e.detail && e.detail.uid;
  if (uid == null) return;
  const em = _emails.find(x => String(x.uid) === String(uid));
  if (em) { em.is_answered = true; em.is_read = true; }
  document.querySelectorAll('.email-item[data-uid="' + CSS.escape(String(uid)) + '"]').forEach(item => {
    item.classList.remove('email-unread');
    const check = item.querySelector('.email-done-check');
    if (check) check.classList.add('active');
  });
});
let _loading = false;
let _expanded = false;
let _docModule = null;
let _listSpinner = null;
let _senderFilter = null;       // email address (lowercased) to filter by, or null
let _senderFilterLabel = null;  // display label for the active filter chip

export function init(documentModule) {
  _docModule = documentModule;
  _bindEvents();
  // Init the library popup with a callback to open emails
  initEmailLibrary({
    documentModule,
    onEmailClick: async (opts) => {
      // Reply / AI Reply / Compose open a draft in the doc editor.
      //  - Desktop: dock the email to the LEFT so it stays visible beside the
      //    reply draft (which opens on the right) — read-while-you-reply.
      //  - Mobile: there's no room for a split, so minimize the email modal;
      //    the draft comes to the front and the inbox stays a tap away as a
      //    minimized chip.
      // Never call closeEmailLibrary() here — that destroys state.
      try {
        if (Modals.isRegistered('email-lib-modal')) {
          const emailModal = document.getElementById('email-lib-modal');
          if (window.innerWidth > 768 && emailModal && !emailModal.classList.contains('hidden')) {
            applyEdgeDock(emailModal, 'left');
          }
          // Mobile: do NOT pre-mount the pane here. The load path (open/inject)
          // mounts it exactly once when the doc is ready; the doc-view z-index
          // rule slides it up OVER the email (which stays behind). Pre-mounting
          // here caused a double-mount — the early pane was torn down by the
          // compose session-switch, then remounted, which looked like a doc
          // flashing before the smooth slide.
        }
      } catch (_) {}
      if (opts.compose) { _composeNew(); return; }
      if (opts.email) {
        await _openEmail(opts.email, null, opts.emailData, opts.mode || 'reply');
      }
    },
  });
  _watchDocOpenToReDockEmail();
}

export async function openReplyDraft(uid, folder = 'INBOX', mode = 'reply') {
  if (!uid) return;
  const previousFolder = _currentFolder;
  _currentFolder = folder || 'INBOX';
  try {
    await _openEmail({ uid: String(uid), subject: '' }, null, null, mode || 'reply');
  } finally {
    _currentFolder = previousFolder || _currentFolder;
  }
}

// When the document editor pane opens (body.doc-view turns on), make sure the
// email modal is on the LEFT — even if it was previously docked RIGHT or
// floating — so the email and the doc always end up side-by-side. The actual
// width math lives in modalSnap.js (`_anchorLeftDock` shrinks the email when
// the doc is rendered to the right).
let _docOpenObs = null;
function _watchDocOpenToReDockEmail() {
  if (_docOpenObs) return;
  if (typeof MutationObserver === 'undefined') return;
  let last = document.body.classList.contains('doc-view');
  _docOpenObs = new MutationObserver(() => {
    const cur = document.body.classList.contains('doc-view');
    if (cur && !last) {
      if (window.innerWidth > 768) {
        const emailModal = document.getElementById('email-lib-modal');
        if (emailModal && !emailModal.classList.contains('hidden')) {
          // Already left-docked → nothing to do (modalSnap re-anchors on its own).
          if (!emailModal.classList.contains('modal-left-docked')) {
            try { applyEdgeDock(emailModal, 'left'); } catch (_) {}
          }
        }
        // Same treatment for an open email-reader modal (one specific email
        // open standalone — typical "click email, click doc" flow).
        document.querySelectorAll('.modal[id^="email-reader-"]').forEach(m => {
          if (m.classList.contains('hidden')) return;
          if (m.classList.contains('modal-left-docked')) return;
          try { applyEdgeDock(m, 'left'); } catch (_) {}
        });
      }
    }
    last = cur;
  });
  _docOpenObs.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}

function _bindEvents() {
  // Clicking anywhere in the email section header opens the popup
  // (except the compose button which has its own handler)
  const section = document.getElementById('email-section');
  const header = section?.querySelector('.section-header-flex');
  if (header) {
    header.style.cursor = 'pointer';
    header.addEventListener('click', (e) => {
      if (e.target.closest('#email-compose-btn')) return;
      openEmailLibrary();
      markInboxAsSeen();
    });
  }

  // Compose button creates a new email document
  const composeBtn = document.getElementById('email-compose-btn');
  if (composeBtn) {
    composeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _composeNew();
    });
  }

  // Initial unread count check, refresh every 60s
  _refreshUnreadCount();
  setInterval(_refreshUnreadCount, 60000);
  prewarmEmailLibrary({ delay: 3000 });

  // Deep-link: #email=<folder>:<uid> opens the library and expands that card
  _maybeOpenFromHash();
  window.addEventListener('hashchange', _maybeOpenFromHash);
}

function _maybeOpenFromHash() {
  const h = window.location.hash || '';
  const m = h.match(/^#email=([^:]+):(\d+)/);
  if (!m) return;
  const folder = decodeURIComponent(m[1]);
  const uid = m[2];
  try { openEmailLibrary({ folder, uid }); } catch (e) { console.error(e); }
  // Clear the hash so reloads don't reopen
  try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (_) {}
}

// Tint helper — turns the urgent-email-scanner's max_score into a dot color.
// Falls back to the default (blue / unset) when scanner is off or no urgent.
function _urgencyColor(score) {
  if (score >= 3) return 'var(--color-error, #e06c75)';   // red — urgent now
  if (score === 2) return '#f0ad4e';                       // orange — reply soon
  return '';                                                // default (blue / theme)
}

async function _refreshUnreadCount() {
  // Default the dot to hidden — only the verified "new mail above threshold"
  // path below should turn it on. Without this, a fetch error or a backend
  // returning malformed data left a stale dot from a previous account/session.
  const dot = document.getElementById('email-unread-dot');
  if (dot && !dot._stickyState) dot.style.display = 'none';
  try {
    // Parallel: unread list + urgency state.
    const [listRes, urgRes] = await Promise.all([
      fetch(`${API_BASE}/api/email/list?folder=INBOX&limit=50&filter=unread${_acct()}`),
      fetch(`${API_BASE}/api/email/urgency-state`, { credentials: 'same-origin' }).catch(() => null),
    ]);
    if (!listRes || !listRes.ok) return;
    const data = await listRes.json();
    if (!dot) return;

    const emails = data.emails || [];
    if (emails.length === 0) {
      dot.style.display = 'none';
      return;
    }

    // Compare highest unread UID to the last-seen threshold in localStorage
    const lastSeen = parseInt(localStorage.getItem('odysseus-email-last-seen-uid') || '0', 10);
    const maxUid = Math.max(...emails.map(e => parseInt(e.uid, 10) || 0));

    // Only show dot if there's a new email above the threshold
    dot.style.display = maxUid > lastSeen ? '' : 'none';

    // Color the dot by urgency tier. Cache the per-uid map so the per-row
    // renderer can reuse it without a second fetch.
    if (dot.style.display !== 'none' && urgRes && urgRes.ok) {
      try {
        const ud = await urgRes.json();
        window._emailUrgencyState = ud;
        const tint = _urgencyColor(ud.max_score || 0);
        if (tint) dot.style.backgroundColor = tint;
        else dot.style.backgroundColor = '';
      } catch (_) {}
    } else if (dot.style.display !== 'none') {
      dot.style.backgroundColor = '';
    }
  } catch (e) {
    // Network/parse error — keep the dot hidden (default at the top).
    if (dot) dot.style.display = 'none';
  }
}

export function markInboxAsSeen() {
  // Called when the user opens the inbox popup — clears the notif dot
  try {
    // Find current max UID so subsequent arrivals trigger the dot
    fetch(`${API_BASE}/api/email/list?folder=INBOX&limit=1${_acct()}`)
      .then(r => r.json())
      .then(data => {
        const emails = data.emails || [];
        if (emails.length > 0) {
          const maxUid = Math.max(...emails.map(e => parseInt(e.uid, 10) || 0));
          localStorage.setItem('odysseus-email-last-seen-uid', String(maxUid));
        }
        const dot = document.getElementById('email-unread-dot');
        if (dot) dot.style.display = 'none';
      })
      .catch(() => {});
  } catch (e) {}
}

export async function loadEmails(append = false) {
  if (_loading) return;
  _loading = true;

  const list = document.getElementById('email-list');
  if (!list) { _loading = false; return; }

  if (!append) {
    list.innerHTML = '';
    // Show whirlpool spinner
    if (_listSpinner) { _listSpinner.destroy(); _listSpinner = null; }
    const sp = spinnerModule.createWhirlpool(20);
    _listSpinner = sp;
    list.appendChild(sp.element);
  }

  try {
    const fromQS = _senderFilter ? `&from=${encodeURIComponent(_senderFilter)}` : '';
    const res = await fetch(`${API_BASE}/api/email/list?folder=${encodeURIComponent(_currentFolder)}&limit=50&offset=${_offset}${fromQS}${_acct()}&_=${Date.now()}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    if (!append) _emails = [];
    _emails.push(...(data.emails || []));
    _total = data.total || 0;

    // Remove spinner
    if (_listSpinner) { _listSpinner.destroy(); _listSpinner = null; }

    _renderList();

    const unreadCount = _emails.filter(e => !e.is_read).length;
    const dot = document.getElementById('email-unread-dot');
    if (dot) dot.style.display = unreadCount > 0 ? '' : 'none';
  } catch (e) {
    console.error('Failed to load emails:', e);
    if (_listSpinner) { _listSpinner.destroy(); _listSpinner = null; }
    if (!append && list) {
      const msg = e && e.message ? `Failed to load: ${e.message}` : 'Failed to load';
      list.innerHTML = `<div class="email-loading">${msg.replace(/&/g, '&amp;').replace(/</g, '&lt;')}${_emailSetupHint()}</div>`;
    }
  } finally {
    _loading = false;
  }
}

async function loadFolders() {
  try {
    const res = await fetch(`${API_BASE}/api/email/folders?_=1${_acct()}`);
    const data = await res.json();
    const select = document.getElementById('email-folder-select');
    if (!select || !data.folders) return;
    _populateFolderSelect(select, data.folders);
  } catch (e) {
    console.error('Failed to load folders:', e);
  }
}

export function sortedFolders(folders) {
  const roleOf = (folder) => {
    const f = String(folder || '').toLowerCase();
    if (f === 'inbox') return 'inbox';
    if (f.includes('sent')) return 'sent';
    if (f.includes('starred') || f.includes('flagged')) return 'starred';
    if (f.includes('draft')) return 'drafts';
    if (f.includes('all mail') || f.includes('archive')) return 'archive';
    if (f.includes('spam') || f.includes('junk')) return 'junk';
    if (f.includes('trash') || f.includes('bin') || f.includes('deleted')) return 'trash';
    return '';
  };
  const roleOrder = ['inbox', 'sent', 'starred', 'archive', 'junk', 'trash', 'drafts'];
  const found = new Map();
  const others = [];
  for (const f of folders) {
    const role = roleOf(f);
    if (role && !found.has(role)) found.set(role, f);
    else others.push(f);
  }
  return { priority: roleOrder.map(role => found.get(role)).filter(Boolean), others };
}

export function folderDisplayName(folder) {
  const raw = String(folder || '');
  const f = raw.toLowerCase();
  if (f === 'inbox') return 'INBOX';
  if (f.includes('all mail')) return 'Archive / All Mail';
  if (f.includes('archive')) return 'Archive';
  if (f.includes('spam')) return 'Spam';
  if (f.includes('junk')) return 'Junk';
  if (f.includes('trash') || f.includes('bin') || f.includes('deleted')) return 'Trash';
  if (f.includes('sent')) return 'Sent';
  if (f.includes('draft')) return 'Drafts';
  return raw;
}

function _populateFolderSelect(select, folders) {
  select.innerHTML = '';
  const { priority, others } = sortedFolders(folders);

  for (const folder of priority) {
    const opt = document.createElement('option');
    opt.value = folder;
    opt.textContent = folderDisplayName(folder);
    if (folder === _currentFolder) opt.selected = true;
    select.appendChild(opt);
  }

  if (priority.length > 0 && others.length > 0) {
    const sep = document.createElement('option');
    sep.disabled = true;
    sep.textContent = '─────────';
    select.appendChild(sep);
  }

  for (const folder of others) {
    const opt = document.createElement('option');
    opt.value = folder;
    opt.textContent = folderDisplayName(folder);
    if (folder === _currentFolder) opt.selected = true;
    select.appendChild(opt);
  }
}

function _renderList() {
  const list = document.getElementById('email-list');
  if (!list) return;
  list.innerHTML = '';

  if (_senderFilter) {
    const chip = document.createElement('div');
    chip.className = 'email-filter-chip';
    chip.innerHTML = `<span class="email-filter-chip-label">From: ${_esc(_senderFilterLabel || _senderFilter)}</span><button class="email-filter-chip-clear" title="Clear filter">&times;</button>`;
    chip.querySelector('.email-filter-chip-clear').addEventListener('click', () => _clearSenderFilter());
    list.appendChild(chip);
  }

  if (_emails.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'email-loading';
    empty.textContent = _senderFilter ? `No emails from ${_senderFilterLabel || _senderFilter}` : 'No emails';
    list.appendChild(empty);
    return;
  }

  for (const em of _emails) {
    list.appendChild(_createEmailItem(em));
  }

  const loadMore = document.getElementById('email-load-more');
  if (loadMore) {
    loadMore.style.display = (_emails.length < _total) ? '' : 'none';
  }
}

function _setSenderFilter(addr, label) {
  _senderFilter = addr;
  _senderFilterLabel = label || addr;
  _offset = 0;
  loadEmails(false);
}

function _clearSenderFilter() {
  _senderFilter = null;
  _senderFilterLabel = null;
  _offset = 0;
  loadEmails(false);
}

function _createEmailItem(em) {
  const item = document.createElement('div');
  item.className = 'list-item email-item' + (em.is_spam_verdict ? ' email-item-spam' : '');
  item.setAttribute('role', 'option');
  item.setAttribute('data-uid', em.uid);

  let dateStr = '';
  if (em.date) {
    try {
      const d = new Date(em.date);
      const now = new Date();
      const isToday = d.toDateString() === now.toDateString();
      if (isToday) {
        dateStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      } else {
        dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
      }
    } catch (_) {}
  }

  const senderName = em.from_name || em.from_address;
  const initial = (senderName || '?')[0].toUpperCase();
  const color = _senderColor(senderName);

  const attachIcon = em.has_attachments
    ? '<span title="Has attachments" style="opacity:0.6;display:inline-flex;flex-shrink:0;margin-left:4px;"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></span>'
    : '';

  // Per-row dot tint: if the urgency scanner flagged this UID, override the
  // per-sender pastel with red (3) / orange (2). Look up by any cached key
  // ending in `:<uid>` since the per_uid map is keyed `<account_id>:<uid>`
  // and the inbox list doesn't surface the account id per row.
  let _unreadColor = color;
  let _unreadTitle = 'Unread';
  try {
    const us = window._emailUrgencyState;
    if (us && us.per_uid && em.uid != null) {
      const suffix = ':' + String(em.uid);
      for (const k of Object.keys(us.per_uid)) {
        if (k.endsWith(suffix)) {
          const v = us.per_uid[k] || {};
          const score = v.score || 0;
          if (score >= 3) { _unreadColor = 'var(--color-error, #e06c75)'; _unreadTitle = 'Urgent — ' + (v.reason || 'needs reply now'); }
          else if (score === 2) { _unreadColor = '#f0ad4e'; _unreadTitle = 'Reply soon — ' + (v.reason || ''); }
          break;
        }
      }
    }
  } catch (_) {}
  const unreadIcon = (!em.is_read && !em.is_answered)
    ? `<span class="email-unread-dot-inline" title="${_esc(_unreadTitle)}" style="display:inline-flex;align-items:center;flex-shrink:0;margin-left:4px;color:${_unreadColor}"><svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg></span>`
    : '';

  const tags = Array.isArray(em.tags) ? em.tags : [];
  const tagPills = tags.length
    ? `<span class="email-tags">${tags.map(t => `<span class="email-tag email-tag-${_esc(t)}">${_esc(t)}</span>`).join('')}</span>`
    : '';

  const spamTag = em.is_spam_verdict
    ? `<span class="email-tag email-tag-spam" title="AI flagged as spam — click ✓ to unflag">spam <button class="email-spam-unflag" data-uid="${em.uid}" title="Not spam">\u2713</button></span>`
    : '';

  const senderAddr = (em.from_address || '').toLowerCase();
  item.innerHTML = `
    <span class="email-avatar" style="background:${color}">${initial}</span>
    <div class="email-item-content">
      <div class="email-item-top">
        <span class="email-sender email-sender-clickable" style="color:${color}" data-from-addr="${_esc(senderAddr)}" data-from-name="${_esc(senderName)}" title="Show all emails from ${_esc(senderName)}">${_esc(senderName)}</span>
        <span class="email-date">${_esc(dateStr)}</span>
      </div>
      <div class="email-subject">${_esc(em.subject)}${unreadIcon}${attachIcon}${tagPills}${spamTag}</div>
    </div>
    <div class="email-menu-wrap">
      <button class="hamburger email-menu-btn" title="Actions">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
      </button>
    </div>
  `;

  // Click sender name → filter list to that sender
  const senderEl = item.querySelector('.email-sender-clickable');
  if (senderEl) {
    senderEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const addr = senderEl.dataset.fromAddr || '';
      const name = senderEl.dataset.fromName || addr;
      if (addr) _setSenderFilter(addr, name);
    });
  }

  // Wire the "not spam" button
  const unflagBtn = item.querySelector('.email-spam-unflag');
  if (unflagBtn) {
    unflagBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        await fetch(`${API_BASE}/api/email/${em.uid}/unflag-spam`, {
          method: 'POST', credentials: 'same-origin',
        });
        em.is_spam_verdict = false;
        item.classList.remove('email-item-spam');
        const tag = item.querySelector('.email-tag-spam');
        if (tag) tag.remove();
      } catch (_) {}
    });
  }

  // Click to open — do NOT close sidebar
  item.addEventListener('click', (e) => {
    if (e.target.closest('.email-menu-wrap')) return;
    if (item.dataset.swipeBlock === '1') return;
    _openEmail(em, item);
  });

  const menuWrap = item.querySelector('.email-menu-wrap');
  menuWrap.addEventListener('click', (e) => {
    e.stopPropagation();
    _showEmailMenu(em, menuWrap, item);
  });

  // Swipe left to archive (mobile). Mirrors sidebar-layout.js swipe pattern.
  if ('ontouchstart' in window) {
    let startX = 0, startY = 0, dx = 0, dy = 0, swiping = false, swiped = false;
    const HORIZ_THRESHOLD = 70; // px to trigger archive
    const VERT_CANCEL = 30;     // px vertical motion cancels swipe (treat as scroll)

    item.addEventListener('touchstart', (e) => {
      if (e.target.closest('.email-menu-wrap')) return;
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY;
      dx = 0; dy = 0; swiping = true; swiped = false;
      item.style.transition = 'none';
    }, { passive: true });

    item.addEventListener('touchmove', (e) => {
      if (!swiping) return;
      const t = e.touches[0];
      dx = t.clientX - startX;
      dy = t.clientY - startY;
      if (Math.abs(dy) > VERT_CANCEL) {
        // Vertical scroll — cancel swipe
        swiping = false;
        item.style.transform = '';
        return;
      }
      if (dx < 0) {
        // Only swipe-left for archive; clamp at -160 so it doesn't fly off
        const offset = Math.max(dx, -160);
        item.style.transform = `translateX(${offset}px)`;
        item.style.background = `linear-gradient(to right, transparent, transparent ${100 + offset/1.6}%, var(--red) ${100 + offset/1.6}%)`;
      }
    }, { passive: true });

    item.addEventListener('touchend', () => {
      if (!swiping) return;
      swiping = false;
      item.style.transition = 'transform 0.2s ease, opacity 0.2s ease';
      if (dx <= -HORIZ_THRESHOLD) {
        // Trigger archive — animate off-screen, suppress next click
        swiped = true;
        item.dataset.swipeBlock = '1';
        item.style.transform = 'translateX(-100%)';
        item.style.opacity = '0';
        setTimeout(() => {
          _archiveEmail(em);
          delete item.dataset.swipeBlock;
        }, 200);
      } else {
        // Snap back
        item.style.transform = '';
        item.style.background = '';
      }
    });

    item.addEventListener('touchcancel', () => {
      swiping = false;
      item.style.transition = 'transform 0.2s ease';
      item.style.transform = '';
      item.style.background = '';
    });
  }

  return item;
}

async function _openEmail(em, itemEl, preloadedData = null, mode = 'reply') {
  const aiReplyMode = mode === 'ai-reply-fast' ? 'fast' : (mode === 'ai-reply-full' ? 'full' : '');
  const wantsAiReply = mode === 'ai-reply' || !!aiReplyMode;
  let aiSuggestedBody = null;
  if (wantsAiReply) {
    // Fall through to reply-all (not plain reply) so the generated AI
    // draft addresses everyone on the original thread. On single-
    // recipient emails this collapses to a regular reply since there's
    // no one else to CC.
    mode = 'reply-all';
  }
  // Show whirlpool spinner on the right side of the item (only if from sidebar)
  let spinner = null;
  if (itemEl) {
    const sp = spinnerModule.createWhirlpool(16);
    spinner = sp;
    sp.element.style.cssText = 'margin:0;flex-shrink:0;';
    const menuWrap = itemEl.querySelector('.email-menu-wrap');
    if (menuWrap) menuWrap.style.display = 'none';
    itemEl.appendChild(sp.element);
  }

  try {
    let data = preloadedData;
    if (!data) {
      const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`);
      data = await res.json();
    }
    if (data.error) {
      console.error('Failed to read email:', data.error);
      return;
    }
    if (wantsAiReply) {
      if (data.cached_ai_reply) {
        aiSuggestedBody = _cleanAiReplyText(data.cached_ai_reply);
      } else {
        let draftToastTimer = null;
        draftToastTimer = setTimeout(() => {
          import('./ui.js').then(m => m.showToast && m.showToast('Drafting AI reply', { duration: 3000, leadingIcon: 'spinner' })).catch(() => {});
        }, 450);
        try {
          let currentModel = '';
          let currentSessionId = '';
          try {
            currentModel = sessionModule?.getCurrentModel() || '';
            currentSessionId = sessionModule?.getCurrentSessionId() || '';
          } catch (_) {}
          const res = await fetch(`${API_BASE}/api/email/ai-reply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              to: data.from_address,
              subject: `Re: ${data.subject}`,
              original_body: data.body,
              model: currentModel,
              session_id: currentSessionId,
              message_id: data.message_id || '',
              uid: String(em.uid || ''),
              folder: _currentFolder,
              fast: aiReplyMode ? aiReplyMode === 'fast' : _shouldUseFastAiReply(data),
            }),
          });
          const result = await res.json();
          if (draftToastTimer) clearTimeout(draftToastTimer);
          if (result.success && result.reply) {
            aiSuggestedBody = _cleanAiReplyText(result.reply);
          } else {
            const _msg = result.error || 'AI reply could not be generated';
            console.error('AI reply generation failed:', _msg);
            import('./ui.js').then(m => m.showError && m.showError('AI reply failed: ' + _msg)).catch(() => {});
            return;
          }
        } catch (e) {
          if (draftToastTimer) clearTimeout(draftToastTimer);
          console.error('AI reply generation failed:', e);
          import('./ui.js').then(m => m.showError && m.showError('AI reply failed: ' + (e.message || e))).catch(() => {});
          return;
        }
      }
    }

    em.is_read = true;
    if (itemEl) itemEl.classList.remove('email-unread');

    // Addresses to exclude from Reply All. Prefer the full set of configured
    // accounts (so a multi-account user's other mailboxes are excluded too),
    // falling back to the single active address. Empty ⇒ no exclusion.
    const myAddresses = (Array.isArray(window._myEmailAddresses) && window._myEmailAddresses.length)
      ? window._myEmailAddresses
      : (window._myEmailAddress ? [window._myEmailAddress] : []);

    let toAddress = data.from_address;
    let ccAddresses = '';
    let subjectPrefix = 'Re: ';

    if (mode === 'reply-all') {
      // Build reply-all: TO = original sender, CC = everyone else (To + Cc minus me)
      ccAddresses = buildReplyAllCc(data, myAddresses);
    } else if (mode === 'forward') {
      toAddress = '';
      subjectPrefix = 'Fwd: ';
    }

    // Don't double-prefix `Re:` / `Fwd:` when the subject already starts with one.
    // Replies to replies were producing `Re: Re: Re: …` which can also break
    // some IMAP servers' header parsing on very long subject lines.
    let _baseSubject = (data.subject || '').trim();
    if (subjectPrefix === 'Re: ' && /^re\s*:/i.test(_baseSubject)) subjectPrefix = '';
    else if (subjectPrefix === 'Fwd: ' && /^fwd?\s*:/i.test(_baseSubject)) subjectPrefix = '';
    let content = `To: ${toAddress}\nSubject: ${subjectPrefix}${_baseSubject}`;
    if (ccAddresses) content += `\nCc: ${ccAddresses}`;
    if (mode !== 'forward' && data.message_id) content += `\nIn-Reply-To: ${data.message_id}`;
    if (mode !== 'forward' && data.message_id) content += `\nReferences: ${data.references ? data.references + ' ' + data.message_id : data.message_id}`;
    content += `\nX-Source-UID: ${em.uid}`;
    content += `\nX-Source-Folder: ${_currentFolder}`;
    if (data.attachments && data.attachments.length > 0) {
      const attStr = data.attachments.map(a => `${a.index}:${a.filename}:${a.size}`).join('|');
      content += `\nX-Attachments: ${attStr}`;
    }
    content += '\n---\n';

    // Format the original date in a human-readable way for the quote header
    let niceDate = data.date || '';
    try {
      if (data.date) {
        const d = new Date(data.date);
        if (!isNaN(d.getTime())) {
          niceDate = d.toLocaleString([], {
            weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
          });
        }
      }
    } catch (_) {}

    // Plain-text body, with HTML fallback stripped if no text part exists.
    // Without this, an HTML-only email gives data.body === null/undefined
    // and the reply doc opens empty (data.body.split throws).
    let _origBody = (typeof data.body === 'string' && data.body.length) ? data.body : '';
    if (!_origBody && typeof data.body_html === 'string' && data.body_html) {
      _origBody = data.body_html
        .replace(/<style[\s\S]*?<\/style>/gi, '')
        .replace(/<script[\s\S]*?<\/script>/gi, '')
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/p>/gi, '\n\n')
        .replace(/<[^>]+>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
    }

    if (mode === 'forward') {
      content += `\n\n---------- Forwarded message ----------\n`;
      content += `From: ${data.from_name} <${data.from_address}>\n`;
      content += `Date: ${niceDate}\n`;
      content += `Subject: ${data.subject}\n`;
      if (data.to) content += `To: ${data.to}\n`;
      content += `\n${_origBody}`;
    } else {
      const quotedBody = _origBody.split('\n').map(l => '> ' + l).join('\n');
      // Inject AI-suggested body if present. No leading newline — the header
      // block already ends with "---\n", so the reply must start on the very
      // first body line, not one row down.
      if (aiSuggestedBody) {
        content += `${aiSuggestedBody}\n\n`;
      } else {
        content += '\n\n';
      }
      content += `${_replySeparator}\nOn ${niceDate}, ${data.from_name} <${data.from_address}> wrote:\n${quotedBody}`;
    }

    if (_docModule) {
      // Only reuse an existing doc tab if the user really just wants to "view"
      // the email again. For reply/reply-all/forward/ai-reply, always create
      // a fresh draft — otherwise a previously-emptied doc (sent reply, AI
      // reply that came back blank, etc.) keeps coming back instead of a
      // proper pre-filled reply.
      const reuseExisting = (mode === 'view' || mode === 'open');
      const existingDocId = (reuseExisting && _docModule.findEmailDocId)
        ? _docModule.findEmailDocId(em.uid, _currentFolder)
        : null;
      if (existingDocId) {
        if (!_docModule.isPanelOpen()) _docModule.openPanel();
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        await _docModule.loadDocument(existingDocId);
      } else {
        // If the user already has a chat session open, reuse it instead of
        // spawning a new one. They asked for this explicitly — opening reply
        // mid-conversation shouldn't whip them out of context.
        let activeSid = '';
        try { activeSid = sessionModule?.getCurrentSessionId?.() || ''; } catch {}
        if (!activeSid) {
          // No chat in flight — keep the old behavior of creating a scoped
          // email-thread chat, then RE-READ the now-current session id. The
          // POST below requires a session_id (backend 400s without one), and
          // the freshly-created chat is what should own the reply draft.
          await _createEmailChat(data);
          try { activeSid = sessionModule?.getCurrentSessionId?.() || ''; } catch {}
        }
        // Guarantee a session — _createEmailChat can't make one when there's
        // no enabled default-chat endpoint, which left the reply POSTing a
        // null session_id → 400. Create a bare session so the draft always
        // has a home regardless of chat/endpoint config.
        if (!activeSid) {
          try {
            const _fd = new FormData();
            _fd.append('name', `Email: ${(data.subject || '').slice(0, 60)}`);
            _fd.append('skip_validation', 'true');
            const _sres = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: _fd, credentials: 'same-origin' });
            if (_sres.ok) {
              const _sdata = await _sres.json();
              if (_sdata && _sdata.id) {
                activeSid = _sdata.id;
                if (sessionModule?.loadSessions) await sessionModule.loadSessions();
                if (sessionModule?.selectSession) await sessionModule.selectSession(activeSid);
              }
            }
          } catch (e) { console.error('reply: bare session create failed', e); }
        }

        const docRes = await fetch(`${API_BASE}/api/document`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            // Reuse the user's current chat session if there is one (so the
            // reply draft lives in the chat they were just in); otherwise
            // null and the new email-chat (created above) takes over.
            session_id: activeSid || null,
            title: data.subject,
            content: content,
            language: 'email',
          }),
        });
        if (!docRes.ok) {
          const errText = await docRes.text();
          console.error('[reply-debug] POST /api/document failed', docRes.status, errText);
          // uiModule isn't statically imported here — use the dynamic
          // import pattern the rest of this file uses. (Previously this
          // referenced a bare `uiModule`, throwing a ReferenceError that
          // the outer catch swallowed → reply silently did nothing.)
          import('./ui.js').then(m => m.showError && m.showError('Failed to create reply draft (' + docRes.status + ')')).catch(() => {});
          return;
        }
        const doc = await docRes.json();
        if (doc.id) {
          const wasOpen = _docModule.isPanelOpen();
          if (!wasOpen) _docModule.openPanel();
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
          // Use the doc dict from the POST directly — avoids a 404 race
          // when the GET fires before the new row is visible to the read
          // connection (or when caching is interfering). loadDocument's
          // GET path can still be used as a fallback.
          if (_docModule.injectFreshDoc) {
            _docModule.injectFreshDoc(doc);
          } else {
            await _docModule.loadDocument(doc.id);
          }
        }
      }
    }
  } catch (e) {
    console.error('Failed to open email:', e);
    // Surface the failure so a silent throw in the reply flow doesn't
    // look like "nothing happened". Dynamic import — uiModule isn't a
    // static import in this file.
    const msg = e && e.message ? e.message : String(e);
    import('./ui.js').then(m => m.showError && m.showError('Reply failed: ' + msg)).catch(() => {});
  } finally {
    if (spinner) { spinner.destroy(); spinner.element.remove(); }
    if (itemEl) {
      const menuWrap = itemEl.querySelector('.email-menu-wrap');
      if (menuWrap) menuWrap.style.display = '';
    }
  }
}

function _showEmailMenu(em, anchor, itemEl) {
  document.querySelectorAll('.email-dropdown').forEach(d => d.remove());

  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown email-dropdown show';

  const actions = [
    { label: 'Open', icon: _replyIcon, action: () => _openEmail(em, itemEl) },
    { label: 'Remind to reply', icon: _bellIcon, submenu: 'remind' },
    { label: 'Archive', icon: _archiveIcon, action: () => _archiveEmail(em) },
    { label: 'Delete', icon: _deleteIcon, danger: true, action: () => _deleteEmail(em) },
  ];

  for (const a of actions) {
    const menuItem = document.createElement('div');
    menuItem.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    const arrow = a.submenu ? ' <span style="margin-left:auto;opacity:0.5;">›</span>' : '';
    menuItem.innerHTML = _icon(a.icon) + `<span>${a.label}</span>${arrow}`;
    menuItem.addEventListener('click', (e) => {
      e.stopPropagation();
      if (a.submenu === 'remind') {
        _showRemindSubmenu(em, dropdown);
        return;
      }
      dropdown.remove();
      a.action();
    });
    dropdown.appendChild(menuItem);
  }

  anchor.appendChild(dropdown);

  const close = (e) => {
    if (!dropdown.contains(e.target) && !anchor.contains(e.target)) {
      dropdown.remove();
      document.removeEventListener('click', close, true);
    }
  };
  setTimeout(() => document.addEventListener('click', close, true), 10);
}

// ---- Reminder submenu (creates a Note with a reminder for this email) ----

function _showRemindSubmenu(em, parentDropdown) {
  // Replace content of parent dropdown with time presets
  parentDropdown.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'dropdown-item-compact';
  header.style.cssText = 'opacity:0.5;font-size:10px;pointer-events:none;text-transform:uppercase;letter-spacing:0.5px;padding-top:6px;';
  header.innerHTML = '<span>Remind me</span>';
  parentDropdown.appendChild(header);

  const now = new Date();
  const laterToday = new Date(now);
  const sixPm = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0);
  if (sixPm - now < 60*60*1000) laterToday.setTime(now.getTime() + 3 * 60 * 60 * 1000);
  else laterToday.setTime(sixPm.getTime());

  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1); tomorrow.setHours(8, 0, 0, 0);
  const daysUntilMon = (8 - now.getDay()) % 7 || 7;
  const nextWeek = new Date(now); nextWeek.setDate(now.getDate() + daysUntilMon); nextWeek.setHours(8, 0, 0, 0);

  const presets = [
    { label: 'Later today', sub: laterToday.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), date: laterToday },
    { label: 'Tomorrow', sub: tomorrow.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), date: tomorrow },
    { label: 'Next week', sub: nextWeek.toLocaleDateString([], { weekday: 'short' }) + ' ' + nextWeek.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), date: nextWeek },
  ];
  for (const p of presets) {
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact';
    item.innerHTML = `<span>${p.label}</span><span style="margin-left:auto;opacity:0.5;font-size:10px;">${p.sub}</span>`;
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      parentDropdown.remove();
      await _createReplyReminder(em, p.date);
    });
    parentDropdown.appendChild(item);
  }
  const customItem = document.createElement('div');
  customItem.className = 'dropdown-item-compact';
  customItem.innerHTML = '<span>Pick date and time…</span>';
  customItem.addEventListener('click', async (e) => {
    e.stopPropagation();
    parentDropdown.remove();
    const tmp = document.createElement('input');
    tmp.type = 'datetime-local';
    const def = new Date(tomorrow);
    const pad = n => String(n).padStart(2, '0');
    tmp.value = `${def.getFullYear()}-${pad(def.getMonth()+1)}-${pad(def.getDate())}T${pad(def.getHours())}:${pad(def.getMinutes())}`;
    tmp.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:99999;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;';
    document.body.appendChild(tmp);
    tmp.focus();
    if (typeof tmp.showPicker === 'function') { try { tmp.showPicker(); } catch {} }
    // Cleanup helper — also unwires the global listeners so they don't
    // linger after dismiss.
    const _cleanup = () => {
      tmp.remove();
      document.removeEventListener('keydown', _onKey);
      document.removeEventListener('mousedown', _onDocClick, true);
    };
    const _onKey = (ev) => { if (ev.key === 'Escape') _cleanup(); };
    // Click-outside dismiss. Replaces the old blur-based auto-remove —
    // blur fires whenever the native datetime popup steals focus, so
    // the input vanished before the user could click any date. Now we
    // only dismiss when the user clicks something that is NOT the
    // input itself (the native picker popup is a browser-owned overlay
    // OUTSIDE the document, so its clicks don't fire here at all — no
    // false dismissals).
    const _onDocClick = (ev) => { if (ev.target !== tmp) _cleanup(); };
    tmp.addEventListener('change', async () => {
      if (tmp.value) {
        await _createReplyReminder(em, new Date(tmp.value));
      }
      _cleanup();
    });
    document.addEventListener('keydown', _onKey);
    // Defer the click-outside listener so the click that opened this
    // input doesn't immediately close it.
    setTimeout(() => document.addEventListener('mousedown', _onDocClick, true), 50);
  });
  parentDropdown.appendChild(customItem);
}

async function _createReplyReminder(em, dueDate) {
  const pad = n => String(n).padStart(2, '0');
  const iso = `${dueDate.getFullYear()}-${pad(dueDate.getMonth()+1)}-${pad(dueDate.getDate())}T${pad(dueDate.getHours())}:${pad(dueDate.getMinutes())}`;
  const from = em.from || em.sender || 'someone';
  const payload = {
    title: `Reply: ${em.subject || '(no subject)'}`,
    content: `From: ${from}\n\nRemember to reply to this email.`,
    note_type: 'note',
    label: 'email',
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
    const fmt = dueDate.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    showToast(`Reminder set for ${fmt}`);
    // Request notification permission if needed
    if ('Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch {}
    }
  } catch (e) {
    const { showError } = await import('./ui.js');
    showError('Failed to create reminder');
  }
}

async function _archiveEmail(em) {
  try {
    await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'POST' });
    _emails = _emails.filter(e => e.uid !== em.uid);
    _renderList();
  } catch (e) {
    console.error('Failed to archive:', e);
  }
}

async function _deleteEmail(em) {
  const subject = em.subject || '(no subject)';
  const { styledConfirm } = await import('./ui.js');
  const ok = await styledConfirm(`Delete "${subject}"?`, { confirmText: 'Delete', cancelText: 'Cancel', danger: true });
  if (!ok) return;
  try {
    await fetch(`${API_BASE}/api/email/delete/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'DELETE' });
    _emails = _emails.filter(e => e.uid !== em.uid);
    _renderList();
  } catch (e) {
    console.error('Failed to delete:', e);
  }
}

async function _toggleDone(em, itemEl) {
  const newState = !em.is_answered;
  em.is_answered = newState;
  if (newState) em.is_read = true; // mark-done implies mark-read
  if (itemEl) {
    if (newState) {
      itemEl.classList.remove('email-unread');
      // Also drop any inline unread indicator dots the renderer may have added
      itemEl.querySelectorAll('.email-unread-dot, [data-unread-dot]').forEach(n => n.remove());
    }
    const check = itemEl.querySelector('.email-done-check');
    if (check) check.classList.toggle('active', newState);
  }
  try {
    if (newState) {
      await fetch(`${API_BASE}/api/email/mark-answered/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'POST' });
      await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'POST' });
    } else {
      await fetch(`${API_BASE}/api/email/clear-answered/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'POST' });
    }
  } catch (e) {
    console.error('Failed to toggle done:', e);
  }
}

async function _createEmailChat(emailData) {
  try {
    // Try current session's endpoint first
    const current = sessionModule.getSessions?.().find(s => s.id === sessionModule.getCurrentSessionId?.());
    let url, model, endpointId;
    if (current && current.endpoint_url && current.model) {
      url = current.endpoint_url;
      model = current.model;
      endpointId = current.endpoint_id;
    } else {
      // Fall back to default chat config
      const dcRes = await fetch(`${API_BASE}/api/default-chat`);
      const dc = await dcRes.json();
      url = dc.endpoint_url;
      model = dc.model;
      endpointId = dc.endpoint_id;
    }

    if (url && model) {
      await sessionModule.createDirectChat(url, model, endpointId);
      // Set a helpful title in the chat meta
      const meta = document.getElementById('current-meta');
      if (meta) meta.textContent = `Email: ${(emailData.subject || '').slice(0, 60)}`;
    }
  } catch (e) {
    console.error('Failed to create email chat:', e);
  }
}

async function _composeNew() {
  if (!_docModule) return;
  // NOTE: don't open the panel here. Creating the email-scoped chat below can
  // switch sessions, which tears the panel down — so an early open would mount
  // the pane, get closed, then injectFreshDoc remounts it: a visible flash
  // (doc shows for a frame, then slides up again). Mount once, at injectFreshDoc,
  // after the session + doc exist.
  try {
    // /api/document requires a session_id (returns 400 if null), so reuse
    // the active chat if there is one — otherwise spin up an email-scoped
    // chat first, same pattern the reply path uses.
    let sid = '';
    try { sid = sessionModule?.getCurrentSessionId?.() || ''; } catch (_) {}
    if (!sid) {
      await _createEmailChat({ subject: 'New Email' });
      try { sid = sessionModule?.getCurrentSessionId?.() || ''; } catch (_) {}
    }
    // Guarantee a session — _createEmailChat can't make one when there's no
    // enabled default-chat endpoint, which left compose POSTing a null
    // session_id → 400 (the draft silently never appeared). Same bare-session
    // fallback the reply flow uses.
    if (!sid) {
      try {
        const _fd = new FormData();
        _fd.append('name', 'New Email');
        _fd.append('skip_validation', 'true');
        const _sres = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: _fd, credentials: 'same-origin' });
        if (_sres.ok) {
          const _sdata = await _sres.json();
          if (_sdata && _sdata.id) {
            sid = _sdata.id;
            // NOTE: intentionally do NOT loadSessions()/selectSession() here.
            // Re-selecting the (empty) session re-renders the chat and flashes
            // the welcome splash for a frame before the draft opens — the
            // "splash flickers like crazy then email opens" bug. The doc only
            // needs the session_id; the draft opens in the doc panel regardless.
          }
        }
      } catch (e) { console.error('compose: bare session create failed', e); }
    }
    if (!sid) {
      console.error('compose: could not obtain a session_id');
      import('./ui.js').then(m => m.showError && m.showError('Could not start a new email (no session).')).catch(() => {});
      return;
    }
    const res = await fetch(`${API_BASE}/api/document`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sid,
        title: 'New Email',
        content: 'To: \nSubject: \n---\n',
        language: 'email',
      }),
    });
    if (!res.ok) {
      console.error('compose POST failed', res.status, await res.text().catch(() => ''));
      import('./ui.js').then(m => m.showError && m.showError('Failed to create new email (' + res.status + ')')).catch(() => {});
      return;
    }
    const doc = await res.json();
    if (doc.id) {
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      // Use the doc dict from POST directly to avoid the GET 404 race that
      // hits a freshly-created doc on a separate read connection.
      if (_docModule.injectFreshDoc) {
        _docModule.injectFreshDoc(doc);
      } else {
        _docModule.loadDocument(doc.id);
      }
    }
  } catch (e) {
    console.error('Failed to create email:', e);
  }
}

function _esc(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

function _senderColor(name) {
  if (!name) return 'hsl(220, 55%, 65%)';
  const key = name.toLowerCase();
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 65%)`;
}
