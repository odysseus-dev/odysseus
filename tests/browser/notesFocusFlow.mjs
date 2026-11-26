// A checklist item's agent run, opened from the toast while it is still
// going, against the stub model (its FOCUSTEST rules):
// - on my private note the run reads notes with no approval, and its answer
//   names the item;
// - a follow-up typed in that chat ("continue"), with the composer in Chat
//   mode, is still an agent turn whose context names the item;
// - on a shared note the read waits for approval: the card shows in the chat
//   opened from the toast, and Allow runs the held read and the answer.
import assert from 'node:assert/strict';
import path from 'node:path';
import { createAccount, modelRequests, openNotes, refreshNotes, runItemAgent, tapOrClick, toolNames, until, untilNote } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const textOf = content => (Array.isArray(content) ? content.map(part => part.text || '').join(' ') : String(content || ''));
const contextText = body => (body.messages || []).map(m => textOf(m.content)).join('\n');

async function startFromToast(page, mobile, noteId, item) {
  const win = page.locator('#note-window-modal');
  await tapOrClick(mobile, page.locator(`.note-card[data-note-id="${noteId}"] .note-card-title`));
  await win.locator('.note-window-body').waitFor();
  const row = win.locator('li.md-task').filter({ hasText: item });
  if (!mobile) await row.hover();
  await runItemAgent(mobile, row.locator('.note-task-agent'));
  const toast = page.locator('#toast').filter({ hasText: 'Agent working in the background' });
  await toast.waitFor();
  // Open the chat straight away: the stub holds each focus-todo answer for a
  // moment, so the chat opens while the run is still going.
  await tapOrClick(mobile, toast.locator('.toast-action-btn').filter({ hasText: 'Open chat' }));
}

/** The chat that opened, once its run's last answer is on screen. */
async function landed(page, owner, noteId, text) {
  const sid = (await untilNote(owner.api, noteId, n => !!n?.agent_session_id, 'the linked run')).agent_session_id;
  await page.locator('#notes-pane').waitFor({ state: 'detached' });
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, sid);
  await page.locator('#chat-history').getByText(text).first().waitFor({ timeout: 30000 });
  return sid;
}

/** The latest result of the focus run's search that mentions `marker`. */
const searchResult = marker => until(
  () => modelRequests().flatMap(body => (body.messages || []).filter(m => m.role === 'tool' && m.tool_call_id === 'call_focus_search_1'))
    .map(m => textOf(m.content)).filter(text => text.includes(marker)),
  list => list.length > 0, `the search result for ${marker}`, 30000,
).then(list => list.at(-1));

export async function checkFocusTodo({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const ITEM = `FOCUSTEST priv-${size}`;
  const note = await (await owner.api.post('/api/notes', { data: { title: `Focus ${size}`, content: `- [ ] Something else\n- [ ] ${ITEM}` } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await page.locator(`.note-card[data-note-id="${note.id}"]`).waitFor();

  // 1. My private note: no approval, and the answer names the item.
  await startFromToast(page, mobile, note.id, ITEM);
  await landed(page, owner, note.id, `Done with the focus todo ${ITEM}`);
  assert.equal(await page.locator('#chat-history .ask-user-option').count(), 0, 'no approval card on a private note');
  const read = await searchResult(`Focus ${size}`);
  assert.doesNotMatch(read, /APPROVAL|approval|BLOCKED/, read);
  if (evidence) await page.screenshot({ path: path.join(evidence, `focus-private-${size}.png`), animations: 'disabled' });

  // 2. A follow-up, typed with the composer in Chat mode (the default here).
  assert.notEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('odysseus-toggles') || '{}').mode), 'agent');
  const before = modelRequests().length;
  if (mobile) await page.locator('#message:visible').tap();
  await page.locator('#message:visible').fill('continue');
  await tapOrClick(mobile, page.locator('.send-btn:visible'));
  await page.locator('#chat-history').getByText(`Continuing the focus todo ${ITEM}`).first().waitFor({ timeout: 30000 });
  const followUp = modelRequests().slice(before).find(body => contextText(body).includes(`Focus todo: ${ITEM}`));
  assert.ok(followUp, 'the follow-up carries the focus todo');
  assert.ok(toolNames(followUp).includes('manage_notes'), `the follow-up is an agent turn: ${toolNames(followUp)}`);
  if (evidence) await page.screenshot({ path: path.join(evidence, `focus-follow-up-${size}.png`), animations: 'disabled' });

  // 3. A shared note: the read waits for approval, shown in the chat opened
  // from the toast, and Allow resumes it.
  const friend = await createAccount(admin, `focus-friend-${size}`);
  const SHARED_ITEM = `FOCUSTEST shared-${size}`;
  const shared = await (await owner.api.post('/api/notes', { data: { title: `Shared focus ${size}`, content: `- [ ] ${SHARED_ITEM}` } })).json();
  const grant = await owner.api.post(`/api/notes/${shared.id}/share`, { data: { users: [friend.username], permission: 'edit' } });
  assert.equal(grant.ok(), true, await grant.text());
  await page.evaluate(() => window.notesModule.openPanel());
  await refreshNotes(page);
  await page.locator(`.note-card[data-note-id="${shared.id}"]`).waitFor();
  await startFromToast(page, mobile, shared.id, SHARED_ITEM);
  const allow = page.locator('#chat-history .ask-user-option').filter({ hasText: /Allow for this task/ }).first();
  await landed(page, owner, shared.id, 'Allow for this task');
  if (evidence) await page.screenshot({ path: path.join(evidence, `focus-shared-approval-${size}.png`), animations: 'disabled' });
  await tapOrClick(mobile, allow);
  await page.locator('#chat-history').getByText(`Done with the focus todo ${SHARED_ITEM}`).first().waitFor({ timeout: 30000 });
  assert.doesNotMatch(await searchResult(`Shared focus ${size}`), /APPROVAL|BLOCKED/);

  await context.close();
  for (const id of [note.id, shared.id]) await owner.api.delete(`/api/notes/${id}`);
}
