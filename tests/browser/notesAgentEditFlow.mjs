// A run started from a note edits that note, against the stub model: the edit
// shows in the open window with an Undo that puts the exact bytes back; the
// same kind of run aimed at another note is refused; and on a shared note
// the edit waits for the owner's approval in the run's chat, and a NUL after
// its id is refused rather than written past that approval. A run on a
// private note that reads the note before changing it is not stopped.
import assert from 'node:assert/strict';
import path from 'node:path';
import { createAccount, getNote, modelRequests, openNotes, refreshNotes, runItemAgent, tapOrClick, until } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
// A run reaches the stub model and back in a few seconds; allow more than the harness default.
const untilNote = (api, id, accept, label, timeout = 30000) => until(() => getNote(api, id), r => accept(r.note, r.status), label, timeout).then(r => r.note);
const textOf = content => (Array.isArray(content) ? content.map(part => part.text || '').join(' ') : String(content || ''));

/** The tool result the model was sent for one of its calls. */
const toolResult = callId => until(
  () => modelRequests().flatMap(body => (body.messages || []).filter(m => m.role === 'tool' && m.tool_call_id === callId)).map(m => textOf(m.content)),
  list => list.length > 0, `the result of ${callId}`, 30000,
).then(list => list.at(-1));

async function runRow(page, mobile, win, text) {
  const row = win.locator('li.md-task').filter({ hasText: text });
  if (!mobile) await row.hover();
  await runItemAgent(mobile, row.locator('.note-task-agent'));
}

const openWindow = async (page, mobile, id) => {
  const win = page.locator('#note-window-modal');
  await tapOrClick(mobile, page.locator(`.note-card[data-note-id="${id}"] .note-card-title`));
  await win.locator('.note-window-body').waitFor();
  return win;
};

const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator('#note-window-modal').waitFor({ state: 'detached' });
};

