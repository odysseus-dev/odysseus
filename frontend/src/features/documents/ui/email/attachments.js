import { _scheduleAutoSave } from '../../application/persistence.js';
import { uiModule, spinnerModule, langIcon } from '../host.js';
import { _activeDocLanguage } from '../status/documentStatus.js';
import { _showRichImageSourceMenu } from '../richEditor/imageMenus.js';
import { API_BASE, activeDocId, docs } from "../../application/documentState.js";

import { _escHtml, _isRichTextLang } from "../../domain/documentKinds.js";

function _afterOdysseusAttachmentsAdded(count, label, docId = activeDocId) {
    if (activeDocId === docId) _renderComposeAttachments();
    _scheduleAutoSave(docId, 800);
    if (uiModule) uiModule.showToast(count > 1 ? `Attached ${count} items` : `Attached ${label || 'item'}`);
  }

function _attachMenuEscape(e) {
    if (e.key !== 'Escape') return;
    _closeOdysseusAttachMenu();
  }

function _attachMenuOutsideClick(e) {
    if (_odysseusAttachMenu && !_odysseusAttachMenu.contains(e.target)) _closeOdysseusAttachMenu();
  }

async function _attachOdysseusItem(kind, id, label, opts = {}) {
    const docId = activeDocId;
    try {
      const data = await _stageOdysseusAttachment(kind, id, docId);
      if (!data) return;
      _afterOdysseusAttachmentsAdded(1, label || data.filename, docId);
      if (!opts.keepOpen) _closeOdysseusAttachMenu();
    } catch (err) {
      console.error('Failed to attach Odysseus item:', err);
      if (uiModule) uiModule.showError('Failed to attach from Odysseus');
    }
  }

async function _attachSelectedOdysseusItems(menu) {
    const docId = activeDocId;
    const rows = _selectedOdysseusAttachRows(menu);
    if (!rows.length) return;
    const btn = menu.querySelector('.email-odysseus-attach-selected');
    if (btn) {
      btn.disabled = true;
      btn.classList.add('is-loading');
    }
    let added = 0;
    try {
      const items = rows.map(row => ({ kind: row.dataset.kind, id: row.dataset.id })).filter(x => x.kind && x.id);
      let zip = false;
      if (items.length > 5) {
        const ask = window.styledConfirm || uiModule?.styledConfirm;
        zip = ask
          ? await ask(`Attach ${items.length} files as one zip?`, { confirmText: 'Zip', cancelText: 'Separate' })
          : window.confirm(`Attach ${items.length} files as one zip?`);
      }
      if (zip) {
        await _stageOdysseusZip(items, docId);
        added = 1;
      } else {
        for (const item of items) {
          await _stageOdysseusAttachment(item.kind, item.id, docId);
          added += 1;
        }
      }
      _afterOdysseusAttachmentsAdded(added, zip ? 'odysseus-attachments.zip' : undefined, docId);
      _closeOdysseusAttachMenu();
    } catch (err) {
      console.error('Failed to attach selected Odysseus items:', err);
      if (uiModule) uiModule.showError(added ? `Attached ${added}, then failed` : 'Failed to attach from Odysseus');
      _renderComposeAttachments();
    } finally {
      if (btn) {
        btn.classList.remove('is-loading');
        btn.disabled = false;
      }
    }
  }

function _closeOdysseusAttachMenu() {
    if (_odysseusAttachMenu) {
      _odysseusAttachMenu.remove();
      _odysseusAttachMenu = null;
    }
    document.removeEventListener('click', _attachMenuOutsideClick, true);
    document.removeEventListener('keydown', _attachMenuEscape, true);
  }

export async function _handleAttachUpload(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = ''; // reset for next upload
    await _uploadComposeFiles(files);
  }

