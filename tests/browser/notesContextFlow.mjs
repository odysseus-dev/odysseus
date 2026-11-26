// Notes in a chat's context: by default no note is on the chat screen, even
// a pinned one. Notes sent from Notes -> Select -> Send to agent show as cards
// above the composer, each with an x; a click opens the note window over the
// chat. A sent message names the notes it carried, after a reload too. In a
// chat started from a note, that note is a card without an x and every turn
// carries it; that note sits at the top of the conversation.
import assert from 'node:assert/strict';
import path from 'node:path';
import { modelRequests, origin, sqlite, tapOrClick, until, VIEWPORTS } from './notesHarness.mjs';

const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
const STRIP = '#notes-context-strip';
const card = (page, id) => page.locator(`${STRIP} [data-context-note-id="${id}"]`);
const stripIds = page => page.locator(`${STRIP} [data-context-note-id]`).evaluateAll(cards => cards.map(c => c.dataset.contextNoteId));
const textOf = content => (Array.isArray(content) ? content.map(part => part.text || '').join(' ') : String(content || ''));
const requestWith = marker => until(
  () => modelRequests().filter(body => (body.messages || []).some(m => m.role === 'user' && textOf(m.content).includes(marker))),
  list => list.length > 0, `the model request for ${marker}`, 30000,
).then(list => list.at(-1));
const contextText = body => (body.messages || []).map(m => textOf(m.content)).join('\n');

async function openChat(browser, account, size) {
  const context = await browser.newContext({ ...VIEWPORTS[size], storageState: account.storage });
  await context.addInitScript(() => {
    localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
    localStorage.setItem('odysseus-toggles', JSON.stringify({ mode: 'agent' }));
  });
  const page = await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => window.notesModule?.openPanel && window.sessionModule?.createDirectChat);
  return { context, page };
}

async function newDirectChat(page, owner) {
  const chat = await (await owner.api.get('/api/default-chat')).json();
  await page.evaluate(({ url, model, id }) => window.sessionModule.createDirectChat(url, model, id), { url: chat.endpoint_url, model: chat.model, id: chat.endpoint_id });
}

/** Send `text` and wait for its own reply, and for the chat to be idle again. */
async function send(page, mobile, text) {
  const replies = page.locator('#chat-history .msg').filter({ hasText: 'Stub reply:' });
  const before = await replies.count();
  // A phone keeps the composer readonly after a send until it is tapped.
  if (mobile) await page.locator('#message:visible').tap();
  await page.locator('#message:visible').fill(text);
  await tapOrClick(mobile, page.locator('.send-btn:visible'));
  await until(() => replies.count(), n => n > before, `the reply to ${text}`, 30000);
  await page.waitForFunction(() => document.getElementById('chat-container')?.getAttribute('aria-busy') !== 'true');
}

const userBubble = (page, text) => page.locator('#chat-history .msg').filter({ hasText: text }).first();
const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator('#note-window-modal').waitFor({ state: 'detached' });
};

