import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import fs from 'node:fs';

const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
const manifest = JSON.parse(fs.readFileSync(new URL('../../frontend/modules.json', import.meta.url), 'utf8'));
const outputs = manifest.modules.map(entry => '/static/' + entry.deployment);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ serviceWorkers: 'allow' });
  const page = await context.newPage();
  await page.goto(`${origin}/static/js/panels.js`);
  const cached = await page.evaluate(async outputs => {
    await navigator.serviceWorker.register('/static/sw.js');
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
    const missing = [];
    for (const output of outputs) if (!(await caches.match(output))?.ok) missing.push(output);
    return missing;
  }, outputs);
  assert.deepEqual(cached, []);
  assert.equal(await page.evaluate(async () => {
    const shared = await import('/static/js/shared/ui/confetti.js');
    const compare = await import('/static/js/compare/vote.js?v=20260828resendcaldrag1');
    return shared.spawnConfetti === compare.spawnConfetti;
  }), true);
  await context.setOffline(true);
  const offline = await context.newPage();
  offline.on('requestfailed', request => console.log('offline failed:', request.url(), request.failure()));
  offline.on('console', message => { if (message.type() === 'error') console.log('browser:', message.text()); });
  await offline.goto(`${origin}/static/js/panels.js`);
  const result = await offline.evaluate(async () => {
    const facade = await import('/static/js/notes.js?v=20260911notesselectioncancel1');
    const feature = await import('/static/js/features/notes/index.js');
    return { identity: facade.default === feature.default && window.notesModule === feature.default, closed: facade.isPanelOpen() === false, exports: Object.keys(facade).sort() };
  });
  assert.equal(result.identity && result.closed, true);
  console.log(JSON.stringify({ ...result, maintainedPrecache: outputs.length }));
} finally { await browser.close(); }
