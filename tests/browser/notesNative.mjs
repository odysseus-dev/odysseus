import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const origin = process.env.ODYSSEUS_NOTES_RUNTIME_ORIGIN;
const evidence = process.env.NOTES_NATIVE_EVIDENCE_DIR;
if (evidence) fs.mkdirSync(evidence, { recursive: true });
const browser = await chromium.launch({ headless: true });
const credentials = { username: 'notes-reference', password: randomUUID() + 'Aa1!' };
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, hasTouch: mobile, isMobile: mobile });
    const page = await context.newPage();
    if (!mobile) {
      const setup = await context.request.post(`${origin}/api/auth/setup`, { data: credentials });
      assert.equal(setup.ok(), true, await setup.text());
    }
    const login = await context.request.post(`${origin}/api/auth/login`, { data: credentials });
    assert.equal(login.ok(), true, await login.text());
    const response = await context.request.post(`${origin}/api/notes`, { data: { title: mobile ? 'Mobile reference' : 'Desktop reference', content: 'Native API draft', note_type: 'note', label: 'reference' } });
    assert.equal(response.ok(), true, await response.text());
    const created = await response.json();
    assert.ok(created.id);
    assert.equal(created.owner, credentials.username);
    await page.goto(origin);
    await page.waitForFunction(() => window.notesModule?.openPanel);
    await page.evaluate(() => { localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'); window.notesModule.openPanel(); });
    const card = page.locator(`.note-card[data-note-id="${created.id}"]`);
    await card.waitFor();
    if (evidence) await page.screenshot({ path: path.join(evidence, mobile ? 'native-mobile.png' : 'native-desktop.png'), fullPage: true, animations: 'disabled' });
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
    if (mobile) await card.locator('.note-card-title').tap();
    else await card.locator('.note-card-title').click();
    if (mobile) await page.locator('.note-form-content-reader').tap({ position: { x: 5, y: 5 } });
    await page.locator('.note-form-content').fill('Saved by the native Notes application');
    await page.waitForFunction(id => !localStorage.getItem('odysseus-note-draft-' + id), created.id);
    const fetched = await context.request.get(`${origin}/api/notes`);
    const notes = (await fetched.json()).notes;
    assert.equal(notes.find(note => note.id === created.id).content, 'Saved by the native Notes application');
    await page.evaluate(() => window.notesModule.closePanel());
    await page.locator('#notes-pane').waitFor({ state: 'detached' });
    await context.close();
    console.log(JSON.stringify({ native: true, mobile, noteId: created.id, crud: true, selectedContext: true }));
  }
  const smoke = spawnSync(process.env.NOTES_TEST_PYTHON, ['-m', 'pytest', '-q', 'tests/smoke/test_notes_smoke.py'], {
    env: { ...process.env, APP_PORT: new URL(origin).port, ODYSSEUS_ADMIN_USER: credentials.username, ODYSSEUS_ADMIN_PASSWORD: credentials.password },
    encoding: 'utf8', timeout: 90000,
  });
  assert.equal(smoke.status, 0, smoke.stdout + smoke.stderr);
  console.log('PASS: original Notes release smoke test against the disposable authenticated instance');
} finally { await browser.close(); }
