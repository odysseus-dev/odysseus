// Critical flows no other flow drove end to end:
// - the owner turns a share off in the popover while the collaborator has the
//   note open: the collaborator's next keystroke finds it gone, and the note
//   and its media then 404 for them;
// - the owner deletes a shared note under the collaborator's open window;
// - a sent message's note card keeps the title the note had when it was sent.
import assert from 'node:assert/strict';
import path from 'node:path';
import { createAccount, getNote, openNotes, origin, pngOf, refreshNotes, tapOrClick, until, untilNote, uploadPng, VIEWPORTS } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const WIN = '#note-window-modal';
const GONE = 'This note no longer exists or is no longer shared with you. Changes here are not saved.';

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

/** Put the caret at the end of the body's first paragraph and type. */
async function typeAtEnd(page, text) {
  await page.evaluate(() => {
    const p = document.querySelector('#note-window-modal .note-window-body p');
    const range = document.createRange(); range.selectNodeContents(p); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    p.closest('[contenteditable="true"]').focus();
  });
  await page.keyboard.type(text);
}

/** The collaborator types into an open window whose note went away: the save gets a 404 and the window says so. */
async function expectGoneOnNextSave(page, id) {
  const saved = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname === `/api/notes/${id}`);
  await typeAtEnd(page, ' late edit');
  assert.equal((await saved).status(), 404);
  await page.locator(`${WIN} .note-window-banner`).filter({ hasText: GONE }).waitFor();
  assert.match(await page.locator(`${WIN} .note-window-body`).textContent(), /late edit/, 'the text stays on screen so it can be copied');
  await page.locator(`.note-card[data-note-id="${id}"]`).waitFor({ state: 'detached' });
  const draft = await page.evaluate(id => localStorage.getItem(`odysseus-note-draft-${id}`), id);
  assert.equal(draft, null, 'no draft is kept for a note that is gone');
}

export async function checkRevokeAndDelete({ browser, owner, admin, size }) {
  const mobile = size === 'phone';
  const collab = await createAccount(admin, `revoke-${size}`);
  const ownerSide = await openNotes(browser, owner, size);
  const image = await uploadPng(owner.api, await pngOf(ownerSide.page, '#5a7fa0'), 'revoke.png');
  const upload = image.split('/').pop();
  const post = data => owner.api.post('/api/notes', { data }).then(r => r.json());
  const revoked = await post({ title: `Revoke ${size}`, content: `Shared then revoked\n\n![](${image})` });
  const deleted = await post({ title: `Deleted under ${size}`, content: 'Shared then deleted' });
  for (const note of [revoked, deleted]) {
    const grant = await owner.api.post(`/api/notes/${note.id}/share`, { data: { users: [collab.username], permission: 'edit' } });
    assert.equal(grant.ok(), true, await grant.text());
  }
  const collabSide = await openNotes(browser, collab, size);
  const collabPage = collabSide.page;
  try {
    // 1. Revoke from the owner's popover while the collaborator has the note open.
    await openFromCard(collabPage, mobile, revoked.id);
    assert.equal((await collab.api.get(`/api/notes/${revoked.id}/media/${upload}`)).status(), 200, 'the image loads through the note while shared');
    await refreshNotes(ownerSide.page);
    await openFromCard(ownerSide.page, mobile, revoked.id);
    const share = ownerSide.page.locator(`${WIN} .note-window-share`);
    assert.equal((await share.locator('.note-window-share-count').textContent()).trim(), '1');
    await tapOrClick(mobile, share);
    const row = ownerSide.page.locator('.note-share-menu .note-share-row').filter({ hasText: collab.username });
    const toggle = row.locator(`input[data-user="${collab.username}"]`);
    await toggle.waitFor();
    assert.equal(await toggle.isChecked(), true);
    await toggle.uncheck();
    const shares = async () => (await (await owner.api.get(`/api/notes/${revoked.id}/shares`)).json()).shared_with;
    await until(shares, list => list.length === 0, 'the share removed');
    await until(async () => (await share.locator('.note-window-share-count').textContent()).trim(), text => text === '', 'no count on Share once nobody has it');
    if (evidence) await ownerSide.page.screenshot({ path: path.join(evidence, `revoke-popover-${size}.png`), animations: 'disabled' });
    await ownerSide.page.keyboard.press('Escape');
    await closeWindow(ownerSide.page);

    await expectGoneOnNextSave(collabPage, revoked.id);
    if (evidence) await collabPage.screenshot({ path: path.join(evidence, `revoke-gone-${size}.png`), animations: 'disabled' });
    await closeWindow(collabPage);
    assert.equal((await getNote(collab.api, revoked.id)).status, 404);
    assert.equal((await collab.api.get(`/api/notes/${revoked.id}/media/${upload}`)).status(), 404, 'the image goes with the share');
    assert.equal((await owner.api.get(`/api/notes/${revoked.id}/media/${upload}`)).status(), 200, 'and stays the owner\'s');
    assert.equal((await getNote(owner.api, revoked.id)).note.content, revoked.content, 'the late edit wrote nothing');
    await refreshNotes(collabPage);
    assert.equal(await collabPage.locator(`.note-card[data-note-id="${revoked.id}"]`).count(), 0);

    // 2. The owner deletes a shared note while the collaborator has it open.
    await openFromCard(collabPage, mobile, deleted.id);
    assert.equal((await owner.api.delete(`/api/notes/${deleted.id}`)).ok(), true);
    await expectGoneOnNextSave(collabPage, deleted.id);
    await closeWindow(collabPage);
    await refreshNotes(collabPage);
    assert.equal(await collabPage.locator(`.note-card[data-note-id="${deleted.id}"]`).count(), 0, 'gone after a refresh too');
  } finally {
    await collabSide.context.close();
    await ownerSide.context.close();
    await owner.api.delete(`/api/notes/${revoked.id}`);
  }
}

