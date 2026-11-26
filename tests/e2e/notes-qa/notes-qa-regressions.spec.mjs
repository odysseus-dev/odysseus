// Notes regression spec.
// Every test asserts the EXPECTED behaviour, so it is red while its bug is present.
//
// Run: venv/bin/python tests/e2e/notes-qa/run_notes_qa.py (see README.md here).
// It needs AUTH_ENABLED=true, an admin (NOTES_QA_OWNER) and one non-admin (NOTES_QA_COLLAB).
import { test, expect, request, devices } from '@playwright/test';
import { deflateSync, crc32 } from 'node:zlib';

const ORIGIN = process.env.NOTES_QA_ORIGIN;
const OWNER = { username: process.env.NOTES_QA_OWNER, password: process.env.NOTES_QA_OWNER_PW };
const COLLAB = { username: process.env.NOTES_QA_COLLAB, password: process.env.NOTES_QA_COLLAB_PW };

test.use({ viewport: { width: 1440, height: 900 } });

async function signIn(account) {
  const api = await request.newContext({ baseURL: ORIGIN });
  for (let i = 0; i < 40; i++) {
    const r = await api.post('/api/auth/login', { data: account });
    if (r.status() !== 429) { expect(r.ok(), `login ${account.username}`).toBeTruthy(); break; }
    await new Promise(done => setTimeout(done, 2000));
  }
  return api;
}
const created = [];
async function makeNote(api, title, content, extra = {}) {
  const note = await (await api.post('/api/notes', { data: { title, content, ...extra } })).json();
  created.push(note.id);
  return note.id;
}
const read = async (api, id) => (await api.get(`/api/notes/${id}`)).json();
// `setup(context)` runs before the page loads, e.g. to register routes.
async function openNote(browser, api, id, setup = null) {
  const context = await browser.newContext({ storageState: await api.storageState() });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  if (setup) await setup(context);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).click();
  return { context, page, errors, body: page.locator('#note-window-modal .note-window-body') };
}

test('typing during a checklist request survives the response and the next save', async ({ browser }) => {
  const api = await signIn(OWNER);
  let context;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let received = false;
  const text = 'text typed during the toggle';
  try {
    const id = await makeNote(api, 'Checklist response race', '- [ ] Item');
    const opened = await openNote(browser, api, id, ctx => ctx.route(`**/api/notes/${id}/items/0/toggle`, async route => {
      const response = await route.fetch();
      received = true;
      await gate;
      await route.fulfill({ response });
    }));
    ({ context } = opened);
    const { body, page } = opened;
    await body.locator('li input[type=checkbox], li [role=checkbox]').first().click();
    await expect.poll(() => received).toBe(true);
    await body.press('End');
    await page.keyboard.insertText(` ${text}`);
    await expect(body).toContainText(text);
    release();
    await expect.poll(async () => (await read(api, id)).content).toContain(`- [x] Item ${text}`);
    await expect(body).toContainText(text);
  } finally {
    release();
    await context?.close();
    await api.dispose();
  }
});

let owner, collab;
test.beforeAll(async () => { owner = await signIn(OWNER); collab = await signIn(COLLAB); });
test.afterAll(async () => { for (const id of created) await owner.delete(`/api/notes/${id}`); });

test('BLOCKER an edit in a note with a bullet list followed by a numbered list is saved', async ({ browser }) => {
  // This content is what the editor itself writes for: Bullet list, two items, Enter Enter, Numbered list, two items.
  const id = await makeNote(owner, 'QA lists', '- apples\n- pears\n1. wash\n2. peel');
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('li', { hasText: 'apples' }).first().click();
  await page.keyboard.press('Home');
  await page.keyboard.type('green ');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('green apples');
  await context.close();
});

test('BLOCKER the rendered note keeps the numbered list a list (no orphan <li>)', async ({ browser }) => {
  const id = await makeNote(owner, 'QA lists render', '- apples\n- pears\n1. wash\n2. peel');
  const { context, body } = await openNote(browser, owner, id);
  await expect(body.locator('ol > li')).toHaveCount(2);
  expect(await body.evaluate(root => [...root.querySelectorAll('li')].filter(li => !li.parentElement.matches('ul, ol')).length)).toBe(0);
  await context.close();
});

// the table rendered inside the item or heading right above
// it, and every edit in that block was saved without the table, or not at all.
for (const [name, lead, selector] of [['a list item', '- item', 'li'], ['a heading', '# Plan', 'h1']]) {
  test(`MAJOR an edit next to a table right under ${name} is saved, table included`, async ({ browser }) => {
    const table = '| A | B |\n|---|---|\n| 1 | 2 |';
    const id = await makeNote(owner, `QA table under ${name}`, `${lead}\n${table}`);
    const { context, page, body } = await openNote(browser, owner, id);
    await expect(body.locator('table')).toHaveCount(1);
    expect(await body.evaluate((root, tag) => root.querySelectorAll(`${tag} table`).length, selector), 'table inside the block above').toBe(0);
    await body.locator(selector).first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(' typed above');
    await body.locator('td', { hasText: '1' }).first().click();
    await page.keyboard.press('End');
    await page.keyboard.type('0');
    await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('| 10 | 2 |');
    const saved = (await read(owner, id)).content;
    expect(saved).toContain(`${lead} typed above`);
    expect(saved).toContain('| A | B |');
    await context.close();
    const again = await openNote(browser, owner, id);
    await expect(again.body.locator(selector).first()).toContainText('typed above');
    await expect(again.body.locator('td').first()).toHaveText('10');
    await again.context.close();
  });
}

for (const [name, content] of [['inline fence','see ```js\nfoo()``` here'], ['fence inside a quote', '> ```js\n> x\n> ```']]) {
  test(`BLOCKER a note with a ${name} opens in the window`, async ({ browser }) => {
    const id = await makeNote(owner, `QA ${name}`, content);
    const { context, page, errors, body } = await openNote(browser, owner, id);
    await page.waitForTimeout(1200);
    expect(errors, 'page errors').toEqual([]);
    await expect(body).not.toBeEmpty();
    await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(1);
    await context.close();
  });
}

// the code of a quoted fence showed its `> `, and every save
// wrote it into the code once more, plus a quote line per block. A fence in a
// <details> with a line above it was saved as the rendered <pre>.
for (const [name, content, selector, needle, code] of [
  ['a quote holding a code block', '> quote\n> ```js\n> x = 1\n> ```\n> end', 'blockquote p', 'quote', 'x = 1'],
  ['a quote holding code with a blank line', '> quote\n> ```\n> a\n>\n> b\n> ```', 'blockquote p', 'quote', 'a\n\nb'],
  ['a <details> holding a code block under a line', 'Intro line\n<details>\n<summary>S</summary>\n```js\nx = 1\n```\n</details>', 'p', 'Intro line', 'x = 1'],
]) {
  test(`MAJOR three edits in ${name} change only what was typed`, async ({ browser }) => {
    const id = await makeNote(owner, `QA ${name}`, content);
    for (let round = 1; round <= 3; round++) {
      const { context, page, errors, body } = await openNote(browser, owner, id);
      await expect(body.locator('pre code').first()).toHaveText(code);
      await body.locator(selector, { hasText: needle }).first().click();
      await page.keyboard.press('End');
      await page.keyboard.type('Z');
      const expected = content.replace(needle, needle + 'Z'.repeat(round));
      await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toBe(expected);
      expect(errors, 'page errors').toEqual([]);
      await context.close();
    }
  });
}

// A fault injected into one module, so the window's guard is tested on its own,
// whatever the renderer gets right: the served file is patched in flight.
async function openWithFault(browser, api, id, file, find, inject) {
  const context = await browser.newContext({ storageState: await api.storageState(), serviceWorkers: 'block' });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  await context.route(url => url.pathname.endsWith(file), async route => {
    const response = await route.fetch();
    const text = await response.text();
    if (!text.includes(find)) throw new Error(`fault anchor missing in ${file}`);
    await route.fulfill({ response, body: text.replace(find, find + inject) });
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`);
  await card.click();
  return { context, page, errors, card };
}

test('BLOCKER a note the editor fails to render opens once, in Markdown source, and still saves', async ({ browser }) => {
  const content = 'QA_RENDER_FAULT first line\n\nsecond line';
  const id = await makeNote(owner, 'QA render fault', content);
  const { context, page, errors, card } = await openWithFault(browser, owner, id, '/static/js/markdown.js',
    'export function mdToHtml(src, opts) {',
    "\n  if (opts && opts.sourceMap && String(src).includes('QA_RENDER_FAULT')) throw new TypeError('injected render fault');");
  const modal = page.locator('#note-window-modal');
  await expect(modal).toHaveCount(1);
  await expect(modal.locator('.note-window-banner')).toContainText('open in Markdown source');
  const source = modal.locator('.note-window-source');
  await expect(source).toBeVisible();
  await expect(source).toHaveValue(content);
  await card.click();
  await page.waitForTimeout(500);
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(1);
  // Back to the formatted view fails the same way: the window stays in source, text intact.
  await modal.locator('.note-window-toolbar [data-md="source"]').click();
  await expect(source).toBeVisible();
  await expect(source).toHaveValue(content);
  await source.click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' edited');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toBe(content + ' edited');
  expect(errors, 'page errors').toEqual([]);
  await context.close();
});

test('BLOCKER a note window that fails to build leaves nothing behind and says so', async ({ browser }) => {
  const id = await makeNote(owner, 'QA mount fault', 'body', { label: 'qafault' });
  const { context, page, card } = await openWithFault(browser, owner, id, '/static/js/features/notes/ui/noteTags.js',
    "function mount(container, { label = '', knownTags = () => [], onChange, onEscape = () => {} }) {",
    "\n    if (String(label).includes('qafault')) throw new Error('injected mount fault');");
  await expect(page.getByText('This note could not be opened.').first()).toBeVisible();
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(0);
  await card.click();
  await page.waitForTimeout(500);
  await expect(page.locator('#note-window-modal, .note-window')).toHaveCount(0);
  await context.close();
});

test('MAJOR editing a note written with "*" bullets does not rewrite it as italics', async ({ browser }) => {
  const id = await makeNote(owner, 'QA star bullets', '* alpha\n* beta');
  const { context, page, body } = await openNote(browser, owner, id);
  await expect(body.locator('ul > li')).toHaveCount(2);
  await body.getByText('alpha').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' x');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toMatch(/^[*-] alpha x\n[*-] beta$/);
  await context.close();
});

test('MAJOR a card tick made while the window holds typed text survives the next keystroke', async ({ browser }) => {
  const id = await makeNote(owner, 'QA tick revert', '- [ ] one\n- [ ] two\n\ntail text');
  const { context, page, body } = await openNote(browser, owner, id);
  const tail = body.locator('p', { hasText: 'tail text' });
  await tail.click(); await page.keyboard.press('End'); await page.keyboard.type(' A');
  await expect.poll(async () => (await read(owner, id)).content).toContain('tail text A');
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .md-task`, { hasText: 'one' }).locator('.md-task-box').evaluate(box => box.click());
  await expect.poll(async () => (await read(owner, id)).content).toContain('- [x] one');
  await tail.click(); await page.keyboard.press('End'); await page.keyboard.type('B');
  await page.waitForTimeout(1800);
  expect((await read(owner, id)).content).toContain('- [x] one');
  await context.close();
});

