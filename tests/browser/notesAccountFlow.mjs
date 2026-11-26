// Sharing stays out of the chat agent, and deleting an account deletes the
// notes it owns: the chat agent's share call is refused after approval, an
// open window on another account learns the note is gone, the admin dialog
// says so up front, and a failed cleanup is reported in the admin UI.
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  createAccount, lastUserText, modelRequests, openNotes, origin, refreshNotes, sqlite, tapOrClick, toolNames, until, VIEWPORTS,
} from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const REFUSAL = 'Sharing is changed from the Notes window';
const GONE = 'This note no longer exists or is no longer shared with you. Changes here are not saved.';
const DIALOG = name => `Remove user "${name}"? Their notes are deleted too, including notes they shared: collaborators lose them.`;

/** The owner's chat, in agent mode, on the default (stub) model. */
async function openChat(browser, owner, size) {
  const context = await browser.newContext({ ...VIEWPORTS[size], storageState: owner.storage });
  await context.addInitScript(() => localStorage.setItem('odysseus-toggles', JSON.stringify({ mode: 'agent' })));
  const page = await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => window.sessionModule?.createDirectChat);
  const chat = await (await owner.api.get('/api/default-chat')).json();
  await page.evaluate(({ url, model, id }) => window.sessionModule.createDirectChat(url, model, id), { url: chat.endpoint_url, model: chat.model, id: chat.endpoint_id });
  return { context, page };
}

export async function checkAgentCannotShare({ browser, owner, size, target }) {
  const note = await (await owner.api.post('/api/notes', { data: { title: `Private ${size}`, content: 'not for sharing' } })).json();
  const { context, page } = await openChat(browser, owner, size);
  const message = `Update my notes: share the note ${note.title} with ${target}. SHARETEST ${note.id} ${target}`;
  await page.locator('#message:visible').fill(message);
  await tapOrClick(size === 'phone', page.locator('.send-btn:visible'));
  // The model's manage_notes call stops at the approval gate; allowing it
  // still changes nothing.
  const allow = page.locator('.ask-user-option').filter({ hasText: /Allow/ }).first();
  await allow.waitFor({ timeout: 30000 });
  const offered = (await until(() => modelRequests().filter(b => lastUserText(b).includes(message)), r => r.length > 0, 'the share request')).at(0);
  assert.ok(toolNames(offered).includes('manage_notes'), 'plain chat still offers manage_notes, without share');
  const schema = offered.tools.find(t => t.function?.name === 'manage_notes').function.parameters;
  assert.equal(JSON.stringify(schema.properties.action.enum).includes('share'), false, `share in the schema: ${JSON.stringify(schema.properties.action.enum)}`);
  await tapOrClick(size === 'phone', allow);
  // The approved call runs and its refusal is shown as the turn's answer.
  await page.locator('#chat-history p').filter({ hasText: `The approved manage_notes action could not run: ${REFUSAL}` }).first().waitFor({ timeout: 30000 });
  if (evidence) await page.screenshot({ path: path.join(evidence, `agent-share-refused-${size}.png`), animations: 'disabled' });
  assert.deepEqual(sqlite('SELECT count(*) FROM note_shares WHERE note_id = ?', [note.id]), [[0]]);
  assert.deepEqual((await (await owner.api.get(`/api/notes/${note.id}/shares`)).json()).shared_with, []);
  await context.close();
  await owner.api.delete(`/api/notes/${note.id}`);
}

/** Admin → Users → Remove, through the real dialog. Returns the error toast text, if any. */
async function removeInAdmin(page, name, { removed = true } = {}) {
  await page.evaluate(() => window.adminModule.open('users'));
  const remove = page.locator(`[data-adm-del-user="${name}"]`);
  await remove.waitFor();
  await remove.click();
  const msg = page.locator('#styled-confirm-msg');
  await msg.waitFor();
  assert.equal(await msg.textContent(), DIALOG(name));
  if (evidence) await page.screenshot({ path: path.join(evidence, `admin-delete-dialog-${name}.png`), animations: 'disabled' });
  await page.locator('#styled-confirm-ok').click();
  if (removed) await remove.waitFor({ state: 'detached' });
}

