import { topPortalZ, uiModule } from '../host.js';
// frontend/src/features/documents/ui/richEditor/links.js
/**
 * Document Editor Rich-Text Subsystem: links
 */

import { _normalizeRichLinkUrl } from '../../domain/richTextRules.js';
import { _deleteRichImage, _replaceRichImage, _replaceRichImageFigure } from './imageOps.js';
import { _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';
import { _selectedRichImage } from './richOperations.js';

function _promptLink({ text = '', url = '', editing = false } = {}) {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.id = 'doc-link-prompt-overlay';
      overlay.className = 'modal';
      overlay.style.zIndex = String(topPortalZ());
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box styled-prompt-box">' +
          `<div class="modal-header"><h4>${editing ? 'Edit link' : 'Insert link'}</h4></div>` +
          '<div class="modal-body">' +
            '<input type="text" id="doc-link-text" class="styled-prompt-input" placeholder="Link text (optional)" maxlength="500" />' +
            '<input type="url" id="doc-link-url" class="styled-prompt-input" placeholder="https://example.com" maxlength="2048" style="margin-top:8px;" />' +
          '</div>' +
          '<div class="modal-footer">' +
            (editing ? '<button id="doc-link-open" class="confirm-btn confirm-btn-secondary">Open</button>' : '') +
            (editing ? '<button id="doc-link-remove" class="confirm-btn confirm-btn-secondary doc-link-remove-btn">Remove</button>' : '') +
            '<span style="flex:1"></span>' +
            '<button id="doc-link-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>' +
            `<button id="doc-link-ok" class="confirm-btn confirm-btn-primary">${editing ? 'Save' : 'Insert'}</button>` +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
      const textEl = overlay.querySelector('#doc-link-text');
      const urlEl = overlay.querySelector('#doc-link-url');
      textEl.value = text;
      urlEl.value = url;
      function done(result) {
        overlay.remove();
        document.removeEventListener('keydown', onKey, true);
        resolve(result);
      }
      function submit() {
        const safeUrl = _normalizeRichLinkUrl(urlEl.value);
        if (!safeUrl) {
          urlEl.setAttribute('aria-invalid', 'true');
          urlEl.focus();
          urlEl.select();
          return;
        }
        done({ action: 'save', url: safeUrl, text: (textEl.value || '').trim() });
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      }
      overlay.querySelector('#doc-link-ok').addEventListener('click', submit);
      overlay.querySelector('#doc-link-cancel').addEventListener('click', () => done(null));
      overlay.querySelector('#doc-link-open')?.addEventListener('click', () => {
        const safeUrl = _normalizeRichLinkUrl(urlEl.value);
        if (!safeUrl) { urlEl.setAttribute('aria-invalid', 'true'); urlEl.focus(); return; }
        done({ action: 'open', url: safeUrl });
      });
      overlay.querySelector('#doc-link-remove')?.addEventListener('click', () => done({ action: 'remove' }));
      overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
      urlEl.addEventListener('input', () => urlEl.removeAttribute('aria-invalid'));
      urlEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
      textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); urlEl.focus(); } });
      document.addEventListener('keydown', onKey, true);
      requestAnimationFrame(() => {
        const target = editing || text ? urlEl : textEl;
        target.focus();
        if (editing) target.select();
      });
    });
  }

function _promptImageField({ overlayId, title, inputId, placeholder, cancelId, okId }, defaultValue = '') {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.id = overlayId;
      overlay.className = 'modal';
      overlay.style.zIndex = String(topPortalZ());
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box styled-prompt-box">' +
          `<div class="modal-header"><h4>${title}</h4></div>` +
          '<div class="modal-body">' +
            `<input type="text" id="${inputId}" class="styled-prompt-input" placeholder="${placeholder}" maxlength="500" />` +
          '</div>' +
          '<div class="modal-footer">' +
            `<button id="${cancelId}" class="confirm-btn confirm-btn-secondary">Cancel</button>` +
            `<button id="${okId}" class="confirm-btn confirm-btn-primary">Save</button>` +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
      const input = overlay.querySelector(`#${inputId}`);
      input.value = defaultValue || '';
      function done(result) {
        overlay.remove();
        document.removeEventListener('keydown', onKey, true);
        resolve(result);
      }
      function submit() { done((input.value || '').trim()); }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      }
      overlay.querySelector(`#${okId}`).addEventListener('click', submit);
      overlay.querySelector(`#${cancelId}`).addEventListener('click', () => done(null));
      overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
      document.addEventListener('keydown', onKey, true);
      requestAnimationFrame(() => { input.focus(); input.select(); });
    });
  }

