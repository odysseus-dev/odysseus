#!/usr/bin/env node
// Quick Edit benchmark v2 runner (standalone; not a Playwright spec).
//
// For each fixed prompt: import a fixed photo, run the editor agent, and
// record the executed op trace, latency (ms), undo-group count (history.mark
// delta), new layers, and before/after screenshots. Grading (eye + gpt-5.4)
// happens separately on the saved images (see quickedit-bench-grade.js).
//
// Usage:
//   BASE_URL=http://127.0.0.1:7011 SESSION_FILE=/path/sessions.json \
//   NODE_PATH=/path/node_modules node quickedit-bench.js
//
//   CATS=structural,looks   limit to categories
//   PROMPTS=1,2,3           limit to ids
//   OUT=/tmp/opencode/qe-bench
//   PHOTOS=/tmp/opencode    where the fixed jpg fixtures live
//   HEADFUL=1
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:7011';
const OUT = process.env.OUT || '/tmp/opencode/qe-bench';
const PHOTOS = process.env.PHOTOS || '/tmp/opencode';
const SESSION_FILE = process.env.SESSION_FILE || '/home/pewds/odysseus-cookbook-fresh/data/sessions.json';

// photo fixtures (in PHOTOS/): real-person.jpg pic-64.jpg pic-1015.jpg
// pic-1018.jpg real-sky.jpg real-city.jpg real-mountainlake.jpg
// real-beachsunset.jpg old-faded.jpg food.jpg night.jpg pic-1016.jpg
// pic-1027.jpg demo-street.jpg
const P = {
  portrait: 'real-person.jpg',
  portrait2: 'pic-64.jpg',
  fjord: 'pic-1015.jpg',
  hills: 'pic-1018.jpg',
  sky: 'real-sky.jpg',
  city: 'real-city.jpg',
  lake: 'real-mountainlake.jpg',
  sunset: 'real-beachsunset.jpg',
  old: 'old-faded.jpg',
  canyon: 'pic-1016.jpg',
  face: 'pic-1027.jpg',
  street: 'demo-street.jpg',
};

