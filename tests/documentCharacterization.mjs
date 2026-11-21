import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const scenario = process.argv[2];
const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
assert.ok(origin, 'ODYSSEUS_TEST_STATIC_ORIGIN must be set');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on('pageerror', err => errors.push(err.message));
page.on('console', msg => { if (msg.text().startsWith('SEND_')) console.error(msg.text()); });

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');

const pending = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};

try {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"></main>`);

  if (scenario === 'import-time-effects') {
    // M7: Verify effects before import, at import, after init, and after open
    await page.evaluate(() => {
      window.__captureKeydownRegistrations = 0;
      const add = window.addEventListener;
      window.addEventListener = function (type, handler, options) {
        if (type === 'keydown' && (options === true || options?.capture)) window.__captureKeydownRegistrations++;
        return add.call(this, type, handler, options);
      };
    });
    const beforeImport = await page.evaluate(() => ({
      hasGlobal: window.documentModule !== undefined,
      styleCount: document.querySelectorAll('#doc-tab-menu-styles').length,
      hasPane: Boolean(document.getElementById('doc-editor-pane')),
      captureKeydowns: window.__captureKeydownRegistrations,
    }));
    assert.equal(beforeImport.hasGlobal, false, 'No global module before import');
    assert.equal(beforeImport.styleCount, 0, 'No tab menu styles before import');
    assert.equal(beforeImport.hasPane, false, 'No pane before import');
    assert.equal(beforeImport.captureKeydowns, 0, 'No capture keydown registrations before import');

    const atImport = await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=import-time-check-1');
      return {
        isWindowSameAsDefault: window.documentModule === mod.default,
        styleCount: document.querySelectorAll('#doc-tab-menu-styles').length,
        hasPane: Boolean(document.getElementById('doc-editor-pane')),
        captureKeydowns: window.__captureKeydownRegistrations,
      };
    });
    assert.equal(atImport.isWindowSameAsDefault, true, 'window.documentModule equals default export');
    assert.equal(atImport.styleCount, 1, 'Exactly one style#doc-tab-menu-styles injected at evaluation');
    assert.equal(atImport.hasPane, false, 'No premature doc-editor-pane mounted on import');
    assert(atImport.captureKeydowns > 0, 'Capture keydown handlers register at module evaluation');

    // After init, pane is still not mounted
    await page.evaluate(() => {
      window.editor = window.documentModule;
      window.editor.init('');
    });
    const afterInitHasPane = await page.evaluate(() => Boolean(document.getElementById('doc-editor-pane')));
    assert.equal(afterInitHasPane, false, 'No pane after init() before openPanel');

    // After openPanel, pane is mounted
    await page.evaluate(async () => {
      await window.editor.injectFreshDoc({ id: 'open-test', title: 'Open Test', language: 'markdown', current_content: 'sample' });
      await window.editor.openPanel();
    });
    const afterOpenHasPane = await page.evaluate(() => Boolean(document.getElementById('doc-editor-pane')));
    assert.equal(afterOpenHasPane, true, 'doc-editor-pane mounted on first openPanel');
    await page.evaluate(() => { globalThis._selections = [{ start: 0, end: 3, text: 'sam' }]; });
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => globalThis._selections.length), 0, 'Imported Escape layering clears the inner selection');
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), true, 'Inner Escape preserves the mounted pane');

  } else if (scenario === 'multi-instance-check') {
    // Characterize evaluating the facade under multiple query strings in a single page
    const multiEval = await page.evaluate(async () => {
      const modA = await import('/static/js/document.js?v=multi-instance-a');
      const modB = await import('/static/js/document.js?v=multi-instance-b');
      return {
        modulesAreIdentical: modA === modB,
        defaultsAreIdentical: modA.default === modB.default,
        windowMatchesLastEvaluated: window.documentModule === modB.default,
        stylesCount: document.querySelectorAll('#doc-tab-menu-styles').length,
      };
    });
    // The facade evaluates twice, while its extracted dependencies share their
    // canonical module identity and install singleton styles only once.
    assert.equal(multiEval.modulesAreIdentical, false, 'Distinct query URLs evaluate separate instances');
    assert.equal(multiEval.defaultsAreIdentical, false, 'Default export objects differ between distinct URLs');
    assert.equal(multiEval.windowMatchesLastEvaluated, true, 'window.documentModule points to latest evaluated facade');
    assert.equal(multiEval.stylesCount, 1, 'Shared extracted tab module installs styles once');

  } else {
    // All subsequent scenarios initialize editor facade with merged named + default surface
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=20260916docctx2');
      window.editor = Object.assign({}, mod.default, mod);
      window.editor.init('');
    });

    if (scenario === 'storage-keys') {
      // M4: Storage key names, formats, scoping, and retention
      const res = await page.evaluate(async () => {
        localStorage.clear();
        const s1 = 'sess-alpha';
        const s2 = 'sess-beta';

        // 1. Session visibility keys: open, minimized, active
        window.editor.prepareDocumentOpen(s1);
        const s1OpenAfterPrepare = localStorage.getItem('odysseus-doc-open-' + s1);
        const s1MinAfterPrepare = localStorage.getItem('odysseus-doc-minimized-' + s1);
        const s2OpenAfterPrepare = localStorage.getItem('odysseus-doc-open-' + s2);

        await window.editor.loadSessionDocs(s1);
        await window.editor.injectFreshDoc({ id: 'doc-s1', title: 'Doc S1', language: 'markdown', current_content: 'test', session_id: s1 });
        await window.editor.loadDocument('doc-s1');
        const s1ActiveDoc = localStorage.getItem('odysseus-doc-active-' + s1);

        window.editor.closePanel('down');
        const s1MinAfterDown = localStorage.getItem('odysseus-doc-minimized-' + s1);
        const s1OpenAfterDown = localStorage.getItem('odysseus-doc-open-' + s1);

        window.editor.closePanel('right');
        const s1MinAfterRight = localStorage.getItem('odysseus-doc-minimized-' + s1);
        const s1OpenAfterRight = localStorage.getItem('odysseus-doc-open-' + s1);

        // 2. Actions recent key: odysseus-doc-actions-recent
        localStorage.setItem('odysseus-doc-actions-recent', JSON.stringify(['doc-copy-btn', 'doc-export-btn']));
        const recentRaw = localStorage.getItem('odysseus-doc-actions-recent');
        const recentParsed = JSON.parse(recentRaw);

        // 3. Fontsize key: odysseus-doc-fontsize
        localStorage.setItem('odysseus-doc-fontsize', '1');
        const fontsizeVal = localStorage.getItem('odysseus-doc-fontsize');

        // 4. Email local draft key format and retention: odysseus.email.replyDraft.v1:
        const draftKey = 'odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|msg-42|');
        const freshDraft = {
          to: 'lead@example.com',
          cc: '',
          bcc: '',
          subject: 'Project Kickoff',
          inReplyTo: '',
          references: '',
          sourceUid: 'msg-42',
          sourceFolder: 'INBOX',
          body: 'Draft body message',
          updatedAt: Date.now(),
        };
        localStorage.setItem(draftKey, JSON.stringify(freshDraft));
        const storedDraft = JSON.parse(localStorage.getItem(draftKey));

        // Expired draft (> 45 days)
        const expiredKey = 'odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|msg-old|');
        const expiredDraft = {
          ...freshDraft,
          sourceUid: 'msg-old',
          updatedAt: Date.now() - (46 * 24 * 60 * 60 * 1000),
        };
        localStorage.setItem(expiredKey, JSON.stringify(expiredDraft));

        // 5. AI reply context key: odysseus:email-ai-reply-context:v1:
        const aiKey = 'odysseus:email-ai-reply-context:v1:acc1:INBOX:msg-42';
        localStorage.setItem(aiKey, 'Context prompt text');
        const storedAiContext = localStorage.getItem(aiKey);

        // 6. Suggestions key format: odysseus-suggestions-{docId}
        const sugKey = 'odysseus-suggestions-doc-s1';
        const suggestionsData = [{ id: 'sug-1', find: 'typo', replace: 'fixed', reason: 'spelling' }];
        localStorage.setItem(sugKey, JSON.stringify(suggestionsData));
        const storedSuggestions = JSON.parse(localStorage.getItem(sugKey));

        return {
          s1OpenAfterPrepare, s1MinAfterPrepare, s2OpenAfterPrepare,
          s1ActiveDoc,
          s1MinAfterDown, s1OpenAfterDown,
          s1MinAfterRight, s1OpenAfterRight,
          recentParsed,
          fontsizeVal,
          draftKeyPrefixMatches: draftKey.startsWith('odysseus.email.replyDraft.v1:'),
          storedDraftTo: storedDraft.to,
          aiKeyPrefixMatches: aiKey.startsWith('odysseus:email-ai-reply-context:v1:'),
          storedAiContext,
          storedSuggestionsLength: storedSuggestions.length,
          expiredKeyExistsBefore: Boolean(localStorage.getItem(expiredKey)),
        };
      });

      assert.equal(res.s1OpenAfterPrepare, '1');
      assert.equal(res.s1MinAfterPrepare, null);
      assert.equal(res.s2OpenAfterPrepare, null, 'Session s2 isolated from s1 prepare');
      assert.equal(res.s1ActiveDoc, 'doc-s1');
      assert.equal(res.s1MinAfterDown, '1');
      assert.equal(res.s1OpenAfterDown, null);
      assert.equal(res.s1MinAfterRight, null);
      assert.equal(res.s1OpenAfterRight, null);
      assert.deepEqual(res.recentParsed, ['doc-copy-btn', 'doc-export-btn']);
      assert.equal(res.fontsizeVal, '1');
      assert.equal(res.draftKeyPrefixMatches, true);
      assert.equal(res.storedDraftTo, 'lead@example.com');
      assert.equal(res.aiKeyPrefixMatches, true);
      assert.equal(res.storedAiContext, 'Context prompt text');
      assert.equal(res.storedSuggestionsLength, 1);
      assert.equal(res.expiredKeyExistsBefore, true);

    } else if (scenario === 'email-double-send') {
      // M5: Double-send prevention during held send request
      const emailContent = 'To: dev@example.com\nSubject: Deploy Status\n---\nAll checks passed.';
      await page.evaluate(async (content) => {
        await window.editor.injectFreshDoc({
          id: 'email-send-test',
          title: 'Email Send Test',
          language: 'email',
          current_content: content,
          version_count: 1,
          sourceEmailAccountId: 'acc-send-1',
        });
        await window.editor.loadDocument('email-send-test');
      }, emailContent);

      let sendCalls = 0;
      const sendGate = pending();

      await page.unroute('**/api/**');
      await page.route('**/api/email/accounts', route => route.fulfill({
        json: [{ id: 'acc-send-1', smtp_host: 'smtp.example.com', smtp_user: 'dev@example.com', has_smtp_password: true }]
      }));
      await page.route('**/api/email/draft', route => route.fulfill({
        json: { success: true, draft_uid: 'draft-temp-1' }
      }));
      await page.route('**/api/email/send', async route => {
        sendCalls++;
        await sendGate.promise;
        await route.fulfill({ json: { success: true } });
      });

      const firstSendPromise = page.waitForRequest('**/api/email/send');
      await page.locator('#doc-email-send-btn:visible').first().click();
      await firstSendPromise;

      // Attempt second submission while held
      await page.evaluate(() => {
        const btn = Array.from(document.querySelectorAll('#doc-email-send-btn')).find(candidate => candidate.getClientRects().length > 0);
        if (btn) btn.click();
      });

      assert.equal(sendCalls, 1, 'Second submit blocked by in-flight guard');

      // Release first send
      sendGate.release();
      await page.waitForTimeout(50);
      assert.equal(sendCalls, 1, 'Exactly one request dispatched');

    } else if (scenario === 'email-stale-ai-reply') {
      // M5: Stale AI reply generations and manual edit interruption
      const emailContent = 'To: client@example.com\nSubject: Invoice Query\n---\n';
      await page.evaluate(async (content) => {
        await window.editor.injectFreshDoc({
          id: 'email-ai-test',
          title: 'Email AI Test',
          language: 'email',
          current_content: content,
          version_count: 1,
        });
        await window.editor.loadDocument('email-ai-test');
      }, emailContent);

      // Part A: User edits textarea while AI reply is streaming
      const sseGate = pending();
      await page.unroute('**/api/**');
      await page.route('**/api/email/ai-reply', async route => {
        await sseGate.promise;
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
          body: 'data: {"type": "reply", "text": "Stale AI suggested body"}\n\ndata: {"type": "result", "success": true}\n\n',
        });
      });

      const aiRequestPromise = page.waitForRequest('**/api/email/ai-reply');
      await page.evaluate(() => {
        window.editor.generateEmailReply();
      });
      await aiRequestPromise;

      // While request is in-flight, user types into rich body
      await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        if (rich) {
          rich.innerHTML = 'Manual user override content';
          rich.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });

      // Release AI response
      sseGate.release();
      await page.waitForTimeout(100);

      // Verify stale AI reply was NOT injected over the user edit
      const richContent = await page.evaluate(() => {
        const rich = document.getElementById('doc-email-richbody');
        return rich ? rich.innerText.trim() : '';
      });
      assert.equal(richContent, 'Manual user override content', 'Stale AI reply dropped after manual edit');

    } else if (scenario === 'email-attachments') {
      // M5: Attachment staging via file input and compose-upload
      const emailContent = 'To: partner@example.com\nSubject: Contract\n---\nPlease review attached agreement.';
      await page.evaluate(async (content) => {
        await window.editor.injectFreshDoc({
          id: 'email-att-test',
          title: 'Email Attachment Test',
          language: 'email',
          current_content: content,
          version_count: 1,
        });
        await window.editor.loadDocument('email-att-test');
      }, emailContent);

      await page.route('**/api/email/compose-upload', route => route.fulfill({
        json: { success: true, token: 'tok-agreement-99', filename: 'agreement.pdf', size: 1048576 }
      }));

      // Stage attachment via file input change event
      await page.setInputFiles('#doc-email-file-input', {
        name: 'agreement.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.4 test document'),
      });

      // Verify attachment chip rendered in DOM
      await page.locator('.email-compose-chip').waitFor({ state: 'visible' });
      const chipText = await page.locator('.email-compose-chip .compose-chip-name').textContent();
      assert.equal(chipText, 'agreement.pdf');

    } else if (scenario === 'email-local-draft') {
      // M5: Local draft write under odysseus.email.replyDraft.v1: and restore
      const emailContent = 'To: initial@example.com\nSubject: Draft Test\nX-Source-UID: uid-local-1\nX-Source-Folder: INBOX\n---\nInitial body';
      await page.evaluate(async (content) => {
        await window.editor.injectFreshDoc({
          id: 'email-draft-doc',
          title: 'Draft Doc',
          language: 'email',
          current_content: content,
          version_count: 1,
        });
        await window.editor.loadDocument('email-draft-doc');
      }, emailContent);

      // Edit fields and persist local draft
      const draftResult = await page.evaluate(() => {
        const toInput = document.getElementById('doc-email-to');
        const subjInput = document.getElementById('doc-email-subject');
        const rich = document.getElementById('doc-email-richbody');
        if (toInput) toInput.value = 'updated@example.com';
        if (subjInput) subjInput.value = 'Updated Subject';
        if (rich) rich.innerHTML = 'Updated body content';

        const sourceUid = document.getElementById('doc-email-source-uid')?.value || 'uid-local-1';
        const sourceFolder = document.getElementById('doc-email-source-folder')?.value || 'INBOX';
        const key = 'odysseus.email.replyDraft.v1:' + encodeURIComponent(`${sourceFolder}|${sourceUid}|`);
        const payload = {
          to: 'updated@example.com',
          cc: '',
          bcc: '',
          subject: 'Updated Subject',
          inReplyTo: '',
          references: '',
          sourceUid,
          sourceFolder,
          body: 'Updated body content',
          updatedAt: Date.now(),
        };
        localStorage.setItem(key, JSON.stringify(payload));
        return { key, stored: JSON.parse(localStorage.getItem(key)) };
      });

      assert.equal(draftResult.stored.to, 'updated@example.com');
      assert.equal(draftResult.stored.subject, 'Updated Subject');
      assert.equal(draftResult.stored.body, 'Updated body content');

      // Clear local draft removes it
      await page.evaluate((key) => {
        localStorage.removeItem(key);
      }, draftResult.key);
      const afterClear = await page.evaluate((key) => localStorage.getItem(key), draftResult.key);
      assert.equal(afterClear, null, 'Draft removed from localStorage on clear');

    } else if (scenario === 'selection-highlights') {
      // M6: Selection highlight validity across edits
      const text = 'First line intro.\nTarget highlighted phrase to anchor.\nLast line footer.';
      await page.evaluate(async (content) => {
        // Characterizes source-offset re-anchoring, so pin the Markdown source editor.
        localStorage.setItem('odysseus-doc-md-editor-mode', 'markdown');
        await window.editor.injectFreshDoc({
          id: 'doc-highlight',
          title: 'Highlight Test',
          language: 'markdown',
          current_content: content,
          version_count: 1,
        });
        await window.editor.loadDocument('doc-highlight');
      }, text);

      const phrase = 'Target highlighted phrase to anchor.';
      const initialStart = text.indexOf(phrase);
      const initialEnd = initialStart + phrase.length;

      // 1. Programmatic edit before selection: re-anchors forward
      const prefix = 'Header line inserted before.\n';
      const shiftedResult = await page.evaluate(async ({ phrase, prefix }) => {
        await window.editor.restoreSelectionReference('L2', {
          documentId: 'doc-highlight',
          selections: [{ text: phrase }],
        });

        const textarea = document.getElementById('doc-editor-textarea');
        textarea.value = prefix + textarea.value;

        // getSelectionContext triggers _validateSelections to re-anchor
        return window.editor.getSelectionContext();
      }, { phrase, prefix });

      assert.ok(shiftedResult, 'Selection survived insertion before it');
      assert.equal(shiftedResult.text, phrase);
      assert.equal(shiftedResult.start, initialStart + prefix.length, 'Selection start re-anchored forward');
      assert.equal(shiftedResult.end, initialEnd + prefix.length, 'Selection end re-anchored forward');

      // 2. Programmatic edit deleting target phrase: drops selection
      const droppedResult = await page.evaluate(async ({ phrase }) => {
        await window.editor.restoreSelectionReference('L2', {
          documentId: 'doc-highlight',
          selections: [{ text: phrase }],
        });

        const textarea = document.getElementById('doc-editor-textarea');
        textarea.value = 'Completely replaced text without the target phrase.';

        return window.editor.getSelectionContext();
      }, { phrase });

      assert.equal(droppedResult, null, 'Selection dropped when captured text was deleted');

      // 3. User typing in textarea: drops selection on input event
      const typedResult = await page.evaluate(async ({ phrase }) => {
        await window.editor.restoreSelectionReference('L2', {
          documentId: 'doc-highlight',
          selections: [{ text: phrase }],
        });

        const textarea = document.getElementById('doc-editor-textarea');
        textarea.value = 'User typing test';
        textarea.dispatchEvent(new Event('input', { bubbles: true }));

        return window.editor.getSelectionContext();
      }, { phrase });

      assert.equal(typedResult, null, 'Selection cleared on user typing input event');

    } else if (scenario === 'chat-moves') {
      // M6: moving active document into current or new chat
      await page.evaluate(async () => {
        await window.editor.injectFreshDoc({
          id: 'doc-move-1',
          title: 'Move Test',
          language: 'markdown',
          current_content: 'Content to move',
          version_count: 1,
          session_id: 'session-src',
        });
        await window.editor.loadDocument('doc-move-1');
      });

      await page.route('**/api/session', route => {
        return route.fulfill({
          json: { id: 'session-new-created', session_id: 'session-new-created' }
        });
      });

      let lastPatchedSessionId = null;
      await page.route('**/api/document/doc-move-1', async route => {
        if (route.request().method() === 'PATCH') {
          const body = route.request().postDataJSON();
          lastPatchedSessionId = body?.session_id;
          await route.fulfill({
            json: { id: 'doc-move-1', title: 'Move Test', language: 'markdown', current_content: 'Content to move', session_id: lastPatchedSessionId }
          });
        } else {
          await route.fulfill({ json: {} });
        }
      });

      // Move to current chat
      const movePatchPromise = page.waitForRequest(r => r.url().includes('/api/document/doc-move-1') && r.method() === 'PATCH');
      await page.evaluate(async () => {
        await window.editor.moveActiveDocumentToCurrentChat({ quiet: true });
      });
      await movePatchPromise;

      assert.equal(lastPatchedSessionId, 'session-new-created', 'PATCH sent session_id from session creation');
      const activeDocId = await page.evaluate(() => window.editor.getCurrentDocId());
      assert.equal(activeDocId, 'doc-move-1');

    } else {
      throw new Error(`Unknown scenario: ${scenario}`);
    }
  }

  assert.deepEqual(errors, [], `Unexpected browser errors: ${errors.join('; ')}`);
  console.log(JSON.stringify({ scenario, passed: true }));
} finally {
  await browser.close();
}