test('MAJOR text typed while a window tick is on its way to the server is kept and saved', async ({ browser }) => {
  const id = await makeNote(owner, 'QA type during tick', '- [ ] one\n- [ ] two\n\ntail text');
  const { context, page, body } = await openNote(browser, owner, id);
  // A slow link: only the toggle answer is held back, so the typing lands while it is out.
  await page.route('**/api/notes/*/items/*/toggle', async route => { await new Promise(done => setTimeout(done, 400)); await route.continue(); });
  const toggled = page.waitForResponse(response => /\/items\/\d+\/toggle/.test(response.url()));
  await body.locator('.md-task', { hasText: 'one' }).locator('.md-task-box').click();
  await body.locator('p', { hasText: 'tail text' }).click(); await page.keyboard.press('End'); await page.keyboard.type(' KEEP');
  await toggled;
  await expect(body).toContainText('tail text KEEP');
  await expect.poll(async () => (await read(owner, id)).content).toBe('- [x] one\n- [ ] two\n\ntail text KEEP');
  await expect(body).toContainText('tail text KEEP');
  await context.close();
});

test('MAJOR text typed while a window tick is refused (task changed elsewhere) is kept, and asks before overwriting', async ({ browser }) => {
  const id = await makeNote(owner, 'QA type during refused tick', '- [ ] one\n- [ ] two\n\ntail text');
  const { context, page, body } = await openNote(browser, owner, id);
  // While the tick is out, another device renames the task, so the server refuses the tick with 409.
  await page.route('**/api/notes/*/items/*/toggle', async route => {
    await new Promise(done => setTimeout(done, 400));
    const current = await read(owner, id);
    await owner.put(`/api/notes/${id}`, { data: { content: current.content.replace('- [ ] one', '- [ ] one renamed'), base_updated_at: current.updated_at } });
    await route.continue();
  });
  const toggled = page.waitForResponse(response => /\/items\/\d+\/toggle/.test(response.url()));
  await body.locator('.md-task', { hasText: 'one' }).locator('.md-task-box').click();
  await body.locator('p', { hasText: 'tail text' }).click(); await page.keyboard.press('End'); await page.keyboard.type(' KEEP');
  expect((await toggled).status()).toBe(409);
  // The typing stays, the refused tick is taken back, and the save offers Keep mine / Reload.
  await expect(page.locator('#note-window-modal .note-window-banner')).toContainText('This note changed elsewhere', { timeout: 10_000 });
  await expect(body).toContainText('tail text KEEP');
  await expect(body.locator('.md-task', { hasText: 'one' })).not.toHaveAttribute('data-done', 'true');
  await page.waitForTimeout(1500);
  expect((await read(owner, id)).content).toBe('- [ ] one renamed\n- [ ] two\n\ntail text');
  await context.close();
});

// a collaborator appends a paragraph, and before the window's
// next refresh the owner ticks a task and types. Whatever the order and the
// link speed, bob's paragraph is never written over unseen: either it ends up
// saved together with the tick and the typing, or the save asks Keep mine /
// Reload and the server still has it.
for (const [order, delay] of [['tick then type', 600], ['type then tick', 600], ['tick then type', 0], ['type then tick', 0]]) {
  test(`MAJOR (data loss) ${order}${delay ? ' on a slow link' : ''} keeps a collaborator's paragraph the window has not received`, async ({ browser }) => {
    const id = await makeNote(owner, `QA tick race, ${order}, ${delay} ms`, '- [ ] one\n- [ ] two\n\ntail text');
    await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
    const { context, page, body } = await openNote(browser, owner, id);
    await expect(body.locator('p', { hasText: 'tail text' })).toBeVisible();
    const current = await read(collab, id);
    expect((await collab.put(`/api/notes/${id}`, { data: { content: `${current.content}\n\nfrom bob`, base_updated_at: current.updated_at } })).ok()).toBeTruthy();
    // Only the toggle answer is held back, as on a slow link.
    if (delay) await page.route('**/api/notes/*/items/*/toggle', async route => { await new Promise(done => setTimeout(done, delay)); await route.continue(); });
    const tick = () => body.locator('.md-task', { hasText: 'one' }).locator('.md-task-box').click();
    const type = async () => { await body.locator('p', { hasText: 'tail text' }).click(); await page.keyboard.press('End'); await page.keyboard.type(' KEEP'); };
    if (order === 'tick then type') { await tick(); await type(); } else { await type(); await tick(); }
    const banner = page.locator('#note-window-modal .note-window-banner');
    // Settled: the conflict choice is up, or the typing reached the server.
    await expect.poll(async () => (await banner.isVisible()) || (await read(owner, id)).content.includes('KEEP'), { timeout: 15_000 }).toBe(true);
    await page.waitForTimeout(1500);
    const stored = (await read(owner, id)).content;
    expect(stored, 'the collaborator\'s paragraph is still on the server').toContain('from bob');
    if (await banner.isVisible()) {
      await expect(banner).toContainText('This note changed elsewhere');
      await expect(body).toContainText('tail text KEEP');
    } else {
      expect(stored).toContain('- [x] one');
      expect(stored).toContain('tail text KEEP');
    }
    await context.close();
  });
}

test('MAJOR a collaborator\'s change reaches an idle open note that has the caret in it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA idle window refresh', 'first line\n\nsecond line');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  // The caret in the body, nothing typed: the usual state of an open note.
  await body.locator('p', { hasText: 'second line' }).click();
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: 'first line\n\nsecond line from bob', base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  // One 20 s refresh tick, plus slack.
  await expect(body).toContainText('second line from bob', { timeout: 30_000 });
  await expect(page.locator(`#notes-pane .note-card[data-note-id="${id}"]`)).toContainText('second line from bob');
  // The window is on bob's version: typing now saves on top of it, no conflict.
  await body.locator('p', { hasText: 'second line from bob' }).click();
  await page.keyboard.press('End'); await page.keyboard.type(' and alice');
  await expect.poll(async () => (await read(owner, id)).content).toContain('second line from bob and alice');
  await expect(page.locator('#note-window-modal')).not.toContainText('changed elsewhere');
  await context.close();
});

test('MAJOR a collaborator\'s change leaves the caret where it was in an idle open note', async ({ browser }) => {
  const id = await makeNote(owner, 'QA caret after remote refresh', 'first para with a long sentence here\n\nsecond para');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  const caret = () => page.evaluate(() => {
    const sel = getSelection();
    if (!sel.rangeCount) return null;
    const block = (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement).closest('p');
    if (!block) return { block: null, node: sel.anchorNode.nodeName, offset: sel.anchorOffset, collapsed: sel.isCollapsed };
    const range = document.createRange();
    range.selectNodeContents(block);
    range.setEnd(sel.anchorNode, sel.anchorOffset);
    return { block: block.textContent, offset: range.toString().length, collapsed: sel.isCollapsed };
  });
  // The caret in the middle of the second paragraph, nothing typed.
  await body.locator('p', { hasText: 'second para' }).click();
  await page.keyboard.press('End');
  for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowLeft');
  expect(await caret()).toEqual({ block: 'second para', offset: 6, collapsed: true });
  // Bob adds a paragraph above it and one below.
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: `zero para from bob\n\n${current.content}\n\nthird para from bob`, base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  await expect(body).toContainText('third para from bob', { timeout: 30_000 });
  expect(await body.evaluate(el => el.contains(document.activeElement))).toBeTruthy();
  // Same paragraph, same place in it, and the next key lands there.
  expect(await caret()).toEqual({ block: 'second para', offset: 6, collapsed: true });
  await page.keyboard.type('X');
  await expect.poll(async () => (await read(owner, id)).content)
    .toBe('zero para from bob\n\nfirst para with a long sentence here\n\nsecondX para\n\nthird para from bob');
  await context.close();
});

test('MAJOR when a collaborator shortens the paragraph with the caret in it, the caret goes to its end', async ({ browser }) => {
  const id = await makeNote(owner, 'QA caret after remote shortening', 'first para\n\nsecond para is long');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('p', { hasText: 'second para is long' }).click();
  await page.keyboard.press('End');
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: 'first para\n\nsecond', base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  await expect(body.locator('p').nth(1)).toHaveText('second', { timeout: 30_000 });
  await page.keyboard.type('X');
  await expect.poll(async () => (await read(owner, id)).content).toBe('first para\n\nsecondX');
  await context.close();
});

// a whole list or table is one block, so a collaborator's
// item or row above the caret moved it into another item.
const caretInUnit = page => page.evaluate(() => {
  const sel = getSelection();
  if (!sel.rangeCount) return null;
  const element = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
  const unit = element.closest('.md-task-text, td, th, li');
  if (!unit) return { unit: null, node: sel.anchorNode.nodeName, offset: sel.anchorOffset };
  const range = document.createRange();
  range.selectNodeContents(unit);
  range.setEnd(sel.anchorNode, sel.anchorOffset);
  return { unit: unit.textContent, offset: range.toString().length, collapsed: sel.isCollapsed };
});

test('MAJOR a collaborator\'s task added above keeps the caret in its list item', async ({ browser }) => {
  const id = await makeNote(owner, 'QA caret in list after remote task', '- [ ] milk\n- [ ] bread');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('li', { hasText: 'bread' }).locator('.md-task-text').click();
  await page.keyboard.press('End');
  expect(await caretInUnit(page)).toEqual({ unit: 'bread', offset: 5, collapsed: true });
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: `- [ ] NEW FROM BOB\n${current.content}`, base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  await expect(body).toContainText('NEW FROM BOB', { timeout: 30_000 });
  expect(await caretInUnit(page)).toEqual({ unit: 'bread', offset: 5, collapsed: true });
  await page.keyboard.type('X');
  await expect.poll(async () => (await read(owner, id)).content).toBe('- [ ] NEW FROM BOB\n- [ ] milk\n- [ ] breadX');
  await context.close();
});

