// frontend/src/features/documents/ui/documentActions.js
/**
 * Run, copy, title, language, and automatic title actions.
 * Declarations moved from static/js/document.js under Appendix A (ui/documentActions).
 */

import { renderTabs } from '../application/documentCommands.js';
import { API_BASE, activeDocId, docs } from '../application/documentState.js';
import { _isRenderLang } from '../domain/documentKinds.js';
import { codeRunnerModule, uiModule } from './host.js';
import { toggleHtmlPreview } from './previewModes.js';

export function runDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    const code = textarea.value;
    const langSelect = document.getElementById('doc-language-select');
    const lang = (langSelect ? langSelect.value : '').toLowerCase();

    // Get or create the output panel below the editor
    let outputPanel = document.getElementById('doc-run-output');
    if (!outputPanel) {
      outputPanel = document.createElement('div');
      outputPanel.id = 'doc-run-output';
      outputPanel.className = 'doc-run-output';
      const editorWrap = document.getElementById('doc-editor-wrap');
      if (editorWrap) editorWrap.after(outputPanel);
    }
    outputPanel.style.display = 'block';
    outputPanel.innerHTML = '';

    if (!code.trim()) {
      outputPanel.innerHTML = '<pre class="doc-run-error">Nothing to run. Add some code first.</pre>';
      return;
    }

    if (_isRenderLang(lang)) {
      // HTML / SVG / XML — render inline in the sandboxed preview iframe.
      outputPanel.style.display = 'none';
      toggleHtmlPreview();
      return;
    }

    if (!codeRunnerModule) {
      outputPanel.innerHTML = '<pre class="doc-run-error">Code runner not loaded</pre>';
      setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
      return;
    }

    if (lang === 'bash' || lang === 'sh' || lang === 'shell' || lang === 'zsh') {
      codeRunnerModule.runServer(code, outputPanel, 'bash');
      return;
    }

    if (lang === 'python' || lang === 'py') {
      codeRunnerModule.runServer(code, outputPanel, 'python');
      return;
    }

    if (lang === 'javascript' || lang === 'js') {
      codeRunnerModule.runJavaScript(code, outputPanel);
      return;
    }

    outputPanel.innerHTML = '<pre class="doc-run-error">Unsupported language. Supported: bash, python, javascript, html</pre>';
    setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
  }

export async function copyDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || !textarea.value) return;
    if (uiModule && uiModule.copyToClipboard) {
      await uiModule.copyToClipboard(textarea.value);
    } else {
      try {
        await navigator.clipboard.writeText(textarea.value);
      } catch (e) { /* ignore */ }
    }
    if (uiModule) uiModule.showToast('Copied to clipboard');
  }

export async function updateTitle(overrideDocId, overrideTitle, options = {}) {
    const isAutoTitle = Boolean(options && options.isAutoTitle);
    const docId = overrideDocId || activeDocId;
    if (!docId) return;
    const doc = docs.get(docId);
    if (!doc) return;

    let title;
    if (overrideTitle !== undefined && overrideTitle !== null) {
      title = String(overrideTitle).trim();
    } else {
      const input = document.getElementById('doc-title-input');
      title = input ? input.value.trim() : '';
    }

    if (isAutoTitle && (!title || (doc.title && doc.title !== 'Untitled' && doc.title !== '' && doc.title !== title))) {
      return;
    }

    const previousTitle = doc.title;
    doc._titleAbortController?.abort();
    const controller = new AbortController();
    doc._titleAbortController = controller;
    const requestSeq = (doc._titleRequestSeq = (doc._titleRequestSeq || 0) + 1);

    try {
      const payload = { title };
      if (isAutoTitle) payload.if_untitled = true;
      if (options.expectedTitle !== undefined) payload.expected_title = options.expectedTitle;

      const res = await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json().catch(() => null);
      const serverTitle = (data && typeof data.title === 'string') ? data.title : title;

      const currentDoc = docs.get(docId);
      if (currentDoc && currentDoc === doc && doc._titleRequestSeq === requestSeq) {
        if (isAutoTitle && currentDoc.title && currentDoc.title !== 'Untitled' && currentDoc.title !== '' && currentDoc.title !== serverTitle) {
          return;
        }
        currentDoc.title = serverTitle;
        const titleInput = document.getElementById('doc-title-input');
        const pane = document.getElementById('doc-editor-pane');
        const activeTitle = (titleInput?.value || '').trim();
        if (titleInput && docId === activeDocId
            && (!pane || pane.dataset.activeDocumentId === docId)
            && document.activeElement !== titleInput
            && (!activeTitle || activeTitle === 'Untitled' || !isAutoTitle)) {
          titleInput.value = serverTitle;
        }
        renderTabs();
      }
    } catch (e) {
      if (e?.name === 'AbortError') return;
      const currentDoc = docs.get(docId);
      if (currentDoc && currentDoc === doc && doc._titleRequestSeq === requestSeq && !isAutoTitle) {
        currentDoc.title = previousTitle;
        const titleInput = document.getElementById('doc-title-input');
        if (titleInput && docId === activeDocId && document.activeElement !== titleInput) {
          titleInput.value = previousTitle || '';
        }
        renderTabs();
      }
      if (isAutoTitle && e?.message?.includes('409')) return;
      console.error('Failed to update title:', e);
    } finally {
      if (doc._titleAbortController === controller) {
        doc._titleAbortController = null;
      }
    }
  }

export function autoTitleFromContent(content, docId) {
    const id = docId || activeDocId;
    if (!id) return;
    const doc = docs.get(id);
    if (!doc || (doc.title && doc.title !== '' && doc.title !== 'Untitled')) return;

    const text = (content || '').trimStart();
    if (!text) return;

    let title = null;

    // Markdown header: # Title
    const mdMatch = text.match(/^#{1,3}\s+(.+)/m);
    if (mdMatch) {
      title = mdMatch[1].trim();
    }

    // HTML heading: <h1>Title</h1>
    if (!title) {
      const htmlMatch = text.match(/<h[1-3][^>]*>([^<]+)<\/h[1-3]>/i);
      if (htmlMatch) title = htmlMatch[1].trim();
    }

    // First non-empty line as fallback (only if short enough to be a title)
    if (!title) {
      const firstLine = text.split('\n').find(l => l.trim().length > 0);
      if (firstLine) {
        const cleaned = firstLine.trim();
        if (cleaned.length <= 60 && cleaned.length >= 2) {
          title = cleaned;
        }
      }
    }

    if (!title) return;

    // Clean up: strip trailing punctuation like : or ...
    title = title.replace(/[:#*`]+$/g, '').trim();
    if (title.length > 50) title = title.slice(0, 48) + '...';
    if (!title) return;

    updateTitle(id, title, { isAutoTitle: true });
    const titleInput = document.getElementById('doc-title-input');
    const activeTitle = (titleInput?.value || '').trim();
    if (titleInput && id === activeDocId && (!activeTitle || activeTitle === 'Untitled') && document.activeElement !== titleInput) {
      titleInput.value = title;
    }
  }

export async function updateLanguage(docId = activeDocId, language = document.getElementById('doc-language-select')?.value) {
    if (!docId || language == null) return;
    const doc = docs.get(docId);
    if (!doc) return;
    doc.language = language;
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language }),
      });
      if (!res.ok) throw new Error(`Language update failed: HTTP ${res.status}`);
      // The request retains its original identity and value. A response cannot
      // overwrite a later selection or a newly active document.
      if (docs.get(docId) === doc && doc.language === language) renderTabs();
    } catch (e) {
      console.error('Failed to update language:', e);
    }
  }