function _promptImageAlt(defaultAlt = '') {
    return _promptImageField({
      overlayId: 'doc-image-alt-prompt-overlay',
      title: 'Image description',
      inputId: 'doc-image-alt-input',
      placeholder: 'Describe the image',
      cancelId: 'doc-image-alt-cancel',
      okId: 'doc-image-alt-ok',
    }, defaultAlt);
  }

function _promptImageCaption(defaultCaption = '') {
    return _promptImageField({
      overlayId: 'doc-image-caption-prompt-overlay',
      title: 'Image caption',
      inputId: 'doc-image-caption-input',
      placeholder: 'Caption shown below the image',
      cancelId: 'doc-image-caption-cancel',
      okId: 'doc-image-caption-ok',
    }, defaultCaption);
  }

export async function _editRichImageAlt(rich) {
    const image = _selectedRichImage(rich);
    if (!image) {
      uiModule?.showToast?.('Select an image first');
      return;
    }
    const alt = await _promptImageAlt(image.alt || '');
    if (alt === null || !image.isConnected) return;
    const clone = image.cloneNode(true);
    clone.alt = alt;
    _replaceRichImage(rich, image, clone);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
  }

export async function _editRichImageCaption(rich) {
    const image = _selectedRichImage(rich);
    if (!image) {
      uiModule?.showToast?.('Select an image first');
      return;
    }
    const existingFigure = image.closest('figure.richtext-image');
    const existingCaption = existingFigure?.querySelector(':scope > .richtext-image-caption, :scope > figcaption');
    const caption = await _promptImageCaption(existingCaption?.textContent || '');
    if (caption === null || !image.isConnected) return;

    const replacementImage = image.cloneNode(true);
    replacementImage.removeAttribute('data-editor-image-selected');
    let replacement = replacementImage;
    if (caption) {
      const figure = existingFigure?.cloneNode(true) || document.createElement('figure');
      figure.classList.add('richtext-image');
      const clonedImage = figure.querySelector('img');
      if (clonedImage) clonedImage.replaceWith(replacementImage);
      else figure.prepend(replacementImage);
      let captionElement = figure.querySelector(':scope > .richtext-image-caption, :scope > figcaption');
      if (!captionElement || captionElement.tagName === 'FIGCAPTION') {
        const stableCaption = document.createElement('div');
        stableCaption.className = 'richtext-image-caption';
        stableCaption.setAttribute('role', 'note');
        stableCaption.setAttribute('aria-label', 'Image caption');
        stableCaption.setAttribute('contenteditable', 'false');
        if (captionElement) captionElement.replaceWith(stableCaption);
        else figure.appendChild(stableCaption);
        captionElement = stableCaption;
      }
      captionElement.setAttribute('contenteditable', 'false');
      captionElement.textContent = caption;
      replacement = figure;
    }
    _replaceRichImageFigure(rich, image, replacement);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
  }

export function _applyRichImageAction(rich, action) {
    const image = _selectedRichImage(rich);
    if (!image) {
      uiModule?.showToast?.('Select an image first');
      return false;
    }
    if (action === 'image:delete') {
      _deleteRichImage(rich, image);
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      return true;
    }

    const clone = image.cloneNode(true);
    if (action.startsWith('image:size:')) {
      Array.from(clone.classList).forEach(name => {
        if (name.startsWith('richtext-image-size-')) clone.classList.remove(name);
      });
      const size = action.slice('image:size:'.length);
      if (size !== 'auto') clone.classList.add(`richtext-image-size-${size}`);
    } else if (action.startsWith('image:align:')) {
      Array.from(clone.classList).forEach(name => {
        if (name.startsWith('richtext-image-align-')) clone.classList.remove(name);
      });
      clone.classList.add(`richtext-image-align-${action.slice('image:align:'.length)}`);
    } else {
      return false;
    }
    _replaceRichImage(rich, image, clone);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
    return true;
  }

