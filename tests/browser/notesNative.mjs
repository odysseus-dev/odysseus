import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { browserName, engine, pngOf, signIn, sqlite, VIEWPORTS } from './notesHarness.mjs';
import { checkWindowGaps } from './notesGapFlow.mjs';
import { checkWindowUi } from './notesUiFlow.mjs';
import { createFlowRunner } from './notesRunner.mjs';

const origin = process.env.ODYSSEUS_NOTES_RUNTIME_ORIGIN;
// NOTES_FLOWS=native,ui runs only those flows (all by default).
const flows = new Set((process.env.NOTES_FLOWS || 'native,gaps,ui').split(',').map(s => s.trim()));
const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
if (evidence) fs.mkdirSync(evidence, { recursive: true });
const noteById = async (context, id) => (await (await context.request.get(`${origin}/api/notes/${id}`)).json());
// Poll the API from node: page.waitForFunction would treat an async
// predicate's Promise as truthy and return at once.
async function untilNote(context, id, accept, label) {
  const deadline = Date.now() + 10000;
  for (;;) {
    const response = await context.request.get(`${origin}/api/notes/${id}`);
    const note = response.ok() ? await response.json() : null;
    if (accept(note, response.status())) return note;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}: ${JSON.stringify(note)}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

// The seeded legacy note was folded by the startup migration: its card renders
// the task lines, a checkbox toggles through the API and survives a reload,
// and a note naming an external image never makes the page fetch it.
const EXTERNAL_IMAGES = 'Before\n\n![tracker](https://images.invalid/md.png?q=1)\n\n<img src="https://images.invalid/raw.png">\n\n<details><summary>more</summary><img src="https://images.invalid/details.png"></details>';

async function checkMarkdownCards({ context, page, mobile, externalId, outside }) {
  const legacy = await noteById(context, 'legacy-checklist');
  const firstDone = legacy.items[0].done;
  if (!mobile) {
    assert.equal(legacy.content, '- [ ] Seeded first\n- [x] Seeded second');
    assert.deepEqual(legacy.items.map(item => [item.text, item.done]), [['Seeded first', false], ['Seeded second', true]]);
    assert.equal(legacy.note_type, 'checklist');
  }
  const imageCard = page.locator(`.note-card[data-note-id="${externalId}"]`);
  await imageCard.waitFor();
  assert.equal(await imageCard.locator('a[href="https://images.invalid/md.png?q=1"]').count(), 1);
  assert.equal(await imageCard.locator('img[src*="images.invalid"]').count(), 0);
  assert.deepEqual(outside, []);

  const rows = page.locator('.note-card[data-note-id="legacy-checklist"] li.md-task.note-checkbox');
  assert.equal(await rows.count(), 2);
  assert.equal(await rows.nth(1).evaluate(el => el.classList.contains('done')), true);
  if (evidence) await page.screenshot({ path: path.join(evidence, mobile ? 'markdown-mobile.png' : 'markdown-desktop.png'), fullPage: true, animations: 'disabled' });
  if (mobile) await rows.nth(0).locator('.note-check-text').tap();
  else await rows.nth(0).locator('.note-check-dot').click();
  await page.waitForFunction(async done => {
    const note = await (await fetch('/api/notes/legacy-checklist', { credentials: 'same-origin' })).json();
    return note.items[0].done === done;
  }, !firstDone, { polling: 250 });
  assert.equal(await page.locator('#note-window-modal').count(), 0, 'a card checkbox tap must not open the note window');
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await rows.first().waitFor();
  assert.equal(await rows.nth(0).evaluate(el => el.classList.contains('done')), !firstDone);
}

// The note window is the only editor. Every write it makes is recorded so the
// test can assert that opening and closing write nothing.
async function checkNoteWindow({ context, page, mobile, created }) {
  const writes = [];
  page.on('request', request => { if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/notes')) writes.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const tap = locator => (mobile ? locator.tap() : locator.click());
  const closeWindow = () => page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  const listCount = async () => (await (await context.request.get(`${origin}/api/notes`)).json()).notes.length;

  // A new note closed untouched creates nothing; the sidebar action focuses the body.
  const before = await listCount();
  await page.evaluate(() => window.notesModule.newNote());
  await body.waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('note-window-body')), true, 'new note focuses the body');
  await closeWindow();
  await win.waitFor({ state: 'detached' });
  await page.waitForTimeout(800);
  assert.equal(await listCount(), before, 'an untouched new note is never created');
  assert.deepEqual(writes, []);

  // Open, type, close, reopen.
  await tap(page.locator(`.note-card[data-note-id="${created.id}"] .note-card-title`));
  await body.waitFor();
  await page.waitForTimeout(600); // the sheet slides up first on a phone
  if (evidence) await page.screenshot({ path: path.join(evidence, mobile ? 'window-mobile.png' : 'window-desktop.png'), animations: 'disabled' });
  if (mobile) {
    const box = await win.locator('.note-window-content').boundingBox();
    // Full height of whatever phone this is (a device profile or the plain 390x844).
    const screen = page.viewportSize();
    assert.ok(box.width >= screen.width - 1 && box.height >= screen.height - 1, `phone sheet is full height: ${JSON.stringify({ box, screen })}`);
    assert.equal(await body.evaluate(el => getComputedStyle(el).fontSize), '16px', 'no iOS focus zoom');
    // iOS Safari can focus the tapped card title after the tap opened the window;
    // that focus must not lift the Notes panel over the sheet.
    await page.evaluate(id => document.querySelector(`.note-card[data-note-id="${id}"] .note-card-title`).focus(), created.id);
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => {
      const r = document.querySelector('#note-window-modal .note-window-content').getBoundingClientRect();
      return !!document.elementFromPoint(r.left + r.width / 2, r.top + 80)?.closest('#note-window-modal');
    }), true, 'the note window stays above the Notes panel after focus moves into the panel');
  }
  await page.waitForTimeout(700);
  assert.deepEqual(writes, [], 'opening a note writes nothing');
  await body.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('Saved by the note window');
  await page.waitForFunction(id => !localStorage.getItem('odysseus-note-draft-' + id), created.id);
  assert.equal((await noteById(context, created.id)).content, 'Saved by the note window');

  // Checklist from the toolbar, then Enter, Tab and Enter on an empty task.
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await win.locator('[data-md="check"]').click();
  await page.keyboard.type('first task');
  await page.keyboard.press('Enter');
  await page.keyboard.type('nested task');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await page.keyboard.type('after the list');
  await untilNote(context, created.id, note => note.content.includes('after the list'), 'the checklist save');
  assert.equal((await noteById(context, created.id)).content, 'Saved by the note window\n- [ ] first task\n  - [ ] nested task\nafter the list');

  // A checkbox goes through the toggle endpoint, never a whole-content PUT.
  writes.length = 0;
  await page.evaluate(() => document.activeElement?.blur());
  await tap(win.locator('.md-task-box').first());
  await untilNote(context, created.id, note => note.items?.[0]?.done, 'the toggle');
  assert.deepEqual(writes, [`POST /api/notes/${created.id}/items/0/toggle`]);
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('note-window-body')), false, 'a checkbox tap does not focus the body (no phone keyboard)');

  // Colour ring and tags.
  await win.locator('.note-color-dot[data-color="blue"]').click();
  await win.locator('.note-window-tag-input').click();
  await page.keyboard.type('#Errands');
  await page.keyboard.press('Enter');
  await untilNote(context, created.id, note => note.color === 'blue' && /Errands/.test(note.label || ''), 'colour and tag');
  assert.notEqual(await win.locator('.note-window-content').evaluate(el => getComputedStyle(el).getPropertyValue('--note-ring').trim()), 'transparent');

  // Close and reopen: the window shows what was saved.
  await closeWindow();
  await win.waitFor({ state: 'detached' });
  await tap(page.locator(`.note-card[data-note-id="${created.id}"] .note-card-title`));
  await body.waitFor();
  assert.equal(await win.locator('li.md-task').count(), 2);
  assert.equal(await win.locator('.note-tag-chip').filter({ hasText: 'Errands' }).count(), 1);

  // A stale save is refused, the local text is kept and the choice is offered.
  const server = await noteById(context, created.id);
  await context.request.put(`${origin}/api/notes/${created.id}`, { data: { content: server.content + '\n\nFrom another device', replace_content: true, body_revision: server.body_revision } });
  await win.locator('.note-window-title').click();
  await page.keyboard.press('End');
  await page.keyboard.type(' (edited here)');
  await win.locator('.note-window-banner').filter({ hasText: 'changed elsewhere' }).waitFor();
  assert.match(await page.evaluate(id => JSON.parse(localStorage.getItem('odysseus-note-draft-' + id)).title, created.id), /edited here/);
  await win.locator('.note-window-banner button', { hasText: 'Reload' }).click();
  assert.match(await body.textContent(), /From another device/);

  // Archive from the window, then delete a scratch note from its window.
  await win.locator('[data-act="archive"]').click();
  await win.waitFor({ state: 'detached' });
  await untilNote(context, created.id, note => note.archived === true, 'archive');
  const scratch = await (await context.request.post(`${origin}/api/notes`, { data: { title: 'Delete me', content: 'gone soon' } })).json();
  await page.evaluate(id => void window.notesModule.openNote(id), scratch.id);
  await body.waitFor();
  await win.locator('[data-act="delete"]').click();
  await page.locator('button', { hasText: /^Delete$/ }).last().click();
  await untilNote(context, scratch.id, (_note, status) => status === 404, 'delete');
  await context.request.put(`${origin}/api/notes/${created.id}`, { data: { archived: false } });
}

