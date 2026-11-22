// frontend/src/features/documents/ui/history/versionHistory.js
/**
 * Document version history panel.
 */

import { API_BASE, activeDocId, docs, isOpen } from '../../application/documentState.js';
import { saveCurrentToMap, switchToDoc } from '../../application/documentCommands.js';
import { persistMarkdownDraft } from '../../application/documentSession.js';
import { _cancelAutoSave, _scheduleAutoSave, _setDocumentSaveState } from '../../application/persistence.js';
import { _buildDiffSummary, _escHtml } from '../../domain/documentKinds.js';
import { syncHighlighting } from '../editor/sourceEditor.js';
import { exitDiffMode, _diffModeActive } from '../diffViewer.js';
import { _releasePdfPane } from '../pdfTools.js';
import { uiModule } from '../host.js';
import { markdownToVisualHtml } from '../../domain/markdownVisualCodec.js';
import { assessMarkdownVisualSupport, isMarkdownVisualModeActive, isVisualMarkdownDoc, unbindVisualSurface } from '../richEditor/markdownVisualSync.js';

let _versionClickOutside = null;

export let _versionHistory = null;

let _versionHistoryGeneration = 0;

export function _historyOwnsView(state) {
    return state && _versionHistory === state
      && state.generation === _versionHistoryGeneration
      && state.docId === activeDocId && isOpen
      && state.pane === document.getElementById('doc-editor-pane');
  }

export async function toggleVersionHistory({ focus = false } = {}) {
    const panel = document.getElementById('doc-version-panel');
    if (!panel || !activeDocId) return;
    if (!panel.classList.contains('hidden')) {
      _closeVersionPanel();
      return;
    }
    if (_diffModeActive) exitDiffMode(true);
    saveCurrentToMap();
    const textarea = document.getElementById('doc-editor-textarea');
    const state = {
      docId: activeDocId, doc: docs.get(activeDocId),
      pane: document.getElementById('doc-editor-pane'),
      generation: ++_versionHistoryGeneration, operation: 0,
      previewing: false, readOnly: textarea?.readOnly || false,
    };
    _versionHistory = state;
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth > 768 && sidebar) {
      const atLeft = sidebar.classList.contains('right-side') || document.body.classList.contains('sidebar-collapsed');
      panel.style.left = atLeft ? '0' : sidebar.offsetWidth + 'px';
      panel.style.right = 'auto';
    } else {
      panel.style.left = '';
      panel.style.right = '';
      panel.style.top = '';
    }
    if (panel.parentElement !== document.body) document.body.appendChild(panel);
    panel.classList.remove('hidden');
    _versionClickOutside = event => {
      if (!panel.contains(event.target) && !event.target.closest('.doc-tab-version')) _closeVersionPanel();
    };
    document.addEventListener('click', _versionClickOutside, true);
    await loadVersionHistory(state);
    if (focus && _historyOwnsView(state)) (panel.querySelector('.doc-version-preview') || panel.querySelector('#doc-version-close'))?.focus({ preventScroll: true });
  }

export function _closeVersionPanel({ restoreFocus = false } = {}) {
    const state = _versionHistory;
    const ownsView = _historyOwnsView(state);
    _versionHistory = null;
    ++_versionHistoryGeneration;
    document.getElementById('doc-version-panel')?.classList.add('hidden');
    if (ownsView) {
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        if (state.previewing) textarea.value = state.doc.content || '';
        textarea.readOnly = state.readOnly;
      }
      if (state.previewing) {
        if (state.wasVisualMarkdown && isVisualMarkdownDoc(state.doc)) {
          const rich = document.getElementById('doc-email-richbody');
          if (rich) rich.contentEditable = 'true';
          switchToDoc(state.docId, { captureCurrent: false, preserveHistory: true });
        }
        syncHighlighting();
      }
    }
    if (_versionClickOutside) document.removeEventListener('click', _versionClickOutside, true);
    _versionClickOutside = null;
    if (restoreFocus && state) document.querySelector(`.doc-tab[data-doc-id="${CSS.escape(state.docId)}"] .doc-tab-version`)?.focus({ preventScroll: true });
  }

