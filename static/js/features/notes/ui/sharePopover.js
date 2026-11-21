/** The owner's share popover: View / Edit for the ones the note is shared with, and a switch per account. An admin gets every other account; anyone else types an exact username to add someone. Each change is saved at once; the new roster goes back through onChange. */
const SHARE_MENU_CLASS = 'note-share-menu';
// The server's answer to a name with no account (core/notes_sharing.py).
const NO_SUCH_ACCOUNT = 'No account with that name';

export function createSharePopover({ api, esc, bindMenuDismiss, dismissOrRemove, portalZ = () => null }) {
  let openMenu = null;

  function close() {
    if (openMenu) dismissOrRemove(openMenu);
    openMenu = null;
  }

  function place(menu, anchor) {
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
  }

  function open(anchor, noteId, { onChange = () => {}, onError = () => {} } = {}) {
    close();
    const menu = document.createElement('div');
    menu.className = `note-reminder-menu ${SHARE_MENU_CLASS}`;
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', 'Share note');
    const z = portalZ();
    if (z) menu.style.zIndex = String(z);
    document.body.appendChild(menu);
    openMenu = menu;
    // Keyboard users land in the popover and go back to Share when it closes,
    // unless they already moved on to something else.
    bindMenuDismiss(menu, () => {
      const active = document.activeElement;
      const returnFocus = !active || active === document.body || menu.contains(active);
      menu.remove();
      if (openMenu === menu) openMenu = null;
      if (returnFocus && anchor.isConnected) anchor.focus();
    });
    const focusables = () => Array.from(menu.querySelectorAll('input, button'));
    // Tab stays inside the popover while it is open.
    menu.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (!items.length) return;
      const at = items.indexOf(document.activeElement);
      const next = event.shiftKey ? (at <= 0 ? items.length - 1 : at - 1) : (at < 0 || at === items.length - 1 ? 0 : at + 1);
      event.preventDefault();
      items[next].focus();
    });
    // Each change redraws the popover; focus goes back to the same control.
    const focusKey = () => {
      const active = document.activeElement;
      if (!menu.contains(active)) return null;
      return { user: active.dataset.user || null, perm: active.dataset.perm || null, act: active.dataset.act || null };
    };
    let pendingFocus = { first: true };
    function restoreFocus() {
      const key = pendingFocus;
      pendingFocus = null;
      if (!key) return;
      const items = focusables();
      const same = items.find(el => !key.first && (el.dataset.user || null) === key.user && (el.dataset.perm || null) === key.perm && (el.dataset.act || null) === key.act);
      (same || items[0] || menu).focus();
    }
    menu.tabIndex = -1;

    let users = [];
    // Only an admin gets the account list; everyone else types a name.
    let directory = true;
    let draft = '';
    let addError = '';
    let roster = new Map();
    let confirmed = new Map();
    const queue = [];
    let busy = false;

    function status(message, retry = false) {
      pendingFocus = focusKey() || pendingFocus;
      menu.innerHTML = `<div class="note-reminder-menu-title">Share</div><div class="note-share-status">${esc(message)}</div>${retry ? '<button type="button" class="note-reminder-menu-item" data-act="retry"><span>Try again</span></button>' : ''}`;
      menu.querySelector('[data-act="retry"]')?.addEventListener('click', load);
      place(menu, anchor);
      // "Loading…" has nothing to focus yet; keep the request for the list.
      if (pendingFocus && focusables().length) restoreFocus();
      else if (pendingFocus?.first) menu.focus();
    }

    function addField() {
      if (directory) return '';
      return `<div class="note-reminder-menu-picker"><input type="text" class="note-reminder-date-input note-share-name" data-act="name" placeholder="Username" aria-label="Username to share with" autocomplete="off" autocapitalize="none" spellcheck="false" /></div>
        ${addError ? `<div class="note-share-status" role="alert">${esc(addError)}</div>` : ''}
        <button type="button" class="note-reminder-menu-item note-reminder-menu-confirm" data-act="add"${draft.trim() ? '' : ' disabled'}><span>Share</span></button>`;
    }

    function render() {
      if (!users.length && directory) { status('No other users on this instance.'); return; }
      pendingFocus = focusKey() || pendingFocus;
      menu.innerHTML = `<div class="note-reminder-menu-title">Share</div>${users.map(name => {
        const perm = roster.get(name) || '';
        const pill = value => `<button type="button" class="note-share-pill${perm === value ? ' active' : ''}" data-user="${esc(name)}" data-perm="${value}" aria-pressed="${perm === value}">${value === 'edit' ? 'Edit' : 'View'}</button>`;
        return `<div class="note-share-row${perm ? ' shared' : ''}">
          <label class="note-share-user"><input type="checkbox" class="memory-select-cb" data-user="${esc(name)}"${perm ? ' checked' : ''} /><span>${esc(name)}</span></label>
          ${perm ? `<span class="note-share-pills">${pill('view')}${pill('edit')}</span>` : ''}
        </div>`;
      }).join('')}${addField()}`;
      menu.querySelectorAll('input[data-user]').forEach(box => box.addEventListener('change', () => {
        const name = box.dataset.user;
        apply(() => (box.checked ? api.share(noteId, [name], 'view') : api.unshare(noteId, name).then(() => null)), name, box.checked ? 'view' : '');
      }));
      menu.querySelectorAll('.note-share-pill').forEach(pill => pill.addEventListener('click', () => {
        if (pill.classList.contains('active')) return;
        apply(() => api.share(noteId, [pill.dataset.user], pill.dataset.perm), pill.dataset.user, pill.dataset.perm);
      }));
      const nameInput = menu.querySelector('input[data-act="name"]');
      const addButton = menu.querySelector('[data-act="add"]');
      // Set as a property: the typed text never goes through the markup.
      if (nameInput) nameInput.value = draft;
      nameInput?.addEventListener('input', () => {
        draft = nameInput.value;
        addButton.disabled = !draft.trim();
        if (addError) { addError = ''; menu.querySelector('.note-share-status[role="alert"]')?.remove(); }
      });
      nameInput?.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); add(); }
      });
      addButton?.addEventListener('click', add);
      place(menu, anchor);
      restoreFocus();
    }

    // A typed name is shared as View once the server finds the account. It is
    // not drawn before that: until then nobody knows whether it exists, or
    // how the account spells it.
    function add() {
      const typed = draft.trim();
      // A second Enter on the same name while it is still on its way is the same request.
      if (!typed || queue.some(step => step.adds === typed.toLowerCase())) return;
      addError = '';
      // The field keeps focus, ready for the next name; the button it was
      // pressed from is disabled once the field empties.
      menu.querySelector('input[data-act="name"]')?.focus();
      apply(() => api.share(noteId, [typed], 'view'), null, null, {
        adds: typed.toLowerCase(),
        failed: error => {
          if (error?.status !== 404 || error?.body?.detail !== NO_SUCH_ACCOUNT) return false;
          addError = 'No account with that name.';
          return true;
        },
        done: result => {
          const found = result.find(r => r.username === typed.toLowerCase());
          if (!found) { addError = 'You own this note.'; return; }
          if (!users.includes(found.username)) users.push(found.username);
          if (draft.trim() === typed) draft = '';
        },
      });
    }

    // Changes are saved one at a time, in the order they were made. One made
    // while another saves waits its turn instead of being dropped: the pills
    // show it at once, so it has to land. `confirmed` is what the server last
    // said; what the popover shows is that plus every change still queued.
    function shown() {
      const view = new Map(confirmed);
      for (const step of queue) {
        if (!step.name) continue;
        if (step.perm) view.set(step.name, step.perm); else view.delete(step.name);
      }
      return view;
    }

    // `failed` takes an error the popover answers itself (true: it did, and
    // the queue goes on); `done` gets the roster the server sent back.
    async function apply(call, name, perm, { adds = null, failed = () => false, done = () => {} } = {}) {
      queue.push({ call, name, perm, adds, failed, done });
      roster = shown();
      render();
      if (busy) return;
      busy = true;
      try {
        while (queue.length) {
          const step = queue[0];
          let result;
          try {
            result = await step.call();
          } catch (error) {
            // A name with no account changed nothing on the server, so the
            // changes queued behind it still stand.
            if (step.failed(error)) { queue.shift(); continue; }
            // A failed change drops the ones queued behind it: they were
            // made on top of a state that never existed.
            queue.length = 0;
            roster = new Map(confirmed);
            onError(error);
            break;
          }
          queue.shift();
          if (Array.isArray(result)) confirmed = new Map(result.map(r => [r.username, r.permission]));
          else if (step.perm) confirmed.set(step.name, step.perm);
          else confirmed.delete(step.name);
          if (Array.isArray(result)) step.done(result);
          roster = shown();
          onChange(Array.from(confirmed, ([username, permission]) => ({ username, permission })));
        }
      } finally {
        busy = false;
        if (openMenu === menu) render();
      }
    }

    async function load() {
      status('Loading…');
      try {
        const [targets, shares] = await Promise.all([api.shareTargets(), api.listShares(noteId)]);
        directory = targets.directory;
        users = targets.users.map(t => t.username).filter(Boolean);
        confirmed = new Map(shares.map(r => [r.username, r.permission]));
        roster = new Map(confirmed);
        // Someone the note is still shared with stays listed even if the
        // account list no longer has them.
        for (const name of roster.keys()) if (!users.includes(name)) users.push(name);
        if (openMenu === menu) render();
      } catch (error) {
        if (openMenu !== menu) return;
        status(error?.status === 403 ? 'Your account is not allowed to share notes.' : "Couldn't load users.", error?.status !== 403);
      }
    }

    load();
    return menu;
  }

  return { open, close, isOpen: () => !!openMenu && openMenu.isConnected };
}
