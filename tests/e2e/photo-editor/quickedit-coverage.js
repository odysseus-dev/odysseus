#!/usr/bin/env node
// Quick Edit coverage audit runner (standalone; not a Playwright spec).
//
// Drives the live editor agent for every prompt in CASES and grades each by
// whether an expected control op appears in the executed-step trace.
//
// Usage:
//   BASE_URL=http://127.0.0.1:7011 SESSION_FILE=/path/sessions.json \
//   NODE_PATH=/path/node_modules node quickedit-coverage.js
//
//   PROMPTS=1,2,3   limit to a subset
//   OUT=/tmp/qe      output dir for the JSON report
//   HEADFUL=1        show the browser
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:7011';
const OUT = process.env.OUT || '/tmp/opencode/qe-coverage';
const SESSION_FILE = process.env.SESSION_FILE || '/home/pewds/odysseus-cookbook-fresh/data/sessions.json';

const CASES = [
  { id: 1, group: 'Fill', prompt: 'fill the white area with red', expect: ['fill.flood', 'selection.fill'] },
  { id: 2, group: 'Fill', prompt: 'flood fill the sky with blue', expect: ['fill.flood', 'selection.fill', 'selection.sky'] },
  { id: 3, group: 'Text', prompt: 'make the text bigger', expect: ['text.edit'], setup: 'text' },
  { id: 4, group: 'Text', prompt: 'make the title red', expect: ['text.edit'], setup: 'text' },
  { id: 5, group: 'Text', prompt: 'add the title "Summer"', expect: ['text.add'] },
  { id: 6, group: 'Layer style', prompt: 'add a drop shadow to the title', expect: ['layers.addStyle'], setup: 'text' },
  { id: 7, group: 'Layer style', prompt: 'add a stroke to the layer', expect: ['layers.addStyle'] },
  { id: 8, group: 'Layer style', prompt: 'add a colour overlay', expect: ['layers.addStyle'] },
  { id: 9, group: 'Align', prompt: 'align the logo to the centre', expect: ['layers.align'] },
  { id: 10, group: 'Align', prompt: 'align the layer to the left', expect: ['layers.align'] },
  { id: 11, group: 'Layer transform', prompt: 'rotate only this layer 90 degrees', expect: ['layer.rotate'] },
  { id: 12, group: 'Layer transform', prompt: 'flip this layer horizontally', expect: ['layer.flip'] },
  { id: 13, group: 'Document transform', prompt: 'rotate the whole image 90 degrees', expect: ['document.rotate'] },
  { id: 14, group: 'Document transform', prompt: 'flip the canvas vertically', expect: ['document.flip'] },
  { id: 15, group: 'Crop', prompt: 'crop to a square', expect: ['document.crop'] },
  { id: 16, group: 'Crop', prompt: 'make this 16:9', expect: ['document.crop'] },
  { id: 17, group: 'Crop', prompt: 'crop to 9:16 for a story', expect: ['document.crop'] },
  { id: 18, group: 'Extend', prompt: 'extend the canvas to 16:9', expect: ['ai.outpaint'] },
  { id: 19, group: 'Clipboard', prompt: 'copy the selection', expect: ['selection.copy'], setup: 'select' },
  { id: 20, group: 'Clipboard', prompt: 'paste', expect: ['selection.paste'], setup: 'select' },
  { id: 21, group: 'Selection', prompt: 'select the subject', expect: ['selection.subject'] },
  { id: 22, group: 'Selection', prompt: 'select the sky', expect: ['selection.sky'] },
  { id: 23, group: 'Selection', prompt: 'select all', expect: ['selection.selectAll'] },
  { id: 24, group: 'Selection', prompt: 'invert the selection', expect: ['selection.invert'] },
  { id: 25, group: 'Background', prompt: 'remove the background', expect: ['ai.removeBackground', 'selection.background'] },
  { id: 26, group: 'Background', prompt: 'blur the background', expect: ['filters.applyMasked', 'filters.apply'] },
  { id: 27, group: 'Adjustment', prompt: 'make it brighter', expect: ['photo.autoTone', 'adjustments.apply', 'shadows-highlights'] },
  { id: 28, group: 'Adjustment', prompt: 'make it black and white', expect: ['adjustments.apply', 'black-white'], assert: 'black' },
  { id: 29, group: 'Adjustment', prompt: 'warm it up', expect: ['adjustments.apply', 'photo.autoTone', 'white-balance'] },
  { id: 30, group: 'Adjustment', prompt: 'make it punchy', expect: ['photo.autoTone', 'adjustments.apply', 'vibrance'] },
  { id: 31, group: 'Adjustment', prompt: 'make it moody and cinematic', expect: ['adjustments.apply', 'white-balance'] },
  { id: 32, group: 'Restore', prompt: 'restore this old photo', expect: ['photo.autoTone', 'adjustments.apply'] },
  { id: 33, group: 'Retouch', prompt: 'smooth the skin', expect: ['retouch.skin', 'adjustments.apply'] },
  { id: 34, group: 'Retouch', prompt: 'whiten the teeth', expect: ['retouch.teeth', 'adjustments.apply'] },
  { id: 35, group: 'Retouch', prompt: 'remove the blemishes', expect: ['retouch.blemish', 'retouch.skin', 'adjustments.apply'] },
  { id: 36, group: 'Filter', prompt: 'sharpen it', expect: ['filters.apply', 'adjustments.apply', 'photo.autoTone'] },
  { id: 37, group: 'Filter', prompt: 'add a vignette', expect: ['filters.apply', 'adjustments.apply'] },
  { id: 38, group: 'Layers', prompt: 'add a new layer', expect: ['layers.addRaster', 'layers.addAdjustment'] },
  { id: 39, group: 'Layers', prompt: 'duplicate this layer', expect: ['layers.duplicate'] },
  { id: 40, group: 'Layers', prompt: 'flatten the image', expect: ['layers.flatten'] },
  { id: 41, group: 'Layers', prompt: 'merge the layer down', expect: ['layers.mergeDown'] },
  { id: 42, group: 'Document', prompt: 'save as PSD', expect: ['document.savePsd'] },
  { id: 43, group: 'Document', prompt: 'resize to 1920 wide', expect: ['document.resizeCanvas'] },
  { id: 44, group: 'AI edit', prompt: 'remove the person on the left', expect: ['ai.generativeFill'] },
  { id: 45, group: 'AI generate', prompt: 'add a full moon in the sky', expect: ['ai.generativeFill'] },
];

