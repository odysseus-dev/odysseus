const { chromium } = require('playwright');
const { readFileSync } = require('node:fs');
const assert = require('node:assert/strict');
const { extractThemeBootstrap } = require('./helpers/theme_bootstrap.cjs');

(async () => {
  const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    const svgRequests = [];
    const svgFailures = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.url().includes('example.invalid/security-probe')) svgRequests.push(request.url()); });
    page.on('requestfailed', request => { if (request.url().includes('example.invalid/security-probe')) svgFailures.push(request.failure().errorText); });
    await page.route('**/security-harness', route => route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><div id="toast"></div><div id="sidebar"></div><div id="chat-container"></div><div id="chat-history"></div><div id="adm-epList-local"></div><div id="adm-epList-api"></div>' }));
    const secrets = { access_token: 'ACCESS-SENTINEL', refresh_token: 'REFRESH-SENTINEL', api_key: 'KEY-SENTINEL', password: 'PASSWORD-SENTINEL' };
    const endpoints = ['a', 'b'].map(id => ({ id: 'endpoint-' + id, provider_auth_id: 'session-' + id,
      provider: 'chatgpt-subscription', name: 'ChatGPT · LABEL-SENTINEL', category: 'api',
      base_url: 'https://chatgpt.com/backend-api/codex', is_enabled: true, online: true, models: [], ...secrets }));
    await page.route('**/api/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/model-endpoints') return route.fulfill({ json: endpoints });
      if (url.pathname.endsWith('/usage')) return route.fulfill({ json: { available: true, usage: { limits: [] }, ...secrets } });
      if (url.pathname.includes('/device/')) return route.fulfill({ status: 400, json: { detail: 'Fixture declines device flow' } });
      return route.fulfill({ json: { fonts: {}, value: null, tools: [], models: [] } });
    });
    // Expose the real internal loader only in this served test copy.
    await page.route('**/static/js/admin-codeql-harness.js', route => route.fulfill({ contentType: 'application/javascript',
      body: readFileSync('static/js/admin.js', 'utf8') + '\nexport { loadEndpoints };\n' }));
    await page.route('**/static/js/sessions-codeql-harness.js', route => route.fulfill({ contentType: 'application/javascript',
      body: readFileSync('static/js/sessions.js', 'utf8') + '\nexport { moveToFolder };\n' }));
    await page.goto(origin + '/security-harness');

    const rendered = await page.evaluate(async () => {
      const { addMessage } = await import('/static/js/chatRenderer.js');
      const markdown = await import('/static/js/markdown.js');
      window.executed = 0;
      const payloads = [
        '<img src=x onerror="window.executed++">',
        '<svg onload="parent.executed++"><script>parent.executed++</script></svg>',
        '<script>window.executed++</script>',
        '\"\'><img src=x onerror="window.executed++"> & <angle>',
        '<details><summary>Nested</summary><img src=x onerror="window.executed++"><a href="javascript:window.executed++">link</a><svg onload="window.executed++"></svg></details>',
        '<think><img src=x onerror="window.executed++"></think>**Valid** instruction',
      ];
      const results = [];
      for (const payload of payloads) {
        const message = addMessage('user', 'In the document, edit this specific text (line 1):\n```\nselected\n```\n\nInstruction: ' + payload);
        if (!message) throw new Error('addMessage failed');
        const body = message.querySelector('.body');
        results.push({ tag: body.querySelector('.doc-edit-tag')?.dataset.docEditRef,
          unsafe: body.querySelectorAll('script, svg[onload], [onerror], [onload], a[href^="javascript:"]').length });
        const direct = document.createElement('div');
        direct.innerHTML = markdown.processWithThinking(payload);
        results.push({ unsafe: direct.querySelectorAll('script, [onerror], [onload], a[href^="javascript:"]').length });
        message.remove();
      }
      const valid = addMessage('user', 'In the document, edit this specific text (lines 1–2):\n```\nselected\n```\n\nInstruction: **Keep bold** and `code`');
      const titles = [
        { source: '<svg xmlns="http://www.w3.org/2000/svg"><title>Valid &amp; safe</title></svg>', title: 'Valid & safe' },
        { source: '<svg><title>Numeric &#60;safe&#62; &#x26; sound</title></svg>', title: 'Numeric <safe> & sound' },
        { source: '<svg><title>Nested <b>bold</b> &amp; text</title></svg>', title: 'Visual explanation' },
        { source: '<svg><title>\" onload=\"parent.executed++ &lt;script&gt;</title></svg>', title: '\" onload=\"parent.executed++ <script>' },
        { source: '<svg><title>Malformed <b>nested</title ></svg>', title: 'Visual explanation' },
        { source: '<svg><title><script>parent.executed++</script><b onload="parent.executed++">nested</b></title></svg>', title: 'Visual explanation' },
        { source: '<svg><title><![CDATA["><img src=x onerror="parent.executed++">]]></title></svg>', title: 'Visual explanation' },
        { source: '<svg><title></title><img src=x onerror="parent.executed++"></svg>', title: 'Visual explanation' },
        { source: '<svg><title> </title></svg>', title: 'Visual explanation' },
        { source: '<svg><text>No title</text></svg>', title: 'Visual explanation' },
      ].map(({ source, title }) => {
        const host = document.createElement('div');
        host.innerHTML = markdown.mdToHtml('```svg\n' + source + '\n```');
        document.body.appendChild(host);
        const frame = host.querySelector('iframe');
        return { expected: title, actual: frame.title, sandbox: frame.getAttribute('sandbox'),
          referrer: frame.referrerPolicy, onload: frame.hasAttribute('onload'),
          csp: new DOMParser().parseFromString(frame.srcdoc, 'text/html').querySelector('meta[http-equiv="Content-Security-Policy"]').content };
      });
      const svgHost = document.createElement('div');
      svgHost.innerHTML = markdown.mdToHtml('```svg\n<svg onload="parent.executed++"><title>Attack</title><script>parent.executed++</script><image href="https://example.invalid/security-probe"/></svg>\n```');
      document.body.appendChild(svgHost);
      const directValid = document.createElement('div');
      directValid.innerHTML = markdown.processWithThinking('**Keep bold** and `code`');
      return { results, titles, instruction: valid.querySelector('.body').textContent,
        bold: directValid.querySelector('strong')?.textContent, code: directValid.querySelector('code')?.textContent };
    });
    assert(rendered.results.every(result => result.unsafe === 0));
    assert(rendered.results.filter((result, index) => index % 2 === 0).every(result => result.tag === 'line 1'));
    assert(rendered.instruction.includes('Keep bold and code'));
    assert.equal(rendered.bold, 'Keep bold');
    assert.equal(rendered.code, 'code');
    for (const title of rendered.titles) {
      assert.equal(title.actual, title.expected);
      assert.equal(title.sandbox, '');
      assert.equal(title.referrer, 'no-referrer');
      assert.equal(title.onload, false);
      assert.equal(title.csp, "default-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; style-src 'unsafe-inline'");
    }

    const folderRequest = page.waitForRequest(request => request.method() === 'PATCH');
    const sessionId = 'session/other?folder=bad#fragment%value';
    await page.evaluate(async id => {
      const { moveToFolder } = await import('/static/js/sessions-codeql-harness.js');
      await moveToFolder(id, 'Safe folder');
    }, sessionId);
    const folderUrl = new URL((await folderRequest).url());
    assert.equal(folderUrl.pathname, '/api/session/' + encodeURIComponent(sessionId));
    assert.equal(folderUrl.search, '');
    assert.equal(folderUrl.hash, '');

    const menus = await page.evaluate(async () => {
      const { _showReaderMoreMenu } = await import('/static/js/emailLibrary/menus.js');
      const { _safeRenderEmailBody } = await import('/static/js/emailLibrary/bodyRender.js');
      const reader = document.createElement('div');
      const anchor = document.createElement('button');
      document.body.append(reader, anchor);
      const labels = ['<img src=x onerror="window.executed++">', '<svg onload="window.executed++">', '<script>window.executed++</script>', '\" & <angle>', '<b><i>nested</i></b>'];
      let clicks = 0;
      for (const [index, label] of labels.entries()) {
        const button = document.createElement('button');
        button.className = 'reader-action-overflowed';
        button.innerHTML = '<svg viewBox="0 0 24 24"><path d="M1 1h2"/></svg>';
        if (index === 0) button.title = label;
        else {
          const span = document.createElement('span');
          span.className = 'reader-btn-label';
          span.textContent = label;
          button.appendChild(span);
        }
        button.addEventListener('click', () => clicks++);
        reader.appendChild(button);
      }
      // A remote HTML body can preserve action-like CSS classes and escaped
      // label text, even though the email sanitizer removes its SVG/handlers.
      const remoteLabel = '<img src=x onerror="window.executed++">';
      const remoteBody = document.createElement('div');
      remoteBody.className = 'email-reader-body';
      remoteBody.innerHTML = _safeRenderEmailBody({ body_html:
        '<button class="reader-action-overflowed"><svg onload="window.executed++"></svg><span class="reader-btn-label">&lt;img src=x onerror=&quot;window.executed++&quot;&gt;</span></button>' });
      if (!remoteBody.querySelector('.reader-action-overflowed')) throw new Error('Remote label fixture was lost');
      if (remoteBody.querySelector('svg, [onload], [onerror]')) throw new Error('Email body sanitizer failed');
      reader.appendChild(remoteBody);
      labels.push(remoteLabel);
      _showReaderMoreMenu({ uid: 'fixture' }, document.createElement('div'), reader, anchor, {});
      const menu = document.querySelector('.email-card-dropdown');
      const items = [...menu.querySelectorAll('.dropdown-item-compact')].slice(0, labels.length);
      items[0].click();
      return { labels, actual: items.map(item => item.children[1].textContent), clicks,
        icons: items.slice(0, -1).every(item => item.querySelector('.dropdown-icon svg path')),
        remoteIconRemoved: !items.at(-1).querySelector('svg'),
        arrows: [...menu.querySelectorAll('.dropdown-item-compact')].filter(item => item.lastChild.textContent === '›').length,
        unsafe: menu.querySelectorAll('script, img, [onload], [onerror]').length };
    });
    assert.deepEqual(menus.actual, menus.labels);
    assert.equal(menus.clicks, 1);
    assert.equal(menus.icons, true);
    assert.equal(menus.remoteIconRemoved, true);
    assert(menus.arrows >= 2);
    assert.equal(menus.unsafe, 0);

    await page.evaluate(async () => {
      localStorage.removeItem('odysseus-chatgpt-usage-expanded');
      window.adminTest = await import('/static/js/admin-codeql-harness.js');
      await window.adminTest.loadEndpoints();
    });
    const toggle = id => page.locator(`[data-adm-chatgpt-usage-toggle="session-${id}"]`);
    const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('odysseus-chatgpt-usage-expanded') || '[]').sort());
    assert.deepEqual(await stored(), []);
    await toggle('a').click();
    assert.deepEqual(await stored(), ['endpoint-a', 'session-a']);
    assert.equal(await toggle('a').getAttribute('aria-expanded'), 'true');
    assert.equal(await toggle('b').getAttribute('aria-expanded'), 'false');
    await page.evaluate(() => window.adminTest.loadEndpoints());
    assert.equal(await toggle('a').getAttribute('aria-expanded'), 'true');
    await toggle('a').click();
    assert.deepEqual(await stored(), []);
    await page.locator('.adm-chatgpt-controls [data-adm-chatgpt-reconnect="session-b"]').click();
    assert.deepEqual(await stored(), ['endpoint-b', 'session-b']);
    const storageText = JSON.stringify(await stored());
    for (const secret of [...Object.values(secrets), 'LABEL-SENTINEL']) assert(!storageText.includes(secret));

    const code = '/* Apply font early */ window.executed++';
    const fixtures = [
      `<SCRIPT>${code}</SCRIPT >`,
      `<script data-note=">">${code}</script\t>`,
      `<!-- <script>${code}</script> --><script src="external.js">${code}</script><script>${code}</script>`,
    ];
    for (const html of fixtures) assert.equal(await page.evaluate(extractThemeBootstrap, html), code);
    await assert.rejects(page.evaluate(extractThemeBootstrap, `<script>${code}</script><script>${code}</script>`), /found 2/);
    await assert.rejects(page.evaluate(extractThemeBootstrap, '<script>unrelated</script>'), /found 0/);
    assert.equal(await page.evaluate(() => window.executed), 0);
    // Chromium reports a request event even when CSP prevents network access.
    assert(svgRequests.length > 0);
    assert.equal(svgFailures.length, svgRequests.length);
    assert(svgFailures.every(reason => reason === 'csp'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ chat: true, markdown: true, svg: true, menus: true, admin: true, bootstrap: true }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
