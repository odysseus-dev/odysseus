/** Agent runs started from a note: create a session, link it to the note, and drain its background stream. */
import { _noteToAgentPrompt, askNotePrompt, askTaskPrompt, canRunNoteAgent, taskAgentPrompt } from '../domain/noteRules.js';

const LABEL_MAX = 40;

/**
 * The note ids an agent changed, read from a chunk of its event stream. The
 * server marks each manage_notes result that edited the run's own note with
 * `note_edit` (see core/note_agent_scope.py). `rest` is an incomplete last line.
 */
export function noteEditsInStream(buffer) {
  const lines = String(buffer || '').split('\n');
  const rest = lines.pop();
  const ids = [];
  for (const line of lines) {
    if (!line.startsWith('data: ') || !line.includes('"note_edit"')) continue;
    try {
      const event = JSON.parse(line.slice(6));
      const id = event?.type === 'tool_output' ? event.note_edit?.note_id : null;
      if (id) ids.push(id);
    } catch {}
  }
  return { ids, rest };
}

/**
 * Reads a run's event stream chunk by chunk: `push` returns the note ids the
 * agent changed (see noteEditsInStream), and `failure` holds why the run
 * failed, from the stream's first `event: error`. A model that answers 500,
 * or times out on the server, ends the run that way.
 */
export function createRunStream() {
  let rest = '';
  let errorNext = false;
  const run = {
    failure: '',
    push(chunk) {
      const text = rest + String(chunk || '');
      const parsed = noteEditsInStream(text);
      rest = parsed.rest;
      // The complete lines only: the text before `rest` ends with a newline.
      for (const line of text.slice(0, text.length - rest.length).split('\n').slice(0, -1)) {
        if (line === 'event: error') { errorNext = true; continue; }
        if (!line) { errorNext = false; continue; }
        if (!errorNext || !line.startsWith('data: ')) continue;
        errorNext = false;
        let detail = '';
        try {
          const event = JSON.parse(line.slice(6));
          detail = String(event?.text || event?.error || '').trim();
        } catch {}
        run.failure ||= detail || 'Model request failed';
      }
      return parsed.ids;
    },
  };
  return run;
}

/**
 * Ports: `api` (default chat, session create, chat stream), `patchNote`,
 * `createFormData`, `sessions` ({ markStreamComplete, open }), `closeNotes`,
 * and `effects` ({ render, showToast, showError, agentEdited }).
 */