test('MAJOR a collaborator\'s table row added above keeps the caret in its cell', async ({ browser }) => {
  const id = await makeNote(owner, 'QA caret in table after remote row', '| A | B |\n|---|---|\n| 1 | two |\n| 3 | four |');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('td', { hasText: 'four' }).click();
  await page.keyboard.press('End');
  expect(await caretInUnit(page)).toEqual({ unit: 'four', offset: 4, collapsed: true });
  const current = await read(collab, id);
  const put = await collab.put(`/api/notes/${id}`, { data: { content: current.content.replace('| 1 | two |', '| 0 | zero |\n| 1 | two |'), base_updated_at: current.updated_at } });
  expect(put.ok()).toBeTruthy();
  await expect(body).toContainText('zero', { timeout: 30_000 });
  expect(await caretInUnit(page)).toEqual({ unit: 'four', offset: 4, collapsed: true });
  await page.keyboard.type('X');
  await expect.poll(async () => (await read(owner, id)).content).toContain('fourX');
  const content = (await read(owner, id)).content;
  expect(content.split('X').length - 1, content).toBe(1);
  expect(content).toContain('| 0 | zero |');
  await context.close();
});

test('MAJOR (security) an edit collaborator cannot null the owner-only fields through /api/codex/todos', async () => {
  const id = await makeNote(owner, 'QA collaborator null fields', 'x');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  await collab.post('/api/codex/todos', { data: { action: 'update', id, content: 'x', archived: null, pinned: null } });
  const listed = (await (await owner.get('/api/notes')).json()).notes.some(note => note.id === id);
  expect(listed, "the note is still in the owner's list").toBeTruthy();
  const note = await read(owner, id);
  expect([note.archived, note.pinned]).toEqual([false, false]);
});

test('MAJOR the owner can get back text a collaborator overwrote a minute later', async () => {
  const id = await makeNote(owner, 'QA history authors', 'v0 original');
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'edit' } });
  let current = await read(owner, id);
  await owner.put(`/api/notes/${id}`, { data: { content: 'v1 OWNER wrote this paragraph', base_updated_at: current.updated_at } });
  current = await read(collab, id);
  await collab.put(`/api/notes/${id}`, { data: { content: 'v2 collaborator replaced everything', base_updated_at: current.updated_at } });
  const listing = await (await owner.get(`/api/notes/${id}/versions`)).json();
  const rows = Array.isArray(listing) ? listing : listing.versions;
  const texts = await Promise.all(rows.map(async row => (await (await owner.get(`/api/notes/${id}/versions/${row.id}`)).json()).content));
  expect(texts.some(text => (text || '').includes('OWNER wrote'))).toBeTruthy();
});

test('MAJOR adding a to-do to a note with an unclosed code fence works', async () => {
  const id = await makeNote(owner, 'QA unclosed fence', 'notes\n```\ncode');
  const response = await owner.post(`/api/notes/${id}/items`, { data: { text: 'buy milk' } });
  expect(response.status()).toBeLessThan(500);
  expect((await read(owner, id)).items?.length || 0).toBe(1);
});

test('MINOR a reopened note still offers Undo for its last agent edit', async ({ browser }) => {
  // Needs the stub model as the owner's endpoint (run_notes_qa.py).
  const before = 'Keep  these bytes\n\n- [ ] EDITTEST reopen';
  const id = await makeNote(owner, 'QA undo after reopen', before);
  const { context, page, body } = await openNote(browser, owner, id);
  const row = body.locator('li.md-task', { hasText: 'EDITTEST' });
  await row.hover();
  await row.locator('.note-task-agent').click();
  await page.locator('.note-ask-menu [data-act="solve"]').click();
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 30_000 }).toMatch(/^Edited by the agent: reopen/);
  await expect(body).toContainText('Edited by the agent', { timeout: 15_000 });
  await page.locator('#note-window-modal .modal-close').click();
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  // A fresh page: the toast is gone, the server still says the edit can be undone.
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  expect((await read(owner, id)).agent_edit?.undoable).toBe(true);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).click();
  const meta = page.locator('#note-window-modal .note-window-meta');
  await expect(meta).toContainText(/Agent edited .* · Undo/);
  await meta.locator('.note-window-meta-undo:not(.note-window-meta-history)').click();
  await expect.poll(async () => (await read(owner, id)).content).toBe(before);
  await expect(body).toContainText('Keep');
  await expect(meta).not.toContainText('Agent edited');
  await context.close();
});

test('MINOR typing at the end of a list leaves the lines it did not touch as they were', async ({ browser }) => {
  const content = '1. Do X\n2. Do Y\n   - sub';
  const id = await makeNote(owner, 'QA untouched lines', content);
  const { context, page, body } = await openNote(browser, owner, id);
  await body.getByText('sub').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type('ZQ');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('ZQ');
  expect((await read(owner, id)).content).toBe(`${content}ZQ`);
  await context.close();
});

test('MINOR a paragraph typed under a checklist is saved as a block of its own', async ({ browser }) => {
  // Written in the editor, so any markdown renderer reads two blocks: a blank
  // line between the list and the text, not a single newline.
  const id = await makeNote(owner, 'QA typed blocks', 'Start');
  const { context, page, body } = await openNote(browser, owner, id);
  await body.getByText('Start').first().click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await page.locator('#note-window-modal [data-md="check"]').click();
  await page.keyboard.type('task one');
  await page.keyboard.press('Enter');
  await page.keyboard.type('task two');
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await page.keyboard.type('after the list');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('after the list');
  expect(JSON.stringify((await read(owner, id)).content)).toBe(JSON.stringify('Start\n\n- [ ] task one\n- [ ] task two\n\nafter the list'));
  await context.close();
});

// The quick bar has one way in: a note. A to-do list is that note with the
// toolbar's Checklist. The bug was a quick-bar to-do whose session
// held `- [ ]` while the server held '': a colour, tag or reminder picked next
// left the base behind and the first task met "changed elsewhere". The same
// sequence through the Checklist button must not.
const META_STEPS = {
  colour: async (page, win) => { await win.locator('.note-color-dot[data-color="red"]').click(); },
  tag: async (page, win) => { await win.locator('.note-window-tag-input').click(); await page.keyboard.type('#home'); await page.keyboard.press('Enter'); },
  reminder: async (page, win) => { await win.locator('.note-window-remind').click(); await page.locator('.note-reminder-picker [data-action="preset"][data-i="1"]').click(); },
};
for (const [what, pick] of Object.entries(META_STEPS)) {
  test(`MAJOR a quick-bar note made a checklist takes a ${what} and then its first task without "changed elsewhere"`, async ({ browser }) => {
    const context = await browser.newContext({ storageState: await owner.storageState() });
    await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
    const page = await context.newPage();
    await page.goto(ORIGIN);
    await page.waitForFunction(() => window.notesModule?.openPanel);
    await page.evaluate(() => window.notesModule.openPanel());
    await expect(page.locator('#notes-pane .notes-quick-input')).toHaveAttribute('placeholder', 'Add a note…');
    await expect(page.locator('#notes-pane .notes-quick-add button')).toHaveCount(1);
    const title = `QA quick checklist ${what} ${Date.now()}`;
    await page.locator('#notes-pane .notes-quick-input').focus();
    const win = page.locator('#note-window-modal');
    const posted = page.waitForResponse(response => response.url().endsWith('/api/notes') && response.request().method() === 'POST');
    await page.keyboard.type(title);
    await expect(win.locator('.note-window-title')).toHaveValue(title);
    await page.keyboard.press('Enter');
    await win.locator('.note-window-toolbar [data-md="check"]').click();
    const note = await (await posted).json();
    created.push(note.id);
    await expect.poll(async () => (await read(owner, note.id)).content, { timeout: 5000 }).toBe('- [ ]');
    const metaWrite = page.waitForResponse(response => response.url().endsWith(`/api/notes/${note.id}`) && response.request().method() === 'PUT');
    await pick(page, win);
    expect((await metaWrite).status()).toBe(200);
    await win.locator('.note-window-body li').first().click();
    await page.keyboard.type('milk');
    await expect.poll(async () => (await read(owner, note.id)).content, { timeout: 5000 }).toBe('- [ ] milk');
    await expect(win.locator('.note-window-banner').filter({ hasText: 'changed elsewhere' })).toHaveCount(0);
    const stored = await read(owner, note.id);
    expect(stored.title).toBe(title);
    await context.close();
  });
}

