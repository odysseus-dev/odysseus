// ============================================
// Odysseus UI — Main Application Orchestrator
// ES6 module — entry point, no exports (wires all modules together)
// ============================================
import Storage from './js/storage.js';
import uiModule from './js/ui.js';
import workspaceModule from './js/workspace.js';
import fileHandlerModule from './js/fileHandler.js';
import modelsModule from './js/models.js';
import ragModule from './js/rag.js';
import presetsModule from './js/presets.js';
import searchModule from './js/search.js';
import chatModule from './js/chat.js';
import compareModule from './js/compare/index.js';
import documentModule from './js/document.js';
import searchChatModule from './js/search-chat.js';
import { makeWindowDraggable } from './js/windowDrag.js';
import markdownModule from './js/markdown.js';
import chatRenderer from './js/chatRenderer.js';
import sessionModule from './js/sessions.js';
import memoryModule from './js/memory.js';
import voiceRecorderModule from './js/voiceRecorder.js';
import censorModule from './js/censor.js';
import galleryModule from './js/gallery.js';
import tasksModule from './js/tasks.js';
import calendarModule from './js/calendar.js';
import notesModule from './js/notes.js';
import adminModule from './js/admin.js';
import settingsModule from './js/settings.js';
// Eagerly bind unified minimize/restore behavior across all tool modals.
import './js/modalManager.js';
// Desktop window tiling — drag a modal near an edge/corner to snap.
import './js/tileManager.js';
import themeModule from './js/theme.js';
// IMPORTANT: import cookbook.js with NO ?v= query — the same plain specifier
// every other importer (cookbook-hwfit.js / cookbook-diagnosis.js) uses. A query
// mismatch makes the browser load cookbook.js twice as separate modules (two
// _envState objects), which broke server selection. Keep all cookbook imports
// unversioned so this can't recur.
import cookbookModule from './js/cookbook.js';
import groupModule from './js/group.js';
import * as researchPanelModule from './js/research/panel.js';
import ttsModule from './js/tts-ai.js';
import spinnerModule from './js/spinner.js';
import { initKeyboardShortcuts } from './js/keyboard-shortcuts.js';
import { initSidebarLayout, syncRailSide } from './js/sidebar-layout.js';
import { initSectionCollapse, initSectionDrag } from './js/section-management.js';

const API_BASE = window.location.origin;
window.themeModule = themeModule;
window.sessionModule = sessionModule;
window.uiModule = uiModule;
window.adminModule = adminModule;
window.cookbookModule = cookbookModule;

// Redirect to login on 401 from any fetch
const _origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 401 && !String(args[0]).includes('/api/auth/')) {
    window.location.href = '/login';
  }
  return res;
};

// Search settings


const el = uiModule.el;

// Default chat config — refreshed on every new-chat action so settings
// changes take effect immediately (previously cached once at page load and
// went stale when the user changed their default model).
let _defaultChat = null;
async function _refreshDefaultChat() {
  try {
    const d = await (await fetch('/api/default-chat')).json();
    if (d && d.endpoint_url && d.model) {
      _defaultChat = d;
      try { window.__odysseusDefaultChat = d; } catch (_) {}
      return d;
    }
  } catch (_) {}
  return null;
}
// Prime the cache once at load for initial paint paths that read _defaultChat
// synchronously; later reads should call _refreshDefaultChat() first.
_refreshDefaultChat();

async function _createDirectChatFromPreferredModel() {
  if (!sessionModule) return false;

  const pending = sessionModule.getPendingChat && sessionModule.getPendingChat();
  if (pending && pending.url && pending.modelId) {
    sessionModule.createDirectChat(pending.url, pending.modelId, pending.endpointId);
    return true;
  }

  const sessions = sessionModule.getSessions();
  const currentId = sessionModule.getCurrentSessionId();
  const current = sessions.find(s => s.id === currentId);
  if (current && current.endpoint_url && current.model) {
    sessionModule.createDirectChat(current.endpoint_url, current.model, current.endpoint_id);
    return true;
  }

  const dc = await _refreshDefaultChat();
  if (dc) {
    sessionModule.createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id);
    return true;
  }

  const withModel = sessions.filter(s => s.endpoint_url && s.model);
  if (withModel.length > 0) {
    const last = withModel[0]; // sessions are sorted by recent
    sessionModule.createDirectChat(last.endpoint_url, last.model, last.endpoint_id);
    return true;
  }

  return false;
}

