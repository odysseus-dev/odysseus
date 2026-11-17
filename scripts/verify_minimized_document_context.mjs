#!/usr/bin/env node
// Real mobile UI: minimize/save/restore/close/session-switch; no model or real records.
import fs from 'node:fs';
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const base = 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error('Test account is not logged in');
const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' });
await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
const sessions = [], documents = [];
const checks = [];
try {
  for (let i = 0; i < 2; i++) {
    const response = await context.request.post(`${base}/api/session`, { multipart: {
      name: '[fixture] minimized editor context', model: 'odysseus-qwen3.5-tools-pre-heretic',
      endpoint_id: '1d1022ef', endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(),
      skip_validation: 'true', rag: 'false',
    }});
    assert.equal(response.ok(), true);
    sessions.push((await response.json()).id);
  }
  const created = await context.request.post(`${base}/api/document`, { data: {
    session_id: sessions[0], title: '[fixture] minimized persistence', language: 'markdown', content: 'Original fixture text.',
  }});
  assert.equal(created.ok(), true);
  const id = (await created.json()).id;
  documents.push(id);
  const page = await context.newPage();
  await page.goto(`${base}/#${sessions[0]}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, sessions[0]);
  await page.evaluate(id => window.documentModule.loadDocument(id), id);
  await page.waitForFunction(id => window.documentModule?.getCurrentDocId?.() === id, id);
  const textarea = page.locator('#doc-editor-textarea');
  await textarea.fill('Updated fixture text before minimizing.');
  await page.evaluate(() => window.documentModule.closePanel('down'));
  await page.waitForFunction(() => !window.documentModule.isPanelOpen() && !window.documentModule.getCurrentDocId());
  assert.equal(await page.evaluate(() => window.documentModule.getChatDocumentId()), id);
  assert.equal(await page.evaluate(() => window.documentModule.saveDocument({ silent: true })), true);
  const saved = await context.request.get(`${base}/api/document/${id}`);
  assert.equal((await saved.json()).current_content, 'Updated fixture text before minimizing.');
  checks.push('minimized document stays bound and persists the captured text');

  // Exercise public editor operations, not private local variables.
  await page.evaluate(id => window.documentModule.loadDocument(id), id);
  await page.waitForFunction(id => window.documentModule.getCurrentDocId() === id, id);
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'Updated fixture text before minimizing.');
  await page.evaluate(() => window.documentModule.closePanel());
  await page.waitForFunction(() => !window.documentModule.getCurrentDocId());
  assert.equal(await page.evaluate(() => window.documentModule.getChatDocumentId()), null);
  checks.push('restore retains text; actual close removes chat binding');

  await page.evaluate(id => window.documentModule.loadDocument(id), id);
  await page.waitForFunction(id => window.documentModule.getCurrentDocId() === id, id);
  await page.evaluate(() => window.documentModule.closePanel('down'));
  await page.waitForFunction(() => !window.documentModule.getCurrentDocId());
  await page.goto(`${base}/#${sessions[1]}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, sessions[1]);
  assert.equal(await page.evaluate(() => window.documentModule.getChatDocumentId()), null);
  checks.push('switching chats does not carry the minimized document');
} finally {
  for (const id of documents) assert.equal((await context.request.delete(`${base}/api/document/${id}`)).ok(), true);
  for (const id of sessions) assert.equal((await context.request.delete(`${base}/api/session/${id}`)).ok(), true);
  await browser.close();
}
console.log(JSON.stringify({ status: 'passed', checks, cleanup: true }));
