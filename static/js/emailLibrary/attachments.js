// static/js/emailLibrary/attachments.js
//
// Attachment chips in a reader: building the markup, wiring open/download/
// "open in editor" per type, and the deferred load for messages whose
// attachment list was not in the list response.
//
// The deferred path exists because `/api/email/list` does not carry attachment
// metadata: a card can know it *has* attachments without knowing what they
// are, so the chips are rendered late and the card icon repaired afterwards.

import spinnerModule from '../spinner.js';
import * as Modals from '../modalManager.js';
import { state } from './state.js';
import { _esc } from './utils.js';
import { _acct, _openCalendarEventFromEmail, _prepareEmailWindowForDocument } from './index.js';
import { _openEmailAsTab, _openEmailWindow } from './reader.js';

const API_BASE = window.location.origin;

// Wire click handlers for attachment chips + "open in editor" sub-buttons
// inside a reader. Safe to call multiple times — uses dataset.wired flag to
// skip nodes that already have listeners.
function _wireAttachmentHandlers(reader, folder) {
  const useFolder = folder || state._libFolder;
  // Detect mobile here so the attachment-chip handler doesn't blow up with
  // a ReferenceError when this fn is called from contexts that don't have
  // _isMobileUA in scope (e.g. _openEmailAsTab, _openEmailWindow).
  const _isMobileUA = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  reader.querySelectorAll('.email-attachments-download-all').forEach(btn => {
    if (btn.dataset.wired === '1') return;
    btn.dataset.wired = '1';
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      if (btn.dataset.downloading === '1') return;
      const uid = btn.dataset.attUid;
      const sourceFolder = btn.dataset.attFolder || useFolder;
      const count = Number(btn.dataset.attCount || 0);
      if (!uid) return;
      const originalHtml = btn.innerHTML;
      const originalTitle = btn.title;
      btn.dataset.downloading = '1';
      btn.classList.add('is-loading');
      try {
        const sp = window.spinnerModule || (await import('../spinner.js')).default;
        const wp = sp.createWhirlpool(12);
        wp.element.style.margin = '0';
        btn.textContent = '';
        btn.appendChild(wp.element);
        const label = document.createElement('span');
        label.textContent = 'All';
        btn.appendChild(label);
      } catch (_) {
        btn.textContent = 'All...';
      }
      try {
        const url = `${API_BASE}/api/email/attachments-download/${encodeURIComponent(uid)}?folder=${encodeURIComponent(sourceFolder)}${_acct()}`;
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok) {
          const msg = await res.text().catch(() => '');
          console.error('attachments zip download failed', res.status, msg);
          location.href = url;
          return;
        }
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = `email-${uid}-attachments.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
        try { uiModule.showToast && uiModule.showToast(`Downloading ${count || 'all'} attachments`); } catch (_) {}
      } catch (e) {
        console.error('attachments zip download error', e);
        try { const { showError } = await import('../ui.js?v=20260916largetoolscroll1'); showError('Could not download attachments'); } catch (_) {}
      } finally {
        delete btn.dataset.downloading;
        btn.classList.remove('is-loading');
        btn.title = originalTitle;
        btn.innerHTML = originalHtml;
      }
    });
  });
  reader.querySelectorAll('.email-attachment-calendar-open').forEach(openBtn => {
    if (openBtn.dataset.wired === '1') return;
    openBtn.dataset.wired = '1';
    openBtn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      if (openBtn.dataset.opening === '1') return;
      const uid = openBtn.dataset.openUid;
      const index = openBtn.dataset.openIndex;
      const name = openBtn.dataset.openName || `calendar-${index}`;
      const sourceFolder = openBtn.dataset.openFolder || useFolder;
      if (!uid || index == null) return;
      openBtn.dataset.opening = '1';
      openBtn.classList.add('is-loading');
      const originalHtml = openBtn.innerHTML;
      try {
        const wp = spinnerModule.createWhirlpool(12);
        wp.element.style.margin = '0';
        openBtn.textContent = '';
        openBtn.appendChild(wp.element);
        const folderQs = encodeURIComponent(sourceFolder);
        const attachmentUrl = `${API_BASE}/api/email/attachment/${encodeURIComponent(uid)}/${encodeURIComponent(index)}?folder=${folderQs}${_acct()}`;
        const attachmentRes = await fetch(attachmentUrl, { credentials: 'same-origin' });
        if (!attachmentRes.ok) throw new Error(`HTTP ${attachmentRes.status}`);
        const blob = await attachmentRes.blob();
        const fd = new FormData();
        fd.append('file', blob, name);
        const importRes = await fetch(`${API_BASE}/api/calendar/import`, {
          method: 'POST', body: fd, credentials: 'same-origin',
        });
        const result = await importRes.json().catch(() => ({}));
        if (!importRes.ok || !result.ok) {
          throw new Error(result.detail || result.error || `HTTP ${importRes.status}`);
        }
        const existingEventUid = Array.isArray(result.event_uids) ? String(result.event_uids[0] || '').trim() : '';
        if (existingEventUid && Number(result.imported || 0) === 0 && Number(result.skipped || 0) > 0) {
          _openCalendarEventFromEmail(existingEventUid);
        }
        try { uiModule.showToast && uiModule.showToast(`${result.imported || 0} event${result.imported === 1 ? '' : 's'} added to ${result.calendar || 'calendar'}`); } catch (_) {}
        window.dispatchEvent(new CustomEvent('calendar-refresh'));
      } catch (e) {
        console.error('calendar attachment import failed', e);
        try {
          const { showError } = await import('../ui.js?v=20260916largetoolscroll1');
          showError(`Couldn't add ${name} to calendar: ${e?.message || 'Import failed'}`);
        } catch (_) {}
      } finally {
        delete openBtn.dataset.opening;
        openBtn.classList.remove('is-loading');
        openBtn.innerHTML = originalHtml;
      }
    });
  });
  reader.querySelectorAll('.email-attachment-open').forEach(openBtn => {
    if (openBtn.dataset.wired === '1') return;
    openBtn.dataset.wired = '1';
    openBtn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      if (openBtn.dataset.opening === '1') return;
      const uid = openBtn.dataset.openUid;
      const index = openBtn.dataset.openIndex;
      const name = openBtn.dataset.openName || `attachment-${index}`;
      const sourceFolder = openBtn.dataset.openFolder || useFolder;
      if (!uid || index == null) return;
      openBtn.dataset.opening = '1';
      openBtn.classList.add('is-loading');
      const origHtml = openBtn.innerHTML;
      const wp = spinnerModule.createWhirlpool(12);
      wp.element.style.margin = '0';
      openBtn.textContent = '';
      openBtn.appendChild(wp.element);
      try {
        const folderQs = encodeURIComponent(sourceFolder);
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 55000);
        let res;
        try {
          res = await fetch(
            `${API_BASE}/api/email/attachment-as-doc/${encodeURIComponent(uid)}/${encodeURIComponent(index)}?folder=${folderQs}${_acct()}`,
            { method: 'POST', credentials: 'same-origin', signal: controller.signal }
          );
        } finally {
          clearTimeout(timeout);
        }
        const json = await res.json().catch(() => ({}));
        if (!res.ok || !json.doc_id) {
          const msg = (json && json.error) || `HTTP ${res.status}`;
          try { const { showError } = await import('../ui.js?v=20260916largetoolscroll1'); showError(`Couldn't open ${name}: ${msg}`); } catch (_) { alert(`Couldn't open ${name}: ${msg}`); }
          return;
        }
        try {
          // Tab the email modal down only when the viewport cannot fit both
          // Email and the document pane. Desktop keeps a side-by-side layout
          // when there is room; mobile still gives the document the screen.
          const ownerModal = openBtn.closest('.modal');
          if (ownerModal && ownerModal.id && _prepareEmailWindowForDocument(ownerModal)) {
            try {
              const ok = Modals.minimize(ownerModal.id);
              if (!ok) ownerModal.classList.add('hidden');
            } catch (_) {
              ownerModal.classList.add('hidden');
            }
          }
          const docMod = await import('../document.js?v=20260916docctx2');
          const load = (docMod && docMod.loadDocument) || (docMod && docMod.default && docMod.default.loadDocument);
          if (typeof load === 'function') {
            await load(json.doc_id);
          } else {
            location.href = `/?doc=${encodeURIComponent(json.doc_id)}`;
          }
        } catch (e) {
          console.error('Open document failed:', e);
          try { const { showError } = await import('../ui.js?v=20260916largetoolscroll1'); showError('Document opened but panel could not mount'); } catch (_) {}
        }
      } catch (e) {
        console.error('attachment-as-doc error', e);
        const msg = e && e.name === 'AbortError'
          ? `Opening ${name} timed out. Try downloading it instead.`
          : `Couldn't open ${name}`;
        try { const { showError } = await import('../ui.js?v=20260916largetoolscroll1'); showError(msg); } catch (_) {}
      } finally {
        delete openBtn.dataset.opening;
        openBtn.classList.remove('is-loading');
        openBtn.innerHTML = origHtml;
      }
    });
  });

  reader.querySelectorAll('.email-attachment-download').forEach(downloadBtn => {
    if (downloadBtn.dataset.wired === '1') return;
    downloadBtn.dataset.wired = '1';
    downloadBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      const chip = downloadBtn.closest('.email-attachment-chip');
      if (!chip) return;
      // Reuse the established attachment download handler below. Marking the
      // chip expanded bypasses its old first-click reveal behavior.
      chip.classList.add('is-expanded');
      chip.dataset.downloadTrigger = 'button';
      chip.click();
    });
    downloadBtn.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      ev.preventDefault();
      downloadBtn.click();
    });
  });

  reader.querySelectorAll('.email-attachment-chip').forEach(chip => {
    if (chip.dataset.wired === '1') return;
    chip.dataset.wired = '1';
    chip.addEventListener('click', async (ev) => {
      if (ev.target.closest('.email-attachment-open, .email-attachment-calendar-open, .email-attachment-download')) return;
      ev.stopPropagation();
      ev.preventDefault();
      const uid = chip.dataset.attUid;
      const index = chip.dataset.attIndex;
      const name = chip.dataset.attName || `attachment-${index}`;
      const sourceFolder = chip.dataset.attFolder || useFolder;
      if (!uid || index == null) return;
      if (!chip.classList.contains('is-expanded')) {
        reader.querySelectorAll('.email-attachment-chip.is-expanded').forEach(other => {
          if (other !== chip) other.classList.remove('is-expanded');
        });
        chip.classList.add('is-expanded');
        return;
      }
      const url = `${API_BASE}/api/email/attachment/${encodeURIComponent(uid)}/${encodeURIComponent(index)}?folder=${encodeURIComponent(sourceFolder)}${_acct()}`;
      if (_isMobileUA) {
        delete chip.dataset.downloadTrigger;
        window.open(url, '_blank');
        return;
      }
      // Swap the paperclip icon for a whirlpool spinner while the
      // download is in flight, so large attachments give a clear cue
      // they're loading. Restore on completion.
      const buttonTriggered = chip.dataset.downloadTrigger === 'button';
      const iconSvg = buttonTriggered
        ? chip.querySelector('.email-attachment-download > svg')
        : chip.querySelector(':scope > svg');
      const origIconHtml = iconSvg ? iconSvg.outerHTML : '';
      let _wp = null;
      let _spinnerHost = null;
      try {
        const sp = window.spinnerModule || (await import('../spinner.js')).default;
        _wp = sp.createWhirlpool(12);
        _spinnerHost = document.createElement('span');
        _spinnerHost.className = 'email-attachment-spinner';
        _spinnerHost.style.cssText = 'display:inline-flex;width:12px;height:12px;align-items:center;justify-content:center;flex-shrink:0;position:relative;top:-2px;';
        _spinnerHost.appendChild(_wp.element);
        if (iconSvg) iconSvg.replaceWith(_spinnerHost);
      } catch (_) {}
      const origOpacity = chip.style.opacity;
      chip.style.opacity = '0.85';
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok) {
          console.error('attachment download failed', res.status, await res.text().catch(() => ''));
          location.href = url;
          return;
        }
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      } catch (e) {
        console.error('attachment download error', e);
        location.href = url;
      } finally {
        delete chip.dataset.downloadTrigger;
        chip.style.opacity = origOpacity;
        if (_spinnerHost && _spinnerHost.parentNode && origIconHtml) {
          const tmp = document.createElement('div');
          tmp.innerHTML = origIconHtml;
          const restored = tmp.firstChild;
          if (restored) _spinnerHost.replaceWith(restored);
        }
        if (_wp) { try { _wp.destroy(); } catch (_) {} }
      }
    });
  });
}

