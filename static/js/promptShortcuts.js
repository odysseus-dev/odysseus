// static/js/promptShortcuts.js
//
// Per-user, click-to-send prompt shortcuts. Renders stacked rows above the
// chat input when a chat session is empty; configured via a sidebar tool
// window. Persisted server-side as `chat_shortcut_prompts` via
// /api/prefs (see routes/prefs_routes.py).

import * as Modals from './modalManager.js';
import dragSortModule from './dragSort.js';

const PREFS_KEY = 'chat_shortcut_prompts';
const MAX_PROMPTS = 20;
const MAX_VISIBLE = 4;
const MAX_TITLE_LEN = 50;
const MAX_TEXT_LEN = 2000;
const MIN_TEXT_LEN = 1;
const MODAL_ID = 'prompt-shortcuts-modal';

function _deriveTitle(text) {
  // Used to back-fill a title for legacy prompts saved before the title field
  // existed. Take the first non-empty line, trim to MAX_TITLE_LEN.
  const firstLine = String(text || '').split(/\r?\n/).find(l => l.trim()) || '';
  return firstLine.trim().slice(0, MAX_TITLE_LEN);
}

let _list = [];
let _loaded = false;
let _editingId = null;     // id of the row currently in edit mode (null = none)
let _pendingNew = false;   // true while the unsaved "add new" row is open
let _chatBarObserver = null;
let _persistInFlight = null;

function _newId() {
  return 'p_' + Math.random().toString(36).slice(2, 10);
}

function _escape(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── Persistence ─────────────────────────────────────────────────────

async function _loadFromServer() {
  if (_loaded) return _list;
  try {
    const res = await fetch('/api/prefs/' + PREFS_KEY, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      const val = data && data.value;
      if (Array.isArray(val)) {
        _list = val
          .filter(it => it && typeof it.text === 'string' && it.text.trim())
          .map(it => {
            const text = it.text.slice(0, MAX_TEXT_LEN);
            const rawTitle = typeof it.title === 'string' ? it.title.trim() : '';
            return {
              id: typeof it.id === 'string' && it.id ? it.id : _newId(),
              title: (rawTitle || _deriveTitle(text)).slice(0, MAX_TITLE_LEN),
              text,
            };
          })
          .slice(0, MAX_PROMPTS);
      }
    }
  } catch (_) {
    // Network failure or no auth — fall through with whatever we have.
  }
  _loaded = true;
  return _list;
}

async function _persist() {
  // Serialize concurrent PUTs — late writes win, but earlier ones still flush
  // first so the server never sees them out of order.
  const payload = _list.map(p => ({ id: p.id, title: p.title, text: p.text }));
  const send = async () => {
    try {
      await fetch('/api/prefs/' + PREFS_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ value: payload }),
      });
    } catch (_) { /* swallow — UI already updated optimistically */ }
  };
  const prev = _persistInFlight || Promise.resolve();
  _persistInFlight = prev.then(send, send);
  return _persistInFlight;
}

// ── Chat-input bar rendering ────────────────────────────────────────

function _chatBarEl() {
  return document.getElementById('chat-shortcuts-bar');
}

function _renderChatBar() {
  const bar = _chatBarEl();
  if (!bar) return;
  // Clear and re-render the visible top-N. CSS hides the container when the
  // chat is non-empty (#chat-container.welcome-active gate) and when the
  // appearance toggle is off (applyUIVis inline display:none).
  bar.replaceChildren();
  const visible = _list.slice(0, MAX_VISIBLE);
  if (visible.length === 0) return;
  visible.forEach(prompt => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'chat-shortcut-row';
    row.dataset.promptId = prompt.id;
    row.title = prompt.text;
    row.textContent = prompt.title || _deriveTitle(prompt.text);
    row.addEventListener('click', () => _sendPrompt(prompt));
    bar.appendChild(row);
  });
}

