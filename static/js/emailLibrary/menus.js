// static/js/emailLibrary/menus.js
//
// Every action menu the email library opens: the reader's More menu, a card's
// kebab menu, the bulk-selection Actions menu, and `_bulkAction`, which runs
// the chosen bulk mutation.
//
// Item order is not local — it comes from `orderActionMenuItems` in
// ../actionMenuOrder.js, so an action keeps the same position here as in the
// document, task, session and memory menus. `tests/test_action_menu_order.py`
// pins that.

import spinnerModule from '../spinner.js';
import { SELECT_MENU_ICON, actionMenuRank, orderActionMenuItems } from '../actionMenuOrder.js';
import { bindMenuDismiss, dismissOrRemove } from '../escMenuStack.js';
import { topPortalZ } from '../toolWindowZOrder.js';
import { showToast, styledConfirm } from '../ui.js?v=20260916largetoolscroll1';
import { state } from './state.js';
import {
  _handleAiReplyButton,
  _showEmailTranslateSubmenu,
  _showLibRemindSubmenu,
} from './aiReply.js';
import {
  _acct,
  _animateEmailCardRemoval,
  _clearDoneResponseTagsLocal,
  _emailMutationQuery,
  _exportSelectedAttachments,
  _findSiblingEmailCard,
  _fitEmailDropdown,
  _libCacheWriteBack,
  _loadEmailsFresh,
  _renderGrid,
  _requireSuccessfulEmailMutation,
  _showEmailDeleteOverlay,
  _syncEmailReadState,
  _toggleCardPreview,
} from './index.js';
import { _openEmailAsTab } from './reader.js';

const API_BASE = window.location.origin;

