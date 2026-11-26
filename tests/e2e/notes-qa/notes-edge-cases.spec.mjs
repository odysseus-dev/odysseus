// Notes edge cases found by exploratory testing. Each test asserts the
// expected behaviour, so it FAILS while the defect is present.
//
// Run: venv/bin/python tests/e2e/notes-qa/run_notes_qa.py (see README.md here).
// Never against :7860. NOTES_USER must be an admin that already has a working model endpoint.
// The "failing model" test also needs NOTES_FAILING_MODEL=1 and that admin's
// default endpoint answering HTTP 500 (see bad_model.py).
import { test, expect, request as pwRequest, devices } from '@playwright/test';

const origin = process.env.NOTES_ORIGIN;
if (!origin || /:7860\b/.test(origin)) throw new Error('set NOTES_ORIGIN to a scratch instance');
const U1 = { username: process.env.NOTES_USER, password: process.env.NOTES_PASS };
const U2 = { username: process.env.NOTES_USER2, password: process.env.NOTES_PASS2 };

async function apiFor(user) {
  const api = await pwRequest.newContext({ baseURL: origin });
  for (let i = 0; i < 20; i++) {
    const r = await api.post('/api/auth/login', { data: user });
    if (r.status() !== 429) { expect(r.ok()).toBe(true); return api; }
    await new Promise(res => setTimeout(res, 5000));
  }
  throw new Error('login rate limited');
}
async function notesPage(browser, api, options = {}) {
  const context = await browser.newContext({ storageState: await api.storageState(), ...options });
  await context.addInitScript(() => localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'));
  const page = await context.newPage();
  await page.goto(origin);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator('#notes-pane .notes-pane-body').waitFor();
  return { context, page };
}
const newNote = async (api, data) => (await api.post('/api/notes', { data })).json();
const stored = async (api, id) => (await (await api.get(`/api/notes/${id}`)).json());
const openNote = async (page, id) => {
  await page.evaluate(id => window.notesModule.openNote(id), id);
  await page.locator('#note-window-modal .note-window-body').waitFor();
  await page.waitForTimeout(400);
};
const win = page => page.locator('#note-window-modal');
const body = page => win(page).locator('.note-window-body');
const banner = page => win(page).locator('.note-window-banner:not(.hidden)');
const settle = page => page.waitForTimeout(1500);

let api1, api2;
test.beforeAll(async () => { api1 = await apiFor(U1); api2 = U2.username ? await apiFor(U2) : null; });

test('P1 Backspace at the start of the paragraph after a code block keeps that paragraph', async ({ browser }) => {
  const n = await newNote(api1, { title: 'island backspace', content: 'Before\n\n```js\nconst a = 1;\n```\n\nAfter the code' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).locator('p', { hasText: 'After the code' }).click();
  await page.keyboard.press('Home');
  await page.keyboard.press('Backspace');
  await settle(page);
  expect((await stored(api1, n.id)).content).toContain('After the code');
});

test('P1 Backspace under a code block or a table leaves the line a line, and an empty one goes away', async ({ browser }) => {
  const cases = [
    ['```js\nconst a = 1;\n```', 'After the code'],
    ['| a | b |\n|---|---|\n| 1 | 2 |', 'After the table'],
  ];
  const { page } = await notesPage(browser, api1);
  for (const [block, line] of cases) {
    const n = await newNote(api1, { title: 'fixed block backspace', content: `Before\n\n${block}\n\n${line}` });
    await openNote(page, n.id);
    await body(page).locator('p', { hasText: line }).click();
    await page.keyboard.press('Home');
    await page.keyboard.press('Backspace');
    await settle(page);
    // The table's line must not become a row: it stays a paragraph after a blank line.
    expect((await stored(api1, n.id)).content).toBe(`Before\n\n${block}\n\n${line}`);
    await page.keyboard.press('Escape');
  }
  const n = await newNote(api1, { title: 'fixed block empty line', content: 'Before\n\n```js\nconst a = 1;\n```' });
  await openNote(page, n.id);
  // An empty line under the fence, with the caret on it.
  await page.evaluate(() => {
    const b = document.querySelector('#note-window-modal .note-window-body');
    const p = document.createElement('p'); p.append(document.createElement('br')); b.append(p);
    const r = document.createRange(); r.setStart(p, 0); r.collapse(true);
    getSelection().removeAllRanges(); getSelection().addRange(r); b.focus();
  });
  await page.keyboard.press('Backspace');
  await page.keyboard.type('2;');
  await settle(page);
  expect((await stored(api1, n.id)).content).toBe('Before\n\n```js\nconst a = 1;2;\n```');
});

test('P1 a tick from a tab that has not polled yet does not undo a tick made elsewhere', async ({ browser }) => {
  const n = await newNote(api1, { title: 'Groceries', content: '- [ ] milk\n- [ ] eggs' });
  const { page } = await notesPage(browser, api1);
  const milk = page.locator(`.note-card[data-note-id="${n.id}"] li.md-task.note-checkbox`).first();
  await milk.waitFor();
  await api1.post(`/api/notes/${n.id}/items/0/toggle`, { data: { text: 'milk' } }); // another device ticks milk
  await milk.locator('.note-check-dot').click(); // this tab still shows it unticked; the user ticks it
  await settle(page);
  expect((await stored(api1, n.id)).content).toContain('- [x] milk');
  const shownDone = await milk.evaluate(e => e.classList.contains('done'));
  expect(shownDone).toBe((await stored(api1, n.id)).content.includes('- [x] milk'));
});

test('P1 a note run whose model fails says so', async ({ browser }) => {
  test.skip(!process.env.NOTES_FAILING_MODEL, 'needs the admin default endpoint to answer HTTP 500');
  const n = await newNote(api1, { title: 'failing run', content: 'Trip\n\n- [ ] book a cabin' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  const row = win(page).locator('li.md-task').first();
  await row.hover();
  await row.locator('.note-task-agent').click();
  await page.locator('.note-ask-menu [data-act="solve"]').click();
  // The server logs "local endpoint is having an outage (HTTP 500)" within a second.
  await expect(page.locator('body')).toContainText(/outage|failed|error|could not/i, { timeout: 20000 });
  await expect(page.locator('#toast').filter({ hasText: 'Agent working in the background' })).toBeHidden({ timeout: 20000 });
});

test('P1 a note run whose model fails shows the error in a toast, and its chat says why it stopped', async ({ browser }) => {
  test.skip(!process.env.NOTES_FAILING_MODEL, 'needs the admin default endpoint to answer HTTP 500');
  const n = await newNote(api1, { title: 'failing run, chat', content: 'Trip\n\n- [ ] book a cabin' });
  const { page, context } = await notesPage(browser, api1);
  await openNote(page, n.id);
  const row = win(page).locator('li.md-task').first();
  await row.hover();
  await row.locator('.note-task-agent').click();
  await page.locator('.note-ask-menu [data-act="solve"]').click();
  // The run's own failure, not a word that happens to be on the page.
  await expect(page.locator('#toast.error.show')).toContainText(/Agent failed: .*HTTP 500/, { timeout: 20000 });
  // A fresh page, the card's Agent tag: the chat has the question and why the run stopped.
  const fresh = await context.newPage();
  await fresh.goto(origin);
  await fresh.waitForFunction(() => window.notesModule?.openPanel);
  await fresh.evaluate(() => window.notesModule.openPanel());
  await fresh.locator(`.note-card[data-note-id="${n.id}"] .note-card-agent-tag`).click();
  await expect(fresh.locator('#chat-history')).toContainText('Agent stopped: Model request failed (HTTP 500)', { timeout: 20000 });
});

test('P1 pasting 100 plain-text lines does not freeze the page', async ({ browser, browserName }) => {
  const n = await newNote(api1, { title: 'paste 100', content: 'Before' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).locator('p').click();
  await page.keyboard.press('End');
  const text = Array.from({ length: 100 }, (_, i) => `line ${i}`).join('\n');
  const t0 = Date.now();
  await page.evaluate(t => {
    const dt = new DataTransfer(); dt.setData('text/plain', t);
    document.querySelector('#note-window-modal .note-window-body').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, text);
  expect(Date.now() - t0, `${browserName}: page blocked`).toBeLessThan(1000);
});

test('P1 lines pasted into a task become tasks, the rest of the task follows them, and Ctrl+Z takes the paste back', async ({ browser }) => {
  const n = await newNote(api1, { title: 'paste into a task', content: '- [ ] milk\n- [ ] eggs' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).locator('.md-task-text').first().click();
  await page.evaluate(() => {
    const text = document.querySelector('#note-window-modal .note-window-body .md-task-text').firstChild;
    const range = document.createRange(); range.setStart(text, 2); range.collapse(true);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    const dt = new DataTransfer(); dt.setData('text/plain', 'x1\r\nx2\r\nx3');
    document.querySelector('#note-window-modal .note-window-body').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  });
  await expect(body(page).locator('li.md-task > .md-task-box')).toHaveCount(4);
  await expect.poll(async () => (await stored(api1, n.id)).content).toBe('- [ ] mix1\n- [ ] x2\n- [ ] x3lk\n- [ ] eggs');
  await page.keyboard.press('ControlOrMeta+z');
  await expect(body(page).locator('li.md-task')).toHaveCount(2);
  await page.keyboard.type('Q');
  await expect.poll(async () => (await stored(api1, n.id)).content).toBe('- [ ] miQlk\n- [ ] eggs');
});

test('P2 (intermittent) rapid taps on a card checkbox leave the card showing what the server has', async ({ browser }) => {
  const { page } = await notesPage(browser, api1);
  let mismatches = 0;
  for (let k = 0; k < 10; k++) {
    const n = await newNote(api1, { title: `rapid ${k}`, content: '- [ ] a\n- [ ] b\n- [ ] c' });
    await page.reload(); await page.waitForFunction(() => window.notesModule?.openPanel); await page.evaluate(() => window.notesModule.openPanel());
    const box = page.locator(`.note-card[data-note-id="${n.id}"] li.md-task.note-checkbox`).nth(1).locator('.note-check-dot');
    await box.waitFor();
    for (let i = 0; i < 5; i++) await box.click();
    await page.waitForTimeout(3000);
    const server = (await stored(api1, n.id)).content.includes('- [x] b');
    const shown = await page.locator(`.note-card[data-note-id="${n.id}"] li.md-task`).nth(1).evaluate(e => e.classList.contains('done'));
    if (server !== shown) mismatches++;
  }
  expect(mismatches).toBe(0);
});

test('P2 Checklist on three selected lines makes three tasks', async ({ browser }) => {
  const n = await newNote(api1, { title: 'convert', content: 'x' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).click(); await page.keyboard.press('ControlOrMeta+a'); await page.keyboard.press('Backspace');
  await page.keyboard.type('one'); await page.keyboard.press('Enter'); await page.keyboard.type('two'); await page.keyboard.press('Enter'); await page.keyboard.type('three');
  await settle(page);
  await page.keyboard.press('ControlOrMeta+a');
  await win(page).locator('[data-md="check"]').click();
  await settle(page);
  expect((await stored(api1, n.id)).content).toBe('- [ ] one\n- [ ] two\n- [ ] three'); // Bullet list gives "- one\n- two\n- three"
});

test('P2 Ctrl+Z after Checklist leaves the window and the saved note agreeing', async ({ browser }) => {
  const n = await newNote(api1, { title: 'undo checklist', content: 'Buy milk' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).locator('p').click(); await page.keyboard.press('End');
  await page.keyboard.type(' now');
  await win(page).locator('[data-md="check"]').click();
  await settle(page);
  await body(page).click(); await page.keyboard.press('End');
  await page.keyboard.press('ControlOrMeta+z');
  await settle(page);
  await page.keyboard.press('ControlOrMeta+z');
  await settle(page);
  const isTaskShown = (await win(page).locator('li.md-task').count()) > 0;
  const isTaskStored = (await stored(api1, n.id)).content.startsWith('- [ ]');
  expect(isTaskShown).toBe(isTaskStored);
});

for (const [what, change] of [['colour', api => ({ data: { color: 'blue' } })], ['tag', api => ({ data: { label: 'Errands' } })]]) {
  test(`P2 a ${what} change from another device does not make the next text save a conflict`, async ({ browser }) => {
    const n = await newNote(api1, { title: `meta ${what}`, content: 'Line' });
    const { page } = await notesPage(browser, api1);
    await openNote(page, n.id);
    await api1.put(`/api/notes/${n.id}`, change(api1));
    await body(page).locator('p').click(); await page.keyboard.press('End'); await page.keyboard.insertText(' typed');
    await page.waitForTimeout(2500);
    await expect(banner(page)).toHaveCount(0);
    expect((await stored(api1, n.id)).content).toBe('Line typed');
  });
}

test('P2 a collaborator downgraded to view-only is told so, not that the edit is pending', async ({ browser }) => {
  test.skip(!api2, 'needs NOTES_USER2');
  const n = await newNote(api1, { title: 'downgrade', content: 'Shared line' });
  await api1.post(`/api/notes/${n.id}/share`, { data: { users: [U2.username], permission: 'edit' } });
  const { page } = await notesPage(browser, api2);
  await openNote(page, n.id);
  await api1.post(`/api/notes/${n.id}/share`, { data: { users: [U2.username], permission: 'view' } });
  await body(page).locator('p').click(); await page.keyboard.press('End'); await page.keyboard.insertText(' bob');
  await page.waitForTimeout(3000);
  await expect(page.locator('.toast').filter({ hasText: 'Your edits are kept on this device' })).toHaveCount(0);
  await expect(body(page)).toHaveAttribute('contenteditable', 'false');
});

test('P2 edits made offline are sent when the connection comes back', async ({ browser }) => {
  const n = await newNote(api1, { title: 'offline', content: 'start' });
  const { page, context } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await context.setOffline(true);
  await body(page).locator('p').click(); await page.keyboard.press('End'); await page.keyboard.insertText(' offline-edit');
  await page.waitForTimeout(1500);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await context.setOffline(false);
  await expect.poll(async () => (await stored(api1, n.id)).content, { timeout: 30000 }).toBe('start offline-edit');
});

test('P2 a note with one 300 KB line opens in WebKit in under 3 s', async ({ browser, browserName }) => {
  test.skip(browserName !== 'webkit', 'WebKit only (Chromium: ~1 s)');
  const blob = Buffer.from(Array.from({ length: 225000 }, (_, i) => i % 251)).toString('base64');
  const n = await newNote(api1, { title: 'pasted blob', content: 'data: ' + blob });
  const { page } = await notesPage(browser, api1);
  const t0 = Date.now();
  await page.evaluate(id => window.notesModule.openNote(id), n.id);
  await page.locator('#note-window-modal .note-window-body').waitFor({ timeout: 60000 });
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => r())));
  expect(Date.now() - t0).toBeLessThan(3000);
  await api1.delete(`/api/notes/${n.id}`);
});

test('P2 an iPhone HEIC photo is visible in Chromium', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium' || !process.env.NOTES_HEIC, 'needs NOTES_HEIC=<path to a real .heic>');
  const fs = await import('node:fs');
  const up = await api1.post('/api/upload', { multipart: { files: { name: 'IMG_0420.HEIC', mimeType: 'image/heic', buffer: fs.readFileSync(process.env.NOTES_HEIC) } } });
  const file = (await up.json()).files[0];
  const n = await newNote(api1, { title: 'HEIC', content: `Holiday\n\n![](/api/upload/${file.id})` });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  const img = win(page).locator('img.md-note-image');
  await img.waitFor({ state: 'attached' });
  await page.waitForTimeout(1500);
  expect(await img.evaluate(e => e.naturalWidth)).toBeGreaterThan(0);
});

test('P2 a phone in landscape shows at least 150 px of the note', async ({ browser }) => {
  const iphone = devices['iPhone 15'];
  const n = await newNote(api1, { title: 'landscape', content: Array.from({ length: 30 }, (_, i) => `- [ ] item ${i}`).join('\n') });
  const { page } = await notesPage(browser, api1, { ...iphone, viewport: { width: iphone.viewport.height, height: iphone.viewport.width } });
  await openNote(page, n.id);
  const h = await win(page).locator('.note-window-scroll').evaluate(e => e.getBoundingClientRect().height);
  expect(h).toBeGreaterThanOrEqual(150);
  // A phone sheet has no close button (a swipe down closes it): its title bar stays in view.
  await expect(win(page).locator('.note-window-title')).toBeInViewport();
});

test('P2 Back with a note open closes the note, not the app', async ({ browser }) => {
  const n = await newNote(api1, { title: 'back', content: 'Line' });
  const { page } = await notesPage(browser, api1, devices['Pixel 7']);
  await page.goto('about:blank'); await page.goto(origin);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await openNote(page, n.id);
  await page.goBack();
  await page.waitForTimeout(1000);
  expect(page.url()).toContain(new URL(origin).host);
  await expect(win(page)).toHaveCount(0);
});

// Back with a note open: the window has its own history entry (#note-<id>).
const backToApp = async (page, { hash = '' } = {}) => {
  await page.goBack();
  await page.waitForTimeout(1000);
  expect(new URL(page.url()).host).toBe(new URL(origin).host);
  expect(new URL(page.url()).hash).toBe(hash);
  await expect(win(page)).toHaveCount(0);
};

test('P2 Back on a phone saves the edit, closes the note, and Forward opens it again', async ({ browser }) => {
  const n = await newNote(api1, { title: 'back phone', content: 'Line' });
  const { page } = await notesPage(browser, api1, devices['Pixel 7']);
  await openNote(page, n.id);
  expect(new URL(page.url()).hash).toBe(`#note-${n.id}`);
  await body(page).click();
  await page.keyboard.press('ControlOrMeta+End');
  await page.keyboard.insertText(' typed before Back');
  await backToApp(page);
  await expect.poll(async () => (await stored(api1, n.id)).content, { timeout: 10_000 }).toBe('Line typed before Back');
  await page.goForward();
  await body(page).waitFor();
  await expect(body(page)).toHaveText('Line typed before Back');
  expect(new URL(page.url()).hash).toBe(`#note-${n.id}`);
});

test('P2 Back on desktop closes the note and keeps the chat it was opened over', async ({ browser }) => {
  // A chat to come back to; its model is never called.
  const created = await api1.post('/api/session', { form: { name: 'back to this chat', endpoint_url: 'http://127.0.0.1:9/v1', model: 'stub', skip_validation: 'true' } });
  expect(created.ok(), await created.text()).toBe(true);
  const chat = (await created.json()).id;
  const n = await newNote(api1, { title: 'back desktop', content: 'Line' });
  const { page } = await notesPage(browser, api1);
  await page.goto(`${origin}/#${chat}`);
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId?.() === id, chat);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await openNote(page, n.id);
  expect(new URL(page.url()).hash).toBe(`#note-${n.id}`);
  await backToApp(page, { hash: `#${chat}` });
  expect(await page.evaluate(() => window.sessionModule.getCurrentSessionId())).toBe(chat);
  // Closed by hand, the entry goes too: Back then leaves the chat for the page before it.
  await openNote(page, n.id);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await page.waitForTimeout(500);
  expect(new URL(page.url()).hash).toBe(`#${chat}`);
});

test('P2 a URL on #note-<id> opens that note, and Back from it stays in the app', async ({ browser }) => {
  const n = await newNote(api1, { title: 'deep link', content: 'Opened from its URL' });
  const { page } = await notesPage(browser, api1);
  await page.goto('about:blank');
  await page.goto(`${origin}/#note-${n.id}`);
  await body(page).waitFor();
  await expect(body(page)).toHaveText('Opened from its URL');
  await backToApp(page);
  // Not readable (another user's private note): nothing opens, the hash goes.
  if (api2) {
    const other = await newNote(api2, { title: 'private', content: 'not yours' });
    await page.goto(`${origin}/#note-${other.id}`);
    await page.waitForTimeout(2500);
    await expect(win(page)).toHaveCount(0);
    expect(new URL(page.url()).hash).toBe('');
  }
});

// A reload with a note open comes back to the chat it was opened over (the
// note's entry keeps that chat in history.state, which a reload keeps and a
// new tab does not have).
const chatFor = async (api, name) => {
  const created = await api.post('/api/session', { form: { name, endpoint_url: 'http://127.0.0.1:9/v1', model: 'stub', skip_validation: 'true' } });
  expect(created.ok(), await created.text()).toBe(true);
  return (await created.json()).id;
};
const currentChat = page => page.evaluate(() => window.sessionModule?.getCurrentSessionId?.() || null);
const onChat = async (page, chat) => {
  await page.goto(`${origin}/#${chat}`);
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId?.() === id, chat);
  await page.waitForFunction(() => window.notesModule?.openPanel);
};
const afterReload = async page => {
  await page.reload();
  await page.waitForFunction(() => window.sessionModule?.getCurrentSessionId && window.notesModule?.openPanel);
  await page.waitForTimeout(2500);
};

for (const [label, options] of [['desktop', {}], ['phone', devices['Pixel 7']]]) {
  test(`P1 ${label}: a reload with a note open keeps the chat, and Back then closes the note on that chat`, async ({ browser }) => {
    const chat = await chatFor(api1, `reload keeps this chat ${label}`);
    const n = await newNote(api1, { title: `reload ${label}`, content: 'Still here after a reload' });
    const { page } = await notesPage(browser, api1, options);
    await onChat(page, chat);
    // Control: a reload without a note stays on the chat.
    await afterReload(page);
    expect(await currentChat(page)).toBe(chat);
    expect(new URL(page.url()).hash).toBe(`#${chat}`);
    await openNote(page, n.id);
    expect(new URL(page.url()).hash).toBe(`#note-${n.id}`);
    await afterReload(page);
    await body(page).waitFor();
    await expect(body(page)).toHaveText('Still here after a reload');
    expect(await currentChat(page)).toBe(chat);
    expect(new URL(page.url()).hash).toBe(`#note-${n.id}`);
    await backToApp(page, { hash: `#${chat}` });
    expect(await currentChat(page)).toBe(chat);
    // A second reload, now without the note, is still on that chat.
    await afterReload(page);
    expect(await currentChat(page)).toBe(chat);
    await expect(win(page)).toHaveCount(0);
  });
}

test('P1 a reload after switching chats with a note open comes back to the chat switched to', async ({ browser }) => {
  const first = await chatFor(api1, 'switched from');
  const second = await chatFor(api1, 'switched to');
  const n = await newNote(api1, { title: 'switch then reload', content: 'Line' });
  const { page } = await notesPage(browser, api1);
  await onChat(page, first);
  await openNote(page, n.id);
  await page.evaluate(id => window.sessionModule.selectSession(id), second);
  await page.waitForFunction(id => window.sessionModule.getCurrentSessionId() === id, second);
  const open = await win(page).count();
  await afterReload(page);
  expect(await currentChat(page)).toBe(second);
  await expect(win(page)).toHaveCount(open);
});

test('P1 a #note-<id> URL in a new tab opens the note over the chat a plain load gives', async ({ browser }) => {
  const chat = await chatFor(api1, 'not restored in a new tab');
  const n = await newNote(api1, { title: 'new tab', content: 'Opened in a new tab' });
  const { context, page } = await notesPage(browser, api1);
  await onChat(page, chat);
  await openNote(page, n.id);
  // Same browser, new tab: no history.state, no sessionStorage.
  const plain = await context.newPage();
  await plain.goto(origin);
  await plain.waitForFunction(() => window.sessionModule?.getCurrentSessionId && window.notesModule?.openPanel);
  await plain.waitForTimeout(2500);
  const plainChat = await currentChat(plain);
  const tab = await context.newPage();
  await tab.goto(`${origin}/#note-${n.id}`);
  await body(tab).waitFor();
  await expect(body(tab)).toHaveText('Opened in a new tab');
  await tab.waitForTimeout(1500);
  expect(await currentChat(tab)).toBe(plainChat);
  await backToApp(tab, { hash: plainChat ? `#${plainChat}` : '' });
});

test('P3 typed \\(a+b\\) and \\[c\\] are shown as typed', async ({ browser }) => {
  const n = await newNote(api1, { title: 'parens', content: 'seed' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await body(page).click(); await page.keyboard.press('ControlOrMeta+a'); await page.keyboard.press('Backspace');
  await page.keyboard.insertText('LaTeX source: \\(a+b\\) and \\[c\\]');
  await settle(page);
  await page.evaluate(() => document.querySelector('#note-window-modal .modal-close').click());
  await openNote(page, n.id);
  expect(await body(page).innerText()).toBe('LaTeX source: \\(a+b\\) and \\[c\\]');
});

test('P3 an autolinked URL in parentheses does not swallow the closing parenthesis', async ({ browser }) => {
  const n = await newNote(api1, { title: 'autolink', content: 'Booking (see https://example.com/booking) for details.' });
  const { page } = await notesPage(browser, api1);
  await openNote(page, n.id);
  await expect(body(page).locator('a').first()).toHaveAttribute('href', 'https://example.com/booking');
});
