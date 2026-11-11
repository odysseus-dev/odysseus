import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';

const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
const evidence = process.env.NOTES_EVIDENCE_DIR;
if (evidence) fs.mkdirSync(evidence, { recursive: true });
const baseline = process.env.NOTES_BASELINE === '1';
const browser = await chromium.launch({ headless: true });
try {
  for (const device of ['desktop', 'mobile', 'tablet']) {
    const mobile = device === 'mobile';
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : device === 'tablet' ? { width: 1024, height: 800 } : { width: 1440, height: 1000 }, hasTouch: device !== 'desktop', isMobile: mobile });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack));
    if (baseline) {
      const body = execFileSync('git', ['show', '1cc56cb9:static/js/notes.js'], { encoding: 'utf8' });
      await page.route('**/static/js/notes.js*', route => route.fulfill({ contentType: 'text/javascript', body }));
    }
    const requests = [];
    const notes = [
      { id: 'plain', title: 'Reference note', note_type: 'note', content: 'Visit https://example.org and keep this draft.', label: 'work', sort_order: 0 },
      { id: 'todo', title: 'Reference checklist', note_type: 'todo', items: [{ text: 'First task', done: false }, { text: 'Second task', done: true }], sort_order: 1 },
      { id: 'goal', title: 'Reference goal', note_type: 'goal', content: 'A bounded goal', items: [{ text: 'Next step', done: false }], sort_order: 2 },
    ];
    await page.route('**/api/**', async route => {
      const request = route.request();
      requests.push({ url: new URL(request.url()).pathname, method: request.method(), body: request.postData() });
      const id = new URL(request.url()).pathname.split('/').at(-1);
      if (request.method() === 'PUT') Object.assign(notes.find(n => n.id === id) || {}, request.postDataJSON());
      await route.fulfill({ json: request.method() === 'GET' ? { notes } : notes.find(n => n.id === id) || {} });
    });
    await page.goto(`${origin}/static/js/panels.js`);
    const shell = fs.readFileSync(new URL('../../static/index.html', import.meta.url), 'utf8');
    const styles = [...shell.matchAll(/<link[^>]+rel="stylesheet"[^>]*>/g)].map(m => m[0]).join('\n');
    await page.setContent(`<html><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}</head><body><div id="chat-container"></div><div id="toast"></div><button id="tool-notes-btn"><span id="notes-new-note-btn"></span></button><button id="rail-notes"></button></body></html>`);
    await page.evaluate(() => {
      localStorage.setItem('odysseus-notes-first-open-hint-v1', '1');
      window.referenceIntervals = [];
      window.referenceMouseups = new Set();
      const add = window.addEventListener.bind(window), remove = window.removeEventListener.bind(window);
      window.addEventListener = (type, fn, options) => { if (type === 'mouseup' && fn.name === '_end') window.referenceMouseups.add(fn); add(type, fn, options); };
      window.removeEventListener = (type, fn, options) => { if (type === 'mouseup' && fn.name === '_end') window.referenceMouseups.delete(fn); remove(type, fn, options); };
      const interval = window.setInterval.bind(window);
      window.setInterval = (fn, ms, ...args) => { const id = interval(fn, ms, ...args); window.referenceIntervals.push({ id, ms }); return id; };
    });
    const publicAPI = await page.evaluate(async () => {
      const mod = await import('/static/js/notes.js?v=20260911notesselectioncancel1');
      const again = await import('/static/js/notes.js?v=20260911notesselectioncancel1');
      window.referenceNotes = mod;
      mod.openPanel();
      return { names: Object.keys(mod).sort(), defaults: Object.keys(mod.default).sort(), arities: Object.fromEntries(Object.keys(mod).filter(k => k !== 'default').map(k => [k, mod[k].length])), asyncMethods: Object.keys(mod).filter(k => mod[k]?.constructor?.name === 'AsyncFunction').sort(), singleton: again === mod, bridge: window.notesModule === mod.default, aliases: mod.openNotes === mod.openPanel && mod.closeNotes === mod.closePanel && mod.isNotesOpen === mod.isPanelOpen && mod.default.openNotes === mod.openPanel };
    });
    assert.equal(publicAPI.singleton && publicAPI.bridge && publicAPI.aliases, true);
    assert.deepEqual(publicAPI.names, ['closeNotes', 'closePanel', 'default', 'dismissFiredReminderDot', 'isNotesOpen', 'isPanelOpen', 'newNote', 'openNote', 'openNotes', 'openPanel', 'refreshDueBadge', 'togglePanel']);
    assert.deepEqual(publicAPI.defaults, ['closeNotes', 'closePanel', 'isNotesOpen', 'isPanelOpen', 'newNote', 'openNote', 'openNotes', 'openPanel', 'refreshDueBadge', 'togglePanel']);
    assert.deepEqual(publicAPI.arities, { closeNotes: 1, closePanel: 1, dismissFiredReminderDot: 0, isNotesOpen: 0, isPanelOpen: 0, newNote: 0, openNote: 1, openNotes: 0, openPanel: 0, refreshDueBadge: 0, togglePanel: 0 });
    assert.deepEqual(publicAPI.asyncMethods, ['openNote', 'refreshDueBadge']);
    await page.locator('.note-card').first().waitFor();
    assert.equal(await page.locator('.note-card').count(), 3);
    if (evidence) await page.screenshot({ path: path.join(evidence, `notes-${device}.png`), fullPage: true, animations: 'disabled' });
    await page.locator('#notes-search').fill('checklist');
    assert.equal(await page.locator('.note-card').count(), 1);
    await page.locator('#notes-search').fill('');
    await page.locator('#notes-select-btn').click();
    await page.locator('.note-card-cb').first().check({ force: mobile });
    await page.locator('#notes-send-agent').click();
    assert.equal(await page.locator('.note-card-selectmode').count(), 3, 'rejected selection stays selected');
    await page.evaluate(() => window.addEventListener('odysseus:notes-context-selected', e => { window.referenceSelected = e.detail.noteIds; e.detail.accepted = true; }, { once: true }));
    await page.locator('#notes-send-agent').click();
    assert.equal(await page.locator('.note-card-selectmode').count(), 0);
    assert.equal((await page.evaluate(() => window.referenceSelected)).length, 1);
    await page.locator('#notes-search').fill('checklist');
    await page.evaluate(() => window.referenceNotes.closePanel());
    await page.locator('#notes-pane').waitFor({ state: 'detached' });
    await page.evaluate(() => window.referenceNotes.openPanel());
    await page.locator('.note-card').first().waitFor();
    assert.equal(await page.locator('#notes-search').inputValue(), '');
    assert.equal(await page.locator('.note-card').count(), 3);
    if (!baseline && device !== 'desktop') {
      const beginTouch = () => page.evaluate(() => {
        const card = document.querySelector('.note-card');
        const rect = card.getBoundingClientRect();
        const touch = new Touch({ identifier: 1, target: card, clientX: rect.left + 5, clientY: rect.top + 5 });
        card.dispatchEvent(new TouchEvent('touchstart', { touches: [touch], bubbles: true }));
      });
      if (device === 'tablet') {
        await beginTouch();
        await page.waitForFunction(() => document.querySelector('.note-card.dragging'));
        await page.evaluate(() => document.querySelector('.note-card.dragging').dispatchEvent(new TouchEvent('touchcancel', { bubbles: true })));
        await page.locator('#notes-search').fill('checklist');
        await page.locator('#notes-search').fill('');
        await beginTouch();
        await page.waitForFunction(() => document.querySelector('.note-card.dragging'));
      } else {
        await beginTouch(); // Close before the selection hold timer can fire.
      }
      await page.evaluate(() => window.referenceNotes.closePanel());
      await page.locator('#notes-pane').waitFor({ state: 'detached' });
      await page.waitForTimeout(500);
      assert.equal(await page.evaluate(() => document.documentElement.style.touchAction), '');
      await page.evaluate(() => window.referenceNotes.openPanel());
      await page.locator('.note-card').first().waitFor();
      assert.equal(await page.locator('.note-card-selectmode').count(), 0);
    }
    if (mobile) await page.locator('[data-note-id="plain"] .note-card-title').tap();
    else await page.locator('[data-note-id="plain"] .note-card-title').click();
    if (mobile) await page.locator('.note-form-content-reader').tap({ position: { x: 5, y: 5 } });
    await page.locator('.note-form-content').fill('Recovered draft');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('odysseus-note-draft-plain')).content), 'Recovered draft');
    await page.waitForFunction(() => !localStorage.getItem('odysseus-note-draft-plain'));
    assert.ok(requests.some(r => r.method === 'PUT' && JSON.parse(r.body).content === 'Recovered draft'));
    await page.locator('.note-form-type-pill[data-type="draw"]').click();
    const canvas = page.locator('.note-form-canvas');
    const box = await canvas.boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + 25, box.y + 25);
    await page.mouse.down();
    await page.mouse.move(box.x + 55, box.y + 55, { steps: 5 });
    await page.mouse.up();
    await page.locator('.note-form-draw-undo').waitFor();
    assert.equal(await page.locator('.note-form-draw-undo').isDisabled(), false);
    await page.locator('.note-form-draw-undo').click();
    assert.equal(await page.locator('.note-form-draw-undo').isDisabled(), true);
    await page.locator('.note-form-type-pill[data-type="note"]').click();
    if (!baseline) await page.waitForFunction(() => window.referenceMouseups.size === 0);
    await page.locator('.note-form-remind-btn').click();
    await page.locator('.note-reminder-menu').waitFor();
    await page.evaluate(() => window.referenceNotes.closePanel());
    await page.locator('#notes-pane').waitFor({ state: 'detached' });
    if (!baseline) {
      assert.equal(await page.locator('.note-reminder-menu').count(), 0);
      assert.equal(await page.evaluate(() => window.referenceIntervals.filter(t => t.ms === 30000).length), 1);
      const identity = await page.evaluate(async () => {
        const feature = await import('/static/js/features/notes/index.js');
        const unversioned = await import('/static/js/notes.js');
        return feature.default === window.referenceNotes.default && unversioned.default === feature.default && feature.openPanel === window.referenceNotes.openPanel;
      });
      assert.equal(identity, true);
    }
    assert.deepEqual(errors, []);
    await context.close();
    console.log(JSON.stringify({ device, mobile, ...publicAPI, requests: requests.length }));
  }
} finally { await browser.close(); }