test('MINOR a one-line code block on a grid card is not one character per line', async ({ browser }) => {
  const id = await makeNote(owner, 'QA snippet card', 'Run this:\n\n```bash\ndocker compose up -d --build\n```');
  const context = await browser.newContext({ storageState: await owner.storageState() });
  await context.addInitScript(() => { localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'); localStorage.setItem('odysseus-notes-view', 'grid'); });
  const page = await context.newPage();
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"]`);
  const pre = card.locator('pre');
  await pre.scrollIntoViewIfNeeded();
  await card.hover();
  expect((await pre.boundingBox()).height).toBeLessThan(120);
  // Chat marks a one-line <pre> as .pre-compact and reserves 200px on the
  // right for its Run/Edit/Copy row. Cards show none of those buttons, so the
  // text must get the whole width of the block.
  const room = await pre.evaluate(el => el.clientWidth - parseFloat(getComputedStyle(el).paddingLeft) - parseFloat(getComputedStyle(el).paddingRight));
  expect(room).toBeGreaterThan((await pre.boundingBox()).width * 0.8);
  await expect(card.getByTitle('Run code')).toBeHidden();
  await expect(card.locator('pre :is(.run-code, .edit-code, .copy-code)')).toHaveCount(3);
  for (const button of await card.locator('pre button').all()) await expect(button).toBeHidden();
  await context.close();
});

test('MINOR a 3 MB body or a 50,000-character title is refused with 413', async () => {
  // Both were stored (200).
  const body = await owner.post('/api/notes', { data: { title: 'QA huge body', content: 'x'.repeat(3_000_000) } });
  expect(body.status()).toBe(413);
  expect((await body.json()).detail).toMatchObject({ field: 'content', limit: 1_000_000 });
  const title = await owner.post('/api/notes', { data: { title: 't'.repeat(50_000), content: 'ok' } });
  expect(title.status()).toBe(413);
  expect((await title.json()).detail).toMatchObject({ field: 'title', limit: 1_000 });
});

test('MINOR a note too long to save says why, keeps the text, and saves once it is shortened', async ({ browser }) => {
  // On the client: the 413 must not read as "offline".
  const id = await makeNote(owner, 'QA long title', 'body');
  const { context, page } = await openNote(browser, owner, id);
  const title = page.locator('#note-window-modal .note-window-title');
  const banner = page.locator('#note-window-modal .note-window-banner');
  await title.fill('T'.repeat(1_001));
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await expect(banner).toContainText('too long to save');
  await expect(banner).toContainText('1,000');
  await expect(page.locator('#toast')).not.toContainText('Note not synced');
  expect((await read(owner, id)).title).toBe('QA long title');
  expect(await title.inputValue()).toBe('T'.repeat(1_001));
  await title.fill('QA long title, shortened');
  await expect.poll(async () => (await read(owner, id)).title, { timeout: 10_000 }).toBe('QA long title, shortened');
  await expect(banner).toBeHidden();
  await context.close();
});

test('MINOR an edit typed offline is saved when the connection comes back, and says so', async ({ browser }) => {
  // QA "Offline, then back online", W5. The window stays open and nothing is typed after reconnecting.
  const id = await makeNote(owner, 'QA offline retry', 'start');
  const { context, page, body } = await openNote(browser, owner, id);
  const toast = page.locator('#toast');
  await context.setOffline(true);
  await body.locator('p', { hasText: 'start' }).click();
  await page.keyboard.press('End');
  await page.keyboard.type(' offline-edit');
  await expect(toast).toContainText('Note not synced', { timeout: 10_000 });
  await context.setOffline(false);
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 10_000 }).toBe('start offline-edit');
  await expect(toast).toContainText('Note synced');
  await expect(toast).not.toHaveClass(/\berror\b/);
  await expect(page.locator(`#notes-pane .note-card[data-note-id="${id}"]`)).toContainText('offline-edit');
  await context.close();
});

// QA "Phone profile" (13-phone-window-252px.png), W2/W3. iPhone 15, the device the QA pass used.
async function phonePage(browser, api, setup = null) {
  const context = await browser.newContext({ ...devices['iPhone 15'], storageState: await api.storageState() });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  if (setup) await setup(context);
  const page = await context.newPage();
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  return { context, page };
}
// The window slides up as a sheet: measure once it has stopped moving.
async function settledWindow(page) {
  let last = null;
  await expect.poll(async () => {
    const top = await page.locator('#note-window-modal .note-window-content').evaluate(e => Math.round(e.getBoundingClientRect().top));
    const still = top === last; last = top; return still;
  }, { intervals: [150] }).toBe(true);
}

test('MINOR on a phone the note window keeps the screen for the text while typing', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone room', Array.from({ length: 40 }, (_, i) => `Line ${i}`).join('\n\n'));
  const { context, page } = await phonePage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  const win = page.locator('#note-window-modal');
  const toolbar = win.locator('.note-window-toolbar');
  const footer = win.locator('.note-window-footer');
  const room = () => win.locator('.note-window-scroll').evaluate(e => e.getBoundingClientRect().height);
  await settledWindow(page);
  // The toolbar is one row of buttons, not three.
  const bold = await toolbar.locator('[data-md="bold"]').boundingBox();
  expect((await toolbar.boundingBox()).height).toBeLessThan(bold.height * 2);
  await expect(footer).toBeVisible();
  // Typing: the footer (colours, tags, actions) gets out of the way.
  await win.locator('.note-window-body p', { hasText: 'Line 0' }).tap();
  await page.keyboard.type('x');
  await expect(footer).toBeHidden();
  const viewport = page.viewportSize().height;
  expect(await room()).toBeGreaterThanOrEqual(viewport * 0.6);
  // Done typing: the footer comes back.
  await win.locator('.note-window-title').tap();
  await expect(footer).toBeVisible();
  // Every formatting action can still be reached: swiped into view, a tap lands on it.
  const missed = await toolbar.evaluate(bar => Array.from(bar.querySelectorAll('[data-md]')).filter(b => {
    b.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const r = b.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !(at === b || b.contains(at));
  }).map(b => b.dataset.md));
  expect(missed).toEqual([]);
  // The tap leaves the caret at the end of the short line.
  await expect.poll(async () => (await read(owner, id)).content).toMatch(/^Line 0x\n/);
  await context.close();
});

// the footer steps aside while the text has focus, but Tab from the
// text still lands in it, and it shows again for as long as it has focus.
test('MINOR on a phone Tab from the text reaches the note window footer', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone footer keys', 'Line 0\n\nLine 1');
  const { context, page } = await phonePage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  const win = page.locator('#note-window-modal');
  const footer = win.locator('.note-window-footer');
  await settledWindow(page);
  await win.locator('.note-window-body p', { hasText: 'Line 1' }).tap();
  await page.keyboard.type('x');
  await expect(footer).toBeHidden();
  // Tab walks out of the text (past the meta line's History link, if any) into
  // the footer, in order, and the footer is on screen while it holds the focus.
  const reached = [];
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab');
    const at = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el?.closest('#note-window-modal')) return { out: true };
      if (!el.closest('.note-window-footer')) return { before: true };
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { name: el.dataset.act || (el.dataset.color !== undefined ? 'color' : el.tagName.toLowerCase()), onScreen: r.height > 0 && (hit === el || el.contains(hit)) };
    });
    if (at.out || (at.before && reached.length)) break;
    if (at.before) continue;
    expect(at.onScreen, `${at.name} is on screen while it has focus`).toBe(true);
    reached.push(at.name);
  }
  expect(reached[0]).toBe('color');
  expect(reached).toContain('input');
  expect(reached).toEqual(expect.arrayContaining(['history', 'archive', 'delete']));
  await expect(footer).toBeVisible();
  // Shift+Tab goes back the same way (past the meta line, if it has a link),
  // into the text, and the footer steps aside again.
  await win.locator('.note-color-dot').first().focus();
  const body = win.locator('.note-window-body');
  for (let i = 0; i < 3 && !(await body.evaluate(e => e === document.activeElement)); i++) {
    await page.keyboard.press('Shift+Tab');
    expect(await page.evaluate(() => !!document.activeElement?.closest('#note-window-modal .note-window-scroll'))).toBe(true);
  }
  await expect(body).toBeFocused();
  await expect(footer).toBeHidden();
  await expect.poll(async () => (await read(owner, id)).content).toMatch(/Line 1x/);
  await context.close();
});

// on an iPhone the keyboard does not resize the page. iOS shrinks the
// visible area (window.visualViewport) and pans it towards the caret, so a
// sheet sized to the whole screen loses its title bar above the visible area.
// Chromium has no on-screen keyboard: keyboard(page, px, pan) plays iOS's part
// by shrinking a stand-in visualViewport by px and panning it by pan.
const KEYBOARD = 336;
async function proMaxPage(browser, api) {
  const context = await browser.newContext({ ...devices['iPhone 15 Pro Max'], storageState: await api.storageState() });
  await context.addInitScript(() => {
    localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
    const real = window.visualViewport;
    const fake = new EventTarget();
    const shown = { keyboard: 0, pan: 0 };
    for (const key of ['width', 'offsetLeft', 'pageLeft', 'scale']) Object.defineProperty(fake, key, { get: () => real[key] });
    Object.defineProperty(fake, 'height', { get: () => real.height - shown.keyboard });
    Object.defineProperty(fake, 'offsetTop', { get: () => shown.pan });
    Object.defineProperty(fake, 'pageTop', { get: () => real.pageTop + shown.pan });
    Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => fake });
    window.__keyboard = (keyboard, pan) => {
      Object.assign(shown, { keyboard, pan });
      fake.dispatchEvent(new Event('resize'));
      fake.dispatchEvent(new Event('scroll'));
    };
  });
  const page = await context.newPage();
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  return { context, page };
}
const keyboard = (page, px, pan = 0) => page.evaluate(([px, pan]) => window.__keyboard(px, pan), [px, pan]);
// What of the window is inside the visible area: the title bar, the text's
// scroll box, and the caret, all relative to the visible top.
const inView = page => page.evaluate(() => {
  const vv = window.visualViewport;
  const win = document.querySelector('#note-window-modal');
  const box = el => { const r = el.getBoundingClientRect(); return { top: r.top - vv.offsetTop, bottom: r.bottom - vv.offsetTop }; };
  const title = win.querySelector('.note-window-title');
  const t = title.getBoundingClientRect();
  const hit = document.elementFromPoint(t.left + t.width / 2, t.top + t.height / 2);
  const sel = getSelection();
  const field = document.activeElement;
  let caret = null;
  if (field?.matches?.('#note-window-modal input, #note-window-modal textarea')) caret = box(field);
  else if (sel.rangeCount && win.contains(sel.focusNode)) {
    const range = sel.getRangeAt(0).cloneRange();
    range.collapse(false);
    // A collapsed range at a text node's end can measure as all zeros: use its line then.
    const rect = range.getClientRects()[0];
    const line = (sel.focusNode.nodeType === 1 ? sel.focusNode : sel.focusNode.parentElement).getBoundingClientRect();
    const at = rect && (rect.top || rect.bottom) ? rect : line;
    caret = { top: at.top - vv.offsetTop, bottom: at.bottom - vv.offsetTop, inTitle: false };
  }
  if (caret && field === title) caret.inTitle = true;
  return { height: vv.height, header: box(win.querySelector('.note-window-header')), scroll: box(win.querySelector('.note-window-scroll')), caret, titleHit: hit === title };
});
function expectInView(at, what) {
  expect(at.header.top, `${what}: the title bar's top is on screen`).toBeGreaterThanOrEqual(-1);
  expect(at.header.bottom, `${what}: the title bar is above the keyboard`).toBeLessThanOrEqual(at.height + 1);
  expect(at.titleHit, `${what}: a tap on the title lands on it`).toBe(true);
  expect(at.scroll.bottom, `${what}: the text ends at the keyboard`).toBeLessThanOrEqual(at.height + 1);
  expect(at.caret, `${what}: the caret is in the window`).not.toBeNull();
  if (at.caret.inTitle) return;
  expect(at.caret.top, `${what}: the caret is below the title bar`).toBeGreaterThanOrEqual(at.scroll.top - 1);
  expect(at.caret.bottom, `${what}: the caret is above the keyboard`).toBeLessThanOrEqual(Math.min(at.scroll.bottom, at.height) + 1);
}
const headerTop = page => page.locator('#note-window-modal .note-window-header').evaluate(e => Math.round(e.getBoundingClientRect().top));