export async function checkSentCardKeepsTitle({ browser, owner, size }) {
  const mobile = size === 'phone';
  const note = await (await owner.api.post('/api/notes', { data: { title: `Before rename ${size}`, content: 'Carried as context' } })).json();
  const context = await browser.newContext({ ...VIEWPORTS[size], storageState: owner.storage });
  await context.addInitScript(() => {
    localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
    localStorage.setItem('odysseus-toggles', JSON.stringify({ mode: 'agent' }));
  });
  const page = await context.newPage();
  try {
    await page.goto(origin);
    await page.waitForFunction(() => window.notesModule?.openPanel && window.sessionModule?.createDirectChat);
    const chat = await (await owner.api.get('/api/default-chat')).json();
    await page.evaluate(({ url, model, id }) => window.sessionModule.createDirectChat(url, model, id), { url: chat.endpoint_url, model: chat.model, id: chat.endpoint_id });
    await page.evaluate(() => window.notesModule.openPanel());
    await page.locator('#notes-select-btn').click();
    await page.locator(`#notes-pane .note-card[data-note-id="${note.id}"] .note-card-cb`).check({ force: mobile });
    await page.locator('#notes-send-agent').click();
    await page.evaluate(() => window.notesModule.closePanel());
    const text = `CTXRENAME ${size}`;
    const replies = page.locator('#chat-history .msg').filter({ hasText: 'Stub reply:' });
    if (mobile) await page.locator('#message:visible').tap();
    await page.locator('#message:visible').fill(text);
    await tapOrClick(mobile, page.locator('.send-btn:visible'));
    await until(() => replies.count(), n => n > 0, 'the reply', 30000);
    await page.waitForFunction(() => document.getElementById('chat-container')?.getAttribute('aria-busy') !== 'true');
    const names = () => page.locator('#chat-history .msg').filter({ hasText: text }).first().locator('.context-note-card .attach-card-name').allInnerTexts();
    assert.deepEqual(await until(names, list => list.length === 1, 'the sent card'), [note.title]);
    const sid = await page.evaluate(() => window.sessionModule.getCurrentSessionId());

    // Renamed afterwards: the message still names what was sent; the note itself has the new name.
    assert.equal((await owner.api.put(`/api/notes/${note.id}`, { data: { title: `After rename ${size}` } })).ok(), true);
    await page.reload();
    await page.waitForFunction(() => window.sessionModule?.selectSession && window.notesModule?.openNoteWindow);
    await page.evaluate(async id => { await window.sessionModule.loadSessions(); await window.sessionModule.selectSession(id); }, sid);
    assert.deepEqual(await until(names, list => list.length === 1, 'the card after a reload'), [`Before rename ${size}`]);
    await tapOrClick(mobile, page.locator('#chat-history .msg').filter({ hasText: text }).first().locator('.context-note-card').first());
    await page.locator(`${WIN} .note-window-body`).waitFor();
    assert.equal(await page.locator(`${WIN} .note-window-title`).inputValue(), `After rename ${size}`, 'the card opens the note as it is now');
    await closeWindow(page);
  } finally {
    await context.close();
    await owner.api.delete(`/api/notes/${note.id}`);
  }
}

