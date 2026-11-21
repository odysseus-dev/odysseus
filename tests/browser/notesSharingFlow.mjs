// Sharing between two accounts, driven through the UI: the owner shares from
// the note window's popover, the collaborator edits and then only reads, a
// stranger sees nothing, a rename carries the share and the collaborator
// leaves. Then the grid view with both owned and shared notes.
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  createAccount, getNote, imagesLoaded, openNotes, origin, pngOf, putBody, refreshNotes, tapOrClick, until, untilNote, uploadPng, VIEWPORTS,
} from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const UPLOAD = /\/api\/upload\/([^)\s"]+)/g;
const uploadIds = content => [...String(content || '').matchAll(UPLOAD)].map(m => m[1]);
const closeWindow = page => page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());

// What a footer offers, by data-act (colour dots and the tag input count too).
const footerControls = win => win.locator('.note-window-footer').evaluate(f => [...f.querySelectorAll('button, .note-color-dot, input')]
  .filter(el => el.offsetParent !== null).map(el => el.dataset.act || el.className));

async function openFromCard(page, mobile, id) {
  await tapOrClick(mobile, page.locator(`.note-card[data-note-id="${id}"] .note-card-title`));
  const body = page.locator('#note-window-modal .note-window-body');
  await body.waitFor();
  return body;
}

/** Card order puts every note shared with me after the "Shared with me" header. In the grid that must also hold on screen. */
async function sharedHeaderPlacement(page) {
  return page.evaluate(() => {
    const body = document.querySelector('#notes-pane .notes-pane-body');
    const header = body.querySelector('.notes-shared-header');
    if (!header) return null;
    const h = header.getBoundingClientRect();
    const cards = [...body.querySelectorAll('.note-card')].map(card => ({
      shared: card.classList.contains('note-card-shared-in'), rect: card.getBoundingClientRect(),
      afterHeader: !!(header.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING),
    }));
    return {
      sharedAfterInDom: cards.filter(c => c.shared).every(c => c.afterHeader),
      ownBeforeInDom: cards.filter(c => !c.shared).every(c => !c.afterHeader),
      // Compared with the header's top edge: phone grid cards pull themselves
      // up 8 px into the gap under the row before them.
      sharedAbove: cards.filter(c => c.shared && c.rect.top <= h.top).length,
      ownBelow: cards.filter(c => !c.shared && c.rect.top >= h.top).length,
      shared: cards.filter(c => c.shared).length,
      own: cards.filter(c => !c.shared).length,
    };
  });
}

/** The control for the view-only Space step: Space over another idle window still minimises it. */
async function checkSpaceStillMinimises(page) {
  await page.evaluate(() => document.getElementById('tool-calendar-btn').click());
  const calendar = page.locator('#calendar-modal .modal-content');
  await calendar.waitFor({ state: 'visible' });
  await page.evaluate(() => document.activeElement?.blur());
  await calendar.hover({ position: { x: 40, y: 120 } });
  await page.keyboard.press('Space');
  await page.locator('#calendar-modal.modal-minimized').waitFor({ state: 'attached' });
  await page.evaluate(() => document.querySelector('.minimized-dock-chip[data-modal-id="calendar-modal"] .minimized-dock-x').click());
  await page.locator('.minimized-dock-chip[data-modal-id="calendar-modal"]').waitFor({ state: 'detached' });
}

