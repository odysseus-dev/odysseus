const DRAFT_PREFIX = 'odysseus.chatDraft.';
const DRAFT_INDEX_KEY = 'odysseus.chatDraftIndex';
const MAX_DRAFTS = 50;

export const NEW_DRAFT_ID = '__new__';

function _draftKey(id) {
  return id ? DRAFT_PREFIX + String(id) : '';
}

function _readDraftIndex() {
  try {
    const raw = localStorage.getItem(DRAFT_INDEX_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const index = {};
    Object.entries(parsed).forEach(([id, ts]) => {
      if (!id) return;
      const n = Number(ts);
      if (Number.isFinite(n)) index[id] = n;
    });
    return index;
  } catch (_) {
    return {};
  }
}

function _writeDraftIndex(index) {
  try {
    if (index && Object.keys(index).length) localStorage.setItem(DRAFT_INDEX_KEY, JSON.stringify(index));
    else localStorage.removeItem(DRAFT_INDEX_KEY);
  } catch (_) {}
}

function _pruneComposerDrafts(index) {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(DRAFT_PREFIX)) continue;
      const id = key.slice(DRAFT_PREFIX.length);
      if (id && index[id] == null) index[id] = 0;
    }
  } catch (_) {}
  const entries = Object.entries(index)
    .filter(([id, ts]) => id && Number.isFinite(Number(ts)))
    .sort((a, b) => Number(a[1]) - Number(b[1]));
  while (entries.length > MAX_DRAFTS) {
    const [id] = entries.shift();
    try { localStorage.removeItem(_draftKey(id)); } catch (_) {}
    delete index[id];
  }
  return index;
}

function _rememberDraftId(id) {
  if (!id) return;
  const index = _readDraftIndex();
  index[String(id)] = Date.now();
  _writeDraftIndex(_pruneComposerDrafts(index));
}

function _forgetDraftId(id) {
  if (!id) return;
  const index = _readDraftIndex();
  if (index[id] != null) {
    delete index[id];
    _writeDraftIndex(index);
  }
}

export function hasComposerDraft(id) {
  try {
    return (localStorage.getItem(_draftKey(id)) || '').length > 0;
  } catch (_) {
    return false;
  }
}

export function readComposerDraft(id) {
  try { return localStorage.getItem(_draftKey(id)) || ''; } catch (_) { return ''; }
}

export function writeComposerDraft(id, text) {
  const key = _draftKey(id);
  if (!key) return;
  try {
    if (text) {
      localStorage.setItem(key, text);
      _rememberDraftId(id);
    } else {
      localStorage.removeItem(key);
      _forgetDraftId(id);
    }
  } catch (_) {}
}

export function clearComposerDraft(id) {
  const key = _draftKey(id);
  if (!key) return;
  try { localStorage.removeItem(key); } catch (_) {}
  _forgetDraftId(id);
}

export function createComposerDraftController({
  getCurrentSessionId,
  hasPendingChat,
  isWelcomeActive,
  getInput,
  resizeInput,
} = {}) {
  let draftTimer = null;
  let suppressDraftWrite = false;

  function _isNewChatDraftContext() {
    const sid = getCurrentSessionId && getCurrentSessionId();
    if (sid) return false;
    return !!((hasPendingChat && hasPendingChat()) || (isWelcomeActive && isWelcomeActive()));
  }

  function currentDraftId(sessionId = null) {
    if (sessionId) return String(sessionId);
    const sid = getCurrentSessionId && getCurrentSessionId();
    if (sid) return String(sid);
    return _isNewChatDraftContext() ? NEW_DRAFT_ID : '';
  }

  function _queueSave() {
    if (suppressDraftWrite) return;
    const id = currentDraftId();
    if (!id) return;
    const input = getInput && getInput();
    if (!input) return;
    const text = input.value || '';
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      draftTimer = null;
      writeComposerDraft(id, text);
    }, 200);
  }

  function bind() {
    const input = getInput && getInput();
    if (!input || input._draftPersistBound) return;
    input._draftPersistBound = true;
    input.addEventListener('input', _queueSave);
    setTimeout(() => restore(), 0);
  }

  function restore(sessionId = null) {
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    let id = currentDraftId(sessionId);
    if (!id && !sessionId && hasComposerDraft(NEW_DRAFT_ID)) {
      id = NEW_DRAFT_ID;
    }
    if (!id) return;
    const input = getInput && getInput();
    if (!input) return;
    const draft = readComposerDraft(id);
    suppressDraftWrite = true;
    try {
      input.value = draft;
      input.style.height = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    } finally {
      suppressDraftWrite = false;
    }
    if (resizeInput) resizeInput(input);
  }

  function clear(sessionId = null) {
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    const id = currentDraftId(sessionId);
    if (!id) return;
    clearComposerDraft(id);
    if (!sessionId || String(sessionId) === currentDraftId()) {
      const input = getInput && getInput();
      if (input) {
        suppressDraftWrite = true;
        try {
          input.value = '';
          input.style.height = '';
          input.dispatchEvent(new Event('input', { bubbles: true }));
        } finally {
          suppressDraftWrite = false;
        }
        if (resizeInput) resizeInput(input);
      }
    }
  }

  function suspend(fn) {
    suppressDraftWrite = true;
    try { return fn && fn(); }
    finally { suppressDraftWrite = false; }
  }

  return { bind, currentDraftId, restore, clear, suspend };
}

export default createComposerDraftController;