async function _sendPrompt(prompt) {
  const messageInput = document.getElementById('message');
  const chatForm = document.getElementById('chat-form');
  if (!messageInput || !chatForm) return;
  messageInput.value = prompt.text;
  messageInput.dispatchEvent(new Event('input', { bubbles: true }));
  chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

// ── Tool window: modal scaffold ─────────────────────────────────────

function _buildModal() {
  if (document.getElementById(MODAL_ID)) return;
  const modal = document.createElement('div');
  modal.className = 'modal hidden';
  modal.id = MODAL_ID;
  modal.innerHTML = `
    <div class="modal-content prompt-shortcuts-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="2" y="3" width="16" height="5" rx="1.5"/><rect x="4" y="10" width="16" height="5" rx="1.5"/><rect x="6" y="17" width="16" height="4" rx="1.5"/></svg>
          Prompt Shortcuts
        </h4>
        <button class="modal-close" id="prompt-shortcuts-close" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body prompt-shortcuts-body">
        <p class="prompt-shortcuts-desc">Each shortcut has a title (shown on the button) and a prompt (what gets sent). The first ${MAX_VISIBLE} appear in a 2&times;2 grid above the chat input when a chat is empty &mdash; toggle the row in <em>Settings &rarr; Appearance &rarr; Chat Area</em>.</p>
        <div class="prompt-shortcuts-list" id="prompt-shortcuts-list"></div>
        <div class="prompt-shortcuts-footer">
          <button type="button" class="prompt-shortcuts-add-btn" id="prompt-shortcuts-add-btn">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add prompt
          </button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector('#prompt-shortcuts-close').addEventListener('click', closePromptShortcuts);
  modal.querySelector('#prompt-shortcuts-add-btn').addEventListener('click', _startAddNew);
  // Click outside the modal content closes the window — mirrors the gallery /
  // compare modal behavior.
  modal.addEventListener('click', e => {
    if (e.target === modal) closePromptShortcuts();
  });

  // Escape closes (only when no editor is open to avoid stealing Esc from
  // the textarea's cancel handler).
  modal._psKeydownHandler = e => {
    if (e.key === 'Escape' && _editingId === null && !_pendingNew) {
      closePromptShortcuts();
    }
  };
  document.addEventListener('keydown', modal._psKeydownHandler);

  Modals.register(MODAL_ID, {
    sidebarBtnId: 'tool-prompt-shortcuts-btn',
    restoreFn: () => openPromptShortcuts(),
    closeFn: () => _forceClose(),
  });
}

function _forceClose() {
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  modal.classList.add('hidden');
  const btn = document.getElementById('tool-prompt-shortcuts-btn');
  if (btn) btn.classList.remove('active');
}

// ── Tool window: list rendering ─────────────────────────────────────

function _renderToolList() {
  const listEl = document.getElementById('prompt-shortcuts-list');
  if (!listEl) return;

  listEl.replaceChildren();

  if (_list.length === 0 && !_pendingNew) {
    const empty = document.createElement('div');
    empty.className = 'prompt-shortcuts-empty';
    empty.innerHTML = `
      <div class="prompt-shortcuts-empty-text">No shortcut prompts yet.</div>
      <button type="button" class="prompt-shortcuts-add-btn prompt-shortcuts-add-first-btn" id="prompt-shortcuts-add-first-btn">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Add your first prompt
      </button>
    `;
    listEl.appendChild(empty);
    empty.querySelector('#prompt-shortcuts-add-first-btn').addEventListener('click', _startAddNew);
    _syncAddBtn();
    return;
  }

  _list.forEach((prompt, idx) => {
    if (idx === MAX_VISIBLE) {
      const sep = document.createElement('div');
      sep.className = 'prompt-shortcuts-separator';
      sep.innerHTML = `<span>Only the prompts above are shown above the chat input</span>`;
      listEl.appendChild(sep);
    }
    listEl.appendChild(_renderRow(prompt));
  });

  if (_pendingNew) {
    // Append unsaved row at the end (uses transient id so save/cancel can
    // find it without colliding with the persisted list).
    listEl.appendChild(_renderEditor({ id: '__new__', title: '', text: '' }, { isNew: true }));
  }

  _syncAddBtn();
  _enableDragSort();
}

function _renderRow(prompt) {
  const row = document.createElement('div');
  row.className = 'prompt-shortcuts-row';
  row.dataset.promptId = prompt.id;

  if (_editingId === prompt.id) {
    return _renderEditor(prompt, { isNew: false });
  }

  const handle = document.createElement('span');
  handle.className = 'prompt-shortcuts-drag-handle';
  handle.title = 'Drag to reorder';
  handle.setAttribute('aria-hidden', 'true');
  handle.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg>`;

  const body = document.createElement('div');
  body.className = 'prompt-shortcuts-row-body';
  body.addEventListener('click', () => _startEdit(prompt.id));

  const titleEl = document.createElement('div');
  titleEl.className = 'prompt-shortcuts-row-title';
  titleEl.textContent = prompt.title || _deriveTitle(prompt.text);

  const textEl = document.createElement('div');
  textEl.className = 'prompt-shortcuts-row-text';
  textEl.textContent = prompt.text;

  body.append(titleEl, textEl);

  const actions = document.createElement('div');
  actions.className = 'prompt-shortcuts-row-actions';

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'prompt-shortcuts-delete-btn';
  delBtn.title = 'Delete prompt';
  delBtn.setAttribute('aria-label', 'Delete');
  delBtn.innerHTML = '&times;';
  delBtn.addEventListener('click', e => { e.stopPropagation(); _confirmDelete(prompt); });
  actions.appendChild(delBtn);

  row.append(handle, body, actions);
  return row;
}

function _renderEditor(prompt, { isNew }) {
  const wrap = document.createElement('div');
  wrap.className = 'prompt-shortcuts-row prompt-shortcuts-row-editing';
  wrap.dataset.promptId = prompt.id;

  const titleRow = document.createElement('div');
  titleRow.className = 'prompt-shortcuts-editor-title-row';

  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.className = 'prompt-shortcuts-editor-title';
  titleInput.maxLength = MAX_TITLE_LEN;
  titleInput.placeholder = 'Title (shown on the button)';
  titleInput.value = prompt.title || '';

  const titleCounter = document.createElement('span');
  titleCounter.className = 'prompt-shortcuts-editor-counter prompt-shortcuts-editor-title-counter';

  titleRow.append(titleInput, titleCounter);

  const textarea = document.createElement('textarea');
  textarea.className = 'prompt-shortcuts-editor-textarea';
  textarea.maxLength = MAX_TEXT_LEN;
  textarea.placeholder = 'The prompt sent when this button is clicked...';
  textarea.value = prompt.text || '';
  textarea.rows = 3;

  const footer = document.createElement('div');
  footer.className = 'prompt-shortcuts-editor-footer';

  const counter = document.createElement('span');
  counter.className = 'prompt-shortcuts-editor-counter';

  const spacer = document.createElement('span');
  spacer.style.flex = '1';

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'prompt-shortcuts-editor-cancel';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.addEventListener('click', () => _cancelEdit({ isNew, id: prompt.id }));

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'prompt-shortcuts-editor-save';
  saveBtn.textContent = 'Save';
  saveBtn.addEventListener('click', () =>
    _commitEdit({ isNew, id: prompt.id, title: titleInput.value, text: textarea.value }),
  );

  footer.append(counter, spacer, cancelBtn, saveBtn);

  const updateCounters = () => {
    const titleLen = titleInput.value.length;
    const textLen = textarea.value.length;
    titleCounter.textContent = `${titleLen}/${MAX_TITLE_LEN}`;
    counter.textContent = `${textLen}/${MAX_TEXT_LEN}`;
    const titleOk = titleInput.value.trim().length >= MIN_TEXT_LEN;
    const textOk = textarea.value.trim().length >= MIN_TEXT_LEN;
    saveBtn.disabled = !(titleOk && textOk);
  };

  titleInput.addEventListener('input', updateCounters);
  textarea.addEventListener('input', updateCounters);

  const onKeydown = e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!saveBtn.disabled) saveBtn.click();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelBtn.click();
    }
  };
  titleInput.addEventListener('keydown', e => {
    // Plain Enter in the title hops to the body textarea instead of submitting
    // a containing form.
    if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      textarea.focus();
      return;
    }
    onKeydown(e);
  });
  textarea.addEventListener('keydown', onKeydown);

  wrap.append(titleRow, textarea, footer);
  // Focus the first empty field on the next frame so the row is in the DOM.
  requestAnimationFrame(() => {
    const focusTarget = titleInput.value ? textarea : titleInput;
    focusTarget.focus();
    if (focusTarget === textarea) {
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
    updateCounters();
  });
  return wrap;
}

