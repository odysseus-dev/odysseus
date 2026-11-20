// frontend/src/features/documents/ui/richEditor/imageOps.js
/**
 * Document Editor Rich-Text Subsystem: imageOps
 */

import { _markdownImageAlt } from '../../domain/documentKinds.js';
import { _emailRichbodyActive, _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';
import { _activeRichImage, setActiveRichImage } from './richOperations.js';

let imageSelectionObserver = null;

export function setImageSelectionObserver(observer) {
  imageSelectionObserver = observer;
}

export let _richImageInsertRange = null;

export function setRichImageInsertRange(range) {
  _richImageInsertRange = range;
}

export function _insertRichTextImages(uploadedFiles) {
    const rich = _emailRichbodyActive();
    const files = Array.isArray(uploadedFiles) ? uploadedFiles : [];
    if (!rich || !files.length) return;

    const images = [];
    for (const file of files) {
      const id = encodeURIComponent(file.id || file.file_id || '');
      if (!id) continue;
      const img = document.createElement('img');
      img.className = 'richtext-image';
      img.src = `/api/upload/${id}`;
      img.alt = _markdownImageAlt(file.name || file.filename);
      images.push(img);
    }
    if (!images.length) return;

    let range = _richImageInsertRange;
    if (!range || !rich.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(rich);
      range.collapse(false);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const token = `doc-image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    images[images.length - 1].dataset.editorImageToken = token;
    const imageHtml = images.map(image => image.outerHTML);
    const markup = rich.classList.contains('markdown-visual-mode')
      ? imageHtml.map(html => '<p>' + html + '</p>').join('') + '<p><br></p>'
      : imageHtml.join('<br>') + '<br><br>';
    document.execCommand('insertHTML', false, markup);
    const inserted = rich.querySelector(`[data-editor-image-token="${token}"]`);
    if (inserted) {
      inserted.removeAttribute('data-editor-image-token');
      _selectRichImage(rich, inserted);
    }
    _richImageInsertRange = null;
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
  }

export function _normalizeRichTextImages(root) {
    root.querySelectorAll('figure.richtext-image').forEach(figure => {
      const image = figure.querySelector('img');
      if (!image) return;
      image.classList.add('richtext-image');
      const legacyCaption = figure.querySelector(':scope > figcaption');
      if (legacyCaption) {
        const caption = document.createElement('div');
        caption.className = 'richtext-image-caption';
        caption.setAttribute('role', 'note');
        caption.setAttribute('aria-label', 'Image caption');
        caption.setAttribute('contenteditable', 'false');
        caption.innerHTML = legacyCaption.innerHTML;
        legacyCaption.replaceWith(caption);
      }
      const caption = figure.querySelector(':scope > .richtext-image-caption');
      if (caption) {
        caption.setAttribute('role', 'note');
        caption.setAttribute('aria-label', 'Image caption');
        caption.setAttribute('contenteditable', 'false');
      }
      Array.from(figure.classList).forEach(name => {
        if (name.startsWith('richtext-image-size-') || name.startsWith('richtext-image-align-')) {
          image.classList.add(name);
          figure.classList.remove(name);
        }
      });
      if (figure.hasAttribute('data-editor-image-selected')) {
        figure.removeAttribute('data-editor-image-selected');
        image.dataset.editorImageSelected = 'true';
      }
    });
  }

export function _clearRichImageSelection() {
    imageSelectionObserver?.(null, null);
    if (_activeRichImage?.isConnected) {
      _activeRichImage.removeAttribute('data-editor-image-selected');
    }
    document.querySelectorAll('#doc-email-richbody [data-editor-image-selected]').forEach(image => {
      image.removeAttribute('data-editor-image-selected');
    });
    setActiveRichImage(null);
    const imageButton = document.querySelector('[data-dd="image"]');
    if (imageButton) {
      imageButton.style.display = 'none';
      imageButton.classList.remove('is-active');
      imageButton.disabled = true;
      imageButton.setAttribute('aria-disabled', 'true');
    }
  }

export function _selectRichImage(rich, image) {
    if (!image || !rich.contains(image)) {
      _clearRichImageSelection();
      return;
    }
    _clearRichImageSelection();
    setActiveRichImage(image);
    image.dataset.editorImageSelected = 'true';
    const imageButton = document.querySelector('[data-dd="image"]');
    if (imageButton) {
      imageButton.style.display = '';
      imageButton.classList.add('is-active');
      imageButton.disabled = false;
      imageButton.setAttribute('aria-disabled', 'false');
    }
    imageSelectionObserver?.(rich, image);
    rich.focus({ preventScroll: true });
    const range = document.createRange();
    range.selectNode(image);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

export function _replaceRichImage(rich, original, replacement) {
    const figure = original.closest('figure.richtext-image');
    if (figure) {
      const figureClone = figure.cloneNode(true);
      const clonedImage = figureClone.querySelector('img.richtext-image, img');
      if (!clonedImage) return null;
      clonedImage.replaceWith(replacement);
      return _replaceRichImageFigure(rich, original, figureClone);
    }
    const token = `doc-image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    replacement.dataset.editorImageToken = token;
    replacement.removeAttribute('data-editor-image-selected');
    const range = document.createRange();
    range.selectNode(original);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertHTML', false, replacement.outerHTML);
    const inserted = rich.querySelector(`[data-editor-image-token="${token}"]`);
    if (!inserted) return null;
    inserted.removeAttribute('data-editor-image-token');
    _selectRichImage(rich, inserted);
    return inserted;
  }

export function _replaceRichImageFigure(rich, image, replacement) {
    const original = image.closest('figure.richtext-image') || image;
    const token = `doc-image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    replacement.dataset.editorImageToken = token;
    replacement.removeAttribute('data-editor-image-selected');
    replacement.querySelectorAll?.('[data-editor-image-selected]').forEach(item => {
      item.removeAttribute('data-editor-image-selected');
    });
    const range = document.createRange();
    range.selectNode(original);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    if (!document.execCommand('insertHTML', false, replacement.outerHTML)) return null;
    const inserted = rich.querySelector(`[data-editor-image-token="${token}"]`);
    if (!inserted) return null;
    inserted.removeAttribute('data-editor-image-token');
    const insertedImage = inserted.matches('img') ? inserted : inserted.querySelector('img.richtext-image, img');
    if (insertedImage) _selectRichImage(rich, insertedImage);
    return insertedImage;
  }

export function _deleteRichImage(rich, image) {
    const range = document.createRange();
    range.selectNode(image);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('delete');
    _clearRichImageSelection();
    rich.focus();
  }