export function _showReaderMoreMenu(em, card, reader, anchor, data) {
  // Toggle: if a dropdown for THIS anchor is already open, close it.
  const existing = document.querySelector('.email-card-dropdown');
  if (existing && existing._anchor === anchor) {
    dismissOrRemove(existing);
    return;
  }
  // Otherwise close any other open dropdown (its own teardown clears its
  // anchor's active state) before opening a fresh one.
  document.querySelectorAll('.email-card-dropdown').forEach(dismissOrRemove);

  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown';
  dropdown._anchor = anchor;
  anchor.classList.add('reader-more-active');
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:${topPortalZ()};min-width:180px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;`;

  const _icon = (svg) => `<span class="dropdown-icon">${svg}</span>`;
  const _unreadIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  const _archIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
  const _spamIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  const _trashIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
  const _deleteForeverIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="14" y2="15"/><line x1="14" y1="11" x2="10" y2="15"/></svg>';
  const _bellIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  const _newTabIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
  const _checkIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  const _translateIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-primary, var(--red))" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/></svg>';

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
  // Three groups separated by dividers:
  //   1. Open / Mark Unread / Remind — the per-email view actions
  //   2. Save sender / Not Done / Archive — non-destructive state changes
  //   3. Move to Spam / Move to Trash / Delete — destructive
  const overflowActions = Array.from(reader.querySelectorAll('.reader-action-overflowed')).map(button => ({
    label: button.querySelector('.reader-btn-label')?.textContent?.trim() || button.title || 'Action',
    icon: button.querySelector('svg')?.outerHTML || '',
    button,
    action: (positionAnchor) => {
      if (button.dataset.act === 'ai-reply') {
        _handleAiReplyButton({
          currentTarget: button,
          stopPropagation() {},
        }, em, data, positionAnchor || button);
      } else {
        button.click();
      }
    },
  }));
  const actions = [
    ...overflowActions,
    ...(overflowActions.length ? [{ separator: true }] : []),
    {
      label: 'Open in new tab',
      icon: _newTabIcon,
      action: async () => {
        const folder = state._libFolder || 'INBOX';
        await _openEmailAsTab(em, folder);
      },
    },
    {
      label: 'Remind to reply',
      icon: _bellIcon,
      submenu: 'remind',
    },
    {
      label: 'Translate',
      icon: _translateIcon,
      submenu: 'translate',
    },
    { separator: true },
    {
      label: em.is_read ? 'Mark as Unread' : 'Mark as Read',
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
      // Favorite (pin to top). Same bookmark glyph we use for the
      // sidebar-pin / favorites filter so the visual language stays
      // consistent. Toggling updates em.is_flagged and re-sorts via
      // _renderGrid (favorited rows are always pinned at the top).
      label: em.is_flagged ? 'Unfavorite' : 'Favorite (pin to top)',
      icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="' + (em.is_flagged ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
      action: async () => {
        const next = !em.is_flagged;
        em.is_flagged = next;
        _renderGrid();
        try {
          await fetch(`${API_BASE}/api/email/flag/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}&on=${next ? 'true' : 'false'}`, { method: 'POST' });
        } catch (e) {
          // Roll back the optimistic flip if the server didn't take it.
          em.is_flagged = !next;
          _renderGrid();
          console.error('Failed to toggle favorite:', e);
        }
      },
    },
    {
      label: em.is_answered ? 'Mark as Not Done' : 'Mark as Done',
      icon: _checkIcon,
      action: async () => {
        const newState = !em.is_answered;
        em.is_answered = newState;
        if (newState) {
          _clearDoneResponseTagsLocal(em);
          _syncEmailReadState(em.uid, true);
        }
        try {
          if (newState) {
            await fetch(`${API_BASE}/api/email/mark-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
            await fetch(`${API_BASE}/api/email/mark-read/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          } else {
            await fetch(`${API_BASE}/api/email/clear-answered/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
          }
        } catch (e) { console.error('Failed to toggle done:', e); }
        _renderGrid();
      },
    },
    {
      label: 'Move to Archive',
      icon: _archIcon,
      action: async () => {
        try {
          await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        } catch (e) { console.error(e); }
        await closeAndRemove();
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
          import('../ui.js?v=20260916largetoolscroll1').then(m => m.showError && m.showError('No sender address')).catch(() => {});
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
          import('../ui.js?v=20260916largetoolscroll1').then(m => {
            if (!m.showToast) return;
            if (d.success && d.message === 'Already exists') m.showToast('Already in contacts');
            else if (d.success) m.showToast('Saved to contacts');
            else m.showError && m.showError('Failed to save contact');
          }).catch(() => {});
        } catch (_) {
          import('../ui.js?v=20260916largetoolscroll1').then(m => m.showError && m.showError('Failed to save contact')).catch(() => {});
        }
      },
    },
    { separator: true },
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
        const busy = _showEmailDeleteOverlay(card);
        await busy?.ready;
        try {
          const response = await fetch(`${API_BASE}/api/email/delete/${encodeURIComponent(em.uid)}?${_emailMutationQuery(em)}`, { method: 'DELETE' });
          await _requireSuccessfulEmailMutation(response, 'Failed to delete email');
        } catch (e) {
          console.error(e);
          busy?.remove?.();
          showToast('Failed to delete email');
          return;
        }
        busy?.remove?.();
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
        const busy = _showEmailDeleteOverlay(card);
        await busy?.ready;
        try {
          const response = await fetch(`${API_BASE}/api/email/delete-permanent/${encodeURIComponent(em.uid)}?${_emailMutationQuery(em)}`, { method: 'DELETE' });
          await _requireSuccessfulEmailMutation(response, 'Failed to delete email');
        } catch (e) {
          console.error(e);
          busy?.remove?.();
          showToast('Failed to delete email');
          return;
        }
        busy?.remove?.();
        await closeAndRemove();
      },
    },
  ];

  for (const a of actions) {
    if (a.separator) {
      const sep = document.createElement('div');
      sep.className = 'dropdown-divider';
      dropdown.appendChild(sep);
      continue;
    }
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    // Icons come from repository-owned SVGs in this menu or reader buttons.
    item.innerHTML = _icon(a.icon);
    const label = document.createElement('span');
    label.textContent = a.label;
    item.appendChild(label);
    if (a.submenu) {
      const arrow = document.createElement('span');
      arrow.style.cssText = 'margin-left:auto;opacity:0.5;';
      arrow.textContent = '›';
      item.appendChild(arrow);
    }
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      if (a.submenu === 'remind') {
        _showLibRemindSubmenu(em, dropdown);
        return;
      }
      if (a.submenu === 'translate') {
        _showEmailTranslateSubmenu(reader, dropdown);
        return;
      }
      // A hidden overflowed button has a zero-sized client rect. Invoke the
      // AI chooser while the visible More item is still mounted, then close
      // the action menu after the chooser has captured its position.
      if (a.button?.dataset.act === 'ai-reply') {
        a.action(item);
        close();
        return;
      }
      close();
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
    close();
  });
  dropdown.appendChild(cancelItem);

  document.body.appendChild(dropdown);
  _fitEmailDropdown(dropdown, rect);
  const close = bindMenuDismiss(dropdown, () => {
    dropdown.remove();
    anchor.classList.remove('reader-more-active');
  }, (ev) => !dropdown.contains(ev.target) && ev.target !== anchor);
}

