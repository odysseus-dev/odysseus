// frontend/src/features/documents/ui/editor/markdownImages.js
/**
 * Markdown image upload, insertion, and autosave scheduling.
 */

import { API_BASE, activeDocId } from '../../application/documentState.js';
import { _scheduleAutoSave } from '../../application/persistence.js';
import { _isMarkdownImageFile, _isRichTextLang, _markdownImageAlt } from '../../domain/documentKinds.js';
import { uiModule } from '../host.js';
import { _activeDocLanguage } from '../status/documentStatus.js';
import { _insertRichTextImages } from '../richEditor/imageOps.js';
import { _emailRichbodyActive } from '../richEditor/richBodySync.js';
import { _refreshMarkdownPreviewIfVisible, _replaceRange, scheduleSourceEditorWork, updateLineNumbers } from './sourceEditor.js';

function _scheduleMarkdownImageAutosave(ta) {
    updateLineNumbers(ta.value);
    const codeEl = document.getElementById('doc-editor-code');
    if (codeEl && !codeEl.dataset.hasDiff) {
      codeEl.textContent = ta.value + '\n';
      codeEl.style.minHeight = ta.scrollHeight + 'px';
    }
    scheduleSourceEditorWork(ta.value, activeDocId);
    _scheduleAutoSave(activeDocId, 800);
  }

function _insertMarkdownImages(uploadedFiles) {
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    const files = Array.isArray(uploadedFiles) ? uploadedFiles : [];
    if (!files.length) return;

    const start = ta.selectionStart || 0;
    const end = ta.selectionEnd || start;
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    const lines = files.map(file => {
      const id = encodeURIComponent(file.id || file.file_id || '');
      const alt = _markdownImageAlt(file.name || file.filename);
      return id ? `![${alt}](/api/upload/${id})` : '';
    }).filter(Boolean);
    if (!lines.length) return;

    const prefix = before && !before.endsWith('\n') ? '\n' : '';
    const suffix = after && !after.startsWith('\n') ? '\n' : '';
    const insert = `${prefix}${lines.join('\n\n')}${suffix}`;
    _replaceRange(ta, start, end, insert);
    const caret = start + insert.length;
    ta.selectionStart = caret;
    ta.selectionEnd = caret;
    ta.focus();
    _scheduleMarkdownImageAutosave(ta);
    _refreshMarkdownPreviewIfVisible(activeDocId, ta.value);
  }

export async function _uploadMarkdownImages(files) {
    const images = Array.from(files || []).filter(_isMarkdownImageFile);
    if (!images.length) {
      if (uiModule) uiModule.showError('Choose an image file');
      return;
    }
    const language = _activeDocLanguage();
    const emailRichBody = language === 'email' && _emailRichbodyActive();
    if (language !== 'markdown' && !_isRichTextLang(language) && !emailRichBody) {
      if (uiModule) uiModule.showError('Switch the document to Markdown or Rich Text before inserting images');
      return;
    }

    const docId = activeDocId;
    const pane = document.getElementById('doc-editor-pane');
    const fd = new FormData();
    images.forEach(file => fd.append('files', file));
    try {
      const res = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      });
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
      const uploaded = Array.isArray(data?.files) ? data.files : [];
      if (!uploaded.length) throw new Error('No uploaded files returned');
      if (!pane?.isConnected || pane !== document.getElementById('doc-editor-pane') || pane.dataset.activeDocumentId !== docId || activeDocId !== docId) return;
      const activeRich = _emailRichbodyActive();
      if (_isRichTextLang(language) || emailRichBody || (language === 'markdown' && activeRich)) {
        _insertRichTextImages(uploaded);
      } else {
        _insertMarkdownImages(uploaded);
      }
      if (uiModule) uiModule.showToast(images.length === 1 ? 'Image inserted' : 'Images inserted');
    } catch (err) {
      console.error('Failed to insert markdown image:', err);
      if (uiModule) uiModule.showError('Failed to insert image');
    }
  }

export async function _handleMarkdownImageUpload(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await _uploadMarkdownImages(files);
  }