// ============================================
// EVENT LISTENERS INITIALIZATION
// ============================================
function initializeEventListeners() {
  // Chat form submission
//  document.getElementById('chat-form').addEventListener('submit', chatModule.handleChatSubmit);

  // File attachments (inside overflow menu)
  const _overflowAttach = el('overflow-attach-btn');
  if (_overflowAttach) _overflowAttach.addEventListener('click', fileHandlerModule.openPicker);
  el('file-input').addEventListener('change', (e)=>{
    for (const f of e.target.files) fileHandlerModule.addFiles([f]);
    fileHandlerModule.renderAttachStrip();
    // Refocus textarea after file picker closes (mobile keyboard)
    const ta = el('message');
    if (ta) setTimeout(() => ta.focus(), 100);
  });

  // Paste handler
  window.addEventListener('paste', async (e)=>{
    if (!e.clipboardData) return;
    let changed = false;
    for (const item of e.clipboardData.items){
      if (item.kind === 'file'){
        const f = item.getAsFile();
        if (f) {
          fileHandlerModule.addFiles([f]);
          changed = true;
        }
      }
    }
    if (changed) fileHandlerModule.renderAttachStrip();
  });

  // Message count in the header — recount on any DOM change in
  // #chat-history and write "· N msgs" next to the title. Counts top-
  // level .msg elements (one per user/assistant turn); excludes the
  // welcome screen since it isn't inside chat-history.
  const _metaCountEl = el('current-meta-count');
  const _chatHistEl = el('chat-history');
  if (_metaCountEl && _chatHistEl) {
    let _countScheduled = false;
    const _updateMsgCount = () => {
      _countScheduled = false;
      const n = _chatHistEl.querySelectorAll(':scope > .msg').length;
      _metaCountEl.textContent = n ? `· ${n} msg${n === 1 ? '' : 's'}` : '';
    };
    const _scheduleCount = () => {
      if (_countScheduled) return;
      _countScheduled = true;
      requestAnimationFrame(_updateMsgCount);
    };
    new MutationObserver(_scheduleCount).observe(_chatHistEl, { childList: true });
    _updateMsgCount();
  }

  // Scrolling
  el('chat-history').addEventListener('scroll', uiModule.debounce(() => {
    const box = el('chat-history');
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    uiModule.setAutoScroll(atBottom);
  }, 100));
  // Close all footer popups immediately on any scroll
  el('chat-history').addEventListener('scroll', () => {
    document.querySelectorAll('.ctx-popup, .memory-used-detail, .msg-overflow-menu').forEach(p => p.remove());
    document.querySelectorAll('.memory-used-pill').forEach(p => { p._openDetail = null; });
  }, { passive: true });

  el('chat-history').addEventListener('wheel', (e) => {
    // Only disable auto-scroll when user scrolls UP (deltaY < 0)
    if (e.deltaY < 0) uiModule.setAutoScroll(false);
  });
  let _touchThrottled = false;
  el('chat-history').addEventListener('touchmove', () => {
    if (_touchThrottled) return;
    _touchThrottled = true;
    uiModule.setAutoScroll(false);
    requestAnimationFrame(() => { _touchThrottled = false; });
  }, { passive: true });

  // Internal #session-id links from AI search results
  el('chat-history').addEventListener('click', (e) => {
    const link = e.target.closest('a.chat-link');
    if (!link) return;
    const href = link.getAttribute('href');
    if (href && href.startsWith('#') && sessionModule) {
      e.preventDefault();
      sessionModule.selectSession(href.slice(1));
    }
  });

  // Export dropdown button
  const exportDlBtn = el('export-dl-btn');
  // ── Unified popup dismissal ──
  // Lightweight popups (header dropdowns, kebab menus, pickers) should vanish
  // on any "other action" — opening the sidebar, opening a tool window, etc.
  // Each popup used to wire its own outside-click/Escape close but missed
  // non-click actions. closeAllPopups() centralizes it: toggled menus drop
  // their `.open`; ephemeral body-appended menus are removed. Full modals/
  // windows are deliberately NOT touched here — those close via their own
  // controls.
  window.closeAllPopups = function closeAllPopups(except) {
    document.querySelectorAll(
      '.export-dropdown-menu.open, .overflow-menu.open, .model-picker-menu.open, .doc-overflow-menu.open'
    ).forEach(m => { if (m !== except) m.classList.remove('open'); });
    document.querySelectorAll(
      '.skill-kebab-menu, .note-reminder-menu, .task-dropdown, .doclib-card-dropdown, .email-card-dropdown, .msg-overflow-menu'
    ).forEach(m => { if (m !== except) m.remove(); });
  };
  // Window-opening / nav controls (rail buttons, sidebar tool rows + session
  // rows, section headers) count as "other actions" — dismiss popups when one
  // is clicked. Bubble phase so it runs after the control's own handler (the
  // window is already opening; we just clear stray popups). Popup triggers
  // themselves aren't these selectors, so toggles aren't broken.
  document.addEventListener('click', (e) => {
    if (e.target.closest('.icon-rail-btn, #sidebar .list-item, .section-header-flex')) {
      window.closeAllPopups();
    }
  });

  const exportMenu = el('export-dropdown-menu');
  if (exportDlBtn && exportMenu) {
    exportDlBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (exportMenu.classList.contains('open')) {
        exportMenu.classList.remove('open');
      } else {
        // Move menu to body so it's not affected by ancestor transforms
        if (exportMenu.parentElement !== document.body) document.body.appendChild(exportMenu);
        const rect = exportDlBtn.getBoundingClientRect();
        exportMenu.style.top = (rect.bottom + 4) + 'px';
        exportMenu.style.left = 'auto';
        exportMenu.style.right = (window.innerWidth - rect.right) + 'px';
        exportMenu.classList.add('open');
      }
    });
    document.addEventListener('click', () => exportMenu.classList.remove('open'));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && exportMenu.classList.contains('open')) {
        exportMenu.classList.remove('open');
      }
    });
    // Opening the sidebar should dismiss any open popup. Many code paths open
    // the sidebar (toggle button, swipe, keyboard, rail), so watch its class
    // for a hidden→visible transition rather than hooking each one.
    const _sidebarEl = el('sidebar');
    if (_sidebarEl) {
      let _wasHidden = _sidebarEl.classList.contains('hidden');
      new MutationObserver(() => {
        const nowHidden = _sidebarEl.classList.contains('hidden');
        if (_wasHidden && !nowHidden) window.closeAllPopups();
        _wasHidden = nowHidden;
      }).observe(_sidebarEl, { attributes: true, attributeFilter: ['class'] });
    }
    // Clicking session name also opens dropdown
    const currentMeta = el('current-meta');
    if (currentMeta) {
      currentMeta.style.cursor = 'pointer';
      currentMeta.addEventListener('click', (e) => {
        e.stopPropagation();
        exportDlBtn.click();
      });
    }
  }

  // Serialize the current chat history into a plain-text transcript.
  // Includes user messages, assistant rounds, and agent tool calls in DOM order.
  function _serializeChatTranscript() {
    const box = document.getElementById('chat-history');
    if (!box) return '';
    const parts = [];
    for (const child of box.children) {
      if (child.classList?.contains('msg')) {
        const isUser = child.classList.contains('msg-user');
        let label;
        if (isUser) {
          label = 'User';
        } else {
          const roleEl = child.querySelector('.role');
          const ts = roleEl?.querySelector('.role-timestamp');
          let raw = roleEl ? roleEl.textContent : '';
          if (ts) raw = raw.replace(ts.textContent, '');
          label = (raw || '').trim() || 'Assistant';
        }
        const body = child.querySelector('.body');
        // Prefer dataset.raw (original markdown) over innerText (rendered HTML as text)
        // to avoid extra newlines and formatting artifacts.
        const text = body ? (body.dataset.raw || body.innerText || body.textContent || '').trim() : '';
        if (text) parts.push(`${label}: ${text}`);
      } else if (child.classList?.contains('agent-thread')) {
        const lines = ['[Tool calls]'];
        for (const n of child.querySelectorAll('.agent-thread-node')) {
          const tool = n.querySelector('.agent-thread-tool')?.textContent?.trim() || 'tool';
          const cmd = n.querySelector('.agent-thread-cmd')?.textContent?.trim() || '';
          const output = n.querySelector('.agent-tool-output pre')?.textContent?.trim() || '';
          const status = n.classList.contains('error') ? 'failed' : 'done';
          let line = `- ${tool} [${status}]`;
          if (cmd) line += `\n  cmd: ${cmd}`;
          if (output) {
            const truncated = output.length > 2000 ? output.slice(0, 2000) + '…' : output;
            line += `\n  out: ${truncated}`;
          }
          lines.push(line);
        }
        parts.push(lines.join('\n'));
      }
    }
    return parts.join('\n\n');
  }

  // Export: Copy all messages
  const exportCopyBtn = el('export-copy-btn');
  if (exportCopyBtn) {
    exportCopyBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      exportMenu.classList.remove('open');
      const transcript = _serializeChatTranscript();
      // A new/empty chat has nothing to copy — don't write an empty string and
      // falsely report "Copied".
      if (!transcript.trim()) { uiModule.showToast('Nothing to copy yet'); return; }
      await uiModule.copyToClipboard(transcript);
    });
  }

  // Export: PDF
  const exportPdfBtn = el('export-pdf-btn');
  if (exportPdfBtn) {
    exportPdfBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      exportMenu.classList.remove('open');
      const meta = sessionModule.getSessions().find(s => s.id === sessionModule.getCurrentSessionId());
      const sessionName = meta ? meta.name : 'Odysseus Chat';
      const originalTitle = document.title;
      document.title = sessionName;
      const chatHistory = document.getElementById('chat-history');
      if (chatHistory) chatHistory.dataset.printTitle = sessionName;
      document.querySelectorAll('#chat-history details:not([open])').forEach(d => {
        d.setAttribute('open', '');
        d.dataset.printOpened = '1';
      });
      window.print();
      document.title = originalTitle;
      document.querySelectorAll('#chat-history details[data-print-opened]').forEach(d => {
        d.removeAttribute('open');
        d.removeAttribute('data-print-opened');
      });
    });
  }

  // Export: Save to Docs
  const exportDocBtn = el('export-doc-btn');
  if (exportDocBtn) {
    exportDocBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      exportMenu.classList.remove('open');
      try {
        const sessionId = sessionModule.getCurrentSessionId();
        const texts = _serializeChatTranscript();
        const meta = sessionModule.getSessions().find(s => s.id === sessionId);
        const title = meta?.name || 'Untitled';
        const res = await fetch(`${API_BASE}/api/document`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, title, content: texts }),
        });
        if (!res.ok) throw new Error('Failed');
        const doc = await res.json();
        if (documentModule) documentModule.loadDocument(doc.id);
        uiModule.showToast('Saved to documents');
      } catch (err) {
        console.error('Save to docs failed:', err);
        uiModule.showError('Failed to save to documents');
      }
    });
  }

  // Rename session from top bar
  const exportRenameBtn = el('export-rename-btn');
  if (exportRenameBtn) {
    exportRenameBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      exportMenu.classList.remove('open');
      let sid = sessionModule.getCurrentSessionId();
      // A brand-new chat has no session id yet — still allow renaming if there's
      // a pending chat (we materialize it on commit so the name sticks).
      const hasPending = sessionModule.hasPendingChat && sessionModule.hasPendingChat();
      if (!sid && !hasPending) return;
      const meta = sid ? sessionModule.getSessions().find(s => s.id === sid) : null;
      const currentName = meta?.name || '';
      const metaEl = el('current-meta');
      if (!metaEl) return;

      // Replace title with an input
      const input = document.createElement('input');
      input.type = 'text';
      input.value = currentName;
      input.className = 'session-rename-input';
      input.style.cssText = 'font-size:inherit;background:transparent;border:none;border-bottom:1px solid var(--accent, var(--red));color:var(--fg);outline:none;width:100%;padding:0;';
      const origText = metaEl.textContent;
      metaEl.textContent = '';
      metaEl.appendChild(input);
      input.focus();
      input.select();

      const commit = async () => {
        const newName = input.value.trim();
        if (newName && newName !== currentName) {
          // Materialize a pending (new) chat first so it has an id to rename.
          if (!sid && sessionModule.materializePendingSession) {
            try { await sessionModule.materializePendingSession(); sid = sessionModule.getCurrentSessionId(); } catch (_) {}
          }
          if (!sid) { metaEl.textContent = newName; return; }
          const fd = new FormData();
          fd.append('name', newName);
          await fetch(`${API_BASE}/api/session/${sid}`, { method: 'PATCH', body: fd });
          const _m = sessionModule.getSessions().find(s => s.id === sid);
          if (_m) _m.name = newName;
          metaEl.textContent = newName;
          uiModule.showToast('Renamed');
          sessionModule.loadSessions();
        } else {
          metaEl.textContent = origText;
        }
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        if (ev.key === 'Escape') { input.removeEventListener('blur', commit); metaEl.textContent = origText; }
      });
    });
  }

  // Custom preset modal handlers
  const closeCustomPreset = el('close-custom-preset');
  const cancelCustomPreset = el('cancel-custom-preset');
  const saveCustomPreset = el('save-custom-preset');

  if (closeCustomPreset) {
    closeCustomPreset.addEventListener('click', () => {
      el('custom-preset-modal').classList.add('hidden');
    });
  }

  if (cancelCustomPreset) {
    cancelCustomPreset.addEventListener('click', () => {
      el('custom-preset-modal').classList.add('hidden');
    });
  }

  if (saveCustomPreset) {
    saveCustomPreset.addEventListener('click', async () => {
      // Skip character save when Group tab is active — group.js handles it
      const activeTab = document.querySelector('.preset-tab.active');
      if (activeTab && activeTab.dataset.chartab === 'group') return;
      await presetsModule.saveCustomPreset(uiModule.showToast, uiModule.showError);
    });
  }

  // Settings dropdown removed — items are now inline in sidebar section

  


  // Close popups one by one with Escape key (topmost first)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      // If a confirm dialog is open, let it handle the Escape
      const confirmOverlay = document.getElementById('styled-confirm-overlay');
      if (confirmOverlay && !confirmOverlay.classList.contains('hidden')) return;

      // If editing a memory inline, cancel the edit instead of closing the modal
      const editingMemory = document.querySelector('.memory-item-editing');
      if (editingMemory) {
        if (window.memoryModule) window.memoryModule.renderMemoryList();
        return;
      }

      // Priority order: topmost overlay first. Close exactly one per press
      // so a window stacked on another (e.g. scoreboard over compare) only
      // dismisses the top one, not both.

      // Scoreboard sits on top of the compare window — close it first.
      const scoreboardOverlay = document.getElementById('scoreboard-overlay');
      if (scoreboardOverlay) {
        scoreboardOverlay.remove();
        return;
      }

      if (searchChatModule && searchChatModule.isOpen()) {
        searchChatModule.closeSearch();
        return;
      }

      // Compare model selector
      const cmpOverlay = document.getElementById('compare-model-overlay');
      if (cmpOverlay) {
        cmpOverlay.remove();
        return;
      }

      // Theme popup
      const themeModal = document.getElementById('theme-modal');
      if (themeModal && !themeModal.classList.contains('hidden')) {
        themeModule.closePopup();
        return;
      }

      // Calendar owns a few inner Escape layers (settings panel, event form,
      // then the calendar modal itself). Let calendar.js handle those instead
      // of falling through to unrelated page-level fallbacks like document
      // panel minimize.
      const calendarModal = document.getElementById('calendar-modal');
      if (calendarModal && !calendarModal.classList.contains('hidden') && getComputedStyle(calendarModal).display !== 'none') {
        return;
      }

      // Model picker popup — close before opening any modals
      const modelPickerMenu = document.getElementById('model-picker-menu');
      if (modelPickerMenu && modelPickerMenu.classList.contains('open')) {
        modelPickerMenu.classList.remove('open');
        return;
      }

      // Close one modal at a time (last in DOM = topmost)
      // Map modal id → sidebar list-item id to clear active state
      const modalItemMap = {
        'cookbook-modal': null,
        'rename-session-modal': null,
        'rename-ai-modal': null,
        'custom-preset-modal': null,
        'memory-modal': null,
      };

      // Dynamic modals (removed from DOM on close)
      const dynamicModals = ['library-modal', 'archive-modal', 'doclib-modal', 'gallery-modal', 'tasks-modal', 'email-lib-modal'];
      for (const id of dynamicModals) {
        const m = document.getElementById(id);
        if (id === 'gallery-modal') {
          const editor = document.getElementById('gallery-editor-container');
          const editing = !!window.__galleryEditLive || !!(
            editor &&
            getComputedStyle(editor).display !== 'none' &&
            editor.querySelector('.gallery-editor')
          );
          if (editing) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
          }
        }
        if (m) { dismissModal(m); return; }
      }

      for (const modalId of Object.keys(modalItemMap)) {
        const modal = el(modalId);
        if (modal && !modal.classList.contains('hidden')) {
          dismissModal(modal);
          return;
        }
      }

      // No modals/popups open — minimize the document panel if open.
      // Esc should tab the doc down to a dock chip (same as the chevron),
      // NOT fully close it — closePanel('down') registers the chip +
      // Modals.minimize so the doc is preserved and restorable.
      if (documentModule && documentModule.isPanelOpen()) {
        // If there's a text selection in the document editor, let Escape clear that first
        const docTextarea = document.getElementById('doc-editor-textarea');
        if (docTextarea && docTextarea.selectionStart !== docTextarea.selectionEnd) {
          return;
        }
        documentModule.closePanel('down');
        return;
      }
    }
  });

  // ── Shared modal dismiss helper ──
  const _modalSidebarMap = {
    'memory-modal': null,
    'theme-modal': null,
  };
  const _dynamicModalIds = ['library-modal', 'archive-modal', 'doclib-modal', 'gallery-modal', 'tasks-modal'];
  function dismissModal(modal) {
    if (!modal || modal.classList.contains('hidden')) return;
    if (modal.id === 'gallery-modal') {
      const editor = document.getElementById('gallery-editor-container');
      const editing = !!window.__galleryEditLive || !!(
        editor &&
        getComputedStyle(editor).display !== 'none' &&
        editor.querySelector('.gallery-editor')
      );
      if (editing) return;
    }
    const content = modal.querySelector('.modal-content') || modal.querySelector('#theme-popup');
    if (content && !content.classList.contains('modal-closing')) {
      content.classList.remove('sheet-ready');
      content.style.transform = '';
      content.style.transition = '';
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => {
        if (_dynamicModalIds.includes(modal.id)) {
          modal.remove();
        } else {
          modal.classList.add('hidden');
          content.classList.remove('modal-closing');
        }
      }, { once: true });
      // Fallback in case animationend doesn't fire
      setTimeout(() => {
        if (modal.parentElement && !modal.classList.contains('hidden')) {
          if (_dynamicModalIds.includes(modal.id)) modal.remove();
          else { modal.classList.add('hidden'); content.classList.remove('modal-closing'); }
        }
      }, 250);
    } else {
      if (content) content.classList.remove('sheet-ready');
      if (_dynamicModalIds.includes(modal.id)) modal.remove();
      else modal.classList.add('hidden');
    }
  }

  // Click outside modal content → close modal
  document.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal()) return; // suppress synthetic events from touch scrolling
    const modal = e.target.closest('.modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (e.target.closest('.modal-content')) return;
    dismissModal(modal);
  });

  // Mobile bottom-sheet swipe-to-dismiss is handled by ui.js (header-only)

  // ── Helper: start a fresh chat (deselect current, clear history, show welcome) ──
  function _startFreshChat() {
    try {
      const prevId = sessionModule && sessionModule.getCurrentSessionId ? sessionModule.getCurrentSessionId() : null;
      if (chatModule && chatModule.detachCurrentStream) chatModule.detachCurrentStream(prevId);
      else if (chatModule && chatModule.abortCurrentRequest) chatModule.abortCurrentRequest();
    } catch (e) {
      console.warn('fresh chat stream detach failed:', e);
    }
    if (sessionModule) sessionModule.setCurrentSessionId(null);
    const box = el('chat-history');
    if (box) box.innerHTML = '';
    if (chatModule && chatModule.showWelcomeScreen) {
      chatModule.showWelcomeScreen();
    }
    // Close document panel if open
    if (documentModule && documentModule.closePanel) documentModule.closePanel();
    if (researchPanelModule && researchPanelModule.isOpen()) researchPanelModule.closePanel();
    // Reset research overflow dot (but don't touch research state — caller manages that)
    const _overflowRes = el('overflow-research-btn');
    if (_overflowRes) _overflowRes.classList.remove('active');
    if (typeof updatePlusDot === 'function') updatePlusDot();
    // Reset agent mode to Chat
    const modeToggle = el('agent-mode-toggle');
    if (modeToggle && modeToggle.checked) { modeToggle.checked = false; modeToggle.dispatchEvent(new Event('change')); }
    // Clear character/persona
    if (presetsModule && presetsModule.deactivateCharacter) presetsModule.deactivateCharacter();
  }

  /** Sync Research indicator button + overflow + tool sidebar active state. */
  function _syncResearchIndicator(active) {
    const btn = el('research-toggle-btn');
    const overflow = el('overflow-research-btn');
    const toolBtn = el('tool-research-btn');
    const chk = el('research-toggle');
    if (btn) {
      btn.style.display = active ? '' : 'none';
      btn.classList.toggle('active', active);
    }
    // Hide from overflow menu when showing in chatbox (avoid duplicate)
    if (overflow) {
      overflow.classList.toggle('active', active);
      overflow.style.display = active ? 'none' : '';
    }
    if (toolBtn) toolBtn.classList.toggle('active', active);
    if (chk) chk.checked = active;
    // Research disables shell access
    const bashChk = el('bash-toggle');
    const bashBtn = el('bash-toggle-btn');
    if (active) {
      if (bashChk && bashChk.checked) {
        bashChk.checked = false;
        if (bashBtn) bashBtn.classList.remove('active');
        saveToolPref('bash', (loadToggleState().mode || 'chat'), false);
      }
    }
    const s = loadToggleState(); s.research = active; saveToggleState(s);
    updatePlusDot();
    document.dispatchEvent(new CustomEvent('overflow-state-change'));
  }

  /** Sync Group Chat indicator button + overflow. */
  function _syncGroupIndicator(active) {
    const btn = el('group-toggle-btn');
    const overflow = el('overflow-group-btn');
    const chk = el('group-toggle');
    if (btn) {
      btn.style.display = active ? '' : 'none';
      btn.classList.toggle('active', active);
    }
    if (overflow) {
      overflow.classList.toggle('active', active);
      overflow.style.display = active ? 'none' : '';
    }
    if (chk) chk.checked = active;
    // Hide/show model picker
    const _mpw = el('model-picker-wrap');
    if (_mpw) _mpw.style.display = active ? 'none' : '';
    // Mutual exclusion: group disables research + web search
    if (active) {
      _syncResearchIndicator(false);
      const _webChk = el('web-toggle');
      if (_webChk && _webChk.checked) {
        _webChk.checked = false;
        saveToolPref('web', (loadToggleState().mode || 'chat'), false);
      }
    }
    const s = loadToggleState(); s.group = active; saveToggleState(s);
    updatePlusDot();
    document.dispatchEvent(new CustomEvent('overflow-state-change'));

    // Update welcome screen for research mode
    const ws = el('welcome-screen');
    const welcomeName = document.querySelector('.welcome-name');
    const welcomeSub = el('welcome-sub');
    const tipEl = el('welcome-tip');
    const _resIco = '<svg class="welcome-boat" style="position:relative;top:0.5px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
    if (active) {
      if (welcomeName) {
        if (!welcomeName.dataset.researchOrigHtml) welcomeName.dataset.researchOrigHtml = welcomeName.innerHTML;
        welcomeName.innerHTML = _resIco + 'Deep Research';
      }
      if (welcomeSub) {
        if (!welcomeSub.dataset.researchOrigText) welcomeSub.dataset.researchOrigText = welcomeSub.textContent;
        welcomeSub.textContent = 'Deep multi-step research with source gathering and synthesis.';
      }
      if (tipEl) {
        if (!tipEl.dataset.researchOrigTip) tipEl.dataset.researchOrigTip = tipEl.textContent;
        tipEl.textContent = '';
        tipEl.style.display = 'none';
      }
      // Hide Nobody toggle during research mode
      const _incBtn = el('incognito-btn');
      if (_incBtn) { _incBtn.dataset.researchOrigDisplay = _incBtn.style.display; _incBtn.style.display = 'none'; }
      // Close document panel if open
      if (window.documentModule && window.documentModule.isPanelOpen()) {
        window.documentModule.closePanel();
      }
    } else {
      if (welcomeName && welcomeName.dataset.researchOrigHtml) {
        welcomeName.innerHTML = welcomeName.dataset.researchOrigHtml;
        delete welcomeName.dataset.researchOrigHtml;
      }
      if (welcomeSub && welcomeSub.dataset.researchOrigText) {
        welcomeSub.textContent = welcomeSub.dataset.researchOrigText;
        delete welcomeSub.dataset.researchOrigText;
      }
      if (tipEl && tipEl.dataset.researchOrigTip) {
        tipEl.textContent = tipEl.dataset.researchOrigTip;
        tipEl.style.opacity = '';
        tipEl.style.display = '';
        delete tipEl.dataset.researchOrigTip;
      }
      // Restore Nobody toggle
      const _incBtn2 = el('incognito-btn');
      if (_incBtn2 && _incBtn2.dataset.researchOrigDisplay !== undefined) {
        _incBtn2.style.display = _incBtn2.dataset.researchOrigDisplay;
        delete _incBtn2.dataset.researchOrigDisplay;
      }
    }
    if (ws) { ws.style.animation = 'none'; ws.offsetHeight; ws.style.animation = 'welcome-enter 0.3s ease-out both'; }
  }

  // ── Close compare if active (used by all tool/sidebar activations) ──
  // Returns true if compare was active (page will reload), caller should return early
  function _closeCompareIfActive() {
    if (compareModule && compareModule.isActive()) {
      compareModule.deactivate(true);
      return true;
    }
    return false;
  }

  // ── Tools section click handlers ──
  const toolCompareBtn = el('tool-compare-btn');
  if (toolCompareBtn) {
    toolCompareBtn.addEventListener('click', () => {
      if (compareModule) {
        if (compareModule.isActive()) {
          // Already active — toggle off
          compareModule.toggleMode();
          return;
        }
        // Close other exclusive tools before opening compare
        const resChk = el('research-toggle');
        if (resChk && resChk.checked) {
          _syncResearchIndicator(false);
        }
        _startFreshChat();
        compareModule.toggleMode();
      }
    });
  }

  const toolResearchBtn = el('tool-research-btn');
  if (toolResearchBtn) {
    toolResearchBtn.addEventListener('click', () => {
      researchPanelModule.toggle();
    });
  }

  // ── Cookbook modal toggle ──
  const toolCookbookBtn = el('tool-cookbook-btn');
  if (toolCookbookBtn) {
    toolCookbookBtn.addEventListener('click', async () => {
      if (!cookbookModule) return;
      // Try minimized→restore or open→minimize via the manager first
      const Modals = await import('./js/modalManager.js');
      if (!Modals.toggle('cookbook-modal')) {
        // Not registered yet → fresh open
        cookbookModule.open();
      }
    });
  }

  // Document library tool button
  const toolDoclibBtn = el('tool-doclib-btn');
  if (toolDoclibBtn) {
    toolDoclibBtn.addEventListener('click', () => {
      if (_closeCompareIfActive()) return;
      if (documentModule) {
        if (documentModule.isLibraryOpen()) {
          documentModule.closeLibrary();
        } else {
          documentModule.openLibrary();
        }
      }
    });
  }

  // Gallery tool button
  const toolGalleryBtn = el('tool-gallery-btn');
  if (toolGalleryBtn) {
    toolGalleryBtn.addEventListener('click', async () => {
      if (!galleryModule) return;
      const Modals = await import('./js/modalManager.js');
      if (!Modals.toggle('gallery-modal')) {
        if (galleryModule.isGalleryOpen()) galleryModule.closeGallery();
        else galleryModule.openGallery();
      }
    });
  }

  // Tasks tool button
  const toolTasksBtn = el('tool-tasks-btn');
  if (toolTasksBtn) {
  // Agents buttons (sidebar + rail)
  const agentsBtns = [el("rail-agents"), el("tool-agents-btn")].filter(Boolean);
  agentsBtns.forEach(btn => {
    btn.addEventListener("click", () => {
    });
  });
    toolTasksBtn.addEventListener('click', () => {
      if (tasksModule) {
        tasksModule.isTasksOpen() ? tasksModule.closeTasks() : tasksModule.openTasks();
      }
    });
  }

  // Calendar tool button
  const toolCalendarBtn = el('tool-calendar-btn');
  if (toolCalendarBtn) {
    toolCalendarBtn.addEventListener('click', async () => {
      if (!calendarModule) return;
      const Modals = await import('./js/modalManager.js');
      // toggle returns true when a registered modal was minimized/restored;
      // returns false when nothing is registered → open fresh.
      if (!Modals.toggle('calendar-modal')) {
        if (calendarModule.isCalendarOpen()) calendarModule.closeCalendar();
        else calendarModule.openCalendar();
      }
    });
  }

  // Notes tool button
  const toolNotesBtn = el('tool-notes-btn');
  if (toolNotesBtn) {
    toolNotesBtn.addEventListener('click', () => {
      if (notesModule) {
        notesModule.togglePanel();
      }
    });
  }
  // Refresh notes due-reminder badge on load and every 5 minutes
  if (notesModule && notesModule.refreshDueBadge) {
    notesModule.refreshDueBadge();
    setInterval(() => notesModule.refreshDueBadge(), 5 * 60 * 1000);
  }

  // URL-based panel routing — bookmark /calendar, /notes, /cookbook etc
  // and the matching tool opens automatically on page load.
  const urlPath = window.location.pathname;
  // Current width of the always-visible icon rail. The rail is resizable
  // and hides on narrow viewports, so read it live each call rather than
  // baking 48px in. Returns 0 when the rail isn't rendered.
  const _iconRailWidth = () => {
    const r = document.getElementById('icon-rail');
    if (!r) return 0;
    const cs = window.getComputedStyle(r);
    if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
    return Math.round(r.getBoundingClientRect().width);
  };
  // Collapse the wide sidebar so the icon rail (48px mini sidebar) shows
  // in its place. The two are mutually exclusive — sidebar-layout.js:57
  // only displays the rail when `.sidebar.hidden` is set. Used by /email
  // and /notes route openers so those fullscreen views keep the rail
  // visible as the user's navigation strip. Records the prior state on
  // body so a paired close-handler can restore it without overriding a
  // manual toggle the user did in between.
  const _collapseSidebarToRail = () => {
    const sb = document.getElementById('sidebar');
    const rail = document.getElementById('icon-rail');
    if (!sb || !rail) return;
    const wasVisible = !sb.classList.contains('hidden');
    if (wasVisible) {
      document.body.dataset.routeCollapsedSidebar = '1';
    }
    sb.classList.add('hidden');
    rail.classList.remove('rail-hidden');
    // syncRailSide() flips iconRail.style.display based on the classes
    // we just set. Exposed by sidebar-layout.js on window.
    try { window.syncRailSide && window.syncRailSide(); } catch (_) {}
  };
  // Paired restore: if the route opener collapsed the sidebar, re-expand
  // it when the fullscreen view closes. Only restores if the user didn't
  // manually toggle in between (we clear the marker on manual hamburger
  // clicks via a MutationObserver on `.sidebar.hidden`).
  const _restoreSidebarIfRouteCollapsed = () => {
    if (document.body.dataset.routeCollapsedSidebar !== '1') return;
    delete document.body.dataset.routeCollapsedSidebar;
    const sb = document.getElementById('sidebar');
    if (!sb) return;
    sb.classList.remove('hidden');
    try { window.syncRailSide && window.syncRailSide(); } catch (_) {}
  };
  // Expose so closeEmailLibrary / notes close can call this without
  // needing to import app.js directly.
  window._restoreSidebarIfRouteCollapsed = _restoreSidebarIfRouteCollapsed;
  // Clear the marker the moment the sidebar becomes visible again (user
  // hamburger click, or our own _restoreSidebarIfRouteCollapsed call —
  // both endpoints are the same observable state change).
  {
    const sb = document.getElementById('sidebar');
    if (sb && typeof MutationObserver !== 'undefined') {
      new MutationObserver(() => {
        if (!sb.classList.contains('hidden')) {
          delete document.body.dataset.routeCollapsedSidebar;
        }
      }).observe(sb, { attributes: true, attributeFilter: ['class'] });
    }
  }
  const _routeOpen = {
    '/notes':    () => {
      if (!notesModule) return;
      _collapseSidebarToRail();
      notesModule.openPanel();
      // Promote to fullscreen-with-rail-visible. The pane wires up its own
      // fullscreen toggle (#notes-fullscreen-toggle); piggyback on that
      // path so the button icon flips and overflow:hidden gets applied
      // alongside. Retry on rAF in case the panel mounts a tick later.
      const _go = () => {
        const btn = document.getElementById('notes-fullscreen-toggle');
        const pane = document.querySelector('.notes-pane');
        if (!pane) return false;
        if (!pane.classList.contains('notes-pane-fullscreen') && btn) btn.click();
        return true;
      };
      if (!_go()) {
        requestAnimationFrame(_go);
        setTimeout(_go, 50);
        setTimeout(_go, 200);
      }
    },
    '/calendar': () => calendarModule && calendarModule.openCalendar(),
    '/cookbook': () => document.getElementById('tool-cookbook-btn')?.click(),
    '/email':    () => {
      // Collapse the wide sidebar → icon rail (48px) so the user keeps
      // navigation visible alongside the fullscreen email view.
      _collapseSidebarToRail();
      // Spawn a fresh chat first so a reply (or any AI work the user
      // chains off the email) lives in its own session instead of grafting
      // onto whatever was last open. The rail button has the full
      // default-chat / fallback-model resolution logic baked in, so just
      // delegate to it.
      try { document.getElementById('rail-new-session')?.click(); } catch (_) {}
      // The email library is opened by clicking the email section's HEADER
      // row (.section-header-flex), not the title span. Trigger that, then
      // snap the modal to fullscreen on the next frame.
      const hdr = document.querySelector('#email-section .section-header-flex');
      if (hdr) hdr.click();
      // The modal is built synchronously inside openEmailLibrary, so a
      // single frame later it's in the DOM and ready to be flagged.
      // Fullscreen leaves the icon-rail visible on the left so navigation
      // stays one click away (per #93). Width = viewport minus rail.
      // Just add the class — the CSS rule for .email-lib-fullscreen .modal-content
      // owns all the positioning (with !important so it beats openEmailLibrary's
      // post-mount centering rAF) and reads the rail width from --icon-rail-w.
      const _goFullscreen = () => {
        const modal = document.getElementById('email-lib-modal');
        if (!modal) return false;
        modal.classList.add('email-lib-fullscreen');
        return true;
      };
      _goFullscreen();
      requestAnimationFrame(_goFullscreen);
      setTimeout(_goFullscreen, 50);
      setTimeout(_goFullscreen, 200);
    },
    '/memory':   () => document.getElementById('tool-memory-btn')?.click(),
    '/gallery':  () => document.getElementById('tool-gallery-btn')?.click(),
    '/tasks':    () => document.getElementById('tool-tasks-btn')?.click(),
    '/library':  () => sessionModule && sessionModule.openLibrary && sessionModule.openLibrary(),
  };
  const _opener = _routeOpen[urlPath];
  // Defer the opener — at this point in init, the modules whose handlers
  // we trigger (#rail-new-session click handler, the email-section header
  // click handler in emailInbox, sessionModule's loaded session list) are
  // still being wired up further down in this same function. Stash the
  // opener so it runs from sessionModule.loadSessions().finally() below.
  if (_opener) window._odysseusRouteOpener = _opener;

  // Archive browser tool button
  const toolLibraryBtn = el('tool-library-btn');
  if (toolLibraryBtn) {
    toolLibraryBtn.addEventListener('click', () => {
      if (sessionModule) sessionModule.openLibrary();
    });
  }

  // "+" on the Library row → create a new blank document and open it in the
  // editor (mirrors the email section's compose "+"). stopPropagation so it
  // doesn't also fire the row's open-library click.
  const libraryNewDocBtn = el('library-new-doc-btn');
  if (libraryNewDocBtn) {
    libraryNewDocBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        if (documentModule && documentModule.newDocument) await documentModule.newDocument();
      } catch (err) {
        console.error('New document from Library failed:', err);
        if (uiModule && uiModule.showError) uiModule.showError('Could not create document');
      }
    });
  }

  // Manage Chats — opens Full Library modal (decoupled from Chats accordion toggle)
  const chatsLibraryBtn = el('chats-library-btn');
  if (chatsLibraryBtn) {
    chatsLibraryBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (sessionModule) sessionModule.openLibrary('chats');
    });
  }

  const toolArchiveBtn = el('tool-archive-btn');
  if (toolArchiveBtn) {
    toolArchiveBtn.addEventListener('click', () => {
      if (sessionModule) sessionModule.openLibrary('archive');
    });
  }

  const toolThemeBtn = el('tool-theme-btn');
  if (toolThemeBtn) {
    toolThemeBtn.addEventListener('click', () => {
      const tm = document.getElementById('theme-modal');
      if (tm) tm.classList.remove('hidden');
    });
  }

  // Sidebar toggle
  const toggleSidebarOption = el('toggle-sidebar-option');
  if (toggleSidebarOption) {
    toggleSidebarOption.addEventListener('click', () => {
      const sidebar = el('sidebar');
      sidebar.classList.toggle('hidden');
    });
  }

  // Sidebar user bar — settings, admin, profile
  const userBarSettings = el('user-bar-settings');
  const userBarProfile = el('user-bar-profile');
  const userBarAdmin = el('user-bar-admin');

  if (userBarSettings) {
    userBarSettings.addEventListener('click', () => settingsModule.open());
  }
  if (userBarProfile) {
    // Clicking the user (avatar + name) jumps straight to the Account tab
    // instead of landing on whatever was last selected.
    userBarProfile.addEventListener('click', () => settingsModule.open('account'));
  }
  if (userBarAdmin) {
    userBarAdmin.addEventListener('click', () => adminModule.open());
  }

  // Fetch auth status — populate user bar and show admin button if admin
  fetch(`${API_BASE}/api/auth/status`, { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => {
      window._isAdmin = !!d.is_admin;
      if (d.is_admin && userBarAdmin) userBarAdmin.style.display = '';
      const userBarName = el('user-bar-name');
      const userBarAvatar = el('user-bar-avatar');
      if (userBarName && d.username) {
        let displayName = d.username;
        // Mask email addresses
        if (displayName.includes('@')) {
          const [local, domain] = displayName.split('@');
          const ext = domain.includes('.') ? domain.slice(domain.lastIndexOf('.')) : '';
          displayName = local.charAt(0) + '•••@••••' + ext;
        }
        userBarName.textContent = displayName;
        if (userBarAvatar) userBarAvatar.textContent = d.username.charAt(0).toUpperCase();
      }
      // Apply per-user privilege restrictions
      if (d.privileges) {
        window._userPrivileges = d.privileges;
        const p = d.privileges;
        // Hide agent mode toggle
        if (!p.can_use_agent) {
          const modeToggle = document.getElementById('mode-toggle');
          if (modeToggle) modeToggle.closest('.chat-input-toggle')?.style.setProperty('display', 'none');
        }
        // Hide bash toggle
        if (!p.can_use_bash) {
          const bashToggle = document.getElementById('bash-toggle');
          if (bashToggle) bashToggle.closest('.chat-input-toggle')?.style.setProperty('display', 'none');
          const bashBtn = document.getElementById('tool-bash-btn');
          if (bashBtn) bashBtn.style.display = 'none';
        }
        // Hide document button
        if (!p.can_use_documents) {
          const docBtn = document.getElementById('overflow-doc-btn');
          if (docBtn) docBtn.style.display = 'none';
          const docInd = document.getElementById('doc-indicator-btn');
          if (docInd) docInd.style.display = 'none';
        }
        // Hide research toggle
        if (!p.can_use_research) {
          const resBtn = document.getElementById('research-toggle-btn');
          if (resBtn) resBtn.style.display = 'none';
          const resOverflow = document.getElementById('overflow-research-btn');
          if (resOverflow) resOverflow.style.display = 'none';
        }
        // Hide image generation options
        if (!p.can_generate_images) {
          const imgBtn = document.getElementById('tool-image-btn');
          if (imgBtn) imgBtn.style.display = 'none';
        }
      }
    })
    .catch(() => {});

  // Session sort dropdown
  const sortBtn = el('session-sort-btn');
  const sortDropdown = el('session-sort-dropdown');
  if (sortBtn && sortDropdown) {
    sortBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      sortDropdown.style.display = sortDropdown.style.display === 'block' ? 'none' : 'block';
    });
    document.addEventListener('click', () => { sortDropdown.style.display = 'none'; });
    sortDropdown.addEventListener('click', (e) => e.stopPropagation());

    // Sort mode options (newest, oldest, last active) — toggleable
    sortDropdown.querySelectorAll('.sort-option').forEach(opt => {
      opt.addEventListener('click', () => {
        const mode = opt.dataset.sort;
        const current = sessionModule.getSortMode();
        // Toggle: clicking the active sort reverts to manual
        if (current === mode) {
          sessionModule.setSortMode(null);
          sortDropdown.style.display = 'none';
          uiModule.showToast('Manual order');
        } else {
          sessionModule.setSortMode(mode);
          sortDropdown.style.display = 'none';
          uiModule.showToast(`Sorted: ${opt.textContent.trim().toLowerCase()}`);
        }
        _syncSortChecks();
      });
    });

    // Sync checkmarks on sort options
    function _syncSortChecks() {
      const current = sessionModule.getSortMode();
      sortDropdown.querySelectorAll('.sort-option').forEach(o => {
        const check = o.querySelector('.sort-check') || document.createElement('span');
        check.className = 'sort-check';
        check.style.cssText = 'float:right;font-size:20px;line-height:1;position:relative;top:3px;color:var(--accent, var(--red));opacity:' + (o.dataset.sort === current ? '1' : '0');
        check.textContent = '\u2022';
        if (!o.querySelector('.sort-check')) o.appendChild(check);
      });
      // Highlight filter icon when a sort is active
      if (sortBtn) sortBtn.classList.toggle('active', !!current);
    }
    // Sync on dropdown open + initial load
    sortBtn.addEventListener('click', _syncSortChecks);
    _syncSortChecks();

    // AI auto-sort — spinner on the sort button itself. Used by both
    // the main "★ Tidy" button (AI) and the sub-row "Tidy" button
    // (no AI, Phase 1 cleanup only) via the skipLlm flag.
    async function _runTidy(skipLlm) {
      const btnIcon = sortBtn.querySelector('.sort-icon');
      if (btnIcon) btnIcon.style.display = 'none';
      const wp = spinnerModule.create('', 'clean', 'whirlpool');
      const wpEl = wp.createElement();
      wpEl.style.cssText = 'width:13px;height:13px;display:inline-block;vertical-align:middle;margin-top:-5px;';
      sortBtn.appendChild(wpEl);
      wp.start();
      sortDropdown.style.display = 'none';
      try {
        const url = `${API_BASE}/api/sessions/auto-sort${skipLlm ? '?skip_llm=true' : ''}`;
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Auto-sort failed');
        if (data.status === 'ok') {
          sessionModule.setSortMode(null); // clear sort — tidy creates manual folder order
          _syncSortChecks();
          if (skipLlm) {
            // No-AI path: just report what got cleaned. No "unfiled
            // remaining" prompt because we never tried to file anything.
            const cleaned = (data.deleted_empty || 0) + (data.deleted_throwaway || 0);
            uiModule.showToast(cleaned ? `Cleaned ${cleaned} empty/throwaway chat${cleaned === 1 ? '' : 's'}` : 'Already clean');
          } else {
            // Tidy now works in batches (15 most-recent unfiled per click)
            // so the user gets fast feedback and a manageable LLM call
            // even with hundreds of chats. Tell them what's left.
            const remaining = data.unfiled_remaining || 0;
            let msg;
            if (data.updated > 0) {
              msg = `Sorted ${data.updated} into ${data.folders.length} folder${data.folders.length === 1 ? '' : 's'}`;
              if (remaining > 0) msg += ` — ${remaining} unfiled left, hit Tidy again`;
            } else if (remaining > 0) {
              msg = `${remaining} unfiled chats — hit Tidy again`;
            } else {
              msg = 'All sorted';
            }
            uiModule.showToast(msg);
          }
          if (sessionModule) await sessionModule.loadSessions();
        } else {
          uiModule.showToast(data.reason || 'Nothing to sort');
        }
      } catch (e) {
        uiModule.showError('Auto-sort: ' + e.message);
      } finally {
        wp.destroy();
        if (wpEl.parentNode) wpEl.parentNode.removeChild(wpEl);
        if (btnIcon) btnIcon.style.display = '';
      }
    }

    const autoSortBtn = el('auto-sort-sessions-btn');
    if (autoSortBtn) autoSortBtn.addEventListener('click', () => _runTidy(false));

    // Chevron next to the Tidy row toggles the no-AI sub-item.
    const autoSortMoreBtn = el('auto-sort-sessions-more');
    const autoSortNoaiBtn = el('auto-sort-sessions-noai-btn');
    if (autoSortMoreBtn && autoSortNoaiBtn) {
      autoSortMoreBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        autoSortNoaiBtn.style.display = autoSortNoaiBtn.style.display === 'none' ? 'block' : 'none';
      });
      autoSortNoaiBtn.addEventListener('click', () => _runTidy(true));
    }
  }

  // Model sort dropdown
  const modelSortBtn = el('model-sort-btn');
  const modelSortDropdown = el('model-sort-dropdown');
  if (modelSortBtn && modelSortDropdown) {
    modelSortBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      modelSortDropdown.style.display = modelSortDropdown.style.display === 'block' ? 'none' : 'block';
    });
    document.addEventListener('click', () => { modelSortDropdown.style.display = 'none'; });
    modelSortDropdown.addEventListener('click', (e) => e.stopPropagation());
    modelSortDropdown.querySelectorAll('.sort-option').forEach(opt => {
      opt.addEventListener('click', () => {
        const mode = opt.dataset.sort;
        Storage.set('odysseus-model-sort', mode);
        if (modelsModule) modelsModule.refreshModels();
        modelSortDropdown.style.display = 'none';
        uiModule.showToast('Models sorted: ' + opt.textContent.trim().toLowerCase());
      });
    });
  }



  // Feature visibility — hide admin-disabled features
  // Use prefetched data from login page if available
  const _prefetchedFeatures = sessionStorage.getItem('ody-prefetch-features');
  sessionStorage.removeItem('ody-prefetch-features');
  window._initFeaturesReady = (_prefetchedFeatures
    ? Promise.resolve(JSON.parse(_prefetchedFeatures))
    : fetch(`${API_BASE}/api/auth/features`, { credentials: 'same-origin' }).then(r => r.json())
  ).then(features => {
      const map = {
        web_search:      ['web-toggle-btn'],
        deep_research:   ['research-toggle-btn', 'tool-research-btn', 'overflow-research-btn', 'rail-research'],
        document_editor: ['overflow-doc-btn', 'rail-documents'],
        gallery:         ['tool-gallery-btn', 'rail-gallery'],
      };
      Object.entries(map).forEach(([key, ids]) => {
        if (features[key] === false) {
          ids.forEach(id => { const e = el(id); if (e) e.style.display = 'none'; });
        }
      });
      // Re-apply the user's Appearance UI-vis preferences after the
      // features fetch finishes hiding things — otherwise an admin-
      // disabled feature leaves the sidebar entry hidden even when the
      // user's "Show in sidebar" toggle is on. The user has to toggle
      // off then on to trigger applyUIVis a second time, which is the
      // bug they report as "deep research only shows after I toggle".
      try { if (window.applyUIVis && window.loadUIVis) window.applyUIVis(window.loadUIVis()); } catch (_) {}
    })
    .catch(() => {});

  // Hide Gallery when image generation is disabled in settings
  const _prefetchedSettings = sessionStorage.getItem('ody-prefetch-settings');
  sessionStorage.removeItem('ody-prefetch-settings');
  window._initSettingsReady = (_prefetchedSettings
    ? Promise.resolve(JSON.parse(_prefetchedSettings))
    : fetch(`${API_BASE}/api/auth/settings`, { credentials: 'same-origin' }).then(r => r.json())
  ).then(settings => {
      // NOTE: image_gen_enabled only governs *generating* images in chat — the
      // tool is blocked server-side (chat_routes / agent_loop). The Gallery
      // holds uploads and past images too, so it stays visible regardless;
      // use the `gallery` feature flag to hide the Gallery entirely.
      // Hide TTS overflow button when TTS is disabled or no provider configured
      const ttsOff = settings.tts_enabled === false || !settings.tts_provider || settings.tts_provider === 'disabled';
      const overflowTts = el('overflow-tts-btn');
      if (overflowTts) {
        overflowTts.style.display = ttsOff ? 'none' : '';
      }
    })
    .catch(() => {});

  // (Logout handler moved to sidebar user bar above)

  // Rename AI modal
  const renameAiOption = el('rename-ai-option');
  const renameAiModal = el('rename-ai-modal');
  const closeRenameAi = el('close-rename-ai');
  const cancelRenameAi = el('cancel-rename-ai');
  const saveAiName = el('save-ai-name');
  const aiNameInput = el('ai-name-input');
  
  if (renameAiOption) {
    renameAiOption.addEventListener('click', () => {
      const currentName = aiNameInput.value;
      renameAiModal.classList.remove('hidden');
    });
  }
  
  if (closeRenameAi) {
    closeRenameAi.addEventListener('click', () => {
      renameAiModal.classList.add('hidden');
    });
  }
  
  if (cancelRenameAi) {
    cancelRenameAi.addEventListener('click', () => {
      renameAiModal.classList.add('hidden');
    });
  }
  
  if (saveAiName) {
    saveAiName.addEventListener('click', async () => {
      const newName = aiNameInput.value.trim();
      
      if (!newName) {
        uiModule.showError('Please enter a name for the AI');
        return;
      }
      
      try {
        const response = await fetch(`${API_BASE}/api/ai/name`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: newName })
        });
        
        const result = await response.json();
        if (result.success) {
          uiModule.showToast(`AI renamed to ${newName}`);
          renameAiModal.classList.add('hidden');
          aiNameInput.value = '';
        }
      } catch (e) {
        uiModule.showError('Failed to rename AI: ' + e.message);
      }
    });
  }

  // Memory management
  const memoryModal = el('memory-modal');
  const closeMemoryBtn = el('close-memory-modal');

  // Theme popup close button
  const closeThemeBtn = el('close-theme-popup');
  if (closeThemeBtn && themeModule) {
    closeThemeBtn.addEventListener('click', () => {
      themeModule.closePopup();
    });
  }

  // Rename session modal
  const renameSessionModal = el('rename-session-modal');
  const closeRenameSession = el('close-rename-session');
  const cancelRenameSession = el('cancel-rename-session');
  const saveSessionName = el('save-session-name');
  const sessionNameInput = el('session-name-input');
  
  // Close handlers for rename session modal
  if (closeRenameSession) {
    closeRenameSession.addEventListener('click', () => {
      renameSessionModal.classList.add('hidden');
    });
  }
  
  if (cancelRenameSession) {
    cancelRenameSession.addEventListener('click', () => {
      renameSessionModal.classList.add('hidden');
    });
  }
  
  if (saveSessionName) {
    saveSessionName.addEventListener('click', async () => {
      const newName = sessionNameInput.value.trim();
      
      if (!newName) {
        uiModule.showError('Please enter a name for the session');
        return;
      }
      
      try {
        const response = await fetch(`${API_BASE}/api/session/${sessionModule.getCurrentSessionId()}`, {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: newName })
        });
        
        const result = await response.json();
        if (response.ok) {
          uiModule.showToast(`Session renamed to ${newName}`);
          renameSessionModal.classList.add('hidden');
          sessionNameInput.value = '';
          // Update the current session name in the UI
          const meta = sessionModule.getSessions().find(s => s.id === sessionModule.getCurrentSessionId());
          if (meta) {
            meta.name = newName;
            const ver = window._appVersion ? ` v${window._appVersion}` : '';
            el('current-meta').textContent = `Session: ${meta.name}${meta.model ? ' ' + meta.model.split('/').pop() : ''}${meta.rag ? ' [RAG]' : ''}${ver}`;
          }
          // Refresh the sessions list
        await sessionModule.loadSessions();
        } else {
          throw new Error(result.detail || 'Failed to rename session');
        }
      } catch (e) {
        uiModule.showError('Failed to rename session: ' + e.message);
      }
    });
  }
  
  if (closeMemoryBtn) {
    closeMemoryBtn.addEventListener('click', () => {
      dismissModal(memoryModal);
    });
  }

  // Sidebar Memory button
  const toolMemoryBtn = el('tool-memory-btn');
  if (toolMemoryBtn && memoryModal) {
    toolMemoryBtn.addEventListener('click', () => {
      memoryModal.classList.remove('hidden');
      if (memoryModule && memoryModule.renderMemoryList) memoryModule.renderMemoryList();
      if (memoryModule && memoryModule.updateMemoryCount) memoryModule.updateMemoryCount();
    });
  }

  const addMemBtn = el('add-memory-btn');
  if (addMemBtn) {
    addMemBtn.addEventListener('click', memoryModule.addNewMemory);
  }
  
  const memorySearchInput = el('memory-search');
  if (memorySearchInput) {
    memorySearchInput.addEventListener('input', () => {
      memoryModule.renderMemoryList();
      memoryModule.updateMemoryCount();
    });
  }
  
  const newMemoryInput = el('new-memory-input');
  if (newMemoryInput) {
    newMemoryInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        memoryModule.addNewMemory();
      }
    });
  }