export function _showCardMenu(em, anchor) {
  const openMenu = document.querySelector('.email-card-dropdown:not(.email-bulk-menu)');
  if (openMenu && openMenu._emailMenuAnchor === anchor) {
    dismissOrRemove(openMenu);
    return;
  }
  document.querySelectorAll('.email-card-dropdown').forEach(dismissOrRemove);

  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown';
  dropdown._emailMenuAnchor = anchor;
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:${topPortalZ()};min-width:140px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;`;

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
      label: _currentlyDone ? 'Not Done' : 'Done',
      icon: _checkIcon,
      action: async () => {
        const card = anchor.closest('.doclib-card');
        const check = card ? card.querySelector('.email-card-done') : null;
        const wasActive = check ? check.classList.contains('active') : !!em.is_answered;
        const newState = !wasActive;
        em.is_answered = newState;
        if (newState) {
          _clearDoneResponseTagsLocal(em);
          _syncEmailReadState(em.uid, true); // mark-done implies mark-read
        }
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
          if (newState) {
            _syncEmailReadState(em.uid, true);
            card.querySelectorAll('.email-tag-urgent, .email-tag-reply-soon, .email-tag-action-needed').forEach(n => n.remove());
          }
        }
      },
    });
    actions.push({
      label: em.is_flagged ? 'Unfavorite' : 'Favorite (pin to top)',
      icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="' + (em.is_flagged ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
      action: async () => {
        const next = !em.is_flagged;
        em.is_flagged = next;
        _renderGrid();
        try {
          await fetch(`${API_BASE}/api/email/flag/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}&on=${next ? 'true' : 'false'}`, { method: 'POST' });
        } catch (e) {
          em.is_flagged = !next;
          _renderGrid();
          console.error('Failed to toggle favorite:', e);
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
      label: em.is_flagged ? 'Unfavorite' : 'Favorite (pin to top)',
      icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="' + (em.is_flagged ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
      action: async () => {
        const next = !em.is_flagged;
        em.is_flagged = next;
        _renderGrid();
        try {
          await fetch(`${API_BASE}/api/email/flag/${em.uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}&on=${next ? 'true' : 'false'}`, { method: 'POST' });
        } catch (e) {
          em.is_flagged = !next;
          _renderGrid();
          console.error('Failed to toggle favorite:', e);
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
  }

  // "Select" — switch to multi-select mode with THIS email pre-selected so
  // the user can quickly fan-out to neighbours with the bulk bar.
  // Match the chat-sidebar Select icon — a thick bullet character reads
  // much heavier than a small SVG circle. Nudged up 2px so its visual
  // center lines up with the SVG icons above (which sit a bit higher).
  const _selectIcon = SELECT_MENU_ICON;
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
      const card = document.querySelector(`#email-lib-grid .doclib-card[data-uid="${CSS.escape(String(em.uid))}"]`);
      const busy = _showEmailDeleteOverlay(card);
      await busy?.ready;
      try {
        const response = await fetch(`${API_BASE}/api/email/delete/${encodeURIComponent(em.uid)}?${_emailMutationQuery(em)}`, { method: 'DELETE' });
        await _requireSuccessfulEmailMutation(response, 'Failed to delete email');
      } catch (e) {
        busy?.remove?.();
        showToast(e?.message || 'Could not move email to Trash');
        return;
      }
      busy?.remove?.();
      await _animateEmailCardRemoval([em.uid]);
      state._libEmails = state._libEmails.filter(e => String(e.uid) !== String(em.uid));
      _renderGrid();
      _libCacheWriteBack();
    }},
  );

  const orderedActions = orderActionMenuItems(actions);
  let addedActionDivider = false;
  for (const [index, a] of orderedActions.entries()) {
    if (!addedActionDivider && index > 0 && actionMenuRank(a) >= 700) {
      const divider = document.createElement('div');
      divider.className = 'dropdown-divider';
      dropdown.appendChild(divider);
      addedActionDivider = true;
    }
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
      close();
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
    close();
  });
  dropdown.appendChild(cancelItem);

  document.body.appendChild(dropdown);
  _fitEmailDropdown(dropdown, rect);
  const close = bindMenuDismiss(dropdown, () => {
    dropdown.remove();
    anchor.classList.remove('reader-more-active');
  }, (ev) => !dropdown.contains(ev.target) && ev.target !== anchor);
}

