/**
 * emojiPicker.js — Monochrome icon picker (no colored emojis).
 * Curated set of common icons as inline SVGs. The PICKER shows monochrome SVGs,
 * and — crucially — every character it INSERTS is one with a real monochrome
 * (text) presentation. On insert we append U+FE0E (VARIATION SELECTOR-15) so the
 * glyph renders flat/text, not as a system color emoji — so the RECIPIENT of an
 * email/message sees a non-colored symbol too, not just the sender. Pure-emoji
 * faces (😂, 👍, 😎) have no text form and are intentionally excluded.
 */

// Each entry: [char, label, svgPath OR svg]
// SVG icons matching Lucide style (24x24 viewBox, 2 stroke)
const I = (path) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

// Text variation selector — appended to chars that might render as color emoji,
// asks the browser to use text (monochrome) presentation if available.
const VS15 = '\uFE0E';

const EMOJI_GROUPS = [
  {
    name: 'Faces & Hearts',
    // Only chars with a genuine monochrome (text) presentation. VS15 is appended
    // on insert (see _insertEmoji) so they render flat for the recipient too.
    // Pure-emoji faces (grin/cry/sunglasses/thumbs) have no text form, so they're
    // omitted — there is no way to send them non-colored as plain text.
    items: [
      ['☻', 'grin', I('<circle cx="12" cy="12" r="10"/><path d="M7 14 C 7 18, 17 18, 17 14 Z"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>')],
      ['♡', 'heart-outline', I('<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>')],
      ['★', 'star', I('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" fill="currentColor" stroke="none"/>')],
      ['☆', 'star-outline', I('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>')],
      ['✦', 'sparkle', I('<polygon points="12 2 14 10 22 12 14 14 12 22 10 14 2 12 10 10" fill="currentColor" stroke="none"/>')],
      ['☽', 'moon', I('<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>')],
    ],
  },
  {
    name: 'Checks & Marks',
    items: [
      ['✓', 'check', I('<polyline points="20 6 9 17 4 12"/>')],
      ['✗', 'cross', I('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>')],
      ['✘', 'cross-heavy', I('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>')],
      ['★', 'star-filled', I('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>')],
      ['☆', 'star-empty', I('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>')],
      ['●', 'dot', I('<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>')],
      ['○', 'circle', I('<circle cx="12" cy="12" r="8"/>')],
      ['■', 'square-filled', I('<rect x="6" y="6" width="12" height="12" fill="currentColor" stroke="none"/>')],
      ['□', 'square-empty', I('<rect x="5" y="5" width="14" height="14"/>')],
      ['◆', 'diamond', I('<polygon points="12 3 21 12 12 21 3 12"/>')],
      ['◇', 'diamond-empty', I('<polygon points="12 3 21 12 12 21 3 12"/>')],
      ['†', 'dagger', I('<line x1="12" y1="4" x2="12" y2="20"/><line x1="8" y1="8" x2="16" y2="8"/>')],
    ],
  },
  {
    name: 'Arrows',
    items: [
      ['→', 'arrow-right', I('<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>')],
      ['←', 'arrow-left', I('<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>')],
      ['↑', 'arrow-up', I('<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>')],
      ['↓', 'arrow-down', I('<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>')],
      ['⇒', 'arrow-r-dbl', I('<polyline points="10 5 17 12 10 19"/><polyline points="6 5 13 12 6 19"/>')],
      ['⇐', 'arrow-l-dbl', I('<polyline points="14 5 7 12 14 19"/><polyline points="18 5 11 12 18 19"/>')],
    ],
  },
  {
    name: 'Math & Punctuation',
    items: [
      ['±', 'plus-minus', I('<line x1="4" y1="10" x2="20" y2="10"/><line x1="12" y1="2" x2="12" y2="18"/><line x1="4" y1="20" x2="20" y2="20"/>')],
      ['×', 'multiply', I('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>')],
      ['÷', 'divide', I('<circle cx="12" cy="6" r="1.5" fill="currentColor" stroke="none"/><line x1="5" y1="12" x2="19" y2="12"/><circle cx="12" cy="18" r="1.5" fill="currentColor" stroke="none"/>')],
      ['≈', 'approx', I('<path d="M4 9 C 6 6, 8 12, 10 9 S 14 6, 16 9 S 20 12, 22 9"/><path d="M4 15 C 6 12, 8 18, 10 15 S 14 12, 16 15 S 20 18, 22 15"/>')],
      ['≠', 'not-equal', I('<line x1="5" y1="9" x2="19" y2="9"/><line x1="5" y1="15" x2="19" y2="15"/><line x1="16" y1="5" x2="8" y2="19"/>')],
      ['≤', 'lte', I('<polyline points="17 5 7 11 17 17"/><line x1="7" y1="20" x2="17" y2="20"/>')],
      ['≥', 'gte', I('<polyline points="7 5 17 11 7 17"/><line x1="7" y1="20" x2="17" y2="20"/>')],
      ['∞', 'infinity', I('<path d="M18.178 8c5.096 0 5.096 8 0 8-5.095 0-7.133-8-12.739-8-4.585 0-4.585 8 0 8 5.606 0 7.644-8 12.739-8z"/>')],
      ['π', 'pi', I('<line x1="4" y1="8" x2="20" y2="8"/><line x1="9" y1="8" x2="9" y2="20"/><line x1="15" y1="8" x2="15" y2="20"/>')],
      ['Σ', 'sum', I('<polyline points="6 4 18 4 10 12 18 20 6 20"/>')],
      ['∆', 'delta', I('<polygon points="12 4 20 20 4 20"/>')],
      ['√', 'root', I('<polyline points="4 14 8 20 14 4 22 4"/>')],
      ['°', 'degree', I('<circle cx="12" cy="8" r="3"/>')],
      ['§', 'section', I('<path d="M14 6 a4 3 0 1 0 -4 4 q-3 0 -3 3 t3 3 q3 0 3 -3"/>')],
      ['¶', 'pilcrow', I('<path d="M16 4 H 9 a4 4 0 0 0 0 8 H 12 V 20"/><line x1="16" y1="4" x2="16" y2="20"/>')],
      ['•', 'bullet', I('<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>')],
      ['…', 'ellipsis', I('<circle cx="6" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.5" fill="currentColor" stroke="none"/>')],
      ['—', 'em-dash', I('<line x1="4" y1="12" x2="20" y2="12"/>')],
      ['«', 'quote-l', I('<polyline points="12 5 6 12 12 19"/><polyline points="18 5 12 12 18 19"/>')],
      ['»', 'quote-r', I('<polyline points="6 5 12 12 6 19"/><polyline points="12 5 18 12 12 19"/>')],
      ['"', 'quote-dbl', I('<line x1="8" y1="5" x2="8" y2="11"/><line x1="11" y1="5" x2="11" y2="11"/><line x1="13" y1="5" x2="13" y2="11"/><line x1="16" y1="5" x2="16" y2="11"/>')],
    ],
  },
  {
    name: 'Currency & Misc',
    items: [
      ['€', 'euro', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">€</text>')],
      ['£', 'pound', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">£</text>')],
      ['¥', 'yen', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">¥</text>')],
      ['$', 'dollar', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">$</text>')],
      ['¢', 'cent', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">¢</text>')],
      ['%', 'percent', I('<text x="12" y="16" font-size="16" text-anchor="middle" fill="currentColor" stroke="none">%</text>')],
      ['‰', 'per-mille', I('<text x="12" y="16" font-size="13" text-anchor="middle" fill="currentColor" stroke="none">‰</text>')],
      ['№', 'number', I('<text x="12" y="16" font-size="12" text-anchor="middle" fill="currentColor" stroke="none">№</text>')],
    ],
  },
];

let _pickerEl = null;
let _pickerOpenedAt = 0;
let _targetEl = null;
let _closeOnOutsideClick = null;
let _closeOnEscape = null;
// For contenteditable targets we snapshot the caret/selection when the picker
// opens, since focusing the picker's search box collapses the live selection.
let _savedRange = null;

// `target` may be a textarea element id (string) or a resolver function that
// returns the live target element — the latter lets a caller switch between a
// textarea and a contenteditable (e.g. plain markdown vs. WYSIWYG email).
export function createEmojiButton(target) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'emoji-picker-btn';
  btn.title = 'Insert icon';
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>';
  // Don't steal focus from the editor on press — keeps the caret/selection so
  // the emoji lands where the user was typing.
  btn.addEventListener('mousedown', (e) => e.preventDefault());
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const el = (typeof target === 'function') ? target() : document.getElementById(target);
    if (!el) return;
    togglePicker(btn, el);
  });
  return btn;
}

function togglePicker(anchor, target) {
  const now = Date.now();
  if (_pickerEl) {
    // Ignore the duplicate/ghost click mobile fires right after opening, which
    // would otherwise re-toggle the picker shut the instant it appears.
    if (now - _pickerOpenedAt < 400) return;
    _closePicker();
    return;
  }
  _targetEl = target;
  _savedRange = null;
  if (target.isContentEditable) {
    const sel = window.getSelection();
    if (sel && sel.rangeCount) {
      const r = sel.getRangeAt(0);
      if (target.contains(r.commonAncestorContainer)) _savedRange = r.cloneRange();
    }
  }
  _pickerEl = _buildPicker();
  _pickerOpenedAt = now;
  document.body.appendChild(_pickerEl);

  const rect = anchor.getBoundingClientRect();
  _pickerEl.style.position = 'fixed';
  _pickerEl.style.top = (rect.bottom + 4) + 'px';
  _pickerEl.style.left = rect.left + 'px';
  _pickerEl.style.zIndex = '10000';

  requestAnimationFrame(() => {
    const pr = _pickerEl.getBoundingClientRect();
    if (pr.right > window.innerWidth - 8) {
      _pickerEl.style.left = Math.max(8, window.innerWidth - pr.width - 8) + 'px';
    }
    // Always open downward. If it would run past the bottom, cap its height so
    // it scrolls internally instead of flipping up (which got cut off at top).
    const avail = window.innerHeight - rect.bottom - 12;
    if (pr.height > avail) {
      _pickerEl.style.maxHeight = Math.max(160, avail) + 'px';
    }
  });

  const close = (e) => {
    // Ignore the ghost/duplicate click mobile fires right after opening.
    if (e && e.type === 'click' && Date.now() - _pickerOpenedAt < 400) return;
    if (_pickerEl && !_pickerEl.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) {
      _closePicker();
    }
  };
  _closeOnOutsideClick = close;
  setTimeout(() => document.addEventListener('click', close, true), 10);

  _closeOnEscape = (e) => {
    if (e.key !== 'Escape' || !_pickerEl) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation?.();
    _closePicker();
  };
  document.addEventListener('keydown', _closeOnEscape, true);
}

function _closePicker() {
  if (_pickerEl) {
    _pickerEl.remove();
    _pickerEl = null;
  }
  if (_closeOnOutsideClick) {
    document.removeEventListener('click', _closeOnOutsideClick, true);
    _closeOnOutsideClick = null;
  }
  if (_closeOnEscape) {
    document.removeEventListener('keydown', _closeOnEscape, true);
    _closeOnEscape = null;
  }
}

function _buildPicker() {
  const el = document.createElement('div');
  el.className = 'emoji-picker';

  const search = document.createElement('input');
  search.type = 'text';
  search.placeholder = 'Search…';
  search.className = 'emoji-picker-search';
  el.appendChild(search);

  const groupsContainer = document.createElement('div');
  groupsContainer.className = 'emoji-picker-groups';
  el.appendChild(groupsContainer);

  function render(filter = '') {
    groupsContainer.innerHTML = '';
    const f = filter.toLowerCase();
    for (const group of EMOJI_GROUPS) {
      const filtered = f
        ? group.items.filter(item => item[1].toLowerCase().includes(f) || item[0].includes(filter))
        : group.items;
      if (filtered.length === 0) continue;

      const groupDiv = document.createElement('div');
      groupDiv.className = 'emoji-picker-group';
      const header = document.createElement('div');
      header.className = 'emoji-picker-group-name';
      header.textContent = group.name;
      groupDiv.appendChild(header);

      const grid = document.createElement('div');
      grid.className = 'emoji-picker-grid';
      for (const item of filtered) {
        const [char, label, svg] = item;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'emoji-picker-item';
        btn.title = label;
        btn.innerHTML = svg;
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          _insertEmoji(char);
          _closePicker();
        });
        grid.appendChild(btn);
      }
      groupDiv.appendChild(grid);
      groupsContainer.appendChild(groupDiv);
    }
  }

  render();

  search.addEventListener('input', () => render(search.value.trim()));
  setTimeout(() => search.focus(), 50);

  return el;
}

