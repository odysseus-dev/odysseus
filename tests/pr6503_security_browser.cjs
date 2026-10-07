const playwright = require('playwright');
const { readFileSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');

const source = readFileSync('static/js/document.js', 'utf8');
function documentFunction(name, text = source) {
  const match = text.match(new RegExp('^  (?:async )?function ' + name + '\\(.*?^  }', 'ms'));
  assert(match, name);
  return match[0];
}

(async () => {
  const engine = process.env.ODYSSEUS_SECURITY_BROWSER || 'chromium';
  assert(['chromium', 'firefox', 'webkit'].includes(engine), engine);
  const browser = await playwright[engine].launch({ headless: true });
  try {
    // Opt-in positive controls use an explicit vulnerable revision, so these
    // regressions also work after the fix is committed or in a shallow checkout.
    const baselineRevision = process.env.ODYSSEUS_SECURITY_BASELINE_REVISION;
    if (baselineRevision) {
      const original = execFileSync('git', ['show', baselineRevision + ':static/js/document.js'], { encoding: 'utf8' });
      const baseline = await browser.newPage();
      await baseline.route('**/api/**', route => route.fulfill({ json: { fonts: {}, value: null } }));
      await baseline.route('**/static/js/chatRenderer-head-harness.js', route => route.fulfill({
        contentType: 'application/javascript',
        body: execFileSync('git', ['show', baselineRevision + ':static/js/chatRenderer.js'], { encoding: 'utf8' }),
      }));
      await baseline.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN + '/static/js/documentStats.js');
      const originalFunctions = Object.fromEntries([
        '_unfoldEmailHeaderLines', '_parseEmailHeader', '_looksLikeWrappedEmailContent', '_decodeBase64EmailWrapper',
        '_sanitizeOutgoingEmailBody', '_emailHtmlToPlainText', '_aiReply', '_loadOdysseusAttachItems',
        '_odysseusAttachLabel', '_escHtml',
      ].map(name => [name, documentFunction(name, original)]));
      const vulnerable = await baseline.evaluate(async functions => {
        const { recordSessionMetricsCost } = await import('/static/js/chatRenderer-head-harness.js');
        recordSessionMetricsCost({ model: 'gpt-4o', input_tokens: 100, output_tokens: 10,
          endpoint_cost_tracked: true, _costRecordId: 'pollutedByLedger' }, '__proto__');
        await navigator.locks.request('odysseus-session-cost-ledger', () => {});
        const polluted = Object.hasOwn(Object.prototype, 'pollutedByLedger');
        delete Object.prototype.pollutedByLedger;
        localStorage.clear();
        // Isolate the second annotation's overflow assignment from the real
        // inherited-session lookup defect by seeding an OWN __proto__ run map.
        // The addition assigned at HEAD:1424 is primitive, so __proto__'s setter
        // ignores it rather than changing either this map or Object.prototype.
        const prototypeBefore = Object.getOwnPropertyDescriptors(Object.prototype);
        localStorage.setItem('ody-session-cost-runs', JSON.stringify({ ['__proto__']:
          Object.fromEntries(Array.from({ length: 256 }, (_, i) => ['seed-' + i, 0.001])) }));
        recordSessionMetricsCost({ model: 'gpt-4o', input_tokens: 100, output_tokens: 10,
          endpoint_cost_tracked: true, _costRecordId: 'overflow-proof' }, '__proto__');
        await navigator.locks.request('odysseus-session-cost-ledger', () => {});
        const prototypeAfter = Object.getOwnPropertyDescriptors(Object.prototype);
        const overflowUnchanged = Reflect.ownKeys(prototypeBefore).length === Reflect.ownKeys(prototypeAfter).length
          && Reflect.ownKeys(prototypeBefore).every(key => Reflect.ownKeys(prototypeBefore[key])
            .every(field => prototypeBefore[key][field] === prototypeAfter[key]?.[field]));
        const overflowRuns = JSON.parse(localStorage.getItem('ody-session-cost-runs'))['__proto__'];
        const overflowCostStore = localStorage.getItem('ody-session-cost');
        localStorage.clear();
        const _unfoldEmailHeaderLines = eval('(' + functions._unfoldEmailHeaderLines + ')');
        const _parseEmailHeader = eval('(' + functions._parseEmailHeader + ')');
        const _looksLikeWrappedEmailContent = eval('(' + functions._looksLikeWrappedEmailContent + ')');
        const _decodeBase64EmailWrapper = eval('(' + functions._decodeBase64EmailWrapper + ')');
        const _sanitizeOutgoingEmailBody = eval('(' + functions._sanitizeOutgoingEmailBody + ')');
        const _emailHtmlToPlainText = eval('(' + functions._emailHtmlToPlainText + ')');
        const activeDocId = 'fixture';
        const docs = new Map();
        const uiModule = { showToast() {} };
        const _splitEmailReplyQuote = text => ({ body: text, quote: '' });
        const generate = eval('(' + functions._aiReply + ')');
        document.body.innerHTML = '<textarea id="doc-editor-textarea"></textarea>';
        const payload = '<p>Existing draft</p><img src="data:,bad" onerror="window.executed++">';
        const executions = [];
        for (const inspect of [() => _sanitizeOutgoingEmailBody(payload), () => _emailHtmlToPlainText(payload),
          () => { document.getElementById('doc-editor-textarea').value = payload; return generate(); }]) {
          window.executed = 0;
          await inspect();
          await new Promise(resolve => setTimeout(resolve, 100));
          executions.push(window.executed);
        }
        const API_BASE = '';
        const _escHtml = eval('(' + functions._escHtml + ')');
        const _odysseusAttachLabel = eval('(' + functions._odysseusAttachLabel + ')');
        const spinnerModule = { createLoadingRow: () => document.createElement('div') };
        const _syncOdysseusAttachSelection = () => {};
        const fetch = async () => ({ ok: true, json: async () => ({ items: [{ id: 'quote', filename: 'quote.png',
          url: 'data:,bad" onerror="window.executed++' }] }) });
        const menu = document.createElement('div');
        menu.innerHTML = '<div class="email-odysseus-attach-list"></div>';
        document.body.appendChild(menu);
        window.executed = 0;
        await eval('(' + functions._loadOdysseusAttachItems + ')')(menu, 'gallery');
        await new Promise(resolve => setTimeout(resolve, 100));
        return { polluted, executions, gallery: window.executed, injected: !!menu.querySelector('[onerror]'),
          overflowUnchanged, overflowRuns: Object.keys(overflowRuns).length, overflowCostStore };
      }, originalFunctions);
      assert.equal(vulnerable.polluted, true);
      assert(vulnerable.executions.every(count => count > 0), JSON.stringify(vulnerable));
      assert.equal(vulnerable.injected, true);
      assert(vulnerable.gallery > 0);
      assert.equal(vulnerable.overflowUnchanged, true);
      assert.equal(vulnerable.overflowRuns, 256);
      assert.equal(vulnerable.overflowCostStore, '{}');
      await baseline.close();
    }

    const page = await browser.newPage();
    const errors = [];
    const probes = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.url().includes('/inert-probe')) probes.push(request.url());
    });
    await page.route('**/api/**', route => route.fulfill({ json: { fonts: {}, value: null } }));
    await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN + '/static/js/documentStats.js');
    await page.setContent('<!doctype html><textarea id="doc-editor-textarea"></textarea>');

    const ledger = await page.evaluate(async () => {
      const { recordSessionMetricsCost, getSessionCost, resetSessionCost } = await import('/static/js/chatRenderer.js');
      const metrics = id => ({ model: 'gpt-4o', input_tokens: 100, output_tokens: 10,
        endpoint_cost_tracked: true, _costRecordId: id });
      const before = Object.getOwnPropertyDescriptors(Object.prototype);
      for (const sid of ['__proto__', 'constructor', 'prototype', ['__proto__'], { toString: () => '__proto__' }]) {
        for (const id of ['pollutedByLedger', '__proto__', 'constructor', 'prototype', '']) {
          localStorage.clear();
          recordSessionMetricsCost(metrics(id), sid);
          // Web Locks settle asynchronously in Chromium.
          await navigator.locks.request('odysseus-session-cost-ledger', () => {});
          if (Object.hasOwn(Object.prototype, 'pollutedByLedger')) throw new Error('Object.prototype polluted');
          if (localStorage.getItem('ody-session-cost') || localStorage.getItem('ody-session-cost-runs')) {
            throw new Error('Unsafe session key was stored');
          }
        }
        localStorage.clear();
        recordSessionMetricsCost(metrics('  ' + sid + '  '), 'safe-session');
        await navigator.locks.request('odysseus-session-cost-ledger', () => {});
        if (localStorage.getItem('ody-session-cost-runs')) throw new Error('Unsafe run key was stored');
      }
      localStorage.clear();
      recordSessionMetricsCost(metrics('valid-run'), 'safe-session');
      await navigator.locks.request('odysseus-session-cost-ledger', () => {});
      const cost = getSessionCost('safe-session');
      recordSessionMetricsCost(metrics('valid-run'), 'safe-session');
      await navigator.locks.request('odysseus-session-cost-ledger', () => {});
      // Keys are literal property names, not dot-separated traversal paths.
      recordSessionMetricsCost(metrics('constructor.prototype'), 'safe.__proto__.session');
      await navigator.locks.request('odysseus-session-cost-ledger', () => {});
      const legacy = metrics('');
      recordSessionMetricsCost(legacy, 'legacy-session');
      recordSessionMetricsCost(legacy, 'legacy-session');
      await navigator.locks.request('odysseus-session-cost-ledger', () => {});
      for (let i = 0; i < 257; i++) recordSessionMetricsCost(metrics('run-' + i), 'overflow-session');
      await navigator.locks.request('odysseus-session-cost-ledger', () => {});
      // Snapshot all ordinary-ledger results before the reserved-key fixture
      // deliberately replaces localStorage below.
      const replayCost = getSessionCost('safe-session');
      const legacyCost = getSessionCost('legacy-session');
      const dottedCost = getSessionCost('safe.__proto__.session');
      const overflowCost = getSessionCost('overflow-session');
      const runWire = JSON.parse(localStorage.getItem('ody-session-cost-runs') || '{}');
      const costWire = JSON.parse(localStorage.getItem('ody-session-cost') || '{}');
      const retainedRuns = Object.keys(runWire['overflow-session']).length;
      const wireShape = !Array.isArray(runWire)
        && !Array.isArray(costWire)
        && !!runWire['overflow-session']
        && !Array.isArray(runWire['overflow-session']);

      // A persisted legacy/reserved key must remain data rather than becoming
      // prototype state. Reading and removing it must also be side-effect free.
      localStorage.setItem('ody-session-cost', '{"__proto__":1}');
      localStorage.setItem('ody-session-cost-runs', '{"__proto__":{"legacy-run":2}}');
      const legacyReservedCost = getSessionCost('__proto__');
      resetSessionCost('__proto__');
      const clearedReserved = !Object.hasOwn(
        JSON.parse(localStorage.getItem('ody-session-cost') || '{}'),
        '__proto__',
      ) && !Object.hasOwn(
        JSON.parse(localStorage.getItem('ody-session-cost-runs') || '{}'),
        '__proto__',
      );

      const after = Object.getOwnPropertyDescriptors(Object.prototype);
      return { cost, replayCost, legacyCost, dottedCost,
        overflowCost, retainedRuns, wireShape, legacyReservedCost, clearedReserved,
        unchanged: Reflect.ownKeys(before).length === Reflect.ownKeys(after).length
          && Reflect.ownKeys(before).every(key => Reflect.ownKeys(before[key]).every(field => before[key][field] === after[key]?.[field])) };
    });
    assert(ledger.cost > 0);
    assert.equal(ledger.replayCost, ledger.cost);
    assert.equal(ledger.unchanged, true);
    assert.equal(ledger.legacyCost, ledger.cost);
    assert.equal(ledger.dottedCost, ledger.cost);
    assert(Math.abs(ledger.overflowCost - 257 * ledger.cost) < 1e-10);
    assert.equal(ledger.retainedRuns, 256);
    assert.equal(ledger.wireShape, true);
    assert.equal(ledger.legacyReservedCost, 3);
    assert.equal(ledger.clearedReserved, true);

    const names = ['_unfoldEmailHeaderLines', '_parseEmailHeader', '_looksLikeWrappedEmailContent', '_decodeBase64EmailWrapper',
      '_sanitizeOutgoingEmailBody', '_emailHtmlToPlainText', '_aiReply',
      '_emailPlainTextToHtml', '_emailQuoteMarkerMatch', '_emailBodyFragmentToHtml', '_emailBodyToHtml', '_setEmailBodyText',
      '_loadOdysseusAttachItems', '_odysseusAttachLabel', '_escHtml',
      '_normalizeRichLinkUrl', '_smartRichPasteUrl', '_insertSmartRichPasteLink', '_cleanRichTextPasteHtml', 'exportAsPdf'];
    const functions = Object.fromEntries(names.map(name => [name, documentFunction(name)]));
    const email = await page.evaluate(async functions => {
      window.executed = 0;
      const { cleanEmailReplyText } = await import('/static/js/emailReplyText.js');
      const { readEmailReplyResponse } = await import('/static/js/emailReplyStream.js');
      const markdownModule = await import('/static/js/markdown.js');
      const _unfoldEmailHeaderLines = eval('(' + functions._unfoldEmailHeaderLines + ')');
      const _parseEmailHeader = eval('(' + functions._parseEmailHeader + ')');
      const _looksLikeWrappedEmailContent = eval('(' + functions._looksLikeWrappedEmailContent + ')');
      const _decodeBase64EmailWrapper = eval('(' + functions._decodeBase64EmailWrapper + ')');
      const _sanitizeOutgoingEmailBody = eval('(' + functions._sanitizeOutgoingEmailBody + ')');
      const _emailHtmlToPlainText = eval('(' + functions._emailHtmlToPlainText + ')');
      const _emailPlainTextToHtml = eval('(' + functions._emailPlainTextToHtml + ')');
      const _emailQuoteMarkerMatch = eval('(' + functions._emailQuoteMarkerMatch + ')');
      const _emailBodyFragmentToHtml = eval('(' + functions._emailBodyFragmentToHtml + ')');
      const _emailBodyToHtml = eval('(' + functions._emailBodyToHtml + ')');
      const _setEmailBodyText = eval('(' + functions._setEmailBodyText + ')');
      const activeDocId = 'fixture';
      const docs = new Map([['fixture', { language: 'email', content: '' }]]);
      let _docAiReplyRequestSeq = 0, _emailAiReplyGeneration = 0, _autoSaveDebounce = null;
      let richbody = null;
      const _emailRichbodyActive = () => richbody;
      const _syncEmailRichbody = rich => { document.getElementById('doc-editor-textarea').value = rich.innerText; };
      const syncHighlighting = () => {};
      const _persistEmailLocalDraftSoon = () => {};
      const saveCurrentToMap = () => {};
      const saveDocument = () => {};
      const _docAiReplyContextKey = () => 'fixture-context';
      const _clearDocAiReplyContext = () => {};
      const sessionModule = { getCurrentModel: () => '', getCurrentSessionId: () => 'fixture-session' };
      const toasts = [], errors = [], requests = [];
      const uiModule = { showToast: text => toasts.push(text), showError: text => errors.push(text) };
      const _splitEmailReplyQuote = text => ({ body: text, quote: '' });
      const API_BASE = '';
      let reply = 'Hi Taylor,\n\nThursday works.\n\nMorgan';
      const fetch = async (url, options) => {
        if (url !== '/api/email/ai-reply') throw new Error('Unexpected fixture request');
        requests.push(JSON.parse(options.body));
        return new Response(JSON.stringify({ success: true, reply }), {
          headers: { 'content-type': 'application/json' },
        });
      };
      const generate = eval('(' + functions._aiReply + ')');
      const payloads = [
        '<img src="/inert-probe" onerror="window.executed++">',
        '<svg onload="window.executed++"><script>window.executed++</script></svg>',
        '<iframe srcdoc="<script>parent.executed++</script>"></iframe>',
        '<img src="data:,bad" onerror="window.executed++">',
      ];
      const plain = [], expectedDrafts = [], inputResults = [];
      for (const payload of payloads) {
        _sanitizeOutgoingEmailBody(payload);
        plain.push(_emailHtmlToPlainText(payload));
        // Explicit AI may polish existing text. Inspecting/requesting its HTML
        // must remain inert, and it must stay separate from original_body.
        const body = '<p>Existing draft</p>' + payload;
        document.getElementById('doc-editor-textarea').value = body;
        expectedDrafts.push(body);
        inputResults.push(await generate());
      }
      // Media-only content remains nonempty draft guidance; it must not be
      // dropped as if the user had requested an empty reply.
      for (const body of ['<img src="/inert-probe">', '<video src="/inert-probe"></video>',
        '<audio src="/inert-probe"></audio>', '<iframe src="/inert-probe"></iframe>', '<table><tr><td></td></tr></table>']) {
        document.getElementById('doc-editor-textarea').value = body;
        expectedDrafts.push(body);
        inputResults.push(await generate());
      }
      const inputRequests = requests.slice();
      const inputToasts = toasts.slice();
      const normal = _emailHtmlToPlainText('<p>Hello <b>world</b> &amp; friends</p>');
      const literal = _emailHtmlToPlainText('&lt;img src=x onerror="window.executed++"&gt;');
      const outgoing = _sanitizeOutgoingEmailBody('Hello\n\nworld');
      const wrapped = _sanitizeOutgoingEmailBody(btoa('To: taylor@example.invalid\nSubject: Test\n---\nValid reply'));

      // Exercise the actual insertion/render helpers on a connected rich body:
      // a completed model response is still untrusted at the DOM boundary.
      richbody = document.createElement('div');
      richbody.id = 'doc-email-richbody';
      richbody.contentEditable = 'true';
      document.body.appendChild(richbody);
      let outputUnsafe = 0;
      const outputResults = [];
      let isolatedSvgPreviews = 0;
      for (const payload of [
        '<img src="data:,bad" onerror="window.executed++">',
        '<svg onload="window.executed++"><script>window.executed++</script></svg>',
        '<iframe srcdoc="<script>parent.executed++</script>"></iframe>',
        '<a href="javascript:window.executed++" onclick="window.executed++">unsafe link</a>',
      ]) for (const prefix of ['<p>Hi Taylor, Thursday works.</p>', 'Hi Taylor, Thursday works.\n\n']) {
        richbody.innerText = 'Morgan typed a safe draft.';
        reply = '<think><img src="/inert-probe" onerror="window.executed++"></think>'
          + '<<<REPLY>>>' + prefix + payload + '<<<END>>>Done';
        outputResults.push(await generate());
        outputUnsafe += richbody.querySelectorAll('script,svg,[onerror],[onload],[onclick],a[href^="javascript:"]').length;
        // Plaintext SVG is rendered through the existing isolated preview.
        // The untrusted child must have no script/network access to this page.
        for (const frame of richbody.querySelectorAll('iframe')) {
          const policy = new DOMParser().parseFromString(frame.srcdoc, 'text/html')
            .querySelector('meta[http-equiv="Content-Security-Policy"]')?.content;
          if (frame.className === 'chat-svg-preview' && frame.getAttribute('sandbox') === ''
            && frame.referrerPolicy === 'no-referrer' && !frame.hasAttribute('src')
            && policy === "default-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; style-src 'unsafe-inline'") {
            isolatedSvgPreviews++;
          } else outputUnsafe++;
        }
        if (!richbody.textContent.includes('Hi Taylor, Thursday works.')) throw new Error('Final reply content lost');
        const body = document.getElementById('doc-editor-textarea').value;
        if (/think|<<<|\/inert-probe|\bDone\b/.test(body)) throw new Error('Reasoning or status reached the draft');
        richbody.querySelectorAll('a').forEach(a => a.click());
      }
      const failureResults = [];
      for (const invalid of ['Done', '<<<REPLY>>>Unfinished reply']) {
        richbody.innerText = 'Morgan typed a safe draft.';
        reply = invalid;
        failureResults.push(await generate());
        if (richbody.innerText !== 'Morgan typed a safe draft.'
          || document.getElementById('doc-editor-textarea').value !== 'Morgan typed a safe draft.') {
          throw new Error('Unusable model output replaced user text');
        }
      }
      await new Promise(resolve => setTimeout(resolve, 150));
      richbody.remove();
      clearTimeout(_autoSaveDebounce);
      return { normal, literal, outgoing, wrapped, inputResults, expectedDrafts, inputRequests,
        inputToasts, outputResults, outputUnsafe, isolatedSvgPreviews, failureResults, errors, executed: window.executed };
    }, functions);
    assert.equal(email.normal, 'Hello world & friends');
    assert.equal(email.literal, '<img src=x onerror="window.executed++">');
    assert.equal(email.outgoing, 'Hello\n\nworld');
    assert.equal(email.wrapped, 'Valid reply');
    assert.deepEqual(email.inputResults, Array(9).fill(true));
    assert.deepEqual(email.inputRequests.map(request => request.current_draft), email.expectedDrafts);
    assert(email.inputRequests.every(request => request.stream === true
      && !request.original_body.includes('window.executed')));
    assert.deepEqual(email.inputToasts, Array.from({ length: 9 }, () => ['Writing AI reply', 'AI draft inserted']).flat());
    assert.deepEqual(email.outputResults, Array(8).fill(true));
    assert.equal(email.outputUnsafe, 0);
    assert.equal(email.isolatedSvgPreviews, 1);
    assert.deepEqual(email.failureResults, [false, false]);
    assert.equal(email.errors.length, 2);
    assert.equal(email.executed, 0);
    assert.deepEqual(probes, []);

    // The current payload must execute at an active DOM boundary. This makes
    // the non-execution assertions above independent of old git revisions.
    const activeEmailControl = await page.evaluate(async () => {
      const active = document.createElement('div');
      active.innerHTML = '<img src="data:,bad" onerror="window.executed++">';
      document.body.appendChild(active);
      const deadline = Date.now() + 2000;
      while (!window.executed && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      active.remove();
      const executed = window.executed;
      window.executed = 0;
      return executed;
    });
    assert(activeEmailControl > 0);

    const gallery = await page.evaluate(async functions => {
      const API_BASE = '';
      const _escHtml = eval('(' + functions._escHtml + ')');
      const _odysseusAttachLabel = eval('(' + functions._odysseusAttachLabel + ')');
      const spinnerModule = { createLoadingRow: () => document.createElement('div') };
      const _syncOdysseusAttachSelection = () => {};
      const load = eval('(' + functions._loadOdysseusAttachItems + ')');
      const urls = ['/static/missing.png" onerror="window.executed++" data-injected="yes',
        '/static/missing.png"><svg onload="window.executed++"></svg>', '/static/missing.png',
        'javascript:window.executed++', 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" onload="parent.executed++"/>'];
      const fetch = async () => ({ ok: true, json: async () => ({ items: urls.map((url, i) => ({
        id: 'image-' + i, url, filename: '<b>Picture</b>', title: 'Safe title' })) }) });
      const menu = document.createElement('div');
      menu.innerHTML = '<div class="email-odysseus-attach-list"></div>';
      document.body.appendChild(menu);
      await load(menu, 'gallery');
      const rows = [...menu.querySelectorAll('.email-odysseus-attach-row')];
      rows[0].click();
      await new Promise(resolve => setTimeout(resolve, 150));
      return { urls, sources: rows.map(row => row.querySelector('img').getAttribute('src')),
        unsafe: menu.querySelectorAll('[onerror], [onload], [data-injected], svg, script').length,
        selected: rows[0].classList.contains('is-selected'),
        labels: rows.map(row => row.querySelector('.email-odysseus-attach-meta').textContent),
        executed: window.executed };
    }, functions);
    assert.deepEqual(gallery.sources, gallery.urls);
    assert.equal(gallery.unsafe, 0);
    assert.equal(gallery.executed, 0);
    assert.equal(gallery.selected, true);
    assert.deepEqual(gallery.labels, Array(5).fill('<b>Picture</b>'));

    const links = await page.evaluate(async functions => {
      const markdownModule = await import('/static/js/markdown.js');
      const _normalizeRichLinkUrl = eval('(' + functions._normalizeRichLinkUrl + ')');
      const _smartRichPasteUrl = eval('(' + functions._smartRichPasteUrl + ')');
      const insert = eval('(' + functions._insertSmartRichPasteLink + ')');
      const cleanPaste = eval('(' + functions._cleanRichTextPasteHtml + ')');
      const rejected = ['javascript:window.executed++', 'java\tscript:window.executed++',
        'data:text/html,<script>window.executed++</script>', 'vbscript:msgbox(1)',
        'file:///etc/passwd', 'https://example.com/\njavascript:window.executed++',
        'blob:https://example.com/id', 'about:blank', 'ftp://example.com/path',
        'JaVaScRiPt:window.executed++', 'java\rscript:window.executed++',
        'https://', '//outside.example/path'];
      const rich = document.createElement('div');
      rich.contentEditable = 'true';
      // Literal markup in an existing selection must remain text after linking.
      const literal = '<img src=x onerror="window.executed++">';
      const bold = document.createElement('strong');
      bold.textContent = literal;
      rich.appendChild(bold);
      document.body.appendChild(rich);
      const range = document.createRange();
      range.selectNodeContents(rich);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      rich.focus();
      const internal = location.origin + '/static/index.html?session=valid#doc';
      const inserted = insert(rich, internal);
      const link = rich.querySelector('a');
      const result = { rejected: rejected.map(_smartRichPasteUrl), inserted,
        href: link?.href, internal, text: link?.textContent, literal,
        bold: link?.querySelector('strong')?.textContent,
        unsafe: rich.querySelectorAll('img,script,[onerror],[onload]').length,
        mail: _smartRichPasteUrl('person@example.com'), tel: _smartRichPasteUrl('tel:+12345'),
        external: _smartRichPasteUrl('https://outside.example/path?q=1#fragment'),
        domain: _smartRichPasteUrl('example.com/path'),
        network: _smartRichPasteUrl('//outside.example/path'),
        deceptive: _smartRichPasteUrl('https://internal.example@outside.example/path'),
        executed: window.executed };
      rich.innerHTML = cleanPaste('<p><strong onmouseover="window.executed++"><a href="javascript:window.executed++">Safe selection</a></strong></p>');
      const pastedRange = document.createRange();
      pastedRange.selectNodeContents(rich.querySelector('p'));
      getSelection().removeAllRanges();
      getSelection().addRange(pastedRange);
      result.cleanSelection = insert(rich, 'https://outside.example/path');
      result.cleanText = rich.querySelector('a')?.textContent;
      result.cleanUnsafe = rich.querySelectorAll('[onmouseover], a[href^="javascript:"]').length;

      const collapsed = document.createRange();
      collapsed.selectNodeContents(rich);
      collapsed.collapse(false);
      getSelection().removeAllRanges();
      getSelection().addRange(collapsed);
      result.collapsed = insert(rich, 'https://outside.example/\"><svg/onload=window.executed++>');
      result.quoteUnsafe = rich.querySelectorAll('svg,[onload]').length;

      rich.innerHTML = '<p>First</p><p>Second</p>';
      const crossing = document.createRange();
      crossing.setStart(rich.firstChild.firstChild, 0);
      crossing.setEnd(rich.lastChild.firstChild, 6);
      getSelection().removeAllRanges();
      getSelection().addRange(crossing);
      result.crossing = insert(rich, internal);
      const outside = document.createRange();
      outside.selectNodeContents(document.getElementById('doc-editor-textarea'));
      getSelection().removeAllRanges();
      getSelection().addRange(outside);
      result.outside = insert(rich, internal);
      return result;
    }, functions);
    assert.deepEqual(links.rejected, Array(13).fill(''));
    assert.equal(links.inserted, true);
    assert.equal(links.href, links.internal);
    assert.equal(links.text, links.literal);
    assert.equal(links.bold, links.literal);
    assert.equal(links.unsafe, 0);
    assert.equal(links.executed, 0);
    assert.equal(links.mail, 'mailto:person@example.com');
    assert.equal(links.tel, 'tel:+12345');
    assert.equal(links.external, 'https://outside.example/path?q=1#fragment');
    assert.equal(links.domain, 'https://example.com/path');
    assert.equal(links.network, '');
    assert.equal(new URL(links.deceptive).hostname, 'outside.example');
    assert.equal(links.cleanSelection, true);
    assert.equal(links.cleanText, 'Safe selection');
    assert.equal(links.cleanUnsafe, 0);
    assert.equal(links.collapsed, true);
    assert.equal(links.quoteUnsafe, 0);
    assert.equal(links.crossing, false);
    assert.equal(links.outside, false);

    // Exercise the sanitizer itself, then the exact live HTML insertion path.
    const markdown = await page.evaluate(async functions => {
      const mod = await import('/static/js/markdown.js');
      const markdownModule = mod;
      const _normalizeRichLinkUrl = eval('(' + functions._normalizeRichLinkUrl + ')');
      const cleanPaste = eval('(' + functions._cleanRichTextPasteHtml + ')');
      const payloads = [
        '<script>window.executed++</script><img src="data:,bad" onerror="window.executed++">',
        '<a href="java&#x09;script:window.executed++" onclick="window.executed++">click</a>',
        '<a href="data:text/html,<script>window.executed++</script>">data</a>',
        '<img srcset="/safe.png 1x, data:image/svg+xml,bad 2x" onload="window.executed++">',
        '<svg><foreignObject><img src=x onerror="window.executed++"></foreignObject><script>window.executed++</script></svg>',
        '<math><mtext><img src=x onerror="window.executed++"></mtext></math>',
        '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=window.executed++>">',
        '<svg><p><style><g title="</style><img src=x onerror=window.executed++>">',
        '<details><summary onmouseover="window.executed++">Nested</summary><p style="background:url(javascript:window.executed++)"><a href="javascript:window.executed++">bad</a></p></details>',
        '<iframe srcdoc="<script>parent.executed++</script>"></iframe><object data="data:text/html,bad"></object>',
        '<noscript><p title="</noscript><img src=x onerror=window.executed++>">',
        '<a href="jav&#97;script:window.executed++" ONCLICK="window.executed++">entity</a>',
        '<a href="&#x0d;&#x0a;JaVaScRiPt:window.executed++">controls</a>',
        '<a href="vbscript:msgbox(1)">legacy</a><img src="data:image/svg+xml,bad">',
        '<template><img src=x onerror="window.executed++"></template>',
        '<details open ontoggle="window.executed++"><summary>Event</summary><p>Text</p></details>',
        '<svg><a xlink:href="javascript:window.executed++"><text>SVG link</text></a></svg>',
        '<math><annotation-xml encoding="text/html"><img src=x onerror="window.executed++"></annotation-xml></math>',
        '<table><caption><details><summary onclick="window.executed++">Nested</summary><img src=x onerror="window.executed++"></details></caption></table>',
      ];
      const box = document.createElement('div');
      document.body.appendChild(box);
      let unsafe = 0;
      for (const payload of payloads) {
        const clean = mod.sanitizeAllowedHtml(payload);
        if (mod.sanitizeAllowedHtml(clean) !== clean) throw new Error('Sanitizer did not stabilize');
        for (const html of [clean, cleanPaste(payload)]) {
          // Include repeated serialize/reparse boundaries used by chat and paste.
          box.innerHTML = html;
          for (let pass = 0; pass < 3; pass++) box.innerHTML = box.innerHTML;
          unsafe += box.querySelectorAll('script,svg,math,iframe,object,embed,style,base,meta,template,noscript').length;
          for (const el of box.querySelectorAll('*')) for (const attr of el.attributes) {
            const value = attr.value.replace(/[\u0000-\u0020\u007f-\u009f]+/g, '').toLowerCase();
            if (attr.name.startsWith('on') || attr.name === 'srcdoc'
                || (/^(href|src|srcset|style)$/.test(attr.name) && /javascript:|vbscript:|data:/.test(value))) unsafe++;
          }
          box.querySelectorAll('a').forEach(a => a.click());
        }
      }
      box.innerHTML = mod.sanitizeAllowedHtml('<details><summary>Title</summary><b>Bold</b><a href="https://example.com/path">Link</a></details>');
      await new Promise(resolve => setTimeout(resolve, 100));
      return { count: payloads.length, unsafe, executed: window.executed, bold: box.querySelector('b')?.textContent,
        href: box.querySelector('a')?.href, details: !!box.querySelector('details') };
    }, functions);
    assert.equal(markdown.count, 19);
    assert.equal(markdown.unsafe, 0);
    assert.equal(markdown.executed, 0);
    assert.equal(markdown.bold, 'Bold');
    assert.equal(markdown.href, 'https://example.com/path');
    assert.equal(markdown.details, true);

    // Run the real print function. Only the OS dialog is replaced; HTML parsing,
    // frame policy and renderMath are the production implementations.
    const print = await page.evaluate(async functions => {
      const mod = await import('/static/js/markdown.js');
      const activeDocId = 'fixture';
      const _isRichTextLang = lang => lang === 'richtext';
      const _isDocxLang = () => false;
      const _getExportBaseName = () => 'Print <test>';
      const _richTextExportCss = () => 'b { font-weight: bold; }';
      const failures = [];
      const uiModule = { showError: text => failures.push(text) };
      let prints = 0;
      const markdownModule = { ...mod, renderMath(container) {
        container.ownerDocument.defaultView.print = () => { prints++; };
        container.ownerDocument.defaultView.focus = () => {};
        return mod.renderMath(container);
      } };
      document.body.insertAdjacentHTML('beforeend', '<select id="doc-language-select"><option>richtext</option></select>');
      const payload = '<b>Printable</b><script>parent.executed++</script>'
        + '<img src="data:,bad" onerror="parent.executed++">'
        + '<svg onload="parent.executed++"></svg>'
        + '<iframe sandbox="allow-scripts allow-same-origin" srcdoc="<script>top.executed++</script><img src=x onerror=top.executed++>"></iframe>'
        + '<a href="javascript:parent.executed++">bad link</a>';
      document.getElementById('doc-editor-textarea').value = payload;
      await eval('(' + functions.exportAsPdf + ')')();
      const frame = document.getElementById('doc-browser-print-frame');
      frame.contentDocument.querySelector('a').click();
      await new Promise(resolve => setTimeout(resolve, 150));
      const result = { prints, failures, sandbox: frame.getAttribute('sandbox'),
        text: frame.contentDocument.querySelector('b')?.textContent,
        title: frame.contentDocument.title, executed: window.executed };
      frame.remove();
      // Without sandbox, the same event/srcdoc payload must execute.
      const control = document.createElement('iframe');
      control.srcdoc = payload;
      document.body.appendChild(control);
      await new Promise(resolve => setTimeout(resolve, 150));
      result.controlExecuted = window.executed;
      control.remove();
      window.executed = 0;
      return result;
    }, functions);
    assert.equal(print.prints, 1);
    assert.deepEqual(print.failures, []);
    assert.equal(print.sandbox, 'allow-same-origin allow-modals');
    assert.equal(print.text, 'Printable');
    assert.equal(print.title, 'Print <test>');
    assert.equal(print.executed, 0);
    assert(print.controlExecuted > 0);

    // Both appendChild findings follow tainted card._md, an ordinary JS
    // property. Rendering and re-rendering must keep that markdown as text.
    const skillPayload = '<img src=x onerror="window.executed++"><svg onload="window.executed++"></svg>';
    await page.route('**/api/skills', route => route.fulfill({ json: { skills: [
      { name: 'user-fixture', source: 'user', status: 'published', confidence: 1,
        description: skillPayload, tags: [skillPayload] },
      { name: 'builtin-fixture', source: 'builtin', status: 'published', confidence: 1,
        description: skillPayload, tags: [skillPayload] },
    ] } }));
    await page.route('**/static/js/skills-pr6503-harness.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: readFileSync('static/js/skills.js', 'utf8') + '\nexport { _mdCache, _expandSkillCard, _toggleSkillEdit, _saveSkillEdit };\n',
    }));
    const skills = await page.evaluate(async payload => {
      document.body.insertAdjacentHTML('beforeend', '<div id="toast"></div><div id="skills-list"></div>');
      const mod = await import('/static/js/skills-pr6503-harness.js');
      mod._mdCache.set('user-fixture', payload);
      mod._mdCache.set('builtin-fixture', payload);
      await mod.loadSkills();
      for (const card of document.querySelectorAll('#skills-list .skill-card')) {
        await mod._expandSkillCard(card, card.dataset.skillName);
      }
      await mod.loadSkills();
      const cards = [...document.querySelectorAll('#skills-list .skill-card')];
      for (const card of cards) await mod._expandSkillCard(card, card.dataset.skillName);
      return { sections: cards.map(card => card.dataset.skillSection).sort(),
        text: cards.map(card => card.querySelector('.skill-md-pre').textContent),
        stored: cards.map(card => card._md),
        descriptions: cards.map(card => card.querySelector('.skill-card-desc').textContent),
        tags: cards.map(card => card.querySelector('.skill-tag-pill').textContent),
        unsafe: document.querySelectorAll('#skills-list img, #skills-list script, #skills-list [onerror], #skills-list [onload]').length,
        executed: window.executed };
    }, skillPayload);
    assert.deepEqual(skills.sections, ['builtin', 'user']);
    assert.deepEqual(skills.text, [skillPayload, skillPayload]);
    assert.deepEqual(skills.stored, [skillPayload, skillPayload]);
    assert.deepEqual(skills.descriptions, [skillPayload, skillPayload]);
    assert.deepEqual(skills.tags, [skillPayload, skillPayload]);
    assert.equal(skills.unsafe, 0);
    assert.equal(skills.executed, 0);

    // Also trace the real API -> cache -> textContent path with a cold cache;
    // the seeded fixtures above exercise the preserved-card rerender path.
    const fetchedSkills = new Set();
    let postedSkillMarkdown;
    await page.route('**/api/skills/*/markdown', route => {
      if (route.request().method() === 'POST') {
        postedSkillMarkdown = route.request().postDataJSON().markdown;
        return route.fulfill({ json: {} });
      }
      fetchedSkills.add(decodeURIComponent(new URL(route.request().url()).pathname.split('/')[3]));
      return route.fulfill({ json: { markdown: skillPayload } });
    });
    const coldSkills = await page.evaluate(async () => {
      const mod = await import('/static/js/skills-pr6503-harness.js');
      mod._mdCache.clear();
      document.querySelectorAll('#skills-list .skill-card').forEach(card => card.remove());
      await mod.loadSkills();
      const cards = [...document.querySelectorAll('#skills-list .skill-card')];
      for (const card of cards) await mod._expandSkillCard(card, card.dataset.skillName);
      await new Promise(resolve => setTimeout(resolve, 100));
      return { text: cards.map(card => card.querySelector('.skill-md-pre').textContent),
        stored: cards.map(card => card._md),
        unsafe: document.querySelectorAll('#skills-list img, #skills-list script, #skills-list [onerror], #skills-list [onload]').length,
        executed: window.executed };
    });
    assert.deepEqual([...fetchedSkills].sort(), ['builtin-fixture', 'user-fixture']);
    assert.deepEqual(coldSkills.text, [skillPayload, skillPayload]);
    assert.deepEqual(coldSkills.stored, [skillPayload, skillPayload]);
    assert.equal(coldSkills.unsafe, 0);
    assert.equal(coldSkills.executed, 0);

    // The alert's actual source is the edit textarea at skills.js:1312.
    const editedPayload = '" & <script>window.executed++</script>' + skillPayload;
    const editedSkill = await page.evaluate(async payload => {
      const mod = await import('/static/js/skills-pr6503-harness.js');
      const card = document.querySelector('[data-skill-name="user-fixture"]');
      if (!card.classList.contains('doclib-card-expanded')) await mod._expandSkillCard(card, 'user-fixture');
      mod._toggleSkillEdit(card, 'user-fixture');
      card.querySelector('.skill-md-editor').value = payload;
      await mod._saveSkillEdit(card, 'user-fixture');
      const restored = document.querySelector('[data-skill-name="user-fixture"]');
      await mod._expandSkillCard(restored, 'user-fixture');
      await new Promise(resolve => setTimeout(resolve, 100));
      return { text: restored.querySelector('.skill-md-pre').textContent, stored: restored._md,
        unsafe: restored.querySelectorAll('img,script,[onerror],[onload]').length, executed: window.executed };
    }, editedPayload);
    assert.equal(postedSkillMarkdown, editedPayload);
    assert.equal(editedSkill.text, editedPayload);
    assert.equal(editedSkill.stored, editedPayload);
    assert.equal(editedSkill.unsafe, 0);
    assert.equal(editedSkill.executed, 0);

    // #767 reparses the instruction read from a rendered message's textContent.
    // Exercise the real addMessage path in every selected browser too.
    const chat = await page.evaluate(async () => {
      document.body.insertAdjacentHTML('beforeend',
        '<div id="sidebar"></div><div id="chat-container"></div><div id="chat-history"></div>');
      const { addMessage } = await import('/static/js/chatRenderer.js');
      const payloads = [
        '<img src=x onerror="window.executed++">',
        '<details><summary>Nested</summary><a href="java&#x09;script:window.executed++">bad</a><img src=x onerror="window.executed++"></details>',
        '<math><mtext><img src=x onerror="window.executed++"></mtext></math>',
        '<think><svg onload="window.executed++"></svg></think>**Valid** instruction',
      ];
      const refs = [];
      let unsafe = 0;
      for (const payload of payloads) {
        const message = addMessage('user', 'In the document, edit this specific text (lines 1–2):\n```\nselected\n```\n\nInstruction: ' + payload);
        if (!message) throw new Error('addMessage failed');
        refs.push(message.querySelector('.doc-edit-tag')?.dataset.docEditRef);
        unsafe += message.querySelector('.body').querySelectorAll('script,math,svg[onload],[onerror],[onload],a[href^="javascript:"]').length;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
      return { refs, unsafe, executed: window.executed };
    });
    assert.deepEqual(chat.refs, Array(4).fill('lines 1–2'));
    assert.equal(chat.unsafe, 0);
    assert.equal(chat.executed, 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ledger: true, email: true, gallery: true, links: true, skills: true,
      sanitizer: true, print: true }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