// ── Add / edit / delete handlers ────────────────────────────────────

function _startAddNew() {
  if (_list.length >= MAX_PROMPTS) return;
  // If another editor is open, close it first (Cancel semantics).
  if (_editingId !== null) {
    _editingId = null;
  }
  if (_pendingNew) {
    // Already adding — just refocus.
    const ta = document.querySelector('#prompt-shortcuts-list .prompt-shortcuts-row[data-prompt-id="__new__"] textarea');
    ta?.focus();
    return;
  }
  _pendingNew = true;
  _renderToolList();
}

function _startEdit(id) {
  if (_pendingNew) {
    // Cancel the pending new row first so two editors aren't open.
    _pendingNew = false;
  }
  _editingId = id;
  _renderToolList();
}

async function _commitEdit({ isNew, id, title, text }) {
  const cleanedTitle = (title || '').trim().slice(0, MAX_TITLE_LEN);
  const cleanedText = (text || '').trim().slice(0, MAX_TEXT_LEN);
  if (cleanedTitle.length < MIN_TEXT_LEN || cleanedText.length < MIN_TEXT_LEN) return;
  if (isNew) {
    _list.push({ id: _newId(), title: cleanedTitle, text: cleanedText });
    _pendingNew = false;
  } else {
    const target = _list.find(p => p.id === id);
    if (target) {
      target.title = cleanedTitle;
      target.text = cleanedText;
    }
    _editingId = null;
  }
  _renderToolList();
  _renderChatBar();
  await _persist();
}

