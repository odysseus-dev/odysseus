import assert from 'node:assert/strict';

// What a save and a long note cost the grid, and what a re-read costs. A
// save from the note window redraws only that note's card; a long note's
// card renders only its preview; an unchanged list answers a re-read with
// 304 and no body.
export async function checkCardCost({ context, page, mobile, origin }) {
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const lines = [];
  for (let i = 0; i < 5000; i++) lines.push(i % 4 === 0 ? `- [ ] long task ${i}` : `long line ${i} with some words`);
  const long = await (await context.request.post(`${origin}/api/notes`, { data: { title: 'Long card', content: lines.join('\n') } })).json();
  const small = await (await context.request.post(`${origin}/api/notes`, { data: { title: 'Small card', content: 'one line' } })).json();
  await page.evaluate(() => window.notesModule.closePanel());
  await page.locator('#notes-pane').waitFor({ state: 'detached' });
  await page.evaluate(() => window.notesModule.openPanel());
  const longCard = page.locator(`.note-card[data-note-id="${long.id}"]`);
  await longCard.waitFor();

  // The long card renders its first lines, not 5,000.
  const shape = await longCard.evaluate(card => ({
    nodes: card.getElementsByTagName('*').length,
    tasks: card.querySelectorAll('li.md-task').length,
    last: card.querySelector('.note-md-preview')?.lastElementChild?.textContent.trim(),
  }));
  assert.ok(shape.nodes < 600, `the long card is a preview: ${JSON.stringify(shape)}`);
  assert.ok(shape.tasks > 0 && shape.tasks <= 12, `only the shown tasks are rendered: ${JSON.stringify(shape)}`);
  assert.equal(shape.last, '…');
  // Its first task still ticks the right line on the server.
  await longCard.locator('li.md-task').first().locator('.md-task-box').evaluate(box => box.click());
  await expectNote(context, origin, long.id, note => note.content.startsWith('- [x] long task 0\n'), 'the long card tick');

  // A save from the window redraws only its own card. The first write lifts
  // the note to the top (a full redraw); the next one moves nothing.
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), small.id);
  await body.waitFor();
  await page.waitForTimeout(500);
  const typeAndSave = async text => {
    await body.click();
    await page.keyboard.press('End');
    await page.keyboard.type(text);
    await expectNote(context, origin, small.id, note => note.content.endsWith(text), `the save of ${text}`);
    await page.waitForFunction(id => !localStorage.getItem('odysseus-note-draft-' + id), small.id);
    await page.waitForTimeout(150);
  };
  await typeAndSave(' first');
  const marked = await page.evaluate(() => {
    const cards = document.querySelectorAll('#notes-pane .note-card');
    cards.forEach(card => { card.dataset.costMark = '1'; });
    return cards.length;
  });
  // The save syncs the window; re-setting the body's contenteditable to the
  // same value made the browser re-evaluate the whole body every time.
  await page.evaluate(() => {
    const target = document.querySelector('#note-window-modal .note-window-body');
    window.__editableWrites = 0;
    window.__editableObserver = new MutationObserver(records => { window.__editableWrites += records.length; });
    window.__editableObserver.observe(target, { attributes: true, attributeFilter: ['contenteditable'] });
  });
  await typeAndSave(' second');
  assert.equal(await page.evaluate(() => { window.__editableObserver.disconnect(); return window.__editableWrites; }), 0, 'a save leaves the body\'s contenteditable alone');
  const after = await page.evaluate(id => {
    const cards = Array.from(document.querySelectorAll('#notes-pane .note-card'));
    return { total: cards.length, redrawn: cards.filter(c => !c.dataset.costMark).map(c => c.dataset.noteId), firstId: cards[0]?.dataset.noteId, text: document.querySelector(`.note-card[data-note-id="${id}"]`)?.textContent };
  }, small.id);
  assert.equal(after.total, marked);
  assert.deepEqual(after.redrawn, [small.id], `only the saved card is redrawn: ${JSON.stringify(after)}`);
  assert.ok(after.text.includes('one line first second'), 'the redrawn card shows the save');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await win.waitFor({ state: 'detached' });

  // An unchanged list answers a re-read with 304.
  const conditional = await page.evaluate(async () => {
    const first = await fetch('/api/notes', { credentials: 'same-origin' });
    const tag = first.headers.get('ETag');
    const again = await fetch('/api/notes', { credentials: 'same-origin', headers: { 'If-None-Match': tag } });
    return { tag: !!tag, status: again.status, bytes: (await again.text()).length };
  });
  assert.deepEqual(conditional, { tag: true, status: 304, bytes: 0 });
  for (const note of [long, small]) await context.request.delete(`${origin}/api/notes/${note.id}`);
  return { longCardNodes: shape.nodes, redrawn: after.redrawn.length, mobile };
}

async function expectNote(context, origin, id, accept, label) {
  const deadline = Date.now() + 10000;
  for (;;) {
    const note = await (await context.request.get(`${origin}/api/notes/${id}`)).json();
    if (accept(note)) return note;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}: ${JSON.stringify(note).slice(0, 300)}`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}