// Inline images: insert from the toolbar, paste a file, size S / M, reload
// (sizes kept), card thumbnail, remove, sketch, beside math, and inert when view-only.
const LEGACY_CANVAS = '/api/upload/0123456789abcdef0123456789abcdef.png';
async function checkStoredTypes({ context, page, mobile }) {
  const win = page.locator('#note-window-modal');
  const tag = mobile ? 'phone' : 'desktop';
  const goal = await noteById(context, 'legacy-goal');
  assert.equal(goal.note_type, 'goal');
  assert.ok(goal.content.startsWith('- [x] Goal step one\n- [ ] Goal step two'), goal.content);
  assert.equal(await page.locator('.note-card[data-note-id="legacy-goal"].note-card-goal').count(), 1);
  await page.evaluate(() => void window.notesModule.openNote('legacy-goal', { focus: null }));
  await win.locator('.note-window-body li.md-task').last().waitFor();
  await page.evaluate(() => {
    const text = [...document.querySelectorAll('#note-window-modal .note-window-body li.md-task .md-task-text')].pop();
    const range = document.createRange(); range.selectNodeContents(text); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range); text.closest('[contenteditable="true"]').focus();
  });
  await page.keyboard.press('Enter');
  await page.keyboard.type(`step from ${tag}`);
  const edited = await untilNote(context, 'legacy-goal', n => n.content.includes(`- [ ] step from ${tag}`), 'the goal edit');
  assert.equal(edited.note_type, 'goal', 'an edited goal stays a goal');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await win.waitFor({ state: 'detached' });

  const drawing = await noteById(context, 'legacy-drawing');
  // The seed's canvas names an upload this instance never stored, and a save
  // that references a missing upload is refused. Point the folded row at a
  // real upload once, so the re-save below checks what the window writes.
  let canvas = drawing.content.includes(LEGACY_CANVAS) ? LEGACY_CANVAS : drawing.content.match(/\/api\/upload\/[^)\s]+/)?.[0];
  assert.deepEqual([drawing.note_type, drawing.gallery_id, drawing.content.split(canvas).length - 1], ['draw', 'gallery-legacy-drawing', 1], JSON.stringify(drawing));
  if (canvas === LEGACY_CANVAS) {
    const upload = await context.request.post(`${origin}/api/upload`, { multipart: { files: { name: 'canvas.png', mimeType: 'image/png', buffer: await pngOf(page, '#3f9a6a') } } });
    assert.equal(upload.ok(), true, await upload.text());
    const real = `/api/upload/${(await upload.json()).files[0].id}`;
    sqlite('UPDATE notes SET content = replace(content, ?, ?), image_url = replace(image_url, ?, ?) WHERE id = ?', [LEGACY_CANVAS, real, LEGACY_CANVAS, real, 'legacy-drawing']);
    canvas = real;
    await page.evaluate(() => window.notesModule.closePanel());
    await page.locator('#notes-pane').waitFor({ state: 'detached' });
    await page.evaluate(() => window.notesModule.openPanel());
    await page.locator('.note-card[data-note-id="legacy-drawing"]').waitFor();
  }
  await page.evaluate(() => void window.notesModule.openNote('legacy-drawing', { focus: null }));
  await win.locator('.note-window-body img.md-note-image').waitFor({ state: 'attached' });
  await win.locator('.note-window-title').evaluate(input => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); });
  await page.keyboard.type(` (${tag})`);
  const saved = await untilNote(context, 'legacy-drawing', n => n.title.endsWith(`(${tag})`), 'the drawing re-save');
  assert.deepEqual([saved.note_type, saved.gallery_id, saved.content.split(canvas).length - 1], ['draw', 'gallery-legacy-drawing', 1], 'a re-saved drawing keeps its type, gallery image and one canvas');
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await win.waitFor({ state: 'detached' });
}

