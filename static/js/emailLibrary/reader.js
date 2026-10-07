// static/js/emailLibrary/reader.js
//
// The two ways an email opens outside the list: as a docked tab (a numbered
// slot in the dock, `_openEmailAsTab`) and as a floating window
// (`_openEmailWindow`). Plus the AI summary panel, which both of them and the
// inline expanded card share.
//
// Reader slot numbers are deliberately sticky: once a reader is tab 2 it stays
// tab 2 until it closes, even if tab 1 closes first. The slot map here is what
// holds that.

import spinnerModule from '../spinner.js';
import * as Modals from '../modalManager.js';
import { showToast } from '../ui.js?v=20260916largetoolscroll1';
import { state } from './state.js';
import { _esc, _extractName, _renderEmailSummaryError } from './utils.js';
import { _aiReplyIcon, _handleAiReplyButton, _hasMultipleRecipients } from './aiReply.js';
import {
  _buildAttsHtmlFor,
  _loadDeferredAttachmentsIntoReader,
  _wireEmailAttachmentWrap,
} from './attachments.js';
import {
  _renderEmailBody,
  _safeRenderEmailBody,
  _wireEmailInlineImages,
} from './bodyRender.js';
import {
  _acct,
  _emailReaderSkeletonHtml,
  _makeDraggable,
  _markEmailReaderActive,
  _maybeAutoTranslateEmail,
  _normalizeEmailStateFlags,
  _recipientChipHtml,
  _recipientMetaToggleHtml,
  _setSummaryCollapsedPref,
  _showEmailReaderLoadError,
  _snapEmailModalToLeftSidebar,
  _splitRecipientList,
  _stampReaderContext,
  _summaryCollapsedPref,
  _syncEmailDoneState,
  _syncEmailReadState,
  _toggleCardPreview,
  _wireMetaToggle,
  _wireReaderActionOverflow,
  _wireRecipientChips,
} from './index.js';
import { _showReaderMoreMenu } from './menus.js';

const API_BASE = window.location.origin;

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
export async function _openEmailAsTab(em, folder) {
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
    <div class="modal-content doclib-modal-content email-reader-tab-content" style="background:var(--bg);width:min(720px, 92vw);display:flex;flex-direction:column;">
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
  const showFailedTab = (message) => {
    try { showToast(message || 'Failed to load email'); } catch (_) {}
    _showEmailReaderLoadError(reader, message, () => {
      try { Modals.close(modalId); } catch (_) { try { modal.remove(); } catch (_) {} }
      setTimeout(() => { _openEmailAsTab(em, useFolder); }, 0);
    });
  };
  _markEmailReaderActive(reader);
  const loading = modal.querySelector('.email-reader-tab-loading');
  if (loading) loading.remove();
  if (reader) {
    reader.classList.add('email-card-reader-loading');
    reader.innerHTML = _emailReaderSkeletonHtml();
  }
  try {
    const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(useFolder)}${_acct()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    let data = await res.json();
    if (data.error) {
      showFailedTab(`Failed to load email: ${data.error}`);
      return;
    }
    data = _normalizeEmailStateFlags({ ...em, ...data });
    Object.assign(em, data);
    _syncEmailDoneState(em.uid, data.is_answered);
    _syncEmailReadState(em.uid, true);
    _stampReaderContext(reader, data, useFolder, state._libAccountId);
    const buildChips = (str) => {
      if (!str) return '';
      return _splitRecipientList(str).map(a => {
        const name = _extractName(a);
        return _recipientChipHtml(a, name);
      }).join('');
    };
    const fromChip = _recipientChipHtml(`${data.from_name || ''} <${data.from_address || ''}>`, data.from_name || data.from_address, 'from-chip');
    let attsHtml = '';
    try { attsHtml = _buildAttsHtmlFor(em.uid, data); } catch {}
    reader.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row email-reader-meta-from">
            <strong>From:</strong>
            <span class="recipient-chips">${fromChip}${_recipientMetaToggleHtml(data)}</span>
          </div>
          ${(data.to || data.cc) ? `<div class="email-reader-meta-details" hidden>
            ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${buildChips(data.to)}</span></div>` : ''}
            ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${buildChips(data.cc)}</span></div>` : ''}
          </div>` : ''}
          <div class="email-reader-actions-inline">
            <button class="memory-toolbar-btn reader-icon-btn" data-act="ai-reply" title="${data.cached_ai_reply ? 'AI Reply (cached draft ready)' : 'AI Reply'}">${_aiReplyIcon(data)}<span class="reader-btn-label">AI reply</span></button>
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
    _wireRecipientChips(reader);
    _wireEmailAttachmentWrap(reader, useFolder);
    _wireEmailInlineImages(reader);
    _loadDeferredAttachmentsIntoReader(reader, em.uid, useFolder, data, !!em.has_attachments);
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
    reader.querySelector('[data-act="summarize"]')?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await _summarizeEmail(reader, data, ev.currentTarget); } catch {}
    });
    _wireMetaToggle(reader);
    _wireReaderActionOverflow(reader);
    reader.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      try { _showReaderMoreMenu(em, modal, reader, ev.currentTarget, data); } catch {}
    });
  } catch (err) {
    showFailedTab(err?.message ? `Failed to load email: ${err.message}` : 'Failed to load email');
  }
}