test('MINOR on a phone the keyboard keeps a new note\'s title bar on screen', async ({ browser }) => {
  const { context, page } = await proMaxPage(browser, owner);
  const win = page.locator('#note-window-modal');
  const posted = page.waitForResponse(response => response.url().endsWith('/api/notes') && response.request().method() === 'POST');
  // A tap on the empty quick bar opens a new note with the caret in its body.
  await page.locator('#notes-pane .notes-quick-input').tap();
  await expect(win.locator('.note-window-body')).toBeFocused();
  await settledWindow(page);
  const top = await headerTop(page);
  // The keyboard comes up.
  await keyboard(page, KEYBOARD);
  await expect.poll(async () => (await inView(page)).scroll.bottom).toBeLessThanOrEqual(await page.evaluate(() => visualViewport.height) + 1);
  expectInView(await inView(page), 'new note, keyboard up');
  // A dozen lines later, with iOS panning the visible area towards the caret,
  // the caret is still between the title bar and the keyboard.
  for (let i = 0; i < 12; i++) { await page.keyboard.type(`line ${i}`); await page.keyboard.press('Enter'); }
  await page.keyboard.type('last');
  await keyboard(page, KEYBOARD, 180);
  await expect.poll(async () => (await inView(page)).header.top).toBeGreaterThanOrEqual(-1);
  await expect.poll(async () => { const at = await inView(page); return at.caret && at.caret.bottom <= Math.min(at.scroll.bottom, at.height) + 1; }).toBe(true);
  expectInView(await inView(page), 'new note, after typing, page panned');
  // The title is still there to tap and type into.
  await win.locator('.note-window-title').tap();
  await page.keyboard.type('QA keyboard new');
  await expect(win.locator('.note-window-title')).toHaveValue('QA keyboard new');
  expectInView(await inView(page), 'title, page panned');
  // Keyboard down: the sheet is back where it was, no jump.
  await keyboard(page, 0, 0);
  await expect.poll(() => headerTop(page)).toBe(top);
  created.push((await (await posted).json()).id);
  await context.close();
});

test('MINOR on a phone the keyboard keeps an open note\'s title bar and caret on screen', async ({ browser }) => {
  const id = await makeNote(owner, 'QA keyboard existing', Array.from({ length: 40 }, (_, i) => `Line ${i}`).join('\n\n'));
  const { context, page } = await proMaxPage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  const win = page.locator('#note-window-modal');
  await settledWindow(page);
  const top = await headerTop(page);
  // A line low on the screen, where the keyboard is about to come up.
  const low = await win.locator('.note-window-body p').evaluateAll(ps => ps.findIndex(p => p.getBoundingClientRect().top > innerHeight - 260));
  expect(low).toBeGreaterThan(0);
  await win.locator('.note-window-body p').nth(low).tap();
  await keyboard(page, KEYBOARD, 220);
  await expect.poll(async () => (await inView(page)).header.top).toBeGreaterThanOrEqual(-1);
  await expect.poll(async () => { const at = await inView(page); return at.caret && at.caret.bottom <= Math.min(at.scroll.bottom, at.height) + 1; }).toBe(true);
  expectInView(await inView(page), 'tapped low line, page panned');
  await page.keyboard.type('x');
  await expect.poll(async () => (await read(owner, id)).content).toContain(`Line ${low}x`);
  expectInView(await inView(page), 'after typing');
  await keyboard(page, 0, 0);
  await expect.poll(() => headerTop(page)).toBe(top);
  await context.close();
});

test('MINOR on a phone a note archived from its window can be found again', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone archive', 'Archive me from a phone');
  const { context, page } = await phonePage(browser, owner);
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"]`);
  await card.locator('.note-card-title').tap();
  await settledWindow(page);
  await page.locator('#note-window-modal [data-act="archive"]').tap();
  await expect.poll(async () => (await read(owner, id)).archived).toBe(true);
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  await expect(card).toHaveCount(0);
  const toggle = page.locator('#notes-archive-toggle');
  await expect(toggle).toBeVisible();
  await toggle.tap();
  await expect(card).toBeVisible();
  // And back out of the archive the same way.
  await toggle.tap();
  await expect(card).toHaveCount(0);
  await context.close();
});

// the agent button on a task row asks first, as the note's own
// Ask does, and the keyboard reaches it.
const agentToast = page => page.locator('#toast').filter({ hasText: 'Agent working in the background' });
async function noRunStarted(page, id) {
  await page.waitForTimeout(1500);
  expect((await read(owner, id)).agent_session_id ?? null).toBeNull();
  await expect(agentToast(page)).toBeHidden();
}

test('MINOR the agent button on a task asks before it starts a run, and the keyboard reaches it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA item agent asks', 'Trip\n\n- [ ] ITEMASK book the train');
  const { context, page, body } = await openNote(browser, owner, id);
  const row = body.locator('li.md-task', { hasText: 'ITEMASK' });
  const button = row.locator('.note-task-agent');
  const menu = page.locator('.note-ask-menu');
  // One click opens the question about this item; nothing runs yet.
  await row.hover();
  await button.click();
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute('aria-label', 'Ask about this item');
  await expect(menu).toContainText('ITEMASK book the train');
  await noRunStarted(page, id);
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await noRunStarted(page, id);
  // Keyboard only: Tab from the title reaches the button, Enter opens the same question.
  await page.locator('#note-window-modal .note-window-title').focus();
  let reached = false;
  for (let i = 0; i < 20 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await button.evaluate(el => el === document.activeElement);
  }
  expect(reached, 'Tab reaches the agent button').toBe(true);
  await expect(button).toHaveAttribute('aria-label', /.+/);
  expect(await button.evaluate(el => getComputedStyle(el).opacity)).not.toBe('0');
  await page.keyboard.press('Enter');
  await expect(menu).toBeVisible();
  await noRunStarted(page, id);
  // Enter opened the question and did nothing to the row.
  expect((await read(owner, id)).content).toBe('Trip\n\n- [ ] ITEMASK book the train');
  await expect(body.locator('li.md-task')).toHaveCount(1);
  // The run starts from the menu, about this item.
  await menu.getByRole('button', { name: /Agent: help with this/ }).click();
  await expect(agentToast(page)).toBeVisible();
  await expect.poll(async () => (await read(owner, id)).agent_session_id ?? null, { timeout: 15_000 }).not.toBeNull();
  await context.close();
});

test('MINOR on a phone a tap on a task\'s agent button does not start a run', async ({ browser }) => {
  const id = await makeNote(owner, 'QA phone item agent', 'Trip\n\n- [ ] PHONEASK pack the bags');
  const { context, page } = await phonePage(browser, owner);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  await settledWindow(page);
  const row = page.locator('#note-window-modal .note-window-body li.md-task', { hasText: 'PHONEASK' });
  await row.locator('.note-task-agent').tap();
  const menu = page.locator('.note-ask-menu');
  await expect(menu).toBeVisible();
  await expect(menu).toContainText('PHONEASK pack the bags');
  await noRunStarted(page, id);
  await context.close();
});

// Links in a note open with a plain click or a tap, in a new tab, as in Apple Notes.
const LINK_NOTE = [
  'Read LINKPARA [the docs](https://example.com/qa-link-para) today',
  '',
  '- LINKLIST [the list link](https://example.com/qa-link-list)',
  '',
  '> LINKQUOTE [the quoted link](https://example.com/qa-link-quote)',
  '',
  'Bad [script link](javascript:alert%281%29) here',
].join('\n');
// The new tab never reaches the network: it gets a stub page.
async function stubLinkTargets(context) {
  await context.route('https://example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<title>stub</title>' }));
}
// Resolves to the URL of the tab `act` opens, or null when none opens.
async function openedBy(context, act, wait = 1500) {
  const opened = context.waitForEvent('page', { timeout: wait }).then(page => page.url() === 'about:blank' ? page.waitForURL(/example/).then(() => page.url()) : page.url(), () => null);
  await act();
  return opened;
}

test('MINOR a plain click opens a link in a note, in a paragraph, a list and a quote, with a pointer cursor', async ({ browser }) => {
  const id = await makeNote(owner, 'QA link click', LINK_NOTE);
  const { context, page, body } = await openNote(browser, owner, id);
  await stubLinkTargets(context);
  for (const [name, path] of [['the docs', 'qa-link-para'], ['the list link', 'qa-link-list'], ['the quoted link', 'qa-link-quote']]) {
    const link = body.locator('a', { hasText: name });
    expect(await link.evaluate(a => getComputedStyle(a).cursor), name).toBe('pointer');
    expect(await openedBy(context, () => link.click()), name).toBe(`https://example.com/${path}`);
  }
  expect(page.url()).toContain(ORIGIN);
  // The note's card (read only) opens its links too, without opening the note.
  await page.locator('#note-window-modal .modal-close').click();
  const cardLink = page.locator(`#notes-pane .note-card[data-note-id="${id}"] a`, { hasText: 'the docs' });
  expect(await openedBy(context, () => cardLink.click())).toBe('https://example.com/qa-link-para');
  await context.close();
});

test('MINOR a drag across a link or a click beside it does not open it, and Ctrl+K edits it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA link drag', LINK_NOTE);
  const { context, page, body } = await openNote(browser, owner, id);
  await stubLinkTargets(context);
  const link = body.locator('a', { hasText: 'the docs' });
  const box = await link.boundingBox();
  const y = box.y + box.height / 2;
  const before = await body.innerText();
  // Drag inside the link, then from the link out into the paragraph.
  for (const [from, to] of [[box.x + 2, box.x + box.width - 2], [box.x + box.width / 2, box.x + box.width + 40]]) {
    // A press inside a selection drags the selection: start each drag from a caret.
    await page.evaluate(() => getSelection().collapseToEnd());
    expect(await openedBy(context, async () => {
      await page.mouse.move(from, y);
      await page.mouse.down();
      await page.mouse.move(to, y, { steps: 8 });
      await page.mouse.up();
    })).toBeNull();
    expect(await page.evaluate(() => String(window.getSelection())), `drag ${from}->${to}`).not.toBe('');
  }
  // A drag that starts on a link selects text: it does not move the link's text elsewhere.
  expect(await body.innerText()).toBe(before);
  // A click just after a link, in a paragraph or a list, places the caret there.
  for (const name of ['the docs', 'the list link']) {
    const at = await body.locator('a', { hasText: name }).boundingBox();
    expect(await openedBy(context, () => page.mouse.click(at.x + at.width + 12, at.y + at.height / 2)), name).toBeNull();
    expect(await page.evaluate(() => window.getSelection().isCollapsed), name).toBe(true);
  }
  // The caret moved into the link with the keyboard; Ctrl+K opens its menu.
  await link.evaluate(a => { const r = document.createRange(); r.setStart(a.firstChild, 3); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); });
  await page.keyboard.press('ArrowRight');
  expect(await openedBy(context, () => page.keyboard.press('Control+k'))).toBeNull();
  await expect(page.locator('.note-link-input')).toHaveValue('https://example.com/qa-link-para');
  await context.close();
});

