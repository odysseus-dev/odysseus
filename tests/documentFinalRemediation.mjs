import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');

async function setup(t, handler) {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'published module graph must not throw'));
  const requests = [];
  await page.route('**/api/**', async route => {
    const req = route.request();
    const request = { path: new URL(req.url()).pathname, method: req.method(), body: req.headers()['content-type']?.includes('application/json') ? req.postDataJSON() : null };
    requests.push(request);
    if (await handler?.(route, request)) return;
    await route.fulfill({ json: { version_count: 2 } });
  });
  await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"><textarea id="message"></textarea></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js'); editor.init('');
    window.state = await import('/static/js/document/application/documentState.js');
    window.composer = await import('/static/js/document/ui/email/composerFields.js');
    window.localDraft = await import('/static/js/document/ui/email/localDraft.js');
    window.presenter = await import('/static/js/document/ui/panel/documentPresenter.js');
  });
  return { page, requests };
}

async function inject(page, id, content, language = 'email') {
  await page.evaluate(data => editor.injectFreshDoc(data), { id, title: id, language, current_content: content, version_count: 2 });
}
const reply = (id, { to = `${id}@example.com`, cc = '', bcc = '', subject = id } = {}) =>
  `To: ${to}\nCc: ${cc}\nBcc: ${bcc}\nSubject: ${subject}\nIn-Reply-To: <${id}>\nReferences: <thread-${id}>\nX-Source-UID: ${id}\nX-Source-Folder: INBOX\n---\nBody ${id}`;
const populated = { cc: 'cc-b@example.com', bcc: 'bcc-b@example.com' };
const load = (page, id) => page.evaluate(id => editor.loadDocument(id), id);
async function envelope(page) {
  return page.evaluate(() => Object.fromEntries(['to', 'cc', 'bcc', 'subject', 'in-reply-to', 'references', 'source-uid', 'source-folder'].map(field => [field, document.getElementById(`doc-email-${field}`).value])));
}

// All email endpoints are intercepted. These tests cannot deliver messages.
test('email ownership: empty reply recipients stay empty after B to A and in outgoing payloads', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (!['/api/email/draft', '/api/email/send', '/api/email/schedule'].includes(request.path)) return false;
    await route.fulfill({ json: { success: true, draft_uid: 'saved-draft', draft_folder: 'Drafts' } });
    return true;
  });
  await page.evaluate(() => { window.__odysseusActiveEmailAccount = 'sender'; });
  for (const action of ['draft', 'schedule', 'send']) {
    const id = `a-${action}`;
    await inject(page, id, reply(id));
    await inject(page, 'b', reply('b', populated));
    await load(page, id);
    assert.deepEqual(await envelope(page), {
      to: `${id}@example.com`, cc: '', bcc: '', subject: id,
      'in-reply-to': `<${id}>`, references: `<thread-${id}>`, 'source-uid': id, 'source-folder': 'INBOX',
    });
    assert.equal(await page.locator('#doc-email-cc-row').isVisible(), false);
    assert.equal(await page.locator('#doc-email-bcc-row').isVisible(), false);
    if (action === 'draft' && process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS) {
      await page.screenshot({ path: `${process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS}/recipient-isolation.png`, fullPage: true });
    }
    await page.evaluate(id => {
      state.docs.get(id)._composeAtts = [{ token: `attachment-${id}`, filename: `${id}.txt` }];
      presenter.saveCurrentToMap();
      localDraft._persistEmailLocalDraftNow();
    }, id);
    const snapshot = await page.evaluate(id => ({
      content: state.docs.get(id).content,
      draft: JSON.parse(localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent(`INBOX|${id}|<${id}>`))),
      account: window.__odysseusActiveEmailAccount,
    }), id);
    assert.doesNotMatch(snapshot.content, /(?:Cc|Bcc):/);
    assert.equal(snapshot.draft.cc, ''); assert.equal(snapshot.draft.bcc, '');
    assert.equal(snapshot.draft.sourceUid, id); assert.equal(snapshot.account, 'sender');
    const submitted = page.waitForResponse(res => res.url().endsWith(`/api/email/${action}`));
    await page.evaluate(async action => {
      const workflow = await import('/static/js/document/ui/email/sendWorkflow.js');
      if (action === 'draft') await workflow._saveDraft();
      else if (action === 'send') await workflow._sendEmail();
      else await workflow._scheduleSend();
    }, action);
    if (action === 'schedule') await page.locator('#sched-confirm').click();
    await submitted;
    const payload = requests.filter(req => req.path === `/api/email/${action}`).at(-1).body;
    assert.equal(payload.to, `${id}@example.com`); assert.equal(payload.subject, id);
    assert.equal(payload.cc, null); assert.equal(payload.bcc, null);
    assert.equal(payload.in_reply_to, `<${id}>`); assert.equal(payload.references, `<thread-${id}>`);
    assert.equal(payload.account_id, 'sender'); assert.deepEqual(payload.attachments, [`attachment-${id}`]);
    if (action === 'send') assert.equal(payload.source_uid, id);
  }
});