async function _loadOdysseusAttachItems(menu, kind) {
    const list = menu.querySelector('.email-odysseus-attach-list');
    if (!list) return;
    menu.dataset.odyAttachKind = kind;
    list.replaceChildren(spinnerModule.createLoadingRow('Loading…', 14));
    menu.querySelectorAll('[data-ody-attach-kind]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.odyAttachKind === kind);
    });
    const q = (menu.querySelector('.email-odysseus-attach-search')?.value || '').trim();
    try {
      const params = new URLSearchParams({ sort: 'recent', limit: '20' });
      if (q) params.set('search', q);
      const endpoint = kind === 'gallery'
        ? `${API_BASE}/api/gallery/library?${params}`
        : `${API_BASE}/api/documents/library?${params}`;
      const res = await fetch(endpoint, { credentials: 'same-origin' });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      const items = kind === 'gallery'
        ? (Array.isArray(data?.items) ? data.items : Array.isArray(data?.images) ? data.images : [])
        : (Array.isArray(data?.documents) ? data.documents : Array.isArray(data?.items) ? data.items : []);
      if (!items.length) {
        list.innerHTML = `<div class="email-odysseus-attach-empty">${q ? 'No matches' : `No ${kind === 'gallery' ? 'images' : 'documents'}`}</div>`;
        _syncOdysseusAttachSelection(menu);
        return;
      }
      list.innerHTML = '';
      for (const item of items) {
        const label = _odysseusAttachLabel(item, kind);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = `email-odysseus-attach-row ${kind === 'gallery' ? 'is-gallery' : ''}`;
        row.dataset.id = item.id || '';
        row.dataset.kind = kind;
        if (kind === 'gallery') {
          row.innerHTML = `
            <span class="email-odysseus-attach-dot" aria-hidden="true"></span>
            <span class="email-odysseus-attach-thumb"></span>
            <span class="email-odysseus-attach-main">
              <span class="email-odysseus-attach-title">${_escHtml(label)}</span>
              <span class="email-odysseus-attach-meta">${_escHtml(item.filename || 'image')}</span>
            </span>
          `;
          if (item.url) {
            const img = document.createElement('img');
            img.alt = '';
            img.src = `${API_BASE}${item.url}`;
            row.querySelector('.email-odysseus-attach-thumb').appendChild(img);
          }
        } else {
          row.innerHTML = `
            <span class="email-odysseus-attach-dot" aria-hidden="true"></span>
            <span class="email-odysseus-attach-icon">${langIcon(item.language || 'text', 14, { style: 'opacity:0.8;' })}</span>
            <span class="email-odysseus-attach-main">
              <span class="email-odysseus-attach-title">${_escHtml(label)}</span>
              <span class="email-odysseus-attach-meta">${_escHtml(item.language || 'text')}</span>
            </span>
          `;
        }
        row.addEventListener('click', (ev) => {
          ev.preventDefault();
          row.classList.toggle('is-selected');
          _syncOdysseusAttachSelection(menu);
        });
        row.addEventListener('dblclick', () => _attachOdysseusItem(kind, item.id, label, { keepOpen: false }));
        list.appendChild(row);
      }
      _syncOdysseusAttachSelection(menu);
    } catch (err) {
      console.error('Failed to load Odysseus attach items:', err);
      list.innerHTML = '<div class="email-odysseus-attach-empty">Could not load</div>';
    }
  }

function _odysseusAttachLabel(item, kind) {
    if (kind === 'gallery') {
      return item.caption || item.prompt || item.filename || 'Gallery image';
    }
    return item.title || 'Untitled document';
  }

let _odysseusAttachMenu = null;