export function _richLinkAtRange(rich, range) {
    if (!rich || !range) return null;
    const closestLink = node => {
      const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
      const link = element?.closest?.('a[href]');
      return link && rich.contains(link) ? link : null;
    };
    const startLink = closestLink(range.startContainer);
    const endLink = closestLink(range.endContainer);
    return startLink && (!endLink || endLink === startLink) ? startLink : null;
  }

export function _restoreRichLinkRange(rich, range) {
    if (!range) return;
    try {
      rich.focus();
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } catch (_) {}
  }

export function _removeRichLink(rich, link) {
    if (!link?.isConnected || !rich.contains(link)) return false;
    const range = document.createRange();
    range.selectNode(link);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    rich.focus();
    return document.execCommand('insertHTML', false, link.innerHTML);
  }

export async function _wysiwygInsertLink(rich) {
    const selObj = window.getSelection();
    let savedRange = null;
    if (selObj && selObj.rangeCount) {
      const r = selObj.getRangeAt(0);
      if (rich.contains(r.commonAncestorContainer)) savedRange = r.cloneRange();
    }
    const existingLink = _richLinkAtRange(rich, savedRange);
    if (existingLink) {
      savedRange = document.createRange();
      savedRange.selectNode(existingLink);
    }
    const selText = existingLink?.textContent || (savedRange ? savedRange.toString() : '');
    let res;
    try {
      res = await _promptLink({
        text: selText,
        url: existingLink?.getAttribute('href') || '',
        editing: !!existingLink,
      });
    } catch (_) { res = null; }
    if (!res) { _restoreRichLinkRange(rich, savedRange); return; }
    if (res.action === 'open') {
      window.open(res.url, '_blank', 'noopener,noreferrer');
      _restoreRichLinkRange(rich, savedRange);
      return;
    }

    if (!savedRange) {
      savedRange = document.createRange();
      savedRange.selectNodeContents(rich);
      savedRange.collapse(false);
    }

    const replacementRange = savedRange.cloneRange();
    if (existingLink?.isConnected) replacementRange.selectNode(existingLink);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(replacementRange);
    rich.focus();

    if (res.action === 'remove') {
      if (!_removeRichLink(rich, existingLink)) return;
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
      rich._syncActive?.();
      return;
    }

    const url = _normalizeRichLinkUrl(res.url);
    if (!url) { _restoreRichLinkRange(rich, savedRange); return; }
    const linkText = (res.text || '').trim() || selText || url;
    const a = document.createElement('a');
    a.setAttribute('href', url);
    a.setAttribute('rel', 'noopener noreferrer');
    const preserveContents = existingLink
      ? linkText === (existingLink.textContent || '')
      : !!savedRange && linkText === selText;
    if (preserveContents) {
      const contents = existingLink
        ? existingLink.cloneNode(true)
        : savedRange.cloneContents();
      if (existingLink) a.append(...Array.from(contents.childNodes));
      else a.appendChild(contents);
    } else {
      a.textContent = linkText;
    }
    const token = `doc-link-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    a.dataset.editorLinkToken = token;
    if (!document.execCommand('insertHTML', false, a.outerHTML)) {
      _restoreRichLinkRange(rich, savedRange);
      return;
    }
    const inserted = rich.querySelector(`[data-editor-link-token="${token}"]`);
    if (!inserted) return;
    inserted.removeAttribute('data-editor-link-token');
    // Place the caret right after the inserted link.
    const after = document.createRange();
    after.setStartAfter(inserted);
    after.collapse(true);
    rich.focus();
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(after);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
    rich._syncActive?.();
  }
