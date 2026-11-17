/**
 * Browser print regression tests for Email Library print and PDF export.
 *
 * Requirements:
 * - Node.js (v20+)
 * - Playwright (`npm ci` or `npm install -D @playwright/test`)
 * - Chromium browser binary:
 *     - Standard Playwright install: `npx playwright install chromium`
 *     - Or custom executable via env vars:
 *         - `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chrome`
 *         - `CHROME_PATH=/path/to/chrome`
 *     - Or custom browsers cache dir via:
 *         - `PLAYWRIGHT_BROWSERS_PATH=/path/to/cache`
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (err) {
  if (err && err.code === 'MODULE_NOT_FOUND') {
    console.error(
      'Error: Playwright module not found.\n' +
      'Please install dependencies by running:\n' +
      '  npm ci\n' +
      'or ensure NODE_PATH contains the playwright package.'
    );
    process.exit(1);
  }
  throw err;
}

const ROOT = path.resolve(__dirname, '..');

// Discover available Chromium executable using standard Playwright resolution,
// custom environment variables, standard cache paths, or system binaries.
function findChromiumPath() {
  // 1. Explicit environment variable overrides
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH && fs.existsSync(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }

  // 2. Playwright's configured/default executable path
  try {
    const defaultPath = chromium.executablePath();
    if (defaultPath && fs.existsSync(defaultPath)) {
      return defaultPath;
    }
  } catch (e) {
    // chromium.executablePath() may throw if no default browser is installed
  }

  // 3. Inspect standard Playwright browser cache directories
  const cacheDirs = [];
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    cacheDirs.push(process.env.PLAYWRIGHT_BROWSERS_PATH);
  }
  if (process.platform === 'win32') {
    if (process.env.LOCALAPPDATA) {
      cacheDirs.push(path.join(process.env.LOCALAPPDATA, 'ms-playwright'));
    }
    cacheDirs.push(path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright'));
  } else if (process.platform === 'darwin') {
    cacheDirs.push(path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright'));
  } else {
    cacheDirs.push(path.join(os.homedir(), '.cache', 'ms-playwright'));
  }

  for (const cacheDir of cacheDirs) {
    if (!fs.existsSync(cacheDir)) continue;
    try {
      const entries = fs.readdirSync(cacheDir).sort().reverse();
      for (const entry of entries) {
        const entryPath = path.join(cacheDir, entry);
        const candidates = [
          path.join(entryPath, 'chrome-headless-shell-linux64', 'chrome-headless-shell'),
          path.join(entryPath, 'chrome-linux64', 'chrome'),
          path.join(entryPath, 'chrome-linux', 'chrome'),
          path.join(entryPath, 'chrome-win64', 'chrome.exe'),
          path.join(entryPath, 'chrome-win', 'chrome.exe'),
          path.join(entryPath, 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'),
        ];
        for (const candidate of candidates) {
          if (fs.existsSync(candidate)) {
            return candidate;
          }
        }
      }
    } catch (e) {}
  }

  // 4. System browser fallbacks
  const systemCandidates = process.platform === 'win32' ? [
    path.join(process.env['PROGRAMFILES'] || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)', 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ] : [
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
  ];
  for (const sc of systemCandidates) {
    if (fs.existsSync(sc)) return sc;
  }

  return undefined;
}

async function run() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/email/read/')) {
      const uid = url.pathname.replace('/api/email/read/', '');
      if (uid === 'fail_uid' || uid === 'net_fail') {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Mailbox disconnected' }));
        return;
      }
      if (uid === 'crash_network') {
        req.destroy();
        return;
      }

      const markSeen = url.searchParams.get('mark_seen');
      if (markSeen !== 'false') {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'mark_seen must be false' }));
        return;
      }

      let body_html = '';
      if (uid === 'img_test') {
        body_html = [
          '<div>',
          '<p>Email with diverse images</p>',
          '<!-- Inline image with alt text -->',
          '<img src="cid:logo123" alt="Company Brand Logo">',
          '<!-- Remote image with title -->',
          '<img src="https://example.com/tracker.png" title="Tracking Pixel">',
          '<!-- Image without alt or title -->',
          '<img src="https://example.com/no-alt.jpg">',
          '<!-- Interactive control that shouldn\'t be in print -->',
          '<button type="button" class="btn-action">Interactive Action</button>',
          '<!-- XSS payload -->',
          '<script>window.__pwned = true;</script>',
          '</div>'
        ].join('\n');
      } else if (uid === 'long_msg') {
        body_html = `<div><p>${'A'.repeat(5000)}</p></div>`;
      } else {
        body_html = `<div><p>Standard message body for ${uid}</p></div>`;
      }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        uid: uid,
        subject: `Subject ${uid}`,
        from_name: 'Sender',
        from_address: 'sender@example.com',
        to: 'user@example.com',
        date: '2026-10-20',
        date_display: 'Oct 20, 2026',
        body: `Plain text for ${uid}`,
        body_html: body_html,
      }));
      return;
    }

    let filePath = path.join(ROOT, url.pathname);
    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
      const ext = path.extname(filePath);
      const mime = ext === '.js' ? 'application/javascript' : ext === '.html' ? 'text/html' : ext === '.css' ? 'text/css' : 'text/plain';
      res.writeHead(200, { 'Content-Type': mime });
      res.end(fs.readFileSync(filePath));
      return;
    }
    res.writeHead(404);
    res.end('Not found');
  });

  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;

  const launchOpts = { headless: true };
  const chromePath = findChromiumPath();
  if (chromePath) launchOpts.executablePath = chromePath;

  let browser;
  try {
    browser = await chromium.launch(launchOpts);
  } catch (launchErr) {
    throw new Error(
      `Failed to launch Chromium browser: ${launchErr.message}\n` +
      'Missing or unexecutable Chromium binary.\n' +
      'To install Playwright Chromium, run:\n' +
      '  npx playwright install chromium\n' +
      'Or set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to a valid Chromium executable.'
    );
  }
  const context = await browser.newContext();
  const page = await context.newPage();

  const results = {};

  try {
    await page.goto(`${origin}/static/index.html`);
    await page.evaluate(() => {
      if (!document.getElementById('email-lib-bulk-actions')) {
        const d = document.createElement('div'); d.id = 'email-lib-bulk-actions'; document.body.appendChild(d);
      }
      if (!document.getElementById('email-lib-selected-count')) {
        const d = document.createElement('div'); d.id = 'email-lib-selected-count'; document.body.appendChild(d);
      }
    });

    // TEST 1: Full Print Image Sanitization and Replacement
    const printImageResult = await page.evaluate(async (base) => {
      window.API_BASE = base;
      const mod = await import(`${base}/static/js/emailLibrary/index.js`);
      const stateMod = await import(`${base}/static/js/emailLibrary/state.js`);

      stateMod.state._selectedUids = new Set(['img_test']);
      stateMod.state._libEmails = [{ uid: 'img_test', subject: 'Image Test', folder: 'INBOX' }];

      let iframePrintInvoked = false;
      let printDocHtml = '';
      let afterPrintTriggered = false;
      let hasPlaceholderElement = false;

      const observer = new MutationObserver(() => {
        const frame = document.getElementById('email-browser-print-frame');
        if (frame && frame.contentWindow && !frame._patched) {
          frame._patched = true;
          frame.contentWindow.print = function() {
            iframePrintInvoked = true;
            printDocHtml = frame.contentDocument ? frame.contentDocument.documentElement.outerHTML : '';
            const body = frame.contentDocument ? frame.contentDocument.body : null;
            hasPlaceholderElement = !!body?.querySelector('.email-inline-image-placeholder');
            frame.contentWindow.dispatchEvent(new Event('afterprint'));
            afterPrintTriggered = true;
          };
        }
      });
      observer.observe(document.body, { childList: true });

      await mod._exportSelectedEmailPdf();
      observer.disconnect();

      const frameAfter = document.getElementById('email-browser-print-frame');

      return {
        iframePrintInvoked,
        afterPrintTriggered,
        frameRemovedAfterPrint: frameAfter === null,
        hasLogoNotice: printDocHtml.includes('[Image: Company Brand Logo]'),
        hasTrackerNotice: printDocHtml.includes('[Image: Tracking Pixel]'),
        hasNoAltNotice: printDocHtml.includes('[Image omitted]'),
        hasPlaceholderElement,
        hasRemoteImageBlockedText: printDocHtml.includes('Remote image blocked'),
        hasDownloadButton: printDocHtml.includes('email-inline-image-download-btn'),
        hasInteractiveActionBtn: printDocHtml.includes('btn-action'),
        hasXSS: printDocHtml.includes('<script>') || printDocHtml.includes('window.__pwned'),
      };
    }, origin);

    results.printImageSanitization = printImageResult;

    // TEST 2: Partial Failure - 1 failure out of 3 emails
    const partialFailureResult = await page.evaluate(async (base) => {
      window.API_BASE = base;
      const mod = await import(`${base}/static/js/emailLibrary/index.js`);
      const stateMod = await import(`${base}/static/js/emailLibrary/state.js`);

      const messages = [
        { uid: 'msg1', subject: 'Email One', folder: 'INBOX' },
        { uid: 'fail_uid', subject: 'Failing Email', folder: 'INBOX' },
        { uid: 'msg2', subject: 'Email Two', folder: 'INBOX' },
      ];
      stateMod.state._selectedUids = new Set(messages.map(m => m.uid));
      stateMod.state._libEmails = messages;

      let printInvoked = false;
      let printDocHtml = '';

      const observer = new MutationObserver(() => {
        const frame = document.getElementById('email-browser-print-frame');
        if (frame && frame.contentWindow && !frame._patched) {
          frame._patched = true;
          frame.contentWindow.print = function() {
            printInvoked = true;
            printDocHtml = frame.contentDocument ? frame.contentDocument.documentElement.outerHTML : '';
            frame.contentWindow.dispatchEvent(new Event('afterprint'));
          };
        }
      });
      observer.observe(document.body, { childList: true });

      await mod._exportSelectedEmailPdf();
      observer.disconnect();

      const toast = document.getElementById('toast')?.textContent || '';

      return {
        printInvoked,
        toast,
        hasMsg1: printDocHtml.includes('Subject msg1'),
        hasMsg2: printDocHtml.includes('Subject msg2'),
        hasOmittedNotice: printDocHtml.includes('1 selected email was unavailable and omitted from this print'),
      };
    }, origin);

    results.partialFailureTest = partialFailureResult;

    // TEST 3: All emails fail
    const allFailedResult = await page.evaluate(async (base) => {
      window.API_BASE = base;
      const mod = await import(`${base}/static/js/emailLibrary/index.js`);
      const stateMod = await import(`${base}/static/js/emailLibrary/state.js`);

      const messages = [
        { uid: 'fail_uid', subject: 'Fail 1', folder: 'INBOX' },
        { uid: 'net_fail', subject: 'Fail 2', folder: 'INBOX' },
      ];
      stateMod.state._selectedUids = new Set(messages.map(m => m.uid));
      stateMod.state._libEmails = messages;

      let printInvoked = false;
      const observer = new MutationObserver(() => {
        const frame = document.getElementById('email-browser-print-frame');
        if (frame && frame.contentWindow && !frame._patched) {
          frame._patched = true;
          frame.contentWindow.print = function() {
            printInvoked = true;
          };
        }
      });
      observer.observe(document.body, { childList: true });

      let thrownError = null;
      try {
        await mod._exportSelectedEmailPdf();
      } catch (err) {
        thrownError = err.message || String(err);
      }
      observer.disconnect();

      const frame = document.getElementById('email-browser-print-frame');
      const toast = document.getElementById('toast')?.textContent || '';

      return {
        printInvoked,
        frameOpened: frame !== null,
        thrownError,
        toast,
      };
    }, origin);

    results.allFailedTest = allFailedResult;

    // TEST 4: Idempotent Iframe Cleanup & Window Afterprint
    const cleanupResult = await page.evaluate(async (base) => {
      window.API_BASE = base;
      const mod = await import(`${base}/static/js/emailLibrary/index.js`);
      const stateMod = await import(`${base}/static/js/emailLibrary/state.js`);

      stateMod.state._selectedUids = new Set(['msg1']);
      stateMod.state._libEmails = [{ uid: 'msg1', subject: 'Cleanup Test', folder: 'INBOX' }];

      const observer = new MutationObserver(() => {
        const frame = document.getElementById('email-browser-print-frame');
        if (frame && frame.contentWindow && !frame._patched) {
          frame._patched = true;
          frame.contentWindow.print = function() {
            window.dispatchEvent(new Event('afterprint'));
          };
        }
      });
      observer.observe(document.body, { childList: true });

      await mod._exportSelectedEmailPdf();
      observer.disconnect();

      const frameAfter = document.getElementById('email-browser-print-frame');
      return {
        frameRemovedViaWindowAfterprint: frameAfter === null,
      };
    }, origin);

    results.cleanupTest = cleanupResult;

  } finally {
    await browser.close();
    server.close();
  }

  // Assertions
  const t1 = results.printImageSanitization;
  if (!t1.iframePrintInvoked) throw new Error('Assertion failed: print not invoked');
  if (!t1.frameRemovedAfterPrint) throw new Error('Assertion failed: frame not removed');
  if (!t1.hasLogoNotice) throw new Error('Assertion failed: [Image: Company Brand Logo] missing');
  if (!t1.hasTrackerNotice) throw new Error('Assertion failed: [Image: Tracking Pixel] missing');
  if (!t1.hasNoAltNotice) throw new Error('Assertion failed: [Image omitted] missing');
  if (t1.hasPlaceholderElement) throw new Error('Assertion failed: .email-inline-image-placeholder found in print doc');
  if (t1.hasRemoteImageBlockedText) throw new Error('Assertion failed: Remote image blocked leaked into print doc');
  if (t1.hasDownloadButton) throw new Error('Assertion failed: download button leaked into print doc');
  if (t1.hasInteractiveActionBtn) throw new Error('Assertion failed: interactive button leaked into print doc');
  if (t1.hasXSS) throw new Error('Assertion failed: script tag found');

  const t2 = results.partialFailureTest;
  if (!t2.printInvoked) throw new Error('Assertion failed: partial print was not invoked');
  if (!t2.hasMsg1 || !t2.hasMsg2) throw new Error('Assertion failed: successful messages missing from print');
  if (!t2.hasOmittedNotice) throw new Error('Assertion failed: omitted notice banner missing');
  if (!t2.toast.includes('Prepared 2 emails for printing; 1 unavailable')) {
    throw new Error('Assertion failed: toast text mismatch: ' + t2.toast);
  }

  const t3 = results.allFailedTest;
  if (t3.printInvoked) throw new Error('Assertion failed: print should NOT be invoked when all fail');
  if (t3.frameOpened) throw new Error('Assertion failed: print frame should NOT remain in DOM');

  const t4 = results.cleanupTest;
  if (!t4.frameRemovedViaWindowAfterprint) throw new Error('Assertion failed: top window afterprint did not clean up frame');

  console.log('ALL_BROWSER_TESTS_PASSED');
}

run().catch(err => {
  console.error('Browser test failed:', err);
  process.exit(1);
});
