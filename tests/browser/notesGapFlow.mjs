// The owner's note window on paths no other flow takes: untouched blocks
// written back byte for byte and the edited one with only the edit changed, a
// card tick behind the open window, and opening a second note while the first
// has unsaved typing.
import assert from 'node:assert/strict';
import { getNote, openNotes, pngOf, tapOrClick, until, untilNote } from './notesHarness.mjs';

const closeWindow = async page => {
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close')?.click());
  await page.locator('#note-window-modal').waitFor({ state: 'detached' });
};
// The list is read when the panel opens: reopen it for a note just made through the API.
const reloadList = async page => {
  await page.evaluate(() => window.notesModule.closePanel());
  await page.locator('#notes-pane').waitFor({ state: 'detached' });
  await page.evaluate(() => window.notesModule.openPanel());
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
export async function checkWindowGaps({ browser, owner, size }) {
  const mobile = size === 'phone';
  const { context, page } = await openNotes(browser, owner, size);
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const api = owner.api;

  // Untouched blocks go back byte for byte, although the editor would write
  // each of them differently: typing into the last block changes only what
  // was typed. A block the editor cannot hold (math) is locked.
  const SOURCE = ['Intro with _soft_ and __strong__ words', '+ plus bullet\n+ another', 'Area $x^2$ stays math', '3) odd start', 'Last line'].join('\n\n');
  const blocks = await (await api.post('/api/notes', { data: { title: `Blocks ${size}`, content: SOURCE } })).json();
  await openById(page, blocks.id);
  assert.equal(await body.locator('.note-md-locked', { hasText: 'stays math' }).count(), 1, 'the block with math is locked');
  await caretAtEnd(page, '#note-window-modal .note-window-body > :last-child');
  await page.keyboard.type(' edited');
  await untilNote(api, blocks.id, n => n?.content.endsWith('Last line edited'), 'the one edited block');
  assert.equal((await getNote(api, blocks.id)).note.content, SOURCE + ' edited', 'every other block is the stored source, byte for byte');
  await closeWindow(page);

  // A card tick behind the open window is this tab's own write: the window
  // takes it, and typing afterwards saves without a conflict. Desktop only, where the cards sit beside the window. A click on a card
  // takes focus out of the window; the synthetic click below does not, so
  // the test blurs first.
  if (!mobile) {
    const ticked = await (await api.post('/api/notes', { data: { title: 'Tick behind', content: 'Before\n\n- [ ] behind the window\n- [ ] second' } })).json();
    const tickCard = index => page.evaluate(({ id, index }) => {
      document.activeElement?.blur();
      document.querySelectorAll(`.note-card[data-note-id="${id}"] .note-check-dot`)[index].click();
    }, { id: ticked.id, index });
    await reloadList(page);
    await page.locator(`.note-card[data-note-id="${ticked.id}"]`).waitFor();
    await openById(page, ticked.id);
    await tickCard(0);
    await untilNote(api, ticked.id, n => n?.items?.[0]?.done === true, 'the card tick');
    await body.locator('li.md-task[data-done="true"]').waitFor();
    await caretAtEnd(page, '#note-window-modal .note-window-body p');
    await page.keyboard.type(' and after');
    await untilNote(api, ticked.id, n => n?.content === 'Before and after\n\n- [x] behind the window\n- [ ] second', 'the window save over its own tick');
    assert.equal(await win.locator('.note-window-banner:not(.hidden)').count(), 0, 'no conflict banner');
    await closeWindow(page);
    assert.equal((await getNote(api, ticked.id)).note.content, 'Before and after\n\n- [x] behind the window\n- [ ] second', 'closing writes nothing back');
    await api.delete(`/api/notes/${ticked.id}`);
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

  await context.close();
  for (const id of [blocks.id, first.id, second.id]) await api.delete(`/api/notes/${id}`);
}
