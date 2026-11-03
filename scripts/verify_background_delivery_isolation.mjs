/** Real DOM/module behavior with only polling HTTP responses controlled. No jobs created. */
import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
try {
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html');
  await page.setContent('<div id="chat-history"><div class="msg">Existing chat</div></div>');
  const result = await page.evaluate(async () => {
    const { startBackgroundToolJobs } = await import('/static/js/backgroundToolJobs.js');
    const box = document.querySelector('#chat-history');
    const first = box.firstElementChild;
    let current = 'chat-a', resolveRequest;
    window.__odysseusSessionReadyId = current;
    const originalFetch = window.fetch;
    const payload = { jobs: [{ status: 'delivered', message: {
      role: 'assistant', content: 'Finished research', metadata: { _db_id: 'fixture-result' },
    } }] };
    window.fetch = () => new Promise(resolve => { resolveRequest = () => resolve({ ok: true, json: async () => payload }); });
    const append = (role, content, model, metadata) => {
      const node = document.createElement('div');
      node.dataset.dbId = metadata._db_id;
      node.textContent = content;
      box.append(node);
    };
    const pause = () => new Promise(resolve => setTimeout(resolve, 25));
    const stop = startBackgroundToolJobs({ getSessionId: () => current, addMessage: append });
    try {
      current = 'chat-b'; window.__odysseusSessionReadyId = current;
      resolveRequest(); await pause();
      const checks = { switched_chat_does_not_receive_stale_result: box.children.length === 1 };
      current = 'chat-a'; window.__odysseusSessionReadyId = current;
      const streaming = document.createElement('div'); streaming.className = 'msg-ai streaming'; box.append(streaming);
      document.dispatchEvent(new Event('visibilitychange')); resolveRequest(); await pause();
      checks.active_reply_not_interrupted = !box.querySelector('[data-db-id]');
      streaming.remove();
      document.dispatchEvent(new Event('visibilitychange')); resolveRequest(); await pause();
      checks.delivered_after_reply = box.querySelectorAll('[data-db-id="fixture-result"]').length === 1;
      document.dispatchEvent(new Event('visibilitychange')); resolveRequest(); await pause();
      checks.repeated_poll_is_idempotent = box.querySelectorAll('[data-db-id="fixture-result"]').length === 1;
      checks.existing_transcript_preserved = first === box.firstElementChild;
      return checks;
    } finally { stop(); window.fetch = originalFetch; }
  });
  console.log(JSON.stringify(result));
  if (!Object.values(result).every(Boolean)) process.exitCode = 1;
} finally { await browser.close(); }
