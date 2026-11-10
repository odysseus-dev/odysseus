/** Checklist rows, goal forms, keyboard input and item collection. */
export function createChecklist({
  _esc,
  _uid,
  spawnConfetti
}) {
  function _buildGoalHtml(note, items) {
    const desc = (note?.content || '').toString();
    return `
    <div class="note-form-goal">
      <textarea class="note-form-goal-desc" placeholder="Description (optional)" rows="3">${_esc(desc)}</textarea>
      ${_buildChecklistHtml(items)}
    </div>
  `;
  }

  function _wireGoalForm(form, container) {
    if (!container) return;
    // _wireHashtag is a closure inside _buildForm : out of scope here. Inline
    // the same behavior (type "#foo " in the description → tag added to the
    // form's label input) so editing a goal note doesn't ReferenceError.
    const desc = container.querySelector('.note-form-goal-desc');
    const labelInput = form?.querySelector('.note-form-label');
    if (desc && labelInput) {
      const tagRe = /(^|\s)#([A-Za-z0-9][\w-]*)\s$/;
      desc.addEventListener('input', () => {
        const m = tagRe.exec(desc.value);
        if (!m) return;
        const tag = m[2];
        // Same dedup-after-stripping fix as the plain note hashtag handler.
        const existing = labelInput.value.trim().split(/\s+/).filter(Boolean);
        const stripped = existing.map(t => t.replace(/^#+/, ''));
        if (!stripped.includes(tag)) {
          existing.push('#' + tag);
          labelInput.value = existing.join(' ');
          labelInput.classList.add('flash-once');
          setTimeout(() => labelInput.classList.remove('flash-once'), 600);
        }
        const cut = desc.value.length - m[0].length + m[1].length;
        desc.value = desc.value.slice(0, cut);
      });
    }
    // Always wire the checklist. The previous gate on a `note-form-goal-fresh`
    // class was dead code : nothing ever set that class, so the editor never
    // hooked up add/drag/Tab.
    _wireChecklist(container);
  }

  function _buildChecklistHtml(items) {
    let html = '<div class="note-checklist-inputs">';
    for (const item of items) {
      const indent = Math.min(item.indent || 0, 3);
      html += `<div class="note-cl-row${item.done ? ' done' : ''}" draggable="true" data-item-id="${item.id || _uid()}" data-indent="${indent}" style="padding-left:${indent * 16}px">
      <span class="note-cl-grip" title="Drag to reorder">⋮⋮</span>
      <span class="note-cl-dot"></span>
      <input type="text" class="note-cl-text" value="${_esc(item.text)}" placeholder="Item..." />
      <button type="button" class="note-cl-rm">&times;</button>
    </div>`;
    }
    // `type="button"` matters on mobile : without it some browsers treat
    // bare <button> as form-submit and the click handler never fires inside
    // certain containers. Also bumped tap target so fingers don't miss.
    html += `<button type="button" class="note-cl-add">+ Add</button></div>`;
    return html;
  }

  function _wireRow(row, container) {
    row.querySelector('.note-cl-rm')?.addEventListener('click', () => row.remove());
    row.querySelector('.note-cl-dot')?.addEventListener('click', () => {
      const wasDone = row.classList.contains('done');
      row.classList.toggle('done');
      // The custom dot has no native input/change event. Notify the form so
      // mobile edit mode switches its action from Archive back to Update.
      container.dispatchEvent(new Event('input', { bubbles: true }));
      const becameDone = !wasDone;  // we just flipped it
      const dot = row.querySelector('.note-cl-dot');
      const dRect = (dot || row).getBoundingClientRect();
      // Small confetti burst on each fresh check so the user gets a
      // "well done" beat per item, not just the grand-finale on all-done.
      if (becameDone) {
        spawnConfetti(dRect.left + dRect.width / 2, dRect.top + dRect.height / 2, 16);
      }
      // Bigger burst when the whole list is now done.
      const rows = [...container.querySelectorAll('.note-cl-row')];
      const hasText = rows.some(r => (r.querySelector('.note-cl-text')?.value || '').trim().length > 0);
      if (hasText && rows.every(r => r.classList.contains('done') || !(r.querySelector('.note-cl-text')?.value || '').trim())) {
        spawnConfetti(dRect.left + dRect.width / 2, dRect.top + dRect.height / 2, 60);
      }
    });
    const txt = row.querySelector('.note-cl-text');
    txt?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); container.querySelector('.note-cl-add')?.click(); }
      else if (e.key === 'Tab') {
        e.preventDefault();
        const cur = parseInt(row.dataset.indent || '0');
        const next = e.shiftKey ? Math.max(0, cur - 1) : Math.min(3, cur + 1);
        row.dataset.indent = String(next);
        row.style.paddingLeft = (next * 16) + 'px';
      } else if (e.key === 'Backspace' && txt.value === '') {
        e.preventDefault();
        const prev = row.previousElementSibling;
        row.remove();
        if (prev && prev.classList.contains('note-cl-row')) prev.querySelector('.note-cl-text')?.focus();
      }
    });
    // Drag handlers
    row.addEventListener('dragstart', (e) => {
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', row.dataset.itemId); } catch {}
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      container.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
    });
  }

  function _wireChecklist(container) {
    if (!container) return;
    // Delegate the + Add click off the container so re-renders + mobile
    // touch quirks don't leave the button dead. The previous direct
    // `addEventListener` on the button silently broke on mobile when
    // _wireChecklist ran more than once (or before the button was in DOM).
    if (!container._addDelegated) {
      container._addDelegated = true;
      container.addEventListener('click', (ev) => {
        const addBtn = ev.target.closest('.note-cl-add');
        if (!addBtn || !container.contains(addBtn)) return;
        ev.preventDefault();
        ev.stopPropagation();
        const inputs = container.querySelector('.note-checklist-inputs');
        if (!inputs) return;
        const row = document.createElement('div');
        row.className = 'note-cl-row';
        row.draggable = true;
        row.dataset.itemId = _uid();
        row.dataset.indent = '0';
        row.innerHTML = `<span class="note-cl-grip" title="Drag">⋮⋮</span><span class="note-cl-dot"></span><input type="text" class="note-cl-text" placeholder="Item..." /><button type="button" class="note-cl-rm">&times;</button>`;
        inputs.insertBefore(row, addBtn);
        row.querySelector('.note-cl-text').focus();
        _wireRow(row, container);
      });
    }
    container.querySelectorAll('.note-cl-row').forEach(row => _wireRow(row, container));

    // Drag-over handler on the inputs container
    const inputs = container.querySelector('.note-checklist-inputs');
    if (inputs) {
      inputs.addEventListener('dragover', (e) => {
        e.preventDefault();
        const dragging = inputs.querySelector('.note-cl-row.dragging');
        if (!dragging) return;
        inputs.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
        const rows = [...inputs.querySelectorAll('.note-cl-row:not(.dragging)')];
        const after = rows.find(r => {
          const box = r.getBoundingClientRect();
          return e.clientY < box.top + box.height / 2;
        });
        if (after) {
          after.classList.add('drop-before');
          inputs.insertBefore(dragging, after);
        } else if (rows.length) {
          rows[rows.length - 1].classList.add('drop-after');
          inputs.insertBefore(dragging, container.querySelector('.note-cl-add'));
        }
      });
      inputs.addEventListener('dragleave', (e) => {
        if (!inputs.contains(e.relatedTarget)) {
          inputs.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
        }
      });
    }
  }

  function _collectItems(form) {
    const items = [];
    form.querySelectorAll('.note-cl-row').forEach(row => {
      const text = row.querySelector('.note-cl-text')?.value?.trim();
      if (text) items.push({
        id: row.dataset.itemId || _uid(),
        text,
        done: row.classList.contains('done'),
        indent: parseInt(row.dataset.indent || '0'),
      });
    });
    return items;
  }

  return { _buildGoalHtml, _wireGoalForm, _buildChecklistHtml, _wireRow, _wireChecklist, _collectItems };
}