const CASES = [
  // ---- structural (30) ----
  { id: 1, cat: 'structural', photo: P.sky, prompt: 'crop to a square' },
  { id: 2, cat: 'structural', photo: P.sky, prompt: 'make this 16:9' },
  { id: 3, cat: 'structural', photo: P.portrait, prompt: 'crop to 9:16 for a story' },
  { id: 4, cat: 'structural', photo: P.fjord, prompt: 'crop to 4:5' },
  { id: 5, cat: 'structural', photo: P.city, prompt: 'trim the empty borders' },
  { id: 6, cat: 'structural', photo: P.hills, prompt: 'resize to 1920 wide' },
  { id: 7, cat: 'structural', photo: P.fjord, prompt: 'resize to 1080 tall' },
  { id: 8, cat: 'structural', photo: P.hills, prompt: 'rotate the whole image 90 degrees' },
  { id: 9, cat: 'structural', photo: P.city, prompt: 'rotate the canvas 180' },
  { id: 10, cat: 'structural', photo: P.fjord, prompt: 'flip the canvas horizontally' },
  { id: 11, cat: 'structural', photo: P.sky, prompt: 'flip the whole image vertically' },
  { id: 12, cat: 'structural', photo: P.hills, prompt: 'rotate only this layer 90 degrees' },
  { id: 13, cat: 'structural', photo: P.city, prompt: 'flip this layer horizontally' },
  { id: 14, cat: 'structural', photo: P.sky, prompt: 'add the title "SUMMER"' },
  { id: 15, cat: 'structural', photo: P.fjord, setup: 'text', prompt: 'make the text bigger' },
  { id: 16, cat: 'structural', photo: P.city, setup: 'text', prompt: 'make the text smaller' },
  { id: 17, cat: 'structural', photo: P.hills, setup: 'text', prompt: 'make the title red' },
  { id: 18, cat: 'structural', photo: P.sky, prompt: 'align the layer to the centre', setup: 'align' },
  { id: 19, cat: 'structural', photo: P.fjord, prompt: 'align the layer to the left', setup: 'align' },
  { id: 20, cat: 'structural', photo: P.city, prompt: 'align the layer to the right', setup: 'align' },
  { id: 21, cat: 'structural', photo: P.sky, setup: 'text', prompt: 'add a drop shadow to the title' },
  { id: 22, cat: 'structural', photo: P.fjord, setup: 'text', prompt: 'add a stroke to the layer' },
  { id: 23, cat: 'structural', photo: P.city, prompt: 'add a colour overlay' },
  { id: 24, cat: 'structural', photo: P.sky, prompt: 'fill the white area with red' },
  { id: 25, cat: 'structural', photo: P.sky, prompt: 'flood fill the sky with blue' },
  { id: 26, cat: 'structural', photo: P.hills, prompt: 'add a new layer' },
  { id: 27, cat: 'structural', photo: P.fjord, prompt: 'duplicate this layer' },
  { id: 28, cat: 'structural', photo: P.city, prompt: 'flatten the image' },
  { id: 29, cat: 'structural', photo: P.sky, prompt: 'merge the layer down' },
  { id: 30, cat: 'structural', photo: P.hills, prompt: 'delete this layer' },

  // ---- looks / adjustments (25) ----
  { id: 31, cat: 'looks', photo: P.fjord, prompt: 'make it brighter' },
  { id: 32, cat: 'looks', photo: P.hills, prompt: 'make it darker' },
  { id: 33, cat: 'looks', photo: P.city, prompt: 'warm it up' },
  { id: 34, cat: 'looks', photo: P.portrait, prompt: 'make it cooler' },
  { id: 35, cat: 'looks', photo: P.fjord, prompt: 'make it black and white' },
  { id: 36, cat: 'looks', photo: P.old, prompt: 'make it sepia' },
  { id: 37, cat: 'looks', photo: P.hills, prompt: 'make it punchy' },
  { id: 38, cat: 'looks', photo: P.fjord, prompt: 'make it vivid and colourful' },
  { id: 39, cat: 'looks', photo: P.city, prompt: 'make it dramatic' },
  { id: 40, cat: 'looks', photo: P.hills, prompt: 'make it moody and cinematic' },
  { id: 41, cat: 'looks', photo: P.old, prompt: 'make it vintage' },
  { id: 42, cat: 'looks', photo: P.old, prompt: 'give it a faded film look' },
  { id: 43, cat: 'looks', photo: P.city, prompt: 'increase the contrast' },
  { id: 44, cat: 'looks', photo: P.fjord, prompt: 'lower the contrast' },
  { id: 45, cat: 'looks', photo: P.hills, prompt: 'sharpen it' },
  { id: 46, cat: 'looks', photo: P.portrait, prompt: 'soften it' },
  { id: 47, cat: 'looks', photo: P.fjord, prompt: 'blur it slightly' },
  { id: 48, cat: 'looks', photo: P.city, prompt: 'add a vignette' },
  { id: 49, cat: 'looks', photo: P.hills, prompt: 'dehaze it' },
  { id: 50, cat: 'looks', photo: P.fjord, prompt: 'make it HDR' },
  { id: 51, cat: 'looks', photo: P.city, prompt: 'auto enhance' },
  { id: 52, cat: 'looks', photo: P.hills, prompt: 'give it a teal and orange look' },
  { id: 53, cat: 'looks', photo: P.fjord, prompt: 'golden hour' },
  { id: 54, cat: 'looks', photo: P.old, prompt: 'clean it up' },
  { id: 55, cat: 'looks', photo: P.city, prompt: 'make it look professional' },

  // ---- portrait (10) ----
  { id: 56, cat: 'portrait', photo: P.portrait, prompt: 'smooth the skin' },
  { id: 57, cat: 'portrait', photo: P.portrait, prompt: 'whiten the teeth' },
  { id: 58, cat: 'portrait', photo: P.portrait, prompt: 'remove the blemishes' },
  { id: 59, cat: 'portrait', photo: P.portrait, prompt: 'brighten the face' },
  { id: 60, cat: 'portrait', photo: P.portrait, prompt: 'brighten the eyes' },
  { id: 61, cat: 'portrait', photo: P.portrait2, prompt: 'make her look glamorous' },
  { id: 62, cat: 'portrait', photo: P.portrait, prompt: 'give it a professional headshot look' },
  { id: 63, cat: 'portrait', photo: P.portrait, prompt: 'reduce the shine on the face' },
  { id: 64, cat: 'portrait', photo: P.portrait2, prompt: 'warm up the portrait' },
  { id: 65, cat: 'portrait', photo: P.portrait, prompt: 'soft smooth skin with a nice glow' },

  // ---- generative (20) ----
  { id: 66, cat: 'generative', photo: P.city, prompt: 'remove the person on the left' },
  { id: 67, cat: 'generative', photo: P.hills, prompt: 'remove the distractions' },
  { id: 68, cat: 'generative', photo: P.portrait, prompt: 'put her on a beach' },
  { id: 69, cat: 'generative', photo: P.portrait, prompt: 'replace the background with mountains' },
  { id: 70, cat: 'generative', photo: P.fjord, prompt: 'add a full moon in the sky' },
  { id: 71, cat: 'generative', photo: P.sky, prompt: 'add dramatic clouds' },
  { id: 72, cat: 'generative', photo: P.sky, prompt: 'extend the canvas to 16:9 with generative fill' },
  { id: 73, cat: 'generative', photo: P.sky, prompt: 'extend the canvas to 9:16' },
  { id: 74, cat: 'generative', photo: P.fjord, prompt: 'add a bird flying in the sky' },
  { id: 75, cat: 'generative', photo: P.hills, prompt: 'add wildflowers in the foreground' },
  { id: 76, cat: 'generative', photo: P.fjord, prompt: 'make it look snowy' },
  { id: 77, cat: 'generative', photo: P.hills, prompt: 'make it autumn with orange leaves' },
  { id: 78, cat: 'generative', photo: P.city, prompt: 'add rain and wet streets' },
  { id: 79, cat: 'generative', photo: P.city, prompt: 'remove the car' },
  { id: 80, cat: 'generative', photo: P.city, prompt: 'remove the sign' },
  { id: 81, cat: 'generative', photo: P.fjord, prompt: 'change the sky to a sunset' },
  { id: 82, cat: 'generative', photo: P.portrait, prompt: 'put sunglasses on her' },
  { id: 83, cat: 'generative', photo: P.portrait, prompt: 'add a hat' },
  { id: 84, cat: 'generative', photo: P.fjord, prompt: 'add a rainbow' },
  { id: 85, cat: 'generative', photo: P.sky, prompt: 'generative fill the empty sky' },

  // ---- multi-step (15) ----
  { id: 86, cat: 'multistep', photo: P.city, prompt: 'crop to a square, make it warmer and add the title SUMMER' },
  { id: 87, cat: 'multistep', photo: P.fjord, prompt: 'make it black and white and add a vignette' },
  { id: 88, cat: 'multistep', photo: P.hills, prompt: 'brighten it and sharpen it' },
  { id: 89, cat: 'multistep', photo: P.city, setup: 'text', prompt: 'warm it up and add a drop shadow to the title' },
  { id: 90, cat: 'multistep', photo: P.portrait, prompt: 'remove the background and make it brighter' },
  { id: 91, cat: 'multistep', photo: P.fjord, prompt: 'crop to 16:9 and give it a moody cinematic look' },
  { id: 92, cat: 'multistep', photo: P.hills, prompt: 'flip it horizontally and add a vignette' },
  { id: 93, cat: 'multistep', photo: P.city, prompt: 'sharpen it and increase the contrast' },
  { id: 94, cat: 'multistep', photo: P.fjord, prompt: 'make it punchy and warm it up' },
  { id: 95, cat: 'multistep', photo: P.hills, prompt: 'rotate the whole image 90 and add a vignette' },
  { id: 96, cat: 'multistep', photo: P.sky, prompt: 'add the title HELLO and a drop shadow' },
  { id: 97, cat: 'multistep', photo: P.fjord, prompt: 'select the subject, invert it and blur it' },
  { id: 98, cat: 'multistep', photo: P.old, prompt: 'make it black and white then add a title' },
  { id: 99, cat: 'multistep', photo: P.city, prompt: 'warm and brighten with a vignette' },
  { id: 100, cat: 'multistep', photo: P.hills, prompt: 'crop to a square and make it vintage' },

  // ---- v2.1 harder prompts (30): multi-step with generative, poster/text
  // design, reference looks, and targeted local recolours. ----

  // hardmulti (10): compound instructions where at least one step is generative.
  { id: 101, cat: 'hardmulti', photo: P.city, prompt: 'remove the person on the left and then warm up the photo' },
  { id: 102, cat: 'hardmulti', photo: P.portrait, prompt: 'put her on a beach and add a warm sunset' },
  { id: 103, cat: 'hardmulti', photo: P.fjord, prompt: 'add a full moon and make the sky more purple' },
  { id: 104, cat: 'hardmulti', photo: P.city, prompt: 'remove the sign and add some flowers' },
  { id: 105, cat: 'hardmulti', photo: P.portrait, prompt: 'replace the background with mountains and make it cooler' },
  { id: 106, cat: 'hardmulti', photo: P.sky, prompt: 'add dramatic clouds then increase the contrast' },
  { id: 107, cat: 'hardmulti', photo: P.hills, prompt: 'make it snowy and add a warm glow' },
  { id: 108, cat: 'hardmulti', photo: P.street, prompt: 'remove the red car and add a bicycle' },
  { id: 109, cat: 'hardmulti', photo: P.sky, prompt: 'extend to 16:9 with generative fill and add a title SUMMER' },
  { id: 110, cat: 'hardmulti', photo: P.city, prompt: 'add rain and make it moody' },

  // poster (8): title / tagline text design.
  { id: 111, cat: 'poster', photo: P.canyon, prompt: 'make a movie poster titled THE SUMMIT' },
  { id: 112, cat: 'poster', photo: P.fjord, prompt: 'add a bold title "ESCAPE" at the bottom and a tagline' },
  { id: 113, cat: 'poster', photo: P.canyon, prompt: 'add large centred title text in white with a drop shadow' },
  { id: 114, cat: 'poster', photo: P.fjord, prompt: 'make a travel poster titled VISIT NORWAY' },
  { id: 115, cat: 'poster', photo: P.face, prompt: 'add the title "DREAM" in a stylish font and a thin border' },
  { id: 116, cat: 'poster', photo: P.canyon, prompt: 'turn it into a vintage travel poster with the text EXPLORE' },
  { id: 117, cat: 'poster', photo: P.city, setup: 'text', prompt: 'add a subtitle under the title and align it to the centre' },
  { id: 118, cat: 'poster', photo: P.fjord, prompt: 'design a minimalist poster: add the title "SILENCE" and desaturate the background' },

  // reference (20): "make it look like <style>" — any film, photographer or era.
  { id: 119, cat: 'reference', photo: P.city, prompt: 'make it look like a Wes Anderson film' },
  { id: 120, cat: 'reference', photo: P.hills, prompt: 'give it a Studio Ghibli animated look' },
  { id: 121, cat: 'reference', photo: P.face, prompt: 'make it look like a 1970s film photograph' },
  { id: 122, cat: 'reference', photo: P.city, prompt: 'give it a Blade Runner neon night look' },
  { id: 123, cat: 'reference', photo: P.canyon, prompt: 'make it look like a National Geographic photo' },
  { id: 124, cat: 'reference', photo: P.portrait, prompt: 'make it look like a vintage Polaroid' },
  { id: 131, cat: 'reference', photo: P.fjord, prompt: 'make it look like a Kodak film photograph' },
  { id: 132, cat: 'reference', photo: P.city, prompt: 'give it a cinematic teal and orange movie look' },
  { id: 133, cat: 'reference', photo: P.canyon, prompt: 'make it look like an old western film' },
  { id: 134, cat: 'reference', photo: P.hills, prompt: 'make it look like a National Geographic wildlife photo' },
  { id: 135, cat: 'reference', photo: P.city, prompt: 'give it a moody film noir look' },
  { id: 136, cat: 'reference', photo: P.street, prompt: 'make it look like a 1980s VHS still' },
  { id: 137, cat: 'reference', photo: P.lake, prompt: 'make it look like a golden hour film photograph' },
  { id: 138, cat: 'reference', photo: P.hills, prompt: 'give it a Kurosawa colour film look' },
  { id: 139, cat: 'reference', photo: P.fjord, prompt: 'make it look like a David Attenborough nature documentary' },
  { id: 140, cat: 'reference', photo: P.portrait2, prompt: 'make it look like a 1990s disposable camera photo' },
  { id: 141, cat: 'reference', photo: P.sunset, prompt: 'give it a Technicolor vintage look' },
  { id: 142, cat: 'reference', photo: P.lake, prompt: 'make it look like a black and white Ansel Adams landscape' },
  { id: 143, cat: 'reference', photo: P.city, prompt: 'make it look like a Wong Kar-wai film' },
  { id: 144, cat: 'reference', photo: P.old, prompt: 'give it a faded Kodachrome look' },

  // targeted (6): recolour only a specific object/region.
  { id: 125, cat: 'targeted', photo: P.street, prompt: 'make only the red car blue' },
  { id: 126, cat: 'targeted', photo: P.street, prompt: 'change the yellow car to red' },
  { id: 127, cat: 'targeted', photo: P.canyon, prompt: 'recolour the red rock to teal' },
  { id: 128, cat: 'targeted', photo: P.fjord, prompt: 'turn the sky purple but keep the water natural' },
  { id: 129, cat: 'targeted', photo: P.city, prompt: 'make the flowers bright pink' },
  { id: 130, cat: 'targeted', photo: P.old, prompt: 'colourise the faded photo naturally' },

  // bgremove (6): every common background-removal phrasing must map to the
  // deterministic ai.removeBackground op, never the AI agent / a tone.
  { id: 145, cat: 'bgremove', photo: P.portrait, prompt: 'remove the background' },
  { id: 146, cat: 'bgremove', photo: P.portrait, prompt: 'bg removal' },
  { id: 147, cat: 'bgremove', photo: P.portrait, prompt: 'remove bg' },
  { id: 148, cat: 'bgremove', photo: P.portrait, prompt: 'no background' },
  { id: 149, cat: 'bgremove', photo: P.portrait, prompt: 'make the background transparent' },
  { id: 150, cat: 'bgremove', photo: P.portrait, prompt: 'cut out the subject' },
  // ---- vague / misspelled (5) — the planner must not silently apply a
  // default brightness/vibrance tweak; it now falls back to the image model so
  // the prompt still does something meaningful.
  { id: 151, cat: 'vague', photo: P.city, prompt: 'make it better' },
  { id: 152, cat: 'vague', photo: P.fjord, prompt: 'fix it' },
  { id: 153, cat: 'vague', photo: P.portrait, prompt: 'enhace the pic' },
  { id: 154, cat: 'vague', photo: P.hills, prompt: 'do the thing' },
  { id: 155, cat: 'vague', photo: P.portrait, prompt: 'make it look proffesional' },
  // ---- auto fall-through (model) — a generative prompt in Auto mode must land
  // as ONE NEW layer with a single undo group; a vague/unrecognised prompt now
  // falls through to the image model too (no dead-end "not sure" reply). ----
  { id: 156, cat: 'auto', photo: P.city, prompt: 'add a man to the scene' },
  { id: 157, cat: 'auto', photo: P.sky, prompt: 'add a hot air balloon' },
  { id: 158, cat: 'auto', photo: P.fjord, prompt: 'do the thing' },
  // ---- restyle — must route to ai_edit_image (model edit), NOT ask and NOT
  // a filter tweak; run with MODE=agent to exercise the server planner. ----
  { id: 159, cat: 'restyle', photo: P.fjord, prompt: 'turn it into an oil painting' },
  { id: 160, cat: 'restyle', photo: P.hills, prompt: 'make it look like a watercolour' },
  { id: 161, cat: 'restyle', photo: P.portrait, prompt: 'make it look like an anime' },
  { id: 162, cat: 'restyle', photo: P.city, prompt: 'make it a comic book illustration' },
];

