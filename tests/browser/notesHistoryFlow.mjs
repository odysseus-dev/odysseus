// The note window's History: a "Now" row, then the changes newest first with
// who made them and what they did, grouped by day; the chosen change shows
// what it did (its added lines green, its removed lines red); an
// older change can also show what undoing it and everything after it would
// do to the note as it is; "Undo this change" puts the version before it
// back, and the toast's Undo undoes the restore.
// The meta line says "History (N)". From a real agent edit (stub model),
// "See what changed" in the toast and in the chat open the History on it.
import assert from 'node:assert/strict';
import path from 'node:path';
import { getNote, openNotes, runItemAgent, sqlite, tapOrClick, until } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;

const versionsOf = async (api, id) => (await (await api.get(`/api/notes/${id}/versions`)).json()).versions;
const shot = (page, name) => (evidence ? page.screenshot({ path: path.join(evidence, name), animations: 'disabled' }) : null);

export async function checkHistory({ browser, owner, size, agent = false }) {
  const mobile = size === 'phone';
  const V1 = `First words ${size}\n\n- [ ] pack boots`;
  const V2 = `Second words ${size}\n\n- [x] pack boots`;
  const V3 = `Third words ${size}`;
  const CHAT = `history-chat-${size}`;
  const note = await (await owner.api.post('/api/notes', { data: { title: `History ${size}`, content: V1 } })).json();
  assert.equal((await owner.api.put(`/api/notes/${note.id}`, { data: { content: V2 } })).ok(), true);
  // That version stands for an agent edit from a named chat an hour ago, so
  // the next save is outside the gap and the list shows both kinds.
  sqlite("UPDATE note_versions SET source = 'agent', session_id = ?, created_at = datetime(created_at, '-1 hour') WHERE note_id = ?", [CHAT, note.id]);
  sqlite("INSERT INTO sessions (id, name, endpoint_url, model, owner, archived, created_at, updated_at) VALUES (?, ?, '', 'stub', ?, 0, datetime('now'), datetime('now'))",
    [CHAT, `Agent: History ${size}`, owner.username]);
  // Text sent on its own keeps the note's task lines: this is what is stored.
  const third = await (await owner.api.put(`/api/notes/${note.id}`, { data: { content: V3 } })).json();
  assert.ok(third.content.startsWith(V3));
  const listed = await versionsOf(owner.api, note.id);
  assert.deepEqual(listed.map(v => v.source), ['user', 'agent']);
  assert.deepEqual(listed.map(v => v.summary), ['edited the text', 'ticked “pack boots”, edited the text']);

  // The note a real agent run edits later, there before the panel opens.
  const seen = agent ? await (await owner.api.post('/api/notes', { data: { title: `See ${size}`, content: `Plan\n\n- [ ] EDITTEST seen-${size}` } })).json() : null;

  const { context, page } = await openNotes(browser, owner, size);
  const win = page.locator('#note-window-modal');
  await tapOrClick(mobile, page.locator(`.note-card[data-note-id="${note.id}"] .note-card-title`));
  const body = win.locator('.note-window-body');
  await body.waitFor();
  // The meta line names the history; the footer icon stays.
  const metaLink = win.locator('.note-window-meta-history');
  await until(() => metaLink.textContent().catch(() => ''), text => text === 'History (2)', 'History (2) in the meta line');
  assert.equal(await win.locator('[data-act="history"]').isVisible(), true, 'the footer icon is still there');
  await tapOrClick(mobile, metaLink);

  const panel = win.locator('.note-window-history');
  const rows = panel.locator('[data-vid]');
  await until(() => rows.count(), n => n === 3, 'Now plus two changes');
  assert.equal(await body.isVisible(), false, 'the history takes the body\'s place');
  assert.match(await rows.nth(0).textContent(), /^Now/);
  assert.match(await rows.nth(1).textContent(), /^You edited the text/);
  assert.match(await rows.nth(1).textContent(), /just now|min ago/);
  assert.match(await rows.nth(2).textContent(), new RegExp(`^Agent in Agent: History ${size} ticked “pack boots”, edited the text`));
  assert.ok(await rows.nth(2).locator('[title]').count() > 0, 'the exact time is in a tooltip');
  assert.ok(await panel.locator('.note-window-history-day').count() >= 1, 'grouped by day');

  // The newest change is chosen at once, shown as what it did: the words it
  // wrote are added (green), the ones it replaced removed (red, struck).
  const preview = panel.locator('.note-window-history-preview');
  const change = panel.locator('.note-window-history-preview:not([data-part="undo"]) .note-window-history-diff');
  const removed = change.locator('.note-window-history-del');
  const added = change.locator('.note-window-history-add');
  await until(() => added.allTextContents(), list => list.some(t => t.includes(`Third words ${size}`)), 'what the change wrote, added');
  assert.ok((await removed.allTextContents()).some(t => t.includes(`Second words ${size}`)), 'what the change replaced, removed');
  assert.ok(!(await removed.allTextContents()).some(t => t.includes(`Third words ${size}`)), 'never the undo shown as the change');
  assert.equal(await removed.first().evaluate(el => getComputedStyle(el).textDecorationLine), 'line-through');
  assert.equal(await added.first().evaluate(el => getComputedStyle(el).textDecorationLine), 'none');
  assert.match(await preview.first().textContent(), /What this change did:/);
  assert.equal(await panel.locator('[data-act="restore"]').textContent(), 'Undo this change');
  assert.equal(await panel.locator('[data-act="undo-preview"]').count(), 0, 'nothing after the newest change: its undo is this diff reversed');
  await shot(page, `t-history-diff-${size}.png`);

  // Now is the note as it is.
  await tapOrClick(mobile, rows.nth(0));
  await until(() => preview.textContent().catch(() => ''), text => text.includes('as it is now'), 'the Now row');

  // The agent's change names its chat, clickable.
  await tapOrClick(mobile, rows.nth(2));
  await until(() => added.allTextContents(), list => list.some(t => t.includes('- [x] pack boots')), 'the agent ticked the task');
  assert.ok((await removed.allTextContents()).some(t => t.includes('- [ ] pack boots')), 'the unticked line it replaced');
  assert.ok((await added.allTextContents()).some(t => t.includes(`Second words ${size}`)), 'the words the agent wrote');
  assert.ok(!(await change.textContent()).includes(`Third words ${size}`), 'a later change is not part of this one');
  // A change with later ones: the undo goes back over them too, and says so.
  assert.equal(await panel.locator('[data-act="restore"]').textContent(), 'Undo this change and everything after it');
  const undoPart = panel.locator('[data-part="undo"]');
  assert.equal(await undoPart.count(), 0, 'what undoing does is shown on demand');
  await tapOrClick(mobile, panel.locator('[data-act="undo-preview"]'));
  await undoPart.waitFor();
  assert.match(await undoPart.textContent(), /Undoing this change and everything after it/);
  assert.ok((await undoPart.locator('.note-window-history-del').allTextContents()).some(t => t.includes(`Third words ${size}`)), 'the undo removes the later words');
  assert.ok((await undoPart.locator('.note-window-history-add').allTextContents()).some(t => t.includes(`First words ${size}`)), 'and brings the first ones back');
  await shot(page, `t-history-undo-preview-${size}.png`);
  const chatLink = panel.locator('[data-act="chat"]');
  assert.equal(await chatLink.textContent(), `Agent: History ${size}`);
  await shot(page, `t-history-list-${size}.png`);

  // Undo this change and everything after it, then the toast's Undo.
  await tapOrClick(mobile, panel.locator('[data-act="restore"]'));
  await panel.waitFor({ state: 'hidden' });
  await until(() => body.textContent(), text => text.includes(`First words ${size}`), 'the restored note in the window');
  assert.equal((await getNote(owner.api, note.id)).note.content, V1, 'the exact bytes are back');
  const toast = page.locator('#toast').filter({ hasText: 'Restored the version from' });
  await toast.waitFor();
  await shot(page, `t-history-toast-${size}.png`);
  await tapOrClick(mobile, toast.locator('.toast-action-btn').filter({ hasText: 'Undo' }));
  await until(() => body.textContent(), text => text.includes(`Third words ${size}`), 'the restore undone');
  assert.equal((await getNote(owner.api, note.id)).note.content, third.content);
  assert.deepEqual((await versionsOf(owner.api, note.id)).map(v => v.source), ['restore', 'restore', 'user', 'agent']);
  await until(() => metaLink.textContent().catch(() => ''), text => text === 'History (4)', 'the count follows');

  // Opened for a chat: that chat's latest agent edit is chosen.
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await win.waitFor({ state: 'detached' });
  assert.equal(await page.evaluate(([id, sid]) => window.notesModule.openNoteHistory(id, { sessionId: sid }), [note.id, CHAT]), true);
  await until(() => panel.locator('[data-vid].active').textContent().catch(() => ''), text => text.startsWith('Agent in'), 'the chat\'s edit chosen');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await win.waitFor({ state: 'detached' });

  if (seen) await checkSeeWhatChanged({ page, owner, size, mobile, note: seen });
  await context.close();
  await owner.api.delete(`/api/notes/${note.id}`);
}