async function loadVersionHistory(state = _versionHistory) {
    if (!_historyOwnsView(state)) return;
    const list = document.getElementById('doc-version-list');
    if (!list) return;

    try {
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/versions`);
      if (!res.ok) throw new Error(`History failed: HTTP ${res.status}`);
      const versions = await res.json();
      if (!_historyOwnsView(state)) return;

      // Build diff summaries between consecutive versions
      const diffs = [];
      for (let i = 0; i < versions.length; i++) {
        if (i < versions.length - 1) {
          diffs.push(_buildDiffSummary(versions[i + 1].content, versions[i].content));
        } else {
          diffs.push('');
        }
      }

      list.innerHTML = versions.map((v, i) => `
        <div class="doc-version-item" data-version="${v.version_number}">
          <div class="doc-version-info">
            <button type="button" class="doc-version-preview" aria-label="Preview version ${v.version_number}" aria-pressed="false"><span class="doc-version-num">v${v.version_number}</span></button>
            ${i === 0 ? '<span class="doc-version-latest">latest</span>' : `<span class="doc-version-source">${_escHtml(v.source)}</span><span class="doc-version-time">${v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>`}
          </div>
          ${v.summary ? `<div class="doc-version-summary">${_escHtml(v.summary)}</div>` : ''}
          ${diffs[i] ? `<div class="doc-version-diff">${diffs[i]}</div>` : ''}
          ${i > 0 ? `<button type="button" class="doc-version-restore" data-version="${v.version_number}" aria-label="Restore version ${v.version_number}">Restore</button>` : ''}
        </div>
      `).join('');

      // Wire restore buttons
      list.querySelectorAll('.doc-version-restore').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          restoreVersion(parseInt(btn.dataset.version));
        });
      });

      // Wire click to preview version + active state
      list.querySelectorAll('.doc-version-item').forEach(item => {
        item.addEventListener('click', (e) => {
          if (e.target.classList.contains('doc-version-restore')) return;
          // Toggle active state
          list.querySelectorAll('.doc-version-item.active').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          list.querySelectorAll('.doc-version-preview').forEach(button => button.setAttribute('aria-pressed', button.closest('.doc-version-item') === item ? 'true' : 'false'));
          previewVersion(parseInt(item.dataset.version));
        });
      });
    } catch (e) {
      if (!_historyOwnsView(state)) return;
      list.innerHTML = '<div style="padding:8px;opacity:0.5;">Failed to load versions</div>';
    }
  }

async function previewVersion(num) {
    const state = _versionHistory;
    if (!_historyOwnsView(state) || state.restoring || state.doc._visualSyncBlocked) return;
    const operation = ++state.operation;
    const revision = state.doc._editRevision || 0;
    try {
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/version/${num}`);
      if (!res.ok) throw new Error(`Preview failed: HTTP ${res.status}`);
      const ver = await res.json();
      if (!_historyOwnsView(state) || state.operation !== operation
          || (state.doc._editRevision || 0) !== revision) return;
      const verContent = ver.content || '';
      const rich = document.getElementById('doc-email-richbody');
      const wasVisual = state.wasVisualMarkdown || isMarkdownVisualModeActive();
      if (wasVisual) state.wasVisualMarkdown = true;
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        state.previewing = true;
        textarea.readOnly = true;
        textarea.value = verContent;
      }
      if (state.wasVisualMarkdown && rich) {
        // The preview is read-only and detached from the document, so no sync
        // path can write the previewed version into the live document.
        unbindVisualSurface(rich);
        const wrap = document.getElementById('doc-editor-wrap');
        if (assessMarkdownVisualSupport(verContent).supported) {
          if (wrap) wrap.style.display = 'none';
          rich.style.display = '';
          rich.classList.add('markdown-visual-mode');
          rich.contentEditable = 'false';
          rich.innerHTML = markdownToVisualHtml(verContent);
        } else {
          rich.classList.remove('markdown-visual-mode');
          rich.style.display = 'none';
          if (wrap) wrap.style.display = '';
        }
      }
      syncHighlighting();
    } catch (e) {
      if (_historyOwnsView(state) && state.operation === operation) uiModule?.showError('Failed to preview version');
    }
  }

async function restoreVersion(num) {
    const state = _versionHistory;
    if (!_historyOwnsView(state) || state.restoring || state.doc._visualSyncBlocked) return;
    state.restoring = true;
    const operation = ++state.operation;
    const doc = state.doc;
    const revision = doc._editRevision || 0;
    const content = doc.content;
    _cancelAutoSave(state.docId);
    const previousSave = doc._saveQueueTail || Promise.resolve();
    let release;
    const turn = new Promise(resolve => { release = resolve; });
    doc._saveQueueTail = previousSave.catch(() => {}).then(() => turn);
    await previousSave.catch(() => {});
    try {
      if (!_historyOwnsView(state) || state.operation !== operation) return;
      const res = await fetch(`${API_BASE}/api/document/${state.docId}/restore/${num}`, { method: 'POST' });
      if (!res.ok) throw new Error(`Restore failed: HTTP ${res.status}`);
      const restored = await res.json();
      const unchanged = (doc._editRevision || 0) === revision && doc.content === content;
      doc.version = Math.max(doc.version || 1, restored.version_count || 1);
      doc._persistedContent = restored.current_content ?? restored.content ?? '';
      if (unchanged) {
        doc.content = doc._persistedContent;
        _setDocumentSaveState('saved', state.docId);
      } else {
        _setDocumentSaveState('dirty', state.docId);
        _scheduleAutoSave(state.docId, 0);
      }
      persistMarkdownDraft(doc);
      if (_historyOwnsView(state) && state.operation === operation) {
        state.previewing = false;
        const textarea = document.getElementById('doc-editor-textarea');
        if (textarea) textarea.readOnly = state.readOnly;
        if (unchanged) {
          if (doc.language === 'email') doc._skipLocalDraftOnce = true;
          _releasePdfPane(state.docId);
          switchToDoc(state.docId, { captureCurrent: false, preserveHistory: true });
        }
        await loadVersionHistory(state);
        uiModule?.showToast(`Restored to v${num}`);
      }
    } catch (e) {
      if (_historyOwnsView(state)) uiModule?.showError('Failed to restore version');
    } finally {
      state.restoring = false;
      release();
      if (['dirty', 'error'].includes(doc._saveState)) _scheduleAutoSave(state.docId, 800);
    }
  }
