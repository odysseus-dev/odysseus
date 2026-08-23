const STORAGE_KEY = 'odysseus-permission-mode';
const MODES = new Set(['auto', 'ask_actions', 'ask_all', 'read_only']);

function currentMode() {
  const trigger = document.getElementById('permission-mode-trigger');
  const value = trigger?.dataset.mode || localStorage.getItem(STORAGE_KEY) || 'auto';
  return MODES.has(value) ? value : 'auto';
}

const MODE_LABELS = {
  auto: 'Full access',
  ask_actions: 'Approve for me',
  ask_all: 'Ask for approval',
  read_only: 'Read only',
};

const control = document.getElementById('permission-mode-control');
const trigger = document.getElementById('permission-mode-trigger');
const menu = document.getElementById('permission-mode-menu');
const label = document.getElementById('permission-mode-label');
const items = Array.from(menu?.querySelectorAll('[data-permission-mode]') || []);

function setOpen(open, { focus = false } = {}) {
  if (!trigger || !menu) return;
  trigger.setAttribute('aria-expanded', String(open));
  control?.classList.toggle('open', open);
  if (open) {
    menu.hidden = false;
    requestAnimationFrame(() => menu.classList.add('visible'));
    if (focus) items.find((item) => item.getAttribute('aria-checked') === 'true')?.focus();
  } else {
    menu.classList.remove('visible');
    const finish = () => {
      if (!menu.classList.contains('visible')) menu.hidden = true;
    };
    menu.addEventListener('transitionend', finish, { once: true });
    setTimeout(finish, 180);
  }
}

function selectMode(mode, { close = true } = {}) {
  const selected = MODES.has(mode) ? mode : 'auto';
  if (trigger) trigger.dataset.mode = selected;
  if (label) label.textContent = MODE_LABELS[selected];
  items.forEach((item) => item.setAttribute('aria-checked', String(item.dataset.permissionMode === selected)));
  localStorage.setItem(STORAGE_KEY, selected);
  if (close) {
    setOpen(false);
    trigger?.focus({ preventScroll: true });
  }
}

if (trigger && menu) {
  selectMode(localStorage.getItem(STORAGE_KEY) || 'auto', { close: false });
  menu.hidden = true;
  trigger.addEventListener('click', () => setOpen(trigger.getAttribute('aria-expanded') !== 'true'));
  items.forEach((item) => item.addEventListener('click', () => selectMode(item.dataset.permissionMode)));
  document.addEventListener('pointerdown', (event) => {
    if (trigger.getAttribute('aria-expanded') === 'true' && !control?.contains(event.target)) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (trigger.getAttribute('aria-expanded') !== 'true') return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      trigger.focus({ preventScroll: true });
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const current = items.indexOf(document.activeElement);
    const target = event.key === 'Home' ? 0
      : event.key === 'End' ? items.length - 1
      : event.key === 'ArrowDown' ? (current < 0 ? 0 : (current + 1) % items.length)
      : (current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length);
    items[target]?.focus();
  });
}

window.odysseusPermissions = { getMode: currentMode, setMode: selectMode };