export async function checkSharing({ browser, owner, admin, size, stranger }) {
  const mobile = size === 'phone';
  const collabName = `collab-${size}`;
  const collab = await createAccount(admin, collabName);

  // The owner's note carries a task list and the owner's own image.
  const ownerSide = await openNotes(browser, owner, size);
  const ownerPng = await uploadPng(owner.api, await pngOf(ownerSide.page, '#3f6fb5'), 'owner.png');
  const created = await (await owner.api.post('/api/notes', { data: {
    title: `Trip plan ${size}`, content: `Shared packing list\n\n![](${ownerPng})\n\n- [ ] tent\n- [ ] stove`,
  } })).json();
  await refreshNotes(ownerSide.page);

  // Share from the popover: the switch grants View, the pill makes it Edit.
  const ownerPage = ownerSide.page;
  await openFromCard(ownerPage, mobile, created.id);
  const shareButton = ownerPage.locator('#note-window-modal .note-window-share');
  await shareButton.waitFor();
  // A failed account list says so and offers Try again.
  await ownerPage.route('**/api/notes/share-targets', route => route.abort());
  await tapOrClick(mobile, shareButton);
  const menu = ownerPage.locator('.note-share-menu');
  await menu.locator('.note-share-status').filter({ hasText: "Couldn't load users." }).waitFor();
  await ownerPage.unroute('**/api/notes/share-targets');
  await tapOrClick(mobile, menu.locator('[data-act="retry"]'));
  const row = menu.locator('.note-share-row').filter({ hasText: collabName });
  await row.waitFor();
  const shares = async () => (await (await owner.api.get(`/api/notes/${created.id}/shares`)).json()).shared_with;
  await row.locator(`input[data-user="${collabName}"]`).check();
  await row.locator('.note-share-pill[data-perm="view"].active').waitFor();
  // Edit right away, as a person would: a click made while View is still
  // saving is queued, not dropped (the bugs flow holds the request to pin it).
  await tapOrClick(mobile, row.locator('.note-share-pill[data-perm="edit"]'));
  await row.locator('.note-share-pill[data-perm="edit"].active').waitFor();
  assert.deepEqual(await until(shares, r => r[0]?.permission === 'edit', 'the edit grant'), [{ username: collabName, permission: 'edit' }]);
  assert.equal((await shareButton.locator('.note-window-share-count').textContent()).trim(), '1');
  if (evidence) await ownerPage.screenshot({ path: path.join(evidence, `share-popover-${size}.png`), animations: 'disabled' });
  await ownerPage.keyboard.press('Escape');
  await closeWindow(ownerPage);

  // The collaborator: under "Shared with me", owner's name on the card, the
  // owner's image through the note-scoped route.
  const collabSide = await openNotes(browser, collab, size);
  const collabPage = collabSide.page;

  // A non-admin gets no account list: they type the exact username. A name
  // with no account says so and shares nothing; any case finds the account.
  const byName = await (await collab.api.post('/api/notes', { data: { title: `By name ${size}`, content: 'typed' } })).json();
  await refreshNotes(collabPage);
  await collabPage.evaluate(id => void window.notesModule.openNote(id, { focus: null }), byName.id);
  await tapOrClick(mobile, collabPage.locator('#note-window-modal .note-window-share'));
  const typedMenu = collabPage.locator('.note-share-menu');
  const nameField = typedMenu.locator('input[data-act="name"]');
  await nameField.waitFor();
  assert.equal(await typedMenu.locator('.note-share-row').count(), 0, 'a non-admin sees no account list');
  assert.equal(await typedMenu.locator('[data-act="add"]').isDisabled(), true, 'Share waits for a name');
  if (evidence) await collabPage.screenshot({ path: path.join(evidence, `share-by-name-empty-${size}.png`), animations: 'disabled' });
  const byNameShares = async () => (await (await collab.api.get(`/api/notes/${byName.id}/shares`)).json()).shared_with;
  await nameField.fill(`${owner.username}-nobody`);
  await nameField.press('Enter');
  await typedMenu.locator('.note-share-status[role="alert"]').filter({ hasText: 'No account with that name.' }).waitFor();
  if (evidence) await collabPage.screenshot({ path: path.join(evidence, `share-by-name-unknown-${size}.png`), animations: 'disabled' });
  assert.deepEqual(await byNameShares(), []);
  await nameField.fill(owner.username.toUpperCase());
  assert.equal(await typedMenu.locator('.note-share-status[role="alert"]').count(), 0, 'typing clears the error');
  await tapOrClick(mobile, typedMenu.locator('[data-act="add"]'));
  const ownerName = owner.username.toLowerCase();
  await typedMenu.locator('.note-share-row').filter({ hasText: ownerName }).locator('.note-share-pill[data-perm="view"].active').waitFor();
  assert.equal(await nameField.inputValue(), '');
  assert.deepEqual(await until(byNameShares, r => r.length === 1, 'the typed grant'), [{ username: ownerName, permission: 'view' }]);
  if (evidence) await collabPage.screenshot({ path: path.join(evidence, `share-by-name-shared-${size}.png`), animations: 'disabled' });
  await collabPage.keyboard.press('Escape');
  await closeWindow(collabPage);
  await collab.api.delete(`/api/notes/${byName.id}`);

  // Without the share privilege their own note's popover says so. The admin
  // takes it away in Users, where "Share notes" sits with the other
  // privileges, on by default.
  await ownerPage.evaluate(() => window.adminModule.open('users'));
  const collabRow = ownerPage.locator('.admin-user-row').filter({ has: ownerPage.locator(`[data-adm-del-user="${collabName}"]`) });
  await collabRow.locator('.admin-user-info').click();
  const shareSwitch = collabRow.locator(`input[data-priv="can_share_notes"][data-user="${collabName}"]`);
  await shareSwitch.waitFor({ state: 'attached' });
  assert.equal(await shareSwitch.evaluate(input => input.closest('div').querySelector('span').textContent), 'Share notes');
  assert.equal(await shareSwitch.isChecked(), true, 'on by default');
  await shareSwitch.evaluate(input => input.closest('label').click());
  const privileges = async () => (await (await admin.api.get('/api/auth/users')).json()).users.find(u => u.username === collabName)?.privileges;
  await until(privileges, p => p?.can_share_notes === false, 'the switch saved');
  const theirOwn = await (await collab.api.post('/api/notes', { data: { title: `Own ${size}`, content: 'mine' } })).json();
  await refreshNotes(collabPage);
  await collabPage.evaluate(id => void window.notesModule.openNote(id, { focus: null }), theirOwn.id);
  await tapOrClick(mobile, collabPage.locator('#note-window-modal .note-window-share'));
  await collabPage.locator('.note-share-menu .note-share-status').filter({ hasText: 'Your account is not allowed to share notes.' }).waitFor();
  await collabPage.keyboard.press('Escape');
  await closeWindow(collabPage);
  await collab.api.delete(`/api/notes/${theirOwn.id}`);
  await refreshNotes(collabPage);
  const card = collabPage.locator(`.note-card[data-note-id="${created.id}"]`);
  await card.waitFor();
  assert.equal(await collabPage.locator('.notes-shared-header').count(), 1);
  assert.equal(await collabPage.locator('.notes-shared-header').evaluate((h, id) => !!(h.compareDocumentPosition(document.querySelector(`.note-card[data-note-id="${id}"]`)) & Node.DOCUMENT_POSITION_FOLLOWING), created.id), true, 'the shared card follows the header');
  assert.match(await card.locator('.note-card-share-badge.incoming').textContent(), /notes-reference/);
  for (const absent of ['.note-card-pin', '.note-card-done', '.note-card-archive', '.note-card-delete', '.note-card-colors']) assert.equal(await card.locator(absent).count(), 0, `${absent} on a shared card`);
  const thumbs = await imagesLoaded(card.locator('img'));
  assert.ok(thumbs.every(img => img.src.startsWith(`/api/notes/${created.id}/media/`) && img.width > 0), `owner image for the collaborator: ${JSON.stringify(thumbs)}`);

  // Tick a task on the card, then type in the window and paste an image.
  const tentRow = card.locator('li.md-task').filter({ hasText: 'tent' });
  if (mobile) await tentRow.locator('.note-check-text').tap();
  else await tentRow.locator('.note-check-dot').click();
  await untilNote(owner.api, created.id, n => n?.items?.[0]?.done === true, 'the collaborator tick, seen by the owner');
  const body = await openFromCard(collabPage, mobile, created.id);
  await collabPage.locator('#note-window-modal .note-window-toolbar').waitFor();
  assert.equal(await collabPage.locator('#note-window-modal .note-window-share:not(.hidden)').count(), 0, 'Share is the owner\'s');
  assert.deepEqual(await footerControls(collabPage.locator('#note-window-modal')), ['leave'], 'an edit collaborator gets Leave, not the owner\'s footer');
  await collabPage.evaluate(() => {
    const p = document.querySelector('#note-window-modal .note-window-body p');
    const range = document.createRange(); range.selectNodeContents(p); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range); p.closest('[contenteditable="true"]').focus();
  });
  await collabPage.keyboard.type(' plus a tarp');
  await untilNote(owner.api, created.id, n => n?.content.startsWith('Shared packing list plus a tarp'), 'the collaborator text');
  const pasted = (await pngOf(collabPage, '#3f9a6a')).toString('base64');
  // The upload route allows 3 requests per IP per 10 s, and the flows before
  // this one upload too: a paste answered 429 inserts nothing, so it is
  // pasted again once the window opens, like uploadAs does for the API.
  for (let attempt = 0; ; attempt++) {
    const answer = collabPage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/upload');
    await collabPage.evaluate(base64 => {
      const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
      const data = new DataTransfer();
      data.items.add(new File([bytes], 'collab.png', { type: 'image/png' }));
      document.querySelector('#note-window-modal .note-window-body li.md-task:last-child .md-task-text')
        .dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    }, pasted);
    if ((await answer).status() !== 429 || attempt >= 2) break;
    await collabPage.waitForTimeout(10500);
  }
  const withBoth = await untilNote(owner.api, created.id, n => uploadIds(n?.content).length === 2, 'the collaborator image');
  // The paste lands after the caret's block, the paragraph just typed in.
  const ownerUpload = ownerPng.split('/').pop();
  const collabUpload = uploadIds(withBoth.content).find(id => id !== ownerUpload);
  assert.match(withBoth.content, new RegExp(`^Shared packing list plus a tarp\\n\\n!\\[\\]\\(/api/upload/${collabUpload}\\)\\n\\n!\\[\\]\\(/api/upload/${ownerUpload}\\)\\n\\n- \\[x\\] tent\\n- \\[ \\] stove$`), 'the collaborator wrote back the owner\'s image path unchanged');
  assert.equal((await collab.api.get(`/api/upload/${ownerUpload}`)).status() === 200, false, 'the owner upload stays owner-only outside the note');
  const collabImages = await imagesLoaded(body.locator('img.md-note-image'));
  assert.equal(collabImages.length, 2);
  assert.ok(collabImages.every(img => img.width > 0), JSON.stringify(collabImages));
  await closeWindow(collabPage);

  // The owner sees all of it after a refresh, including the collaborator's image.
  await refreshNotes(ownerPage);
  const ownerCard = ownerPage.locator(`.note-card[data-note-id="${created.id}"]`);
  await ownerCard.waitFor();
  assert.match(await ownerCard.textContent(), /plus a tarp/);
  assert.equal(await ownerCard.locator('li.md-task.done').count(), 1);
  assert.match(await ownerCard.locator('.note-card-share-badge').textContent(), /Shared · 1/);
  const ownerBody = await openFromCard(ownerPage, mobile, created.id);
  const ownerImages = await imagesLoaded(ownerBody.locator('img.md-note-image'));
  assert.equal(ownerImages.length, 2);
  assert.ok(ownerImages.every(img => img.width > 0), `both images for the owner: ${JSON.stringify(ownerImages)}`);
  assert.ok(ownerImages.some(img => img.src.includes(collabUpload)));

  // View only: from the same popover.
  await tapOrClick(mobile, shareButton);
  await tapOrClick(mobile, menu.locator('.note-share-row').filter({ hasText: collabName }).locator('.note-share-pill[data-perm="view"]'));
  await until(shares, r => r[0]?.permission === 'view', 'the view grant');
  await ownerPage.keyboard.press('Escape');
  await closeWindow(ownerPage);

  await refreshNotes(collabPage);
  await card.waitFor();
  assert.equal(await card.locator('.note-cl-quickadd').count(), 0, 'no quick-add on a view-only card');
  const writes = [];
  collabPage.on('request', r => { if (r.method() !== 'GET' && /^\/api\/(notes|upload)/.test(new URL(r.url()).pathname)) writes.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  // Tapping a task on a view-only card opens the note instead of ticking it.
  const stoveRow = card.locator('li.md-task').filter({ hasText: 'stove' });
  await tapOrClick(mobile, stoveRow.locator('.note-check-text'));
  await collabPage.locator('#note-window-modal .note-window-body').waitFor();
  const win = collabPage.locator('#note-window-modal');
  await win.locator('.note-window-banner').filter({ hasText: 'View only, shared by notes-reference' }).waitFor();
  assert.equal(await win.locator('.note-window-toolbar').isVisible(), false, 'no toolbar');
  assert.deepEqual(await footerControls(win), ['leave'], 'the footer offers only Leave');
  assert.equal(await win.locator('.note-window-body').getAttribute('contenteditable'), 'false');
  // Keys typed over a read-only body, Space included: the body never has
  // focus, and Space over an idle window minimises it everywhere else.
  await win.locator('.note-window-body').click();
  await collabPage.keyboard.type('readonly');
  await win.locator('.note-window-body').hover();
  await collabPage.keyboard.press('Space');
  await collabPage.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  assert.equal(await win.evaluate(m => m.classList.contains('modal-minimized')), false, 'Space over a view-only note keeps it open');
  await tapOrClick(mobile, win.locator('.md-task-box').last());
  // Paste and drop do nothing: no upload, no text.
  const viewOnlyText = await win.locator('.note-window-body').textContent();
  await collabPage.evaluate(base64 => {
    const target = document.querySelector('#note-window-modal .note-window-body p');
    const file = () => new File([Uint8Array.from(atob(base64), c => c.charCodeAt(0))], 'nope.png', { type: 'image/png' });
    const pasted = new DataTransfer();
    pasted.items.add(file());
    pasted.setData('text/plain', 'pasted into view only');
    target.dispatchEvent(new ClipboardEvent('paste', { clipboardData: pasted, bubbles: true, cancelable: true }));
    const dropped = new DataTransfer();
    dropped.items.add(file());
    const box = target.getBoundingClientRect();
    target.dispatchEvent(new DragEvent('drop', { clientX: box.left + 4, clientY: box.top + 4, dataTransfer: dropped, bubbles: true, cancelable: true }));
  }, (await pngOf(collabPage, '#9a3f6a')).toString('base64'));
  await collabPage.evaluate(() => fetch('/api/health').then(r => r.status));
  assert.equal(await win.locator('.note-window-body').textContent(), viewOnlyText, 'paste and drop change nothing on screen');
  if (evidence) await collabPage.screenshot({ path: path.join(evidence, `view-only-${size}.png`), animations: 'disabled' });
  await closeWindow(collabPage);
  await win.waitFor({ state: 'detached' });
  assert.deepEqual(writes, [], 'a view-only window writes nothing');
  await checkSpaceStillMinimises(collabPage);
  const current = (await getNote(owner.api, created.id)).note;
  assert.equal((await collab.api.put(`/api/notes/${created.id}`, { data: { content: 'overwritten', base_updated_at: current.updated_at } })).status(), 403);
  assert.equal((await collab.api.post(`/api/notes/${created.id}/items/0/toggle`)).status(), 403);
  assert.equal((await getNote(owner.api, created.id)).note.content, current.content);

  // A stranger gets the same 404 as a missing note, for the note and its media.
  assert.equal((await getNote(stranger.api, created.id)).status, 404);
  for (const id of [ownerUpload, collabUpload]) assert.equal((await stranger.api.get(`/api/notes/${created.id}/media/${id}`)).status(), 404);
  assert.equal((await (await stranger.api.get('/api/notes')).json()).notes.some(n => n.id === created.id), false);

  // Rename: the grant follows, and the open tab keeps working under the new name.
  const renamed = `${collabName}-renamed`;
  const rename = await admin.api.put(`/api/auth/users/${collabName}/rename`, { data: { username: renamed } });
  assert.equal(rename.ok(), true, await rename.text());
  assert.deepEqual(await shares(), [{ username: renamed, permission: 'view' }]);
  assert.equal((await getNote(collab.api, created.id)).status, 200, 'the session follows the rename');

  // Leave from the window (confirmed); then the note and its media are gone
  // for them. The card's own menu sits in an action row the cards hide.
  await refreshNotes(collabPage);
  assert.equal(await card.locator('.note-card-corner-menu').isVisible(), false);
  await openFromCard(collabPage, mobile, created.id);
  await tapOrClick(mobile, win.locator('[data-act="leave"]'));
  await collabPage.locator('#styled-confirm-msg').filter({ hasText: 'Leave this note?' }).waitFor();
  await tapOrClick(mobile, collabPage.locator('#styled-confirm-ok'));
  await win.waitFor({ state: 'detached' });
  await card.waitFor({ state: 'detached' });
  await until(shares, r => r.length === 0, 'the leave');
  assert.equal((await getNote(collab.api, created.id)).status, 404);
  assert.equal((await collab.api.get(`/api/notes/${created.id}/media/${ownerUpload}`)).status(), 404);
  await refreshNotes(collabPage);
  assert.equal(await collabPage.locator(`.note-card[data-note-id="${created.id}"]`).count(), 0);
  await collabSide.context.close();
  await ownerSide.context.close();
  return { collab: renamed };
}

/** The grid with owned and shared notes: every shared card sits below the header, every owned card above it. */
export async function checkSharedGrid({ browser, owner, admin, size }) {
  const friend = await createAccount(admin, `friend-${size}`);
  // Owned notes of very different heights, so the columns end unevenly and
  // leave holes a dense grid would fill.
  const tall = Array.from({ length: 9 }, (_, i) => `- [ ] step ${i + 1}`).join('\n');
  const owned = [];
  for (const [i, content] of ['short', tall, 'one line', `${tall}\n${tall}`, 'two\n\nlines'].entries()) {
    owned.push((await (await owner.api.post('/api/notes', { data: { title: `Grid own ${i}`, content } })).json()).id);
  }
  const theirs = [];
  for (const i of [0, 1, 2]) {
    const note = await (await friend.api.post('/api/notes', { data: { title: `From friend ${i}`, content: i === 1 ? 'tiny' : 'shared\n\nwith a second line' } })).json();
    const grant = await friend.api.post(`/api/notes/${note.id}/share`, { data: { users: [owner.username], permission: i === 2 ? 'view' : 'edit' } });
    assert.equal(grant.ok(), true, await grant.text());
    theirs.push(note.id);
  }
  const { context, page } = await openNotes(browser, owner, size, { view: 'grid' });
  await page.locator('.notes-pane.notes-view-grid .notes-shared-header').waitFor();
  await page.locator('.note-card.note-card-shared-in').first().waitFor();
  // The spans are measured after render; wait until every card has one.
  await page.waitForFunction(() => [...document.querySelectorAll('#notes-pane .note-card')].every(c => /span \d+/.test(c.style.gridRowEnd)));
  const placement = await sharedHeaderPlacement(page);
  if (evidence) await page.screenshot({ path: path.join(evidence, `grid-shared-${size}.png`), fullPage: true, animations: 'disabled' });
  assert.equal(placement.sharedAfterInDom && placement.ownBeforeInDom, true, JSON.stringify(placement));
  // Only this check's notes are counted: a shared note another flow left
  // behind when it failed must not fail this one too.
  for (const id of theirs) assert.equal(await page.locator(`.note-card.note-card-shared-in[data-note-id="${id}"]`).count(), 1, `shared card ${id}`);
  assert.deepEqual({ sharedAbove: placement.sharedAbove, ownBelow: placement.ownBelow }, { sharedAbove: 0, ownBelow: 0 }, `grid placement: ${JSON.stringify(placement)}`);
  await context.close();
  for (const id of owned) await owner.api.delete(`/api/notes/${id}`);
  for (const id of theirs) await friend.api.delete(`/api/notes/${id}`);
}

/** Run while the owner is the only account: the popover has nobody to offer. */
export async function checkLonelyPopover({ browser, owner, size }) {
  const note = await (await owner.api.post('/api/notes', { data: { title: `Alone ${size}`, content: 'nobody else here' } })).json();
  const { context, page } = await openNotes(browser, owner, size);
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), note.id);
  await tapOrClick(size === 'phone', page.locator('#note-window-modal .note-window-share'));
  await page.locator('.note-share-menu .note-share-status').filter({ hasText: 'No other users on this instance.' }).waitFor();
  await context.close();
  await owner.api.delete(`/api/notes/${note.id}`);
}

