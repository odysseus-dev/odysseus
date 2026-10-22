"""Real Chromium proof at the existing /static/ service-worker scope.

This deliberately does not widen SW scope or claim offline SPA root reload.
The helper is first evaluated offline, after install-time precaching only.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_precached_helper_imports_offline_in_a_fresh_page_and_panels_stay_lazy():
    script = r'''
      import assert from 'node:assert/strict';
      import { chromium } from 'playwright';
      const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
      const browser = await chromium.launch({ headless: true });
      try {
        const context = await browser.newContext({ serviceWorkers: 'allow' });
        const page = await context.newPage();
        await page.goto(`${origin}/static/js/panels.js`);
        const installed = await page.evaluate(async () => {
          await navigator.serviceWorker.register('/static/sw.js?v=20260916autoscroll1');
          const registration = await navigator.serviceWorker.ready;
          if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
          const response = await caches.match('/static/js/actionMenuOrder.js');
          return { scope: registration.scope, cached: !!response && response.ok };
        });
        assert.equal(installed.scope, `${origin}/static/`);
        assert.equal(installed.cached, true);
        // New page gives a fresh ES-module map. No helper import occurred online.
        await context.setOffline(true);
        const offline = await context.newPage();
        await offline.goto(`${origin}/static/js/panels.js`);
        const requests = [];
        offline.on('request', request => requests.push(request.url()));
        const result = await offline.evaluate(async () => {
          const helper = await import('/static/js/actionMenuOrder.js');
          const again = await import('./actionMenuOrder.js');
          const panels = await import('/static/js/panels.js?v=20260909movepicklayer1');
          return {
            sameModule: helper === again,
            exports: Object.keys(helper).sort(),
            order: helper.orderActionMenuItems([{ label: 'Delete' }, { label: 'Rename' }]).map(i => i.label),
            panels: panels.panelNames(),
          };
        });
        assert.equal(result.sameModule, true);
        assert.deepEqual(result.order, ['Rename', 'Delete']);
        assert.ok(result.panels.includes('editor'));
        assert.equal(requests.some(url => url.includes('/galleryEditor.js')), false);
        console.log(JSON.stringify({ ...result, ...installed }));
      } finally { await browser.close(); }
    '''
    result = subprocess.run(["node", "--input-type=module", "--eval", script], cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["exports"] == ["SELECT_MENU_ICON", "actionMenuRank", "orderActionMenuItems"]