function _insertEmoji(char) {
  if (!_targetEl) return;
  // Force monochrome (text) presentation for the recipient by appending the
  // text variation selector U+FE0E. It only affects chars that *have* an emoji
  // presentation (e.g. ♥ ▶ ❤ ↩ ☀); for plain ASCII it's pointless, so we skip
  // those. This is why the inserted glyph is non-colored on the other end too,
  // not just in our own (already-SVG) picker UI.
  const cp = char.codePointAt(0);
  const ins = cp >= 0x80 ? char + VS15 : char;
  // Contenteditable (e.g. WYSIWYG email body) — insert at the saved caret.
  if (_targetEl.isContentEditable) {
    _targetEl.focus();
    let range = _savedRange;
    if (!range) {
      range = document.createRange();
      range.selectNodeContents(_targetEl);
      range.collapse(false);
    }
    range.deleteContents();
    const node = document.createTextNode(ins);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    _savedRange = range.cloneRange();
    _targetEl.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  const start = _targetEl.selectionStart || 0;
  const end = _targetEl.selectionEnd || 0;
  const before = _targetEl.value.substring(0, start);
  const after = _targetEl.value.substring(end);
  _targetEl.value = before + ins + after;
  _targetEl.selectionStart = _targetEl.selectionEnd = start + ins.length;
  _targetEl.focus();
  _targetEl.dispatchEvent(new Event('input', { bubbles: true }));
}

export default { createEmojiButton };
