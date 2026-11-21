// Shared plumbing for the Notes browser flows: accounts, API polling, the
// browser engine and the viewport each flow runs at.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { chromium, devices, webkit, request as playwrightRequest } from 'playwright';

export const origin = process.env.ODYSSEUS_NOTES_RUNTIME_ORIGIN;
export const browserName = process.env.NOTES_BROWSER === 'webkit' ? 'webkit' : 'chromium';
export const engine = browserName === 'webkit' ? webkit : chromium;

// NOTES_PHONE_DEVICE names a Playwright device profile for the phone size
// (viewport, pixel ratio, touch, user agent); `auto` picks iPhone 15 under
// WebKit and Pixel 7 under Chromium. Unset: a plain 390x844 touch viewport.
const phoneDevice = process.env.NOTES_PHONE_DEVICE === 'auto' ? (browserName === 'webkit' ? 'iPhone 15' : 'Pixel 7') : process.env.NOTES_PHONE_DEVICE;
function phoneProfile() {
  if (!phoneDevice) return { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true };
  assert.ok(devices[phoneDevice], `no Playwright device named ${phoneDevice}`);
  const { defaultBrowserType: _engine, ...profile } = devices[phoneDevice];
  return profile;
}

export const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 1000 } },
  phone: phoneProfile(),
};

export const password = () => randomUUID() + 'Aa1!';

/** An API client signed in as `username`; `storage` seeds browser contexts without another login. Logins are limited to 15 per IP per minute, so a 429 is retried until the window opens. */
export async function signIn(username, secret) {
  const api = await playwrightRequest.newContext({ baseURL: origin });
  const login = await until(() => api.post('/api/auth/login', { data: { username, password: secret } }), r => r.status() !== 429, 'the login rate limit', 65000);
  assert.equal(login.ok(), true, `login ${username}: ${await login.text()}`);
  return { username, api, storage: await api.storageState() };
}

/** The admin creates a non-admin account and signs it in. */
export async function createAccount(admin, username) {
  const secret = password();
  const created = await admin.api.post('/api/auth/users', { data: { username, password: secret, is_admin: false } });
  assert.equal(created.ok(), true, `create ${username}: ${await created.text()}`);
  return signIn(username, secret);
}

/** A page for `account` at `size` ('desktop' | 'phone'), with the Notes panel open. `clock` installs Playwright's fake clock first. */
export async function openNotes(browser, account, size, { view = 'list', clock = false } = {}) {
  const context = await browser.newContext({ ...VIEWPORTS[size], storageState: account.storage });
  await context.addInitScript(mode => {
    localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
    localStorage.setItem('odysseus-notes-view', mode);
  }, view);
  const page = await context.newPage();
  if (clock) await page.clock.install();
  await page.goto(origin);
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator('#notes-pane .notes-pane-body').waitFor();
  return { context, page };
}

/** Reload and reopen the panel: what the account sees "after a refresh". */
export async function refreshNotes(page) {
  await page.reload();
  await page.waitForFunction(() => window.notesModule?.openPanel);
  await page.evaluate(() => window.notesModule.openPanel());
  await page.locator('#notes-pane .notes-pane-body').waitFor();
}

/** Poll `read` until `accept` holds. A fixed wait would be either flaky or slow. */
export async function until(read, accept, label, timeout = 10000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (accept(value)) return value;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}: ${JSON.stringify(value)?.slice(0, 600)}`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}

export async function getNote(api, id) {
  const response = await api.get(`/api/notes/${id}`);
  return { status: response.status(), note: response.ok() ? await response.json() : null };
}

export const untilNote = (api, id, accept, label) => until(() => getNote(api, id), r => accept(r.note, r.status), label).then(r => r.note);

/** Upload a PNG as `account`. The upload route allows 3 requests per IP per 10 s, so a 429 is retried until the window opens. */
export async function uploadPng(api, buffer, name = 'image.png') {
  const response = await until(
    () => api.post('/api/upload', { multipart: { files: { name, mimeType: 'image/png', buffer } } }),
    r => r.status() !== 429, 'the upload rate limit', 15000,
  );
  assert.equal(response.ok(), true, await response.text());
  const file = (await response.json()).files[0];
  return `/api/upload/${file.id}`;
}

/** A small valid PNG of one colour, drawn by the page. */
export async function pngOf(page, colour) {
  return Buffer.from(await page.evaluate(colour => {
    const canvas = document.createElement('canvas');
    canvas.width = 160; canvas.height = 100;
    const g = canvas.getContext('2d');
    g.fillStyle = colour; g.fillRect(0, 0, 160, 100);
    return canvas.toDataURL('image/png').split(',')[1];
  }, colour), 'base64');
}

/** Every image under `locator` has loaded and decoded to real pixels. */
export async function imagesLoaded(locator) {
  await locator.first().waitFor();
  return locator.evaluateAll(imgs => Promise.all(imgs.map(img => (img.complete ? Promise.resolve() : new Promise(r => { img.onload = img.onerror = r; }))
    .then(() => ({ src: img.getAttribute('src'), width: img.naturalWidth })))));
}

/** Run SQL against the disposable instance's database (never the live one: the harness passes its temp path). */
export function sqlite(sql, params = []) {
  const db = process.env.NOTES_DB_PATH;
  assert.ok(db && db.includes('ody-notes-native-'), `refusing to touch ${db}`);
  const script = 'import json,sqlite3,sys\nc=sqlite3.connect(sys.argv[1])\nrows=c.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall()\nc.commit()\nprint(json.dumps(rows))';
  const run = spawnSync(process.env.NOTES_TEST_PYTHON, ['-c', script, db, sql, JSON.stringify(params)], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout);
}

/** Completion requests the stub model received, oldest first. */
export function modelRequests() {
  const log = process.env.NOTES_STUB_LOG;
  if (!log || !fs.existsSync(log)) return [];
  return fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line).body);
}

export const toolNames = body => (body.tools || []).map(tool => tool.function?.name).filter(Boolean);
// Multipart form fields arrive with CRLF line breaks, as a browser sends them.
export const lastUserText = body => {
  const message = [...(body.messages || [])].reverse().find(m => m.role === 'user');
  const content = message?.content;
  return (Array.isArray(content) ? content.map(part => part.text || '').join(' ') : String(content || '')).replace(/\r\n/g, '\n');
};

// The flows open a note with `page.evaluate(id => void window.notesModule.openNote(id, …))`
// and then wait for the window: openNote reports nothing about the window (it
// polls for it on a timer), and awaiting its promise over CDP once failed with
// "Resulting promise was garbage collected". The window is what a flow checks.
export const tapOrClick = (mobile, locator) => (mobile ? locator.tap() : locator.click());

/** A task row's agent button asks about its item first; the run starts from that popover. */
export async function runItemAgent(mobile, button) {
  await tapOrClick(mobile, button);
  await tapOrClick(mobile, button.page().locator('.note-ask-menu [data-act="solve"]'));
}
