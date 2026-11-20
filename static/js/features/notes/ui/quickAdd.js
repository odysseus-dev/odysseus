/** Quick-add bar: the start of a new note, which opens in the note window. */
export function createQuickAdd({
  openNewNote,
  openPanel,
  state
}) {
  function _renderQuickAdd(body) {
    const wrap = document.createElement('div');
    wrap.className = 'notes-quick-add';
    wrap.innerHTML = `
    <input type="text" class="notes-quick-input" placeholder="Add a note…" />
    <button class="notes-quick-icon" data-action="photo" title="Attach photo">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
  `;
    body.appendChild(wrap);

    const input = wrap.querySelector('.notes-quick-input');
    // A click on the input or the first keystroke opens a new note window,
    // carrying what was typed as its title; Enter there moves on to the body,
    // where the toolbar's Checklist makes it a to-do list. Focus alone (stolen
    // by a missed nearby click) opens nothing.
    const expand = () => {
      const text = input.value;
      input.value = '';
      openNewNote({ text });
    };
    input.addEventListener('click', expand);
    input.addEventListener('input', expand);
    wrap.querySelector('[data-action="photo"]').addEventListener('click', (e) => {
      e.stopPropagation();
      openNewNote({ text: '' });
      requestAnimationFrame(() => document.querySelector('.note-window-toolbar [data-md="image"]')?.click());
    });
  }

  function newNote() {
    if (!state.open) openPanel();
    requestAnimationFrame(() => openNewNote());
  }

  return { _renderQuickAdd, newNote };
}