test('MINOR (security) a javascript: link in a note never opens or runs', async ({ browser }) => {
  const id = await makeNote(owner, 'QA link script', LINK_NOTE);
  const { context, page, body } = await openNote(browser, owner, id);
  await stubLinkTargets(context);
  let dialog = false;
  page.on('dialog', d => { dialog = true; d.dismiss(); });
  const bad = body.locator('a', { hasText: 'script link' });
  for (const click of [() => bad.click(), () => bad.click({ modifiers: ['Control'] })]) {
    expect(await openedBy(context, click)).toBeNull();
  }
  expect(dialog).toBe(false);
  expect(page.url()).toContain(ORIGIN);
  await context.close();
});

test('MINOR a link in a view-only shared note opens with a plain click', async ({ browser }) => {
  const id = await makeNote(owner, 'QA link view only', LINK_NOTE);
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'view' } });
  const { context, page, body } = await openNote(browser, collab, id);
  await stubLinkTargets(context);
  await expect(body).toHaveAttribute('contenteditable', 'false');
  const link = body.locator('a', { hasText: 'the list link' });
  expect(await link.evaluate(a => getComputedStyle(a).cursor)).toBe('pointer');
  expect(await openedBy(context, () => link.click())).toBe('https://example.com/qa-link-list');
  expect(page.url()).toContain(ORIGIN);
  await context.close();
});

test('MINOR on a phone a tap opens a link in a note without opening the keyboard', async ({ browser }) => {
  const id = await makeNote(owner, 'QA link phone', LINK_NOTE);
  const { context, page } = await phonePage(browser, owner);
  await stubLinkTargets(context);
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  await settledWindow(page);
  const body = page.locator('#note-window-modal .note-window-body');
  for (const [name, path] of [['the docs', 'qa-link-para'], ['the list link', 'qa-link-list']]) {
    expect(await openedBy(context, () => body.locator('a', { hasText: name }).tap()), name).toBe(`https://example.com/${path}`);
    expect(await page.evaluate(() => document.activeElement?.classList.contains('note-window-body')), name).toBe(false);
  }
  await context.close();
});

test('MINOR an image from elsewhere shown as a link opens only with Ctrl+click, so a click does not load it', async ({ browser }) => {
  const id = await makeNote(owner, 'QA image chip', 'See ![map](https://example.com/qa-chip-map.png) here');
  const { context, body } = await openNote(browser, owner, id);
  await stubLinkTargets(context);
  const chip = body.locator('a[data-md-image]');
  expect(await chip.evaluate(a => getComputedStyle(a).cursor)).not.toBe('pointer');
  expect(await openedBy(context, () => chip.click())).toBeNull();
  expect(await openedBy(context, () => chip.click({ modifiers: ['Control'] }))).toBe('https://example.com/qa-chip-map.png');
  await context.close();
});

// Leo's feedback of 2026-11-18: an image in a note shows inline however the
// note wrote it. The demo note's SVG data: image showed as a link, and notes
// written on another address of this server (`http://<old host>/api/upload/<id>`)
// showed their images as long links.
function solidPng(width, height) {
  const chunk = (type, data) => {
    const out = Buffer.alloc(12 + data.length);
    out.writeUInt32BE(data.length, 0);
    out.write(type, 4, 'latin1');
    data.copy(out, 8);
    out.writeUInt32BE(crc32(Buffer.concat([Buffer.from(type, 'latin1'), data])), 8 + data.length);
    return out;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  const rows = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) rows.set([108, 92, 230], y * (width * 3 + 1) + 1 + x * 3);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}
// Each upload is a new file: the server keeps one copy of identical bytes, under one id.
let pngSerial = 0;
async function uploadPng(api, name = 'qa.png') {
  const buffer = solidPng(400 + (++pngSerial % 50), 200 + Math.floor(Date.now() / 1000) % 50);
  let response;
  // The server takes only a few uploads every few seconds (429).
  for (let i = 0; i < 20; i++) {
    response = await api.post('/api/upload', { multipart: { files: { name, mimeType: 'image/png', buffer } } });
    if (response.status() !== 429) break;
    await new Promise(done => setTimeout(done, 1500));
  }
  expect(response.ok(), 'upload').toBeTruthy();
  return (await response.json()).files[0].id;
}
const SVG_DATA = `data:image/svg+xml;base64,${Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="#6c5ce6"/></svg>').toString('base64')}`;
// The server refuses a note that names an upload it does not have, so a
// missing file is an upload whose file is gone: it answers 404 here.
const goneFile = upload => context => context.route(url => url.pathname.endsWith(`/${upload}`), route => route.fulfill({ status: 404, body: 'File not found' }));
const OLD_HOST = 'http://qa-old-host.invalid:7860';
// Every image in the note body, as shown: loaded, its width relative to the body, and whether a placeholder stands in for it.
const shownImages = root => root.evaluate(el => [...el.querySelectorAll('img')].map(img => ({
  src: img.getAttribute('src'),
  loaded: img.complete && img.naturalWidth > 0,
  share: Math.round(100 * img.getBoundingClientRect().width / el.getBoundingClientRect().width),
  placeholder: img.nextElementSibling?.classList.contains('note-image-missing') ? img.nextElementSibling.textContent : null,
})));

test('MAJOR a note image shows inline however it was written: a data: image (PNG or SVG), an upload path, an upload under an old address', async ({ browser }) => {
  const upload = await uploadPng(owner);
  const id = await makeNote(owner, 'QA images inline', [
    'svg', `![svg](${SVG_DATA})`,
    'path', `![path](/api/upload/${upload} "w=33")`,
    'old address', `![old](${OLD_HOST}/api/upload/${upload} "w=33")`,
  ].join('\n\n'));
  const { context, page, errors, body } = await openNote(browser, owner, id);
  const requests = [];
  page.on('request', r => requests.push(r.url()));
  await expect.poll(async () => (await shownImages(body)).filter(img => img.loaded).length, { timeout: 5000 }).toBe(3);
  const [svg, path, old] = await shownImages(body);
  expect(svg).toMatchObject({ src: SVG_DATA, loaded: true, placeholder: null });
  expect(path).toMatchObject({ src: `/api/upload/${upload}`, loaded: true, placeholder: null });
  expect(old).toMatchObject({ src: `/api/upload/${upload}`, loaded: true, placeholder: null });
  expect(Math.abs(path.share - 33), 'w=33 is a third of the note').toBeLessThanOrEqual(2);
  expect(old.share).toBe(path.share);
  await expect(body.locator('a[data-md-image]')).toHaveCount(0);
  // The card shows the first image as its picture.
  const thumb = page.locator(`#notes-pane .note-card[data-note-id="${id}"] img.note-card-image`);
  await expect(thumb).toHaveAttribute('src', SVG_DATA);
  expect(await thumb.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  expect(requests.filter(url => url.includes('qa-old-host')), 'the old address is never fetched').toEqual([]);
  expect(errors).toEqual([]);
  await context.close();
});

test('MAJOR an image uploaded under an old address is saved with its path on the next edit; one this server lacks keeps its address', async ({ browser }) => {
  const upload = await uploadPng(owner);
  const gone = await uploadPng(owner, 'gone.png');
  const content = `Intro\n\n![](${OLD_HOST}/api/upload/${upload} "w=33")\n\n![gone](${OLD_HOST}/api/upload/${gone})`;
  const id = await makeNote(owner, 'QA images old address', content);
  const { context, page, body } = await openNote(browser, owner, id, goneFile(gone));
  await expect.poll(async () => (await shownImages(body)).map(img => img.loaded || !!img.placeholder), { timeout: 5000 }).toEqual([true, true]);
  // Opening alone writes nothing.
  await page.waitForTimeout(1500);
  expect((await read(owner, id)).content).toBe(content);
  await body.locator('p', { hasText: 'Intro' }).click();
  await page.keyboard.press('End');
  await page.keyboard.type(' edited');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 })
    .toBe(`Intro edited\n\n![](/api/upload/${upload} "w=33")\n\n![gone](${OLD_HOST}/api/upload/${gone})`);
  await context.close();
});

test('MINOR an image whose file is missing shows a placeholder with its description, in the window and on the card, and can be removed', async ({ browser }) => {
  const gone = await uploadPng(owner, 'gone.png');
  const content = `Before\n\n![receipt from march](/api/upload/${gone} "w=50")\n\nAfter`;
  const id = await makeNote(owner, 'QA image missing', content);
  const { context, page, errors, body } = await openNote(browser, owner, id, goneFile(gone));
  const box = body.locator('.note-image-missing');
  await expect(box).toHaveText('receipt from march');
  await expect(box).toHaveAttribute('aria-label', 'Image not found: receipt from march');
  expect(await body.locator('img').evaluate(img => getComputedStyle(img).display)).toBe('none');
  const card = page.locator(`#notes-pane .note-card[data-note-id="${id}"]`);
  await expect(card.locator('.note-image-missing')).toHaveText('receipt from march');
  // Nothing about the placeholder is written back.
  await body.locator('p', { hasText: 'After' }).click();
  await page.keyboard.press('End');
  await page.keyboard.type('!');
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toBe(`${content}!`);
  // Its menu is the image's: Remove takes both.
  await box.click();
  await page.locator('.note-image-menu [data-act="remove"]').click();
  await expect(box).toHaveCount(0);
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toBe('Before\n\nAfter!');
  expect(errors).toEqual([]);
  await context.close();
});

