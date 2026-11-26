// The owner's note window on paths no other flow takes: an image drop, the
// upload failure messages, untouched blocks written back byte for byte and
// edited ones with only the edit changed, a card tick behind the open window, opening a second note while the
// first has unsaved typing, and a note link in chat.
import assert from 'node:assert/strict';
import { getNote, openNotes, pngOf, tapOrClick, until, untilNote } from './notesHarness.mjs';

const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator('#note-window-modal').waitFor({ state: 'detached' });
};
const openById = async (page, id) => {
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), id);
  await page.locator('#note-window-modal .note-window-body').waitFor();
};
/** Put the caret at the end of the element `selector` names, inside the window body. */
const caretAtEnd = (page, selector) => page.evaluate(selector => {
  const target = document.querySelector(selector);
  const range = document.createRange(); range.selectNodeContents(target); range.collapse(false);
  getSelection().removeAllRanges(); getSelection().addRange(range);
  target.closest('[contenteditable="true"]').focus();
}, selector);
/** Put the caret right after `text`, in the window body. */
const caretAfter = (page, text) => page.evaluate(text => {
  const host = document.querySelector('#note-window-modal .note-window-body');
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const at = node.nodeValue.indexOf(text);
    if (at < 0) continue;
    host.focus();
    const range = document.createRange();
    range.setStart(node, at + text.length);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    return;
  }
  throw new Error(`no text ${text}`);
}, text);
const UPLOADS = content => [...String(content || '').matchAll(/!\[\]\(\/api\/upload\/([^)\s]+)\)/g)].map(m => m[1]);