export function createAgentActions({
  api,
  app,
  state,
  patchNote,
  createFormData,
  sessions,
  closeNotes,
  effects
}) {
  const findNote = id => state.notes.find(n => n.id === id);

  /**
   * Start one agent run for a note. Resolves to the new session id, or null when nothing started.
   * `prompt` is the user's request only; the note goes by id, and `focusTask` names the todo a
   * per-item run is about. Both are stored on the session when it is created, and the server
   * hands them to the model as untrusted context on every turn of that chat.
   */
  async function runNoteAgent({ noteId, prompt, label, focusTask = '' }) {
    const note = findNote(noteId);
    if (!note || !canRunNoteAgent(note)) return null;
    if (!prompt) { effects.showToast('Nothing to ask: the note is empty'); return null; }
    try {
      const dc = await (await api.defaultChat()).json();
      if (!dc.endpoint_url || !dc.model) { effects.showError('No default chat model configured'); return null; }

      // 1. Create the session server-side (no UI switch). skip_validation
      //    avoids re-probing: the default-chat endpoint is already known good.
      const csFd = createFormData();
      csFd.append('name', 'Agent: ' + (String(label || '').trim() || 'note').slice(0, LABEL_MAX));
      csFd.append('endpoint_url', dc.endpoint_url);
      csFd.append('model', dc.model);
      if (dc.endpoint_id) csFd.append('endpoint_id', dc.endpoint_id);
      csFd.append('skip_validation', 'true');
      // The chat may change this note and no other; the server keeps that on
      // the session, for this run and every later turn in the chat.
      csFd.append('note_id', noteId);
      if (focusTask) csFd.append('note_focus_task', focusTask);
      const csRes = await api.createSession(csFd);
      if (!csRes.ok) { effects.showError('Could not create agent session'); return null; }
      const sid = (await csRes.json()).id;

      // 2. Link the session to the note right away so "Agent chat" appears.
      const linked = findNote(noteId);
      if (linked) app.mergeNote(linked, { agent_session_id: sid });
      effects.render();
      // Saved before the run starts, and rendered again: an open note window
      // takes the new version as its own instead of reporting a conflict on
      // its next save, and the run reads the note after this write, so its
      // edit is not refused as based on an older version.
      await patchNote(noteId, { agent_session_id: sid }).then(() => effects.render()).catch(() => {});

      // 3. Run the agent in the background: POST to chat_stream in agent mode
      //    and drain the SSE so the server runs the loop to completion and
      //    saves it, without rendering anything in the chat UI. Each edit the
      //    run makes to its note is passed on as it lands, so the note on
      //    screen follows it and offers Undo. Nobody watches this stream, so
      //    a run that fails says so here; the server saves why in its chat.
      const fd = createFormData();
      fd.append('message', prompt);
      fd.append('session', sid);
      fd.append('mode', 'agent');
      fd.append('selected_note_ids', JSON.stringify([noteId]));
      const failed = reason => effects.showError(`Agent failed: ${reason}`);
      api.startChat(fd)
        .then(async (res) => {
          if (!res.ok) {
            let detail = '';
            try { const body = await res.json(); detail = body?.detail?.message || body?.detail || body?.error || ''; } catch {}
            failed(typeof detail === 'string' && detail.trim() ? detail.trim() : `HTTP ${res.status}`);
            return;
          }
          if (!res.body) return;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const run = createRunStream();
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (!value) continue;
            const ids = run.push(typeof value === 'string' ? value : decoder.decode(value, { stream: true }));
            for (const id of new Set(ids)) if (id === noteId) effects.agentEdited?.(id);
          }
          sessions.markStreamComplete(sid);
          if (run.failure) failed(run.failure);
        })
        .catch(() => failed('the connection to the server was lost'));

      effects.showToast('Agent working in the background', { duration: 6000, action: 'Open chat', onAction: () => openAgentChat(noteId) });
      return sid;
    } catch (e) {
      effects.showError('Agent failed: ' + (e.message || e));
      return null;
    }
  }

  /** "Agent: solve this", the whole note: from the window's Ask popover, or the card menu. `draft` is the title and content as shown; it names the run, and the window saves it before the run reads the note. */
  function _agentSolveNote(id, { draft } = {}) {
    const note = findNote(id);
    if (!note) return Promise.resolve(null);
    const context = draft ? { ...note, ...draft } : note;
    const firstTask = Array.isArray(note.items) ? note.items[0]?.text : '';
    return runNoteAgent({ noteId: id, prompt: _noteToAgentPrompt(context), label: context.title || firstTask || 'todo' });
  }

  /** One checklist item. `title` is the title as shown, which may be ahead of the saved one; it only names the run. */
  function runTaskAgent(noteId, text, { title } = {}) {
    const note = findNote(noteId);
    if (!note) return Promise.resolve(null);
    const context = title === undefined ? note : { ...note, title };
    return runNoteAgent({ noteId, prompt: taskAgentPrompt(context, text), label: text, focusTask: String(text || '').trim() });
  }

  /** "Ask about this item": a preset or a typed request about one checklist item. `title` is the title as shown; it only names the run. */
  function askAboutTask(noteId, text, request, { label, title } = {}) {
    const note = findNote(noteId);
    if (!note) return Promise.resolve(null);
    const context = title === undefined ? note : { ...note, title };
    const item = String(text || '').trim();
    return runNoteAgent({ noteId, prompt: askTaskPrompt(context, request), label: `${label || request}: ${item}`, focusTask: item });
  }

  /** "Ask about this note": a preset or a typed request. `draft` is the title and content as shown; it names the run. */
  function askAboutNote(noteId, request, { label, draft } = {}) {
    const note = findNote(noteId);
    if (!note) return Promise.resolve(null);
    const context = draft ? { ...note, ...draft } : note;
    return runNoteAgent({ noteId, prompt: askNotePrompt(context, request), label: `${label || request}: ${context.title || 'note'}` });
  }

  /** Leave Notes for the chat of the note's latest agent run. */
  async function openAgentChat(noteId) {
    const note = findNote(noteId);
    const sid = note && canRunNoteAgent(note) ? note.agent_session_id : null;
    return openChat(sid);
  }

  /** Any chat by id (the history links an agent edit to the chat that made it). */
  async function openChat(sid) {
    if (!sid) return false;
    await closeNotes();
    try {
      await sessions.open(sid);
      return true;
    } catch {
      effects.showError('Could not open the agent chat');
      return false;
    }
  }

  return { runNoteAgent, _agentSolveNote, runTaskAgent, askAboutTask, askAboutNote, openAgentChat, openChat };
}