/** The 20 s re-read, on Playwright's clock: a shared note's change shows up, an idle open note takes it with focus kept, and nothing is re-read while the share popover is open or in select mode. */
export async function checkSharedRefresh({ browser, owner, admin, size }) {
  const poller = await createAccount(admin, `poller-${size}`);
  const theirs = await (await poller.api.post('/api/notes', { data: { title: `Polled ${size}`, content: 'before' } })).json();
  assert.equal((await poller.api.post(`/api/notes/${theirs.id}/share`, { data: { users: [owner.username], permission: 'edit' } })).ok(), true);
  const { context, page } = await openNotes(browser, owner, size, { clock: true });
  const card = page.locator(`.note-card[data-note-id="${theirs.id}"]`);
  await card.waitFor();
  const reads = [];
  page.on('request', r => { if (r.method() === 'GET' && new URL(r.url()).pathname === '/api/notes') reads.push(r.url()); });
  // A request of our own, answered, means every earlier request was reported.
  const settle = () => page.evaluate(() => fetch('/api/health').then(r => r.status));

  assert.equal((await putBody(poller.api, theirs.id, { content: 'after one poll' })).ok(), true);
  await page.clock.fastForward(20000);
  await card.filter({ hasText: 'after one poll' }).waitFor();

  // An open note with nothing unsaved takes a collaborator's change, and
  // keeps focus (c331dc6a). Unsaved typing still holds it back: that half is
  // in the regression spec.
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), theirs.id);
  const windowBody = page.locator('#note-window-modal .note-window-body');
  await windowBody.focus();
  assert.equal((await putBody(poller.api, theirs.id, { content: 'changed while it was open' })).ok(), true);
  await page.clock.fastForward(20000);
  await windowBody.filter({ hasText: 'changed while it was open' }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('note-window-body')), true, 'focus stays in the note');
  await closeWindow(page);
  await card.filter({ hasText: 'changed while it was open' }).waitFor();

  // Paused while the share popover is open, and in select mode.
  const mine = await (await owner.api.post('/api/notes', { data: { title: `Mine ${size}`, content: 'for the popover' } })).json();
  await refreshNotes(page);
  await card.waitFor();
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), mine.id);
  await tapOrClick(size === 'phone', page.locator('#note-window-modal .note-window-share'));
  await page.locator('.note-share-menu .note-share-row').first().waitFor();
  await page.evaluate(() => document.activeElement?.blur());
  await settle();
  reads.length = 0;
  await page.clock.fastForward(20000);
  await settle();
  assert.deepEqual(reads, [], 'no re-read while the share popover is open');
  await page.keyboard.press('Escape');
  await page.locator('.note-share-menu').waitFor({ state: 'detached' });
  await closeWindow(page);
  await tapOrClick(size === 'phone', page.locator('#notes-select-btn'));
  await page.locator('.note-card-selectmode').first().waitFor();
  await settle();
  reads.length = 0;
  await page.clock.fastForward(20000);
  await settle();
  assert.deepEqual(reads, [], 'no re-read in select mode');
  await page.keyboard.press('Escape');
  await page.locator('.note-card-selectmode').first().waitFor({ state: 'detached' });

  // A failed read keeps the list; the next good one brings the change.
  const listRead = url => url.pathname === '/api/notes';
  await page.route(listRead, route => route.fulfill({ status: 500, body: 'down' }));
  assert.equal((await putBody(poller.api, theirs.id, { content: 'changed during the outage' })).ok(), true);
  reads.length = 0;
  await page.clock.fastForward(20000);
  await until(async () => reads.length, n => n > 0, 'the failed re-read');
  await settle();
  assert.equal(await card.filter({ hasText: 'changed while it was open' }).count(), 1, 'the list survives a failed read');
  await page.unroute(listRead);
  await page.clock.fastForward(20000);
  await card.filter({ hasText: 'changed during the outage' }).waitFor();
  await context.close();
  await poller.api.delete(`/api/notes/${theirs.id}`);
  await owner.api.delete(`/api/notes/${mine.id}`);
}