// Voice recording is handled by the dual-purpose send/mic button (see below)

  // ── Toggle persistence — delegates to Storage module ──
  function loadToggleState() {
    return Storage.loadToggleState();
  }
  function saveToggleState(state) {
    Storage.saveToggleState(state);
  }

  // Mode-affected tools: default ON in Agent mode, default OFF in Chat mode,
  // but the user's explicit per-mode override is persisted and honored.
  const MODE_TOOLS = [
    { btnId: 'web-toggle-btn',  checkboxId: 'web-toggle',  stateKey: 'web' },
    { btnId: 'bash-toggle-btn', checkboxId: 'bash-toggle', stateKey: 'bash' },
    { btnId: 'plan-toggle-btn', checkboxId: 'plan-toggle', stateKey: 'plan' },
  ];

  function _modeKey(stateKey, mode) { return `${stateKey}_${mode}`; }

  function loadToolPref(stateKey, mode) {
    const state = loadToggleState();
    const key = _modeKey(stateKey, mode);
    if (Object.prototype.hasOwnProperty.call(state, key)) return !!state[key];
    // Plan mode is opt-in: never default it on, otherwise every agent turn
    // would be forced into planning.
    if (stateKey === 'plan') return false;
    return mode === 'agent'; // default: ON in agent, OFF in chat
  }

  function saveToolPref(stateKey, mode, value) {
    const state = loadToggleState();
    state[_modeKey(stateKey, mode)] = value;
    saveToggleState(state);
  }

  const TOOL_TOGGLE_TOAST_LABELS = {
    web: 'Web search',
    bash: 'Shell',
    plan: 'Plan mode',
  };

  function showToolToggleToast(stateKey, active) {
    const label = TOOL_TOGGLE_TOAST_LABELS[stateKey];
    if (!label || !uiModule?.showToast) return;
    uiModule.showToast(`${label} ${active ? 'on' : 'off'}`, 1800);
  }

  function applyModeToToggles(mode) {
    MODE_TOOLS.forEach(({ btnId, checkboxId, stateKey }) => {
      const btn = el(btnId);
      if (!btn || btn.style.display === 'none') return;
      const on = loadToolPref(stateKey, mode);
      btn.classList.toggle('active', on);
      if (checkboxId) { const chk = el(checkboxId); if (chk) chk.checked = on; }
    });
  }

  // ── Agent / Chat mode toggle ──
  (function initModeToggle() {
    const agentBtn = el('mode-agent-btn');
    const chatBtn = el('mode-chat-btn');
    if (!agentBtn || !chatBtn) return;
    const state = loadToggleState();
    let currentMode = state.mode || 'chat';

    function setMode(mode) {
      currentMode = mode;
      const st = loadToggleState();
      st.mode = mode;
      saveToggleState(st);
      agentBtn.classList.toggle('active', mode === 'agent');
      chatBtn.classList.toggle('active', mode === 'chat');
      agentBtn.setAttribute('aria-pressed', String(mode === 'agent'));
      chatBtn.setAttribute('aria-pressed', String(mode === 'chat'));
      // Slide the pill to the active button
      const toggle = agentBtn.closest('.mode-toggle');
      if (toggle) toggle.classList.toggle('mode-chat', mode === 'chat');
      // Delay tool glow-up for a staggered effect
      setTimeout(() => applyModeToToggles(mode), 500);
    }
    agentBtn.addEventListener('click', () => {
      // Agent mode turns off research if active
      const resChk = el('research-toggle');
      if (resChk && resChk.checked) _syncResearchIndicator(false);
      setMode('agent');
    });
    chatBtn.addEventListener('click', () => setMode('chat'));
    setMode(currentMode);
  })();

  // ── Tool splash explainer messages (shown first 2 times per tool) ──
  const SPLASH_COUNT_KEY = 'odysseus-tool-splash-counts';
  const SPLASH_MAX = 2;
  const _toolSplashes = {
    web: { role: 'Web Search', text: 'Searches the web for relevant information to include in the response. Results are fetched and summarized before the AI answers.' },
    bash: { role: 'Shell Access', text: 'Gives the AI access to a sandboxed shell for running commands, installing packages, and executing scripts. Use with caution.' },
    builder: { role: 'Tool Builder', text: 'Create custom mini-apps and tools the AI can use. Describe what you need and the AI will build a tool you can reuse across conversations.' },
    research: { role: 'Deep Research', text: 'Multi-round web search with source analysis. Takes longer but produces comprehensive, well-sourced answers. Your next message will trigger a deep research cycle.' },
  };
  function _showToolSplash(key) {
    const splash = _toolSplashes[key];
    if (!splash) return;
    // Only show the first SPLASH_MAX times per tool
    const counts = Storage.getJSON(SPLASH_COUNT_KEY, {});
    const seen = counts[key] || 0;
    if (seen >= SPLASH_MAX) return;
    counts[key] = seen + 1;
    Storage.setJSON(SPLASH_COUNT_KEY, counts);
    // Hide welcome screen so splash is visible
    if (chatModule && chatModule.hideWelcomeScreen) {
      chatModule.hideWelcomeScreen();
    }
    const chatBox = document.getElementById('chat-history');
    if (!chatBox) return;
    const div = document.createElement('div');
    div.className = 'msg msg-ai tool-splash';
    div.innerHTML = '<div class="role">' + splash.role + '</div><div class="body" style="opacity:0.7;font-size:0.92em">' + splash.text + '</div>';
    chatBox.appendChild(div);
    if (uiModule) uiModule.scrollHistory();
  }

  // ── Checkbox-backed toggle buttons (with per-mode persistence) ──
  function setupToggle(btnId, checkboxId, stateKey) {
    const btn = el(btnId);
    if (!btn) return;
    // Restore per-mode saved state for both Agent and Chat modes.
    const mode = (loadToggleState().mode) || 'chat';
    const saved = loadToolPref(stateKey, mode);
    const chk = el(checkboxId);
    if (chk) chk.checked = saved;
    btn.classList.toggle('active', saved);
    btn.setAttribute('aria-pressed', String(saved));
    btn.addEventListener('click', () => {
      const curMode = (loadToggleState().mode) || 'chat';
      const chk = el(checkboxId);
      chk.checked = !chk.checked;
      btn.classList.toggle('active', chk.checked);
      btn.setAttribute('aria-pressed', String(chk.checked));
      saveToolPref(stateKey, curMode, chk.checked);
      showToolToggleToast(stateKey, chk.checked);
      if (chk.checked) _showToolSplash(stateKey);
      // Web search and Research are mutually exclusive — Research takes priority
      if (stateKey === 'web' && chk.checked) {
        const resChk = el('research-toggle');
        if (resChk && resChk.checked) {
          _syncResearchIndicator(false);
        }
      }
    });
  }
  setupToggle('web-toggle-btn', 'web-toggle', 'web');
  setupToggle('bash-toggle-btn', 'bash-toggle', 'bash');
  try { workspaceModule.initWorkspace(); } catch (_) {}
  setupToggle('plan-toggle-btn', 'plan-toggle', 'plan');

  // Set plan mode on/off directly (checkbox + button state + saved pref) WITHOUT
  // going through the button's click handler — used by the plan menu and by the
  // "Approve & Run" flow. Going through .click() would hit the plan-menu
  // intercept below (a stored plan re-opens the menu instead of toggling), which
  // is exactly the bug that left approved plans stuck in plan mode.
  function _setPlanMode(on) {
    const btn = el('plan-toggle-btn');
    const chk = el('plan-toggle');
    const mode = (loadToggleState().mode) || 'chat';
    if (chk) chk.checked = !!on;
    if (btn) { btn.classList.toggle('active', !!on); btn.setAttribute('aria-pressed', String(!!on)); }
    saveToolPref('plan', mode, !!on);
  }
  window._setPlanMode = _setPlanMode;

  // ── Plan-button menu ──
  // When a plan exists for this chat, clicking the plan button opens a small
  // menu (Show plan / Plan mode on-off) instead of plain-toggling — so the plan
  // window can be re-opened and docked at any time while the agent works. With
  // no plan, the button behaves as before (one-click toggle).
  (function initPlanMenu() {
    const planBtn = el('plan-toggle-btn');
    if (!planBtn) return;
    const _hasPlan = () => { try { return !!(window._getStoredPlan && window._getStoredPlan()); } catch (_) { return false; } };
    const _close = () => { const m = document.getElementById('plan-menu'); if (m) m.remove(); };
    function _open() {
      _close();
      const planChk = el('plan-toggle');
      const on = !!(planChk && planChk.checked);
      const menu = document.createElement('div');
      menu.id = 'plan-menu';
      menu.className = 'overflow-menu plan-menu';
      menu.innerHTML =
        '<button type="button" class="overflow-menu-item" data-act="show">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>'
        + '<span>Show plan</span></button>'
        + '<button type="button" class="overflow-menu-item" data-act="toggle">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
        + '<span>Plan mode: ' + (on ? 'On' : 'Off') + '</span></button>';
      document.body.appendChild(menu);
      const r = planBtn.getBoundingClientRect();
      menu.style.position = 'fixed';
      menu.style.left = Math.round(r.left) + 'px';
      menu.style.top = Math.round(r.top - menu.offsetHeight - 6) + 'px';
      menu.querySelector('[data-act="show"]').addEventListener('click', () => {
        _close();
        const txt = window._getStoredPlan ? window._getStoredPlan() : '';
        if (txt && window.planWindowModule) window.planWindowModule.openPlanWindow(txt, null);
      });
      menu.querySelector('[data-act="toggle"]').addEventListener('click', () => {
        _close();
        _setPlanMode(!on);   // flip state directly (no click → no menu re-open)
      });
      // Dismiss on any outside click (capture so it beats other handlers) / Escape.
      setTimeout(() => {
        const off = (e) => {
          if (!menu.contains(e.target) && e.target !== planBtn) {
            _close(); document.removeEventListener('click', off, true); document.removeEventListener('keydown', esc, true);
          }
        };
        const esc = (e) => { if (e.key === 'Escape') { _close(); document.removeEventListener('click', off, true); document.removeEventListener('keydown', esc, true); } };
        document.addEventListener('click', off, true);
        document.addEventListener('keydown', esc, true);
      }, 0);
    }
    planBtn.addEventListener('click', (e) => {
      // With a stored plan, the button opens the menu (Show plan / toggle).
      // Without one, it falls through to the normal one-click toggle.
      if (_hasPlan()) { e.preventDefault(); e.stopImmediatePropagation(); _open(); }
    }, true);  // capture phase: intercept before setupToggle's bubble handler
  })();

  try { workspaceModule.initWorkspace(); } catch (_) {}

  // Document editor toggle (special: uses module panel, not a checkbox)
  const overflowDocBtn = el('overflow-doc-btn');
  if (overflowDocBtn) {
    overflowDocBtn.addEventListener('click', async () => {
      if (!documentModule) return;
      if (documentModule.isPanelOpen()) {
        documentModule.closePanel();
        overflowDocBtn.classList.remove('active');
        const st = loadToggleState(); st.doc = false; saveToggleState(st);
      } else {
        let sessionId = sessionModule.getCurrentSessionId();
        // If there's a pending "New Chat", materialize it first
        if (!sessionId && sessionModule.hasPendingChat && sessionModule.hasPendingChat()) {
          await sessionModule.materializePendingSession();
          sessionId = sessionModule.getCurrentSessionId();
        }
        if (sessionId) {
          documentModule.loadSessionDocs(sessionId, { forceOpen: true });
        } else {
          documentModule.ensureDocPanel();
        }
        overflowDocBtn.classList.add('active');
        const st = loadToggleState(); st.doc = true; saveToggleState(st);
      }
    });
  }

  // Document indicator button (shown outside overflow when docs exist)
  const docIndicatorBtn = el('doc-indicator-btn');
  if (docIndicatorBtn) {
    docIndicatorBtn.addEventListener('click', () => {
      const ob = el('overflow-doc-btn');
      if (ob) ob.click();
    });
  }

  // ── RAG toggle (overflow + indicator) ──
  function _syncRagIndicator(active) {
    const indicator = el('rag-indicator-btn');
    const overflow = el('overflow-rag-btn');
    const chk = el('rag-toggle');
    if (chk) chk.checked = active;
    if (indicator) {
      indicator.style.display = active ? '' : 'none';
      indicator.classList.toggle('active', active);
    }
    if (overflow) overflow.classList.toggle('active', active);
    const s = loadToggleState(); s.rag = active; saveToggleState(s);
    updatePlusDot();
  }
  window._syncRagIndicator = _syncRagIndicator;
  window._syncResearchIndicator = _syncResearchIndicator;
  // Must be assigned at module level (not inside the function body) so the very
  // first external caller — group.js / sessions.js fire it before it has ever
  // run locally — finds it instead of silently no-op'ing (the "group indicator
  // sometimes doesn't appear" bug).
  window._syncGroupIndicator = _syncGroupIndicator;
  // Init RAG state on load
  {
    const st = loadToggleState();
    const ragState = st.rag || false;
    _syncRagIndicator(ragState);
  }

  // ── Overflow "..." menu (Research) ──
  function updatePlusDot() {
    const plusBtn = el('overflow-plus-btn');
    if (!plusBtn) return;
    const menu = el('overflow-menu');
    const anyActive = menu ? Array.from(menu.querySelectorAll('.overflow-menu-item.active')).some(item => item.style.display !== 'none') : false;
    plusBtn.classList.toggle('has-active', anyActive);
  }
  // External modules (compare) dispatch this when their overflow state changes
  document.addEventListener('overflow-state-change', () => updatePlusDot());

  // ── Prevent toolbar buttons from stealing focus (avoids mobile keyboard bounce) ──
  const chatInputBar = document.querySelector('.chat-input-bar');
  // ── Keep textarea focused when interacting with chat bar controls (mobile keyboard fix) ──
  const _msgTextarea = el('message');
  if (chatInputBar && _msgTextarea) {
    let _refocusOnBlur = false;
    function _flagRefocus(e) {
      if (e.target.closest('textarea, input')) return;
      // Don't refocus for attach — file picker needs full focus control
      if (e.target.closest('#overflow-attach-btn')) return;
      // Don't refocus for model picker button — focus should go to picker search input
      if (e.target.closest('.model-picker-btn')) return;
      // Don't refocus when tapping the +/chevron tools button — the user
      // is explicitly trying to dismiss the keyboard and open the tools
      // menu. Without this, the textarea blurs (keyboard down), then this
      // handler re-focuses it (keyboard bounces back up).
      if (e.target.closest('#overflow-plus-btn')) return;
      if (document.activeElement === _msgTextarea) _refocusOnBlur = true;
    }
    chatInputBar.addEventListener('touchstart', _flagRefocus, { passive: true });
    // Overflow menu is position:fixed — may not bubble through chatInputBar on mobile
    const _overflowMenu = el('overflow-menu');
    if (_overflowMenu) _overflowMenu.addEventListener('touchstart', _flagRefocus, { passive: true });
    // Model picker menu too
    const _pickerMenu = document.getElementById('model-picker-menu');
    if (_pickerMenu) _pickerMenu.addEventListener('touchstart', _flagRefocus, { passive: true });
    // Attach strip (outside chat-input-bar)
    const _attachStrip = el('attach-strip');
    if (_attachStrip) _attachStrip.addEventListener('touchstart', _flagRefocus, { passive: true });
    _msgTextarea.addEventListener('blur', () => {
      if (_refocusOnBlur) {
        _refocusOnBlur = false;
        setTimeout(() => _msgTextarea.focus(), 0);
      }
    });
    // Clear flag if touch ends without causing blur
    document.addEventListener('touchend', () => { setTimeout(() => { _refocusOnBlur = false; }, 50); }, { passive: true });
  }

  (function initOverflowMenu() {
    const plusBtn = el('overflow-plus-btn');
    const menu = el('overflow-menu');
    if (!plusBtn || !menu) return;

    // `.chat-input-bar` has `container-type: inline-size`, which makes it the
    // containing block for `position: fixed` descendants — so this menu gets
    // trapped in the composer's stacking context and renders BEHIND the
    // attach-strip (worse the more files you add). Portal it to <body> while
    // open so its fixed position + z-index apply against the viewport, then
    // restore it to its wrapper on close.
    const ownerWrap = menu.parentElement;
    const pickerWrap = el('model-picker-wrap');
    let _vvReposition = null;
    // Pin the menu's bottom 8px above the chevron (viewport-relative, since it's
    // portaled to <body>). Only cap height + show a scrollbar when the list is
    // genuinely taller than the room above the button.
    function positionMenu() {
      const r = plusBtn.getBoundingClientRect();
      menu.style.left = r.left + 'px';
      menu.style.right = 'auto';
      menu.style.bottom = 'auto';
      menu.style.maxHeight = '';      // reset so we can measure the natural height
      menu.style.overflowY = '';
      const avail = r.top - 16;        // room above the chevron
      const natural = menu.scrollHeight;
      const h = Math.min(natural, avail);
      if (natural > avail) {           // only cap + scroll when it doesn't fit
        menu.style.maxHeight = avail + 'px';
        menu.style.overflowY = 'auto';
      }
      menu.style.top = (r.top - 8 - h) + 'px';
    }
    // Tapping the chevron must NOT steal focus from the message box, or the
    // mobile keyboard collapses. preventDefault on pointerdown keeps the
    // textarea focused (keyboard stays up) while click still opens the menu.
    plusBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); });
    plusBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Closing path needs to play the fold-in animation, not just flip
      // .hidden — route through closeOverflowMenu so the second-click
      // close looks the same as click-outside / Escape / item-pick.
      const isOpen = !menu.classList.contains('hidden') && !menu.classList.contains('closing');
      if (isOpen) {
        closeOverflowMenu();
        return;
      }
      // Re-opening while a fold-in is mid-animation: cancel it cleanly.
      menu.classList.remove('closing');
      menu.classList.remove('hidden');
      plusBtn.classList.add('expanded');
      document.body.appendChild(menu);  // escape the composer's container-type trap
      // Hide pill bar label so it doesn't show through the menu
      if (pickerWrap) pickerWrap.style.visibility = 'hidden';
      // Keep the textarea focused so the keyboard stays up if it was open (the
      // pointerdown handler above prevents the focus-steal). Still watch
      // visualViewport so the menu follows the chevron if the viewport shifts.
      positionMenu();
      if (window.visualViewport && !_vvReposition) {
        _vvReposition = () => positionMenu();
        window.visualViewport.addEventListener('resize', _vvReposition);
        window.visualViewport.addEventListener('scroll', _vvReposition);
      }
    });
    function closeOverflowMenu() {
      if (menu.classList.contains('hidden')) return;
      if (menu.classList.contains('closing')) return;
      if (_vvReposition && window.visualViewport) {
        window.visualViewport.removeEventListener('resize', _vvReposition);
        window.visualViewport.removeEventListener('scroll', _vvReposition);
        _vvReposition = null;
      }
      // Play the fold-in animation (items peel top-down, then container
      // scales back into the chevron) before flipping to display:none.
      menu.classList.add('closing');
      plusBtn.classList.remove('expanded');
      if (pickerWrap) pickerWrap.style.visibility = '';
      // Item delays max at 0.18s + 0.20s anim = 0.38s for items, container
      // delay 0.16s + 0.22s = 0.38s. 400ms covers both with margin.
      setTimeout(() => {
        menu.classList.add('hidden');
        menu.classList.remove('closing');
        if (ownerWrap) ownerWrap.appendChild(menu);  // restore from <body> portal
      }, 400);
    }
    // Close menu when clicking any item inside it. preventDefault on pointerdown
    // so tapping an item (e.g. Attach files) doesn't steal focus from the message
    // box — keeps the mobile keyboard up.
    menu.querySelectorAll('.overflow-menu-item').forEach(item => {
      item.addEventListener('pointerdown', (e) => { e.preventDefault(); });
      item.addEventListener('click', () => closeOverflowMenu());
    });
    document.addEventListener('click', (e) => {
      if (!menu.contains(e.target) && e.target !== plusBtn) closeOverflowMenu();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !menu.classList.contains('hidden')) closeOverflowMenu();
    });

    // Research toggle
    const researchBtn = el('research-toggle-btn');
    if (researchBtn) {
      const st = loadToggleState();
      const resState = st.research || false;
      el('research-toggle').checked = resState;
      researchBtn.classList.toggle('active', resState);
      researchBtn.style.display = resState ? '' : 'none';
      // Sync overflow + tool sidebar on load
      const overflowRes = el('overflow-research-btn');
      if (overflowRes) overflowRes.classList.toggle('active', resState);
      const toolRes = el('tool-research-btn');
      if (toolRes) toolRes.classList.toggle('active', resState);
      // On load: if both research and web are ON, research wins
      if (resState) {
        const webChk = el('web-toggle');
        const webBtn = el('web-toggle-btn');
        if (webChk && webChk.checked) {
          webChk.checked = false;
          if (webBtn) webBtn.classList.remove('active');
          saveToolPref('web', (st.mode || 'chat'), false);
        }
      }

      researchBtn.addEventListener('click', () => {
        const chk = el('research-toggle');
        const turningOn = chk ? !chk.checked : false;
        _syncResearchIndicator(turningOn);
        if (turningOn) {
          _showToolSplash('research');
          // Clear character — mutually exclusive with research
          if (presetsModule && presetsModule.deactivateCharacter) presetsModule.deactivateCharacter();
          // Research and Web search are mutually exclusive
          const webChk = el('web-toggle');
          const webBtn = el('web-toggle-btn');
          if (webChk && webChk.checked) {
            webChk.checked = false;
            if (webBtn) webBtn.classList.remove('active');
            saveToolPref('web', (loadToggleState().mode || 'chat'), false);
          }
          // Research requires chat mode — force switch from agent
          const rs = loadToggleState();
          if (rs.mode === 'agent') {
            rs.mode = 'chat';
            saveToggleState(rs);
            const ab = el('mode-agent-btn'), cb = el('mode-chat-btn');
            if (ab) ab.classList.remove('active');
            if (cb) cb.classList.add('active');
            applyModeToToggles('chat');
          }
        }
      });
    }

    updatePlusDot();
  })();

  // ── Auto-collapse toolbar buttons into overflow when space is tight ──
  (function initToolbarOverflow() {
    const inputLeft = document.querySelector('.chat-input-left');
    const overflowMenu = el('overflow-menu');
    const overflowWrapper = document.querySelector('.overflow-wrapper');
    if (!inputLeft || !overflowMenu || !overflowWrapper) return;

    // Buttons that can be collapsed (in reverse priority — last collapsed first)
    const collapsibleIds = ['bash-toggle-btn', 'web-toggle-btn'];
    const collapsibleBtns = collapsibleIds.map(id => el(id)).filter(Boolean);
    // Map of toolbar btn id → overflow mirror element (created dynamically)
    const overflowMirrors = new Map();

    // Create overflow mirror items for each collapsible button
    collapsibleBtns.forEach(btn => {
      const mirror = document.createElement('button');
      mirror.type = 'button';
      mirror.className = 'overflow-menu-item toolbar-overflow-mirror';
      mirror.dataset.mirrorOf = btn.id;
      const title = btn.title || btn.id.replace(/-/g, ' ');
      mirror.innerHTML = btn.querySelector('svg').outerHTML + '<span>' + title + '</span>' +
        '<span class="overflow-active-dot"></span>';
      mirror.style.display = 'none';
      mirror.addEventListener('click', () => btn.click());
      // Insert at top of overflow menu (before existing items)
      overflowMenu.insertBefore(mirror, overflowMenu.firstChild);
      overflowMirrors.set(btn.id, mirror);
    });

    function syncMirrorStates() {
      overflowMirrors.forEach((mirror, btnId) => {
        const btn = el(btnId);
        if (btn) mirror.classList.toggle('active', btn.classList.contains('active'));
      });
      updatePlusDot();
    }

    function checkToolbarOverflow() {
      const inputBottom = inputLeft.parentElement;
      if (!inputBottom) return;
      const rightEl = document.querySelector('.chat-input-right');
      const available = inputBottom.clientWidth -
        (rightEl ? rightEl.offsetWidth : 0) - 16;

      // Uncollapse all to measure natural widths
      collapsibleBtns.forEach(btn => btn.classList.remove('toolbar-collapsed'));
      overflowMirrors.forEach(m => m.style.display = 'none');

      // Temporarily allow overflow for accurate measurement
      const prevOverflow = inputLeft.style.overflow;
      inputLeft.style.overflow = 'visible';
      inputLeft.style.flexWrap = 'nowrap';

      // Force reflow then measure each child
      void inputLeft.offsetWidth;

      // Measure the overflow wrapper (always visible)
      const wrapperWidth = overflowWrapper.offsetWidth + 4;

      // Measure each collapsible button's natural width
      const btnWidths = collapsibleBtns.map(btn => btn.offsetWidth + 4);

      // Measure non-collapsible, non-wrapper children (tool indicators etc)
      let otherWidth = 0;
      Array.from(inputLeft.children).forEach(c => {
        if (c === overflowWrapper) return;
        if (collapsibleBtns.includes(c)) return;
        if (c.offsetWidth) otherWidth += c.offsetWidth + 4;
      });

      let totalWidth = wrapperWidth + otherWidth + btnWidths.reduce((a, b) => a + b, 0);

      // Force-collapse shell & search when research mode + doc panel are both active
      const _resChk = el('research-toggle');
      const _researchOn = _resChk && _resChk.checked;
      const _docViewOn = document.body.classList.contains('doc-view');
      if (_researchOn && _docViewOn) {
        collapsibleBtns.forEach(btn => {
          btn.classList.add('toolbar-collapsed');
          const mirror = overflowMirrors.get(btn.id);
          if (mirror) mirror.style.display = '';
        });
        inputLeft.style.overflow = prevOverflow;
        inputLeft.style.flexWrap = '';
        syncMirrorStates();
        return;
      }

      // Collapse from lowest priority until it fits
      if (totalWidth > available) {
        for (let i = 0; i < collapsibleBtns.length; i++) {
          collapsibleBtns[i].classList.add('toolbar-collapsed');
          const mirror = overflowMirrors.get(collapsibleBtns[i].id);
          if (mirror) mirror.style.display = '';
          totalWidth -= btnWidths[i];
          if (totalWidth <= available) break;
        }
      }

      // Restore
      inputLeft.style.overflow = prevOverflow;
      inputLeft.style.flexWrap = '';
      syncMirrorStates();
    }

    // Observe active class changes to sync mirror states
    const observer = new MutationObserver(() => syncMirrorStates());
    collapsibleBtns.forEach(btn => {
      observer.observe(btn, { attributes: true, attributeFilter: ['class'] });
    });

    // Run on resize and on load
    window.addEventListener('resize', () => requestAnimationFrame(checkToolbarOverflow));
    // Run immediately (state is already restored by this point)
    checkToolbarOverflow();
    // Re-check when sidebar toggles (changes available width)
    document.addEventListener('overflow-state-change', () =>
      requestAnimationFrame(checkToolbarOverflow));
    // Also re-check when sidebar visibility changes
    const sidebarEl = el('sidebar');
    if (sidebarEl) {
      new MutationObserver(() => requestAnimationFrame(checkToolbarOverflow))
        .observe(sidebarEl, { attributes: true, attributeFilter: ['class'] });
    }
    // Re-check when doc panel opens/closes (body.doc-view toggled)
    new MutationObserver(() => requestAnimationFrame(checkToolbarOverflow))
      .observe(document.body, { attributes: true, attributeFilter: ['class'] });
    // Re-check when input bar itself resizes (e.g. doc panel drag)
    const inputBottom = inputLeft.parentElement;
    if (inputBottom) {
      new ResizeObserver(() => requestAnimationFrame(checkToolbarOverflow)).observe(inputBottom);
    }
  })();

  // ── Auto-hide model picker when textarea area is too narrow ──
  (function initModelPickerResponsive() {
    const inputTop = document.querySelector('.chat-input-top');
    const pickerWrap = el('model-picker-wrap');
    if (!inputTop || !pickerWrap) return;

    const PLACEHOLDER_HIDE_WIDTH = 400;
    const PICKER_HIDE_WIDTH = 220;
    const TOOLBAR_HIDE_WIDTH = 160;
    const textarea = el('message');
    const inputBottom = document.querySelector('.chat-input-bottom');
    const _isMobile = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    function checkPickerOverflow() {
      // Skip responsive collapse on mobile — keyboard open/close causes flicker
      if (_isMobile) return;
      const w = inputTop.clientWidth;
      // Hide model picker
      pickerWrap.classList.toggle('picker-auto-hidden', w < PICKER_HIDE_WIDTH);
      // Hide placeholder text
      if (textarea) {
        textarea.setAttribute('placeholder', w < PLACEHOLDER_HIDE_WIDTH ? '' : 'Message Odysseus...');
      }
      // Hide entire bottom toolbar (tools, mode toggle) — only send button remains
      if (inputBottom) {
        inputBottom.classList.toggle('toolbar-auto-hidden', w < TOOLBAR_HIDE_WIDTH);
      }
    }

    const ro = new ResizeObserver(() => requestAnimationFrame(checkPickerOverflow));
    ro.observe(inputTop);
    checkPickerOverflow();
  })();

  // TTS Mode toggle (separate from overflow IIFE for safety)
  (function initTTSToggle() {
    const ttsBtn = document.getElementById('overflow-tts-btn');
    if (!ttsBtn) return;
    try {
      const st = loadToggleState();
      if (st.ttsMode) {
        ttsBtn.classList.add('active');
        if (window.aiTTSManager) window.aiTTSManager.autoPlay = true;
      }
    } catch(e) {}

    ttsBtn.addEventListener('click', () => {
      const isActive = !ttsBtn.classList.contains('active');
      ttsBtn.classList.toggle('active', isActive);
      if (window.aiTTSManager) window.aiTTSManager.autoPlay = isActive;
      const s = loadToggleState(); s.ttsMode = isActive; saveToggleState(s);
      updatePlusDot();
    });
  })();


  // ── Compare indicator (sidebar only, no overflow) ──
  const compareIndicatorBtn = el('compare-indicator-btn');
  if (compareIndicatorBtn) {
    compareIndicatorBtn.addEventListener('click', () => {
      if (compareModule && compareModule.isActive()) {
        compareModule.closeCompare();
      }
    });
  }

  // ── Overflow RAG toggle ──
  const overflowRagBtn = el('overflow-rag-btn');
  const ragIndicatorBtn = el('rag-indicator-btn');
  if (overflowRagBtn) {
    overflowRagBtn.addEventListener('click', () => {
      const chk = el('rag-toggle');
      const isActive = chk ? !chk.checked : true;
      _syncRagIndicator(isActive);
    });
  }
  if (ragIndicatorBtn) {
    ragIndicatorBtn.addEventListener('click', () => {
      _syncRagIndicator(false);
    });
  }

  // ── Overflow Research toggle ──
  const overflowResearchBtn = el('overflow-research-btn');
  if (overflowResearchBtn) {
    overflowResearchBtn.addEventListener('click', () => {
      const chk = el('research-toggle');
      const turningOn = chk ? !chk.checked : false;
      _syncResearchIndicator(turningOn);
      if (turningOn) {
        _showToolSplash('research');
        // Clear character — mutually exclusive with research
        if (presetsModule && presetsModule.deactivateCharacter) presetsModule.deactivateCharacter();
        // Mutual exclusion with web search
        const webChk = el('web-toggle');
        const webBtn = el('web-toggle-btn');
        if (webChk && webChk.checked) {
          webChk.checked = false;
          if (webBtn) webBtn.classList.remove('active');
          saveToolPref('web', (loadToggleState().mode || 'chat'), false);
        }
        // Research requires chat mode
        const rs2 = loadToggleState();
        if (rs2.mode === 'agent') {
          rs2.mode = 'chat';
          saveToggleState(rs2);
          const ab2 = el('mode-agent-btn'), cb2 = el('mode-chat-btn');
          if (ab2) ab2.classList.remove('active');
          if (cb2) cb2.classList.add('active');
          applyModeToToggles('chat');
        }
      }
    });
  }

  // ── Overflow Group Chat toggle ──
  const overflowGroupBtn = el('overflow-group-btn');
  if (overflowGroupBtn) {
    overflowGroupBtn.addEventListener('click', async () => {
      const chk = el('group-toggle');
      const turningOn = chk ? !chk.checked : false;
      if (turningOn) {
        const picked = await groupModule.showModelPicker();
        if (!picked || picked.length < 2) return;
        groupModule.setActive(true);  // Set early so updateModelPicker sees it
        _syncGroupIndicator(true);
        _startFreshChat();
        // Clear any leftover splash screens
        const _chatBox = document.getElementById('chat-history');
        if (_chatBox) {
          _chatBox.querySelectorAll('.tool-splash').forEach(s => s.remove());
          // Also hide welcome screen
          if (chatModule && chatModule.hideWelcomeScreen) chatModule.hideWelcomeScreen();
        }
        // Start group — create participant sessions immediately
        const sid = sessionModule.getCurrentSessionId() || 'group-' + Date.now();
        await groupModule.startGroup(picked, sid);
        // Re-hide picker after everything settles
        const _mpw = el('model-picker-wrap');
        if (_mpw) _mpw.style.display = 'none';
        uiModule.showToast(`Group chat ready — ${picked.length} models`);
      } else {
        _syncGroupIndicator(false);
        groupModule.stopGroup();
        // Restore model picker
        const _mpWrap2 = el('model-picker-wrap');
        if (_mpWrap2) _mpWrap2.style.display = '';
      }
    });
  }

  // ── Group toggle button (chatbox indicator) — click to deactivate ──
  const groupToggleBtn = el('group-toggle-btn');
  if (groupToggleBtn) {
    groupToggleBtn.addEventListener('click', () => {
      _syncGroupIndicator(false);
      groupModule.stopGroup();
    });
  }

  // ── Incognito mode toggle (on welcome screen) ──
  const incognitoBtn = el('incognito-btn');
  const INCOGNITO_EYE_OPEN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const INCOGNITO_EYE_CLOSED = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
  const SESSION_ICON_CHAT = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const SESSION_ICON_INCOGNITO = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function _syncSessionIncognitoIcon(active) {
    const activeSession = document.querySelector('.list-item.active-session .session-icon');
    if (activeSession) {
      activeSession.innerHTML = active ? SESSION_ICON_INCOGNITO : SESSION_ICON_CHAT;
      activeSession.style.color = active ? 'var(--accent)' : '';
    }
  }

  if (incognitoBtn) {
    incognitoBtn.addEventListener('mousedown', (e) => e.preventDefault());
    incognitoBtn.addEventListener('click', () => {
      // Don't toggle mid-chat — incognito only changeable from welcome screen
      const ws = el('welcome-screen');
      if (ws && ws.classList.contains('hidden')) return;
      const chk = el('incognito-toggle');
      chk.checked = !chk.checked;
      incognitoBtn.classList.toggle('active', chk.checked);
      const tipEl = el('welcome-tip');
      incognitoBtn.title = chk.checked ? 'Disable Nobody mode' : 'Enable Nobody mode — no memory, no history saved';
      const welcomeName = document.querySelector('.welcome-name');
      if (chk.checked) {
        incognitoBtn.innerHTML = INCOGNITO_EYE_CLOSED + '<span class="incognito-label">Nobody</span>';
        if (welcomeName) {
          welcomeName.dataset.originalHtml = welcomeName.innerHTML;
          welcomeName.innerHTML = '<svg class="welcome-boat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>Nobody';
          // Restart the L→R clip-wipe reveal on the new label
          welcomeName.style.animation = 'none';
          welcomeName.offsetHeight;
          welcomeName.style.animation = '';
        }
        if (ws) { ws.style.animation = 'none'; ws.offsetHeight; ws.style.animation = 'welcome-enter 0.3s ease-out both'; }
        const welcomeSub = el('welcome-sub');
        if (welcomeSub) {
          if (!welcomeSub.dataset.originalText) welcomeSub.dataset.originalText = welcomeSub.textContent;
          welcomeSub.textContent = "Who am I? I'm nobody.";
          welcomeSub.style.display = '';
        }
        if (tipEl) { tipEl.dataset.originalTip = tipEl.textContent; tipEl.textContent = 'Temporary session \u2014 won\u2019t be saved and no memory activation.'; tipEl.style.opacity = '0.5'; tipEl.style.marginTop = '8px'; }
        // Default to plain chat: disable tools visually, switch to chat mode.
        // IMPORTANT: don't overwrite the user's persisted per-mode tool prefs
        // (`web_agent`, `bash_agent`, `web_chat`, `bash_chat`). Nobody mode is
        // ephemeral — their agent-mode defaults must come back on toggle-off.
        const _offIds = ['web-toggle', 'bash-toggle', 'research-toggle'];
        _offIds.forEach(id => { const c = el(id); if (c) c.checked = false; });
        ['web-toggle-btn', 'bash-toggle-btn'].forEach(id => { const b = el(id); if (b) b.classList.remove('active'); });
        const _ab = el('mode-agent-btn'), _cb = el('mode-chat-btn');
        if (_ab) _ab.classList.remove('active');
        if (_cb) _cb.classList.add('active');
        const ts = Storage.getJSON(Storage.KEYS.TOGGLES, {});
        ts.research = false; ts.mode = 'chat';
        Storage.setJSON(Storage.KEYS.TOGGLES, ts);
      } else {
        incognitoBtn.innerHTML = INCOGNITO_EYE_OPEN + '<span class="incognito-label">Nobody</span>';
        if (welcomeName && welcomeName.dataset.originalHtml) {
          welcomeName.innerHTML = welcomeName.dataset.originalHtml;
          // Restart the L→R clip-wipe reveal on the restored label
          welcomeName.style.animation = 'none';
          welcomeName.offsetHeight;
          welcomeName.style.animation = '';
        }
        if (ws) { ws.style.animation = 'none'; ws.offsetHeight; ws.style.animation = 'welcome-enter 0.3s ease-out both'; }
        const welcomeSub2 = el('welcome-sub');
        if (welcomeSub2) {
          if (welcomeSub2.dataset.originalText) {
            welcomeSub2.textContent = welcomeSub2.dataset.originalText;
            delete welcomeSub2.dataset.originalText;
          }
          welcomeSub2.style.display = '';
        }
        if (tipEl && tipEl.dataset.originalTip) { tipEl.textContent = tipEl.dataset.originalTip; tipEl.style.opacity = ''; tipEl.style.marginTop = ''; }
        // Heal any previously-persisted false values from the old Nobody bug
        // so agent-mode defaults (web/bash ON) come back.
        const _ts = Storage.getJSON(Storage.KEYS.TOGGLES, {});
        let _dirty = false;
        ['web_agent', 'bash_agent', 'web_chat', 'bash_chat'].forEach(k => {
          if (_ts[k] === false) { delete _ts[k]; _dirty = true; }
        });
        if (_dirty) Storage.setJSON(Storage.KEYS.TOGGLES, _ts);
        // Reapply the current mode's real defaults to the visible toggles
        const _curMode = (Storage.getJSON(Storage.KEYS.TOGGLES, {}) || {}).mode || 'chat';
        try { applyModeToToggles(_curMode); } catch (_) {}
      }
      // If toggled off mid-chat (welcome screen hidden), hide the button
      if (!chk.checked && ws && ws.classList.contains('hidden')) {
        incognitoBtn.style.display = 'none';
      }
      // Show/hide persistent incognito indicator in top bar
      const _incInd = el('incognito-indicator');
      if (_incInd) _incInd.style.display = chk.checked ? '' : 'none';
      // Update active session icon in sidebar
      _syncSessionIncognitoIcon(chk.checked);
    });
  }

  // Incognito indicator click — deactivate incognito
  const incognitoIndicator = el('incognito-indicator');
  if (incognitoIndicator) {
    incognitoIndicator.addEventListener('click', () => {
      if (incognitoBtn) incognitoBtn.click();
      else {
        const chk = el('incognito-toggle');
        if (chk) { chk.checked = false; }
        incognitoIndicator.style.display = 'none';
      }
    });
  }

  // ── Deactivate incognito mode (called on new session) ──
  function _deactivateIncognito() {
    const chk = el('incognito-toggle');
    if (!chk || !chk.checked) return;
    if (incognitoBtn) incognitoBtn.click();
  }

  // ── UI Visibility (Customize UI modal) ──
  const UI_VIS_KEY = 'odysseus-ui-visibility';

  // Selector map: key → CSS selector(s) for targets
  const UI_VIS_MAP = {
    'sidebar-brand':       '.sidebar-brand-title',
    'sidebar-new-chat':    '#sidebar-new-chat-btn',
    'sidebar-search':      '#sidebar-search-btn',
    'sessions-section':    '#sessions-section',
    'email-section':       '#email-section',
    'models-section':      '#models-section',
    'tools-section':       '#tools-section',
    // Per-tool visibility — fine-grained control over which entries show
    // inside the Tools section in the sidebar.
    'tool-calendar':       '#tool-calendar-btn',
    'tool-compare':        '#tool-compare-btn',
    'tool-cookbook':       '#tool-cookbook-btn',
    'tool-research':       '#tool-research-btn',
    'tool-gallery':        '#tool-gallery-btn',
    'tool-library':        '#tool-library-btn',
    'tool-memory':         '#tool-memory-btn',
    'tool-notes':          '#tool-notes-btn',
    'tool-tasks':          '#tool-tasks-btn',
    'tool-theme':          '#tool-theme-btn',
    'user-bar':            '#user-bar-profile',
    'sidebar-settings-btn':'#user-bar-settings',
    'chat-meta':           '.chat-meta-overlay',
    'welcome-text':        '.welcome-name, .welcome-sub, #welcome-tip',
    'incognito-btn':       '.incognito-btn',
    'web-toggle-btn':      '#web-toggle-btn',
    'doc-toggle-btn':      '#overflow-doc-btn',
    'rag-toggle-btn':      '#overflow-rag-btn',
    'bash-toggle-btn':     '#bash-toggle-btn',
    'overflow-plus-btn':   '.overflow-wrapper',
    'mode-toggle':         '.mode-toggle',
    'preset-mini-btn':     '#overflow-preset-btn',
    'attach-btn':          '#overflow-attach-btn',
    'research-btn':        '#overflow-research-btn',
    'rail-new-chat':       '#rail-new-session',
  };

  // Keys hidden by default on first run (no localStorage yet)
  const UI_VIS_DEFAULT_OFF = new Set(['models-section', 'rag-toggle-btn', 'text-emojis']);

  // Keys that need admin to toggle off (reserved for future use)
  const UI_VIS_ADMIN_ONLY = new Set([]);

  function loadUIVis() {
    return Storage.getJSON(UI_VIS_KEY, {});
  }

  function saveUIVis(state) {
    Storage.setJSON(UI_VIS_KEY, state);
  }

  function applyUIVis(state) {
    Object.entries(UI_VIS_MAP).forEach(([key, selector]) => {
      // section-drag-reorder uses a body class instead of inline styles
      if (key === 'section-drag-reorder') return;
      const visible = key in state ? state[key] !== false : !UI_VIS_DEFAULT_OFF.has(key);
      document.querySelectorAll(selector).forEach(el => {
        el.style.display = visible ? '' : 'none';
      });
    });
    // Drag reorder: use body class so dynamically created handles are covered
    const dragEnabled = state['section-drag-reorder'] === true;
    document.body.classList.toggle('rearrange-mode', dragEnabled);
    document.querySelectorAll('.section[draggable]').forEach(el => {
      el.setAttribute('draggable', dragEnabled ? 'true' : 'false');
    });
    // Text-only emojis toggle. Default is OFF so model-emitted shortcodes
    // like `:blush:` render through the normal monochrome emoji path.
    applyTextEmojis(state['text-emojis'] === true);
    // Hide thinking sections toggle (show-thinking: checked=show, unchecked=hide)
    document.body.classList.toggle('hide-thinking', state['show-thinking'] === false);
  }

  // Rearrange toggles in session/model sort dropdowns
  function syncRearrangeChecks() {
    const on = loadUIVis()['section-drag-reorder'] === true;
    document.querySelectorAll('.rearrange-toggle .rearrange-check').forEach(ch => {
      ch.style.opacity = on ? '1' : '0';
    });
  }
  document.querySelectorAll('.rearrange-toggle').forEach(toggle => {
    toggle.addEventListener('click', () => {
      const state = loadUIVis();
      const wasOn = state['section-drag-reorder'] === true;
      state['section-drag-reorder'] = !wasOn;
      saveUIVis(state);
      applyUIVis(state);
      syncRearrangeChecks();
      uiModule.showToast(!wasOn ? 'Rearrange enabled' : 'Rearrange disabled');
      // Close the dropdown the toggle lives in — the sort dropdown's own
      // click-stopPropagation means it won't close on its own.
      const dd = toggle.closest('[id$="-sort-dropdown"]');
      if (dd) dd.style.display = 'none';
    });
  });

  // Esc exits rearrange mode (no matter where focus/mouse is) — matches the
  // global Esc-cancels-select pattern. Capture phase so a sort dropdown that
  // happens to be open doesn't swallow it first.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!document.body.classList.contains('rearrange-mode')) return;
    e.preventDefault();
    e.stopPropagation();
    const state = loadUIVis();
    state['section-drag-reorder'] = false;
    saveUIVis(state);
    applyUIVis(state);
    syncRearrangeChecks();
    uiModule.showToast('Rearrange disabled');
  }, true);
  // Sync checkmarks when dropdowns open
  const _sessionSortBtn = el('session-sort-btn');
  const _modelSortBtn = el('model-sort-btn');
  if (_sessionSortBtn) _sessionSortBtn.addEventListener('click', syncRearrangeChecks);
  if (_modelSortBtn) _modelSortBtn.addEventListener('click', syncRearrangeChecks);
  syncRearrangeChecks();

  // ── Text-only emoji conversion ──
  // Regex matching most emoji codepoints (Emoji_Presentation + common sequences)
  const EMOJI_RE = /(?:\p{Emoji_Presentation}|\p{Extended_Pictographic})(?:\uFE0F|\u200D(?:\p{Emoji_Presentation}|\p{Extended_Pictographic}))*/gu;

  // Common emoji → text description map
  const EMOJI_MAP = {
    '😀':'grinning','😃':'smiley','😄':'smile','😁':'grin','😆':'laughing','😅':'sweat smile',
    '🤣':'rofl','😂':'joy','🙂':'slightly smiling','🙃':'upside down','😉':'wink',
    '😊':'blush','😇':'innocent','🥰':'smiling hearts','😍':'heart eyes','🤩':'star struck',
    '😘':'kissing heart','😗':'kissing','😚':'kissing closed eyes','😙':'kissing smiling eyes',
    '🥲':'smiling tear','😋':'yum','😛':'tongue','😜':'winking tongue','🤪':'zany',
    '😝':'squinting tongue','🤑':'money mouth','🤗':'hugging','🤭':'hand over mouth',
    '🤫':'shushing','🤔':'thinking','🫡':'saluting','🤐':'zipper mouth','🤨':'raised eyebrow',
    '😐':'neutral','😑':'expressionless','😶':'no mouth','🫥':'dotted line face',
    '😏':'smirk','😒':'unamused','🙄':'eye roll','😬':'grimacing','🤥':'lying',
    '😌':'relieved','😔':'pensive','😪':'sleepy','🤤':'drooling','😴':'sleeping',
    '😷':'mask','🤒':'thermometer','🤕':'head bandage','🤢':'nauseated','🤮':'vomiting',
    '🥵':'hot','🥶':'cold','🥴':'woozy','😵':'dizzy','🤯':'exploding head',
    '🤠':'cowboy','🥳':'party','🥸':'disguised','😎':'sunglasses','🤓':'nerd',
    '🧐':'monocle','😕':'confused','🫤':'diagonal mouth','😟':'worried','🙁':'slightly frowning',
    '😮':'open mouth','😯':'hushed','😲':'astonished','😳':'flushed','🥺':'pleading',
    '🥹':'holding back tears','😦':'frowning open mouth','😧':'anguished','😨':'fearful',
    '😰':'anxious sweat','😥':'sad relieved','😢':'crying','😭':'sobbing','😱':'screaming',
    '😖':'confounded','😣':'persevering','😞':'disappointed','😓':'downcast sweat',
    '😩':'weary','😫':'tired','🥱':'yawning','😤':'triumph','😡':'pouting',
    '😠':'angry','🤬':'swearing','😈':'smiling devil','👿':'angry devil',
    '💀':'skull','☠️':'skull crossbones','💩':'poop','🤡':'clown','👹':'ogre','👺':'goblin',
    '👻':'ghost','👽':'alien','👾':'space invader','🤖':'robot',
    '😺':'smiling cat','😸':'grinning cat','😹':'tears of joy cat','😻':'heart eyes cat',
    '😼':'wry cat','😽':'kissing cat','🙀':'weary cat','😿':'crying cat','😾':'pouting cat',
    '🙈':'see no evil','🙉':'hear no evil','🙊':'speak no evil',
    '👋':'wave','🤚':'raised back of hand','🖐️':'hand with fingers splayed','✋':'raised hand',
    '🖖':'vulcan salute','🫱':'rightward hand','🫲':'leftward hand',
    '👌':'ok hand','🤌':'pinched fingers','🤏':'pinching hand','✌️':'victory',
    '🤞':'crossed fingers','🫰':'hand with index finger and thumb crossed',
    '🤟':'love you','🤘':'rock on','🤙':'call me','👈':'point left','👉':'point right',
    '👆':'point up','🖕':'middle finger','👇':'point down','☝️':'index up',
    '🫵':'point at viewer','👍':'thumbs up','👎':'thumbs down','✊':'raised fist',
    '👊':'fist bump','🤛':'left fist','🤜':'right fist','👏':'clap','🙌':'raising hands',
    '🫶':'heart hands','👐':'open hands','🤲':'palms up','🤝':'handshake','🙏':'pray',
    '✍️':'writing','💅':'nail polish','🤳':'selfie','💪':'flexed biceps',
    '❤️':'red heart','🧡':'orange heart','💛':'yellow heart','💚':'green heart',
    '💙':'blue heart','💜':'purple heart','🖤':'black heart','🤍':'white heart',
    '🩷':'pink heart','🩵':'light blue heart','🩶':'grey heart','🤎':'brown heart',
    '💔':'broken heart','❤️‍🔥':'heart on fire','❤️‍🩹':'mending heart',
    '💕':'two hearts','💞':'revolving hearts','💓':'heartbeat','💗':'growing heart',
    '💖':'sparkling heart','💘':'heart with arrow','💝':'heart with ribbon',
    '💟':'heart decoration','🔥':'fire','💯':'100','✨':'sparkles','⭐':'star',
    '🌟':'glowing star','💫':'dizzy star','🎉':'party popper','🎊':'confetti ball',
    '🎈':'balloon','🎁':'gift','🏆':'trophy','🥇':'1st place','🥈':'2nd place','🥉':'3rd place',
    '⚡':'zap','💡':'light bulb','🔑':'key','🔒':'locked','🔓':'unlocked',
    '🔔':'bell','🔕':'bell off','📢':'loudspeaker','📣':'megaphone',
    '💬':'speech bubble','💭':'thought bubble','🗯️':'anger bubble',
    '✅':'check mark','❌':'cross mark','❓':'question','❗':'exclamation',
    '⚠️':'warning','🚫':'prohibited','⛔':'no entry','🔴':'red circle','🟢':'green circle',
    '🔵':'blue circle','🟡':'yellow circle','⚪':'white circle','⚫':'black circle',
    '🟠':'orange circle','🟣':'purple circle','🟤':'brown circle',
    '📁':'folder','📂':'open folder','📄':'document','📝':'memo','📎':'paperclip',
    '📌':'pin','📍':'round pin','🔗':'link','📊':'bar chart','📈':'chart up','📉':'chart down',
    '🔍':'magnifying glass left','🔎':'magnifying glass right',
    '🌐':'globe','🌍':'globe europe','🌎':'globe americas','🌏':'globe asia',
    '🕐':'clock 1','🕑':'clock 2','🕒':'clock 3','🕓':'clock 4',
    '⏰':'alarm clock','⏳':'hourglass flowing','⌛':'hourglass done',
    '🚀':'rocket','✈️':'airplane','🚗':'car','🚂':'train','🚢':'ship',
    '🏠':'house','🏢':'building','🏗️':'construction','🏭':'factory',
    '🎵':'musical note','🎶':'musical notes','🎤':'microphone','🎧':'headphones',
    '📷':'camera','📸':'camera flash','🎬':'clapperboard','📺':'television',
    '💻':'laptop','🖥️':'desktop','📱':'mobile phone','☎️':'telephone',
    '🔧':'wrench','🔨':'hammer','⚙️':'gear','🧲':'magnet','🧪':'test tube','🔬':'microscope',
    '📚':'books','📖':'open book','✏️':'pencil','🖊️':'pen','🖋️':'fountain pen',
    '🎯':'bullseye','♟️':'chess pawn','🎲':'game die','🧩':'puzzle piece',
    '🍕':'pizza','🍔':'burger','🍟':'fries','🌮':'taco','🍣':'sushi','🍩':'donut',
    '☕':'coffee','🍺':'beer','🍷':'wine','🥤':'cup with straw',
    '🐶':'dog','🐱':'cat','🐭':'mouse','🐹':'hamster','🐰':'rabbit','🦊':'fox',
    '🐻':'bear','🐼':'panda','🐨':'koala','🐯':'tiger','🦁':'lion','🐮':'cow',
    '🐷':'pig','🐸':'frog','🐵':'monkey','🐔':'chicken','🐧':'penguin','🐦':'bird',
    '🦅':'eagle','🦆':'duck','🦉':'owl','🐺':'wolf','🐗':'boar','🐴':'horse',
    '🦄':'unicorn','🐝':'bee','🐛':'bug','🦋':'butterfly','🐌':'snail','🐞':'ladybug',
    '🐍':'snake','🐢':'turtle','🐙':'octopus','🦀':'crab','🐠':'tropical fish',
    '🐳':'whale','🐋':'whale','🦈':'shark','🐊':'crocodile','🦕':'sauropod','🦖':'t-rex',
    '🌸':'cherry blossom','🌹':'rose','🌻':'sunflower','🌺':'hibiscus','🌷':'tulip',
    '🌱':'seedling','🌲':'evergreen tree','🌳':'deciduous tree','🍀':'four leaf clover',
    '🍎':'red apple','🍐':'pear','🍊':'tangerine','🍋':'lemon','🍌':'banana',
    '🍉':'watermelon','🍇':'grapes','🍓':'strawberry','🫐':'blueberries','🍑':'peach',
    '🌈':'rainbow','☀️':'sun','🌤️':'sun behind cloud','⛅':'sun behind cloud','☁️':'cloud',
    '🌧️':'rain','⛈️':'thunder','❄️':'snowflake','🌊':'wave',
    '👀':'eyes','👁️':'eye','👂':'ear','👃':'nose','👄':'mouth','👅':'tongue',
    '🧠':'brain','🦴':'bone','🦷':'tooth','👶':'baby','🧒':'child','👦':'boy','👧':'girl',
    '🧑':'person','👨':'man','👩':'woman','🧓':'older person',
    '👮':'police officer','🧑‍💻':'technologist','👨‍💻':'man technologist',
    '👩‍💻':'woman technologist',
    '🎓':'graduation cap','🧢':'billed cap','👑':'crown','💎':'gem','👓':'glasses','🕶️':'sunglasses',
    '🩸':'drop of blood','💊':'pill','🩹':'bandage','🧬':'dna','🦠':'microbe',
    '☢️':'radioactive','☣️':'biohazard','♻️':'recycling',
    '🏳️':'white flag','🏴':'black flag','🚩':'red flag','🏁':'checkered flag',
    '➡️':'right arrow','⬅️':'left arrow','⬆️':'up arrow','⬇️':'down arrow',
    '↗️':'upper right arrow','↘️':'lower right arrow','↙️':'lower left arrow','↖️':'upper left arrow',
    '↩️':'left curve','↪️':'right curve','🔄':'counterclockwise','🔃':'clockwise',
    '➕':'plus','➖':'minus','➗':'division','✖️':'multiply','♾️':'infinity',
    '‼️':'double exclamation','⁉️':'exclamation question',
    '©️':'copyright','®️':'registered','™️':'trademark',
  };

  function emojiToText(str) {
    return str.replace(EMOJI_RE, (match) => {
      const desc = EMOJI_MAP[match];
      if (desc) return ':' + desc + ':';
      // Fallback: use the emoji's Unicode name if available, or skip
      return ':emoji:';
    });
  }

  const _DEOJ_SKIP = '.sources-section, .thinking-toggle, .memory-used-pill';

  /** Walk all text nodes inside an element and replace emojis with text descriptions */
  function deEmojify(root) {
    if (!root || !root.querySelectorAll) return;
    // Monochrome SVG spans from svgifyEmoji — Unicode lives in aria-label only
    root.querySelectorAll('.emoji[aria-label]').forEach((span) => {
      if (span.closest(_DEOJ_SKIP)) return;
      const label = span.getAttribute('aria-label') || '';
      span.replaceWith(document.createTextNode(emojiToText(label)));
    });
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      // Skip UI elements that use unicode symbols as functional icons
      if (node.parentElement && node.parentElement.closest(_DEOJ_SKIP)) continue;
      if (EMOJI_RE.test(node.textContent)) {
        EMOJI_RE.lastIndex = 0; // reset regex state
        node.textContent = emojiToText(node.textContent);
      }
    }
  }

  /** Apply or remove text-emoji mode on all chat messages */
  function applyTextEmojis(enabled) {
    document.body.classList.toggle('text-emojis', enabled);
    if (enabled) {
      document.querySelectorAll('.msg .body').forEach(deEmojify);
    }
  }

  // Observe chat history for new/changed messages — de-emojify on the fly
  let _deEmojifyTimer = null;
  const _chatObs = new MutationObserver(() => {
    if (!document.body.classList.contains('text-emojis')) return;
    clearTimeout(_deEmojifyTimer);
    _deEmojifyTimer = setTimeout(() => {
      document.querySelectorAll('.msg .body').forEach(deEmojify);
    }, 150);
  });
  const _chatBox = document.getElementById('chat-history');
  if (_chatBox) _chatObs.observe(_chatBox, { childList: true, subtree: true });

  // Migrate old toolbar visibility key if present
  (function migrateOldToolbarVis() {
    const OLD_KEY = 'odysseus-toolbar-visibility';
    try {
      const old = Storage.getJSON(OLD_KEY, null);
      if (old && typeof old === 'object') {
        const current = loadUIVis();
        let migrated = false;
        Object.entries(old).forEach(([btnId, val]) => {
          if (current[btnId] === undefined) {
            current[btnId] = val;
            migrated = true;
          }
        });
        if (migrated) saveUIVis(current);
        Storage.remove(OLD_KEY);
      }
    } catch {}
  })();

  // Expose UI visibility functions for admin.js
  window.loadUIVis = loadUIVis;
  window.saveUIVis = saveUIVis;
  window.applyUIVis = applyUIVis;
  window.UI_VIS_ADMIN_ONLY = UI_VIS_ADMIN_ONLY;
  window.UI_VIS_DEFAULT_OFF = UI_VIS_DEFAULT_OFF;

  (function initUIVisibility() {
    // Apply saved visibility on load
    applyUIVis(loadUIVis());

    // The only two modals without a per-module makeWindowDraggable call. Wire
    // them onto the shared helper, drag-only, to match their old behavior.
    try {
      ['custom-preset-modal', 'rename-session-modal'].forEach((id) => {
        const m = document.getElementById(id);
        if (!m) return;
        const content = m.querySelector('.modal-content');
        const header = m.querySelector('.modal-header');
        if (!content || !header) return;
        makeWindowDraggable(m, {
          content, header,
          skipSelector: '.close-btn',
          enableDock: false,
          enableResize: false,
        });
        // Re-center on open (these persist in the DOM). Guard on the
        // hidden→visible edge so it never fires mid-drag.
        let wasHidden = m.classList.contains('hidden');
        new MutationObserver(() => {
          const isHidden = m.classList.contains('hidden');
          if (wasHidden && !isHidden) {
            content.style.position = '';
            content.style.left = '';
            content.style.top = '';
            content.style.right = '';
            content.style.bottom = '';
            content.style.margin = '';
          }
          wasHidden = isHidden;
        }).observe(m, { attributes: true, attributeFilter: ['class'] });
      });
    } catch (e) { console.error('Dialog drag init error:', e); }
  })();

  // ── Modal minimize → dock ──
  // Adds a "_" button next to every modal's close button. Clicking it hides
  // the modal and adds an entry to a fixed bottom dock; clicking the dock
  // entry restores the modal. Works for hand-rolled and dynamically-created
  // modals via a MutationObserver on document.body.
  (function initModalMinimize() {
    // custom-preset-modal (the Prompt window) is handled by the new
    // modalManager dock (registered in _AUTO_WIRE), so the legacy dock must
    // not also inject a `_`/chip for it.
    const SKIP_IDS = new Set(['styled-confirm-overlay', 'custom-preset-modal']);
    const dockEntries = new Map(); // modal element -> dock entry element

    let dock = document.getElementById('modal-dock');
    if (!dock) {
      dock = document.createElement('div');
      dock.id = 'modal-dock';
      document.body.appendChild(dock);
    }

    // Keep the dock clear of the sidebar (which can be collapsed, resized,
    // hidden, or flipped to the right side).
    function updateDockOffset() {
      const sidebar = document.getElementById('sidebar');
      const iconRail = document.getElementById('icon-rail');
      let leftPx = 0;
      let rightPx = 0;
      const sidebarRight = sidebar && sidebar.classList.contains('right-side');
      const sidebarVisible = sidebar &&
        !sidebar.classList.contains('hidden') &&
        sidebar.offsetWidth > 0;
      const railVisible = iconRail && iconRail.offsetWidth > 0;
      const sidebarW = sidebarVisible ? sidebar.offsetWidth : 0;
      const railW = railVisible ? iconRail.offsetWidth : 0;
      if (sidebarRight) {
        rightPx = sidebarW + railW;
      } else {
        leftPx = sidebarW + railW;
      }
      dock.style.left = leftPx + 'px';
      dock.style.right = rightPx + 'px';
    }
    updateDockOffset();
    // Recompute when sidebar resizes, collapses, or moves sides
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(updateDockOffset);
      const sb = document.getElementById('sidebar');
      const ir = document.getElementById('icon-rail');
      if (sb) ro.observe(sb);
      if (ir) ro.observe(ir);
    }
    window.addEventListener('resize', updateDockOffset);
    // Side-flip / collapse toggles class names on body or sidebar
    new MutationObserver(updateDockOffset).observe(document.body, {
      attributes: true, attributeFilter: ['class'],
    });
    const sbEl = document.getElementById('sidebar');
    if (sbEl) {
      new MutationObserver(updateDockOffset).observe(sbEl, {
        attributes: true, attributeFilter: ['class', 'style'],
      });
    }

    function modalTitle(modal) {
      const h = modal.querySelector('.modal-header h4, .modal-header h3, .modal-header h2');
      if (h && h.textContent.trim()) return h.textContent.trim();
      if (modal.id) return modal.id.replace(/-modal$|-overlay$|-popup$/, '').replace(/-/g, ' ');
      return 'Window';
    }

    function removeDockEntry(modal) {
      const entry = dockEntries.get(modal);
      if (entry) {
        entry.remove();
        dockEntries.delete(modal);
      }
    }

    function restoreModal(modal) {
      modal.classList.remove('minimized');
      modal.classList.remove('hidden');
      removeDockEntry(modal);
      // Bring to front (matches existing focus-on-click behavior)
      modal.style.zIndex = '';
    }

    function minimizeModal(modal) {
      if (modal.classList.contains('hidden')) return;
      modal.classList.add('minimized');
      if (dockEntries.has(modal)) return;

      const entry = document.createElement('div');
      entry.className = 'modal-dock-item';
      entry.title = `Restore ${modalTitle(modal)}`;

      const label = document.createElement('span');
      label.className = 'modal-dock-label';
      label.textContent = modalTitle(modal);

      const closeX = document.createElement('button');
      closeX.className = 'modal-dock-close';
      closeX.textContent = '×';
      closeX.title = 'Close';
      closeX.addEventListener('click', (e) => {
        e.stopPropagation();
        modal.classList.remove('minimized');
        modal.classList.add('hidden');
        modal.style.display = '';
        removeDockEntry(modal);
      });

      entry.appendChild(label);
      entry.appendChild(closeX);
      entry.addEventListener('click', () => restoreModal(modal));
      dock.appendChild(entry);
      dockEntries.set(modal, entry);
    }

    function injectMinimizeButton(modal) {
      if (!modal || !modal.classList || !modal.classList.contains('modal')) return;
      if (modal.id && SKIP_IDS.has(modal.id)) return;
      // Modals managed by the new modalManager (Modals.register) get their own
      // .modal-minimize-btn and chips via the .minimized-dock-chip system.
      // Skip them entirely so we don't double-up minimize buttons or chips.
      if (modal.id && /^email-reader-/.test(modal.id)) return;
      if (modal.id && window.Modals && window.Modals.isRegistered && window.Modals.isRegistered(modal.id)) return;
      const header = modal.querySelector('.modal-header');
      if (!header) return;
      if (header.querySelector('.minimize-btn, .modal-minimize-btn')) return;
      const closeBtn = header.querySelector('.close-btn, .modal-close');
      if (!closeBtn) return;

      const minBtn = document.createElement('button');
      minBtn.className = 'minimize-btn';
      minBtn.type = 'button';
      minBtn.title = 'Minimize';
      minBtn.textContent = '_';
      minBtn.addEventListener('mousedown', (e) => e.stopPropagation()); // don't start drag
      minBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        minimizeModal(modal);
      });
      closeBtn.parentElement.insertBefore(minBtn, closeBtn);

      // Watch this modal's class so close-from-elsewhere clears the dock entry
      new MutationObserver(() => {
        if (modal.classList.contains('hidden') && !modal.classList.contains('minimized')) {
          removeDockEntry(modal);
        }
      }).observe(modal, { attributes: true, attributeFilter: ['class'] });
    }

    // Initial pass over existing modals
    document.querySelectorAll('.modal').forEach(injectMinimizeButton);

    // Watch for dynamically-created modals
    new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          if (n.classList && n.classList.contains('modal')) {
            injectMinimizeButton(n);
          }
          if (n.querySelectorAll) {
            n.querySelectorAll('.modal').forEach(injectMinimizeButton);
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  })();

  // Preset button (in overflow menu)
  const overflowPresetBtn = el('overflow-preset-btn');
  if (overflowPresetBtn) {
    overflowPresetBtn.addEventListener('click', () => {
      if (presetsModule && presetsModule.openCustomPresetModal) {
        presetsModule.openCustomPresetModal();
      }
    });
  }

  // RAG directory
  const addDirBtn = el('add-directory-btn');
  if (addDirBtn) {
    addDirBtn.addEventListener('click', () => {
      ragModule.addRagDirectory(uiModule.showToast, uiModule.showError);
    });
  }
  
  const directoryInput = el('rag-directory');
  if (directoryInput) {
    directoryInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        ragModule.addRagDirectory(uiModule.showToast, uiModule.showError);
      }
    });

  }

  // Sidebar layout (extracted to js/sidebar-layout.js)
  initSidebarLayout(Storage, {
    documentModule, _closeCompareIfActive, _deactivateIncognito,
    presetsModule, sessionModule, el, _defaultChat, _syncResearchIndicator
  });

  // Mobile: horizontal swipe on a tabbed window switches tabs. Works for any
  // tab bar whose buttons are siblings and switch on click (Prompt, Library,
  // Brain, Theme) — we just click the prev/next tab so the existing switch
  // logic runs. Swipes that start on interactive controls (sliders, inputs,
  // the chip dock) are ignored so they don't fight text selection / dragging.
  (function initTabSwipe() {
    if (window.innerWidth > 768) return;
    // [tab-bar selector, tab-button selector] for each tabbed window.
    const SYSTEMS = [
      ['.preset-tabs', '.preset-tab'],
      ['.lib-tabs', '.lib-tab'],
      ['.memory-tabs', '.memory-tab'],
      ['.admin-tabs', '.admin-tab'],
    ];
    const _IGNORE = 'input, textarea, select, [contenteditable="true"], .preset-range, ' +
      '.note-cl-row, .minimized-dock-chip, canvas, .email-card-reader';
    let sx = 0, sy = 0, tracking = false;

    document.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 768 || e.touches.length !== 1) { tracking = false; return; }
      if (e.target.closest && e.target.closest(_IGNORE)) { tracking = false; return; }
      sx = e.touches[0].clientX; sy = e.touches[0].clientY; tracking = true;
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const t = e.changedTouches[0];
      if (!t) return;
      const dx = t.clientX - sx, dy = t.clientY - sy;
      // Require a deliberate, mostly-horizontal swipe.
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      for (const [barSel, tabSel] of SYSTEMS) {
        const bar = document.querySelector(barSel);
        if (!bar || bar.offsetParent === null) continue;  // not the visible window
        // Only act if the swipe happened inside this bar's window (not some
        // other on-screen element).
        const host = bar.closest('.modal, #notes-pane, .preset-modal-content, .admin-card') || bar.parentElement;
        const startEl = document.elementFromPoint(sx, sy);
        if (host && startEl && !host.contains(startEl)) continue;
        const tabs = [...bar.querySelectorAll(tabSel)];
        if (tabs.length < 2) continue;
        let idx = tabs.findIndex(tb => tb.classList.contains('active'));
        if (idx < 0) idx = 0;
        // Swipe left (dx<0) → next tab; swipe right (dx>0) → previous.
        const nextIdx = dx < 0 ? idx + 1 : idx - 1;
        if (nextIdx < 0 || nextIdx >= tabs.length) return;  // at an edge
        tabs[nextIdx].click();
        return;
      }
    }, { passive: true });
  })();

  // Elastic overscroll (rubber-band bounce) — desktop wheel only, on chat-history not container
  (function initElasticScroll() {
    const hist = el('chat-history');
    if (!hist) return;
    const SNAP_BACK = 'transform 0.25s cubic-bezier(0.25, 0.46, 0.45, 0.94)';

    let wheelPull = 0;
    let wheelTimer = null;
    hist.addEventListener('wheel', (e) => {
      const atTop = hist.scrollTop <= 0 && e.deltaY < 0;
      const atBottom = hist.scrollTop + hist.clientHeight >= hist.scrollHeight - 1 && e.deltaY > 0;
      if (!atTop && !atBottom) { wheelPull = 0; return; }

      wheelPull += e.deltaY * -0.03;
      wheelPull = Math.max(-7, Math.min(7, wheelPull));
      hist.style.transition = 'none';
      hist.style.transform = `translateY(${wheelPull}px)`;

      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => {
        wheelPull = 0;
        hist.style.transition = SNAP_BACK;
        hist.style.transform = '';
      }, 120);
    }, { passive: true });
  })();

  // New session button on icon rail
  const railNewSession = el('rail-new-session');
  if (railNewSession) {
    railNewSession.addEventListener('click', async () => {
      if (!sessionModule) return;
      if (_closeCompareIfActive()) return;
      _deactivateIncognito();
      // Clear character on new chat
      if (presetsModule && presetsModule.deactivateCharacter) presetsModule.deactivateCharacter();
      // Clear research mode if active
      const _resChk = el('research-toggle');
      if (_resChk && _resChk.checked) _syncResearchIndicator(false);
      if (await _createDirectChatFromPreferredModel()) return;
      // No models at all — show welcome screen
      sessionModule.setCurrentSessionId(null);
      if (documentModule && documentModule.isPanelOpen && documentModule.isPanelOpen()) documentModule.closePanel();
      const docBtn3 = el('overflow-doc-btn');
      if (docBtn3) docBtn3.classList.remove('active', 'has-docs');
      const box = el('chat-history');
      if (box) box.innerHTML = '';
      if (chatModule && chatModule.showWelcomeScreen) {
        chatModule.showWelcomeScreen();
      }
      document.querySelectorAll('.session-item.active').forEach(s => s.classList.remove('active'));
    });
  }

  // Mobile new chat button — always go to blank welcome screen.
  // The send path at chat.js:354 will auto-create a session using /api/default-chat
  // on first submit, so users can start typing before models finish loading and
  // the default model attaches when they hit send.
  const mobileNewChat = el('mobile-new-chat-btn');
  if (mobileNewChat) {
    mobileNewChat.addEventListener('click', () => {
      if (!sessionModule) return;
      if (_closeCompareIfActive()) return;
      _deactivateIncognito();
      _startFreshChat();
      document.querySelectorAll('.session-item.active').forEach(s => s.classList.remove('active'));
      // Focus the composer synchronously so mobile keyboards pop open.
      // iOS Safari only honours programmatic focus inside the original click
      // callback — a setTimeout breaks the user-gesture chain.
      const _input = el('message-input');
      if (_input) { try { _input.focus(); } catch (_) {} }
    });
  }

  // Logo click → new chat (same logic as rail new-session button)
  const brandBtn = el('sidebar-brand-btn');
  if (brandBtn) {
    brandBtn.addEventListener('click', async () => {
      if (!sessionModule) return;
      if (_closeCompareIfActive()) return;
      _deactivateIncognito();
      if (presetsModule && presetsModule.deactivateCharacter) presetsModule.deactivateCharacter();
      // Clear research toggle when starting a fresh chat (not via research button)
      _syncResearchIndicator(false);
      if (await _createDirectChatFromPreferredModel()) return;
      // No models at all — show welcome screen
      sessionModule.setCurrentSessionId(null);
      if (documentModule && documentModule.isPanelOpen && documentModule.isPanelOpen()) documentModule.closePanel();
      const docBtn2 = el('overflow-doc-btn');
      if (docBtn2) docBtn2.classList.remove('active', 'has-docs');
      const box = el('chat-history');
      if (box) box.innerHTML = '';
      if (chatModule && chatModule.showWelcomeScreen) chatModule.showWelcomeScreen();
      document.querySelectorAll('.session-item.active').forEach(s => s.classList.remove('active'));
    });
  }

  const sidebarNewChatBtn = el('sidebar-new-chat-btn');
  if (sidebarNewChatBtn) {
    sidebarNewChatBtn.addEventListener('click', () => {
      const brandBtn = el('sidebar-brand-btn');
      if (brandBtn) brandBtn.click();
    });
  }

  // Delete session button on icon rail
  const railDelete = el('rail-delete-session');
  if (railDelete) {
    railDelete.addEventListener('click', async () => {
      if (!sessionModule) return;
      const currentId = sessionModule.getCurrentSessionId();
      if (!currentId) return;
      const sessions = sessionModule.getSessions();
      const current = sessions.find(s => s.id === currentId);
      const name = current ? current.name : 'this session';
      if (!await uiModule.styledConfirm(`Delete "${name}"?`, { confirmText: 'Delete', danger: true })) return;
      try {
        // Find the next session below the current one before deleting
        const idx = sessions.findIndex(s => s.id === currentId);
        const nextSession = sessions.filter(s => !s.archived && s.id !== currentId)[Math.max(0, idx)] ||
                            sessions.find(s => !s.archived && s.id !== currentId);
        const res = await fetch(`${API_BASE}/api/session/${currentId}`, { method: 'DELETE' });
        if (res.ok) {
          await sessionModule.loadSessions();
          if (nextSession) {
            await sessionModule.selectSession(nextSession.id);
          }
          uiModule.showToast('Session deleted');
        } else {
          uiModule.showError('Failed to delete session');
        }
      } catch (e) {
        uiModule.showError('Failed to delete session: ' + e);
      }
    });
  }

  // Textarea auto-resize
  const textarea = el('message');
  if (textarea) {
    uiModule.autoResize(textarea);
    textarea.addEventListener('input', () => {
      uiModule.autoResize(textarea);
    });
    textarea.addEventListener('paste', () => {
      setTimeout(() => uiModule.autoResize(textarea), 1);
    });
    textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        // If ghost autocomplete is active, accept the suggestion instead of submitting
        if (window._ghostAutocomplete && window._ghostAutocomplete.isActive()) {
          e.preventDefault();
          e.stopPropagation();
          window._ghostAutocomplete.accept();
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        // Check if already submitting before triggering form submission
        const form = el('chat-form');
        if (form) {
         const submitBtn = form.querySelector('button[type="submit"]');
         if (submitBtn) submitBtn.click();
        }
      }
    });
  }

  // ── Ghost text autocomplete for /new and /create commands ──
  (function initGhostAutocomplete() {
    const textarea = el('message');
    const ghost = document.getElementById('message-ghost');
    if (!textarea || !ghost) return;

    let modelCache = null;     // { models: [{ mid, url, endpointId, displayName }], ts }
    let filtered = [];         // currently matching models
    let cycleIdx = 0;          // index into filtered[]
    let active = false;        // is ghost visible?
    const CACHE_TTL = 60000;   // re-fetch after 60s
    const CMD_RE = /^\/(new|create)\s/i;

    async function fetchModels() {
      if (modelCache && Date.now() - modelCache.ts < CACHE_TTL) return modelCache.models;
      try {
        const res = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin' });
        const data = await res.json();
        const models = [];
        (data.items || []).forEach(ep => {
          const displayNames = ep.models_display || ep.models || [];
          (ep.models || []).forEach((mid, i) => {
            models.push({
              mid,
              url: ep.url,
              endpointId: ep.endpoint_id || null,
              displayName: displayNames[i] || mid,
            });
          });
        });
        modelCache = { models, ts: Date.now() };
        return models;
      } catch (e) {
        console.warn('Ghost autocomplete: failed to fetch models', e);
        return modelCache ? modelCache.models : [];
      }
    }

    function hide() {
      active = false;
      filtered = [];
      cycleIdx = 0;
      ghost.textContent = '';
      ghost.style.display = 'none';
    }

    function show(typed, suggestion) {
      active = true;
      ghost.innerHTML = '';
      // Invisible portion matches what user typed (keeps alignment)
      const span1 = document.createElement('span');
      span1.style.visibility = 'hidden';
      span1.textContent = typed;
      // Visible faded suggestion portion
      const span2 = document.createElement('span');
      span2.className = 'ghost-suggestion';
      span2.textContent = suggestion;
      ghost.appendChild(span1);
      ghost.appendChild(span2);
      ghost.style.display = 'block';
    }

    function syncSize() {
      // Match ghost overlay dimensions to textarea
      const cs = getComputedStyle(textarea);
      ghost.style.width = cs.width;
      ghost.style.height = cs.height;
    }

    async function update() {
      const val = textarea.value;
      const match = val.match(CMD_RE);
      if (!match) { hide(); return; }

      const prefix = val.slice(match[0].length); // text after "/new " or "/create "
      const models = await fetchModels();
      if (!models.length) { hide(); return; }

      // Filter models whose mid or displayName starts with the typed prefix (case-insensitive)
      const lp = prefix.toLowerCase();
      filtered = models.filter(m =>
        m.mid.toLowerCase().startsWith(lp) || m.displayName.toLowerCase().startsWith(lp)
      );

      if (!filtered.length) { hide(); return; }

      // Clamp cycle index
      cycleIdx = cycleIdx % filtered.length;
      const chosen = filtered[cycleIdx];
      // Determine which name matched for completion
      const name = chosen.mid.toLowerCase().startsWith(lp) ? chosen.mid : chosen.displayName;
      const remainder = name.slice(prefix.length);
      if (!remainder && filtered.length <= 1) { hide(); return; }

      syncSize();
      show(val, remainder);
    }

    // --- Event listeners ---

    textarea.addEventListener('input', () => {
      cycleIdx = 0;
      update();
    });

    textarea.addEventListener('keydown', (e) => {
      if (!active) return;

      if (e.key === 'Tab') {
        // Tab fills the current suggestion into the textarea
        e.preventDefault();
        e.stopPropagation();
        const val = textarea.value;
        const match = val.match(CMD_RE);
        if (match && filtered.length) {
          const prefix = val.slice(match[0].length);
          const chosen = filtered[cycleIdx % filtered.length];
          const lp = prefix.toLowerCase();
          const name = chosen.mid.toLowerCase().startsWith(lp) ? chosen.mid : chosen.displayName;
          textarea.value = match[0] + name;
          textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        e.stopPropagation();
        cycleIdx = (cycleIdx + 1) % filtered.length;
        update();
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        cycleIdx = (cycleIdx - 1 + filtered.length) % filtered.length;
        update();
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        hide();
        return;
      }
    });

    textarea.addEventListener('blur', hide);

    // Observe textarea resize (from autoResize) to keep ghost in sync
    const ro = new ResizeObserver(() => { if (active) syncSize(); });
    ro.observe(textarea);

    // Public API for the Enter handler above
    window._ghostAutocomplete = {
      isActive() { return active && filtered.length > 0; },
      accept() {
        if (!active || !filtered.length) return;
        const val = textarea.value;
        const match = val.match(CMD_RE);
        if (!match) { hide(); return; }
        const prefix = val.slice(match[0].length);
        const chosen = filtered[cycleIdx % filtered.length];
        const lp = prefix.toLowerCase();
        const name = chosen.mid.toLowerCase().startsWith(lp) ? chosen.mid : chosen.displayName;
        textarea.value = match[0] + name;
        hide();
        // Trigger input event so autoResize fires
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        // Now submit the form (the /new command handler will process it)
        setTimeout(() => {
          const form = el('chat-form');
          if (form) form.querySelector('button[type="submit"]').click();
        }, 0);
      }
    };
  })();

  // Keyboard shortcuts (extracted to js/keyboard-shortcuts.js)
  initKeyboardShortcuts({
    el, Storage, sessionModule, uiModule, chatModule,
    adminModule, settingsModule, searchChatModule,
    _closeCompareIfActive, _deactivateIncognito, API_BASE
  });
  
}

