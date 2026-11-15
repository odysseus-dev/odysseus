// Real Chromium UI + real Cookbook routes; transfer data is a labelled replay.
// No package install, network model download, inference or production contact.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, '.odysseus-dev/cookbook-preview/evidence');
await mkdir(out, { recursive: true });
const edge = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=9337', `--user-data-dir=${path.join(out, 'edge-profile-' + Date.now())}`,
  '--window-size=1200,950', 'http://127.0.0.1:7012/',
], { stdio: 'ignore' });
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, name, timeout = 45000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) { last = await fn(); if (last) return last; await delay(300); }
  throw Error(`Timed out: ${name}; last=${JSON.stringify(last)}`);
}
let ws, seq = 0;
const pending = new Map();
const results = {};
try {
  const target = await until(async () => {
    try { return (await (await fetch('http://127.0.0.1:9337/json')).json()).find(t => t.type === 'page' && t.url.includes(':7012')); }
    catch { return null; }
  }, 'isolated Edge target');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    if (pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id); pending.delete(data.id);
      data.error ? reject(Error(JSON.stringify(data.error))) : resolve(data.result);
    }
  };
  function call(method, params = {}) {
    const id = ++seq;
    return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  }
  async function evaluate(expression) {
    const r = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  const card = sid => `document.querySelector('[data-task-id="${sid}"]')`;
  const snap = sid => evaluate(`(()=>{const e=${card(sid)};return e?{status:e.dataset.status,visible:!!e.getClientRects().length,collapsed:e.querySelector('.cookbook-output-wrap').classList.contains('cookbook-task-collapsed'),log:e.querySelector('pre').textContent,text:e.innerText}:null})()`);
  async function screenshot(name) {
    const r = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    await writeFile(path.join(out, name + '.png'), Buffer.from(r.data, 'base64'));
  }
  await call('Emulation.setFocusEmulationEnabled', { enabled: true });
  await until(() => evaluate(`document.getElementById('lab-status')?.textContent.startsWith('Ready')`), 'UI ready');
  results.initialLaunchEmpty = await evaluate(`!document.querySelector('.memory-item[data-repo="lab/cached-GGUF"]')`);
  await evaluate(`document.getElementById('replay-gemma').click()`);
  results.gemmaAppeared = await until(async () => {
    const c = await snap('cookbook-b167833a');
    return c?.visible && !c.collapsed && c.status === 'running' ? c : null;
  }, 'new Gemma card auto-visible and expanded');
  results.gemmaProgress = await until(async () => {
    const c = await snap('cookbook-b167833a'); return /(?:35|70)%/.test(c?.log) ? c : null;
  }, 'progress changes with no clicks');
  results.gemmaFailure = await until(async () => {
    const c = await snap('cookbook-b167833a'); return c?.status === 'error' && c.text.includes('No matching files') ? c : null;
  }, 'error and diagnosis automatic');
  const log = results.gemmaFailure.log;
  if (/[\x1b\uFFFD]|\[32m|\[0m/.test(log) || !log.includes('✓ Downloaded') || !log.includes('NOT SET —')) throw Error('Log display corrupted');
  if (log.split('HF token:').length !== 2) throw Error('Repeated terminal tail');
  results.rawAnsiPreserved = await evaluate(`JSON.parse(localStorage.getItem('cookbook-tasks')).find(t=>t.sessionId==='cookbook-b167833a').output.includes('\\x1b[32m')`);
  await screenshot('gemma-error-clean-utf8');
  await evaluate(`document.getElementById('replay-success').click()`);
  await until(async () => (await snap('cookbook-lab-success'))?.status === 'running', 'success fixture starts');
  results.successProgress = await until(async () => {
    const c = await snap('cookbook-lab-success'); return c?.log.includes('35%') ? c : null;
  }, 'success fixture progress');
  await evaluate(`${card('cookbook-lab-success')}.querySelector('.cookbook-task-header').click()`);
  results.successFinished = await until(async () => {
    const c = await snap('cookbook-lab-success'); return c?.status === 'done' ? c : null;
  }, 'success immediately reflected by poll');
  if (!results.successFinished.collapsed) throw Error('Manual collapse lost on update');
  await until(() => evaluate(`!!document.querySelector('.memory-item[data-repo="lab/cached-GGUF"]')`), 'Launch list refreshed by completion');
  await evaluate(`document.querySelector('.cookbook-tab[data-backend="Serve"]').click()`);
  results.launchNewModelVisible = await evaluate(`!!document.querySelector('.memory-item[data-repo="lab/cached-GGUF"]').getClientRects().length`);
  await screenshot('launch-fixture-appears-without-refresh');
  results.errors = await evaluate('window.labErrors');
  await call('Page.reload', { ignoreCache: true });
  await until(() => evaluate(`document.getElementById('lab-status')?.textContent.startsWith('Ready')`), 'reload restores UI');
  await evaluate(`document.querySelector('.cookbook-tab[data-backend="Running"]').click()`);
  results.restoredGemma = await until(async () => {
    const c = await snap('cookbook-b167833a'); return c?.status === 'error' ? c : null;
  }, 'retained error after refresh');
  results.restoredSuccess = await snap('cookbook-lab-success');
  results.errorsAfterReload = await evaluate('window.labErrors');
  if (results.errors.length || results.errorsAfterReload.length) throw Error('Browser errors');
  await writeFile(path.join(out, 'ui-results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  await call('Browser.close');
} finally {
  if (ws?.readyState === WebSocket.OPEN) ws.close();
  // This handle belongs only to the isolated headless Edge launched above.
  if (edge.exitCode === null) edge.kill();
}