/** A collaborator renamed while their page is open: the next 20 s re-read picks up the new name, so Leave still works without a reload. */
export async function checkRenamedPoll({ browser, owner, admin, size }) {
  const before = `renamee-${size}`;
  const renamee = await createAccount(admin, before);
  const note = await (await owner.api.post('/api/notes', { data: { title: `Renamed reader ${size}`, content: 'stay or go' } })).json();
  assert.equal((await owner.api.post(`/api/notes/${note.id}/share`, { data: { users: [before], permission: 'edit' } })).ok(), true);
  const { context, page } = await openNotes(browser, renamee, size, { clock: true });
  const card = page.locator(`.note-card[data-note-id="${note.id}"]`);
  await card.waitFor();
  const after = `${before}-new`;
  assert.equal((await admin.api.put(`/api/auth/users/${before}/rename`, { data: { username: after } })).ok(), true);
  const polled = page.waitForResponse(r => new URL(r.url()).pathname === '/api/notes' && r.request().method() === 'GET');
  await page.clock.fastForward(20000);
  assert.equal((await (await polled).json()).me, after, 'the re-read carries the new name');
  await tapOrClick(size === 'phone', card.locator('.note-card-title'));
  const win = page.locator('#note-window-modal');
  await tapOrClick(size === 'phone', win.locator('[data-act="leave"]'));
  await tapOrClick(size === 'phone', page.locator('#styled-confirm-ok'));
  await win.waitFor({ state: 'detached' });
  await until(async () => (await (await owner.api.get(`/api/notes/${note.id}/shares`)).json()).shared_with, r => r.length === 0, 'the leave under the new name');
  await context.close();
  await owner.api.delete(`/api/notes/${note.id}`);
}

