// static/js/emailLibrary/settingsPage.js
//
// The Email Settings page: the in-panel settings view, its form markup and
// controls, the away/auto-reply configuration, and the display preferences
// (inline images, tag visibility, compact rows).
//
// Two things here reach outside settings, which is why they live together.
// Turning away-replies on writes a matching calendar event, so this module owns
// the `odysseus.email.autoReplyCalendarEvent.*` keys and the create/update/
// delete sync against /api/calendar. And the inline-image preference is read by
// ./bodyRender.js while rendering a message, not only by the settings form.

import spinnerModule from '../spinner.js';
import { emailApiUrl } from '../emailShared.js';
import { showToast } from '../ui.js?v=20260916largetoolscroll1';
import { state } from './state.js';
import { _esc } from './utils.js';
import { _wireEmailInlineImages } from './bodyRender.js';
import {
  _loadAccounts,
  _loadEmails,
  _loadFolders,
  _loadedEmailsHaveVisibleTags,
  _notifyNoLoadedEmailTags,
  _openTasksForEmailTags,
  _publishActiveAccount,
  _refreshUnreadBadge,
  _renderAccountsStrip,
  _renderGrid,
  _resetEmailListForFreshLoad,
} from './index.js';
import { _openUnsubscribeReviewModal } from './unsubscribe.js';

const API_BASE = window.location.origin;

function _emailInlineImagesStorageKey(accountId = state._libAccountId) {
  return `odysseus.email.viewInlineImages.${String(accountId || 'default')}`;
}

export function _readEmailInlineImagesPreference(accountId = state._libAccountId) {
  try {
    const stored = localStorage.getItem(_emailInlineImagesStorageKey(accountId));
    return stored === null ? true : stored !== '0';
  } catch (_) {
    return true;
  }
}

function _writeEmailInlineImagesPreference(enabled, accountId = state._libAccountId) {
  try {
    localStorage.setItem(_emailInlineImagesStorageKey(accountId), enabled ? '1' : '0');
  } catch (_) {}
}

export async function _disableInlineImages(accountId = state._libAccountId) {
  state._libViewInlineImages = false;
  _writeEmailInlineImagesPreference(false, accountId);
  document.querySelectorAll('#email-settings-view-inline-images').forEach(toggle => {
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
  });
  try {
    const res = await fetch(emailApiUrl('/api/email/config', {
      account_id: accountId || undefined,
    }), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email_view_inline_images: false }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.error('Failed to disable inline images:', err);
  }
}

