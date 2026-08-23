// Codex-style text selection action for chat messages.
// Selecting rendered conversation text offers one deliberate action: quote the
// selection into the composer as removable context without changing the
// user's typed prompt.

const MAX_SELECTION_CHARS = 16000;

let toolbar = null;
let selectedText = '';
let selectionFrame = 0;
let nextCitationId = 1;
const citations = [];

function citationStrip() {
  const composer = document.querySelector('.chat-input-bar');
  if (!composer) return null;
  let strip = composer.querySelector('.selection-citation-strip');
  if (!strip) {
    strip = document.createElement('div');
    strip.className = 'selection-citation-strip';
    strip.setAttribute('aria-label', 'Selected chat context');
    composer.prepend(strip);
  }
  return strip;
}

function renderCitations() {
  const strip = citationStrip();
  if (!strip) return;
  strip.replaceChildren();
  strip.hidden = citations.length === 0;
  for (const citation of citations) {
    const chip = document.createElement('div');
    chip.className = 'selection-citation-chip';
    chip.dataset.citationId = String(citation.id);

    const quote = document.createElement('span');
    quote.className = 'selection-citation-icon';
    quote.textContent = '“';
    quote.setAttribute('aria-hidden', 'true');

    const preview = document.createElement('span');
    preview.className = 'selection-citation-preview';
    preview.textContent = citation.text.replace(/\s+/g, ' ').trim();
    preview.title = citation.text;

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'selection-citation-remove';
    remove.setAttribute('aria-label', 'Remove selected context');
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      const index = citations.findIndex((item) => item.id === citation.id);
      if (index >= 0) citations.splice(index, 1);
      renderCitations();
    });

    chip.append(quote, preview, remove);
    strip.appendChild(chip);
  }
}

function getToolbar() {
  if (toolbar) return toolbar;
  toolbar = document.createElement('div');
  toolbar.className = 'chat-selection-toolbar';
  toolbar.hidden = true;
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', 'Selected text actions');
  toolbar.innerHTML = '<button type="button" class="chat-selection-add">Add to chat</button>';
  document.body.appendChild(toolbar);

  // Preserve the current browser selection until the action is applied.
  toolbar.addEventListener('pointerdown', (event) => event.preventDefault());
  toolbar.querySelector('.chat-selection-add').addEventListener('click', addSelectionToChat);
  return toolbar;
}

function hideToolbar() {
  if (toolbar) toolbar.hidden = true;
  selectedText = '';
}

function isChatSelection(selection) {
  if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
  const history = document.getElementById('chat-history');
  if (!history) return false;
  const anchor = selection.anchorNode?.nodeType === Node.TEXT_NODE
    ? selection.anchorNode.parentElement : selection.anchorNode;
  const focus = selection.focusNode?.nodeType === Node.TEXT_NODE
    ? selection.focusNode.parentElement : selection.focusNode;
  return !!(anchor && focus && history.contains(anchor) && history.contains(focus)
    && anchor.closest('.msg, .agent-thread') && focus.closest('.msg, .agent-thread'));
}

function positionToolbar(rect) {
  const el = getToolbar();
  el.hidden = false;
  el.style.left = '0px';
  el.style.top = '0px';
  const own = el.getBoundingClientRect();
  const gap = 8;
  const left = Math.min(
    window.innerWidth - own.width - gap,
    Math.max(gap, rect.left + (rect.width - own.width) / 2),
  );
  const above = rect.top - own.height - gap;
  const top = above >= gap ? above : Math.min(window.innerHeight - own.height - gap, rect.bottom + gap);
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
}

function updateToolbar() {
  selectionFrame = 0;
  const selection = window.getSelection();
  if (!isChatSelection(selection)) {
    hideToolbar();
    return;
  }
  const text = selection.toString().trim();
  if (!text) {
    hideToolbar();
    return;
  }
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  if ((!rect.width && !rect.height) || rect.bottom < 0 || rect.top > window.innerHeight) {
    hideToolbar();
    return;
  }
  selectedText = text.slice(0, MAX_SELECTION_CHARS);
  positionToolbar(rect);
}

function scheduleToolbarUpdate() {
  if (selectionFrame) cancelAnimationFrame(selectionFrame);
  selectionFrame = requestAnimationFrame(updateToolbar);
}

function addSelectionToChat() {
  const input = document.getElementById('message');
  if (!input || !selectedText) return;
  const text = selectedText.slice(0, MAX_SELECTION_CHARS);
  if (!citations.some((item) => item.text === text)) {
    citations.push({ id: nextCitationId++, text });
    if (citations.length > 5) citations.shift();
  }
  renderCitations();
  input.focus({ preventScroll: true });
  window.getSelection()?.removeAllRanges();
  hideToolbar();
}

document.addEventListener('selectionchange', scheduleToolbarUpdate);
document.addEventListener('pointerup', scheduleToolbarUpdate);
document.addEventListener('keyup', (event) => {
  if (event.key === 'Escape') hideToolbar();
  else scheduleToolbarUpdate();
});
document.addEventListener('pointerdown', (event) => {
  if (toolbar && !toolbar.contains(event.target)) hideToolbar();
}, true);
document.addEventListener('scroll', hideToolbar, true);
window.addEventListener('resize', hideToolbar);

window.odysseusCitations = {
  getAll: () => citations.map(({ text }) => ({ text })),
  clear: () => {
    citations.splice(0, citations.length);
    renderCitations();
  },
};