// Heuristic: skip "attachments" that are clearly inline images used by
// signatures / quoted-reply headers (small image files, Outlook-style
// image001.png placeholders, logo*.png, etc.). They aren't real user-
// shared attachments and adding them to the chips makes every email look
// like it has content the user needs to act on.
function _isLikelySignatureImage(a) {
  if (!a || !a.filename) return false;
  const name = String(a.filename).toLowerCase();
  const isImage = /\.(png|jpe?g|gif|bmp|svg|webp)$/i.test(name);
  if (!isImage) return false;
  const size = Number(a.size) || 0;
  // Outlook / Gmail inline image placeholders always look like this.
  if (/^image\d{3,}\.(png|jpe?g|gif)$/i.test(name)) return true;
  if (/^(signature|logo|sig|footer|banner)[-_\d]*\.(png|jpe?g|gif|svg)$/i.test(name)) return true;
  // Most signature logos / inline thumbnails are < 30 KB. Real user-
  // shared images (screenshots, photos) are typically 50 KB+.
  if (size > 0 && size < 30 * 1024) return true;
  return false;
}

// Build the attachments header+chips HTML for an email read response. Pulled
// out so both the initial-open and the swap-reader paths can render it.
export function _buildAttsHtmlFor(uid, data) {
  if (!data) return '';
  const _OPENABLE_RE = /\.(pdf|docx|txt|md|markdown|eml)$/i;
  const _CALENDAR_RE = /\.(calendar|ics|ical)$/i;
  const currentAttachments = Array.isArray(data.attachments) ? data.attachments : [];
  const relatedAttachments = Array.isArray(data.related_attachments) ? data.related_attachments : [];
  if (!currentAttachments.length && !relatedAttachments.length) return '';
  const visible = currentAttachments.filter(a => !_isLikelySignatureImage(a));
  const hidden = currentAttachments.filter(a => _isLikelySignatureImage(a));
  const related = relatedAttachments.filter(a => !_isLikelySignatureImage(a));
  if (!visible.length && !related.length && state._libViewInlineImages === false) return '';
  const renderChip = (a, extraClass = '') => {
    const calendarAttachment = _CALENDAR_RE.test(a.filename || '');
    const openable = _OPENABLE_RE.test(a.filename || '');
    const chipUid = a.source_uid || a.uid || uid;
    const chipFolder = a.source_folder || data.folder || state._libFolder || 'INBOX';
    const openBtn = calendarAttachment
      ? `<span class="email-attachment-calendar-open" role="button" tabindex="0" aria-label="Add to calendar" title="Add to calendar" data-open-uid="${_esc(chipUid)}" data-open-index="${a.index}" data-open-name="${_esc(a.filename)}" data-open-folder="${_esc(chipFolder)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="m8 15 2 2 5-5"/></svg></span>`
      : openable
      ? `<span class="email-attachment-open" role="button" tabindex="0" aria-label="Open in document editor" title="Open in document editor" data-open-uid="${_esc(chipUid)}" data-open-index="${a.index}" data-open-name="${_esc(a.filename)}" data-open-folder="${_esc(chipFolder)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/></svg></span>`
      : '';
    const downloadBtn = `<span class="email-attachment-download" role="button" tabindex="0" aria-label="Download attachment" title="Download attachment"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>`;
    return `<button type="button" class="email-attachment-chip${extraClass}" data-att-uid="${_esc(chipUid)}" data-att-index="${a.index}" data-att-name="${_esc(a.filename)}" data-att-folder="${_esc(chipFolder)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><span>${_esc(a.filename)}</span><span class="att-size">${Math.round((a.size||0)/1024)} KB</span>${openBtn}${downloadBtn}</button>`;
  };
  const chips = visible.map(a => renderChip(a)).join('');
  const hiddenChips = hidden.map(a => renderChip(a, ' email-attachment-chip-muted')).join('');
  const relatedChips = related.map(a => renderChip(a, ' email-attachment-chip-related')).join('');
  const visibleSection = visible.length
    ? '<div class="email-reader-atts">' + chips + '</div>'
    : '';
  const relatedSection = related.length
    ? '<div class="email-reader-atts-hidden-note">From earlier in this thread</div><div class="email-reader-atts email-reader-atts-related">' + relatedChips + '</div>'
    : '';
  const hiddenSection = hidden.length && state._libViewInlineImages !== false
    ? '<div class="email-reader-atts-hidden-note">Filtered inline images / signature files</div><div class="email-reader-atts email-reader-atts-hidden">' + hiddenChips + '</div>'
    : '';
  const label = visible.length
    ? `Attachments (${visible.length + related.length})`
    : related.length
      ? `Thread attachments (${related.length})`
      : state._libViewInlineImages === false
        ? 'Attachments'
        : `Hidden inline attachments (${hidden.length})`;
  const startCollapsed = !visible.length && !related.length;
  const downloadAllBtn = visible.length > 4
    ? `<button type="button" class="email-attachments-download-all" title="Download all attachments" data-att-uid="${_esc(uid)}" data-att-folder="${_esc(data.folder || state._libFolder || 'INBOX')}" data-att-count="${visible.length}"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>All</span></button>`
    : '';
  return (
    `<div class="email-reader-atts-wrap${startCollapsed ? ' collapsed' : ''}">`
    +   '<div class="email-reader-atts-header email-summary-toggle" role="button" tabindex="0">'
    +     '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
    +     `<span>${label}</span>`
    +     downloadAllBtn
    +     '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    +   '</div>'
    +   visibleSection
    +   relatedSection
    +   hiddenSection
    + '</div>'
  );
}