// "Open in new window" — spawns a floating draggable modal that shows just
// the email content. Multiple windows can stack; each has its own DOM id
// and close button. Uses `_makeDraggable` so dragging the header pans the
// window around. Renders the body via _renderEmailBody for parity with the
// expanded reader.
let _emailWindowSeq = 0;
export async function _openEmailWindow(em, folder) {
  const useFolder = folder || state._libFolder || 'INBOX';
  _emailWindowSeq += 1;
  const winId = `email-window-${em.uid}-${_emailWindowSeq}`;
  const modal = document.createElement('div');
  modal.className = 'modal email-window-modal';
  modal.id = winId;
  modal.style.cssText = 'pointer-events:none;background:transparent;';
  modal.innerHTML = `
    <div class="modal-content email-window-content" style="width:min(640px, 92vw);display:flex;flex-direction:column;background:var(--bg);">
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
    if (loading) loading.remove();
    if (bodyEl) {
      bodyEl.classList.add('email-card-reader', 'email-card-reader-loading');
      bodyEl.style.padding = '0';
      bodyEl.innerHTML = _emailReaderSkeletonHtml();
    }
    const res = await fetch(`${API_BASE}/api/email/read/${em.uid}?folder=${encodeURIComponent(useFolder)}${_acct()}`);
    let data = await res.json();
    if (data.error) {
      bodyEl.innerHTML = `<div style="color:var(--red,#e55);padding:16px;">${_esc(data.error)}</div>`;
      return;
    }
    data = _normalizeEmailStateFlags({ ...em, ...data });
    Object.assign(em, data);
    _syncEmailDoneState(em.uid, data.is_answered);
    _syncEmailReadState(em.uid, true);
    const subjEl = modal.querySelector('.email-window-subject');
    if (subjEl && data.subject) subjEl.textContent = data.subject;
    // Build recipient chips the same way the inline reader does so the
    // standalone viewer looks/feels exactly like a real email view.
    const _chipsFor = (addrs) => {
      if (!addrs) return '';
      const list = _splitRecipientList(addrs);
      return list.map(a => {
        const name = _extractName(a);
        return _recipientChipHtml(a, name);
      }).join('');
    };
    const fromChip = _recipientChipHtml(`${data.from_name || ''} <${data.from_address || ''}>`, data.from_name || data.from_address, 'from-chip');
    let attsHtml = '';
    try { attsHtml = _buildAttsHtmlFor(em.uid, data); } catch {}
    // Repurpose bodyEl as a full email-card-reader so the inline reader's
    // CSS applies (sized header, action buttons in two rows, etc.).
    bodyEl.classList.add('email-card-reader');
    bodyEl.classList.remove('email-card-reader-loading');
    _stampReaderContext(bodyEl, { ...em, ...data }, useFolder, state._libAccountId);
    _markEmailReaderActive(bodyEl);
    bodyEl.style.padding = '0';
    bodyEl.innerHTML = `
      <div class="email-reader-header">
        <div class="email-reader-meta">
          <div class="email-reader-meta-row email-reader-meta-from">
            <strong>From:</strong>
            <span class="recipient-chips">${fromChip}${_recipientMetaToggleHtml(data)}</span>
          </div>
          ${(data.to || data.cc) ? `<div class="email-reader-meta-details" hidden>
            ${data.to ? `<div class="email-reader-meta-row"><strong>To:</strong><span class="recipient-chips">${_chipsFor(data.to)}</span></div>` : ''}
            ${data.cc ? `<div class="email-reader-meta-row"><strong>Cc:</strong><span class="recipient-chips">${_chipsFor(data.cc)}</span></div>` : ''}
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
    _markEmailReaderActive(bodyEl);
    _wireRecipientChips(bodyEl);
    // Wire all the same action handlers the inline reader has.
    _wireEmailAttachmentWrap(bodyEl, useFolder);
    _wireEmailInlineImages(bodyEl);
    _loadDeferredAttachmentsIntoReader(bodyEl, em.uid, useFolder, data, !!em.has_attachments);
    _maybeAutoTranslateEmail(bodyEl);
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
    _wireMetaToggle(bodyEl);
    _wireReaderActionOverflow(bodyEl);
    bodyEl.querySelector('[data-act="more"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      // Use a synthetic "card" — the more-menu only needs the anchor
      // element and the email data. The card param is mostly used to find
      // the next sibling; the standalone window has none so we just pass
      // bodyEl as a stand-in.
      try { _showReaderMoreMenu(em, modal, bodyEl, ev.currentTarget, data); } catch {}
    });
  } catch (err) {
    bodyEl.innerHTML = `<div style="color:var(--red,#e55);padding:16px;">Failed to load: ${_esc(String(err))}</div>`;
  }
}

export async function _summarizeEmail(reader, data, btn) {
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
      _renderEmailSummaryError(content, result);
    }
  } catch (e) {
    sp.destroy();
    _renderEmailSummaryError(content, null);
    if (uiModule) uiModule.showError?.('Failed to summarize');
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function _emailBodyTextForTranslate(reader) {
  const body = reader?.querySelector?.('.email-reader-body');
  if (!body) return '';
  const clone = body.cloneNode(true);
  clone.querySelectorAll('.email-summary-panel, details.email-quote-fold, details.email-sig-fold').forEach(n => n.remove());
  return (clone.innerText || clone.textContent || '').trim();
}
