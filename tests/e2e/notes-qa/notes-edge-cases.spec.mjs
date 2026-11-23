// Notes edge cases in the note window. Each test asserts the expected
// behaviour, so it FAILS while the defect is present.
//
// Run: venv/bin/python tests/e2e/notes-qa/run_notes_qa.py (see README.md here).
// Never against :7860. NOTES_USER must be an admin.
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