// Cases that load a heavy in-process model on the app host (remove-background,
// ~9.5 GB) and can OOM-kill the user's service; HARNESS is moving these to the
// seneca image server. Marked pending instead of executed so a bench run never
// crashes the app. Extend with SKIP=<ids> (e.g. SAM-heavy cases) if needed.
// Heavy in-process cases to skip. Default #90 (remove-background) while it
// risked the 7011 OOM; now that heavy image ops proxy to seneca, set
// PENDING= (empty) to run it.
const PENDING = new Set(String(process.env.PENDING != null ? process.env.PENDING : '90')
  .split(',').map(s => Number(s.trim())).filter(Boolean));
for (const id of String(process.env.SKIP || '').split(',').map(s => Number(s.trim())).filter(Boolean)) PENDING.add(id);

function authToken() {
  const data = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
  const now = Date.now() / 1000;
  for (const [token, info] of Object.entries(data)) {
    if (info.expiry == null || info.expiry > now) return token;
  }
  throw new Error(`no unexpired session in ${SESSION_FILE}`);
}

function selected() {
  let cases = CASES;
  if (process.env.CATS) {
    const cats = new Set(process.env.CATS.split(',').map(s => s.trim()));
    cases = cases.filter(c => cats.has(c.cat));
  }
  if (process.env.PROMPTS) {
    const want = new Set(process.env.PROMPTS.split(',').map(s => Number(s.trim())));
    cases = cases.filter(c => want.has(c.id));
  }
  return cases;
}