export async function checkContextNotes({ browser, owner, size }) {
  const mobile = size === 'phone';
  const post = data => owner.api.post('/api/notes', { data }).then(r => r.json());
  const trip = await post({ title: `Trip ${size}`, content: `CTXTRIP-${size}\n\nPack light\nBook the cabin` });
  const list = await post({ title: `Groceries ${size}`, content: '- [x] bread\n- [ ] milk\n- [ ] eggs', note_type: 'checklist' });
  const pinned = await post({ title: `Pinned ${size}`, content: 'never shown by default' });
  assert.equal((await owner.api.put(`/api/notes/${pinned.id}`, { data: { pinned: true } })).ok(), true);
  const { context, page } = await openChat(browser, owner, size);
  const created = [trip, list, pinned];
  try {
    // 1. By default no note is on screen: not on a new chat, not a pinned one.
    await newDirectChat(page, owner);
    await page.waitForTimeout(500);
    assert.equal(await page.locator(STRIP).isHidden(), true);
    assert.equal(await page.locator(`${STRIP} [data-context-note-id]`).count(), 0);
    assert.equal(await page.locator('#selected-notes-context-btn').isHidden(), true);

    // 2. Notes -> Select -> Send to agent: each note is a card with an x, in place of the old chip.
    await page.evaluate(() => window.notesModule.openPanel());
    await page.locator('#notes-pane .notes-pane-body').waitFor();
    await page.locator('#notes-select-btn').click();
    for (const note of [trip, list]) await page.locator(`#notes-pane .note-card[data-note-id="${note.id}"] .note-card-cb`).check({ force: mobile });
    await page.locator('#notes-send-agent').click();
    await page.evaluate(() => window.notesModule.closePanel());
    await card(page, list.id).waitFor();
    assert.deepEqual((await stripIds(page)).sort(), [trip.id, list.id].sort());
    assert.equal(await page.locator(`${STRIP} [data-context-remove]`).count(), 2);
    assert.match(await card(page, trip.id).innerText(), /Pack light/);
    assert.match(await card(page, list.id).innerText(), /milk[\s\S]*eggs/);
    assert.doesNotMatch(await card(page, list.id).innerText(), /bread/);
    assert.equal(await page.locator('#selected-notes-context-btn').isHidden(), true);
    const stripBox = await page.locator(STRIP).boundingBox();
    const composerBox = await page.locator('.chat-input-bar').boundingBox();
    assert.ok(stripBox.y + stripBox.height <= composerBox.y + 1, 'the cards sit above the composer');
    if (mobile) assert.equal(await page.locator(STRIP).evaluate(el => getComputedStyle(el).flexWrap), 'nowrap');
    if (evidence) await page.screenshot({ path: path.join(evidence, `context-cards-${size}.png`), animations: 'disabled' });

    // 3. A click opens the note window over the chat.
    await tapOrClick(mobile, card(page, trip.id).locator('.note-card-title'));
    const win = page.locator('#note-window-modal');
    await win.locator('.note-window-body').waitFor();
    assert.equal(await win.locator('.note-window-title').inputValue(), trip.title);
    await closeWindow(page);

    // 4. The sent message names the notes it carried; the server stored them.
    const first = `CTXFIRST ${size}`;
    await send(page, mobile, first);
    const bubble = userBubble(page, first);
    await bubble.locator('.context-note-card').nth(1).waitFor();
    assert.deepEqual(await bubble.locator('.context-note-card .attach-card-name').allInnerTexts(), [trip.title, list.title]);
    assert.ok(contextText(await requestWith(first)).includes(`CTXTRIP-${size}`), 'the turn carried the note');
    const sid = await page.evaluate(() => window.sessionModule.getCurrentSessionId());
    const stored = sqlite("SELECT metadata FROM chat_messages WHERE session_id = ? AND role = 'user'", [sid]).map(([m]) => JSON.parse(m || '{}'));
    assert.deepEqual(stored.at(-1).context_notes, [{ id: trip.id, title: trip.title }, { id: list.id, title: list.title }]);
    if (evidence) await page.screenshot({ path: path.join(evidence, `context-sent-${size}.png`), animations: 'disabled' });

    // 5. The x takes a note out: the next message carries the other one only.
    await tapOrClick(mobile, card(page, list.id).locator('[data-context-remove]'));
    await until(() => stripIds(page), ids => ids.length === 1 && ids[0] === trip.id, 'the removed card gone');
    const second = `CTXSECOND ${size}`;
    await send(page, mobile, second);
    await userBubble(page, second).locator('.context-note-card').first().waitFor();
    assert.deepEqual(await userBubble(page, second).locator('.context-note-card .attach-card-name').allInnerTexts(), [trip.title]);
    await tapOrClick(mobile, card(page, trip.id).locator('[data-context-remove]'));
    await page.locator(STRIP).waitFor({ state: 'hidden' });
    const third = `CTXTHIRD ${size}`;
    await send(page, mobile, third);
    assert.equal(await userBubble(page, third).locator('.context-note-card').count(), 0);
    assert.ok(!contextText(await requestWith(third)).includes(`CTXTRIP-${size}`), 'nothing selected, nothing carried');

    // 6. After a reload the messages still name their notes, and a chip opens its note.
    await page.reload();
    await page.waitForFunction(() => window.sessionModule?.selectSession && window.notesModule?.openNoteWindow);
    await page.evaluate(async id => { await window.sessionModule.loadSessions(); await window.sessionModule.selectSession(id); }, sid);
    const reloaded = userBubble(page, first);
    await reloaded.locator('.context-note-card').nth(1).waitFor();
    assert.equal(await page.locator(STRIP).isHidden(), true, 'a selection does not outlive the page');
    await tapOrClick(mobile, reloaded.locator('.context-note-card').first());
    await win.locator('.note-window-body').waitFor();
    assert.equal(await win.locator('.note-window-title').inputValue(), trip.title);
    assert.equal(await page.locator('#notes-pane').count(), 0, 'the Notes panel stays shut');
    await closeWindow(page);

    // 7. A chat started from a note: that note is a card without an x, and a follow-up carries it.
    const chat = await (await owner.api.get('/api/default-chat')).json();
    const made = await owner.api.post('/api/session', { multipart: {
      name: `Agent: ${trip.title}`, endpoint_url: chat.endpoint_url, model: chat.model, endpoint_id: chat.endpoint_id || '', skip_validation: 'true', note_id: trip.id,
    } });
    assert.equal(made.ok(), true, await made.text());
    const noteChat = (await made.json()).id;
    await page.evaluate(async id => { await window.sessionModule.loadSessions(); await window.sessionModule.selectSession(id); }, noteChat);
    // At the top of the conversation, before its first message; nothing above the composer.
    const chatNote = page.locator(`#chat-history > .notes-chat-note [data-context-note-id="${trip.id}"]`);
    await chatNote.waitFor();
    assert.equal(await page.locator('#chat-history > :first-child').evaluate(el => el.classList.contains('notes-chat-note')), true);
    assert.equal(await page.locator(STRIP).isHidden(), true);
    assert.equal(await page.locator('#chat-history .notes-chat-note [data-context-remove]').count(), 0);
    const follow = `CTXFOLLOW ${size}`;
    await send(page, mobile, follow);
    await userBubble(page, follow).locator('.context-note-card').first().waitFor();
    assert.deepEqual(await userBubble(page, follow).locator('.context-note-card .attach-card-name').allInnerTexts(), [trip.title]);
    assert.ok(contextText(await requestWith(follow)).includes(`CTXTRIP-${size}`), 'a follow-up in a note chat carries its note');
    assert.equal(await page.locator('#chat-history > :first-child').evaluate(el => el.classList.contains('notes-chat-note')), true, 'still first after the reply');
    if (evidence) await page.screenshot({ path: path.join(evidence, `context-note-chat-${size}.png`), animations: 'disabled' });
    // Reopening the chat (its history reloads) keeps the note on top; a click opens it.
    await page.evaluate(async id => { await window.sessionModule.selectSession(id); }, sid);
    await page.locator('#chat-history .notes-chat-note').waitFor({ state: 'detached' });
    await page.evaluate(async id => { await window.sessionModule.selectSession(id); }, noteChat);
    await chatNote.waitFor();
    assert.equal(await page.locator('#chat-history > :first-child').evaluate(el => el.classList.contains('notes-chat-note')), true);
    await tapOrClick(mobile, chatNote.locator('.note-card-title'));
    await win.locator('.note-window-body').waitFor();
    assert.equal(await win.locator('.note-window-title').inputValue(), trip.title);
    await closeWindow(page);

    // 8. Back to a new chat: nothing on screen again.
    await newDirectChat(page, owner);
    await page.locator(STRIP).waitFor({ state: 'hidden' });
  } finally {
    await context.close();
    for (const note of created) await owner.api.delete(`/api/notes/${note.id}`);
  }
}