test('MINOR (privacy) an image from another site stays a link with the setting off and shows with it on; an old-address upload never asks that site', async ({ browser }) => {
  const upload = await uploadPng(owner);
  const id = await makeNote(owner, 'QA image external', `![ext](https://images.qa.invalid/pic.png)\n\n![old](https://images.qa.invalid/api/upload/${upload})`);
  const png = solidPng(40, 20);
  for (const setting of [false, true]) {
    await owner.put('/api/prefs/notes_show_external_images', { data: { value: setting } });
    const { context, page, body } = await openNote(browser, owner, id);
    const asked = [];
    await context.route('https://images.qa.invalid/**', route => { asked.push(new URL(route.request().url()).pathname); route.fulfill({ contentType: 'image/png', body: png }); });
    // The first open may have started before the route: reopen to count.
    await page.reload();
    await page.waitForFunction(() => window.notesModule?.openPanel);
    await page.evaluate(() => window.notesModule.openPanel());
    await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).click();
    const fresh = page.locator('#note-window-modal .note-window-body');
    await expect.poll(async () => (await shownImages(fresh)).some(img => img.src === `/api/upload/${upload}` && img.loaded), { timeout: 5000 }).toBe(true);
    if (setting) {
      await expect.poll(async () => (await shownImages(fresh)).some(img => img.src === 'https://images.qa.invalid/pic.png' && img.loaded), { timeout: 5000 }).toBe(true);
      expect(asked).toEqual(['/pic.png']);
    } else {
      await expect(fresh.locator('a[data-md-image]')).toHaveText('ext');
      await page.waitForTimeout(800);
      expect(asked).toEqual([]);
    }
    await context.close();
  }
  await owner.put('/api/prefs/notes_show_external_images', { data: { value: false } });
});

test('MINOR a view-only shared note shows its images, the old-address upload too, and a placeholder for a missing one', async ({ browser }) => {
  const upload = await uploadPng(owner);
  const gone = await uploadPng(owner, 'gone.png');
  const id = await makeNote(owner, 'QA images shared', `![a](/api/upload/${upload})\n\n![b](${OLD_HOST}/api/upload/${upload} "w=33")\n\n![c](/api/upload/${gone})`);
  await owner.post(`/api/notes/${id}/share`, { data: { users: [COLLAB.username], permission: 'view' } });
  const { context, body } = await openNote(browser, collab, id, goneFile(gone));
  await expect.poll(async () => (await shownImages(body)).map(img => img.loaded || img.placeholder), { timeout: 5000 }).toEqual([true, true, 'c']);
  expect((await shownImages(body)).slice(0, 2).every(img => img.src === `/api/notes/${id}/media/${upload}`)).toBe(true);
  await context.close();
});

test('MINOR on a phone a note image shows inline and a missing one shows its placeholder', async ({ browser }) => {
  const upload = await uploadPng(owner);
  const gone = await uploadPng(owner, 'gone.png');
  const id = await makeNote(owner, 'QA images phone', `![svg](${SVG_DATA})\n\n![old](${OLD_HOST}/api/upload/${upload} "w=66")\n\n![gone](/api/upload/${gone})`);
  const { context, page } = await phonePage(browser, owner, goneFile(gone));
  await page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`).tap();
  await settledWindow(page);
  const body = page.locator('#note-window-modal .note-window-body');
  await expect.poll(async () => (await shownImages(body)).map(img => img.loaded || img.placeholder), { timeout: 5000 }).toEqual([true, true, 'gone']);
  await context.close();
});

test('MINOR an image inserted from a file is saved as a path, not this page\'s address', async ({ browser }) => {
  const id = await makeNote(owner, 'QA image insert', 'Start');
  const { context, page, body } = await openNote(browser, owner, id);
  await body.locator('p', { hasText: 'Start' }).click();
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#note-window-modal button[data-md="image"]').click();
  await (await chooser).setFiles({ name: 'insert.png', mimeType: 'image/png', buffer: solidPng(400, 200) });
  await expect.poll(async () => (await read(owner, id)).content, { timeout: 8000 }).toMatch(/^Start\n\n!\[\]\(\/api\/upload\/[0-9a-f]{32}\.png\)$/);
  await context.close();
});

// on an iPhone, a note closed by swiping its sheet down did not open
// again when its card was tapped. The swipe only hid the window: the note
// stayed "open", so the tap raised a hidden window. swipeDown() plays the
// gesture with real touch events, from the title bar's free space, the way the
// sheet's own grab zone takes it.
async function proMaxPhone(browser, api) {
  const context = await browser.newContext({ ...devices['iPhone 15 Pro Max'], storageState: await api.storageState() });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  const page = await context.newPage();
  await page.goto('about:blank');
  await page.goto(ORIGIN);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  return { context, page };
}
async function swipeDown(page) {
  const start = await page.locator('#note-window-modal .note-window-header').evaluate(header => {
    const r = header.getBoundingClientRect();
    const y = Math.round(r.top + Math.min(r.height / 2, 20));
    for (let x = Math.round(r.right) - 4; x > r.left; x -= 4) {
      const hit = document.elementFromPoint(x, y);
      if (hit && header.contains(hit) && !hit.closest('button, input, select, label')) return { x, y };
    }
    for (let x = Math.round(r.left) + 4; x < r.right; x += 4) {
      const hit = document.elementFromPoint(x, y);
      if (hit && header.contains(hit) && !hit.closest('button, input, select, label')) return { x, y };
    }
    return null;
  });
  expect(start, 'free space in the title bar to start the swipe').not.toBeNull();
  const cdp = await page.context().newCDPSession(page);
  const touch = (type, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: start.x, y }] });
  await touch('touchStart', start.y);
  for (let step = 1; step <= 10; step++) { await touch('touchMove', start.y + step * 30); await page.waitForTimeout(16); }
  await touch('touchEnd', start.y + 300);
  await cdp.detach();
  // The sheet slides away (at most 300 ms), then the window is gone.
  await expect(page.locator('#note-window-modal:not(.hidden)')).toHaveCount(0);
}
const card = (page, id) => page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-card-title`);
// The window is shown and on top: what is under the middle of its sheet is the sheet.
async function expectWindowOnTop(page, id, what) {
  const win = page.locator('#note-window-modal');
  await expect(win, what).toBeVisible();
  await expect(win.locator('.note-window-title'), what).toHaveValue(id.title);
  await settledWindow(page);
  const hit = await win.locator('.note-window-content').evaluate(content => {
    const r = content.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!at && content.contains(at);
  });
  expect(hit, `${what}: the middle of the screen is the note window`).toBe(true);
}

test('MAJOR on a phone a note swiped away opens again when its card is tapped', async ({ browser }) => {
  const note = { id: await makeNote(owner, 'QA swipe again', 'Swipe me'), title: 'QA swipe again' };
  const { context, page } = await proMaxPhone(browser, owner);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'first open');
  await swipeDown(page);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'tapped again after the swipe');
  // Twice in a row. Swiped away is closed, as with the close button: no #note- entry left behind.
  await swipeDown(page);
  expect(new URL(page.url()).hash).not.toMatch(/^#note-/);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'tapped again after a second swipe');
  await context.close();
});

test('MAJOR on a phone a swipe-down saves the edit like the close button', async ({ browser }) => {
  const note = { id: await makeNote(owner, 'QA swipe saves', 'Line'), title: 'QA swipe saves' };
  const { context, page } = await proMaxPhone(browser, owner);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'open');
  await page.locator('#note-window-modal .note-window-body p', { hasText: 'Line' }).tap();
  await page.keyboard.press('End');
  await page.keyboard.type(' swiped');
  await swipeDown(page);
  await expect.poll(async () => (await read(owner, note.id)).content, { timeout: 5000 }).toBe('Line swiped');
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'reopened');
  await expect(page.locator('#note-window-modal .note-window-body')).toHaveText('Line swiped');
  await context.close();
});

test('Focus-Deferred Synchronization: focus on editor or modal defers sync and reconciles on authentic focusout', async ({ browser }) => {
  const id = await makeNote(owner, 'QA Focus Sync Note', '- [ ] Task 1\n- [ ] Task 2');
  const { context, page, body } = await openNote(browser, owner, id);
  try {
    // 1-4. Edit the content and allow autosave to complete
    await body.locator('li .md-task-text').first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(' edited');
    await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('- [ ] Task 1 edited');

    // 5. Keep focus inside the modal on an actual editor element
    const titleInput = page.locator('#note-window-modal .note-window-title');
    await titleInput.focus();
    const isFocusedInModal = await page.evaluate(() => {
      const active = document.activeElement;
      return !!active && document.querySelector('#note-window-modal')?.contains(active);
    });
    expect(isFocusedInModal).toBe(true);

    // 6-7. Trigger a newer note revision through the application flow without moving focus
    await page.evaluate(noteId => {
      const dot = document.querySelectorAll(`.note-card[data-note-id="${noteId}"] .note-checkbox`)[1];
      dot?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    }, id);

    // Wait for the server to have recorded the toggled item
    await expect.poll(async () => (await read(owner, id)).items?.[1]?.done, { timeout: 5000 }).toBe(true);

    // 8. Verify synchronization did not destructively replace the active editor while focused
    const taskDoneInModalBeforeBlur = await body.locator('.md-task-box[aria-checked="true"]').count();
    expect(taskDoneInModalBeforeBlur).toBe(0);

    // 9. Move focus outside the modal using actual browser interaction
    await page.locator('#notes-search').focus();

    // 10. Verify the real focusout handler reconciles the updated content
    await expect.poll(async () => body.locator('.md-task-box[aria-checked="true"]').count(), { timeout: 5000 }).toBe(1);
    await expect(body).toContainText('Task 1 edited');
  } finally {
    await context?.close();
  }
});

test('Minimized Window: updating checklist on card synchronizes into restored window', async ({ browser }) => {
  const id = await makeNote(owner, 'QA Minimized Window Note', '- [ ] Item Alpha\n- [ ] Item Beta');
  const { context, page, body } = await openNote(browser, owner, id);
  try {
    // 2-3. Edit and successfully autosave
    await body.locator('li .md-task-text').first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(' saved');
    await expect.poll(async () => (await read(owner, id)).content, { timeout: 5000 }).toContain('- [ ] Item Alpha saved');

    // 4. Minimize its window
    const minimizeBtn = page.locator('#note-window-modal .modal-minimize-btn');
    await minimizeBtn.click();
    await expect(page.locator('#note-window-modal')).toHaveClass(/minimized/);

    // 5. Update a checklist item through the note card
    const cardItemBox = page.locator(`#notes-pane .note-card[data-note-id="${id}"] .note-checkbox`).nth(1);
    await cardItemBox.click();
    await expect.poll(async () => (await read(owner, id)).items?.[1]?.done, { timeout: 5000 }).toBe(true);

    // 6. Restore the window via the dock chip
    const dockChip = page.locator('.minimized-dock-chip[data-modal-id="note-window-modal"]');
    await dockChip.click();
    await expect(page.locator('#note-window-modal')).not.toHaveClass(/minimized/);

    // 7. Verify the new checklist state appears in the restored window
    await expect(body.locator('.md-task-box[aria-checked="true"]')).toHaveCount(1);
    await expect(body).toContainText('Item Alpha saved');

    // 8. Verify no unintended content overwrite or duplicate request occurred
    const serverNote = await read(owner, id);
    expect(serverNote.content).toContain('- [ ] Item Alpha saved');
    expect(serverNote.items?.[1]?.done).toBe(true);
  } finally {
    await context?.close();
  }
});