export async function checkAccountDeletion({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const doomed = await createAccount(admin, `doomed-${size}`);
  const witness = await createAccount(admin, `witness-${size}`);
  const shared = await (await doomed.api.post('/api/notes', { data: { title: `Doomed shared ${size}`, content: '- [ ] shared task' } })).json();
  const own = await (await doomed.api.post('/api/notes', { data: { title: `Doomed private ${size}`, content: 'private' } })).json();
  assert.equal((await doomed.api.post(`/api/notes/${shared.id}/share`, { data: { users: [witness.username], permission: 'edit' } })).ok(), true);
  // A grant the account holds on someone else's note goes too.
  const ownersNote = await (await owner.api.post('/api/notes', { data: { title: `Owner shares with doomed ${size}`, content: 'x' } })).json();
  assert.equal((await owner.api.post(`/api/notes/${ownersNote.id}/share`, { data: { users: [doomed.username], permission: 'view' } })).ok(), true);

  // The witness has the doomed account's note open while the admin removes it.
  const { context, page } = await openNotes(browser, witness, size);
  const card = page.locator(`.note-card[data-note-id="${shared.id}"]`);
  await tapOrClick(mobile, card.locator('.note-card-title'));
  const win = page.locator('#note-window-modal');
  await win.locator('.note-window-body').waitFor();

  const adminSide = await openNotes(browser, admin, size);
  await removeInAdmin(adminSide.page, doomed.username);
  assert.deepEqual(sqlite('SELECT count(*) FROM notes WHERE lower(owner) = ?', [doomed.username]), [[0]]);
  assert.deepEqual(sqlite('SELECT count(*) FROM note_shares WHERE note_id IN (?, ?) OR shared_with = ?', [shared.id, own.id, doomed.username]), [[0]]);
  assert.equal((await owner.api.get(`/api/notes/${ownersNote.id}`)).ok(), true, 'the owner\'s own note stays');

  // Typing in the open window: one save, a 404, the banner; the card goes and no draft is left.
  const writes = [];
  page.on('response', r => { if (r.request().method() === 'PUT' && r.url().includes(`/api/notes/${shared.id}`)) writes.push(r.status()); });
  await win.locator('.md-task-text').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' typed after removal');
  await win.locator('.note-window-banner').filter({ hasText: GONE }).waitFor();
  assert.deepEqual(writes, [404]);
  await card.waitFor({ state: 'detached' });
  assert.equal(await page.evaluate(id => localStorage.getItem('odysseus-note-draft-' + id), shared.id), null, 'no draft left');
  if (evidence) await page.screenshot({ path: path.join(evidence, `deleted-owner-banner-${size}.png`), animations: 'disabled' });
  await page.keyboard.type(' and more');
  await win.locator('.note-window-banner button', { hasText: 'Close' }).click();
  await win.waitFor({ state: 'detached' });
  assert.deepEqual(writes, [404], 'nothing more is sent once the note is gone');
  await refreshNotes(page);
  assert.equal(await page.locator(`.note-card[data-note-id="${shared.id}"]`).count(), 0);
  assert.equal(await page.locator('.notes-shared-header').count(), 0);

  // A cleanup that fails: the account and its note are kept, the admin is told, and a retry finishes.
  const jammed = await createAccount(admin, `jammed-${size}`);
  const stuck = await (await jammed.api.post('/api/notes', { data: { title: 'Stuck', content: 'cannot be deleted' } })).json();
  const trigger = `jam_${size}`;
  sqlite(`CREATE TRIGGER ${trigger} BEFORE DELETE ON notes WHEN OLD.owner = '${jammed.username}' BEGIN SELECT RAISE(ABORT, 'jammed for the test'); END`);
  try {
    await removeInAdmin(adminSide.page, jammed.username, { removed: false });
    await adminSide.page.locator('#toast.error').filter({ hasText: "The account's notes could not be removed, so the account was kept." }).waitFor();
    if (evidence) await adminSide.page.screenshot({ path: path.join(evidence, `admin-cleanup-warning-${size}.png`), animations: 'disabled' });
  } finally {
    sqlite(`DROP TRIGGER IF EXISTS ${trigger}`);
  }
  let users = await (await admin.api.get('/api/auth/users')).json();
  assert.equal(JSON.stringify(users).includes(`"${jammed.username}"`), true, 'the account is kept');
  assert.deepEqual(sqlite('SELECT id FROM notes WHERE id = ?', [stuck.id]), [[stuck.id]], 'and so is its note');
  // Retry from a fresh page, as an admin would after reading the message.
  await adminSide.page.reload();
  await adminSide.page.waitForFunction(() => !!window.adminModule);
  await removeInAdmin(adminSide.page, jammed.username);
  users = await (await admin.api.get('/api/auth/users')).json();
  assert.equal(JSON.stringify(users).includes(`"${jammed.username}"`), false, 'the retry deletes the account');
  assert.deepEqual(sqlite('SELECT count(*) FROM notes WHERE id = ?', [stuck.id]), [[0]], 'with its note');
  await adminSide.context.close();
  await context.close();
  await owner.api.delete(`/api/notes/${ownersNote.id}`);
}