/** A real agent edit (the stub model's EDITTEST run): the toast and the chat's tool row both open the History on it. */
async function checkSeeWhatChanged({ page, owner, size, mobile, note }) {
  const card = page.locator(`.note-card[data-note-id="${note.id}"]`);
  await card.waitFor({ timeout: 15000 });
  const win = page.locator('#note-window-modal');
  await tapOrClick(mobile, card.locator('.note-card-title'));
  await win.locator('.note-window-body').waitFor();
  const row = win.locator('li.md-task').filter({ hasText: 'EDITTEST' });
  if (!mobile) await row.hover();
  await runItemAgent(mobile, row.locator('.note-task-agent'));
  const toast = page.locator('#toast').filter({ hasText: 'Agent edited this note' });
  await toast.waitFor({ timeout: 30000 });
  await shot(page, `t-agent-toast-${size}.png`);
  await tapOrClick(mobile, toast.locator('.toast-action-btn').filter({ hasText: 'See what changed' }));
  const panel = win.locator('.note-window-history');
  const active = panel.locator('[data-vid].active');
  await until(() => active.textContent().catch(() => ''), text => text.startsWith('Agent in '), 'the agent edit chosen from the toast', 15000);
  // The version's diff is fetched after the row is chosen: wait for it.
  await until(() => panel.locator('.note-window-history-add').allTextContents(), adds => adds.some(t => t.includes(`Edited by the agent: seen-${size}`)), 'the diff shows the agent text as added', 15000);
  assert.ok(!(await panel.locator('.note-window-history-del').allTextContents()).some(t => t.includes('Edited by the agent')), 'the agent text is not shown as removed');

  // The chat's tool row has the same link.
  const sid = (await getNote(owner.api, note.id)).note.agent_session_id;
  assert.ok(sid, 'the run is linked');
  await tapOrClick(mobile, panel.locator('[data-act="back"]'));
  await tapOrClick(mobile, win.locator('[data-act="agent-chat"]'));
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, sid);
  const link = page.locator('a[data-note-history]').first();
  await link.waitFor({ state: 'attached', timeout: 15000 });
  await link.evaluate(el => el.closest('.agent-thread-node')?.classList.add('open'));
  await link.click();
  await until(() => page.locator('#note-window-modal .note-window-history [data-vid].active').textContent().catch(() => ''), text => text.startsWith('Agent in'), 'the agent edit chosen from the chat', 15000);
  await shot(page, `t-see-from-chat-${size}.png`);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await owner.api.delete(`/api/notes/${note.id}`);
}