function _positionOdysseusAttachMenu(anchor, menu) {
    const r = anchor?.getBoundingClientRect?.();
    if (!r) return;
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 310))}px`;
    menu.style.top = `${r.bottom + 6}px`;
    requestAnimationFrame(() => {
      const mr = menu.getBoundingClientRect();
      if (mr.bottom > window.innerHeight - 8) {
        menu.style.top = `${Math.max(8, r.top - mr.height - 6)}px`;
      }
    });
  }

export function _renderComposeAttachments() {
    const container = document.getElementById('doc-email-compose-atts');
    if (!container) return;
    const doc = docs.get(activeDocId);
    const atts = doc?._composeAtts || [];
    if (atts.length === 0) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.style.display = '';
    container.innerHTML = '';
    for (const att of atts) {
      const chip = document.createElement('span');
      chip.className = 'email-compose-chip';
      const sizeKb = att.size > 0 ? `${Math.round(att.size / 1024)} KB` : '';
      chip.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        <span class="compose-chip-name">${_escHtml(att.filename)}</span>
        <span class="att-size">${sizeKb}</span>
        <button class="compose-chip-remove" title="Remove">×</button>
      `;
      chip.querySelector('.compose-chip-remove').addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          await fetch(`${API_BASE}/api/email/compose-upload/${encodeURIComponent(att.token)}`, { method: 'DELETE' });
        } catch (_) {}
        const d = docs.get(activeDocId);
        if (d) d._composeAtts = d._composeAtts.filter(a => a.token !== att.token);
        _renderComposeAttachments();
      });
      container.appendChild(chip);
    }
  }

function _selectedOdysseusAttachRows(menu) {
    return Array.from(menu?.querySelectorAll?.('.email-odysseus-attach-row.is-selected') || []);
  }

export function _showComposeAttachMenu(anchor) {
    const activeLanguage = _activeDocLanguage();
    if (_isRichTextLang(activeLanguage)) {
      _showRichImageSourceMenu(anchor);
      return;
    }
    if (activeLanguage !== 'email') {
      document.getElementById('doc-md-image-input')?.click();
      return;
    }
    _closeOdysseusAttachMenu();
    const menu = document.createElement('div');
    menu.className = 'email-odysseus-attach-menu';
    menu.innerHTML = `
      <button type="button" class="email-odysseus-attach-local">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        Upload from computer
      </button>
      <div class="email-odysseus-attach-tabs">
        <button type="button" data-ody-attach-kind="document" class="active">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h6"/></svg>
          <span>Documents</span>
        </button>
        <button type="button" data-ody-attach-kind="gallery">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
          <span>Gallery images</span>
        </button>
      </div>
      <label class="email-odysseus-attach-search-wrap">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input type="search" class="email-odysseus-attach-search" placeholder="Search documents or images">
      </label>
      <div class="email-odysseus-attach-list"></div>
      <div class="email-odysseus-attach-actions">
        <span class="email-odysseus-attach-count"></span>
        <button type="button" class="email-odysseus-attach-selected" disabled>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>
          <span>Attach</span>
        </button>
      </div>
    `;
    document.body.appendChild(menu);
    _odysseusAttachMenu = menu;
    _positionOdysseusAttachMenu(anchor, menu);
    menu.querySelector('.email-odysseus-attach-local')?.addEventListener('click', () => {
      _closeOdysseusAttachMenu();
      document.getElementById('doc-email-file-input')?.click();
    });
    menu.querySelectorAll('[data-ody-attach-kind]').forEach(btn => {
      btn.addEventListener('click', () => _loadOdysseusAttachItems(menu, btn.dataset.odyAttachKind));
    });
    let attachSearchTimer = null;
    menu.querySelector('.email-odysseus-attach-search')?.addEventListener('input', () => {
      clearTimeout(attachSearchTimer);
      attachSearchTimer = setTimeout(() => {
        _loadOdysseusAttachItems(menu, menu.dataset.odyAttachKind || 'document');
      }, 220);
    });
    menu.querySelector('.email-odysseus-attach-selected')?.addEventListener('click', () => _attachSelectedOdysseusItems(menu));
    setTimeout(() => {
      document.addEventListener('click', _attachMenuOutsideClick, true);
      document.addEventListener('keydown', _attachMenuEscape, true);
    }, 0);
    _loadOdysseusAttachItems(menu, 'document');
  }