async function _ensureEmailAttachmentData(uid, folder, data, knownHasAttachments = false) {
  if (!data) return data;
  const current = Array.isArray(data.attachments) ? data.attachments : [];
  const related = Array.isArray(data.related_attachments) ? data.related_attachments : [];
  const shouldFetch = data.attachments_deferred || (knownHasAttachments && !current.length && !related.length);
  if (!shouldFetch) return data;
  try {
    const metaRes = await fetch(`${API_BASE}/api/email/attachments/${encodeURIComponent(uid)}?folder=${encodeURIComponent(folder || 'INBOX')}${_acct()}`);
    const meta = await metaRes.json().catch(() => ({}));
    if (metaRes.ok && Array.isArray(meta.attachments)) {
      return {
        ...data,
        attachments: meta.attachments,
        related_attachments: related,
        attachments_deferred: false,
      };
    }
  } catch (_) {}
  // Do not full-fetch the message during ordinary open. That can download and
  // parse large MIME bodies immediately after the reader renders, keeping IMAP
  // busy and making the UI feel stuck. Full attachment lookup is still available
  // from explicit attachment/download/forward actions.
  return data;
}

export function _wireEmailAttachmentWrap(reader, folder) {
  if (!reader) return;
  const attsWrap = reader.querySelector('.email-reader-atts-wrap');
  if (attsWrap && !attsWrap.dataset.wired) {
    attsWrap.dataset.wired = '1';
    const attsToggle = attsWrap.querySelector('.email-reader-atts-header');
    if (attsToggle) {
      attsToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        attsWrap.classList.toggle('collapsed');
      });
      attsToggle.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          attsWrap.classList.toggle('collapsed');
        }
      });
    }
  }
  try { _wireAttachmentHandlers(reader, folder); } catch {}
}

