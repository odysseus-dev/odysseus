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

export async function updateTitle(overrideDocId, overrideTitle) {
    const docId = overrideDocId || activeDocId;
    if (!docId) return;
    const title = overrideTitle || document.getElementById('doc-title-input')?.value;
    if (!title) return;
    try {
      await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (docs.has(docId)) {
        docs.get(docId).title = title;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update title:', e);
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

    updateTitle(id, title);
    const titleInput = document.getElementById('doc-title-input');
    if (titleInput && id === activeDocId) titleInput.value = title;
  }

export async function updateLanguage() {
    if (!activeDocId) return;
    const select = document.getElementById('doc-language-select');
    if (!select) return;
    try {
      await fetch(`${API_BASE}/api/document/${activeDocId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: select.value }),
      });
      if (docs.has(activeDocId)) {
        docs.get(activeDocId).language = select.value;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update language:', e);
    }
  }
