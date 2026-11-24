// frontend/src/features/documents/ui/richEditor/imageMenus.js
/**
 * Document Editor Rich-Text Subsystem: imageMenus
 */

import { API_BASE } from '../../application/documentState.js';
import { _escHtml, _markdownImageAlt } from '../../domain/documentKinds.js';
import { _showMdDropdown } from './formatMenus.js';
import { setImageSelectionObserver, _deleteRichImage, _richImageInsertRange, _selectRichImage, setRichImageInsertRange } from './imageOps.js';
import { _emailRichbodyActive, _scheduleEmailRichbodySave, _syncEmailRichbody } from './richBodySync.js';
import { _activeRichImage } from './richOperations.js';

let _richImageToolbar = null;

function _hideRichImageToolbar() {
    if (_richImageToolbar) _richImageToolbar.remove();
    _richImageToolbar = null;
  }

function _ensureRichImageToolbar(rich) {
    if (_richImageToolbar?.isConnected) {
      requestAnimationFrame(_richImageToolbar._position);
      return _richImageToolbar;
    }
    const toolbar = document.createElement('div');
    toolbar.className = 'doc-rich-image-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Image actions');
    toolbar.innerHTML = `
      <button type="button" class="doc-rich-image-edit" title="Edit image" aria-label="Edit image">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1-1-4 9.5-9.5z"></path></svg>
      </button>
      <button type="button" class="doc-rich-image-remove" title="Remove image" aria-label="Remove image">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>`;
    const position = () => {
      if (!_activeRichImage?.isConnected) return _hideRichImageToolbar();
      const rect = _activeRichImage.getBoundingClientRect();
      const width = toolbar.offsetWidth || 58;
      toolbar.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width))}px`;
      toolbar.style.top = `${Math.max(8, rect.top - toolbar.offsetHeight - 6)}px`;
    };
    toolbar._position = position;
    toolbar.addEventListener('pointerdown', event => event.preventDefault());
    toolbar.querySelector('.doc-rich-image-edit').addEventListener('click', event => {
      event.preventDefault();
      const imageButton = document.querySelector('[data-dd="image"]');
      if (imageButton) {
        imageButton.style.display = '';
        _showMdDropdown(imageButton);
        requestAnimationFrame(() => {
          const menu = document.querySelector('#doc-md-dd-menu[data-dd="image"]');
          if (!menu || !_activeRichImage?.isConnected) return;
          const imageRect = _activeRichImage.getBoundingClientRect();
          const menuRect = menu.getBoundingClientRect();
          menu.style.top = `${Math.max(8, imageRect.top - menuRect.height - 6)}px`;
          menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menuRect.width - 8, imageRect.left))}px`;
        });
      }
    });
    toolbar.querySelector('.doc-rich-image-remove').addEventListener('click', event => {
      event.preventDefault();
      if (_activeRichImage?.isConnected) _deleteRichImage(rich, _activeRichImage);
    });
    document.body.appendChild(toolbar);
    _richImageToolbar = toolbar;
    requestAnimationFrame(position);
    return toolbar;
  }

function _insertRichGalleryImage(rich, item) {
    const source = item?.url || item?.src || '';
    if (!rich || !source) return;
    const image = document.createElement('img');
    image.className = 'richtext-image';
    image.src = source.startsWith('http') ? source : `${API_BASE}${source}`;
    image.alt = _markdownImageAlt(item.filename || item.name || item.caption || 'Gallery image');
    let range = _richImageInsertRange;
    if (!range || !rich.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(rich);
      range.collapse(false);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const token = `doc-gallery-image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    image.dataset.editorImageToken = token;
    document.execCommand('insertHTML', false, `${image.outerHTML}<br><br>`);
    const inserted = rich.querySelector(`[data-editor-image-token="${token}"]`);
    if (inserted) {
      inserted.removeAttribute('data-editor-image-token');
      _selectRichImage(rich, inserted);
    }
    setRichImageInsertRange(null);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
  }

export function _showRichImageSourceMenu(anchor) {
    document.querySelector('.doc-rich-image-source-menu')?.remove();
    const rich = _emailRichbodyActive();
    if (!rich) return;
    const selection = window.getSelection();
    setRichImageInsertRange(null);
    if (selection?.rangeCount && rich.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      setRichImageInsertRange(selection.getRangeAt(0).cloneRange());
    }
    const menu = document.createElement('div');
    menu.className = 'doc-rich-image-source-menu';
    menu.innerHTML = `
      <div class="doc-rich-image-source-title">Add image</div>
      <button type="button" data-image-source="computer"><span aria-hidden="true">↑</span> Upload from computer</button>
      <button type="button" data-image-source="gallery"><span aria-hidden="true">▧</span> Choose from Gallery</button>`;
    document.body.appendChild(menu);
    const rect = anchor?.getBoundingClientRect?.() || { left: 12, bottom: 40 };
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menu.offsetWidth - 8, rect.left))}px`;
    menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 8, rect.bottom + 5)}px`;
    menu.querySelector('[data-image-source="computer"]').addEventListener('click', () => {
      menu.remove();
      document.getElementById('doc-md-image-input')?.click();
    });
    menu.querySelector('[data-image-source="gallery"]').addEventListener('click', async () => {
      menu.innerHTML = '<div class="doc-rich-image-source-title">Choose from Gallery</div><div class="doc-rich-gallery-list">Loading…</div>';
      try {
        const response = await fetch(`${API_BASE}/api/gallery/library?sort=recent&limit=30`, { credentials: 'same-origin' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        const items = Array.isArray(data?.items) ? data.items : Array.isArray(data?.images) ? data.images : [];
        const list = menu.querySelector('.doc-rich-gallery-list');
        if (!items.length) { list.textContent = 'No gallery images'; return; }
        list.replaceChildren(...items.map(item => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'doc-rich-gallery-item';
          button.innerHTML = `<span class="doc-rich-gallery-thumb"></span><span>${_escHtml(item.caption || item.filename || item.name || 'Gallery image')}</span>`;
          const thumb = button.querySelector('.doc-rich-gallery-thumb');
          const src = item.url || item.src || '';
          if (src) thumb.style.backgroundImage = `url("${src.startsWith('http') ? src : `${API_BASE}${src}`}" )`;
          button.addEventListener('click', () => { menu.remove(); _insertRichGalleryImage(rich, item); });
          return button;
        }));
        menu.style.top = `${Math.max(8, Math.min(window.innerHeight - menu.offsetHeight - 8, rect.bottom + 5))}px`;
      } catch (error) {
        menu.querySelector('.doc-rich-gallery-list').textContent = 'Could not load gallery';
        console.warn('Failed to load gallery images:', error);
      }
    });
    setTimeout(() => {
      const close = event => {
        if (!menu.contains(event.target) && event.target !== anchor) {
          menu.remove();
          document.removeEventListener('click', close, true);
        }
      };
      document.addEventListener('click', close, true);
    }, 0);
  }

setImageSelectionObserver((rich, image) => {
  if (!image) _hideRichImageToolbar();
  else _ensureRichImageToolbar(rich);
});
