/** Create an agent session, link it to a note and drain its background stream. */
import { _noteToAgentPrompt } from '../domain/noteRules.js';
export function createAgentActions({
  _patchNote,
  _renderNotes,
  api,
  app,
  createFormData,
  state,
  streamComplete,
  uiModule
}) {
  async function _agentSolveNote(id) {
    const note = state.notes.find(n => n.id === id);
    if (!note) return;
    const prompt = _noteToAgentPrompt(note);
    if (!prompt) { uiModule.showToast('Nothing to solve — note is empty'); return; }
    try {
      const dc = await (await api.defaultChat()).json();
      if (!dc.endpoint_url || !dc.model) { uiModule.showError('No default chat model configured'); return; }

      // 1. Create the session server-side (no UI switch). skip_validation
      //    avoids re-probing : the default-chat endpoint is already known good.
      const label = (note.title || (Array.isArray(note.items) && note.items[0]?.text) || 'todo').slice(0, 40);
      const csFd = createFormData();
      csFd.append('name', 'Agent: ' + label);
      csFd.append('endpoint_url', dc.endpoint_url);
      csFd.append('model', dc.model);
      if (dc.endpoint_id) csFd.append('endpoint_id', dc.endpoint_id);
      csFd.append('skip_validation', 'true');
      const csRes = await api.createSession(csFd);
      if (!csRes.ok) { uiModule.showError('Could not create agent session'); return; }
      const sess = await csRes.json();
      const sid = sess.id;

      // 2. Link the session to the note right away so the tag appears.
      const n = state.notes.find(x => x.id === id);
      if (n) app.mergeNote(n, { agent_session_id: sid });
      _renderNotes();
      _patchNote(id, { agent_session_id: sid }).catch(() => {});

      // 3. Kick off the agent run in the background. POST to chat_stream in
      //    agent mode and drain the SSE so the server runs the loop to
      //    completion + saves : without rendering anything in the chat UI.
      const fd = createFormData();
      fd.append('message', prompt);
      fd.append('session', sid);
      fd.append('mode', 'agent');
      fd.append('disabled_tools', JSON.stringify(['manage_notes']));
      api.startChat(fd)
        .then(async (res) => {
          if (!res.ok || !res.body) return;
          const reader = res.body.getReader();
          // Drain to completion (server finishes + persists the run).
          while (true) { const { done } = await reader.read(); if (done) break; }
          streamComplete(sid);
        })
        .catch(() => {});

      uiModule.showToast('Agent working in background — tap the Agent tag when ready');
    } catch (e) {
      uiModule.showError('Agent failed: ' + (e.message || e));
    }
  }

  return { _agentSolveNote };
}
