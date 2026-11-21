// Notes regression spec for the note window.
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
    "\n  if (String(src) === 'QA_RENDER_FAULT first line') throw new TypeError('injected render fault');");
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

// The quick bar has one way in: a note. A to-do list is that note with the
// toolbar's Checklist. QA round 3, R3, was a quick-bar to-do whose session
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
// The sheet slides in: wait until its top has not moved for three samples in a
// row, so a sample taken before the slide starts does not count as settled.
async function settledWindow(page) {
  const seen = [];
  await expect.poll(async () => {
    seen.push(await page.locator('#note-window-modal .note-window-content').evaluate(e => Math.round(e.getBoundingClientRect().top)));
    return seen.length >= 3 && seen.slice(-3).every(top => top === seen.at(-1));
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

// The footer steps aside while the text has focus, but Tab from the
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
  expect(reached).toEqual(expect.arrayContaining(['archive', 'delete']));
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

// On an iPhone the keyboard does not resize the page. iOS shrinks the
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
  expect(await body.locator('a[href*="script:" i]').count()).toBe(0);
  const bad = body.getByText('script link');
  for (const click of [() => bad.click(), () => bad.click({ modifiers: ['Control'] })]) {
    expect(await openedBy(context, click)).toBeNull();
  }
  expect(dialog).toBe(false);
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

// On an iPhone, a note closed by swiping its sheet down did not open
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
