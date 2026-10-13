import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const url = process.env.ODYSSEUS_UI_TEST_URL;
test('reply stream tolerates caret setup but never overwrites user input', { skip: !url }, async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    await context.addCookies([{ name: 'odysseus_session', value: process.env.ODYSSEUS_UI_TEST_COOKIE, url }]);
    const page = await context.newPage();
    await page.goto(url);
    await page.waitForFunction(() => window.documentModule);
    await page.route('**/api/document/qa-stream-*', route => route.fulfill({ json: {} }));
    let editDuringRequest = false;
    await page.route('**/api/email/ai-reply', async route => {
      if (editDuringRequest) {
        await page.evaluate(() => {
          const rich = document.getElementById('doc-email-richbody');
          rich.textContent = 'My own reply';
          rich.dispatchEvent(new Event('input', { bubbles: true }));
        });
      }
      await route.fulfill({ contentType: 'text/event-stream', body: [
        { type: 'reply', text: '' },
        { type: 'reply', text: 'Hi QA,\nTomorrow works.\nFelix' },
        { type: 'result', success: true, reply: 'Hi QA,\nTomorrow works.\nFelix', model_used: 'fixture' },
      ].map(event => `data: ${JSON.stringify(event)}\n\n`).join('') });
    });
    const run = id => page.evaluate(async id => {
      await window.documentModule.injectFreshDoc({ id, title: 'QA reply stream', language: 'email',
        content: 'To: qa@example.com\nSubject: QA\n---\n\n---------- Previous message ----------\nHello' });
      const success = await window.documentModule.generateEmailReply({ originalBody: 'Hi Felix, can we meet tomorrow?' });
      return { success, body: document.getElementById('doc-editor-textarea').value,
        toast: [...document.querySelectorAll('.toast-message')].map(el => el.textContent).join('\n') };
    }, id);
    const generated = await run('qa-stream-normal');
    assert.equal(generated.success, true, generated.toast);
    assert.match(generated.body, /Tomorrow works/);
    assert.match(generated.body, /Previous message/);
    editDuringRequest = true;
    const edited = await run('qa-stream-edited');
    assert.notEqual(edited.success, true);
    assert.equal(edited.body, 'My own reply');
  } finally {
    await browser.close();
  }
});