export async function checkWindowGaps({ browser, owner, size }) {
  const mobile = size === 'phone';
  const { context, page } = await openNotes(browser, owner, size);
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const api = owner.api;

  // Dropping two image files on the body: one upload request, both inserted.
  const note = await (await api.post('/api/notes', { data: { title: `Gaps ${size}`, content: 'Drop target' } })).json();
  await openById(page, note.id);
  const uploads = [];
  page.on('request', r => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/upload') uploads.push(r.url()); });
  const [red, green] = [(await pngOf(page, '#b53f3f')).toString('base64'), (await pngOf(page, '#3f9a6a')).toString('base64')];
  const dropFiles = (files) => page.evaluate(files => {
    const data = new DataTransfer();
    for (const [name, base64] of files) data.items.add(new File([Uint8Array.from(atob(base64), c => c.charCodeAt(0))], name, { type: 'image/png' }));
    const target = document.querySelector('#note-window-modal .note-window-body p');
    const box = target.getBoundingClientRect();
    const at = { clientX: box.right - 2, clientY: box.top + box.height / 2, dataTransfer: data, bubbles: true, cancelable: true };
    target.dispatchEvent(new DragEvent('dragover', at));
    target.dispatchEvent(new DragEvent('drop', at));
  }, files);
  await dropFiles([['red.png', red], ['green.png', green]]);
  const dropped = await untilNote(api, note.id, n => UPLOADS(n?.content).length === 2, 'the dropped images');
  assert.equal(uploads.length, 1, 'a multi-file drop is one upload request');
  assert.match(dropped.content, /^Drop target\n\n!\[\]\(\/api\/upload\/[^)]+\)\n\n!\[\]\(\/api\/upload\/[^)]+\)$/);

  // Upload failures, answered by the page's own route so the server's
  // rate limit is not in play: a 429 inserts nothing, a short answer inserts
  // what came back. Each says so.
  const known = UPLOADS(dropped.content)[0];
  const toast = page.locator('#toast');
  await page.route('**/api/upload', route => route.fulfill({ status: 429, contentType: 'application/json', body: '{"detail":"Too many uploads"}' }));
  await dropFiles([['late.png', red]]);
  await toast.filter({ hasText: 'Image upload failed: too many uploads in a few seconds. Wait a moment and try again.' }).waitFor();
  await page.unroute('**/api/upload');
  await page.route('**/api/upload', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ files: [{ id: known }] }) }));
  await dropFiles([['one.png', red], ['two.png', green]]);
  await toast.filter({ hasText: 'Some images could not be uploaded' }).waitFor();
  await untilNote(api, note.id, n => UPLOADS(n?.content).length === 3, 'the one image that came back');
  await page.unroute('**/api/upload');
  await closeWindow(page);

  // Untouched blocks go back byte for byte, although the editor would write
  // each of them differently. Nothing is locked: typing into the block with
  // math, or the `+` bullets the renderer shows as text, changes only what
  // was typed (an escape the editor would add is not added).
  const SOURCE = ['Intro with _soft_ and __strong__ words', '+ plus bullet\n+ another', 'Area $x^2$ stays math', '3) odd start', 'Last line'].join('\n\n');
  const blocks = await (await api.post('/api/notes', { data: { title: `Blocks ${size}`, content: SOURCE } })).json();
  await openById(page, blocks.id);
  assert.equal(await body.locator('[contenteditable="false"]:not(.md-island):not(.md-task-box)').count(), 0, 'no block is locked');
  assert.equal(await win.locator('.note-window-hint').count(), 0);
  await caretAtEnd(page, '#note-window-modal .note-window-body > :last-child');
  await page.keyboard.type(' edited');
  await untilNote(api, blocks.id, n => n?.content.endsWith('Last line edited'), 'the one edited block');
  assert.equal((await getNote(api, blocks.id)).note.content, SOURCE + ' edited', 'every other block is the stored source, byte for byte');
  await caretAfter(page, '+ another');
  await page.keyboard.type(' too');
  const both = SOURCE.replace('+ another', '+ another too') + ' edited';
  await untilNote(api, blocks.id, n => n?.content === both, 'the bullets shown as text, typed into');
  await caretAfter(page, 'stays math');
  await page.keyboard.type(' still');
  await untilNote(api, blocks.id, n => n?.content === both.replace('stays math', 'stays math still'), 'the block with math, typed into');
  await closeWindow(page);

  // A card tick behind the open window is this tab's own write: the window
  // takes it, and typing afterwards saves without a conflict. Then the same
  // with unsaved typing in the window when the card is ticked: both survive.
  // Desktop only, where the cards sit beside the window. A click on a card
  // takes focus out of the window; the synthetic click below does not, so
  // the test blurs first.
  if (!mobile) {
    const ticked = await (await api.post('/api/notes', { data: { title: 'Tick behind', content: 'Before\n\n- [ ] behind the window\n- [ ] second' } })).json();
    const tickCard = index => page.evaluate(({ id, index }) => {
      document.activeElement?.blur();
      document.querySelectorAll(`.note-card[data-note-id="${id}"] .note-check-dot`)[index].click();
    }, { id: ticked.id, index });
    await page.locator(`.note-card[data-note-id="${ticked.id}"]`).waitFor();
    await openById(page, ticked.id);
    await tickCard(0);
    await untilNote(api, ticked.id, n => n?.items?.[0]?.done === true, 'the card tick');
    await body.locator('li.md-task[data-done="true"]').waitFor();
    await caretAtEnd(page, '#note-window-modal .note-window-body p');
    await page.keyboard.type(' and after');
    await untilNote(api, ticked.id, n => n?.content === 'Before and after\n\n- [x] behind the window\n- [ ] second', 'the window save over its own tick');
    await caretAtEnd(page, '#note-window-modal .note-window-body p');
    await page.keyboard.type(', still typing');
    await tickCard(1);
    await untilNote(api, ticked.id, n => n?.content === 'Before and after, still typing\n\n- [x] behind the window\n- [x] second', 'the typing and the second tick');
    assert.equal(await win.locator('.note-window-banner:not(.hidden)').count(), 0, 'no conflict banner');
    await closeWindow(page);
    assert.equal((await getNote(api, ticked.id)).note.content, 'Before and after, still typing\n\n- [x] behind the window\n- [x] second', 'closing writes neither tick back');
    await api.delete(`/api/notes/${ticked.id}`);

    // A stored note using CRLF and ending in a newline: the window shows it
    // without either, which read as an edit, so a tick on its card with the
    // caret idle in the window was never drawn. The caret stays in the
    // window this time (no blur), which is the path that compares the two.
    const crlf = await (await api.post('/api/notes', { data: { title: 'CRLF tick', content: 'Before\r\n\r\n- [ ] crlf first\r\n- [ ] crlf second\r\n' } })).json();
    assert.equal((await getNote(api, crlf.id)).note.content, 'Before\r\n\r\n- [ ] crlf first\r\n- [ ] crlf second\r\n', 'stored as sent');
    await page.locator(`.note-card[data-note-id="${crlf.id}"]`).waitFor();
    await openById(page, crlf.id);
    await caretAtEnd(page, '#note-window-modal .note-window-body p');
    await page.evaluate(id => document.querySelectorAll(`.note-card[data-note-id="${id}"] .note-check-dot`)[0].click(), crlf.id);
    const tick = await untilNote(api, crlf.id, n => n?.items?.[0]?.done === true, 'the card tick on the CRLF note');
    assert.equal(await page.evaluate(() => !!document.activeElement?.closest('#note-window-modal .note-window-body')), true, 'the caret stayed in the window');
    await body.locator('li.md-task[data-done="true"]').waitFor({ timeout: 5000 });
    assert.equal(await body.locator('li.md-task[data-done="true"]').count(), 1, 'the window draws the card\'s tick');
    assert.equal(await win.locator('.note-window-banner:not(.hidden)').count(), 0, 'no conflict banner');
    await closeWindow(page);
    const closed = (await getNote(api, crlf.id)).note;
    assert.equal(closed.content, tick.content, 'the window writes nothing back');
    assert.equal(closed.updated_at, tick.updated_at, 'not even the same text');
    await api.delete(`/api/notes/${crlf.id}`);
  }

  // Opening another note flushes the typing of the open one first.
  const first = await (await api.post('/api/notes', { data: { title: `First ${size}`, content: 'One' } })).json();
  const second = await (await api.post('/api/notes', { data: { title: `Second ${size}`, content: 'Two' } })).json();
  await openById(page, first.id);
  await caretAtEnd(page, '#note-window-modal .note-window-body p');
  await page.keyboard.type(' typed then switched');
  await openById(page, second.id);
  await until(() => win.locator('.note-window-title').inputValue(), v => v === `Second ${size}`, 'the second note in the window');
  await untilNote(api, first.id, n => n?.content === 'One typed then switched', 'the flush of the first note');
  // The server has the text before the page has read the answer that clears the draft.
  await until(() => page.evaluate(id => localStorage.getItem('odysseus-note-draft-' + id), first.id), draft => draft === null, 'no draft left for the first note');
  await closeWindow(page);

  // A note link in chat opens it in the window (the chat renderer's legacy
  // `notes.js` facade). The window gets its own #note-<id> entry, and closing
  // it gives the hash from before back.
  await page.evaluate(() => window.notesModule.closePanel());
  await page.locator('#notes-pane').waitFor({ state: 'detached' });
  await page.evaluate(id => {
    const link = document.createElement('a');
    link.href = `#note-${id}`;
    link.className = 'chat-note-link-under-test';
    link.textContent = 'the note';
    document.getElementById('chat-history').appendChild(link);
  }, second.id);
  const hashBefore = await page.evaluate(() => location.hash);
  await tapOrClick(mobile, page.locator('.chat-note-link-under-test'));
  await until(() => win.locator('.note-window-title').inputValue().catch(() => ''), v => v === `Second ${size}`, 'the linked note in the window');
  assert.equal(await page.evaluate(() => location.hash), `#note-${second.id}`);
  await closeWindow(page);
  await until(() => page.evaluate(() => location.hash), h => h === hashBefore, 'the hash from before the link');

  await context.close();
  for (const id of [note.id, blocks.id, first.id, second.id]) await api.delete(`/api/notes/${id}`);
}