function authToken() {
  const data = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
  const now = Date.now() / 1000;
  for (const [token, info] of Object.entries(data)) {
    if (info.expiry == null || info.expiry > now) return token;
  }
  throw new Error(`no unexpired session in ${SESSION_FILE}`);
}

function selected() {
  const raw = process.env.PROMPTS;
  if (!raw) return CASES;
  const want = new Set(raw.split(',').map(s => Number(s.trim())));
  return CASES.filter(c => want.has(c.id));
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: !process.env.HEADFUL });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, serviceWorkers: 'block' });
  await context.addCookies([{ name: 'odysseus_session', value: authToken(), domain: new URL(BASE).hostname, path: '/' }]);
  const page = await context.newPage();
  page.on('pageerror', e => console.log('  [pageerror]', String(e).slice(0, 240)));

  const results = [];
  for (const c of selected()) {
    let record = { id: c.id, group: c.group, prompt: c.prompt, expect: c.expect, ops: [], pass: false, error: null };
    try {
      await page.goto(BASE + '/?nodraft=1', { waitUntil: 'domcontentloaded' });
      await page.locator('#tool-gallery-btn').waitFor({ state: 'attached', timeout: 30000 });
      await page.evaluate(async () => {
        const gallery = await import('/static/js/gallery.js?v=20260830editor4');
        gallery.openGallery();
      });
      await page.locator('#gallery-editor-tab').first().waitFor({ state: 'attached', timeout: 30000 });
      await page.evaluate(() => document.getElementById('gallery-editor-tab')?.click());
      await page.evaluate(async () => {
        const editor = await import('/static/js/galleryEditor.js?qe=' + Date.now());
        window.__geNoDraft = true;
        if (editor.isEditorOpen()) editor.closeEditor({ force: true });
        editor.openEditor(null, null, { w: 640, h: 480 }, 'QE ' + Date.now());
      });
      await page.locator('.ge-main-canvas').waitFor({ state: 'visible', timeout: 30000 });
      await page.waitForFunction(() => !!window.geEditorControl, null, { timeout: 20000 });

      if (c.setup) {
        await page.evaluate(async (setup) => {
          const control = window.geEditorControl;
          if (setup === 'text') await control.invoke('text.add', { text: 'Summer', x: 80, y: 80, size: 64 });
          else if (setup === 'select') await control.invoke('selection.rectangle', { x: 100, y: 100, width: 200, height: 160 });
        }, c.setup);
      }

      const trace = await page.evaluate(async (prompt) => {
        const ops = [];
        const control = window.geEditorControl;
        const orig = control.invoke.bind(control);
        control.invoke = async (op, params) => { ops.push({ op, params }); return orig(op, params); };
        let result = null, error = null;
        try {
          result = window.geQuickEditRun
            ? await window.geQuickEditRun(prompt, {})
            : await window.geEditorAgent.run(prompt, {});
        }
        catch (err) { error = String((err && err.message) || err); }
        finally { control.invoke = orig; }
        return { ops, reply: result && (result.reply || result.message), error };
      }, c.prompt);

      record.ops = trace.ops.map(o => o.op);
      record.reply = trace.reply;
      record.error = trace.error;
      const opHit = c.expect.some(e => record.ops.some(o => o === e || o.startsWith(e + '.')));
      const assertHit = !c.assert || trace.ops.some(o => JSON.stringify(o.params || {}).includes(c.assert));
      record.pass = opHit && assertHit;
    } catch (err) {
      record.error = String((err && err.message) || err);
    }
    results.push(record);
    console.log(`QE ${record.pass ? 'PASS' : 'FAIL'} #${c.id} [${c.group}] "${c.prompt}" -> ${record.ops.join(', ') || record.error}`);
  }

  const pass = results.filter(r => r.pass).length;
  fs.writeFileSync(path.join(OUT, 'quickedit-coverage.json'), JSON.stringify({ base: BASE, pass, total: results.length, results }, null, 2));
  console.log(`QUICKEDIT_COVERAGE ${pass}/${results.length} passed -> ${path.join(OUT, 'quickedit-coverage.json')}`);
  await browser.close();
  process.exit(0);
})().catch(err => { console.error('FAIL', err); process.exit(1); });
