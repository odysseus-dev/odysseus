// static/js/fileHandler.js

/**
 * File attachment and upload handling
 */

import uiModule from './ui.js';
import spinnerModule from './spinner.js';

let pendingFiles = [];
let uploaded = [];
// Holds the full meta (id/name/mime/size/width/height/…) from the most recent
// uploadPending() so callers can stamp width/height onto their attachment
// objects without changing uploadPending()'s return signature.
let _lastUploadedMeta = [];
let API_BASE = '';
let _uploadSpinners = [];
let _uploadAbortCtrl = null;
let _uploading = false;
let _lastUploadCancelled = false;
const _previewUrls = new WeakMap();

const MAX_FILES = 10;
const MAX_VISIBLE = 3;
let _expanded = false;

function _isMobileViewport() {
  return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
}

function _isCroppableImage(f) {
  const mime = (f?.type || '').toLowerCase();
  const name = (f?.name || '').toLowerCase();
  if (!(mime.startsWith('image/') || /\.(png|jpe?g|webp|bmp)$/i.test(name))) return false;
  return !mime.includes('svg') && !mime.includes('gif') && !/\.svg|\.gif$/i.test(name);
}

function _loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

function _canvasToBlob(canvas, type, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, type || 'image/png', quality));
}

async function _openMobileCropper(file) {
  const url = _getPreviewUrl(file);
  const imgProbe = await _loadImage(url);
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'attach-crop-overlay';
    overlay.innerHTML = `
      <div class="attach-crop-panel" role="dialog" aria-modal="true" aria-label="Crop image">
        <div class="attach-crop-stage">
          <img class="attach-crop-img" alt="">
          <div class="attach-crop-box"><span class="attach-crop-handle"></span></div>
        </div>
        <div class="attach-crop-actions">
          <button type="button" class="attach-crop-btn" data-action="cancel">Cancel</button>
          <button type="button" class="attach-crop-btn" data-action="original">Original</button>
          <button type="button" class="attach-crop-btn attach-crop-primary" data-action="crop">Use crop</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const img = overlay.querySelector('.attach-crop-img');
    const box = overlay.querySelector('.attach-crop-box');
    img.src = url;
    img.alt = file.name || 'image';

    let crop = { x: 0.08, y: 0.08, w: 0.84, h: 0.84 };
    let drag = null;

    function applyCrop() {
      const r = img.getBoundingClientRect();
      const pr = overlay.querySelector('.attach-crop-stage').getBoundingClientRect();
      box.style.left = (r.left - pr.left + crop.x * r.width) + 'px';
      box.style.top = (r.top - pr.top + crop.y * r.height) + 'px';
      box.style.width = (crop.w * r.width) + 'px';
      box.style.height = (crop.h * r.height) + 'px';
    }
    function clampCrop() {
      crop.w = Math.max(0.12, Math.min(1, crop.w));
      crop.h = Math.max(0.12, Math.min(1, crop.h));
      crop.x = Math.max(0, Math.min(1 - crop.w, crop.x));
      crop.y = Math.max(0, Math.min(1 - crop.h, crop.y));
    }
    function finish(value) {
      overlay.remove();
      window.removeEventListener('resize', applyCrop);
      resolve(value);
    }
    requestAnimationFrame(applyCrop);
    img.addEventListener('load', applyCrop);
    window.addEventListener('resize', applyCrop);

    box.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      box.setPointerCapture(e.pointerId);
      drag = {
        mode: e.target.classList.contains('attach-crop-handle') ? 'resize' : 'move',
        sx: e.clientX,
        sy: e.clientY,
        start: { ...crop },
      };
    });
    box.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const r = img.getBoundingClientRect();
      const dx = (e.clientX - drag.sx) / Math.max(1, r.width);
      const dy = (e.clientY - drag.sy) / Math.max(1, r.height);
      if (drag.mode === 'resize') {
        crop.w = drag.start.w + dx;
        crop.h = drag.start.h + dy;
      } else {
        crop.x = drag.start.x + dx;
        crop.y = drag.start.y + dy;
      }
      clampCrop();
      applyCrop();
    });
    box.addEventListener('pointerup', () => { drag = null; });
    box.addEventListener('pointercancel', () => { drag = null; });

    overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => finish(null));
    overlay.querySelector('[data-action="original"]').addEventListener('click', () => finish(file));
    overlay.querySelector('[data-action="crop"]').addEventListener('click', async () => {
      clampCrop();
      const canvas = document.createElement('canvas');
      const sx = Math.round(crop.x * imgProbe.naturalWidth);
      const sy = Math.round(crop.y * imgProbe.naturalHeight);
      const sw = Math.max(1, Math.round(crop.w * imgProbe.naturalWidth));
      const sh = Math.max(1, Math.round(crop.h * imgProbe.naturalHeight));
      canvas.width = sw;
      canvas.height = sh;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(imgProbe, sx, sy, sw, sh, 0, 0, sw, sh);
      const type = file.type && file.type !== 'image/bmp' ? file.type : 'image/png';
      const blob = await _canvasToBlob(canvas, type, 0.92);
      if (!blob) { finish(file); return; }
      const ext = type.includes('jpeg') ? 'jpg' : (type.split('/')[1] || 'png');
      const base = (file.name || 'image').replace(/\.[^.]+$/, '');
      finish(new File([blob], `${base}-cropped.${ext}`, { type, lastModified: Date.now() }));
    });
  });
}

function _getPreviewUrl(f) {
  if (!f) return '';
  let url = _previewUrls.get(f);
  if (!url) {
    url = URL.createObjectURL(f);
    _previewUrls.set(f, url);
  }
  return url;
}

function _revokePreviewUrl(f) {
  const url = _previewUrls.get(f);
  if (url) {
    try { URL.revokeObjectURL(url); } catch (_) {}
    _previewUrls.delete(f);
  }
}

/**
 * Initialize with dependencies
 */
export function init(apiBase) {
  API_BASE = apiBase;
}

/**
 * Open file picker dialog
 */
export function openPicker() {
  document.getElementById('file-input').click();
}

/**
 * Render the attachment strip with pending files.
 * 1-3 files: show individual chips.
 * 4+  files: collapse into a single "N files" badge (click to expand).
 */
export function renderAttachStrip() {
  const strip = document.getElementById('attach-strip');

  while (strip.firstChild) strip.removeChild(strip.firstChild);
  if (pendingFiles.length === 0) {
    _expanded = false;
    if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    return;
  }

  const total = pendingFiles.length;
  const collapsed = total > MAX_VISIBLE && !_expanded;

  if (collapsed) {
    // Single compact badge: "5 files ×"
    const badge = document.createElement('div');
    badge.className = 'thumb thumb-collapsed';
    const label = document.createElement('span');
    label.textContent = total + ' file' + (total > 1 ? 's' : '');
    label.className = 'thumb-collapsed-label';
    badge.appendChild(label);
    badge.title = pendingFiles.map(f => f.name || 'pasted-image').join('\n');
    badge.style.cursor = 'pointer';
    badge.addEventListener('click', (e) => {
      if (e.target.closest('.thumb-collapsed-x')) return;
      _expanded = true;
      renderAttachStrip();
    });
    const x = document.createElement('button');
    x.className = 'thumb-collapsed-x';
    x.textContent = '\u00d7';
    x.title = 'Remove all';
    x.addEventListener('click', (e) => { e.stopPropagation(); clearPending(); });
    badge.appendChild(x);
    strip.appendChild(badge);
  } else {
    // Show individual chips
    for (let idx = 0; idx < total; idx++) {
      strip.appendChild(_createChip(pendingFiles[idx], idx));
    }
  }
  if (window._updateSendBtnIcon) window._updateSendBtnIcon();
}

function _createChip(f, idx) {
  const chip = document.createElement('div');
  chip.className = 'thumb';
  const isImage = f.type?.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(f.name || '');
  if (isImage) {
    chip.classList.add('thumb-image');  // lets CSS overlay the remove-X on the corner (mobile)
    const img = document.createElement('img');
    img.className = 'thumb-img';
    img.src = _getPreviewUrl(f);
    img.alt = f.name || 'image';
    chip.appendChild(img);
  } else {
    const span = document.createElement('span');
    span.textContent = f.name || 'pasted-image';
    chip.appendChild(span);
  }
  const x = document.createElement('button');
  x.textContent = '\u00d7';
  x.setAttribute('aria-label', 'Remove attachment');
  x.addEventListener('click', (e) => { e.stopPropagation(); removePending(idx); });
  chip.appendChild(x);
  return chip;
}

/**
 * Remove a pending file by index
 */
export function removePending(idx) {
  if (_uploading) cancelUpload();
  _revokePreviewUrl(pendingFiles[idx]);
  pendingFiles.splice(idx, 1);
  renderAttachStrip();
}

/**
 * Upload all pending files to server
 */
export async function uploadPending(opts = {}) {
  if (pendingFiles.length === 0) return [];
  _lastUploadCancelled = false;

  // The message bubble is shown immediately, but the upload can take a moment —
  // dim the chips and overlay a whirlpool so it's clear the files are still
  // being sent (and aren't stuck). Cleared in the finally below.
  const strip = document.getElementById('attach-strip');
  if (strip) {
    strip.classList.add('attach-uploading');
    // Put a whirlpool ON each attachment chip (image/doc) so the spinner sits on
    // the thing being uploaded, not floating over the whole strip.
    strip.querySelectorAll('.thumb').forEach(chip => {
      try {
        const sp = spinnerModule.create('', 'clean', 'whirlpool');
        const ov = document.createElement('span');
        ov.className = 'thumb-upload-spinner';
        ov.appendChild(sp.createElement());
        chip.appendChild(ov);
        sp.start();
        _uploadSpinners.push(sp);
      } catch (_) { /* spinner is best-effort */ }
    });
  }

  const fd = new FormData();
  pendingFiles.forEach(f => fd.append('files', f, f.name || 'paste.png'));
  if (opts.sessionId) fd.append('session_id', opts.sessionId);
  _uploadAbortCtrl = new AbortController();
  _uploading = true;
  const timeoutId = setTimeout(() => {
    if (_uploadAbortCtrl && !_uploadAbortCtrl.signal.aborted) {
      try { _uploadAbortCtrl.abort(); } catch (_) {}
    }
  }, 120000);

  try {
    const res = await fetch(`${API_BASE}/api/upload`, {
      method: 'POST',
      body: fd,
      signal: _uploadAbortCtrl.signal,
    });
    if (!res.ok) {
      // Surface the failure instead of swallowing it. Previously a non-OK
      // response (e.g. 429 rate limit, 413 too large) was ignored: the files
      // silently vanished and the chat sent with no attachments, so the model
      // "didn't even see them" (issue #1346). Show the server's reason and keep
      // pendingFiles so the strip re-renders for a retry (see finally below).
      let detail = '';
      try { const e = await res.json(); detail = e.detail || e.error || ''; } catch (_) {}
      _showToast('Upload failed' + (detail ? ': ' + detail : ` (HTTP ${res.status})`));
      return [];
    }
    const data = await res.json();
    uploaded = (data.files || []);
    if (uploaded.some(x => x && x.gallery_id)) {
      try { localStorage.setItem('gallery-fresh-chat-upload', String(Date.now())); } catch (_) {}
      window.dispatchEvent(new CustomEvent('gallery-refresh', { detail: { source: 'chat-upload' } }));
    }
    pendingFiles = [];          // clear only on success
    // Stash the full meta (incl. width/height for images) on the module so
    // callers that want it can grab it via getLastUploadedMeta(). Keep the
    // returned shape as `ids` for backward-compatibility with existing call sites.
    _lastUploadedMeta = uploaded;
    return uploaded.map(x => x.id);
  } catch (e) {
    if (e && e.name === 'AbortError') {
      _lastUploadCancelled = true;
      _showToast('Upload cancelled');
      return [];
    }
    _showToast('Upload failed: ' + (e?.message || 'network error'));
    return [];
  } finally {
    clearTimeout(timeoutId);
    _uploading = false;
    _uploadAbortCtrl = null;
    _uploadSpinners.forEach(sp => { try { sp.stop && sp.stop(); } catch (_) {} });
    _uploadSpinners = [];
    if (strip) strip.classList.remove('attach-uploading');
    // Re-render: empty on success (chips gone), or restored on error so the
    // user can retry — and either way the spinners are removed.
    renderAttachStrip();
  }
}

/**
 * Add files to pending list (capped at MAX_FILES)
 */
export async function addFiles(files) {
  for (const f of files) {
    if (pendingFiles.length >= MAX_FILES) {
      _showToast(`Max ${MAX_FILES} files allowed`);
      break;
    }
    let nextFile = f;
    if (_isMobileViewport() && _isCroppableImage(f)) {
      try {
        nextFile = await _openMobileCropper(f);
      } catch (_) {
        nextFile = f;
      }
      if (!nextFile) continue;
    }
    pendingFiles.push(nextFile);
  }
  renderAttachStrip();
}

export async function cropForMobileUpload(file) {
  if (!_isMobileViewport() || !_isCroppableImage(file)) return file;
  try {
    return await _openMobileCropper(file);
  } catch (_) {
    return file;
  }
}

function _showToast(msg) {
  if (window.showToast) { window.showToast(msg); return; }
  // Fallback inline toast
  let t = document.getElementById('_attach-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = '_attach-toast';
    t.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--panel);border:1px solid var(--red);color:var(--red);padding:6px 14px;border-radius:6px;font-size:13px;z-index:9999;opacity:0;transition:opacity .3s';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = '1';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.opacity = '0'; }, 2500);
}

/**
 * Get pending files count
 */
export function getPendingCount() {
  return pendingFiles.length;
}

/**
 * Get raw pending File objects (for reading content before upload clears them)
 */
export function getPendingRaw() {
  return [...pendingFiles];
}

/**
 * Get pending file metadata (name, size, type) for display
 */
export function getPendingInfo() {
  return pendingFiles.map(f => {
    const isImage = f.type?.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(f.name || '');
    return {
      name: f.name || 'pasted-image',
      size: f.size || 0,
      mime: f.type || '',
      previewUrl: isImage ? _getPreviewUrl(f) : '',
    };
  });
}

/**
 * Clear all pending files
 */
export function clearPending() {
  if (_uploading) cancelUpload();
  pendingFiles.forEach(_revokePreviewUrl);
  pendingFiles = [];
  renderAttachStrip();
}

/** Full meta (incl. width/height for images) from the most recent uploadPending(). */
export function getLastUploadedMeta() {
  return _lastUploadedMeta;
}

export function isUploading() {
  return _uploading;
}

export function wasLastUploadCancelled() {
  return _lastUploadCancelled;
}

export function cancelUpload() {
  _lastUploadCancelled = true;
  if (_uploadAbortCtrl && !_uploadAbortCtrl.signal.aborted) {
    try { _uploadAbortCtrl.abort(); } catch (_) {}
  }
}

var escapeHtml = uiModule.esc;

const fileHandlerModule = {
  init,
  openPicker,
  renderAttachStrip,
  removePending,
  uploadPending,
  addFiles,
  cropForMobileUpload,
  getPendingCount,
  getPendingInfo,
  getPendingRaw,
  clearPending,
  getLastUploadedMeta,
  isUploading,
  wasLastUploadCancelled,
  cancelUpload,
};

export default fileHandlerModule;