export const _EMAIL_SETTINGS_ICON = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06A2 2 0 1 1 7.04 4.3l.06.06A1.65 1.65 0 0 0 8.92 4a1.65 1.65 0 0 0 1-1.51V2a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82 1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>`;
const _EMAIL_SAVE_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>';
const _EMAIL_SAVED_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

function _setEmailSaveIcon(button, saved) {
  if (!button) return;
  const icon = button.querySelector('svg');
  if (icon) icon.outerHTML = saved ? _EMAIL_SAVED_ICON : _EMAIL_SAVE_ICON;
  button.classList.toggle('is-saved', !!saved);
}
const _DEFAULT_AUTO_REPLY_SUBJECT = '(Away) {subject}';
const _DEFAULT_AUTO_REPLY_MESSAGE = "Thanks for your email. I'm away and may be slower to reply.";

function _normalizeAutoReplyConfig(cfg = {}) {
  const normalized = Object.prototype.hasOwnProperty.call(cfg, 'enabled');
  return {
    enabled: normalized ? !!cfg.enabled : !!cfg.email_auto_reply,
    start: String(normalized ? (cfg.start || '') : (cfg.email_auto_reply_start || '')),
    end: String(normalized ? (cfg.end || '') : (cfg.email_auto_reply_end || '')),
    subject: String(normalized ? (cfg.subject || _DEFAULT_AUTO_REPLY_SUBJECT) : (cfg.email_auto_reply_subject || _DEFAULT_AUTO_REPLY_SUBJECT)),
    message: String(normalized ? (cfg.message || '') : (cfg.email_auto_reply_message || '')),
    cooldown: String(normalized ? (cfg.cooldown || 'period') : (cfg.email_auto_reply_cooldown || 'period')),
    scope: String(normalized ? (cfg.scope || 'all') : (cfg.email_auto_reply_scope || 'all')),
    accountId: String(normalized ? (cfg.accountId || '') : (cfg.email_auto_reply_account_id || '')),
    excludeAutomated: normalized ? cfg.excludeAutomated !== false : cfg.email_auto_reply_exclude_automated !== false,
    pauseNotifications: normalized ? !!cfg.pauseNotifications : !!cfg.email_auto_reply_pause_notifications,
    viewInlineImages: normalized ? cfg.viewInlineImages !== false : cfg.email_view_inline_images !== false,
  };
}

function _autoReplyDateValue(value) {
  const s = String(value || '').trim();
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : '';
}

function _todayDateInputValue() {
  const now = new Date();
  const pad = value => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const _AUTO_REPLY_CALENDAR_KEY_PREFIX = 'odysseus.email.autoReplyCalendarEvent.';
const _autoReplyCalendarSyncs = new Map();

function _autoReplyCalendarKey(accountId) {
  return `${_AUTO_REPLY_CALENDAR_KEY_PREFIX}${String(accountId || '').trim()}`;
}

function _readAutoReplyCalendarUid(accountId) {
  if (!accountId) return '';
  try { return String(localStorage.getItem(_autoReplyCalendarKey(accountId)) || ''); } catch (_) { return ''; }
}

function _writeAutoReplyCalendarUid(accountId, uid) {
  if (!accountId) return;
  try {
    if (uid) localStorage.setItem(_autoReplyCalendarKey(accountId), uid);
    else localStorage.removeItem(_autoReplyCalendarKey(accountId));
  } catch (_) {}
}

function _addLocalCalendarDays(value, days) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return '';
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days);
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function _autoReplyCalendarPayload(cfg, accountId = state._libAccountId) {
  const start = _autoReplyDateValue(cfg?.start) || _todayDateInputValue();
  let end = _autoReplyDateValue(cfg?.end) || start;
  if (end < start) end = start;
  return {
    summary: 'Email Auto Reply (away)',
    dtstart: start,
    dtend: _addLocalCalendarDays(end, 1),
    all_day: true,
    description: `Odysseus email auto reply - account:${String(accountId || '')}`,
  };
}

async function _findAutoReplyCalendarEventUids(cfg, accountId) {
  const start = _autoReplyDateValue(cfg?.start) || _todayDateInputValue();
  const end = _autoReplyDateValue(cfg?.end) || start;
  const startYear = Number(start.slice(0, 4)) || new Date().getFullYear();
  const endYear = Math.max(startYear, Number(end.slice(0, 4)) || startYear);
  try {
    const response = await fetch(`${API_BASE}/api/calendar/events?start=${startYear - 1}-01-01&end=${endYear + 2}-01-01`, {
      credentials: 'same-origin',
    });
    if (!response.ok) return [];
    const data = await response.json().catch(() => ({}));
    const marker = `Odysseus email auto reply - account:${String(accountId || '')}`;
    return (data.events || [])
      .filter(ev => String(ev?.description || '').startsWith(marker))
      .map(ev => String(ev?.uid || '').trim())
      .filter(Boolean);
  } catch (_) {
    return [];
  }
}

async function _syncAutoReplyCalendarEventNow(cfg, accountId) {
  accountId = String(accountId || '').trim();
  if (!accountId) return false;
  let uid = _readAutoReplyCalendarUid(accountId);
  const headers = { 'Content-Type': 'application/json' };
  const request = (url, options = {}) => fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });

  if (!cfg?.enabled) {
    const knownUids = new Set(uid ? [uid] : []);
    (await _findAutoReplyCalendarEventUids(cfg, accountId)).forEach(foundUid => knownUids.add(foundUid));
    for (const eventUid of knownUids) {
      const response = await request(`${API_BASE}/api/calendar/events/${encodeURIComponent(eventUid)}`, { method: 'DELETE' });
      if (!response.ok && response.status !== 404) throw new Error(`Calendar HTTP ${response.status}`);
    }
    _writeAutoReplyCalendarUid(accountId, '');
    return true;
  }

  const payload = _autoReplyCalendarPayload(cfg, accountId);
  if (!uid) {
    uid = (await _findAutoReplyCalendarEventUids(cfg, accountId))[0] || '';
    if (uid) _writeAutoReplyCalendarUid(accountId, uid);
  }
  if (uid) {
    const response = await request(`${API_BASE}/api/calendar/events/${encodeURIComponent(uid)}`, {
      method: 'PUT', body: JSON.stringify(payload),
    });
    if (response.ok) return true;
    if (response.status !== 404) throw new Error(`Calendar HTTP ${response.status}`);
    _writeAutoReplyCalendarUid(accountId, '');
  }

  const response = await request(`${API_BASE}/api/calendar/events`, {
    method: 'POST', body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.uid) throw new Error(`Calendar HTTP ${response.status}`);
  _writeAutoReplyCalendarUid(accountId, data.uid);
  return true;
}

export function _syncAutoReplyCalendarEvent(cfg) {
  const accountId = String(state._libAccountId || '').trim();
  if (!accountId) return Promise.resolve(false);
  const previous = _autoReplyCalendarSyncs.get(accountId) || Promise.resolve();
  const next = previous.catch(() => {}).then(() => _syncAutoReplyCalendarEventNow(cfg, accountId));
  _autoReplyCalendarSyncs.set(accountId, next);
  return next.finally(() => {
    if (_autoReplyCalendarSyncs.get(accountId) === next) _autoReplyCalendarSyncs.delete(accountId);
  });
}

export function _isAutoReplyActiveForCurrentAccount(cfg) {
  cfg = _normalizeAutoReplyConfig(cfg || {});
  if (!cfg.enabled) return false;
  if (cfg.scope === 'account' && cfg.accountId && cfg.accountId !== String(state._libAccountId || '')) return false;
  const today = new Date().toISOString().slice(0, 10);
  const start = _autoReplyDateValue(cfg.start);
  const end = _autoReplyDateValue(cfg.end);
  if (start && today < start) return false;
  if (end && today > end) return false;
  return true;
}

export function _syncEmailAutoReplyTitle(active) {
  const titleText = document.getElementById('email-lib-title-text');
  const statusDot = document.getElementById('email-lib-auto-reply-status-dot');
  const badge = document.getElementById('email-lib-auto-reply-badge');
  if (titleText) titleText.textContent = active ? 'Auto Reply - Active' : 'Email';
  if (statusDot) statusDot.style.display = active ? 'inline-block' : 'none';
  if (badge) {
    badge.style.display = 'none';
    badge.textContent = 'Auto Reply';
  }
}

export async function _fetchEmailSettingsConfig() {
  const res = await fetch(emailApiUrl('/api/email/config', {
    account_id: state._libAccountId || undefined,
  }), { credentials: 'include' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const normalized = _normalizeAutoReplyConfig(await res.json());
  state._libViewInlineImages = normalized.viewInlineImages;
  _writeEmailInlineImagesPreference(state._libViewInlineImages);
  return normalized;
}

export async function _fetchEmailWritingStyle() {
  const res = await fetch(emailApiUrl('/api/email/style', {
    account_id: state._libAccountId || undefined,
  }), { credentials: 'include' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json().catch(() => ({}));
  return String(data.style || '');
}

export function _emailSettingsLoadingHtml(label = 'Loading') {
  const wp = spinnerModule.createWhirlpool(16);
  const host = document.createElement('span');
  host.className = 'email-settings-loading-spinner';
  host.appendChild(wp.element);
  const wrap = document.createElement('div');
  wrap.className = 'email-settings-loading';
  wrap.appendChild(host);
  const text = document.createElement('span');
  text.textContent = label;
  wrap.appendChild(text);
  return wrap.outerHTML;
}

export function _emailSettingsFormHtml(cfg) {
  const activeAccount = state._libAccounts?.find(a => a && a.id === state._libAccountId);
  const accountAddress = activeAccount
    ? String(activeAccount.from_address || activeAccount.imap_user || activeAccount.email || '').trim()
    : '';
  const accountScope = accountAddress
    ? ` <span class="email-settings-account-scope">(${_esc(accountAddress)})</span>`
    : '';
  return `
    <div class="admin-card email-settings-section email-settings-auto-reply-section">
      <div class="email-settings-section-head">
        <div>
          <div class="email-settings-auto-reply-title-row">
            <div class="email-settings-section-title"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg><span>Auto Reply${accountScope}</span></div>
            <div class="email-settings-enabled-control">
              <span class="email-settings-enabled-state"></span>
              <label class="email-settings-toggle admin-switch">
                <input type="checkbox" id="email-auto-reply-enabled" ${cfg.enabled ? 'checked' : ''}>
                <span class="admin-slider"></span>
              </label>
            </div>
          </div>
          <div class="email-settings-section-desc">Holiday or away replies for incoming mail.</div>
        </div>
      </div>
      <div id="email-settings-auto-reply-body" class="email-settings-section-body">
        <div class="email-settings-grid">
          <label><span>Start date</span><input type="date" id="email-auto-reply-start" value="${_esc(_autoReplyDateValue(cfg.start))}"></label>
          <label><span>End date</span><input type="date" id="email-auto-reply-end" value="${_esc(_autoReplyDateValue(cfg.end))}"></label>
        </div>
        <label class="email-settings-field">
          <span>Subject</span>
          <input type="text" id="email-auto-reply-subject" value="${_esc(cfg.subject || _DEFAULT_AUTO_REPLY_SUBJECT)}" placeholder="${_esc(_DEFAULT_AUTO_REPLY_SUBJECT)}">
        </label>
        <label class="email-settings-field">
          <span>Message</span>
          <textarea id="email-auto-reply-message" rows="4">${_esc(cfg.message || _DEFAULT_AUTO_REPLY_MESSAGE)}</textarea>
        </label>
        <label class="email-settings-field">
          <span>Send to same sender</span>
          <select id="email-auto-reply-cooldown">
            <option value="period" ${cfg.cooldown === 'period' ? 'selected' : ''}>Once while active</option>
            <option value="1d" ${cfg.cooldown === '1d' ? 'selected' : ''}>Every day</option>
            <option value="3d" ${cfg.cooldown === '3d' ? 'selected' : ''}>Every 3 days</option>
            <option value="7d" ${cfg.cooldown === '7d' ? 'selected' : ''}>Every 7 days</option>
          </select>
        </label>
        <label class="email-settings-check">
          <span>Pause email notifications while active</span>
          <span class="email-settings-check-spacer"></span>
          <span class="admin-switch"><input type="checkbox" id="email-auto-reply-pause" ${cfg.pauseNotifications ? 'checked' : ''}><span class="admin-slider"></span></span>
        </label>
      </div>
    </div>
  `;
}

export function _emailCleanupSettingsHtml() {
  const activeAccount = state._libAccounts?.find(a => a && a.id === state._libAccountId);
  const accountAddress = activeAccount
    ? String(activeAccount.from_address || activeAccount.imap_user || activeAccount.email || '').trim()
    : '';
  const accountScope = accountAddress
    ? ` <span class="email-settings-account-scope">(${_esc(accountAddress)})</span>`
    : '';
  return `
    <div class="admin-card email-settings-section email-settings-cleanup-section">
      <div class="email-settings-section-head">
        <div>
          <span class="email-settings-section-title"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg><span>Newsletter Unsubscribe${accountScope}</span></span>
          <div class="email-settings-section-desc">Find newsletter and ad emails with unsubscribe options.</div>
        </div>
      </div>
      <div id="email-settings-cleanup-body" class="email-settings-section-body">
        <div class="email-settings-clean-actions">
          <span class="email-unsub-status email-settings-clean-status" style="font-size:12px;color:color-mix(in srgb,var(--fg) 68%,transparent);"></span>
          <button type="button" class="memory-toolbar-btn email-settings-clean-btn">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="vertical-align:-1px;margin-right:2px;color:var(--accent,var(--red));"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
            <span>Clean</span>
          </button>
          <button type="button" class="memory-toolbar-btn email-settings-save">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>
            Save
          </button>
        </div>
      </div>
    </div>
  `;
}

export function _emailDisplaySettingsHtml(cfg = {}) {
  return `
    <div class="admin-card email-settings-section email-settings-display-section">
      <div class="email-settings-section-head">
        <div>
          <div class="email-settings-display-title-row">
            <div class="email-settings-section-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41 11 3.83A2 2 0 0 0 9.59 3H4a1 1 0 0 0-1 1v5.59A2 2 0 0 0 3.59 11l9.59 9.59a2 2 0 0 0 2.82 0l4.59-4.59a2 2 0 0 0 0-2.82Z"/><path d="M7 7h.01"/></svg><span>Show Email Tags</span></div>
            <span class="email-settings-enabled-control">
              <span class="email-settings-enabled-state email-settings-display-enabled-state"></span>
              <label class="email-settings-toggle admin-switch">
                <input type="checkbox" id="email-settings-show-tags" ${state._libShowTags ? 'checked' : ''}>
                <span class="admin-slider"></span>
              </label>
            </span>
          </div>
          <div class="email-settings-section-desc email-settings-support-copy">Choose which labels appear on email cards. <span class="email-settings-display-task-note">Turn off tagging in <button type="button" class="email-settings-inline-link" data-open-email-tasks>Tasks</button></span></div>
        </div>
      </div>
      <div class="email-settings-display-spacer" aria-hidden="true"></div>
    </div>
    <div class="admin-card email-settings-section email-settings-inline-images-section">
      <div class="email-settings-section-head">
        <div>
          <div class="email-settings-display-title-row">
            <div class="email-settings-section-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg><span>Hide Inline Images</span></div>
            <span class="email-settings-enabled-control">
              <span class="email-settings-enabled-state email-settings-inline-images-enabled-state"></span>
              <label class="email-settings-toggle admin-switch">
                <input type="checkbox" id="email-settings-view-inline-images" ${cfg.viewInlineImages === false ? 'checked' : ''}>
                <span class="admin-slider"></span>
              </label>
            </span>
          </div>
          <div class="email-settings-section-desc email-settings-support-copy">Keep images embedded in email bodies hidden until you load them.</div>
        </div>
      </div>
      <div class="email-settings-inline-images-spacer" aria-hidden="true"></div>
    </div>
  `;
}

export function _emailSettingsAccountSelectHtml() {
  const accounts = Array.isArray(state._libAccounts)
    ? state._libAccounts
      .filter(a => a && a.enabled !== false)
      .slice()
      .sort((a, b) => Number(!!b.is_default) - Number(!!a.is_default))
    : [];
  if (!accounts.length) return '';
  const nameCounts = new Map();
  accounts.forEach(a => {
    const name = String(a.name || '').trim();
    if (name) nameCounts.set(name, (nameCounts.get(name) || 0) + 1);
  });
  const opts = accounts.map(a => {
    const name = String(a.name || '').trim();
    const address = String(a.from_address || a.imap_user || '').trim();
    const label = name && nameCounts.get(name) > 1 && address
      ? `${name} · ${address}`
      : (name || address || 'account');
    const suffix = a.is_default ? ' · (default)' : '';
    return `<option value="${_esc(a.id || '')}" ${state._libAccountId === a.id ? 'selected' : ''}>${_esc(label + suffix)}</option>`;
  }).join('');
  const selected = accounts.find(a => a.id === state._libAccountId) || accounts[0];
  const active = !!selected?.is_default;
  const icon = active
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="8 12 11 15 16 9"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/></svg>';
  const defaultTitle = active ? 'Default account' : 'Make this the default account';
  return `<div class="email-settings-account-picker" style="margin-left:auto;display:inline-flex;align-items:center;gap:8px;">`
    + `<span class="cookbook-srv-default email-settings-default${active ? ' active' : ''}" role="button" tabindex="0" aria-pressed="${active ? 'true' : 'false'}" data-email-default-id="${_esc(selected?.id || '')}" title="${defaultTitle}"><span class="cookbook-srv-default-icon" aria-hidden="true">${icon}</span><span class="cookbook-srv-default-label">default</span></span>`
    + `<select id="email-settings-account-select" class="memory-toolbar-btn" title="Active email for all email settings" style="height:28px;max-width:240px;position:relative;top:-2px;">${opts}</select>`
    + `</div>`;
}

export function _syncEmailSettingsAccountPicker(page) {
  const select = page?.querySelector('#email-settings-account-select');
  const selected = (state._libAccounts || []).find(a => a && a.id === state._libAccountId);
  if (select && selected) {
    [...select.options]
      .sort((a, b) => {
        const aAccount = (state._libAccounts || []).find(account => account && account.id === a.value);
        const bAccount = (state._libAccounts || []).find(account => account && account.id === b.value);
        return Number(!!bAccount?.is_default) - Number(!!aAccount?.is_default);
      })
      .forEach(option => select.appendChild(option));
    select.value = selected.id;
    [...select.options].forEach(option => {
      const account = (state._libAccounts || []).find(a => a && a.id === option.value);
      if (!account) return;
      const label = account.name || account.from_address || account.imap_user || 'account';
      option.textContent = label + (account.is_default ? ' · (default)' : '');
    });
  }
  const toggle = page?.querySelector('.email-settings-default');
  if (!toggle || !selected) return;
  const active = !!selected.is_default;
  toggle.classList.toggle('active', active);
  toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
  toggle.dataset.emailDefaultId = selected.id || '';
  toggle.title = active ? 'Default account' : 'Make this the default account';
  toggle.querySelector('.cookbook-srv-default-icon').innerHTML = active
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="8 12 11 15 16 9"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/></svg>';
}

export function _emailWritingStyleHtml(style) {
  return `
    <div class="admin-card email-settings-section email-settings-style-section">
      <div class="email-settings-section-head">
        <div>
          <div class="email-settings-section-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg><span>Email Reply Writing Style</span></div>
          <div class="email-settings-section-desc email-settings-support-copy">Used when Odysseus drafts replies for you.</div>
        </div>
      </div>
      <div id="email-settings-style-body" class="email-settings-section-body">
        <label class="email-settings-field">
        <span>Writing style</span>
        <textarea id="email-writing-style-text" rows="6">${_esc(style)}</textarea>
        </label>
        <div class="email-settings-actions">
          <button type="button" class="memory-toolbar-btn email-style-settings-extract">
            <svg class="email-unsub-accent-icon" width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
            Extract
          </button>
          <button class="email-style-extract-help" aria-label="What does Extract do?" title="Extract analyzes sent emails from the selected account and saves a writing-style prompt for AI replies.">?</button>
          <button type="button" class="memory-toolbar-btn email-style-settings-save">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>
            Save
          </button>
        </div>
      </div>
    </div>
  `;
}

export async function _showEmailSettingsPage() {
  const modal = document.getElementById('email-lib-modal');
  const page = document.getElementById('email-lib-settings-page');
  const btn = document.getElementById('email-lib-settings-btn');
  if (!modal || !page) return;
  modal.classList.add('email-settings-mode');
  page.hidden = false;
  page.scrollTop = 0;
  page.querySelector('.email-settings-body')?.scrollTo(0, 0);
  const headerBackBtn = document.getElementById('email-settings-header-back');
  if (headerBackBtn) headerBackBtn.style.display = 'inline-flex';
  btn?.classList.add('active');
  btn?.setAttribute('aria-expanded', 'true');
  page.innerHTML = `
    <div class="email-settings-page-head">
      <h2 class="email-settings-title">${_EMAIL_SETTINGS_ICON}<span>Email Settings</span></h2>
    </div>
    <div class="email-settings-body">
      ${_emailSettingsLoadingHtml()}
    </div>
  `;
  let cfg;
  let writingStyle = '';
  try {
    await _loadAccounts().catch(() => {});
    const head = page.querySelector('.email-settings-page-head');
    const accountSelectHtml = _emailSettingsAccountSelectHtml();
    const oldPicker = page.querySelector('.email-settings-account-picker');
    if (oldPicker) oldPicker.remove();
    const title = head?.querySelector('.email-settings-title');
    if (title && accountSelectHtml) title.insertAdjacentHTML('beforeend', accountSelectHtml);
    [cfg, writingStyle] = await Promise.all([
      _fetchEmailSettingsConfig(),
      _fetchEmailWritingStyle().catch(() => ''),
    ]);
  } catch (err) {
    page.querySelector('.email-settings-body').innerHTML = `<div class="email-settings-error">Could not load settings.</div>`;
    return;
  }
  page.querySelector('.email-settings-body').innerHTML = _emailWritingStyleHtml(writingStyle) + _emailDisplaySettingsHtml(cfg) + _emailCleanupSettingsHtml() + _emailSettingsFormHtml(cfg);
  page.querySelector('.email-settings-body')?.scrollTo(0, 0);
  state._libAutoReplyActive = _isAutoReplyActiveForCurrentAccount(cfg);
  state._libAutoReplyDraftActive = null;
  _syncEmailAutoReplyTitle(state._libAutoReplyActive);
  _renderAccountsStrip();
  _syncAutoReplyCalendarEvent(cfg).catch((err) => console.warn('Failed to reconcile auto-reply calendar event:', err));
    page.querySelector('#email-settings-account-select')?.addEventListener('change', async (ev) => {
      state._libAccountId = ev.currentTarget.value || null;
      state._libAutoReplyDraftActive = null;
      _publishActiveAccount();
      _syncEmailSettingsAccountPicker(page);
      _renderAccountsStrip();
    _resetEmailListForFreshLoad();
    _loadEmails({ useCache: true });
    _loadFolders({ resetMissing: true }).catch(() => {});
    page.querySelector('.email-unsubscribe-inline-panel')?.remove();
    const body = page.querySelector('.email-settings-body');
    if (body) body.innerHTML = _emailSettingsLoadingHtml();
    try {
      const [nextCfg, nextStyle] = await Promise.all([
        _fetchEmailSettingsConfig(),
        _fetchEmailWritingStyle().catch(() => ''),
      ]);
      if (body) body.innerHTML = _emailWritingStyleHtml(nextStyle) + _emailDisplaySettingsHtml(nextCfg) + _emailCleanupSettingsHtml() + _emailSettingsFormHtml(nextCfg);
      state._libAutoReplyActive = _isAutoReplyActiveForCurrentAccount(nextCfg);
      state._libAutoReplyDraftActive = null;
      _syncEmailAutoReplyTitle(state._libAutoReplyActive);
      _renderAccountsStrip();
      _syncAutoReplyCalendarEvent(nextCfg).catch((err) => console.warn('Failed to reconcile auto-reply calendar event:', err));
      _bindEmailSettingsPageControls(page);
    } catch (_) {
      if (body) body.innerHTML = `<div class="email-settings-error">Could not load settings.</div>`;
      }
    });
  page.querySelector('.email-settings-default')?.addEventListener('click', async (ev) => {
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
      _syncEmailSettingsAccountPicker(page);
      _renderAccountsStrip();
    } catch (err) {
      console.error('Set default account failed:', err);
    }
  });
  page.querySelector('.email-settings-default')?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    ev.currentTarget.click();
  });
  _bindEmailSettingsPageControls(page);
}

export function _bindEmailSettingsPageControls(page) {
  page.querySelector('.email-settings-clean-btn')?.addEventListener('click', (ev) => {
    // Clean is an explicit fresh scan action; do not reopen stale cached
    // candidates when the user asks to clean again.
    _openUnsubscribeReviewModal(ev.currentTarget, { forceRescan: true });
  });
  const showTagsToggle = page.querySelector('#email-settings-show-tags');
  const syncDisplayTagsState = () => {
    const section = page.querySelector('.email-settings-display-section');
    const stateLabel = page.querySelector('.email-settings-display-enabled-state');
    const enabled = !!showTagsToggle?.checked;
    section?.classList.toggle('is-disabled', !enabled);
    section?.classList.toggle('is-enabled', enabled);
    if (stateLabel) stateLabel.textContent = enabled ? 'Show' : 'Hide';
  };
  showTagsToggle?.addEventListener('change', (ev) => {
    state._libShowTags = !!ev.target.checked;
    localStorage.setItem('odysseus.email.showTags', state._libShowTags ? '1' : '0');
    _renderGrid();
    document.dispatchEvent(new CustomEvent('odysseus:email-tags-toggle', { detail: { show: state._libShowTags } }));
    if (state._libShowTags && !_loadedEmailsHaveVisibleTags()) _notifyNoLoadedEmailTags();
    syncDisplayTagsState();
  });
  syncDisplayTagsState();
  const inlineImagesToggle = page.querySelector('#email-settings-view-inline-images');
  const syncInlineImagesState = () => {
    const section = page.querySelector('.email-settings-inline-images-section');
    const stateLabel = page.querySelector('.email-settings-inline-images-enabled-state');
    const hidden = !!inlineImagesToggle?.checked;
    section?.classList.toggle('is-disabled', !hidden);
    section?.classList.toggle('is-enabled', hidden);
    if (stateLabel) stateLabel.textContent = hidden ? 'Hide' : 'Show';
  };
  inlineImagesToggle?.addEventListener('change', async (ev) => {
    const hidden = !!ev.target.checked;
    const enabled = !hidden;
    const previous = state._libViewInlineImages;
    state._libViewInlineImages = enabled;
    _writeEmailInlineImagesPreference(enabled);
    syncInlineImagesState();
    try {
      const res = await fetch(emailApiUrl('/api/email/config', {
        account_id: state._libAccountId || undefined,
      }), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email_view_inline_images: enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      document.querySelectorAll('.email-card-reader').forEach(reader => _wireEmailInlineImages(reader));
    } catch (err) {
      state._libViewInlineImages = previous;
      _writeEmailInlineImagesPreference(previous);
      ev.target.checked = previous === false;
      syncInlineImagesState();
      showToast?.('Failed to save inline image setting');
      console.error('Failed to save inline image setting:', err);
    }
  });
  syncInlineImagesState();
  page.querySelector('[data-open-email-tasks]')?.addEventListener('click', _openTasksForEmailTags);
  const autoReplyStart = page.querySelector('#email-auto-reply-start');
  const seedAutoReplyStartDate = () => {
    if (autoReplyStart && !autoReplyStart.value) autoReplyStart.value = _todayDateInputValue();
  };
  // Empty native date inputs can open on an arbitrary browser baseline date.
  // Seed only the draft field when the picker is opened, leaving saved settings
  // unchanged until the user explicitly presses Save.
  autoReplyStart?.addEventListener('pointerdown', seedAutoReplyStartDate);
  autoReplyStart?.addEventListener('focus', seedAutoReplyStartDate);
  const enabledToggle = page.querySelector('#email-auto-reply-enabled');
  const syncEnabledState = () => {
    const section = page.querySelector('.email-settings-auto-reply-section');
    const stateLabel = section?.querySelector('.email-settings-enabled-state');
    const enabled = !!enabledToggle?.checked;
    section?.classList.toggle('is-disabled', !enabled);
    section?.classList.toggle('is-enabled', enabled);
    if (stateLabel) stateLabel.textContent = enabled ? 'Enabled' : 'Disabled';
  };
  const saveAutoReplySettings = async ({ quiet = false } = {}) => {
    const saveBtn = page.querySelector('.email-settings-save');
    const status = page.querySelector('.email-settings-status');
    if (saveBtn && !quiet) saveBtn.disabled = true;
    _setEmailSaveIcon(saveBtn, false);
    if (status) {
      status.classList.remove('is-success', 'is-error');
      status.textContent = 'Saving…';
    }
    const payload = {
      email_auto_reply: !!page.querySelector('#email-auto-reply-enabled')?.checked,
      email_auto_reply_start: page.querySelector('#email-auto-reply-start')?.value || '',
      email_auto_reply_end: page.querySelector('#email-auto-reply-end')?.value || '',
      email_auto_reply_subject: page.querySelector('#email-auto-reply-subject')?.value || '',
      email_auto_reply_message: page.querySelector('#email-auto-reply-message')?.value || '',
      email_auto_reply_cooldown: page.querySelector('#email-auto-reply-cooldown')?.value || 'period',
      email_auto_reply_scope: 'account',
      email_auto_reply_account_id: state._libAccountId || '',
      email_auto_reply_pause_notifications: !!page.querySelector('#email-auto-reply-pause')?.checked,
    };
    try {
      const res = await fetch(emailApiUrl('/api/email/config', {
        account_id: state._libAccountId || undefined,
      }), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const savedCfg = {
        enabled: !!payload.email_auto_reply,
        start: payload.email_auto_reply_start,
        end: payload.email_auto_reply_end,
      };
      let calendarSynced = true;
      try {
        await _syncAutoReplyCalendarEvent(savedCfg);
      } catch (calendarErr) {
        calendarSynced = false;
        console.warn('Failed to sync auto-reply calendar event:', calendarErr);
      }
      if (status) {
        status.classList.add('is-success');
        status.textContent = 'Saved';
      }
      _setEmailSaveIcon(saveBtn, true);
      if (!quiet) showToast?.('Email settings saved');
      if (!calendarSynced) showToast?.('Saved, but the calendar event could not be updated');
      state._libAutoReplyActive = _isAutoReplyActiveForCurrentAccount(savedCfg);
      state._libAutoReplyDraftActive = null;
      _syncEmailAutoReplyTitle(state._libAutoReplyActive);
      _renderAccountsStrip();
      _refreshUnreadBadge({ preserveAutoReplyTitle: true }).catch(() => {});
      setTimeout(() => { _setEmailSaveIcon(saveBtn, false); if (status) status.textContent = ''; }, quiet ? 900 : 1400);
      return true;
    } catch (err) {
      if (status) {
        status.classList.add('is-error');
        status.textContent = 'Save failed';
      }
      showToast?.('Failed to save email settings');
      return false;
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  };
  enabledToggle?.addEventListener('change', () => {
    syncEnabledState();
    // Invalidate any config read that started before this toggle. Its stale
    // result must not overwrite the title we are about to render.
    state._autoReplyRefreshSeq += 1;
    const active = _isAutoReplyActiveForCurrentAccount({
      email_auto_reply: !!enabledToggle.checked,
      email_auto_reply_start: autoReplyStart?.value || '',
      email_auto_reply_end: page.querySelector('#email-auto-reply-end')?.value || '',
      email_auto_reply_scope: 'account',
      email_auto_reply_account_id: state._libAccountId || '',
    });
    state._libAutoReplyActive = active;
    state._libAutoReplyDraftActive = active;
    _syncEmailAutoReplyTitle(active);
    _renderAccountsStrip();
    saveAutoReplySettings({ quiet: true });
  });
  syncEnabledState();
  page.querySelector('.email-settings-save')?.addEventListener('click', async () => {
    await saveAutoReplySettings();
  });
  page.querySelector('.email-style-settings-save')?.addEventListener('click', async () => {
    const saveBtn = page.querySelector('.email-style-settings-save');
    const status = page.querySelector('.email-style-settings-status');
    saveBtn.disabled = true;
    _setEmailSaveIcon(saveBtn, false);
    if (status) {
      status.classList.remove('is-success', 'is-error');
      status.textContent = 'Saving…';
    }
    try {
      const res = await fetch(emailApiUrl('/api/email/style', {
        account_id: state._libAccountId || undefined,
      }), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ style: page.querySelector('#email-writing-style-text')?.value || '' }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) {
        status.classList.add('is-success');
        status.textContent = 'Saved';
      }
      _setEmailSaveIcon(saveBtn, true);
      showToast?.('Email writing style saved');
      setTimeout(() => { _setEmailSaveIcon(saveBtn, false); if (status) status.textContent = ''; }, 1400);
    } catch (err) {
      if (status) {
        status.classList.add('is-error');
        status.textContent = 'Save failed';
      }
      showToast?.('Failed to save email writing style');
    } finally {
      saveBtn.disabled = false;
    }
  });
  page.querySelector('.email-style-settings-extract')?.addEventListener('click', async () => {
    const extractBtn = page.querySelector('.email-style-settings-extract');
    const saveBtn = page.querySelector('.email-style-settings-save');
    const status = page.querySelector('.email-style-settings-status');
    const styleEl = page.querySelector('#email-writing-style-text');
    extractBtn.disabled = true;
    if (saveBtn) saveBtn.disabled = true;
    let wp = null;
    if (status) {
      status.innerHTML = '';
      try {
        wp = spinnerModule.createWhirlpool(14);
        wp.element.style.cssText = 'display:inline-block;vertical-align:-3px;margin-right:6px;';
        status.appendChild(wp.element);
        status.appendChild(document.createTextNode('Analyzing sent emails…'));
      } catch (_) {
        status.textContent = 'Analyzing sent emails…';
      }
    }
    try {
      const res = await fetch(emailApiUrl('/api/email/extract-style', {
        account_id: state._libAccountId || undefined,
      }), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ sample_count: 15 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success || !data.style) throw new Error(data.error || `HTTP ${res.status}`);
      if (styleEl) styleEl.value = data.style;
      if (status) status.textContent = 'Extracted';
      showToast?.('Email writing style extracted');
      setTimeout(() => { if (status) status.textContent = ''; }, 1800);
    } catch (err) {
      if (status) status.textContent = err?.message || 'Extract failed';
      showToast?.('Failed to extract email writing style');
    } finally {
      if (wp && wp.destroy) { try { wp.destroy(); } catch (_) {} }
      extractBtn.disabled = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  });
}

export function _hideEmailSettingsPage() {
  const modal = document.getElementById('email-lib-modal');
  const page = document.getElementById('email-lib-settings-page');
  const btn = document.getElementById('email-lib-settings-btn');
  const headerBackBtn = document.getElementById('email-settings-header-back');
  modal?.classList.remove('email-settings-mode');
  if (page) page.hidden = true;
  if (headerBackBtn) headerBackBtn.style.display = 'none';
  btn?.classList.remove('active');
  btn?.setAttribute('aria-expanded', 'false');
}