test('In-Flight Save: competing revision during in-flight save prevents silent overwrite and enters conflict', async ({ browser }) => {
  const id = await makeNote(owner, 'QA In-Flight Note', 'Initial text');
  let releaseSave;
  let saveStarted = false;
  const saveGate = new Promise(resolve => { releaseSave = resolve; });

  const { context, page, body } = await openNote(browser, owner, id, async ctx => {
    await ctx.route(`**/api/notes/${id}`, async route => {
      if (route.request().method() === 'PUT' || route.request().method() === 'PATCH') {
        saveStarted = true;
        await saveGate;
      }
      await route.continue();
    });
  });

  try {
    // 1-2. Begin an autosave by typing
    await body.locator('p').first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(' and in-flight local edit');

    // Wait until the autosave request is in flight
    await expect.poll(() => saveStarted, { timeout: 5000 }).toBe(true);

    // 3. While save is held in flight, introduce a competing revision on the server
    const currentRev = (await read(owner, id)).body_revision;
    const updateRes = await owner.put(`/api/notes/${id}`, {
      data: {
        title: 'Competing Title',
        content: 'Remote competing server content',
        body_revision: currentRev,
        replace_content: true,
      },
    });
    expect(updateRes.ok()).toBe(true);

    // 4. Verify the editor does not silently adopt incompatible content while in-flight
    await expect(body).toContainText('in-flight local edit');
    expect(await page.locator('#note-window-modal .note-window-title').inputValue()).not.toBe('Competing Title');

    // 5. Release the in-flight request
    releaseSave();

    // 6. The in-flight save met 409 on the server; noteWindow enters conflict banner
    const banner = page.locator('#note-window-modal .note-window-banner:not(.hidden)');
    await expect(banner).toBeVisible({ timeout: 5000 });
    await expect(banner).toContainText('changed elsewhere');
    await expect(banner.getByRole('button', { name: 'Reload' })).toBeVisible();
    await expect(banner.getByRole('button', { name: 'Keep mine' })).toBeVisible();

    // Verify local edits are preserved in editor
    await expect(body).toContainText('in-flight local edit');
  } finally {
    releaseSave?.();
    await context?.close();
  }
});

test('Failed Save: network failure followed by newer server revision preserves unsaved content for recovery', async ({ browser }) => {
  const id = await makeNote(owner, 'QA Failed Save Note', 'Initial offline baseline');
  let failSave = true;

  const { context, page, body } = await openNote(browser, owner, id, async ctx => {
    await ctx.route(`**/api/notes/${id}`, async route => {
      const m = route.request().method();
      if (failSave && (m === 'PUT' || m === 'PATCH')) {
        await route.abort('failed');
        return;
      }
      await route.continue();
    });
  });

  try {
    // 1-2. Type local edits and let autosave fail
    await body.locator('p').first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(' with offline uncommitted typing');

    // Wait for the failure to occur and verify draft was backed up in local storage
    await expect.poll(async () => {
      return page.evaluate(noteId => {
        const raw = localStorage.getItem(`odysseus-note-draft-${noteId}`);
        return raw ? JSON.parse(raw)?.content : null;
      }, id);
    }, { timeout: 5000 }).toContain('with offline uncommitted typing');

    // Stop failing saves so other API calls can function
    failSave = false;

    // 3. Competing revision is created on server
    const currentRev = (await read(owner, id)).body_revision;
    const putRes = await owner.put(`/api/notes/${id}`, {
      data: {
        title: 'Server Updated While Offline',
        content: 'Server revision content created while offline',
        body_revision: currentRev,
        replace_content: true,
      },
    });
    expect(putRes.ok()).toBe(true);

    // 4. Trigger card render in notes pane
    await page.evaluate(() => {
      window.notesModule.openPanel?.();
    });

    // 5. Verify local edits remain intact in the active editor
    await expect(body).toContainText('with offline uncommitted typing');
    expect(await body.textContent()).not.toContain('Server revision content created while offline');

    // 6. Verify unsaved content remains available in storage for recovery
    const draftInStorage = await page.evaluate(noteId => {
      const raw = localStorage.getItem(`odysseus-note-draft-${noteId}`);
      return raw ? JSON.parse(raw)?.content : null;
    }, id);
    expect(draftInStorage).toContain('with offline uncommitted typing');
  } finally {
    await context?.close();
  }
});

test('Conflict Recovery: Reload adopts server content and Keep mine writes local text over server revision', async ({ browser }) => {
  // Part 1: Keep mine workflow
  const idKeep = await makeNote(owner, 'QA Conflict Keep Note', 'Baseline Keep text');
  let patchCountKeep = 0;

  const { context: ctxKeep, page: pageKeep, body: bodyKeep } = await openNote(browser, owner, idKeep, async ctx => {
    await ctx.route(`**/api/notes/${idKeep}`, async route => {
      const m = route.request().method();
      if (m === 'PUT' || m === 'PATCH') {
        patchCountKeep++;
        if (patchCountKeep === 1) {
          await route.fulfill({
            status: 409,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'conflict' }),
          });
          return;
        }
      }
      await route.continue();
    });
  });

  try {
    await bodyKeep.locator('p').first().click();
    await pageKeep.keyboard.press('End');
    await pageKeep.keyboard.type(' - local keep edits');

    const bannerKeep = pageKeep.locator('#note-window-modal .note-window-banner:not(.hidden)');
    await expect(bannerKeep).toBeVisible({ timeout: 5000 });
    await expect(bannerKeep).toContainText('changed elsewhere');

    // Click 'Keep mine'
    await bannerKeep.getByRole('button', { name: 'Keep mine' }).click();

    // Verify banner is dismissed
    await expect(bannerKeep).toHaveCount(0);

    // Verify server has received the local text overwritten
    await expect.poll(async () => (await read(owner, idKeep)).content, { timeout: 5000 }).toContain('Baseline Keep text - local keep edits');
  } finally {
    await ctxKeep?.close();
  }

  // Part 2: Reload workflow
  const idReload = await makeNote(owner, 'QA Conflict Reload Note', 'Baseline Reload text');
  let patchCountReload = 0;

  const { context: ctxReload, page: pageReload, body: bodyReload } = await openNote(browser, owner, idReload, async ctx => {
    await ctx.route(`**/api/notes/${idReload}`, async route => {
      const m = route.request().method();
      if (m === 'PUT' || m === 'PATCH') {
        patchCountReload++;
        if (patchCountReload === 1) {
          const rev = (await read(owner, idReload)).body_revision;
          const reloadPut = await owner.put(`/api/notes/${idReload}`, {
            data: {
              content: 'Server authoritative update for reload test',
              body_revision: rev,
              replace_content: true,
            },
          });
          expect(reloadPut.ok()).toBe(true);
          await route.fulfill({
            status: 409,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'conflict' }),
          });
          return;
        }
      }
      await route.continue();
    });
  });

  try {
    await bodyReload.locator('p').first().click();
    await pageReload.keyboard.press('End');
    await pageReload.keyboard.type(' - typing to discard on reload');

    const bannerReload = pageReload.locator('#note-window-modal .note-window-banner:not(.hidden)');
    await expect(bannerReload).toBeVisible({ timeout: 5000 });

    // Click 'Reload'
    await bannerReload.getByRole('button', { name: 'Reload' }).click();

    // Verify banner is dismissed and editor shows the authoritative server content
    await expect(bannerReload).toHaveCount(0);
    await expect(bodyReload).toContainText('Server authoritative update for reload test');
    expect(await bodyReload.textContent()).not.toContain('typing to discard on reload');
  } finally {
    await ctxReload?.close();
  }
});

test('MAJOR on a phone, after a swipe-down, another card opens its note and Back does not bring the old one back', async ({ browser }) => {
  const first = { id: await makeNote(owner, 'QA swipe first', 'One'), title: 'QA swipe first' };
  const second = { id: await makeNote(owner, 'QA swipe second', 'Two'), title: 'QA swipe second' };
  const { context, page } = await proMaxPhone(browser, owner);
  await card(page, first.id).tap();
  await expectWindowOnTop(page, first, 'first note');
  await swipeDown(page);
  await card(page, second.id).tap();
  await expectWindowOnTop(page, second, 'the other card after the swipe');
  expect(new URL(page.url()).hash).toBe(`#note-${second.id}`);
  // Back closes the second note and lands in the app, not on the first note.
  await page.goBack();
  await page.waitForTimeout(1000);
  expect(new URL(page.url()).host).toBe(new URL(ORIGIN).host);
  expect(new URL(page.url()).hash).not.toMatch(/^#note-/);
  await expect(page.locator('#note-window-modal')).toHaveCount(0);
  // And the first card still opens its note.
  await card(page, first.id).tap();
  await expectWindowOnTop(page, first, 'first card after Back');
  await context.close();
});

test('MAJOR on a phone, Back after a swipe-down does not reopen the note and its card still opens it', async ({ browser }) => {
  const note = { id: await makeNote(owner, 'QA swipe back', 'Back'), title: 'QA swipe back' };
  const { context, page } = await proMaxPhone(browser, owner);
  // An entry in the app before the note's, so Back has somewhere to go in the app.
  await page.evaluate(() => history.pushState(history.state, '', `${location.pathname}${location.search}#`));
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'open');
  await swipeDown(page);
  await page.waitForTimeout(500);
  await page.goBack();
  await page.waitForTimeout(1000);
  expect(new URL(page.url()).host).toBe(new URL(ORIGIN).host);
  await expect(page.locator('#note-window-modal:not(.hidden)')).toHaveCount(0);
  await card(page, note.id).tap();
  await expectWindowOnTop(page, note, 'tapped after swipe then Back');
  await context.close();
});
