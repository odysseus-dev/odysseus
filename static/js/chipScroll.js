// Shared horizontal scrolling for filter/tag chip rows.

const CHIP_STRIP_SELECTOR = [
  '.skills-summary-strip',
  '#memory-category-filters',
  '.memory-category-filters:has(> .memory-cat-chip)',
  '.doclib-lang-chips',
  '.doclib-chips',
  '.notes-labels-bar',
  '.tasks-activity-filters',
  '.gallery-tag-chips',
  '.gallery-album-chips',
  '.gallery-ai-tags',
  '.cal-filters',
].join(',');

function initChipStrip(strip) {
  if (!strip || strip.dataset.chipScrollBound === '1') return;
  const parent = strip.parentElement;
  if (!parent) return;

  strip.dataset.chipScrollBound = '1';
  strip.classList.add('chip-scroll-strip');

  const frame = document.createElement('div');
  frame.className = 'doclib-chip-scroll-frame';
  parent.insertBefore(frame, strip);
  frame.appendChild(strip);

  const makeArrow = (direction, label) => {
    const button = document.createElement('button');
    const glyph = document.createElement('span');
    button.type = 'button';
    button.className = `doclib-chip-scroll-arrow ${direction}`;
    button.setAttribute('aria-label', label);
    button.title = label;
    glyph.className = 'doclib-chip-scroll-arrow-glyph';
    glyph.textContent = direction === 'left' ? '\u2039' : '\u203a';
    button.appendChild(glyph);
    button.addEventListener('click', () => {
      strip.scrollBy({ left: direction === 'left' ? -180 : 180, behavior: 'smooth' });
    });
    frame.appendChild(button);
    return button;
  };

  const left = makeArrow('left', 'Show previous tags');
  const right = makeArrow('right', 'Show more tags');
  let pointerId = null;
  let startX = 0;
  let startScroll = 0;
  let dragging = false;
  let suppressClick = false;

  const sync = () => {
    const stripHidden = strip.hidden || getComputedStyle(strip).display === 'none';
    frame.style.display = stripHidden ? 'none' : '';
    if (stripHidden) {
      frame.classList.remove('has-overflow');
      left.hidden = true;
      right.hidden = true;
      return;
    }
    const max = Math.max(0, strip.scrollWidth - strip.clientWidth);
    frame.classList.toggle('has-overflow', max > 1);
    left.hidden = max <= 1 || strip.scrollLeft <= 1;
    right.hidden = max <= 1 || strip.scrollLeft >= max - 1;
  };

  strip.addEventListener('scroll', sync, { passive: true });
  strip.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startScroll = strip.scrollLeft;
    dragging = false;
    suppressClick = false;
  });
  strip.addEventListener('pointermove', (event) => {
    if (pointerId !== event.pointerId) return;
    const delta = event.clientX - startX;
    if (!dragging && Math.abs(delta) <= 4) return;
    if (!dragging) {
      dragging = true;
      suppressClick = true;
      strip.classList.add('is-pointer-dragging');
      strip.setPointerCapture?.(event.pointerId);
    }
    if (event.cancelable) event.preventDefault();
    strip.scrollLeft = startScroll - delta;
  });
  const stopDragging = (event) => {
    if (pointerId !== event.pointerId) return;
    if (dragging && strip.hasPointerCapture?.(event.pointerId)) {
      strip.releasePointerCapture(event.pointerId);
    }
    pointerId = null;
    dragging = false;
    strip.classList.remove('is-pointer-dragging');
    sync();
  };
  strip.addEventListener('pointerup', stopDragging);
  strip.addEventListener('pointercancel', stopDragging);
  strip.addEventListener('click', (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopPropagation();
    suppressClick = false;
  }, true);

  if (typeof ResizeObserver === 'function') new ResizeObserver(sync).observe(strip);
  new MutationObserver(sync).observe(strip, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'hidden', 'style'],
  });
  requestAnimationFrame(sync);
}

function scanChipStrips(root = document) {
  if (root.nodeType === Node.ELEMENT_NODE && root.matches?.(CHIP_STRIP_SELECTOR)) initChipStrip(root);
  root.querySelectorAll?.(CHIP_STRIP_SELECTOR).forEach(initChipStrip);
}

export function initChipScrollRows() {
  if (window._chipScrollRowsBound) return;
  window._chipScrollRowsBound = true;

  ['touchstart', 'touchmove'].forEach(type => {
    document.addEventListener(type, event => {
      if (event.target.closest?.('.chip-scroll-strip')) event.stopPropagation();
    }, true);
  });

  const start = () => {
    scanChipStrips();
    new MutationObserver(records => {
      for (const record of records) {
        record.addedNodes.forEach(node => {
          if (node.nodeType === Node.ELEMENT_NODE) scanChipStrips(node);
        });
      }
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
}

initChipScrollRows();
