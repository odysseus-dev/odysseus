// Literal same-tab reload recovery of an unsaved Markdown draft, in Firefox and WebKit.
// Chromium is not used here: headless Chromium crashes the renderer on reload in some
// environments, with or without this editor (see the logout regression for the
// diagnostics). Reload recovery has to be proven in an engine that survives a reload.
import assert from 'node:assert/strict';
import { firefox, webkit } from 'playwright';
import { openApp, origin, tolerateNoAuthNotificationLogs } from './realAppHelpers.mjs';

const secret = `RELOAD-DRAFT-${Date.now()}`;
let failures = 0;
for (const [name, engine] of [['firefox', firefox], ['webkit', webkit]]) {
  const browser = await engine.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await tolerateNoAuthNotificationLogs(context);
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
    const session = await (await context.request.post(`${origin}/api/session`, {
      form: { name: 'reload', endpoint_url: 'http://127.0.0.1:9/v1', model: 'test-model', skip_validation: 'true' },
    })).json();
    const doc = await (await context.request.post(`${origin}/api/document`, {
      data: { title: 'Reload draft', content: 'Original paragraph', language: 'markdown', session_id: session.id },
    })).json();
    const open = async () => {
      await page.evaluate(async (id) => {
        const mod = await import('/static/js/document.js');
        await mod.openPanel();
        await mod.loadDocument(id);
      }, doc.id);
      await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
    };
    await openApp(page);
    await open();
    await page.locator('#doc-email-richbody').click();
    await page.keyboard.press('Control+End');
    await page.keyboard.type(` ${secret}`);
    await page.waitForFunction(() => Object.keys(sessionStorage).some((key) => key.startsWith('odysseus.document.markdownDraft.v1:')));
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !document.getElementById('app-loader'));
    await page.waitForTimeout(1500);
    await open();
    assert.ok((await page.locator('#doc-email-richbody').innerText()).includes(secret), `${name}: the draft is recovered after a literal reload`);
    console.log(`[PASS] ${name}: literal reload recovers the unsaved draft`);
  } catch (error) {
    failures += 1;
    console.error(`[FAIL] ${name}`, error);
  } finally {
    await browser.close();
  }
}
if (failures) process.exit(1);