test('email ownership: focus and empty To/Subject belong to the destination; same-document edits survive refresh', async t => {
  const { page } = await setup(t);
  await inject(page, 'empty', reply('empty', { to: '', subject: '' }));
  await inject(page, 'full', reply('full', populated));
  for (const field of ['to', 'cc', 'bcc', 'subject']) {
    await load(page, 'full');
    await page.locator(`#doc-email-${field}`).focus();
    await load(page, 'empty');
    const values = await envelope(page);
    for (const key of ['to', 'cc', 'bcc', 'subject']) assert.equal(values[key], '', `destination ${key} after focus on ${field}`);
  }
  // Refresh with incomplete server fields while a same-document edit is focused.
  await page.locator('#doc-email-to').fill('typing@example.com');
  await page.evaluate(() => composer._showEmailFields({ ...state.docs.get('empty'), content: 'To: \nSubject: \nX-Source-UID: empty\n---\nBody' }, { applyLocalDraft: false }));
  assert.equal(await page.locator('#doc-email-to').inputValue(), 'typing@example.com');
  await page.locator('#doc-email-to').blur();
  await page.evaluate(() => composer._showEmailFields({ ...state.docs.get('empty'), content: 'To: \nX-Source-UID: empty\n---\nBody' }, { applyLocalDraft: false }));
  assert.equal(await page.locator('#doc-email-to').inputValue(), 'typing@example.com');
  // An authoritative restore may clear same-document fields even while focused.
  await page.locator('#doc-email-to').focus();
  await page.evaluate(() => composer._showEmailFields({ ...state.docs.get('empty'), content: 'To: \nSubject: \n---\nRestored' }, { applyLocalDraft: false, forceHeaderFields: true }));
  assert.equal(await page.locator('#doc-email-to').inputValue(), '');
  await inject(page, 'regular', 'To: regular@example.com\nCc: regular-cc@example.com\nSubject: Regular\n---\nBody');
  await load(page, 'empty');
  assert.equal((await envelope(page)).cc, '');
});

test('email ownership: cleared local recipients and delayed draft writes retain their original owner', async t => {
  const { page } = await setup(t);
  const a = reply('a', { cc: 'server-cc@example.com', bcc: 'server-bcc@example.com' });
  await inject(page, 'a', a);
  // Explicit local clears must override the older server snapshot on restoration.
  await page.evaluate(() => {
    for (const field of ['cc', 'bcc']) document.getElementById(`doc-email-${field}`).value = '';
    localDraft._persistEmailLocalDraftSoon();
  });
  await inject(page, 'b', reply('b', populated));
  await page.waitForFunction(() => localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|a|<a>')) !== null);
  await page.evaluate(content => { state.docs.get('a').content = content; }, a);
  await load(page, 'a');
  assert.equal((await envelope(page)).cc, ''); assert.equal((await envelope(page)).bcc, '');
  // A later asynchronous same-document refresh still restores A's local snapshot.
  await page.evaluate(() => new Promise(resolve => setTimeout(() => { composer._showEmailFields(state.docs.get('a')); resolve(); }, 0)));
  assert.equal((await envelope(page)).cc, ''); assert.equal((await envelope(page)).bcc, '');
  const drafts = await page.evaluate(() => ['a', 'b'].map(id => JSON.parse(localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent(`INBOX|${id}|<${id}>`)))));
  assert.equal(drafts[0].cc, ''); assert.equal(drafts[0].bcc, ''); assert.equal(drafts[0].sourceUid, 'a');
  if (drafts[1]) assert.equal(drafts[1].cc, populated.cc);
  await load(page, 'b');
  // Exercise callers that change pane ownership before calling composer directly.
  await page.evaluate(() => {
    state.setActiveDocId('a');
    document.getElementById('doc-editor-pane').dataset.activeDocumentId = 'a';
    composer._showEmailFields(state.docs.get('a'));
  });
  assert.equal((await envelope(page)).cc, ''); assert.equal((await envelope(page))['source-uid'], 'a');
  await load(page, 'b');
  await page.evaluate(() => {
    state.setActiveDocId('a');
    document.getElementById('doc-editor-pane').dataset.activeDocumentId = 'a';
    composer._syncStreamingEmailFields(state.docs.get('a'));
  });
  assert.equal((await envelope(page)).cc, ''); assert.equal((await envelope(page))['source-uid'], 'a');
});
