// Mobile card selection affordance shared by Memories, Skills, and Tasks.
// A long press enters the existing bulk-select mode and selects the card held.

const CARD_SELECTORS = '.memory-item[data-memory-id], .skill-card[data-skill-name], .task-card[data-id]';
const HOLD_MS = 450;
const MOVE_CANCEL_PX = 10;

function _mobileSelectEnabled() {
  return window.innerWidth <= 768 && ('ontouchstart' in window || navigator.maxTouchPoints > 0);
}

function _selectButtonFor(card) {
  if (card.matches('.skill-card')) return document.getElementById('skills-select-btn');
  if (card.matches('.task-card')) return document.getElementById('tasks-select-btn');
  return document.getElementById('memory-select-btn');
}

function _cardAfterRerender(card) {
  if (card.matches('.skill-card')) {
    const name = card.dataset.skillName;
    return [...document.querySelectorAll('.skill-card[data-skill-name]')]
      .find(el => el.dataset.skillName === name);
  }
  const isTask = card.matches('.task-card');
  const attr = isTask ? 'data-id' : 'data-memory-id';
  const value = card.getAttribute(attr);
  if (!value) return null;
  return document.querySelector(`${isTask ? '.task-card' : '.memory-item'}[${attr}="${CSS.escape(value)}"]`);
}

function _selectCard(card) {
  const button = _selectButtonFor(card);
  if (!button) return;
  if (!button.classList.contains('active')) button.click();
  setTimeout(() => {
    const checkbox = _cardAfterRerender(card)?.querySelector('.memory-select-cb');
    if (checkbox && !checkbox.checked) checkbox.click();
  }, 40);
}

function _bind(card) {
  if (card.dataset.mobileSelectHoldBound === '1') return;
  card.dataset.mobileSelectHoldBound = '1';
  let timer = null;
  let start = null;
  let fired = false;
  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    start = null;
  };
  card.addEventListener('pointerdown', (event) => {
    if (!_mobileSelectEnabled() || event.pointerType === 'mouse') return;
    if (event.target.closest('button, input, textarea, select, a, .memory-item-actions')) return;
    if (card.classList.contains('doclib-card-expanded') || card.classList.contains('email-card-expanded')) return;
    fired = false;
    start = { x: event.clientX, y: event.clientY };
    timer = setTimeout(() => {
      timer = null;
      fired = true;
      card._suppressNextClick = true;
      setTimeout(() => { card._suppressNextClick = false; }, 500);
      try { navigator.vibrate?.(15); } catch (_) {}
      _selectCard(card);
    }, HOLD_MS);
  });
  card.addEventListener('pointermove', (event) => {
    if (!start) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > MOVE_CANCEL_PX) cancel();
  });
  card.addEventListener('pointerup', () => { if (!fired) cancel(); else { start = null; fired = false; } });
  card.addEventListener('pointercancel', cancel);
}

function bindCards(root = document) {
  if (!_mobileSelectEnabled()) return;
  root.querySelectorAll?.(CARD_SELECTORS).forEach(_bind);
}

bindCards();
new MutationObserver(() => bindCards()).observe(document.documentElement, { childList: true, subtree: true });
