// Behavioral browser checks for the accepted publication surface.
const { chromium } = require('playwright');
const { readFileSync } = require('fs');
const assert = require('node:assert/strict');
const { extractThemeBootstrap } = require('./helpers/theme_bootstrap.cjs');

(async () => {
  const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const mediaRequests = [];
    const errors = [];
    page.on('request', req => { if (/\.(webm|mp4)(?:$|\?)/.test(req.url())) mediaRequests.push(req.url()); });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(origin + '/website/index.html');
    assert.equal(await page.locator('video, .preview-panel, .sec-bg-tint').count(), 0);
    assert.equal(await page.locator('.feature-description').count(), 8);
    for (const description of await page.locator('.feature-description .desc').allTextContents()) assert(description.trim());
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert(await page.locator('.feature-description').first().isVisible());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    assert.deepEqual(mediaRequests, []);
    assert.deepEqual(errors, []);

    const manifest = await (await page.request.get(origin + '/static/manifest.json')).json();
    assert.equal(manifest.display, 'standalone');
    assert(!manifest.icons);
    for (const filename of ['index.html', 'login.html']) {
      const html = await (await page.request.get(origin + '/static/' + filename)).text();
      assert(!/<link[^>]+rel=["']apple-touch-icon/.test(html));
    }

    // A small host DOM runs the actual export function and markdown renderer.
    await page.route('**/publication-harness', route => route.fulfill({ contentType: 'text/html', body: '<!DOCTYPE html><body><textarea id="doc-editor-textarea"></textarea><select id="doc-language-select"><option value="markdown">Markdown</option><option value="text">Text</option><option value="html">HTML</option></select><div id="theme-grid"></div><select id="theme-font-select"><option value="mono">Fira Code</option><option value="sans">Sans</option></select><select id="theme-density-select"><option value="comfortable">Comfortable</option></select></body>' }));
    await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ fonts: {}, value: null }) }));
    await page.goto(origin + '/publication-harness');
    const source = readFileSync('static/js/document.js', 'utf8');
    const fn = source.match(/\n  async function exportAsPdf\(\) \{(.*?)\n  \}\n/s)[0];
    const result = await page.evaluate(async ({ origin, fn }) => {
      const module = await import(origin + '/static/js/markdown.js');
      const markdownModule = module.default || module;
      const _isRichTextLang = lang => lang === 'html';
      const _isDocxLang = () => false;
      const _richTextExportCss = () => 'p { color: black; }';
      const _getExportBaseName = () => 'Print test';
      const activeDocId = 'test';
      const errors = [];
      const uiModule = { showError: message => errors.push(message) };
      const originalRender = markdownModule.renderMath;
      const prints = [];
      markdownModule.renderMath = async container => {
        await originalRender(container);
        const frame = document.getElementById('doc-browser-print-frame');
        frame.contentWindow.print = () => {
          prints.push({ body: frame.contentDocument.body.innerHTML, title: frame.contentDocument.title, links: [...frame.contentDocument.querySelectorAll('link')].map(l => l.href) });
          frame.contentWindow.dispatchEvent(new Event('afterprint'));
        };
      };
      const run = eval('(' + fn.trim() + ')');
      const textarea = document.getElementById('doc-editor-textarea');
      textarea.value = 'Formula $E = mc^2$ here.';
      await run();
      document.getElementById('doc-language-select').value = 'text';
      textarea.value = '<script>parent.unsafe = true</script>& literal';
      await run();
      document.getElementById('doc-language-select').value = 'html';
      textarea.value = '<p>Rich content</p><script>parent.unsafe = true</script><img src="data:,x" onerror="parent.unsafe = true">';
      await run();
      const oldPrint = window.print;
      window.print = undefined;
      await run();
      window.print = oldPrint;
      return { prints, errors, unsafe: !!window.unsafe, frameRemaining: !!document.getElementById('doc-browser-print-frame') };
    }, { origin, fn });
    assert.equal(result.prints.length, 3);
    assert(result.prints[0].body.includes('class="katex"'));
    assert(!result.prints[0].body.includes('ody-math-pending'));
    assert(result.prints[0].links.some(url => url.includes('/katex/')));
    assert.equal(result.prints[0].title, 'Print test');
    assert(result.prints[1].body.includes('&lt;script&gt;'));
    assert(result.prints[2].body.includes('Rich content'));
    assert.equal(result.unsafe, false);
    assert.equal(result.frameRemaining, false);
    assert.deepEqual(result.errors, ['Browser printing is unavailable.']);

    // Actual theme module: default, legacy local/server selections and custom theme preferences.
    const fonts = await page.evaluate(async origin => {
      localStorage.setItem('odysseus-theme', JSON.stringify({ name: 'dark', colors: {}, font: 'gohu' }));
      localStorage.setItem('odysseus-custom-themes', JSON.stringify({ old: { font: 'GohuFont' } }));
      const theme = await import(origin + '/static/js/theme.js');
      theme.applyFontDensity(null, null);
      const fallback = document.documentElement.style.getPropertyValue('--font-family');
      theme.applyFontDensity('gohu', null);
      const legacy = document.documentElement.style.getPropertyValue('--font-family');
      theme.initThemeUI();
      const selected = document.getElementById('theme-font-select').value;
      const saved = theme.getSaved().font;
      const custom = theme.getCustomThemes().old.font;
      theme.applyFontDensity('sans', null);
      const sans = document.documentElement.style.getPropertyValue('--font-family');
      return { fallback, legacy, selected, saved, custom, sans };
    }, origin);
    assert.equal(fonts.fallback, "'Fira Code', monospace");
    assert.equal(fonts.legacy, fonts.fallback);
    assert.equal(fonts.selected, 'mono');
    assert.equal(fonts.saved, 'mono');
    assert.equal(fonts.custom, 'mono');
    assert(fonts.sans.includes('system-ui'));

    // Verify the real early bootstrap handles persisted legacy preferences.
    const appHtml = readFileSync('static/index.html', 'utf8');
    const bootstrap = await page.evaluate(extractThemeBootstrap, appHtml);
    assert(bootstrap);
    const early = await page.evaluate(async code => {
      const theme = await import('/static/js/theme.js');
      localStorage.setItem('odysseus-theme', JSON.stringify({ name: 'dark', colors: theme.THEMES.dark, font: 'gohu' }));
      eval(code);
      return document.documentElement.style.getPropertyValue('--font-family');
    }, bootstrap);
    assert.equal(early, fonts.fallback);
    console.log(JSON.stringify({ website: true, fonts: true, print: true, pwa: true }));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
