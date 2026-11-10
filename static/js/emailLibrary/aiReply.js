// static/js/emailLibrary/aiReply.js
//
// The AI-reply entry points and the reader submenus that sit next to them:
// reply-mode choice, the per-message context note (drafted into localStorage so
// a half-typed instruction survives a reload), the translate submenu, and the
// remind-me submenu that writes a todo note.
//
// The context-draft key is versioned (`…:v2:`) and scoped by account, folder
// and uid, so a draft cannot leak from one message to another.

import spinnerModule from '../spinner.js';
import { topPortalZ } from '../toolWindowZOrder.js';
import { showToast } from '../ui.js?v=20260916largetoolscroll1';
import { state } from './state.js';
import { _esc, _extractName } from './utils.js';
import { _snapEmailModalToLeftSidebar, _translateEmail } from './index.js';

const API_BASE = window.location.origin;

export function _aiReplyIcon(data) {
  return '<svg class="email-ai-reply-icon" width="14" height="14" viewBox="0 0 24 24" fill="var(--accent-primary, var(--red))" aria-hidden="true"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>';
}

function _summaryIcon(data) {
  const fill = data?.cached_summary ? 'var(--accent-primary, var(--red))' : 'currentColor';
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="${fill}"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>`;
}

async function _emailTranslateLanguage() {
  try {
    const res = await fetch(`${API_BASE}/api/email/config`);
    const cfg = await res.json();
    return cfg?.email_translate_language || 'English';
  } catch (_) {
    return 'English';
  }
}

async function _runAiReplyFromButton(btn, em, data, mode, noteHint = '') {
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
    if (state._onEmailClick) {
      return await state._onEmailClick({ email: em, emailData: data, mode, noteHint });
    }
    return false;
  } finally {
    try { wp && wp.stop(); } catch (_) {}
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

const _AI_REPLY_CONTEXT_DRAFT_PREFIX = 'odysseus:email-ai-reply-context:v2:';
let _aiReplyChoiceOutsideClose = null;

function _aiReplyContextDraftKey(em, data) {
  const accountId = data?.account_id || em?.account_id || state._libAccountId || '';
  const folder = data?.folder || em?.folder || state._libFolder || 'INBOX';
  const uid = data?.uid || em?.uid || data?.message_id || em?.message_id || '';
  if (!uid) return '';
  return _AI_REPLY_CONTEXT_DRAFT_PREFIX
    + [accountId, folder, uid].map(value => encodeURIComponent(String(value || ''))).join(':');
}

function _loadAiReplyContextDraft(key) {
  if (!key) return '';
  try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
}

function _saveAiReplyContextDraft(key, value) {
  if (!key) return;
  try {
    const text = String(value || '');
    if (text.trim()) localStorage.setItem(key, text);
    else localStorage.removeItem(key);
  } catch (_) {}
}

function _clearAiReplyContextDraft(key) {
  if (!key) return;
  try { localStorage.removeItem(key); } catch (_) {}
}

function _closeAiReplyChoice() {
  if (_aiReplyChoiceOutsideClose) {
    document.removeEventListener('click', _aiReplyChoiceOutsideClose, true);
    _aiReplyChoiceOutsideClose = null;
  }
  document.querySelectorAll('.email-ai-reply-choice').forEach(el => {
    const input = el.querySelector('[data-note-input]');
    _saveAiReplyContextDraft(el.dataset.contextDraftKey || '', input?.value || '');
    el.remove();
  });
}

function _showAiReplyChoice(btn, em, data, positionAnchor = btn) {
  _closeAiReplyChoice();
  const rect = positionAnchor.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'email-ai-reply-choice';
  /* Clamp width to viewport minus 16px margin so the menu never spills off
     the right edge on narrow mobile screens. */
  const menuMaxW = Math.min(220, window.innerWidth - 16);
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuMaxW - 8));
  /* Vertical placement: prefer below the button, but flip above if
     there's not enough room (e.g. button near bottom of viewport).
     Estimated menu height is ~150px (textarea + buttons + padding). */
  const estHeight = 150;
  const spaceBelow = window.innerHeight - rect.bottom - 8;
  const spaceAbove = rect.top - 8;
  let top;
  if (spaceBelow >= estHeight || spaceBelow >= spaceAbove) {
    top = Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - estHeight - 8));
  } else {
    top = Math.max(8, rect.top - estHeight - 6);
  }
  menu.style.cssText = [
    'position:fixed',
    `left:${left}px`,
    `top:${top}px`,
    `max-width:${menuMaxW}px`,
    `max-height:${window.innerHeight - 16}px`,
    'overflow:auto',
    'box-sizing:border-box',
    `z-index:${topPortalZ()}`,
    'display:flex',
    'gap:6px',
    'padding:6px',
    'background:var(--bg,#111)',
    'border:1px solid var(--border,#333)',
    'border-radius:7px',
    'box-shadow:0 8px 24px rgba(0,0,0,.28)',
  ].join(';');
  menu.innerHTML = `
    <div class="email-ai-reply-row" style="display:flex;flex-direction:column;gap:6px;min-width:180px;">
      <textarea data-note-input rows="2" placeholder="Context (optional)" style="width:100%;box-sizing:border-box;resize:vertical;min-height:42px;font-family:inherit;font-size:11px;padding:5px 6px;border-radius:5px;border:1px solid var(--border,#333);background:var(--bg-elev,#1a1a1a);color:var(--fg);"></textarea>
      <div style="display:flex;align-items:center;gap:4px;">
        <button class="memory-toolbar-btn" data-mode="ai-reply-fast" title="Draft reply" style="display:inline-flex;align-items:center;justify-content:center;gap:5px;flex:1;">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="color:var(--accent, var(--red));"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
          Submit
        </button>
      </div>
    </div>
  `;
  const noteInput = menu.querySelector('[data-note-input]');
  const contextDraftKey = _aiReplyContextDraftKey(em, data);
  menu.dataset.contextDraftKey = contextDraftKey;
  noteInput.value = _loadAiReplyContextDraft(contextDraftKey);
  noteInput.addEventListener('input', () => {
    _saveAiReplyContextDraft(contextDraftKey, noteInput.value || '');
  });
  setTimeout(() => noteInput.focus(), 0);
  menu.addEventListener('click', async (ev) => {
    const choice = ev.target.closest('[data-mode]');
    if (!choice) return;
    ev.preventDefault();
    ev.stopPropagation();
    const mode = choice.getAttribute('data-mode') || 'ai-reply-fast';
    const noteHint = (noteInput.value || '').trim();
    _saveAiReplyContextDraft(contextDraftKey, noteInput.value || '');
    _closeAiReplyChoice();
    const draftOpened = await _runAiReplyFromButton(btn, em, data, mode, noteHint);
    if (draftOpened === true) _clearAiReplyContextDraft(contextDraftKey);
  });
  // Esc closes the popover; ignore plain clicks inside the menu so the
  // textarea stays focused.
  menu.addEventListener('mousedown', (ev) => ev.stopPropagation());
  document.body.appendChild(menu);
  // Outside-click closer: only fires when the click target is OUTSIDE
  // the menu. The original handler closed on any click which made
  // focusing the textarea immediately dismiss the popover.
  const outsideClose = (ev) => {
    if (menu.contains(ev.target)) return;
    document.removeEventListener('click', outsideClose, true);
    if (_aiReplyChoiceOutsideClose === outsideClose) _aiReplyChoiceOutsideClose = null;
    _closeAiReplyChoice();
  };
  _aiReplyChoiceOutsideClose = outsideClose;
  setTimeout(() => {
    if (_aiReplyChoiceOutsideClose === outsideClose) {
      document.addEventListener('click', outsideClose, true);
    }
  }, 0);
}

export function _handleAiReplyButton(ev, em, data, positionAnchor = ev.currentTarget) {
  ev.stopPropagation();
  const btn = ev.currentTarget;
  // First click on a cached email surfaces the cached draft. Second
  // click clears the cache and opens the Fast/Full + context menu so
  // the user can ask for a fresh draft (with new steering).
  if (data?.cached_ai_reply && !btn.dataset.shownOnce) {
    btn.dataset.shownOnce = '1';
    _runAiReplyFromButton(btn, em, data, 'ai-reply');
    return;
  }
  if (data?.cached_ai_reply) {
    data.cached_ai_reply = null;
    btn.dataset.shownOnce = '';
  }
  _showAiReplyChoice(btn, em, data, positionAnchor);
}

export function _hasMultipleRecipients(data) {
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

export function _showEmailTranslateSubmenu(reader, parentDropdown) {
  parentDropdown.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'dropdown-item-compact';
  header.style.cssText = 'opacity:0.5;font-size:10px;pointer-events:none;text-transform:uppercase;letter-spacing:0.5px;padding-top:6px;';
  header.innerHTML = '<span>Translate to</span>';
  parentDropdown.appendChild(header);

  const customRow = document.createElement('div');
  customRow.className = 'dropdown-item-compact email-translate-custom-row';
  customRow.style.cssText = 'display:flex;gap:5px;align-items:center;padding:5px 7px;cursor:default;';
  customRow.innerHTML = `
    <input type="text" class="email-translate-custom-input" placeholder="Write language..." style="min-width:0;width:136px;height:26px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;font-size:11px;padding:0 7px;">
    <button type="button" class="email-translate-custom-go" style="height:26px;padding:0 8px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb, var(--fg) 5%, transparent);color:inherit;font:inherit;font-size:11px;cursor:pointer;">Go</button>
  `;
  parentDropdown.appendChild(customRow);
  const input = customRow.querySelector('.email-translate-custom-input');
  const go = customRow.querySelector('.email-translate-custom-go');
  const runCustom = async () => {
    const language = (input?.value || '').trim();
    if (!language) {
      input?.focus();
      return;
    }
    parentDropdown.remove();
    await _translateEmail(reader, language);
  };
  customRow.addEventListener('click', e => e.stopPropagation());
  go?.addEventListener('click', async e => {
    e.stopPropagation();
    await runCustom();
  });
  input?.addEventListener('keydown', async e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      await runCustom();
    }
  });
  _emailTranslateLanguage().then(language => {
    if (input && !input.value) input.value = language || 'English';
  }).catch(() => {});

  const languages = ['English', 'Swedish', 'Japanese', 'Spanish', 'French', 'German'];
  for (const language of languages) {
    const item = document.createElement('div');
    item.className = 'dropdown-item-compact';
    item.innerHTML = `<span>${language}</span>`;
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      parentDropdown.remove();
      await _translateEmail(reader, language);
    });
    parentDropdown.appendChild(item);
  }
}

// ---- Reminder submenu (used by both email menus) ----
export function _showLibRemindSubmenu(em, parentDropdown) {
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
  // "Note" — prompts for free-text and saves it as a note without a
  // due_date, so no timer/reminder fires.
  const noteItem = document.createElement('div');
  noteItem.className = 'dropdown-item-compact';
  noteItem.innerHTML = '<span>Note</span>';
  noteItem.addEventListener('click', (e) => {
    e.stopPropagation();
    parentDropdown.remove();
    _promptEmailNote(em);
  });
  parentDropdown.appendChild(noteItem);
}

function _promptEmailNote(em) {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;padding:16px;';
  const card = document.createElement('div');
  card.style.cssText = 'background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;min-width:280px;max-width:min(420px, 92vw);display:flex;flex-direction:column;gap:8px;box-shadow:0 12px 32px rgba(0,0,0,0.4);';
  const subject = em.subject || '(no subject)';
  card.innerHTML = `
    <div style="font-size:11px;opacity:0.6;">Note about ${_esc(subject)}</div>
    <textarea data-note placeholder="Write your note…" rows="4" style="resize:vertical;min-height:80px;font-family:inherit;font-size:12px;padding:7px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-elev,#1a1a1a);color:var(--fg);box-sizing:border-box;width:100%;"></textarea>
    <div class="email-note-actions" style="display:flex;gap:6px;justify-content:flex-end;position:relative;top:4px;">
      <button class="memory-toolbar-btn" data-act="cancel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" style="vertical-align:-1px;margin-right:4px;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Cancel</button>
      <button class="memory-toolbar-btn active" data-act="save"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-1px;margin-right:4px;"><path d="M4 4h16v16H4z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>Save</button>
    </div>
  `;
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  const ta = card.querySelector('[data-note]');
  setTimeout(() => ta.focus(), 0);
  const close = () => overlay.remove();
  overlay.addEventListener('click', (ev) => { if (ev.target === overlay) close(); });
  card.querySelector('[data-act="cancel"]').addEventListener('click', close);
  card.querySelector('[data-act="save"]').addEventListener('click', async () => {
    const text = (ta.value || '').trim();
    if (!text) { ta.focus(); return; }
    close();
    await _createEmailReplyReminder(em, null, text);
  });
  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') close();
    else if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') card.querySelector('[data-act="save"]').click();
  });
}

async function _createEmailReplyReminder(em, dueDate, customText = '') {
  const pad = n => String(n).padStart(2,'0');
  const iso = dueDate
    ? `${dueDate.getFullYear()}-${pad(dueDate.getMonth()+1)}-${pad(dueDate.getDate())}T${pad(dueDate.getHours())}:${pad(dueDate.getMinutes())}`
    : null;
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
  const itemText = customText || `Reply to ${from}: ${subject}`;
  const payload = {
    title: `Reply: ${subject}`,
    note_type: 'todo',
    items: [
      { text: itemText, checked: false },
    ],
    content: `Open email: ${deepLink}`,
    label: 'email reminder',
    source: 'email',
  };
  if (iso) payload.due_date = iso;
  try {
    const res = await fetch(`${API_BASE}/api/notes`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Failed');
    const { showToast } = await import('../ui.js?v=20260916largetoolscroll1');
    if (dueDate) {
      const fmt = dueDate.toLocaleString([], { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
      showToast(`Todo reminder set for ${fmt}`);
    } else {
      showToast('Reply note saved');
    }
    if ('Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch {}
    }
  } catch (e) {
    const { showError } = await import('../ui.js?v=20260916largetoolscroll1');
    showError('Failed to create reminder');
  }
}