export function _loadDeferredAttachmentsIntoReader(reader, uid, folder, data, knownHasAttachments = false) {
  if (!reader || !uid || !data) return;
  const current = Array.isArray(data.attachments) ? data.attachments : [];
  const related = Array.isArray(data.related_attachments) ? data.related_attachments : [];
  if (!data.attachments_deferred && !(knownHasAttachments && !current.length && !related.length)) return;
  const body = reader.querySelector('.email-reader-body');
  if (!body) return;
  const loading = document.createElement('div');
  loading.className = 'email-reader-atts-wrap email-reader-atts-loading';
  loading.style.cssText = 'min-height:34px;display:flex;flex-direction:row;align-items:center;gap:7px;padding:6px 14px;color:var(--fg);font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;opacity:.7;';
  const loadingSpinner = spinnerModule.createWhirlpool(12);
  loadingSpinner.element.style.margin = '0';
  loading.appendChild(loadingSpinner.element);
  loading.appendChild(document.createTextNode('Loading attachments…'));
  const existingWrap = reader.querySelector('.email-reader-atts-wrap');
  if (existingWrap) existingWrap.replaceWith(loading);
  else body.insertAdjacentElement('beforebegin', loading);
  _ensureEmailAttachmentData(uid, folder, data, knownHasAttachments).then(fullData => {
    if (!reader.isConnected || !fullData) return;
    loading.remove();
    try { loadingSpinner.destroy(); } catch (_) {}
    const visibleCurrent = (fullData.attachments || []).filter(a => !_isLikelySignatureImage(a));
    const matchingEmail = (state._libEmails || []).find(em => (
      String(em.uid) === String(uid)
      && String(em.account_id || state._libAccountId || '') === String(reader.closest('[data-email-account]')?.dataset.emailAccount || state._libAccountId || '')
    ));
    if (matchingEmail) matchingEmail.has_attachments = visibleCurrent.length > 0;
    if (!visibleCurrent.length) {
      reader.closest('.doclib-card')?.querySelector('.email-card-attachment')?.remove();
    }
    const attsHtml = _buildAttsHtmlFor(uid, fullData);
    if (!attsHtml) return;
    const oldWrap = reader.querySelector('.email-reader-atts-wrap');
    if (oldWrap) {
      const tmp = document.createElement('div');
      tmp.innerHTML = attsHtml;
      oldWrap.replaceWith(tmp.firstElementChild);
    } else {
      body.insertAdjacentHTML('beforebegin', attsHtml);
    }
    _wireEmailAttachmentWrap(reader, folder);
  }).catch(() => {
    loading.remove();
    try { loadingSpinner.destroy(); } catch (_) {}
  });
}