function _cancelEdit({ isNew }) {
  if (isNew) {
    _pendingNew = false;
  } else {
    _editingId = null;
  }
  _renderToolList();
}

async function _confirmDelete(prompt) {
  const styledConfirm = window.styledConfirm;
  const preview = (prompt.text || '').slice(0, 120);
  const ok = styledConfirm
    ? await styledConfirm(`Delete this prompt?\n"${preview}"`, { confirmText: 'Delete', danger: true })
    : window.confirm('Delete this prompt?');
  if (!ok) return;
  _list = _list.filter(p => p.id !== prompt.id);
  if (_editingId === prompt.id) _editingId = null;
  _renderToolList();
  _renderChatBar();
  await _persist();
}

function _syncAddBtn() {
  const btn = document.getElementById('prompt-shortcuts-add-btn');
  if (!btn) return;
  const max = _list.length + (_pendingNew ? 1 : 0) >= MAX_PROMPTS;
  btn.disabled = max;
  btn.title = max ? `Maximum ${MAX_PROMPTS} prompts` : 'Add a new prompt';
}

// ── Drag-to-reorder ─────────────────────────────────────────────────

function _enableDragSort() {
  dragSortModule.enable('prompt-shortcuts-list', '.prompt-shortcuts-row', {
    handleSelector: '.prompt-shortcuts-drag-handle',
    excludeSelector: '.prompt-shortcuts-row-editing',
    onReorder: async (rows) => {
      const ids = rows.map(r => r.dataset.promptId).filter(id => id && id !== '__new__');
      // Reorder _list to match the DOM order.
      const byId = new Map(_list.map(p => [p.id, p]));
      const next = [];
      ids.forEach(id => {
        const p = byId.get(id);
        if (p) { next.push(p); byId.delete(id); }
      });
      // Append any prompts that weren't in the DOM (shouldn't happen, but be safe).
      byId.forEach(p => next.push(p));
      _list = next;
      _renderChatBar();
      // Re-render to refresh the separator position (drag can cross it).
      _renderToolList();
      await _persist();
    },
  });
}

// ── Public API ──────────────────────────────────────────────────────

export async function openPromptShortcuts() {
  _buildModal();
  await _loadFromServer();
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  modal.classList.remove('hidden');
  const btn = document.getElementById('tool-prompt-shortcuts-btn');
  if (btn) btn.classList.add('active');
  _renderToolList();
}

export function closePromptShortcuts() {
  // Reset transient edit state so reopen starts clean.
  _editingId = null;
  _pendingNew = false;
  _forceClose();
}

export function togglePromptShortcuts() {
  const modal = document.getElementById(MODAL_ID);
  if (modal && !modal.classList.contains('hidden')) {
    closePromptShortcuts();
  } else {
    openPromptShortcuts();
  }
}

// Subscribe to #chat-container.welcome-active changes. Re-rendering the bar
// on every flip keeps the icons in sync even if the user adds/removes
// prompts while a chat is mid-stream.
function _watchChatLifecycle() {
  const cc = document.getElementById('chat-container');
  if (!cc || _chatBarObserver) return;
  _chatBarObserver = new MutationObserver(() => {
    if (cc.classList.contains('welcome-active')) {
      _renderChatBar();
    }
  });
  _chatBarObserver.observe(cc, { attributes: true, attributeFilter: ['class'] });
}

export async function initPromptShortcuts() {
  await _loadFromServer();
  _renderChatBar();
  _watchChatLifecycle();
}

const promptShortcutsModule = {
  openPromptShortcuts,
  closePromptShortcuts,
  togglePromptShortcuts,
  initPromptShortcuts,
};
export default promptShortcutsModule;
window.promptShortcutsModule = promptShortcutsModule;
