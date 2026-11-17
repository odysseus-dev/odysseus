// Real UI, synthetic SSE only. No model/tool executions or user-record edits.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright';

const base = 'http://127.0.0.1:7011';
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === 'sft_alex_creator')?.[0];
if (!token) throw Error('Missing test-owner authentication');
const browser = await chromium.launch({headless: true});
const context = await browser.newContext({serviceWorkers: 'block'});
await context.addCookies([{name: 'odysseus_session', value: token, url: base}]);
const failures = [];
try {
  for (const partial of ['', 'Partial answer must remain visible.']) {
    const created = await context.request.post(`${base}/api/session`, {multipart: {
      name: '[error-visibility] synthetic regression',
      model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: '1d1022ef',
      endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(), skip_validation: 'true',
    }});
    assert.ok(created.ok());
    const {id} = await created.json();
    const page = await context.newPage();
    try {
      await page.goto(`${base}/#${id}`, {waitUntil: 'domcontentloaded'});
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, id);
      await page.route('**/api/chat_stream', route => route.fulfill({
        status: 200, contentType: 'text/event-stream',
        body: (partial ? `data: ${JSON.stringify({delta: partial})}\n\n` : '')
          + 'event: error\ndata: {"status":503,"error":"Test endpoint unavailable <img src=x>"}\n\n'
          + 'data: [DONE]\n\n',
      }));
      let reloads = 0;
      page.on('request', request => {
        if (new URL(request.url()).pathname.startsWith('/api/history/')) reloads++;
      });
      await page.locator('textarea#message:visible').fill('Synthetic error display check');
      await page.locator('textarea#message:visible').press('Enter');
      await page.waitForFunction(() => document.querySelector('#chat-history')?.innerText.includes('Test endpoint unavailable'), null, {timeout: 8000});
      // Wait for deferred history reconciliation, not merely the first frame.
      await page.waitForTimeout(500);
      const text = await page.locator('#chat-history').innerText();
      assert.ok(text.includes('Test endpoint unavailable <img src=x>'));
      if (partial) assert.ok(text.includes(partial));
      assert.equal(reloads, 0, 'Unsaved terminal error must not reload away the live answer');
      assert.equal(await page.locator('#chat-history img[src="x"]').count(), 0);
      console.log(JSON.stringify({case: partial ? 'partial-503' : 'preoutput-503', status: 'passed'}));
    } catch (error) {
      failures.push(String(error));
      console.log(JSON.stringify({case: partial ? 'partial-503' : 'preoutput-503', status: 'failed', error: String(error)}));
    } finally {
      await page.close();
      assert.ok((await context.request.delete(`${base}/api/session/${id}`)).ok());
    }
  }
} finally {
  await browser.close();
}
if (failures.length) process.exitCode = 1;