// ============================================
// INITIALIZATION ON PAGE LOAD
// ============================================
function startOdysseusApp() {
  if (window.__odysseusAppStarted) return;
  window.__odysseusAppStarted = true;
  // Set CSS variables
  document.documentElement.style.setProperty('--line-height', '20px');

  // Smooth keyboard open/close on mobile — keep chat scrolled to bottom
  if (window.visualViewport && 'ontouchstart' in window) {
    let _prevVPH = visualViewport.height;
    visualViewport.addEventListener('resize', () => {
      const delta = visualViewport.height - _prevVPH;
      _prevVPH = visualViewport.height;
      // Keyboard opened (viewport shrank significantly)
      if (delta < -50) {
        const hist = document.getElementById('chat-history');
        if (hist) {
          hist.style.scrollBehavior = 'smooth';
          hist.scrollTop = hist.scrollHeight;
          // Reset after animation
          setTimeout(() => { hist.style.scrollBehavior = ''; }, 300);
        }
      }
    });
  }

  // Initialize all event listeners
  try { initializeEventListeners(); } catch(e) { console.error('Event init error:', e); }

  // Reveal the toolbar now that all toggle/overflow state is resolved
  // (hidden via inline style="visibility:hidden" in HTML to prevent FOUC)
  const _inputBottom = document.querySelector('.chat-input-bottom');
  if (_inputBottom) _inputBottom.style.visibility = '';

  fileHandlerModule.init(API_BASE);
  modelsModule.init(API_BASE);
  ragModule.init(API_BASE);
  presetsModule.init(API_BASE);
  searchModule.init(API_BASE);
  chatModule.init(API_BASE);
  chatModule.initListeners();
  groupModule.init(API_BASE);
  // Initialize compare module
  if (compareModule) {
    compareModule.init(API_BASE);
  }
  researchPanelModule.init(API_BASE, markdownModule, sessionModule);
  // Initialize document editor module
  if (documentModule) {
    documentModule.init(API_BASE);
    // Restore document panel if it was open before refresh
    const _curSession = sessionModule && sessionModule.getCurrentSessionId();
    if (_curSession && localStorage.getItem('odysseus-doc-open-' + _curSession) === '1') {
      documentModule.loadSessionDocs(_curSession);
    }
  }  
  // Initialize search chat module
  if (searchChatModule) {
    searchChatModule.init(API_BASE);
  }

  // Search buttons — icon rail + sidebar
  const railSearchBtn = el('rail-search-btn');
  if (railSearchBtn) {
    railSearchBtn.addEventListener('click', () => {
      if (searchChatModule) searchChatModule.openSearch();
    });
  }

  // Rail tool buttons — delegate to sidebar tool buttons
  const _railToolMap = {
    'rail-compare':   'tool-compare-btn',
    'rail-research':  'tool-research-btn',
    'rail-cookbook':   'tool-cookbook-btn',
    'rail-archive':   'tool-library-btn',
    'rail-gallery':   'tool-gallery-btn',
    'rail-tasks':     'tool-tasks-btn',
    'rail-calendar':  'tool-calendar-btn',
    'rail-notes':     'tool-notes-btn',
    'rail-memory':    'tool-memory-btn',
    'rail-theme':     'tool-theme-btn',
    'rail-email':     'email-section-title',
  };
  Object.entries(_railToolMap).forEach(([railId, toolId]) => {
    const railBtn = el(railId);
    if (railBtn) {
      railBtn.addEventListener('click', () => {
        const toolBtn = el(toolId);
        if (toolBtn) toolBtn.click();
      });
    }
  });

  // Rail chats — click to open the completed background session
  const _railChatsBtn = el('rail-chats');
  if (_railChatsBtn) {
    _railChatsBtn.addEventListener('click', () => {
      const targetSid = _railChatsBtn.dataset.targetSession;
      if (targetSid && window.sessionModule) {
        window.sessionModule.selectSession(targetSid);
      }
      // Clear notification — session will call clearStreamComplete on load
      _railChatsBtn.classList.remove('rail-notify', 'rail-notify-success');
      delete _railChatsBtn.dataset.targetSession;
      _syncRailDynamic();
    });
  }

  // Rail documents — toggle doc panel on/off (not library)
  const _railDocsBtn = el('rail-documents');
  if (_railDocsBtn) {
    _railDocsBtn.addEventListener('click', () => {
      const ob = el('overflow-doc-btn');
      if (ob) ob.click();
    });
  }

  // Rail: settings button
  const _railSettings = el('rail-settings');
  if (_railSettings) {
    _railSettings.addEventListener('click', () => {
      const sidebar = document.getElementById('sidebar');
      if (sidebar) sidebar.classList.remove('hidden');
      syncRailSide();
      // Scroll to bottom where settings typically are
      const sidebarInner = document.querySelector('.sidebar-inner');
      if (sidebarInner) sidebarInner.scrollTo({ top: sidebarInner.scrollHeight, behavior: 'smooth' });
    });
  }

  // Rail: admin button
  const _railAdmin = el('rail-admin');
  if (_railAdmin) {
    _railAdmin.addEventListener('click', () => {
      // Try to open admin modal
      const adminBtn = document.querySelector('[data-modal="admin-modal"]') || el('tool-admin-btn');
      if (adminBtn) adminBtn.click();
    });
  }

  // Sync the contextual rail icons. Tool launchers (calendar/compare/cookbook/
  // research/gallery/tasks/archive/memory/notes/theme/email) are now
  // always-visible launchers, so only the doc + background-chat indicators
  // are shown/hidden dynamically here.
  function _syncRailDynamic() {
    // Show doc icon if panel is open OR session has documents
    const docPanelOpen = window.documentModule && window.documentModule.isPanelOpen();
    const docIndicator = el('doc-indicator-btn');
    const hasDocs = docIndicator && docIndicator.classList.contains('visible');
    const docOpen = docPanelOpen || hasDocs;
    const hasChatNotif = el('rail-chats')?.classList.contains('rail-notify');

    const _show = (id, visible) => { const b = el(id); if (b) b.style.display = visible ? '' : 'none'; };
    _show('rail-documents', docOpen);
    _show('rail-chats', !!hasChatNotif);
  }
  window._syncRailDynamic = _syncRailDynamic;
  // Sync periodically and on key events
  setInterval(_syncRailDynamic, 1000);
  document.addEventListener('overflow-state-change', _syncRailDynamic);

  const sidebarSearchBtn = el('sidebar-search-btn');
  if (sidebarSearchBtn) {
    sidebarSearchBtn.addEventListener('click', () => {
      if (searchChatModule) searchChatModule.openSearch();
    });
  }
  // Modify form submit to handle special modes
  const chatForm = document.getElementById('chat-form');
  const originalSubmit = chatModule.handleChatSubmit;
  let _submitting = false;

  function handleSubmit(e) {
    if (e) e.preventDefault();
    // Debounce: prevent double-submit while a request is being initiated
    if (_submitting) return;
    _submitting = true;
    // Release after a short delay (stream start sets its own isStreaming guard)
    setTimeout(() => { _submitting = false; }, 300);

    // Compare mode: route submit to compare handler (same message to all panes)
    if (compareModule && compareModule.isActive()) {
      return compareModule.handleCompareSubmit(e);
    }

    // Group chat: route to group module
    if (groupModule && groupModule.isActive()) {
      console.log('[group] Submit intercepted');
      const msgInput = document.getElementById('message');
      const msg = msgInput ? msgInput.value.trim() : '';
      if (!msg) { console.log('[group] Empty message, skipping'); return; }
      console.log('[group] Sending:', msg);
      chatRenderer.hideWelcomeScreen();
      chatRenderer.addMessage('user', msg);
      msgInput.value = '';
      groupModule.sendMessage(msg);
      return;
    }

    return originalSubmit.call(chatModule, e);
  }

  chatForm.onsubmit = handleSubmit;

  // ── Dual-purpose send/mic button ──
  const sendBtn = document.querySelector('.send-btn');
  const messageInput = el('message');
  const modelPickerWrap = document.getElementById('model-picker-wrap');

  const _sendIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  const _micIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
  const _stopIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  const _newChatIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

  // Expose icons globally so chat.js updateSubmitButton can use them
  window._odysseusBtnIcons = { send: _sendIcon, mic: _micIcon, stop: _stopIcon, newChat: _newChatIcon };

  function _isSttEnabled() {
    return voiceRecorderModule._sttProvider && voiceRecorderModule._sttProvider !== 'disabled';
  }

  function _hasAttachments() {
    return fileHandlerModule.getPendingCount && fileHandlerModule.getPendingCount() > 0;
  }

  function _updateSendBtnIcon() {
    if (!sendBtn) return;
    // Don't override if streaming (stop button) or recording
    if (sendBtn.dataset.mode === 'streaming' || sendBtn.dataset.mode === 'recording') return;
    const prevMode = sendBtn.dataset.mode || '';
    const hasText = messageInput && messageInput.value.trim().length > 0;
    const hasFiles = _hasAttachments();
    let newMode;
    if (!hasText && !hasFiles && _isSttEnabled()) {
      clearTimeout(sendBtn._collapseTimer);
      sendBtn.innerHTML = _micIcon;
      sendBtn.title = 'Record voice';
      newMode = 'mic';
      sendBtn.classList.add('mic-mode');
      sendBtn.classList.remove('newchat-mode', 'newchat-expanded');
    } else if (!hasText && !hasFiles && !_isSttEnabled()) {
      clearTimeout(sendBtn._collapseTimer);
      // Group chat: always show send button, never newchat mode
      if (groupModule && groupModule.isActive()) {
        sendBtn.innerHTML = _sendIcon;
        sendBtn.title = 'Send to group';
        newMode = 'idle';
        sendBtn.classList.remove('mic-mode', 'newchat-mode', 'newchat-expanded');
      } else {
      // Check if we're already on a fresh empty session (welcome screen visible)
      const isEmptySession = document.getElementById('chat-container')?.classList.contains('welcome-active');
      if (isEmptySession) {
        // Already on new chat — show arrow in muted style (ready to type)
        sendBtn.innerHTML = _sendIcon;
        sendBtn.title = 'Send message';
        newMode = 'idle';
        sendBtn.classList.add('newchat-mode'); // muted gray style
        sendBtn.classList.remove('mic-mode', 'newchat-expanded');
        clearTimeout(sendBtn._expandTimer);
      } else {
        sendBtn.innerHTML = _newChatIcon + '<span class="send-btn-label">+ New</span>';
        sendBtn.title = 'New chat';
        newMode = 'newchat';
        sendBtn.classList.add('newchat-mode');
        sendBtn.classList.remove('mic-mode');
        // The button stays a 32px compact icon (no auto-expand to label —
        // the "+ New" label inside is for screen readers only; sighted users
        // see the spinning + on hover + the title tooltip).
        clearTimeout(sendBtn._expandTimer);
        sendBtn.classList.remove('newchat-expanded');
      }
      } // close group-else
    } else {
      newMode = 'send';
      clearTimeout(sendBtn._expandTimer);
      const wasExpanded = sendBtn.classList.contains('newchat-expanded');
      const wasNewchat = prevMode === 'newchat' || prevMode === 'mic';
      if (wasExpanded || wasNewchat) {
        // Collapse pill if expanded, then spin arrow in (same as + spin-in)
        if (wasExpanded) sendBtn.classList.remove('newchat-expanded');
        const delay = wasExpanded ? 300 : 0;
        setTimeout(() => {
          if (sendBtn.dataset.mode !== 'send') return;
          sendBtn.innerHTML = _sendIcon;
          sendBtn.title = 'Send message';
          sendBtn.classList.remove('mic-mode', 'newchat-mode', 'anim-spin-swap');
          sendBtn.classList.add('anim-spin');
          sendBtn.addEventListener('animationend', () => sendBtn.classList.remove('anim-spin'), { once: true });
        }, delay);
      } else {
        sendBtn.innerHTML = _sendIcon;
        sendBtn.title = 'Send message';
        sendBtn.classList.remove('mic-mode', 'newchat-mode', 'newchat-expanded', 'anim-spin', 'anim-launch', 'anim-land');
      }
    }
    // Animate icon spin — when switching TO newchat or mic (the + or mic
    // appearing). The previous `prevMode && ...` guard skipped this after
    // streaming ended (dataset.mode is reset to '' there, an empty falsy
    // string), which let the lingering anim-land class from the stop icon's
    // entry replay on the +, making it look like the + comes from below.
    // Never animate into send mode (arrow) — it should just appear instantly.
    if (newMode !== prevMode && (newMode === 'newchat' || newMode === 'mic')) {
      if (!sendBtn.classList.contains('anim-spin')) {
        sendBtn.classList.remove('anim-launch', 'anim-land');
        sendBtn.classList.add('anim-spin');
        sendBtn.addEventListener('animationend', () => sendBtn.classList.remove('anim-spin'), { once: true });
      }
    }
    sendBtn.dataset.mode = newMode;
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', (e) => {
      e.preventDefault();

      // If recording, stop recording
      if (sendBtn.dataset.mode === 'recording' || voiceRecorderModule.getIsRecording()) {
        voiceRecorderModule.stopRecording();
        return;
      }

      const hasText = messageInput && messageInput.value.trim().length > 0;
      const hasFiles = _hasAttachments();

      // New chat mode — empty input, no attachments, no STT
      if (!hasText && !hasFiles && sendBtn.dataset.mode === 'newchat') {
        if (sessionModule) {
          const sessions = sessionModule.getSessions();
          const currentId = sessionModule.getCurrentSessionId();
          const current = sessions.find(s => s.id === currentId);
          if (current && current.endpoint_url && current.model) {
            sessionModule.createDirectChat(current.endpoint_url, current.model, current.endpoint_id);
          } else {
            // Fallback to rail button
            const railNew = el('rail-new-session');
            if (railNew) railNew.click();
          }
        }
        return;
      }

      // If input is empty and STT is enabled, start recording
      if (!hasText && !hasFiles && _isSttEnabled()) {
        sendBtn.innerHTML = _stopIcon;
        sendBtn.title = 'Stop recording';
        sendBtn.dataset.mode = 'recording';
        sendBtn.classList.add('recording');
        voiceRecorderModule.startRecording(
          (audioFile) => fileHandlerModule.addFiles([audioFile]),
          uiModule.showToast,
          uiModule.showError
        );
        return;
      }

      // Otherwise, send message
      handleSubmit(e);
    });
  }

  // Enter to send (shift+enter for newline), or new chat when empty
  if (messageInput) {
    messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        // Flush the debounced icon update so dataset.mode reflects the current
        // text state. Without this, a fast type-and-Enter would still see the
        // stale 'newchat' mode and open a new chat instead of sending.
        try { _updateSendBtnIcon(); } catch {}
        if (sendBtn && sendBtn.dataset.mode === 'newchat') {
          const railNew = el('rail-new-session');
          if (railNew) railNew.click();
          return;
        }
        handleSubmit(e);
      }
    });
  }

  // Toggle mic/send icon on input change + hide model picker after enough text
  if (messageInput) {
    const _debouncedUpdateIcon = uiModule.debounce(_updateSendBtnIcon, 50);
    const _MODEL_PICKER_HIDE_CHARS = 10;
    const _syncModelPickerAutohide = () => {
      const hidePicker = (messageInput.value || '').replace(/\s/g, '').length >= _MODEL_PICKER_HIDE_CHARS;
      if (modelPickerWrap) {
        modelPickerWrap.classList.toggle('model-picker-autohide', hidePicker);
      }
    };
    window._syncModelPickerAutohide = _syncModelPickerAutohide;
    _syncModelPickerAutohide();
    messageInput.addEventListener('input', () => {
      _syncModelPickerAutohide();
      _debouncedUpdateIcon();
    }, { passive: true });
  }

  // Collapse "New Session" label on scroll
  const _chatScroll = document.getElementById('chat-container');
  if (_chatScroll && sendBtn) {
    _chatScroll.addEventListener('scroll', () => {
      if (sendBtn.classList.contains('newchat-expanded')) {
        sendBtn.classList.remove('newchat-expanded');
      }
    }, { passive: true });
  }

  // Expose globally so voiceRecorder can trigger update after async fetch
  window._updateSendBtnIcon = _updateSendBtnIcon;

  // Initial icon state
  _updateSendBtnIcon();

  // Auto-focus input on load
  if (messageInput) {
    setTimeout(() => messageInput.focus(), 100);
  }

  // Add drag and drop handlers for the chat container
  const chatContainer = el('chat-container');

  // Prevent default to allow drop
  const chatInputBar = chatContainer.querySelector('.chat-input-bar');
  function _showDropHighlight() {
    chatContainer.style.backgroundColor = 'rgba(0, 170, 255, 0.1)';
    chatContainer.style.transition = 'background-color 0.2s ease';
    if (chatInputBar) {
      chatInputBar.style.outline = '2px dashed color-mix(in srgb, var(--accent, #0af) 50%, transparent)';
      chatInputBar.style.outlineOffset = '-2px';
      chatInputBar.style.background = 'color-mix(in srgb, var(--accent, #0af) 8%, var(--bg))';
      chatInputBar.style.transition = 'outline 0.2s ease, background 0.2s ease';
    }
  }
  function _hideDropHighlight() {
    chatContainer.style.backgroundColor = '';
    if (chatInputBar) {
      chatInputBar.style.outline = '';
      chatInputBar.style.outlineOffset = '';
      chatInputBar.style.background = '';
    }
  }

  chatContainer.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
    _showDropHighlight();
  });

  chatContainer.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    _hideDropHighlight();
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;
    fileHandlerModule.addFiles(files);
    fileHandlerModule.renderAttachStrip();
    uiModule.showToast(`Added ${files.length} file${files.length > 1 ? 's' : ''} to chat`);
  });

  chatContainer.addEventListener('dragleave', (e) => {
    e.preventDefault();
    _hideDropHighlight();
  });
  
  // Make the attachment strip also a drop target
  const attachStrip = el('attach-strip');
  attachStrip.addEventListener('dragover', (e) => {
    e.preventDefault();
    attachStrip.style.backgroundColor = 'rgba(0, 170, 255, 0.1)';
    attachStrip.style.borderRadius = '4px';
  });
  
  attachStrip.addEventListener('drop', (e) => {
    e.preventDefault();
    attachStrip.style.backgroundColor = '';
    
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;

    uiModule.showToast(`Added ${files.length} file${files.length > 1 ? 's' : ''} to chat`);

  });
  
  attachStrip.addEventListener('dragleave', (e) => {
    e.preventDefault();
    attachStrip.style.backgroundColor = '';
  });

  // ── Compare-mode file drop shield ──────────────────────────────────────────
  // Compare reuses #chat-container, but each pane renders into a sandboxed
  // <iframe>. Iframes swallow drag-and-drop events: a file dropped on a pane is
  // handled by the iframe, not the parent, so the browser loads the file *inside
  // the pane* ("behind" the app) instead of attaching it. The chatContainer drop
  // handler above never sees it because the event doesn't bubble out of the frame.
  //
  // Fix: while a file drag is active in Compare, raise a single full-window shield
  // that sits above every pane/iframe and becomes the drop target. The drop then
  // lands on the parent document and we route the files into the shared composer
  // (the same pending-files pipeline the picker and paste use). Scoped to Compare
  // via the .compare-active class, so normal chat and the tool dropzones (gallery,
  // RAG, document editor, …) are unaffected.
  let _cmpDropShield = null;
  const _isFileDrag = (e) => {
    const types = e.dataTransfer && e.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, 'Files') !== -1;
  };
  const _compareActive = () => {
    const c = el('chat-container');
    return !!c && c.classList.contains('compare-active');
  };
  const _showCmpShield = () => {
    if (!_cmpDropShield) {
      _cmpDropShield = document.createElement('div');
      _cmpDropShield.id = 'compare-drop-shield';
      _cmpDropShield.setAttribute('aria-hidden', 'true');
      _cmpDropShield.style.cssText = 'position:fixed;inset:0;z-index:2147483646;' +
        'display:none;align-items:center;justify-content:center;' +
        'background:color-mix(in srgb, var(--accent, #0af) 16%, rgba(0,0,0,0.5));' +
        'backdrop-filter:blur(2px);';
      const _box = document.createElement('div');
      _box.style.cssText = 'pointer-events:none;border:2px dashed rgba(255,255,255,0.9);' +
        'border-radius:14px;padding:20px 28px;background:rgba(0,0,0,0.4);' +
        'font:600 16px/1.4 system-ui,sans-serif;color:#fff;';
      _box.textContent = 'Drop files to attach';
      _cmpDropShield.appendChild(_box);
      document.body.appendChild(_cmpDropShield);
    }
    _cmpDropShield.style.display = 'flex';
  };
  const _hideCmpShield = () => { if (_cmpDropShield) _cmpDropShield.style.display = 'none'; };
  // Capture phase so we raise the shield before the pointer reaches an iframe.
  window.addEventListener('dragenter', (e) => {
    if (_isFileDrag(e) && _compareActive()) _showCmpShield();
  }, true);
  window.addEventListener('dragover', (e) => {
    if (!_isFileDrag(e) || !_compareActive()) return;
    e.preventDefault();                       // mark as a valid drop target
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    _showCmpShield();
  }, true);
  window.addEventListener('dragleave', (e) => {
    // Hide only when the drag actually leaves the window (no relatedTarget).
    if (_compareActive() && !e.relatedTarget) _hideCmpShield();
  }, true);
  window.addEventListener('dragend', _hideCmpShield, true);
  window.addEventListener('drop', (e) => {
    if (!_isFileDrag(e) || !_compareActive()) return;
    e.preventDefault();
    _hideCmpShield();
    const files = Array.from(e.dataTransfer.files || []);
    if (!files.length) return;
    fileHandlerModule.addFiles(files);
    fileHandlerModule.renderAttachStrip();
    uiModule.showToast(`Added ${files.length} file${files.length > 1 ? 's' : ''} to attach`);
  }, true);

  // Load initial data
  presetsModule.loadPresets(uiModule.showError);

  if (sessionModule) {
    sessionModule.initDependencies({
      API_BASE: API_BASE,
      el: el,
      showToast: uiModule.showToast,
      showError: uiModule.showError,
      addMessage: chatModule.addMessage,
      renderContent: markdownModule.renderContent,
      scrollHistory: uiModule.scrollHistoryInstant
    });

    // Load sessions first (critical path) — remove loader when done
    sessionModule.loadSessions()
      .catch(e => console.warn('loadSessions error:', e))
      .finally(() => {
        const loader = document.getElementById('app-loader');
        if (loader) { loader.style.opacity = '0'; setTimeout(() => loader.remove(), 300); }
        // Fire any URL route opener now that sessions + module wiring are
        // ready. Deferred from up top of init for exactly this reason.
        if (window._odysseusRouteOpener) {
          try { window._odysseusRouteOpener(); } catch (_) {}
          window._odysseusRouteOpener = null;
        }
      });
  } else {
    console.error('Session module not loaded!');
  }

  // Non-critical: load in parallel, resolve silently
  modelsModule.refreshModels(true).then(() => {
    const modelsBox = document.getElementById('models');
    const hasModels = modelsBox && modelsBox.querySelector('.models-row');
    if (!hasModels) {
      const tip = document.getElementById('welcome-tip');
      if (tip) tip.textContent = 'Add an AI endpoint from Settings in the sidebar, or paste an endpoint/API key into the chat.';
    }
  }).catch(() => {});
  modelsModule.refreshProviders();
  ragModule.loadPersonalDocs();
  memoryModule.loadMemories(); // Ensure memories are loaded on page load
  
  // Ensure the memory list is rendered after loading
  setTimeout(async () => {
    await memoryModule.loadMemories();
  }, 1000);
  
  // Ensure proper initial state
  voiceRecorderModule.init();
  if (censorModule) censorModule.init();

  // Auto-focus message input on load
  const msgEl = document.getElementById('message');
  if (msgEl) msgEl.focus();
  
  // Initialize mouse-based drag for sidebar sections
  const sidebar = document.getElementById('sidebar');
  const sidebarInner = sidebar ? sidebar.querySelector('.sidebar-inner') : sidebar;

  // ── Subtle elastic overscroll for sidebar ──
  if (sidebarInner) {
    const MAX_PULL = 8;
    let _overscroll = 0;
    let _resetTimer = null;
    sidebarInner.addEventListener('wheel', (e) => {
      const el = sidebarInner;
      const atTop = el.scrollTop <= 0 && e.deltaY < 0;
      const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1 && e.deltaY > 0;
      if (!atTop && !atBottom) { _overscroll = 0; return; }
      // Accumulate overscroll with diminishing returns
      _overscroll += Math.abs(e.deltaY) * 0.15;
      const pull = Math.min(_overscroll, MAX_PULL);
      const dir = atTop ? 1 : -1;
      el.style.transition = 'none';
      el.style.transform = `translateY(${dir * pull}px)`;
      // Reset after scrolling stops
      clearTimeout(_resetTimer);
      _resetTimer = setTimeout(() => {
        el.style.transition = 'transform 0.3s cubic-bezier(0.25, 1, 0.5, 1)';
        el.style.transform = '';
        _overscroll = 0;
      }, 120);
    }, { passive: true });
  }

  // ── Global touch-scroll guard for sidebar ──
  // Suppress click events when the user was scrolling (finger moved).
  // This prevents accidental session/model/setting selection while swiping.
  if (sidebarInner && 'ontouchstart' in window) {
    let _sidebarTouchMoved = false;
    let _sidebarTouchStartY = 0;
    sidebarInner.addEventListener('touchstart', (e) => {
      _sidebarTouchMoved = false;
      _sidebarTouchStartY = e.touches[0].clientY;
    }, { passive: true });
    sidebarInner.addEventListener('touchmove', (e) => {
      // Only flag as scroll if finger moved more than 8px vertically
      if (Math.abs(e.touches[0].clientY - _sidebarTouchStartY) > 8) {
        _sidebarTouchMoved = true;
      }
    }, { passive: true });
    sidebarInner.addEventListener('click', (e) => {
      if (_sidebarTouchMoved) {
        e.stopPropagation();
        e.preventDefault();
        _sidebarTouchMoved = false;
      }
    }, true); // capture phase — intercepts before any child handlers
  }

  // Section collapse/expand + drag reorder (extracted to js/section-management.js)
  initSectionCollapse(Storage);
  initSectionDrag(Storage, loadUIVis);
  
  // Handle drag over and out for individual sections
  const sections = document.querySelectorAll('.section[draggable="true"]');
  sections.forEach(section => {
    section.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      
      // Only show visual feedback if we're not dragging over the active element
      const activeId = e.dataTransfer.getData('text/plain');
      if (activeId && activeId !== section.id) {
        section.setAttribute('dnd-over', 'true');
      }
    });
    
    section.addEventListener('dragleave', (e) => {
      // Check if we're actually leaving the element
      const rect = section.getBoundingClientRect();
      if (e.clientY < rect.top || e.clientY > rect.bottom || 
          e.clientX < rect.left || e.clientX > rect.right) {
        section.setAttribute('dnd-over', 'false');
      }
    });
  });
  
  // Restore saved order on load
  const savedOrder = Storage.get(Storage.KEYS.SECTION_ORDER);
  if (savedOrder) {
    try {
      const order = JSON.parse(savedOrder);
      const innerContainer = sidebarInner || document.getElementById('sidebar');

      // Create a document fragment to minimize reflows
      const fragment = document.createDocumentFragment();

      // First, collect all sections in the desired order
      for (const id of order) {
        const section = document.getElementById(id);
        if (section) {
          fragment.appendChild(section);
        }
      }

      // Append any remaining sections (in case new ones were added)
      sections.forEach(section => {
        if (!order.includes(section.id)) {
          fragment.appendChild(section);
        }
      });

      // Finally, add all sections back to the container
      innerContainer.appendChild(fragment);
    } catch (e) {
      console.error('Failed to restore sidebar order:', e);
    }
  }
  


  if (window.hljs) {
    console.log('Highlighting all code blocks on page load');
    document.querySelectorAll('pre code:not(.hljs)').forEach(block => {
      window.hljs.highlightElement(block);
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', startOdysseusApp, { once: true });
} else {
  startOdysseusApp();
}
