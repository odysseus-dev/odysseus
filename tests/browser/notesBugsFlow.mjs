// Bugs found while measuring Notes coverage, pinned as failing checks until they are fixed.
// This flow runs after every other one, and runs all its checks before it
// fails, so a known bug never hides a regression elsewhere.
import assert from 'node:assert/strict';
import path from 'node:path';
import { createAccount, getNote, openNotes, refreshNotes, runItemAgent, tapOrClick, until, untilNote } from './notesHarness.mjs';

const WIN = '#note-window-modal';
const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const shot = (page, name) => (evidence ? page.screenshot({ path: path.join(evidence, name), animations: 'disabled' }) : null);

const openFromCard = async (page, mobile, id) => {
  await tapOrClick(mobile, page.locator(`.note-card[data-note-id="${id}"] .note-card-title`));
  const body = page.locator(`${WIN} .note-window-body`);
  await body.waitFor();
  return body;
};

const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator(WIN).waitFor({ state: 'detached' });
};

// P3. The share popover shows a change before it is saved and ignores every
// click until the save answers (`apply()` returns while `busy`). Clicking
// Edit right after switching an account on is dropped without a word: the
// pill looked clickable, the note stays View only. The share request is
// held here until Edit has been clicked, so the check is deterministic.
// Fixed: changes made while one saves are queued and saved in order
// (ui/sharePopover.js).
async function checkEditWhileViewSaves({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const friend = await createAccount(admin, `pill-race-${size}`);
  const note = await (await owner.api.post('/api/notes', { data: { title: `Pill race ${size}`, content: 'x' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  const shares = async () => (await (await owner.api.get(`/api/notes/${note.id}/shares`)).json()).shared_with;
  try {
    await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), note.id);
    await page.locator(`${WIN} .note-window-body`).waitFor();
    await tapOrClick(mobile, page.locator(`${WIN} .note-window-share`));
    const row = page.locator('.note-share-menu .note-share-row').filter({ hasText: friend.username });
    await row.waitFor();
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let posts = 0;
    await page.route('**/api/notes/*/share', async route => { posts += 1; if (posts === 1) await held; await route.continue(); });
    await row.locator(`input[data-user="${friend.username}"]`).check();
    await row.locator('.note-share-pill[data-perm="view"].active').waitFor();
    await tapOrClick(mobile, row.locator('.note-share-pill[data-perm="edit"]'));
    release();
    await until(shares, list => list.length === 1, 'the first grant saved');
    await page.unroute('**/api/notes/*/share');
    // The click on Edit was the user's last choice.
    const final = await until(shares, list => list[0]?.permission === 'edit', 'Edit, clicked while View was saving', 5000).catch(() => null);
    if (!final) await shot(page, `bug-share-pill-${size}.png`);
    assert.ok(final, `Edit clicked while the View grant was saving was dropped: the note is shared as ${JSON.stringify(await shares())}`);
  } finally {
    await context.close();
    await owner.api.delete(`/api/notes/${note.id}`);
  }
}

// P2. A note that already has an undoable agent edit opens without
// "Agent edited <time> · Undo" in its meta line: `open()` draws the meta line
// (`showMeta`) before `applyAccess` sets `ctx.own`, and the meta line only
// offers Undo when `ctx.own` is set (ui/noteWindow.js). The server still
// says `undoable`. The agentedit flow never saw it: there the window is
// already open when the edit arrives. After a reload or a close, the toast is
// gone and History is the only way back. Fixed: showMeta reads ownership
// from the note itself.
async function checkUndoAfterReopen({ browser, owner, size }) {
  const mobile = size === 'phone';
  // Bytes a re-serialization would change: a double space, a trailing blank.
  const CONTENT = `Keep  these bytes \n\n- [ ] EDITTEST reload-${size}`;
  const note = await (await owner.api.post('/api/notes', { data: { title: `Undo later ${size}`, content: CONTENT } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  try {
    let body = await openFromCard(page, mobile, note.id);
    const row = page.locator(`${WIN} li.md-task`).filter({ hasText: 'EDITTEST' });
    if (!mobile) await row.hover();
    await runItemAgent(mobile, row.locator('.note-task-agent'));
    await until(() => getNote(owner.api, note.id), r => r.note?.content?.startsWith(`Edited by the agent: reload-${size}`), 'the agent edit', 30000);
    await until(() => body.textContent(), text => text.includes('Edited by the agent'), 'the edit in the window', 15000);
    const undoable = async step => {
      const { note: now } = await getNote(owner.api, note.id);
      assert.equal(now.agent_edit?.undoable, true, `${step}: ${JSON.stringify({ agent_edit: now.agent_edit, content: now.content, updated_at: now.updated_at })}`);
      return now;
    };
    const edited = await undoable('once the run is over');
    await closeWindow(page);
    const closed = await undoable('after closing the window');
    assert.equal(closed.updated_at, edited.updated_at, 'closing the window wrote nothing');

    // After a reload the toast is long gone; the meta line still offers Undo.
    await refreshNotes(page);
    await undoable('after a reload');
    body = await openFromCard(page, mobile, note.id);
    await undoable('after opening the note again');
    const meta = page.locator(`${WIN} .note-window-meta`);
    if (!/Agent edited/.test(await meta.textContent())) await shot(page, `bug-meta-undo-${size}.png`);
    assert.match(await meta.textContent(), /Agent edited .* · Undo · History \(\d+\)$/, 'the server says the edit can be undone; the reopened window should offer Undo');
    await tapOrClick(mobile, meta.locator('.note-window-meta-undo:not(.note-window-meta-history)'));
    const restored = await untilNote(owner.api, note.id, n => n?.content === CONTENT, 'the undone note');
    assert.equal(restored.agent_edit, null);
    await until(() => body.textContent(), text => text.includes('Keep') && !text.includes('Edited by the agent'), 'the window back to the note', 15000);
    assert.doesNotMatch(await meta.textContent(), /Agent edited/);
    await closeWindow(page);
  } finally {
    await context.close();
    await owner.api.delete(`/api/notes/${note.id}`);
  }
}

// A delete that fails because the network is down: `deleteNow` removes the
// card, the DELETE fails, and the re-read meant to restore the list fails
// too. The card must come back and the other cards stay; a failed re-read
// used to empty the list (unit: notesWorkflows, "a delete or leave that
// fails offline keeps every card on screen").
async function checkOfflineDelete({ browser, owner, size }) {
  const mobile = size === 'phone';
  const post = data => owner.api.post('/api/notes', { data }).then(r => r.json());
  const doomed = await post({ title: `Offline delete ${size}`, content: 'x' });
  const kept = await post({ title: `Offline bystander ${size}`, content: 'y' });
  const { context, page } = await openNotes(browser, owner, size);
  try {
    await openFromCard(page, mobile, doomed.id);
    await context.setOffline(true);
    // The re-read that should put the card back: it fails offline too.
    const reread = page.waitForEvent('requestfailed', r => new URL(r.url()).pathname === '/api/notes' && r.method() === 'GET');
    await tapOrClick(mobile, page.locator(`${WIN} [data-act="delete"]`));
    await tapOrClick(mobile, page.locator('#styled-confirm-ok'));
    await page.locator('#toast').filter({ hasText: 'Failed to delete' }).waitFor();
    await reread;
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const shown = await page.locator('#notes-pane .note-card[data-note-id]').count();
    await context.setOffline(false);
    const empty = await page.locator(`.note-card[data-note-id="${kept.id}"]`).count() === 0;
    const gone = await page.locator(`.note-card[data-note-id="${doomed.id}"]`).count() === 0;
    if (empty || gone) await shot(page, `bug-offline-delete-${size}.png`);
    assert.ok(!empty, `after a delete that failed offline the panel shows ${shown} cards; the other notes should stay`);
    assert.ok(!gone, 'the note the server did not delete should be back in the list');
  } finally {
    await context.setOffline(false).catch(() => {});
    await context.close();
    for (const note of [doomed, kept]) await owner.api.delete(`/api/notes/${note.id}`);
  }
}

export async function checkKnownBugs(args) {
  const failures = [];
  for (const [name, check] of [['share pill clicked while saving', checkEditWhileViewSaves], ['agent Undo in the meta line after reopening', checkUndoAfterReopen], ['cards kept after a delete fails offline', checkOfflineDelete]]) {
    try { await check(args); }
    catch (error) { failures.push(`${name} (${args.size}): ${error.message.split('\n')[0]}`); }
  }
  return failures;
}
