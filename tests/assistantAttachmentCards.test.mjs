// Assistant replies show create_pdf files as download links, in every engine
// the owner uses: Firefox at home, Safari (WebKit), and Chromium browsers.
//
// A real local HTTP server rather than page.route: browser downloads bypass
// Playwright request routing, so only a real origin exercises the download.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { chromium, firefox, webkit } from 'playwright';

const PDF_ID = 'a'.repeat(32) + '.pdf';
const PDF = { id: PDF_ID, name: 'Boot_fix.pdf', mime: 'application/pdf', size: 3100 };
const PDF_BYTES = Buffer.from('%PDF-1.4\n% test\n%%EOF\n');

async function startServer() {
  const uploadRequests = [];
  const server = createServer(async (req, res) => {
    const path = new URL(req.url, 'http://127.0.0.1').pathname;
    if (path === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      return res.end('<!DOCTYPE html><html><body><div id="chat-history"></div></body></html>');
    }
    if (path === `/api/upload/${PDF_ID}`) {
      uploadRequests.push(req.method);
      res.writeHead(200, {
        'Content-Type': 'application/pdf',
        'Content-Disposition': 'attachment; filename="Boot_fix.pdf"',
        'X-Content-Type-Options': 'nosniff',
      });
      return res.end(PDF_BYTES);
    }
    if (path.startsWith('/api/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end('{}');
    }
    if (path.startsWith('/static/') && !path.includes('..')) {
      try {
        const body = await readFile(new URL('..' + path, import.meta.url));
        res.writeHead(200, { 'Content-Type': path.endsWith('.css') ? 'text/css' : 'text/javascript' });
        return res.end(body);
      } catch { /* fall through to 404 */ }
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}`, uploadRequests };
}

async function openPage(browserType) {
  const { server, origin, uploadRequests } = await startServer();
  const browser = await browserType.launch({ headless: true });
  const page = await (await browser.newContext({ acceptDownloads: true })).newPage();
  await page.goto(`${origin}/`);
  await page.evaluate(async () => {
    window.__renderer = (await import('/static/js/chatRenderer.js')).default;
  });
  const close = async () => { await browser.close(); server.close(); };
  return { page, uploadRequests, close };
}

for (const [name, browserType] of [['chromium', chromium], ['firefox', firefox], ['webkit', webkit]]) {
  test(`${name}: assistant PDF attachment renders as a working download link`, async () => {
    const { page, uploadRequests, close } = await openPage(browserType);
    try {
      const card = await page.evaluate((pdf) => {
        window.__renderer.addMessage('assistant', 'Your PDF is ready.', 'qwen2.5', { attachments: [pdf] });
        const link = document.querySelector('#chat-history a.attach-card');
        return link && {
          href: link.getAttribute('href'),
          download: link.getAttribute('download'),
          text: link.textContent,
        };
      }, PDF);

      assert.ok(card, 'assistant reply should contain an attachment link');
      assert.equal(card.href, `/api/upload/${PDF_ID}`);
      assert.equal(card.download, 'Boot_fix.pdf');
      assert.match(card.text, /Boot_fix\.pdf/);

      const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.click('#chat-history a.attach-card'),
      ]);
      assert.equal(download.suggestedFilename(), 'Boot_fix.pdf');
      assert.equal(await download.failure(), null);
      const saved = await readFile(await download.path());
      assert.ok(saved.equals(PDF_BYTES), 'downloaded bytes should be the PDF');
      assert.deepEqual(uploadRequests, ['GET']);
    } finally {
      await close();
    }
  });
}

test('a saved reply with tool activity keeps its PDF card under the answer', async () => {
  // Writer-made PDFs save a create_pdf tool event, which sends the reply
  // through the multi-bubble history renderer.
  const { page, close } = await openPage(chromium);
  try {
    const shape = await page.evaluate((pdf) => {
      // Same shape as the saved Gitea reply; sessions.js adds _fromHistory.
      window.__renderer.addMessage('assistant', 'The PDF covers the Gitea setup.', 'qwen3:8b', {
        _fromHistory: true,
        render_owner: 'streamed',
        attachments: [pdf],
        tool_events: [{ round: 1, tool: 'create_pdf', desc: 'create_pdf', command: 'create_pdf: Gitea setup', output: 'Created the PDF', exit_code: 0 }],
      });
      const answer = [...document.querySelectorAll('#chat-history .msg-ai')].pop();
      return {
        links: document.querySelectorAll('#chat-history a.attach-card').length,
        inAnswer: !!answer?.querySelector('.body a.attach-card'),
        answerText: answer?.querySelector('.body')?.textContent || '',
      };
    }, PDF);
    assert.equal(shape.links, 1);
    assert.ok(shape.inAnswer, 'the card should sit in the answer bubble');
    assert.match(shape.answerText, /Gitea setup/);
  } finally {
    await close();
  }
});

test('user attachment cards are unchanged (no download link)', async () => {
  const { page, close } = await openPage(chromium);
  try {
    const shape = await page.evaluate((pdf) => {
      window.__renderer.addMessage('user', 'here is a file', null, { attachments: [pdf] });
      return {
        links: document.querySelectorAll('#chat-history a.attach-card').length,
        cards: document.querySelectorAll('#chat-history div.attach-card').length,
      };
    }, PDF);
    assert.deepEqual(shape, { links: 0, cards: 1 });
  } finally {
    await close();
  }
});