/** A note shared with me carrying its owner's past reminder neither glows nor pulses for me; my own past reminder does (the control). */
export async function checkSharedReminder({ browser, owner, admin, size }) {
  const sharer = await createAccount(admin, `reminder-friend-${size}`);
  // Local wall-clock time, the form the reminder picker writes.
  const pad = n => String(n).padStart(2, '0');
  const past = new Date(Date.now() - 3600000);
  const due = `${past.getFullYear()}-${pad(past.getMonth() + 1)}-${pad(past.getDate())}T${pad(past.getHours())}:${pad(past.getMinutes())}`;
  const mine = await (await owner.api.post('/api/notes', { data: { title: `My reminder ${size}`, content: 'call the plumber', due_date: due } })).json();
  const theirs = await (await sharer.api.post('/api/notes', { data: { title: `Their reminder ${size}`, content: 'water the plants', due_date: due } })).json();
  assert.equal((await sharer.api.post(`/api/notes/${theirs.id}/share`, { data: { users: [owner.username], permission: 'edit' } })).ok(), true);
  // Before Notes is opened (which dismisses it), the rail badge counts my
  // past reminder only.
  const bare = await browser.newContext({ ...VIEWPORTS[size], storageState: owner.storage });
  const start = await bare.newPage();
  await start.goto(origin);
  const badge = start.locator('#rail-notes .rail-notes-badge');
  await badge.waitFor({ state: 'attached' });
  assert.equal(await badge.textContent(), '1', 'the rail badge counts my reminder, not the shared one');
  await bare.close();
  const { context, page } = await openNotes(browser, owner, size);
  const shared = page.locator(`.note-card[data-note-id="${theirs.id}"]`);
  await shared.waitFor();
  assert.equal((await getNote(owner.api, theirs.id)).note.due_date?.startsWith(due), true, 'the collaborator copy carries the owner\'s reminder');
  await page.locator(`.note-card[data-note-id="${mine.id}"].note-card-reminder-fired-sticky.note-card-reminder-due`).waitFor();
  const classes = await shared.evaluate(card => [...card.classList].filter(c => c.startsWith('note-card-reminder')));
  assert.deepEqual(classes, [], 'no reminder glow or pulse on a note shared with me');
  await refreshNotes(page);
  await page.locator(`.note-card[data-note-id="${mine.id}"].note-card-reminder-due`).waitFor();
  assert.deepEqual(await shared.evaluate(card => [...card.classList].filter(c => c.startsWith('note-card-reminder'))), [], 'still none after a refresh');
  await context.close();
  await owner.api.delete(`/api/notes/${mine.id}`);
  await sharer.api.delete(`/api/notes/${theirs.id}`);
}
