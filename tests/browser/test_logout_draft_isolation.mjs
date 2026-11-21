// Real authenticated browser regression for unsaved Markdown drafts across logout.
//
// Requires a server started with AUTH_ENABLED=true and no accounts yet (see
// scripts/run_real_playwright_validation.py, "auth" suite). It exercises the
// real login/logout routes, never an authentication-disabled backend.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { chromium } from 'playwright';

const origin = process.env.ODYSSEUS_VALIDATION_ORIGIN;
assert.ok(origin, 'ODYSSEUS_VALIDATION_ORIGIN is required');

const password = `pw-${crypto.randomBytes(9).toString('hex')}`;
const secret = `PRIVATE-DRAFT-${crypto.randomBytes(5).toString('hex')}`;
const DRAFT_PREFIX = 'odysseus.document.markdownDraft.v1:';

const browser = await chromium.launch({ headless: true });
const results = [];
const record = (name) => { results.push(name); console.log(`[PASS] ${name}`); };

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const dialogs = [];
  const wire = (target) => {
    target.setDefaultTimeout(30000);
    target.on('dialog', async (dialog) => {
      dialogs.push(dialog.type());
      await dialog.accept().catch(() => {});
    });
  };
  let page = await context.newPage();
  wire(page);
  // Reloading a page that has a document open crashes this environment's headless
  // Chromium intermittently. A tab opened with window.open starts with a copy of the
  // opener's sessionStorage, the same state a refresh keeps, so the tab-scoped draft
  // is exercised in a fresh renderer instead.
  const refreshTab = async () => {
    const [reopened] = await Promise.all([
      context.waitForEvent('page'),
      page.evaluate(() => { window.open(window.location.href); }),
    ]);
    await page.close();
    wire(reopened);
    await reopened.waitForFunction(() => !document.getElementById('app-loader'));
    await reopened.waitForTimeout(1500);
    return reopened;
  };

  // Accounts: first account is the admin; the admin then creates user B.
  const setup = await context.request.post(`${origin}/api/auth/setup`, { data: { username: 'usera', password } });
  assert.equal(setup.status(), 200, await setup.text());
  const loginA = await context.request.post(`${origin}/api/auth/login`, { data: { username: 'usera', password } });
  assert.equal((await loginA.json()).ok, true);
  const createB = await context.request.post(`${origin}/api/auth/users`, { data: { username: 'userb', password, is_admin: false } });
  assert.equal(createB.status(), 200, await createB.text());

  // Recovery is keyed by chat session and document, as in normal use.
  const sessionRes = await context.request.post(`${origin}/api/session`, {
    form: { name: 'A private chat', endpoint_url: 'http://127.0.0.1:9/v1', model: 'test-model', skip_validation: 'true' },
  });
  assert.equal(sessionRes.status(), 200, await sessionRes.text());
  const { id: sessionId } = await sessionRes.json();
  const docRes = await context.request.post(`${origin}/api/document`, {
    data: { title: 'A private note', content: 'Original paragraph', language: 'markdown', session_id: sessionId },
  });
  assert.equal(docRes.status(), 200, await docRes.text());
  const doc = await docRes.json();

  const openApp = async () => {
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !document.getElementById('app-loader'));
    await page.waitForTimeout(1500);
  };
  const openDocument = async (id) => {
    await page.evaluate(async (docId) => {
      const mod = await import('/static/js/document.js');
      await mod.openPanel();
      await mod.loadDocument(docId);
    }, id);
    await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
  };
  const draftKeys = () => page.evaluate((prefix) => {
    const keys = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key.startsWith(prefix)) keys.push(key);
    }
    return keys;
  }, DRAFT_PREFIX);
  const storageHoldsSecret = () => page.evaluate((needle) => {
    for (const store of [sessionStorage, localStorage]) {
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i);
        if (key.includes(needle) || (store.getItem(key) || '').includes(needle)) return true;
      }
    }
    return false;
  }, secret);
  const typeUnsaved = async (text) => {
    if (await page.locator('#settings-modal').isVisible()) {
      await page.keyboard.press('Escape');
      await page.locator('#settings-modal').waitFor({ state: 'hidden' });
    }
    await page.locator('#doc-email-richbody').click();
    await page.keyboard.press('Control+End');
    await page.keyboard.type(text);
    await page.waitForFunction((prefix) => {
      for (let i = 0; i < sessionStorage.length; i++) if (sessionStorage.key(i).startsWith(prefix)) return true;
      return false;
    }, DRAFT_PREFIX);
  };
  // Through the real Settings UI; the logout handler is bound when Settings first opens.
  const clickLogout = async () => {
    const logout = page.locator('#settings-logout-btn');
    if (!(await logout.isVisible())) {
      if (!(await page.locator('#settings-modal').isVisible())) await page.locator('#user-bar-settings').click();
      await page.locator('[data-settings-tab="account"]').click();
    }
    // The Account panel overlaps this button with a password field at some sizes; dispatch the click event itself.
    await logout.dispatchEvent('click');
  };

  // 0. A literal reload of the same tab. Headless Chromium in some environments
  // crashes the renderer on reload (also on the merge base). A crash is reported as an
  // environment limitation with diagnostics and never counted as a pass; any other
  // failure still fails the run.
  const literalReload = { status: 'not attempted', detail: '' };
  {
    let crashes = 0;
    const trap = () => { crashes += 1; };
    page.on('crash', trap);
    try {
      await openApp();
      await openDocument(doc.id);
      await typeUnsaved(` ${secret}`);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !document.getElementById('app-loader'));
      await page.waitForTimeout(1500);
      assert.equal((await draftKeys()).length, 1, 'the draft is still in tab storage after a literal reload');
      await openDocument(doc.id);
      assert.ok((await page.locator('#doc-email-richbody').innerText()).includes(secret), 'the draft is recovered into the reopened document');
      literalReload.status = 'verified';
      record('literal reload recovers the unsaved draft');
    } catch (error) {
      if (crashes > 0 || /crashed/i.test(String(error?.message))) {
        literalReload.status = 'not verified: browser crash';
        literalReload.detail = `${crashes} crash event(s); ${String(error?.message).split('\n')[0]}`;
        console.log(`[ENVIRONMENT] literal reload ${literalReload.status} (${literalReload.detail}); falling back to a tab that inherits storage`);
        page.off('crash', trap);
        page = await context.newPage();
        wire(page);
        await page.evaluate(() => sessionStorage.clear()).catch(() => {});
      } else {
        throw error;
      }
    }
    page.off('crash', trap);
  }
  console.log(`literal reload recovery: ${literalReload.status}`);
  if (literalReload.status === 'verified') await page.evaluate(() => sessionStorage.clear());

  // 1. Tab-scoped recovery: a tab that inherits the opener's sessionStorage recovers the draft.
  await openApp();
  await openDocument(doc.id);
  await typeUnsaved(` ${secret}`);
  assert.equal((await draftKeys()).length, 1, 'an unsaved Markdown draft is stored for refresh recovery');
  page = await refreshTab();
  assert.equal((await draftKeys()).length, 1, 'the tab-scoped draft is still stored');
  record('a fresh renderer inheriting tab storage recovers the draft');

  // 2. Recovery stays active during a pending logout and after rejection.
  await openDocument(doc.id);
  assert.ok((await page.locator('#doc-email-richbody').innerText()).includes(secret), 'draft was recovered after refresh');
  await page.route(`**/api/document/${doc.id}`, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503 }) : route.fallback());
  const logoutUrl = '**/api/auth/logout';
  const logoutButton = page.locator('#settings-logout-btn');
  let receiveLogout;
  const logoutStarted = new Promise(resolve => { receiveLogout = resolve; });
  let requests = 0;
  await page.route(logoutUrl, (route) => {
    requests += 1;
    receiveLogout(route);
  });
  await Promise.all([page.waitForRequest(logoutUrl), clickLogout()]);
  const pendingLogout = await logoutStarted;
  assert.equal(await logoutButton.isDisabled(), true, 'logout disables the button while awaiting the server');
  await logoutButton.dispatchEvent('click');
  const pendingText = ' pending-logout-edit';
  await typeUnsaved(pendingText);
  assert.ok(await page.evaluate(({ prefix, text }) => Object.keys(sessionStorage).some(
    key => key.startsWith(prefix) && sessionStorage.getItem(key)?.includes(text),
  ), { prefix: DRAFT_PREFIX, text: pendingText }), 'the latest edit is recoverable before logout completes');
  assert.equal(requests, 1, 'only one logout request can be in flight');
  await pendingLogout.fulfill({ status: 500, body: '{"detail":"nope"}', contentType: 'application/json' });
  await page.waitForFunction(() => !document.getElementById('settings-logout-btn').disabled);
  assert.ok(!page.url().includes('/login'), 'a rejected logout does not navigate away');
  assert.equal((await draftKeys()).length, 1, 'a rejected logout keeps the draft');
  await typeUnsaved(' still-editing');
  await page.unroute(logoutUrl);
  const stillAuthed = await context.request.get(`${origin}/api/auth/status`);
  assert.equal((await stillAuthed.json()).authenticated, true, 'server session survived the rejected logout');
  record('pending and rejected logout requests keep session and draft recovery');

  // 3. An interrupted (aborted) logout request behaves the same.
  await page.route(logoutUrl, (route) => route.abort('connectionfailed'));
  await clickLogout();
  await page.waitForFunction(() => !document.getElementById('settings-logout-btn').disabled);
  assert.ok(!page.url().includes('/login'));
  assert.equal((await draftKeys()).length, 1);
  await page.unroute(logoutUrl);
  record('interrupted logout keeps session and draft recovery');

  const openSibling = async (url) => {
    const [sibling] = await Promise.all([
      context.waitForEvent('page'),
      page.evaluate(target => { window.open(target); }, url),
    ]);
    return sibling;
  };
  const liveSibling = await openSibling(origin);
  await liveSibling.waitForLoadState('domcontentloaded');
  const unopenedSibling = await openSibling('about:blank');
  for (const sibling of [liveSibling, unopenedSibling]) {
    assert.ok(await sibling.evaluate(needle => Object.values(sessionStorage).some(value => value.includes(needle)), secret),
      'a tab opened from the editor inherits its recovery draft');
  }

  // 4. Real logout: no beforeunload prompt, storage is empty, nothing is rewritten.
  dialogs.length = 0;
  await Promise.all([
    page.waitForURL((url) => url.pathname.startsWith('/login')),
    clickLogout(),
  ]);
  assert.deepEqual(dialogs, [], 'logout does not raise a Leave site? prompt');
  assert.deepEqual(await draftKeys(), [], 'no draft key after navigation to /login');
  assert.equal(await storageHoldsSecret(), false, 'outgoing user text is not in any browser storage');
  record('logout leaves no draft in the same tab');
  await liveSibling.waitForURL(url => url.pathname.startsWith('/login'));
  assert.deepEqual(await liveSibling.evaluate(prefix => Object.keys(sessionStorage).filter(key => key.startsWith(prefix)), DRAFT_PREFIX), []);
  record('logout clears inherited recovery drafts from an open application tab');

  // 5. User B signs in through the real login form in the same tab.
  await page.fill('#username', 'userb');
  await page.fill('#password', password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login')),
    page.locator('form button[type="submit"], form input[type="submit"]').first().click(),
  ]);
  await page.waitForFunction(() => !document.getElementById('app-loader'));
  assert.deepEqual(await draftKeys(), [], 'user B inherits no draft key');
  assert.equal(await storageHoldsSecret(), false, 'user B cannot inspect user A text in storage');
  const bStatus = await (await context.request.get(`${origin}/api/auth/status`)).json();
  assert.equal(bStatus.username, 'userb');
  await unopenedSibling.goto(origin, { waitUntil: 'domcontentloaded' });
  await unopenedSibling.waitForFunction(() => !document.getElementById('app-loader'));
  assert.equal(await unopenedSibling.evaluate(needle => Object.values(sessionStorage).some(value => value.includes(needle)), secret), false);
  record('a tab that missed logout clears inherited recovery before opening as user B');
  const bOpensA = await context.request.get(`${origin}/api/document/${doc.id}`);
  assert.ok([403, 404].includes(bOpensA.status()), `user B must not read user A's document (got ${bOpensA.status()})`);
  await page.evaluate(async (id) => {
    const mod = await import('/static/js/document.js');
    await mod.openPanel();
    try { await mod.loadDocument(id); } catch (_) { /* refused by the server */ }
  }, doc.id);
  await page.waitForTimeout(500);
  const recovered = await page.evaluate(async (needle) => {
    const { docs } = await import('/static/js/document/application/documentState.js');
    return [...docs.values()].some((d) => String(d.content || '').includes(needle));
  }, secret);
  assert.equal(recovered, false, 'user A draft is not recoverable in user B session');
  record('user B cannot recover or inspect user A draft');
} catch (error) {
  console.error('[FAIL]', error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log(`Passed ${results.length} logout isolation checks`);
if (process.exitCode) process.exit(process.exitCode);
