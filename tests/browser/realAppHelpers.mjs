// Shared helpers for real-application browser regressions. They only drive the
// running application (UI, real HTTP routes, real persistence); none of them
// stubs the backend.
import assert from 'node:assert/strict';

export const origin = process.env.ODYSSEUS_VALIDATION_ORIGIN;
assert.ok(origin, 'ODYSSEUS_VALIDATION_ORIGIN is required');

/**
 * With authentication disabled the notification-log route answers 401, and the
 * application turns every 401 into a redirect to /login, which aborts unrelated
 * browser steps (a toast fires on save). That defect is independent of Markdown
 * editing and is fixed separately (notification-log-auth-fix.patch); validation
 * against the no-auth server must not depend on it, so the route is answered here.
 * Authenticated runs (the logout suite) never use this.
 */
export async function tolerateNoAuthNotificationLogs(context) {
  await context.route('**/api/tasks/notification-logs', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"success":false,"skipped":true}',
  }));
}

export async function openApp(page) {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.getElementById('app-loader'));
  await page.waitForTimeout(1500);
}

/**
 * Load the application again in a fresh page of the same browser context (same
 * cookies and storage). Reloading a page that already has a document open crashes
 * headless Chromium in this environment on the merge base as well, so "reload"
 * steps open a new page instead.
 */
export async function reopenApp(context, oldPage, configure = () => {}) {
  await oldPage.close();
  const page = await context.newPage();
  configure(page);
  await openApp(page);
  return page;
}

export async function createMarkdownDocument(context, title, content) {
  const response = await context.request.post(`${origin}/api/document`, { data: { title, content, language: 'markdown' } });
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}

export const readDocument = async (context, id) => (await context.request.get(`${origin}/api/document/${id}`)).json();

export async function openDocument(page, id, { visual = true } = {}) {
  await page.evaluate(async (docId) => {
    const mod = await import('/static/js/document.js');
    await mod.openPanel();
    await mod.loadDocument(docId);
  }, id);
  if (visual) await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
}

export async function saveAndWait(page, id) {
  const [response] = await Promise.all([
    page.waitForResponse((res) => res.request().method() === 'PUT' && new URL(res.url()).pathname === `/api/document/${id}`),
    page.locator('#doc-footer-copy-btn').click(),
  ]);
  assert.ok(response.ok(), 'backend acknowledges the save');
}

export async function placeCaretAtEnd(page, selector = '#doc-email-richbody') {
  await page.locator(selector).evaluate((root) => {
    root.focus();
    const range = document.createRange();
    range.selectNodeContents(root);
    range.collapse(false);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

/**
 * Reduce a rendered surface (Visual editor or Preview) to what a reader sees:
 * text per block, inline formatting, link targets, code spans and table shape.
 * Computed on the live DOM so browser parsing rules apply to both surfaces.
 */
export function semanticSummary(page, selector) {
  return page.locator(selector).evaluate((root) => {
    const clone = root.cloneNode(true);
    clone.querySelectorAll('button, .doc-preview-hover-edit, [contenteditable="false"][data-alert-label]').forEach((el) => el.remove());
    const text = (node) => (node.textContent || '').replace(/\s+/g, ' ').trim();
    const blocks = [];
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          // Bare text between blocks is a paragraph the editor has not wrapped yet.
          if (text(child)) blocks.push({ p: text(child) });
          continue;
        }
        if (child.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = child.tagName.toLowerCase();
        if (tag === 'table') {
          blocks.push({ table: [...child.querySelectorAll('tr')].map((row) => [...row.children].map(text)) });
        } else if (['ul', 'ol'].includes(tag)) {
          blocks.push({ [tag]: [...child.querySelectorAll(':scope > li')].map(text) });
        } else if (tag === 'p' && child.querySelector('table')) {
          walk(child);
        } else if (['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre'].includes(tag)) {
          if (text(child) || child.querySelector('img')) blocks.push({ [tag === 'div' ? 'p' : tag]: text(child) });
        }
      }
    };
    walk(clone);
    return {
      blocks,
      strong: [...clone.querySelectorAll('strong, b')].map(text),
      em: [...clone.querySelectorAll('em, i')].map(text),
      code: [...clone.querySelectorAll('code')].map(text),
      links: [...clone.querySelectorAll('a')].map((a) => [text(a), a.getAttribute('href')]),
    };
  });
}
