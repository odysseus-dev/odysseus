/** Command palette (Ctrl/Cmd+K): fuzzy list of tools, panels and actions. */

export function createCommandPalette({ commands = [], onRun = null } = {}) {
  const overlay = document.createElement('div');
  overlay.className = 'ge-command-palette';
  overlay.hidden = true;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Command palette');
  overlay.innerHTML = `
    <div class="ge-command-panel">
      <input class="ge-command-input" type="text" placeholder="Type a command..." aria-label="Command" autocomplete="off" />
      <div class="ge-command-list" role="listbox"></div>
    </div>`;
  document.body.appendChild(overlay);

  const input = overlay.querySelector('.ge-command-input');
  const list = overlay.querySelector('.ge-command-list');
  let filtered = [];
  let activeIndex = 0;

  function render() {
    const query = input.value.trim().toLowerCase();
    filtered = commands.filter(cmd => !query || String(cmd.label).toLowerCase().includes(query));
    list.innerHTML = '';
    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'ge-command-empty';
      empty.textContent = 'No matching commands';
      list.appendChild(empty);
      return;
    }
    if (activeIndex >= filtered.length) activeIndex = 0;
    filtered.forEach((cmd, index) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `ge-command-item${index === activeIndex ? ' active' : ''}`;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', index === activeIndex ? 'true' : 'false');
      row.textContent = cmd.label;
      row.addEventListener('click', () => run(cmd));
      list.appendChild(row);
    });
  }

  function run(cmd) {
    close();
    if (typeof cmd.run === 'function') cmd.run();
    else if (typeof onRun === 'function') onRun(cmd);
  }

  function open() {
    overlay.hidden = false;
    input.value = '';
    activeIndex = 0;
    render();
    input.focus();
  }

  function close() {
    overlay.hidden = true;
  }

  input.addEventListener('input', () => { activeIndex = 0; render(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); activeIndex = Math.min(activeIndex + 1, filtered.length - 1); render(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); activeIndex = Math.max(activeIndex - 1, 0); render(); }
    else if (event.key === 'Enter') { event.preventDefault(); if (filtered[activeIndex]) run(filtered[activeIndex]); }
  });
  overlay.addEventListener('pointerdown', (event) => { if (event.target === overlay) close(); });

  return {
    element: overlay,
    open,
    close,
    toggle: () => (overlay.hidden ? open() : close()),
    isOpen: () => !overlay.hidden,
  };
}
