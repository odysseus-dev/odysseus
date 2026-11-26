/** Inline images in the note window: S / M / L sizes and Remove from a small menu, and image files carried by a paste or a drop. */
import { imageWidthTitle } from '../domain/markdownSerializer.js';

const IMAGE_SIZES = [
  { label: 'S', name: 'Small', width: 33 },
  { label: 'M', name: 'Medium', width: 66 },
  { label: 'L', name: 'Full width', width: 100 },
];

/** Image files in a paste or drop, in order. Text and other files are left to the caller. */
export function imageFiles(transfer) {
  let files = Array.from(transfer?.files || []);
  if (!files.length) files = Array.from(transfer?.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
  return files.filter(file => /^image\//i.test(file.type || ''));
}

const imageWidth = img => {
  const width = parseInt(img.dataset.w, 10);
  return Number.isFinite(width) ? width : 100;
};

/** Size an image the way the renderer would show its markdown: `data-w` alone, which 15-notes.css sizes (notes keep no inline style). Full width carries no attribute, so it writes no title. */
export function setImageWidth(img, width) {
  const title = imageWidthTitle(width);
  img.style.removeProperty('width');
  if (!img.getAttribute('style')) img.removeAttribute('style');
  if (!title) {
    delete img.dataset.w;
    return;
  }
  img.dataset.w = title.slice(2);
}

/** Remove an image, and the paragraph it leaves with nothing in it. */
export function removeImage(img) {
  const parent = img.parentElement;
  missingPlaceholder(img)?.remove();
  img.remove();
  if (parent?.nodeName === 'P' && !parent.textContent.trim() && !parent.querySelector('img, hr')) parent.remove();
}

export function createImageMenu({ bindMenuDismiss, dismissOrRemove, portalZ = () => null }) {
  const closeAll = () => document.querySelectorAll('.note-image-menu').forEach(dismissOrRemove);

  /** Open beside the tap (`at`), or under the image; always fully on screen. */
  /** `keyboard`: opened from the keyboard, so focus moves into the menu and `returnFocus` gives it back when the menu closes. */
  function open(img, { at = null, onResize, onRemove, keyboard = false, returnFocus = () => {} }) {
    closeAll();
    const current = imageWidth(img);
    const menu = document.createElement('div');
    menu.className = 'note-reminder-menu note-image-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<div class="note-reminder-menu-title">Image size</div>
      <div class="note-image-menu-sizes">${IMAGE_SIZES.map(size => `<button type="button" class="note-reminder-menu-item${size.width === current ? ' active' : ''}" data-w="${size.width}" title="${size.name}" aria-label="${size.name}" aria-pressed="${size.width === current}"><span>${size.label}</span></button>`).join('')}</div>
      <div class="note-reminder-menu-divider"></div>
      <button type="button" class="note-reminder-menu-item" data-act="remove"><span>Remove</span></button>`;
    // Above the note window, whose z-index climbs with every bring-to-front.
    const z = portalZ();
    if (z) menu.style.zIndex = String(z);
    document.body.appendChild(menu);
    const rect = img.getBoundingClientRect();
    const x = at ? at.x : rect.left;
    const y = at ? at.y + 8 : rect.bottom + 4;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    const close = bindMenuDismiss(menu, () => {
      const hadFocus = menu.contains(document.activeElement);
      menu.remove();
      if (hadFocus) returnFocus();
    });
    menu.addEventListener('mousedown', event => event.preventDefault());
    // A menu: the arrow keys move between its items (Safari does not Tab to buttons).
    menu.addEventListener('keydown', event => {
      const items = Array.from(menu.querySelectorAll('button'));
      const at = items.indexOf(document.activeElement);
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (at < 0 || !step) return;
      event.preventDefault();
      items[(at + step + items.length) % items.length].focus();
    });
    if (keyboard) (menu.querySelector('[data-w].active') || menu.querySelector('button'))?.focus();
    menu.querySelectorAll('[data-w]').forEach(button => button.addEventListener('click', event => {
      event.stopPropagation();
      close();
      onResize(Number(button.dataset.w));
    }));
    menu.querySelector('[data-act="remove"]').addEventListener('click', event => {
      event.stopPropagation();
      close();
      onRemove();
    });
    return close;
  }

  return { open, closeAll };
}

// An image whose file is not on this server (a 404, an upload from another
// install) shows a placeholder with its description instead of a broken
// image. The <img> stays, hidden, so the note is written back unchanged and
// Remove still works; the placeholder itself is never written.
const NOTE_IMAGES = 'img.md-note-image, img.note-card-image';
const MISSING_IMAGE_CLASS = 'note-image-missing';
const MISSING_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>';

/** The placeholder shown for `img`, or null. */
export function missingPlaceholder(img) {
  const next = img?.nextElementSibling;
  return next && next.classList.contains(MISSING_IMAGE_CLASS) ? next : null;
}

/** Show the placeholder for an image that did not load. */
function showMissingImage(img) {
  if (!img?.isConnected || missingPlaceholder(img)) return;
  const alt = (img.getAttribute('alt') || '').trim();
  const box = document.createElement('span');
  box.className = MISSING_IMAGE_CLASS;
  box.setAttribute('data-md-skip', '');
  box.setAttribute('contenteditable', 'false');
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', alt ? `Image not found: ${alt}` : 'Image not found');
  box.innerHTML = MISSING_ICON;
  const label = document.createElement('span');
  label.textContent = alt || 'Image not found';
  box.appendChild(label);
  if (img.dataset.w) box.style.width = `${img.dataset.w}%`;
  img.classList.add(`${MISSING_IMAGE_CLASS}-src`);
  img.after(box);
}

/**
 * An upload written with another address of this server in front (see
 * `noteUploadElsewhere` in markdown.js) loaded from here: from now on it is
 * an ordinary upload, so the next save writes its path.
 */
function adoptLocalUpload(img) {
  if (!img.hasAttribute('data-md-upload')) return;
  for (const name of ['data-md-upload', 'data-md-image', 'data-md-alt', 'data-md-dest']) img.removeAttribute(name);
}

/** Watch every note image on the page: `error` and `load` do not bubble, so this listens in the capture phase. */
export function watchNoteImages(root = document) {
  root.addEventListener('error', event => {
    if (event.target?.matches?.(NOTE_IMAGES)) showMissingImage(event.target);
  }, true);
  root.addEventListener('load', event => {
    if (event.target?.matches?.('img.md-note-image[data-md-upload]')) adoptLocalUpload(event.target);
  }, true);
}
