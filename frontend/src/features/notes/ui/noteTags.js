/** Tag chips for the note window: remove with the chip's x, add through an input that accepts `#tag`, suggest tags used on other notes. */
const HIDDEN_TAGS = new Set(['reminder']);
// The chip's remove button: the same X the context strip and the card checklist use.
const REMOVE_ICON = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

export const parseTags = label => [...new Set(String(label || '').split(/\s+/).map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
const normaliseTag = raw => String(raw || '').trim().replace(/^#+/, '').replace(/[\s,]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);

export function createNoteTags({ esc, attrEsc, bindMenuDismiss, dismissOrRemove }) {
  function mount(container, { label = '', knownTags = () => [], onChange, onEscape = () => {} }) {
    // `reminder` is derived from the due date and never shown, but a note that
    // carries it in its label keeps it.
    let tags = parseTags(label);
    container.innerHTML = '<div class="note-tag-chips"></div><input type="text" class="note-tag-input note-window-tag-input" placeholder="Add tag" maxlength="40" spellcheck="false" autocomplete="off" aria-label="Add tag" />';
    const chips = container.querySelector('.note-tag-chips');
    const input = container.querySelector('input');
    let menu = null;

    const visible = () => tags.filter(t => !HIDDEN_TAGS.has(t));
    function render() {
      chips.innerHTML = visible().map(t => `<span class="note-tag-chip">#${esc(t)}<button type="button" class="note-tag-rm" data-tag="${attrEsc(t)}" title="Remove #${attrEsc(t)}" aria-label="Remove tag ${attrEsc(t)}">${REMOVE_ICON}</button></span>`).join('');
    }
    function commit(next) {
      tags = next;
      render();
      onChange(tags.join(' '));
    }
    function add(raw) {
      const tag = normaliseTag(raw);
      if (!tag || tags.includes(tag)) return false;
      commit([...tags, tag]);
      return true;
    }
    function closeMenu() { if (menu) { dismissOrRemove(menu); menu = null; } }
    function suggest() {
      const query = normaliseTag(input.value).toLowerCase();
      const options = [...new Set(knownTags())].filter(t => !HIDDEN_TAGS.has(t) && !tags.includes(t) && t.toLowerCase().startsWith(query)).sort().slice(0, 8);
      closeMenu();
      if (!options.length || document.activeElement !== input) return;
      menu = document.createElement('div');
      menu.className = 'note-reminder-menu note-tag-menu';
      menu.innerHTML = `<div class="note-reminder-menu-title">Existing tags</div>${options.map(t => `<button type="button" class="note-reminder-menu-item" data-tag="${attrEsc(t)}"><span>#${esc(t)}</span></button>`).join('')}`;
      document.body.appendChild(menu);
      const rect = input.getBoundingClientRect();
      const below = rect.bottom + 4 + menu.offsetHeight < window.innerHeight - 8;
      menu.style.top = `${below ? rect.bottom + 4 : Math.max(8, rect.top - menu.offsetHeight - 4)}px`;
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
      const own = menu;
      bindMenuDismiss(own, () => { own.remove(); if (menu === own) menu = null; }, event => event.target !== input && !own.contains(event.target));
      own.querySelectorAll('[data-tag]').forEach(button => {
        button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => { add(button.dataset.tag); input.value = ''; suggest(); });
      });
    }

    chips.addEventListener('click', event => {
      const rm = event.target.closest('.note-tag-rm');
      if (rm) commit(tags.filter(t => t !== rm.dataset.tag));
    });
    input.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Enter' || event.key === ',' || (event.key === ' ' && input.value.trim())) {
        event.preventDefault();
        if (add(input.value)) input.value = '';
        suggest();
      } else if (event.key === 'Backspace' && !input.value && visible().length) {
        commit(tags.filter(t => t !== visible().at(-1)));
      } else if (event.key === 'Escape') {
        // The suggestions first, then the window, like Escape in the title or body.
        event.preventDefault();
        if (menu) closeMenu(); else onEscape();
      }
    });
    input.addEventListener('input', suggest);
    input.addEventListener('focus', suggest);
    input.addEventListener('blur', () => { if (input.value.trim() && add(input.value)) input.value = ''; setTimeout(closeMenu, 120); });
    render();
    return {
      destroy: closeMenu,
      get tags() { return [...tags]; },
      /** A newer label from elsewhere. Ignored while the input is in use, so typing is never cut off. */
      set(next) {
        if (document.activeElement === input || input.value.trim()) return;
        const parsed = parseTags(next);
        if (parsed.join(' ') === tags.join(' ')) return;
        tags = parsed;
        render();
      },
    };
  }

  return { mount };
}