function saveShot(url, file) {
  const idx = url.indexOf(',');
  fs.writeFileSync(file, Buffer.from(url.slice(idx + 1), 'base64'));
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: !process.env.HEADFUL });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, serviceWorkers: 'block' });
  await context.addCookies([{ name: 'odysseus_session', value: authToken(), domain: new URL(BASE).hostname, path: '/' }]);
  const page = await context.newPage();
  page.on('pageerror', e => console.log('  [pageerror]', String(e).slice(0, 240)));
  // Per-case image-op HTTP statuses so a real no-op (request succeeded but the
  // image is unchanged) is distinguishable from an outage (instant 5xx/000).
  let netLog = [];
  page.on('response', r => {
    const m = r.url().match(/\/api\/image\/([a-z-]+)/);
    if (m) netLog.push({ op: m[1], status: r.status() });
  });
  page.on('requestfailed', r => {
    const m = r.url().match(/\/api\/image\/([a-z-]+)/);
    if (m) netLog.push({ op: m[1], status: 'failed:' + ((r.failure() && r.failure().errorText) || '?') });
  });

  const results = [];
  for (const c of selected()) {
    const tag = String(c.id).padStart(3, '0') + '-' + c.cat + '-' + c.prompt.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
    const rec = { id: c.id, cat: c.cat, prompt: c.prompt, photo: c.photo, ms: null, ok: null, blocked: null, reply: null, ops: [], newLayers: [], undoGroups: null, error: null, tag };
    if (PENDING.has(c.id)) {
      rec.pending = true;
      results.push(rec);
      console.log(`BENCH PENDING #${c.id} [${c.cat}] "${c.prompt}" (heavy in-process model; excluded)`);
      fs.writeFileSync(path.join(OUT, 'quickedit-bench.json'), JSON.stringify({ base: BASE, results }, null, 2));
      continue;
    }
    netLog = [];
    try {
      await page.goto(BASE + '/?nodraft=1', { waitUntil: 'domcontentloaded' });
      await page.locator('#tool-gallery-btn').waitFor({ state: 'attached', timeout: 30000 });
      await page.evaluate(async () => {
        const gallery = await import('/static/js/gallery.js?v=20260830editor4');
        gallery.openGallery();
      });
      await page.locator('#gallery-editor-tab').first().waitFor({ state: 'attached', timeout: 30000 });
      await page.evaluate(() => document.getElementById('gallery-editor-tab')?.click());
      const dataUrl = 'data:image/jpeg;base64,' + fs.readFileSync(path.join(PHOTOS, c.photo)).toString('base64');
      const selfCheck = process.env.SELFCHECK !== '0';
      const mode = process.env.MODE || '';
      await page.evaluate(async ({ selfCheck, mode, dataUrl }) => {
        const editor = await import('/static/js/galleryEditor.js?qb=' + Date.now());
        window.__geNoDraft = true;
        if (editor.isEditorOpen()) editor.closeEditor({ force: true });
        window.geQuickEditSelfCheck = selfCheck;
        try { if (mode) localStorage.setItem('ge-ai-command-mode', mode); else localStorage.removeItem('ge-ai-command-mode'); } catch (_) {}
        try { localStorage.setItem('ge-preflight-dismissed', '1'); } catch (_) {}
        // Size the document to the photo's aspect so importing never leaves
        // white letterbox margins (which the model then paints as an edge band).
        const dim = await new Promise((res) => { const im = new Image(); im.onload = () => res({ w: im.naturalWidth || 800, h: im.naturalHeight || 600 }); im.onerror = () => res({ w: 800, h: 600 }); im.src = dataUrl; });
        const W = 800; const H = Math.max(1, Math.min(1600, Math.round(W * dim.h / dim.w)));
        editor.openEditor(null, null, { w: W, h: H }, 'BENCH ' + Date.now());
      }, { selfCheck, mode, dataUrl });
      await page.locator('.ge-main-canvas').waitFor({ state: 'visible', timeout: 30000 });
      await page.waitForFunction(() => !!window.geEditorControl && !!window.geEditorAgent, null, { timeout: 20000 });

      const out = await page.evaluate(async ({ dataUrl, prompt, setup }) => {
        const invoke = (op, params) => window.geEditorControl.invoke(op, params);
        await invoke('document.importImage', { dataUrl, name: 'photo', raster: true });
        if (setup === 'text') await invoke('text.add', { text: 'Summer', x: 80, y: 80, size: 64 });
        else if (setup === 'align') await invoke('layers.addRaster', {});
        const before = (await invoke('screenshot')).result.dataUrl;
        const mark0 = (await invoke('history.mark')).result;
        const layers0 = (await invoke('layers.list')).result.map(l => l.name + ':' + l.kind);
        const ops = [];
        const control = window.geEditorControl;
        const orig = control.invoke.bind(control);
        control.invoke = async (op, params) => { ops.push(op); return orig(op, params); };
        window.__selfCheckLog = [];
        const t0 = Date.now();
        let run = null, error = null;
        try {
          run = window.geQuickEditRun
            ? await window.geQuickEditRun(prompt, {})
            : await window.geEditorAgent.run(prompt, {});
        }
        catch (err) { error = String((err && err.message) || err); }
        finally { control.invoke = orig; }
        const ms = Date.now() - t0;
        const after = (await invoke('screenshot')).result.dataUrl;
        const mark1 = (await invoke('history.mark')).result;
        const layers1 = (await invoke('layers.list')).result;
        const newLayers = layers1.map(l => l.name + ':' + l.kind).filter(s => !layers0.includes(s));
        return { before, after, mark0, mark1, newLayers, ms, ops, reply: run && (run.reply || run.message), ok: run && run.ok, blocked: run && run.blocked, error, selfChecks: window.__selfCheckLog || [] };
      }, { dataUrl, prompt: c.prompt, setup: c.setup || null });

      rec.ms = out.ms; rec.ops = out.ops; rec.reply = out.reply; rec.ok = out.ok;
      rec.blocked = out.blocked; rec.newLayers = out.newLayers;
      rec.selfChecks = out.selfChecks || [];
      rec.undoGroups = (typeof out.mark1 === 'number' && typeof out.mark0 === 'number') ? out.mark1 - out.mark0 : null;
      rec.error = out.error;
      rec.net = netLog.slice();
      saveShot(out.before, path.join(OUT, tag + '-before.png'));
      saveShot(out.after, path.join(OUT, tag + '-after.png'));
    } catch (err) {
      rec.error = String((err && err.message) || err);
    }
    results.push(rec);
    for (const sc of (rec.selfChecks || [])) {
      console.log(`SELFCHECK #${c.id} verdict=${JSON.stringify(sc.verdict)} retry=${sc.retry ? JSON.stringify(sc.retry) : 'null'}`);
    }
    console.log(`BENCH ${rec.ok === false ? 'FAIL' : 'OK'} #${c.id} [${c.cat}] "${c.prompt}" ${rec.ms}ms ops=${rec.ops.join(',') || rec.error} net=${JSON.stringify(rec.net || [])}`);
    fs.writeFileSync(path.join(OUT, 'quickedit-bench.json'), JSON.stringify({ base: BASE, results }, null, 2));
  }

  const pending = results.filter(r => r.pending).length;
  const ran = results.filter(r => !r.pending && r.ok !== false && !r.error).length;
  console.log(`QUICKEDIT_BENCH ${ran}/${results.length - pending} ran without error (${pending} pending) -> ${path.join(OUT, 'quickedit-bench.json')}`);
  await browser.close();
  process.exit(0);
})().catch(err => { console.error('FAIL', err); process.exit(1); });