export async function checkAgentEdits({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const other = await (await owner.api.post('/api/notes', { data: { title: `Other ${size}`, content: 'untouched' } })).json();
  // Bytes a re-serialization would change: a double space, a trailing blank.
  const CONTENT = `Plan  the trip \n\n- [ ] EDITTEST boots-${size}\n- [ ] EDITOTHER ${other.id}`;
  const note = await (await owner.api.post('/api/notes', { data: { title: `Edit me ${size}`, content: CONTENT } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await page.locator(`.note-card[data-note-id="${note.id}"]`).waitFor();
  const win = await openWindow(page, mobile, note.id);
  const body = win.locator('.note-window-body');

  // 1. The item's run edits its own note, and the open window follows.
  await runRow(page, mobile, win, 'EDITTEST');
  const AGENT_TEXT = `Edited by the agent: boots-${size}`;
  const edited = await untilNote(owner.api, note.id, n => n?.content?.startsWith(AGENT_TEXT), 'the agent edit');
  assert.ok(edited.content.includes(`- [ ] EDITOTHER ${other.id}`), 'the task lines are kept');
  assert.equal(edited.agent_edit.undoable, true);
  await until(() => body.textContent(), text => text.includes(AGENT_TEXT), 'the edit in the open window', 15000);
  await page.locator('#toast').filter({ hasText: 'Agent edited this note' }).waitFor();
  // The agent's Undo; "History (N)" shares its link style and follows it.
  const undo = win.locator('.note-window-meta-undo:not(.note-window-meta-history)');
  await undo.waitFor();
  assert.match(await win.locator('.note-window-meta').textContent(), /Agent edited .* · Undo · History \(\d+\)$/);
  if (evidence) await page.screenshot({ path: path.join(evidence, `agent-edit-${size}.png`), animations: 'disabled' });

  // 2. Undo puts the exact bytes back, in the store and in the window.
  await tapOrClick(mobile, undo);
  const restored = await untilNote(owner.api, note.id, n => n?.content === CONTENT, 'the undone note');
  assert.equal(restored.title, `Edit me ${size}`);
  assert.equal(restored.agent_edit, null);
  await until(() => body.textContent(), text => !text.includes(AGENT_TEXT) && text.includes('Plan'), 'the window back to the note', 15000);
  assert.equal(await undo.count(), 0, 'nothing left to undo');

  // 3. The same kind of run aimed at another note is refused, and nothing changes.
  await runRow(page, mobile, win, 'EDITOTHER');
  const refusal = await toolResult('call_edit_other_1');
  assert.match(refusal, /may change only that note/, refusal);
  assert.equal((await (await owner.api.get(`/api/notes/${other.id}`)).json()).content, 'untouched');
  assert.equal((await (await owner.api.get(`/api/notes/${note.id}`)).json()).content, CONTENT);
  await closeWindow(page);

  // 3b. A private note, a run that reads the note and then changes it: the
  // read is the note itself, so the change needs no approval.
  const TWO = `Plan\n\n- [ ] EDITTWO steps-${size}`;
  const two = await (await owner.api.post('/api/notes', { data: { title: `Two step ${size}`, content: TWO } })).json();
  await refreshNotes(page);
  await page.locator(`.note-card[data-note-id="${two.id}"]`).waitFor();
  const twoWin = await openWindow(page, mobile, two.id);
  await runRow(page, mobile, twoWin, 'EDITTWO');
  assert.match(await toolResult(`call_two_steps-${size}_0`), new RegExp(`\\[${two.id}\\]`));
  const twoUpdate = await toolResult(`call_two_steps-${size}_1`);
  assert.match(twoUpdate, /Note updated/, twoUpdate);
  await untilNote(owner.api, two.id, n => n?.content?.startsWith(`Two-step edit: steps-${size}`), 'the two-step edit');
  await closeWindow(page);

  // 4. A shared note: the edit waits for the owner's approval in the run's chat.
  const friend = await createAccount(admin, `edit-friend-${size}`);
  const SHARED = `- [ ] EDITTEST shared-${size}\n- [ ] EDITNUL shared-${size}`;
  const shared = await (await owner.api.post('/api/notes', { data: { title: `Shared edit ${size}`, content: SHARED } })).json();
  const grant = await owner.api.post(`/api/notes/${shared.id}/share`, { data: { users: [friend.username], permission: 'edit' } });
  assert.equal(grant.ok(), true, await grant.text());
  await refreshNotes(page);
  await page.locator(`.note-card[data-note-id="${shared.id}"]`).waitFor();
  const sharedWin = await openWindow(page, mobile, shared.id);
  const requests = modelRequests().length;
  await runRow(page, mobile, sharedWin, 'EDITTEST');
  await until(() => modelRequests().slice(requests).filter(b => JSON.stringify(b).includes(`EDITTEST shared-${size}`)), r => r.length > 0, 'the shared run', 20000);
  for (let i = 0; i < 6; i += 1) {
    assert.equal((await (await owner.api.get(`/api/notes/${shared.id}`)).json()).content, SHARED, 'held until approved');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  const linked = await untilNote(owner.api, shared.id, n => !!n?.agent_session_id, 'the shared run link');
  await tapOrClick(mobile, sharedWin.locator('[data-act="agent-chat"]'));
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, linked.agent_session_id);
  const allow = page.locator('.ask-user-option').filter({ hasText: /Allow/ }).first();
  await allow.waitFor({ timeout: 30000 });
  if (evidence) await page.screenshot({ path: path.join(evidence, `agent-edit-shared-approval-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, allow);
  await untilNote(owner.api, shared.id, n => n?.content?.startsWith(`Edited by the agent: shared-${size}`), 'the approved edit');

  // 5. The same shared note, the model sending its id with a NUL after it: the
  // tool refuses the reference, and nothing is written past the approval.
  const approved = (await (await owner.api.get(`/api/notes/${shared.id}`)).json()).content;
  await context.close();
  const nulRun = await openNotes(browser, owner, size);
  await nulRun.page.locator(`.note-card[data-note-id="${shared.id}"]`).waitFor();
  const nulWin = await openWindow(nulRun.page, mobile, shared.id);
  await runRow(nulRun.page, mobile, nulWin, 'EDITNUL');
  const nulRefusal = await toolResult('call_edit_nul_1');
  assert.match(nulRefusal, /control characters/, nulRefusal);
  const afterNul = (await (await owner.api.get(`/api/notes/${shared.id}`)).json()).content;
  assert.equal(afterNul, approved, 'nothing written past the gate');
  assert.ok(!afterNul.includes('Written past the gate'), afterNul);

  await nulRun.context.close();
  for (const id of [note.id, other.id, two.id, shared.id]) await owner.api.delete(`/api/notes/${id}`);
}