// Bulk "Actions" dropdown for select mode — Delete is a separate visible button.
export function _showBulkActionsMenu(anchor) {
  document.querySelectorAll('.email-card-dropdown').forEach(dismissOrRemove);
  const dropdown = document.createElement('div');
  dropdown.className = 'email-card-dropdown email-bulk-menu';
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:${topPortalZ()};min-width:160px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;left:${rect.left}px;`;
  const _readIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>';
  const _unreadIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>';
  const _doneIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  const _exportIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  const items = [
    { label: 'Export Attachments', icon: _exportIco, action: () => _exportSelectedAttachments() },
    { label: 'Done', icon: _doneIco, action: () => _bulkAction('done') },
    { label: 'Mark Read', icon: _readIco, action: () => _bulkAction('read') },
    { label: 'Mark Unread', icon: _unreadIco, action: () => _bulkAction('unread') },
  ];
  for (const a of items) {
    const it = document.createElement('div');
    it.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
    it.innerHTML = `<span class="dropdown-icon">${a.icon}</span><span>${a.label}</span>`;
    it.addEventListener('click', (e) => { e.stopPropagation(); close(); a.action(); });
    dropdown.appendChild(it);
  }
  // Mobile-only Cancel — matches the per-card and sidebar dropdowns.
  const _cancelIco2 = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const cancelIt = document.createElement('div');
  cancelIt.className = 'dropdown-item-compact dropdown-cancel-mobile';
  cancelIt.innerHTML = `<span class="dropdown-icon">${_cancelIco2}</span><span>Cancel</span>`;
  cancelIt.addEventListener('click', (e) => {
    e.stopPropagation();
    close();
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
  const close = bindMenuDismiss(dropdown, () => {
    dropdown.remove();
  }, (ev) => !dropdown.contains(ev.target) && ev.target !== anchor);
}

export function _updateBulkBar() {
  const bar = document.getElementById('email-lib-bulk');
  const grid = document.getElementById('email-lib-grid');
  if (grid) grid.classList.toggle('email-selecting', !!state._selectMode);
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

export async function _bulkAction(action) {
  const uids = Array.from(state._selectedUids);
  if (uids.length === 0) return;
  let failedReadSync = 0;
  const failedUids = new Set();
  if (action === 'delete') {
    const ok = await styledConfirm(
      `Delete ${uids.length} selected email${uids.length === 1 ? '' : 's'}?`,
      { confirmText: 'Delete', cancelText: 'Cancel', danger: true },
    );
    if (!ok) return;
    // Deletion is intentionally optimistic. The user should not have to keep
    // the mail window open while IMAP moves a batch; refresh and restore the
    // cards only if the provider rejects the background operation.
    const removed = new Set(uids.map(uid => String(uid)));
    state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
    state._selectedUids.clear();
    state._selectMode = false;
    _updateBulkBar();
    _renderGrid();
    _libCacheWriteBack();
    showToast(`Moved ${uids.length} email${uids.length === 1 ? '' : 's'} to Trash`);
    fetch(`${API_BASE}/api/email/delete-bulk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uids, folder: state._libFolder, account_id: state._libAccountId || null }),
    }).then(async (res) => {
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.success === false || (data?.failed_uids || []).length) {
        throw new Error(data?.error || `${data?.failed_uids?.length || 0} emails could not be deleted`);
      }
    }).catch((error) => {
      console.error('Background bulk email delete failed:', error);
      showToast('Delete failed; restoring mailbox');
      _loadEmailsFresh({ force: true, useCache: false, refreshAfterCache: false }).catch(() => {});
    });
    return;
  }

  const deleteBtn = action === 'delete' ? document.getElementById('email-lib-bulk-delete') : null;
  const actionsBtn = document.getElementById('email-lib-bulk-actions');
  const cancelBtn = document.getElementById('email-lib-bulk-cancel');
  const selectAll = document.getElementById('email-lib-select-all');
  const countEl = document.getElementById('email-lib-selected-count');
  const originalDeleteHtml = deleteBtn?.innerHTML || '';
  const originalCountText = countEl?.textContent || '';
  let busySpinner = null;
  // Loading state for every bulk action, not just delete — large
  // selections (e.g. 90+ Dones) used to silently hammer the server
  // with sequential requests and the user got zero feedback. Now the
  // Actions button (or Delete button) shows a whirlpool + verb-ing
  // label, and the count surfaces progress.
  const verbing = {
    delete: 'Deleting',
    archive: 'Archiving',
    done: 'Marking done',
    read: 'Marking read',
    unread: 'Marking unread',
  }[action] || 'Updating';
  const targetBtn = action === 'delete' ? deleteBtn : actionsBtn;
  let originalTargetHtml = '';
  if (targetBtn) {
    originalTargetHtml = targetBtn.innerHTML;
    targetBtn.disabled = true;
    targetBtn.classList.add('email-bulk-loading');
    targetBtn.innerHTML = `<span class="email-bulk-loading-label">${verbing}</span>`;
    busySpinner = spinnerModule.create('', 'clean', 'whirlpool');
    const spEl = busySpinner.createElement();
    spEl.classList.add('email-bulk-whirlpool');
    targetBtn.appendChild(spEl);
    busySpinner.start();
  }
  if (action !== 'delete' && deleteBtn) deleteBtn.disabled = true;
  if (action === 'delete' && actionsBtn) actionsBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  if (selectAll) selectAll.disabled = true;
  if (countEl) countEl.textContent = `${verbing} ${uids.length}…`;
  const deleteOverlays = action === 'delete'
    ? uids.map(uid => {
        const card = document.querySelector(`#email-lib-grid .doclib-card[data-uid="${CSS.escape(String(uid))}"]`);
        return _showEmailDeleteOverlay(card);
      }).filter(Boolean)
    : [];
  if (deleteOverlays.length) {
    await Promise.all(deleteOverlays.map(busy => busy.ready).filter(Boolean));
  }

  // Single-uid worker.
  const handleOne = async (uid) => {
    try {
      if (action === 'archive') {
        const res = await fetch(`${API_BASE}/api/email/archive/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        const data = await res.json().catch(() => null);
        if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      } else if (action === 'delete') {
        // Bulk Delete matches the permanent-delete behavior used by the
        // library and albums. Moving a batch through provider Trash folders
        // is unreliable on several IMAP implementations.
        const res = await fetch(`${API_BASE}/api/email/delete-permanent/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'DELETE' });
        const data = await res.json().catch(() => null);
        if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      } else if (action === 'done') {
        // uid may come back from the Set as a string while em.uid is
        // numeric (or vice versa) — coerce both sides so the in-memory
        // state actually flips and the post-loop re-render shows the
        // done checkmark.
        const em = state._libEmails.find(e => String(e.uid) === String(uid));
        if (em) {
          em.is_answered = true;
          em.is_read = true;
          _clearDoneResponseTagsLocal(em);
        }
        const ansRes = await fetch(`${API_BASE}/api/email/mark-answered/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        const readRes = await fetch(`${API_BASE}/api/email/mark-read/${uid}?folder=${encodeURIComponent(state._libFolder)}${_acct()}`, { method: 'POST' });
        if (!ansRes.ok || !readRes.ok) throw new Error(`mark-done HTTP ${ansRes.status}/${readRes.status}`);
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
      failedUids.add(String(uid));
      console.error(`Failed to ${action} ${uid}:`, e);
    }
  };

  try {
    // Run in parallel with a concurrency cap so 92 emails don't take
    // 30 seconds sequentially but we also don't open 92 simultaneous
    // connections.
    const CONCURRENCY = 6;
    const queue = uids.slice();
    let inFlight = 0;
    let nextSlot = 0;
    let finishedCount = 0;
    let bulkDeletedUids = null;
    if (action === 'delete') {
      const res = await fetch(`${API_BASE}/api/email/delete-bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uids, folder: state._libFolder, account_id: state._libAccountId || null }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      for (const uid of data?.failed_uids || []) failedUids.add(String(uid));
      bulkDeletedUids = (data?.deleted_uids || []).map(String);
      if (countEl) countEl.textContent = `${verbing} ${bulkDeletedUids.length}/${uids.length}…`;
    } else await new Promise((resolve) => {
      const launch = () => {
        while (inFlight < CONCURRENCY && nextSlot < queue.length) {
          const uid = queue[nextSlot++];
          inFlight++;
          handleOne(uid).finally(() => {
            inFlight--;
            finishedCount++;
            if (countEl) countEl.textContent = `${verbing} ${finishedCount}/${queue.length}…`;
            if (nextSlot >= queue.length && inFlight === 0) resolve();
            else launch();
          });
        }
        if (queue.length === 0) resolve();
      };
      launch();
    });

    if (action === 'archive' || action === 'delete') {
      const successfulUids = bulkDeletedUids || uids.filter(uid => !failedUids.has(String(uid)));
      if (action === 'delete') {
        deleteOverlays.forEach(busy => busy.remove?.());
      }
      await _animateEmailCardRemoval(successfulUids);
      const removed = new Set(successfulUids.map(uid => String(uid)));
      state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
    } else if (action === 'done' && state._libFilter === 'undone') {
      // The undone filter is a "show only not-done" view — after marking
      // selected emails done, they no longer match. Animate them out and
      // drop them from the local list so the view reflects the filter
      // instead of leaving freshly-done cards sitting there.
      await _animateEmailCardRemoval(uids);
      const removed = new Set(uids.map(uid => String(uid)));
      state._libEmails = state._libEmails.filter(e => !removed.has(String(e.uid)));
    }
  } finally {
    deleteOverlays.forEach(busy => busy.remove?.());
    if (busySpinner) busySpinner.destroy();
    // Restore whichever button we hijacked (delete vs actions).
    if (targetBtn) {
      targetBtn.disabled = false;
      targetBtn.classList.remove('email-bulk-loading');
      targetBtn.innerHTML = originalTargetHtml || targetBtn.innerHTML;
    }
    if (deleteBtn && deleteBtn !== targetBtn) {
      deleteBtn.disabled = false;
      deleteBtn.innerHTML = originalDeleteHtml || deleteBtn.innerHTML;
    }
    if (actionsBtn && actionsBtn !== targetBtn) actionsBtn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = false;
    if (selectAll) selectAll.disabled = false;
    if (countEl) countEl.textContent = originalCountText;
  }
  state._selectedUids.clear();
  state._selectMode = false;
  _updateBulkBar();
  _renderGrid();
  if (failedReadSync > 0) {
    showToast(`Failed to update ${failedReadSync} email${failedReadSync === 1 ? '' : 's'}`);
  }
  if (failedUids.size > 0 && (action === 'delete' || action === 'archive')) {
    showToast(`${failedUids.size} email${failedUids.size === 1 ? '' : 's'} could not be ${action === 'delete' ? 'deleted' : 'archived'}`);
  }
  // Sync successful local mutations into the SWR cache so reopen doesn't
  // briefly show the pre-bulk state.
  _libCacheWriteBack();
}