/** Select `text` in the window body (first match); `collapseToEnd` leaves a caret after it. */
const selectInBody = (page, text, { collapseToEnd = false } = {}) => page.evaluate(({ text, collapseToEnd }) => {
  const host = document.querySelector('#note-window-modal .note-window-body');
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const at = node.nodeValue.indexOf(text);
    if (at < 0) continue;
    host.focus();
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + text.length);
    if (collapseToEnd) range.collapse(false);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return true;
  }
  return false;
}, { text, collapseToEnd });

// K's formatting with no other e2e step: highlight from the keyboard (the
// toolbar on a phone), superscript and subscript from the toolbar, and an
// image from elsewhere, shown as a link that is never fetched, edited through
// its own menu and written back as the image it is.
export async function checkFormattingAndImageLinks({ browser, owner, size }) {
  const mobile = size === 'phone';
  const SOURCE = 'Make this high\n\nE = mc2 and H2O\n\nSee ![map](https://images.invalid/map.png) now';
  const note = await (await owner.api.post('/api/notes', { data: { title: `Formatting ${size}`, content: SOURCE } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  const outside = [];
  page.on('request', request => { if (/\.invalid$/.test(new URL(request.url()).hostname)) outside.push(request.url()); });
  let expected = SOURCE;
  const saved = label => untilNote(owner.api, note.id, n => n?.content === expected, label);
  const win = page.locator(WIN);
  try {
    await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), note.id);
    const body = win.locator('.note-window-body');
    await body.waitFor();

    assert.ok(await selectInBody(page, 'high'));
    if (mobile) await win.locator('[data-md="highlight"]').tap();
    else await page.keyboard.press(process.platform === 'darwin' ? 'Meta+Shift+H' : 'Control+Shift+H');
    expected = expected.replace('Make this high', 'Make this <mark>high</mark>');
    await saved('the highlight');

    assert.ok(await selectInBody(page, '2 and'));
    await page.evaluate(() => { const s = getSelection(); const r = s.getRangeAt(0); r.setEnd(r.startContainer, r.startOffset + 1); });
    await tapOrClick(mobile, win.locator('[data-md="sup"]'));
    expected = expected.replace('mc2', 'mc<sup>2</sup>');
    await saved('the superscript');
    assert.ok(await selectInBody(page, '2O'));
    await page.evaluate(() => { const s = getSelection(); const r = s.getRangeAt(0); r.setEnd(r.startContainer, r.startOffset + 1); });
    await tapOrClick(mobile, win.locator('[data-md="sub"]'));
    expected = expected.replace('H2O', 'H<sub>2</sub>O');
    await saved('the subscript');

    const chip = body.locator('a[data-md-image]');
    assert.equal(await chip.count(), 1, 'the image from elsewhere is a link');
    assert.equal(await body.locator('img[src*="images.invalid"]').count(), 0);
    assert.ok(await selectInBody(page, 'map', { collapseToEnd: true }));
    await tapOrClick(mobile, win.locator('[data-md="link"]'));
    const menu = page.locator('.note-image-link-menu');
    await menu.waitFor();
    assert.equal(await menu.locator('.note-link-input').inputValue(), 'https://images.invalid/map.png');
    assert.equal(await menu.locator('.note-link-title').inputValue(), 'map');
    await menu.locator('.note-link-input').fill('javascript:alert(1)');
    await tapOrClick(mobile, menu.locator('[data-act="apply"]'));
    assert.equal(await menu.locator('.note-link-input.invalid').count(), 1, 'only an http(s) image address is taken');
    await menu.locator('.note-link-input').fill('https://images.invalid/plan.png');
    await menu.locator('.note-link-title').fill('plan');
    if (evidence) await page.screenshot({ path: path.join(evidence, `image-link-menu-${size}.png`), animations: 'disabled' });
    await tapOrClick(mobile, menu.locator('[data-act="apply"]'));
    expected = expected.replace('![map](https://images.invalid/map.png)', '![plan](https://images.invalid/plan.png)');
    await saved('the edited image link');
    assert.equal((await getNote(owner.api, note.id)).note.content, expected, 'every other byte kept');
    await closeWindow(page);
    assert.deepEqual(outside, [], 'nothing was fetched from elsewhere');
  } finally {
    await context.close();
    await owner.api.delete(`/api/notes/${note.id}`);
  }
}

// The reminder picker in the window (preset, weekly repeat, a typed date,
// removal), then the panel's search and filter chips. No other flow sets a
// reminder through the UI or clicks a chip.
export async function checkRemindersAndFilters({ browser, owner, size }) {
  const mobile = size === 'phone';
  const tag = `chip${size}`;
  const post = data => owner.api.post('/api/notes', { data }).then(r => r.json());
  const plain = await post({ title: `Plain ${size}`, content: `FILTERME ${size} plain` });
  const tagged = await post({ title: `Tagged ${size}`, content: `FILTERME ${size} tagged`, label: tag });
  const remind = await post({ title: `Remind ${size}`, content: `FILTERME ${size} remind` });
  const { context, page } = await openNotes(browser, owner, size);
  const due = async () => (await getNote(owner.api, remind.id)).note;
  try {
    await openFromCard(page, mobile, remind.id);
    const win = page.locator(WIN);
    const menu = page.locator('.note-reminder-picker');
    // Tomorrow, from the presets: the date the page itself calls tomorrow.
    await tapOrClick(mobile, win.locator('.note-window-remind'));
    await tapOrClick(mobile, menu.locator('[data-action="preset"][data-i="1"]'));
    const tomorrow = await page.evaluate(() => { const d = new Date(); d.setDate(d.getDate() + 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; });
    await until(due, n => n?.due_date?.startsWith(tomorrow), 'the reminder for tomorrow');
    const reminderTag = win.locator('.note-reminder-tag');
    await reminderTag.waitFor();
    // Weekly on a chosen day, from the tag.
    await tapOrClick(mobile, reminderTag);
    await tapOrClick(mobile, menu.locator('[data-action="sub"][data-sub="weekly"]'));
    await tapOrClick(mobile, menu.locator('[data-action="weekly-pick"][data-wd="3"]'));
    await until(due, n => n?.repeat === 'weekly:3', 'weekly on day 3');
    await menu.waitFor({ state: 'detached' });
    // A typed date and time; with a reminder set, the tag stands in for the bell.
    await tapOrClick(mobile, reminderTag);
    await tapOrClick(mobile, menu.locator('[data-action="preset"][data-i="3"]'));
    await menu.locator('.note-reminder-date-input').fill('2030-01-02T09:30');
    await tapOrClick(mobile, menu.locator('.note-reminder-menu-confirm'));
    await until(due, n => n?.due_date?.startsWith('2030-01-02T09:30'), 'the typed date');
    if (evidence) await page.screenshot({ path: path.join(evidence, `reminder-tag-${size}.png`), animations: 'disabled' });
    // Removed from the tag's x.
    await tapOrClick(mobile, reminderTag.locator('.note-reminder-tag-x'));
    await until(due, n => n && !n.due_date && (n.repeat || 'none') === 'none', 'the reminder removed');
    // Back on for the filters below.
    await tapOrClick(mobile, win.locator('.note-window-remind'));
    await tapOrClick(mobile, menu.locator('[data-action="preset"][data-i="1"]'));
    await until(due, n => !!n?.due_date, 'the reminder back');
    await closeWindow(page);

    // Search keeps the three notes; each chip keeps its own.
    const visible = () => page.locator('#notes-pane .note-card[data-note-id]').evaluateAll(cards => cards.filter(c => c.offsetParent !== null).map(c => c.dataset.noteId));
    const only = (ids, label) => until(visible, shown => shown.length === ids.length && ids.every(id => shown.includes(id)), label);
    await page.locator('#notes-search').fill(`FILTERME ${size}`);
    await only([plain.id, tagged.id, remind.id], 'the search');
    const toggle = page.locator('#notes-pane .notes-label-toggle');
    if (await toggle.getAttribute('aria-expanded') === 'false') await tapOrClick(mobile, toggle);
    const chip = selector => page.locator(`#notes-pane .notes-labels-bar ${selector}`);
    await tapOrClick(mobile, chip('[data-action="reminders"]'));
    await only([remind.id], 'Reminders: only the reminder');
    await tapOrClick(mobile, chip('[data-action="reminders"]'));
    await tapOrClick(mobile, chip('[data-action="reminders"]'));
    await only([plain.id, tagged.id], 'Reminders a second time: the reminder hidden');
    await tapOrClick(mobile, chip(`[data-label="${tag}"]`));
    await only([tagged.id], 'the tag chip');
    await tapOrClick(mobile, chip('[data-action="default"]'));
    await only([plain.id, remind.id], 'Default: notes without a tag');
    await tapOrClick(mobile, chip('[data-action="all"]'));
    await only([plain.id, tagged.id, remind.id], 'All');
    await page.locator('#notes-search').fill('');
  } finally {
    await context.close();
    for (const note of [plain, tagged, remind]) await owner.api.delete(`/api/notes/${note.id}`);
  }
}

// The panel's keys: Ctrl/Cmd+C over a card copies that note (the only way to
// Copy now that the card menu is hidden; desktop, where a card can be
// hovered), Ctrl/Cmd+Z undoes an archive done from the window, and Escape
// leaves select mode, then the archive view.
export async function checkPanelKeys({ browser, owner, size }) {
  const mobile = size === 'phone';
  const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
  const note = await (await owner.api.post('/api/notes', { data: { title: `Keys ${size}`, content: 'Line one\n\n- [ ] task one\n  - [x] nested done' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  try {
    const card = page.locator(`.note-card[data-note-id="${note.id}"]`);
    await card.waitFor();
    if (!mobile) {
      await page.evaluate(() => {
        window.__copied = [];
        navigator.clipboard.writeText = async text => { window.__copied.push(text); };
      });
      // The panel opens with the search box focused; keys typed in a field
      // are the field's.
      await page.evaluate(() => document.activeElement?.blur());
      await card.hover();
      await page.keyboard.press(`${mod}+c`);
      await until(() => page.evaluate(() => window.__copied), copied => copied.length === 1, 'the copied note');
      const [copied] = await page.evaluate(() => window.__copied);
      assert.equal(copied, `Keys ${size}\nLine one\n\n- [ ] task one\n  - [x] nested done`, 'title, then the markdown with its nesting');
      await page.locator('#toast').filter({ hasText: 'Copied' }).waitFor();
    }

    // Archive from the window, then undo it from the panel.
    await openFromCard(page, mobile, note.id);
    await tapOrClick(mobile, page.locator(`${WIN} [data-act="archive"]`));
    await page.locator(WIN).waitFor({ state: 'detached' });
    await until(() => getNote(owner.api, note.id), r => r.note?.archived === true, 'archived');
    await card.waitFor({ state: 'detached' });
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press(`${mod}+z`);
    await until(() => getNote(owner.api, note.id), r => r.note?.archived === false, 'the archive undone');
    await card.waitFor();

    // Escape: select mode first, then the archive view, back to the notes.
    await page.locator('#notes-select-btn').click();
    await page.locator('.note-card-selectmode').first().waitFor();
    await page.keyboard.press('Escape');
    await until(() => page.locator('.note-card-selectmode').count(), n => n === 0, 'select mode left');
    assert.equal(await page.locator('#notes-pane').count(), 1, 'Escape in select mode keeps the panel open');
    // Phones have no archive view (the toggle is hidden at 768 px and below, as on dev).
    if (!mobile) {
      await page.locator('#notes-archive-toggle').click();
      await until(() => card.count(), n => n === 0, 'the archive view');
      await page.keyboard.press('Escape');
      await card.waitFor();
      assert.equal(await page.locator('#notes-pane').count(), 1, 'Escape in the archive goes back to the notes');
    }
  } finally {
    await context.close();
    await owner.api.delete(`/api/notes/${note.id}`);
  }
}
