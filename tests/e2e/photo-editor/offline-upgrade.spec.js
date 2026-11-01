const { test, expect } = require('@playwright/test');
const path = require('node:path');

test('static editor modules survive a cache upgrade and offline reopen', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    storageState: path.join(process.env.PHOTO_EDITOR_E2E_DATA_DIR, 'browser-auth.json'),
    serviceWorkers: 'allow',
  });
  const page = await context.newPage();
  const pickerUrl = '/static/js/colorPicker.js?v=20260910eyedropper1';
  const cssUrl = '/static/css/13-image-editor.css?v=20260929csssplit';
  const editorUrl = '/static/js/galleryEditor.js?v=20260909movepicklayer1';
  try {
    // The app's worker intentionally controls /static/, not the SPA root.
    await page.goto('/static/js/panels.js?v=20260909movepicklayer1');
    const installed = await page.evaluate(async ({ pickerUrl, cssUrl }) => {
      const previous = await caches.open('odysseus-v1113-pr40-editor-precache');
      await previous.put(pickerUrl, new Response('stale picker'));
      await previous.put(cssUrl, new Response('stale css'));
      const registration = await navigator.serviceWorker.register('/static/sw.js?v=20260916autoscroll1');
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) {
        await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
      }
      return { scope: new URL(registration.scope).pathname, root: !!(await navigator.serviceWorker.getRegistration('/')) };
    }, { pickerUrl, cssUrl });
    expect(installed).toEqual({ scope: '/static/', root: false });
    await expect.poll(() => page.evaluate(async () => (await caches.keys()).includes('odysseus-v1113-pr40-editor-precache'))).toBe(false);
    for (const url of [pickerUrl, cssUrl, editorUrl, '/static/js/platform.js', '/static/js/modelSort.js']) {
      expect(await page.evaluate(async value => !!(await caches.match(value)), url)).toBe(true);
    }

    // Simulate stale entries in the active version, then verify network-first
    // refresh updates both JS and CSS before testing the offline fallback.
    const currentCache = await page.evaluate(async () => (await caches.keys()).find(name => name.startsWith('odysseus-')));
    expect(currentCache).toMatch(/^odysseus-v\d+-pr40-editor-precache$/);
    expect(currentCache).not.toBe('odysseus-v1113-pr40-editor-precache');
    await page.evaluate(async ({ currentCache, pickerUrl, cssUrl }) => {
      const cache = await caches.open(currentCache);
      await cache.put(pickerUrl, new Response('stale picker'));
      await cache.put(cssUrl, new Response('stale css'));
      for (const url of [pickerUrl, cssUrl]) {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Network refresh failed: ${url} ${response.status}`);
      }
    }, { currentCache, pickerUrl, cssUrl });
    for (const url of [pickerUrl, cssUrl]) {
      await expect.poll(() => page.evaluate(async value => {
        const response = await caches.match(value);
        return response ? !(await response.text()).startsWith('stale ') : false;
      }, url)).toBe(true);
    }

    // Origin is the only Vary header the offline fallback may ignore. An entry
    // that varies on anything else (as a per-user response would) must miss.
    const varyProbeUrl = '/static/js/e2e-vary-probe.js';
    await page.evaluate(async ({ currentCache, varyProbeUrl }) => {
      const cache = await caches.open(currentCache);
      await cache.put(
        new Request(varyProbeUrl, { headers: { 'X-Probe-Owner': 'another-user' } }),
        new Response('export default "private";', { headers: { 'Content-Type': 'text/javascript', Vary: 'Origin, X-Probe-Owner' } }),
      );
    }, { currentCache, varyProbeUrl });

    await context.setOffline(true);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const offline = await context.newPage();
      await offline.goto('/static/js/panels.js?v=20260909movepicklayer1');
      expect(await offline.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
      const result = await offline.evaluate(async ({ pickerUrl, editorUrl, cssUrl, varyProbeUrl }) => {
        const editor = await import(editorUrl);
        const picker = await import(pickerUrl);
        const css = await fetch(cssUrl);
        const privateProbe = await import(varyProbeUrl).then(() => 'served', () => 'rejected');
        return {
          editor: typeof editor.openEditor,
          picker: ['getRecentColors', 'recordRecentColor', 'suggestColorsFor'].every(key => typeof picker[key] === 'function'),
          cssOk: css.ok,
          privateProbe,
        };
      }, { pickerUrl, editorUrl, cssUrl, varyProbeUrl });
      expect(result).toEqual({ editor: 'function', picker: true, cssOk: true, privateProbe: 'rejected' });
      await offline.close();
    }
  } finally { await context.close(); }
});
