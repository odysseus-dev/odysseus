// Document writing-style settings panel.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';

export async function initDocumentWritingStyle() {
  const styleEl = el('set-document-style');
  const saveBtn = el('set-document-style-save');
  const extractBtn = el('set-document-style-extract');
  const fileEl = el('set-document-style-file');
  const msg = el('set-document-style-msg');
  if (!styleEl || !saveBtn) return;
  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const data = await res.json();
    styleEl.value = String(data.document_writing_style || '');
  } catch (_) {
    if (msg) msg.textContent = 'Failed to load';
  }
  saveBtn.addEventListener('click', async () => {
    if (msg) msg.textContent = 'Saving...';
    try {
      const res = await _postSettings({ document_writing_style: styleEl.value });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (msg) msg.textContent = '✓ Saved';
    } catch (e) {
      if (msg) msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save';
    }
    setTimeout(() => { if (msg) msg.textContent = ''; }, 3000);
  });
  extractBtn?.addEventListener('click', () => fileEl?.click());
  fileEl?.addEventListener('change', async () => {
    const file = fileEl.files?.[0];
    if (!file) return;
    extractBtn.disabled = true;
    let whirlpool = null;
    if (msg) {
      msg.replaceChildren();
      try {
        const spinner = window.spinnerModule || (await import('../spinner.js')).default;
        whirlpool = spinner.createWhirlpool(14);
        whirlpool.element.style.cssText = 'display:inline-flex;width:14px;height:14px;margin-right:7px;';
        const label = document.createElement('span');
        label.textContent = 'Analyzing...';
        msg.append(whirlpool.element, label);
      } catch (_) {
        msg.textContent = 'Analyzing...';
      }
    }
    try {
      const body = new FormData();
      body.append('file', file);
      const res = await fetch('/api/auth/settings/document-style/extract', {
        method: 'POST', credentials: 'same-origin', body,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.style) throw new Error(data.detail || data.error || 'Extraction failed');
      styleEl.value = String(data.style);
      if (msg) msg.textContent = '✓ Extracted — review and save';
    } catch (error) {
      if (msg) msg.textContent = error.message || 'Extraction failed';
    } finally {
      whirlpool?.destroy?.();
      extractBtn.disabled = false;
      fileEl.value = '';
    }
  });
}