// Raw HTML in a note never fetches from elsewhere, through any attribute
// that loads, on the card or in the window; and a card's add-item input
// appends through the items route, never a whole-note PUT.
const SCRUBBED = [
  '<img srcset="https://images.invalid/srcset.png 1x">',
  '<video poster="https://images.invalid/poster.png"></video>',
  '<table background="https://images.invalid/table.png"><tr><td>cell</td></tr></table>',
  '<svg><image xlink:href="https://images.invalid/xlink.png" href="https://images.invalid/href.png"></image></svg>',
  '<div style="background-image:url(https://images.invalid/style.png)">styled</div>',
].join('\n\n');
async function checkCardEdges({ context, page, mobile, outside }) {
  const note = await (await context.request.post(`${origin}/api/notes`, { data: { title: mobile ? 'Scrub phone' : 'Scrub desktop', content: SCRUBBED } })).json();
  await refreshPanel(page);
  await page.locator(`.note-card[data-note-id="${note.id}"]`).waitFor();
  await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), note.id);
  await page.locator('#note-window-modal .note-window-body').waitFor();
  const frames = await page.locator('#notes-pane iframe, #note-window-modal iframe').evaluateAll(list => list.map(f => [f.className, f.getAttribute('sandbox'), /img-src 'none'/.test(f.getAttribute('srcdoc') || '')]));
  assert.ok(frames.length >= 2 && frames.every(([cls, sandbox, csp]) => cls === 'chat-svg-preview' && sandbox === '' && csp), `raw SVG stays in the locked preview: ${JSON.stringify(frames)}`);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await page.locator('#note-window-modal').waitFor({ state: 'detached' });
  assert.deepEqual(outside, [], 'no request leaves the instance');
  await context.request.delete(`${origin}/api/notes/${note.id}`);
  if (mobile) return; // the card add-item input is desktop only
  const writes = [];
  const record = request => { if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/notes')) writes.push(`${request.method()} ${new URL(request.url()).pathname}`); };
  page.on('request', record);
  const before = await noteById(context, 'legacy-checklist');
  const input = page.locator('.note-card[data-note-id="legacy-checklist"] .note-cl-quickadd-input');
  await input.fill('Added from the card');
  await input.press('Enter');
  await untilNote(context, 'legacy-checklist', n => n.content.endsWith('- [ ] Added from the card'), 'the card append');
  page.off('request', record);
  assert.deepEqual(writes, ['POST /api/notes/legacy-checklist/items']);
  await context.request.put(`${origin}/api/notes/legacy-checklist`, { data: { content: before.content } });
}
const refreshPanel = async page => {
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
};

