import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
assert(origin, 'ODYSSEUS_TEST_STATIC_ORIGIN must be set by the pytest static server');

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const chatRequests = [];
  const documentSaves = [];
  const requestsInOrder = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await page.route('**/api/document/chat-send-regression-doc', async route => {
    documentSaves.push(JSON.parse(route.request().postData() || '{}'));
    requestsInOrder.push('document-save');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'chat-send-regression-doc', version_count: 2 }),
    });
  });
  await page.route('**/api/chat_stream', async route => {
    const request = route.request();
    const form = await request.allHeaders();
    const postData = request.postDataBuffer();
    chatRequests.push({
      contentType: form['content-type'] || '',
      body: postData ? postData.toString('utf8') : '',
    });
    requestsInOrder.push('chat-stream');
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
      body: 'data: [DONE]\n\n',
    });
  });
  await page.route('**/api/client-perf', route => route.fulfill({ status: 204, body: '' }));

  await page.goto(`${origin}/static/js/storage.js`);
  await page.setContent(`<!doctype html><html><body>
    <main id="chat-container" class="chat-container">
      <div id="chat-history"></div>
      <form id="chat-form" class="chat-input-bar">
        <textarea id="message"></textarea>
        <input id="web-toggle" type="checkbox">
        <input id="research-toggle" type="checkbox">
        <input id="incognito-toggle" type="checkbox">
        <input id="rag-toggle" type="checkbox">
        <input id="bash-toggle" type="checkbox">
        <button class="send-btn" type="submit">Send</button>
      </form>
    </main>
    <div id="sidebar"></div><div id="toast"></div>
  </body></html>`);

  const outcome = await page.evaluate(async () => {
    const sessionModule = await import('/static/js/sessions.js');
    const documentModule = (await import('/static/js/document.js?v=20260916docctx2')).default;
    const chatModule = (await import('/static/js/chat.js?chat-send-remediation=1')).default;

    sessionModule.setCurrentSessionId('chat-send-regression-session');
    documentModule.init('/api');
    chatModule.init('');
    await documentModule.injectFreshDoc({
      id: 'chat-send-regression-doc',
      session_id: 'chat-send-regression-session',
      title: 'Chat send regression',
      language: 'markdown',
      current_content: '# Current draft\n\nThis content should save before chat send.',
      version_count: 1,
    });
    document.getElementById('message').value = 'Summarize the open document.';
    await chatModule.handleChatSubmit({ preventDefault() {} });
    return {
      documentId: documentModule.getChatDocumentId(),
      activeEmailContext: documentModule.getActiveEmailComposerContext(),
    };
  });

  assert.equal(outcome.documentId, 'chat-send-regression-doc', 'the real editor should remain linked to the active chat');
  assert.equal(outcome.activeEmailContext, null, 'a markdown document has no active email composer context');
  assert.ok(documentSaves.length >= 1, 'chat submission should save the linked document before posting');
  assert.ok(documentSaves.every(save => /This content should save before chat send\./.test(save.content)));
  assert.equal(chatRequests.length, 1, `normal chat submission should reach /api/chat_stream; console errors: ${consoleErrors.join('; ')}`);
  assert.ok(requestsInOrder.indexOf('document-save') < requestsInOrder.indexOf('chat-stream'),
    'the linked document save should finish before the chat stream request');
  assert.match(chatRequests[0].contentType, /^multipart\/form-data;/);
  assert.match(chatRequests[0].body, /Summarize the open document\./);
  assert.match(chatRequests[0].body, /chat-send-regression-doc/);
  assert.deepEqual(pageErrors, [], `browser page errors: ${pageErrors.join('; ')}`);
  console.log('PASS actual chat submission saves linked document and posts stream request without page errors');
} finally {
  await browser.close();
}
