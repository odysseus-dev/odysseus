/** Agent affordances in Notes, all owner-only: a button on each checklist row, the card's Agent tag, and the "Ask about this note" popover (which also starts the whole-note run). A row's button opens the same popover about its item: no run starts on one tap. */
import { NOTE_ASK_PRESETS, canRunNoteAgent, showsTaskAgent } from '../domain/noteRules.js';

export const TASK_AGENT_CLASS = 'note-task-agent';
export const AGENT_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>';
export const AGENT_CHAT_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
// data-md-skip: the serializer and the editor's block snapshots ignore it, so
// the button can never reach saved markdown.
export const TASK_AGENT_BUTTON = `<button type="button" class="${TASK_AGENT_CLASS}" contenteditable="false" data-md-skip="" tabindex="0" title="Ask the agent about this item" aria-label="Ask the agent about this item">${AGENT_ICON}</button>`;

const ELEMENT = 1;
const hasClass = (node, cls) => ` ${(typeof node.getAttribute === 'function' && node.getAttribute('class')) || ''} `.includes(` ${cls} `);

/** A row's own text, as shown now. Read at click time from the row the button sits in, never by index, so it survives reorders and unsaved edits. */
export function taskRowText(li) {
  const text = Array.from(li?.childNodes || []).find(child => child.nodeType === ELEMENT && hasClass(child, 'md-task-text'));
  return (text ? text.textContent : '').replace(/\s+/g, ' ').trim();
}

/** Add the agent button to every task row under `root` that lacks one, or strip them all when `enabled` is false. Buttons outside a task row (the browser can carry one along while editing) are removed. */
export function decorateTaskRows(root, enabled) {
  if (!root) return;
  root.querySelectorAll(`.${TASK_AGENT_CLASS}`).forEach(button => {
    if (!enabled || !button.parentElement?.matches('li.md-task')) button.remove();
  });
  if (!enabled) return;
  root.querySelectorAll('li.md-task').forEach(li => {
    if (!li.querySelector(`:scope > .${TASK_AGENT_CLASS}`)) li.insertAdjacentHTML('beforeend', TASK_AGENT_BUTTON);
  });
}

export function createNoteAgentUi({ state, actions, esc, bindMenuDismiss, dismissOrRemove, portalZ = () => null }) {
  let askMenu = null;
  const findNote = id => state.notes.find(n => n.id === id);

  /** Cards: per-row buttons (shown on hover in CSS) and the Agent tag. */
  function wireCards(body) {
    if (!body) return;
    body.querySelectorAll('.note-card[data-note-id]').forEach(card => {
      const note = findNote(card.dataset.noteId);
      decorateTaskRows(card.querySelector('.note-md-preview'), showsTaskAgent(note, { selectMode: state.selectMode }));
    });
    body.querySelectorAll(`.note-card .${TASK_AGENT_CLASS}`).forEach(button => {
      button.addEventListener('pointerdown', event => event.stopPropagation());
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if (state.selectMode) return;
        const noteId = button.closest('.note-card')?.dataset.noteId;
        const text = taskRowText(button.closest('li.md-task'));
        if (noteId && text) openAsk(button, noteId, { item: text });
      });
    });
    body.querySelectorAll('.note-card-agent-tag').forEach(tag => {
      tag.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if (!state.selectMode) actions.openAgentChat(tag.dataset.noteId);
      });
    });
  }

  /** The Agent tag for a card's header: my note, linked to a run. */
  function cardTagHtml(note) {
    if (!canRunNoteAgent(note) || !note.agent_session_id || state.selectMode) return '';
    return `<button type="button" class="note-card-share-badge note-card-agent-tag" data-note-id="${esc(note.id)}" title="Open agent chat" aria-label="Open agent chat">${AGENT_ICON}Agent</button>`;
  }

  function closeAsk() {
    if (askMenu) dismissOrRemove(askMenu);
    askMenu = null;
  }

  /**
   * "Ask about this note" popover: presets, or a typed request. `draft()` returns the title and content as shown.
   * With `item` (a checklist row's text) it asks about that item instead, and its first entry is the
   * row's own run, so the row's button takes a second, deliberate tap before anything starts.
   */
  function openAsk(anchor, noteId, { item = '', draft = () => null, before = () => Promise.resolve() } = {}) {
    closeAsk();
    const subject = item ? 'item' : 'note';
    const menu = document.createElement('div');
    menu.className = 'note-reminder-menu note-ask-menu';
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', `Ask about this ${subject}`);
    // The whole-note run sits here: the card menu that also offers it is hidden.
    const solveLabel = item ? 'Agent: help with this' : findNote(noteId)?.agent_session_id ? 'Re-run agent' : 'Agent: solve this';
    menu.innerHTML = `<div class="note-reminder-menu-title">Ask about this ${subject}</div>
      ${item ? `<div class="note-reminder-menu-sub note-ask-item">${esc(item)}</div>` : ''}
      <button type="button" class="note-reminder-menu-item" data-act="solve"><span>${esc(solveLabel)}</span><span class="note-reminder-menu-sub">${item ? 'this item' : 'the whole note'}</span></button>
      <div class="note-ask-presets">${NOTE_ASK_PRESETS.map((preset, i) => `<button type="button" class="note-share-pill" data-preset="${i}">${esc(preset.label)}</button>`).join('')}</div>
      <div class="note-reminder-menu-picker"><input type="text" class="note-link-input note-ask-input" placeholder="Ask anything about this ${subject}" spellcheck="true" autocomplete="off" /></div>
      <button type="button" class="note-reminder-menu-item note-reminder-menu-confirm" data-act="ask"><span>Ask</span></button>`;
    const z = portalZ();
    if (z) menu.style.zIndex = String(z);
    document.body.appendChild(menu);
    askMenu = menu;
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${Math.max(8, Math.min(rect.top - menu.offsetHeight - 4, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
    const close = bindMenuDismiss(menu, () => { menu.remove(); if (askMenu === menu) askMenu = null; });
    const input = menu.querySelector('.note-ask-input');
    let busy = false;
    const run = async (request, label) => {
      if (busy || !String(request || '').trim()) return;
      busy = true;
      close();
      await before();
      const shown = draft() || undefined;
      if (item) actions.askAboutTask(noteId, item, request, { label, title: shown?.title });
      else actions.askAboutNote(noteId, request, { label, draft: shown });
    };
    menu.querySelector('[data-act="solve"]').addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      close();
      await before();
      const shown = draft() || undefined;
      if (item) actions.runTaskAgent(noteId, item, { title: shown?.title });
      else actions._agentSolveNote(noteId, { draft: shown });
    });
    menu.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
      const preset = NOTE_ASK_PRESETS[Number(button.dataset.preset)];
      run(item ? preset.itemRequest : preset.request, preset.label);
    }));
    const submit = () => {
      const request = input.value.trim();
      if (!request) { input.focus(); return; }
      run(request, request);
    };
    menu.querySelector('[data-act="ask"]').addEventListener('click', submit);
    input.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Enter') { event.preventDefault(); submit(); }
    });
    requestAnimationFrame(() => input.focus());
  }

  return { wireCards, cardTagHtml, openAsk, closeAsk, isAskOpen: () => !!askMenu };
}