// The note window's less-travelled paths: Keep mine after a 409, an earlier
// draft offered back (Restore, then Discard), Markdown source mode, and paste
// of rich markup arriving as plain text.
async function checkWindowEdges({ context, page, mobile }) {
  const win = page.locator('#note-window-modal');
  const body = win.locator('.note-window-body');
  const tag = mobile ? 'phone' : 'desktop';
  const closeWindow = async () => {
    await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
    await win.waitFor({ state: 'detached' });
  };
  const open = async id => {
    await page.evaluate(id => void window.notesModule.openNote(id, { focus: null }), id);
    await body.waitFor();
  };
  const caretAtEnd = () => page.evaluate(() => {
    const blocks = document.querySelectorAll('#note-window-modal .note-window-body > *');
    const target = blocks[blocks.length - 1];
    const range = document.createRange(); range.selectNodeContents(target); range.collapse(false);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    target.closest('[contenteditable="true"]').focus();
  });
  const note = await (await context.request.post(`${origin}/api/notes`, { data: { title: `Edges ${tag}`, content: 'First line' } })).json();

  // 409, then Keep mine: the local text is written over the other device's.
  await open(note.id);
  const server = await noteById(context, note.id);
  await context.request.put(`${origin}/api/notes/${note.id}`, { data: { content: 'From another device', replace_content: true, body_revision: server.body_revision } });
  await caretAtEnd();
  await page.keyboard.type(' kept');
  await win.locator('.note-window-banner').filter({ hasText: 'changed elsewhere' }).waitFor();
  await win.locator('.note-window-banner button', { hasText: 'Keep mine' }).click();
  await untilNote(context, note.id, n => n.content === 'First line kept', 'Keep mine');
  assert.equal(await win.locator('.note-window-banner:not(.hidden)').count(), 0);

  // Paste: rich markup arrives as its plain text; nothing from the HTML lands.
  await caretAtEnd();
  await page.evaluate(() => {
    const data = new DataTransfer();
    data.setData('text/html', '<b>bold</b> <img src="https://images.invalid/pasted.png" onerror="window.__pasted = 1">');
    data.setData('text/plain', ' pasted plain');
    document.querySelector('#note-window-modal .note-window-body > :last-child')
      .dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  const pasted = await untilNote(context, note.id, n => n.content.endsWith('pasted plain'), 'the paste');
  assert.equal(pasted.content, 'First line kept pasted plain');
  assert.equal(await page.evaluate(() => window.__pasted), undefined);
  assert.equal(await body.locator('img').count(), 0);

  // Markdown source: the raw text, editable, back to rich and saved.
  await win.locator('[data-md="source"]').click();
  const source = win.locator('.note-window-source');
  await source.waitFor();
  assert.equal(await source.inputValue(), 'First line kept pasted plain');
  assert.equal(await body.isVisible(), false);
  await source.evaluate(el => { el.focus(); el.setSelectionRange(el.value.length, el.value.length); });
  await page.keyboard.type('\n\n- [ ] from source');
  await win.locator('[data-md="source"]').click();
  await body.locator('li.md-task').filter({ hasText: 'from source' }).waitFor();
  await untilNote(context, note.id, n => n.content === 'First line kept pasted plain\n\n- [ ] from source', 'the source edit');
  await closeWindow();

  // An unsaved draft from earlier on this device: Restore, then Discard.
  const current = await noteById(context, note.id);
  const seedDraft = content => page.evaluate(({ id, content, base }) => localStorage.setItem('odysseus-note-draft-' + id, JSON.stringify({ _ts: Date.now(), title: 'Edges restored', content, body_revision: base })), { id: note.id, content, base: current.body_revision });
  await seedDraft('Restored from this device');
  await open(note.id);
  await win.locator('.note-window-banner').filter({ hasText: 'unsaved changes from earlier on this device' }).waitFor();
  await win.locator('.note-window-banner button', { hasText: 'Restore' }).click();
  const restored = await untilNote(context, note.id, n => n.content === 'Restored from this device', 'Restore');
  assert.equal(restored.title, 'Edges restored');
  await closeWindow();
  const writes = [];
  const record = request => { if (request.method() !== 'GET' && request.url().includes(`/api/notes/${note.id}`)) writes.push(request.method()); };
  page.on('request', record);
  await seedDraft('Never saved');
  await open(note.id);
  await win.locator('.note-window-banner button', { hasText: 'Discard' }).click();
  assert.equal(await page.evaluate(id => localStorage.getItem('odysseus-note-draft-' + id), note.id), null);
  await closeWindow();
  page.off('request', record);
  assert.deepEqual(writes, [], 'Discard writes nothing');
  assert.equal((await noteById(context, note.id)).content, 'Restored from this device');

  // Minimised, the window becomes a dock chip named after the note (desktop).
  if (!mobile) {
    await open(note.id);
    await win.locator('.modal-minimize-btn').click();
    const chip = page.locator('.minimized-dock-chip[data-modal-id="note-window-modal"]');
    await chip.waitFor();
    assert.equal(await chip.getAttribute('title'), 'Restore Note: Edges restored');
    await chip.click();
    await body.waitFor();
    await closeWindow();
  }
  await context.request.delete(`${origin}/api/notes/${note.id}`);
}

const browser = await engine.launch({ headless: true });
const credentials = { username: 'notes-reference', password: randomUUID() + 'Aa1!' };
try {
  const setupContext = await browser.newContext();
  const setup = await setupContext.request.post(`${origin}/api/auth/setup`, { data: credentials });
  assert.equal(setup.ok(), true, await setup.text());
  await setupContext.close();
  // Each flow runs isolated (notesRunner.mjs): a failure is recorded, the
  // contexts it opened are closed, and the next flow still runs.
  const runner = createFlowRunner({ browser, browserName });
  const guarded = runner.run;
  for (const mobile of flows.has('native') ? [false, true] : []) await guarded('native', mobile ? 'phone' : 'desktop', async () => {
    const context = await browser.newContext(VIEWPORTS[mobile ? 'phone' : 'desktop']);
    const page = await context.newPage();
    const login = await context.request.post(`${origin}/api/auth/login`, { data: credentials });
    assert.equal(login.ok(), true, await login.text());
    const response = await context.request.post(`${origin}/api/notes`, { data: { title: mobile ? 'Mobile reference' : 'Desktop reference', content: 'Native API draft', note_type: 'note', label: 'reference' } });
    assert.equal(response.ok(), true, await response.text());
    const created = await response.json();
    assert.ok(created.id);
    assert.equal(created.owner, credentials.username);
    const external = await (await context.request.post(`${origin}/api/notes`, { data: { title: mobile ? 'Mobile images' : 'Desktop images', content: EXTERNAL_IMAGES } })).json();
    const outside = [];
    // The page's own requests. Raw SVG renders in a sandboxed preview frame
    // whose CSP refuses every load; Chromium still reports those attempts.
    page.on('request', request => { if (request.frame() === page.mainFrame() && /\.invalid$/.test(new URL(request.url()).hostname)) outside.push(request.url()); });
    await page.goto(origin);
    await page.waitForFunction(() => window.notesModule?.openPanel);
    await page.evaluate(() => { localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'); window.notesModule.openPanel(); });
    const card = page.locator(`.note-card[data-note-id="${created.id}"]`);
    await card.waitFor();
    if (evidence) await page.screenshot({ path: path.join(evidence, mobile ? 'native-mobile.png' : 'native-desktop.png'), fullPage: true, animations: 'disabled' });
    await checkMarkdownCards({ context, page, mobile, externalId: external.id, outside });
    await page.locator('#notes-select-btn').click();
    await card.locator('.note-card-cb').check({ force: mobile });
    await page.locator('#notes-send-agent').click();
    assert.ok(await page.locator('.note-card-selectmode').count());
    await page.evaluate(async () => {
      const sessions = await import('/static/js/sessions.js');
      sessions.createDirectChat('http://127.0.0.1:1', 'reference-model', null);
    });
    await page.locator('#notes-send-agent').click();
    assert.equal(await page.locator('.note-card-selectmode').count(), 0);
    assert.equal(await page.locator('#selected-notes-context-count').textContent(), '1 note selected');
    await page.evaluate(async () => (await import('/static/js/sessions.js')).createDirectChat('http://127.0.0.1:1', 'another-reference-model', null));
    assert.equal(await page.locator('#selected-notes-context-count').textContent(), '');
    await checkCardEdges({ context, page, mobile, outside });
    await checkStoredTypes({ context, page, mobile });
    await checkWindowEdges({ context, page, mobile });
    await checkNoteWindow({ context, page, mobile, created });
    await page.evaluate(() => window.notesModule.closePanel());
    await page.locator('#notes-pane').waitFor({ state: 'detached' });
    await context.close();
    console.log(JSON.stringify({ native: true, browser: browserName, mobile, noteId: created.id, crud: true, selectedContext: true, markdownCards: true, cardEdges: true, storedTypes: true, windowEdges: true, noteWindow: true }));
  });

  const owner = await signIn(credentials.username, credentials.password);
  for (const size of ['desktop', 'phone']) {
    if (flows.has('gaps')) await guarded('gaps', size, async () => {
      await checkWindowGaps({ browser, owner, size });
      console.log(JSON.stringify({ gaps: true, browser: browserName, size, blocksVerbatim: true, ownTick: !size.startsWith('phone'), flushOnSwitch: true }));
    });
    if (flows.has('ui')) await guarded('ui', size, async () => {
      await checkWindowUi({ browser, owner, size });
      console.log(JSON.stringify({ ui: true, browser: browserName, size }));
    });
  }
  if (flows.has('native')) await guarded('smoke', 'api', async () => {
    const smoke = spawnSync(process.env.NOTES_TEST_PYTHON, ['-m', 'pytest', '-q', 'tests/smoke/test_notes_smoke.py'], {
      env: { ...process.env, APP_PORT: new URL(origin).port, ODYSSEUS_ADMIN_USER: credentials.username, ODYSSEUS_ADMIN_PASSWORD: credentials.password },
      encoding: 'utf8', timeout: 90000,
    });
    assert.equal(smoke.status, 0, smoke.stdout + smoke.stderr);
    console.log('PASS: original Notes release smoke test against the disposable authenticated instance');
  });
  runner.finish();
} finally {
  await browser.close();
}