export async function _stageForwardedSourceAttachments(fields) {
    const docId = activeDocId;
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return;
    if (!fields?.forwardAttachments || !fields.sourceUid || !Array.isArray(fields.attachments) || fields.attachments.length === 0) return;
    const sourceKey = `${fields.sourceFolder || 'INBOX'}:${fields.sourceUid}:${fields.attachments.map(a => a.index).join(',')}`;
    if (doc._forwardedAttachmentSourceKey === sourceKey) return;
    doc._forwardedAttachmentSourceKey = sourceKey;
    if (!doc._composeAtts) doc._composeAtts = [];
    const existingForwarded = new Set(doc._composeAtts.filter(a => a.forwardedSourceKey === sourceKey).map(a => String(a.sourceIndex)));
    let added = 0;
    for (const att of fields.attachments) {
      const sourceIndex = String(att.index);
      if (existingForwarded.has(sourceIndex)) continue;
      try {
        const folderQs = encodeURIComponent(fields.sourceFolder || 'INBOX');
        const res = await fetch(`${API_BASE}/api/email/compose-from-attachment/${encodeURIComponent(fields.sourceUid)}/${encodeURIComponent(att.index)}?folder=${folderQs}`, {
          method: 'POST',
          credentials: 'same-origin',
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'failed');
        if (docs.get(docId) !== doc) return;
        doc._composeAtts.push({
          token: data.token,
          filename: data.filename || att.filename,
          size: data.size || att.size || 0,
          forwardedSourceKey: sourceKey,
          sourceIndex,
        });
        added += 1;
      } catch (err) {
        console.error('Failed to stage forwarded attachment:', err);
        if (uiModule) uiModule.showError(`Forward attachment failed: ${att.filename || 'attachment'}`);
      }
    }
    if (added) {
      if (activeDocId === docId) _renderComposeAttachments();
      _scheduleAutoSave(docId, 800);
    }
  }

async function _stageOdysseusAttachment(kind, id, docId = activeDocId) {
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return null;
    if (!doc._composeAtts) doc._composeAtts = [];
    const res = await fetch(`${API_BASE}/api/email/compose-from-odysseus`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, id }),
    });
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || !data?.success) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
    if (docs.get(docId) !== doc) return null;
    doc._composeAtts.push({
      token: data.token,
      filename: data.filename,
      size: data.size || 0,
    });
    return data;
  }

async function _stageOdysseusZip(items, docId = activeDocId) {
    const doc = docs.get(docId);
    if (!doc || doc.language !== 'email') return null;
    if (!doc._composeAtts) doc._composeAtts = [];
    const res = await fetch(`${API_BASE}/api/email/compose-from-odysseus-zip`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || !data?.success) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
    if (docs.get(docId) !== doc) return null;
    doc._composeAtts.push({
      token: data.token,
      filename: data.filename,
      size: data.size || 0,
    });
    return data;
  }

function _syncOdysseusAttachSelection(menu) {
    const selected = _selectedOdysseusAttachRows(menu);
    const bar = menu?.querySelector?.('.email-odysseus-attach-actions');
    const count = menu?.querySelector?.('.email-odysseus-attach-count');
    const attachBtn = menu?.querySelector?.('.email-odysseus-attach-selected');
    if (bar) bar.style.display = '';
    if (count) count.textContent = selected.length ? `${selected.length} selected` : 'Select items to attach';
    if (attachBtn) attachBtn.disabled = selected.length === 0;
  }

export async function _uploadComposeFiles(files) {
    const list = Array.from(files || []);
    if (list.length === 0) return;
    const docId = activeDocId;
    const doc = docs.get(docId);
    if (!doc) return;
    if (doc.language !== 'email') return;
    if (!doc._composeAtts) doc._composeAtts = [];

    for (const file of list) {
      try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${API_BASE}/api/email/compose-upload`, {
          method: 'POST',
          body: fd,
        });
        const data = await res.json();
        if (docs.get(docId) !== doc) return;
        if (data.success) {
          doc._composeAtts.push({
            token: data.token,
            filename: data.filename,
            size: data.size,
          });
        } else {
          if (uiModule) uiModule.showError(`Failed to upload ${file.name}: ${data.error || ''}`);
        }
      } catch (err) {
        if (uiModule) uiModule.showError(`Failed to upload ${file.name}`);
      }
    }
    if (activeDocId === docId) _renderComposeAttachments();
  }
