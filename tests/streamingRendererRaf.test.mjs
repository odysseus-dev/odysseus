import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

test('StreamingRenderer: rAF coalescing and lifecycle cancellation', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route('http://streaming.test/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') {
        return route.fulfill({ contentType: 'text/html', body: '<!DOCTYPE html><html><body><div id="root"></div></body></html>' });
      }
      try {
        const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
        return route.fulfill({ contentType: 'text/javascript', body: source });
      } catch (err) {
        return route.abort();
      }
    });

    await page.goto('http://streaming.test/');

    const result = await page.evaluate(async () => {
      const container = document.getElementById('root');

      // Custom mock for rAF to control frame timing
      const originalRaf = window.requestAnimationFrame;
      const originalCancelRaf = window.cancelAnimationFrame;

      let rafId = 0;
      const pendingRfs = new Map();
      window.requestAnimationFrame = (cb) => {
        const id = ++rafId;
        pendingRfs.set(id, cb);
        return id;
      };
      window.cancelAnimationFrame = (id) => {
        pendingRfs.delete(id);
      };

      const stepFrames = () => {
        const callbacks = Array.from(pendingRfs.values());
        pendingRfs.clear();
        for (const cb of callbacks) cb(performance.now());
        return callbacks.length;
      };

      const { createStreamRenderer } = await import('/static/js/streamingRenderer.js');

      const options = { render: (text) => `<p>${text}</p>` };

      // Test 1: Fast bursts within a frame coalesce into a single rAF render
      const host1 = document.createElement('div');
      container.appendChild(host1);
      const renderer1 = createStreamRenderer(host1, options);

      // Initial token renders synchronously to remove spinner immediately
      renderer1.update('A');
      const textAfterFirst = host1.textContent;

      // Now stream 5 rapid tokens in the same frame
      renderer1.update('AB');
      renderer1.update('ABC');
      renderer1.update('ABCD');
      renderer1.update('ABCDE');

      // Before frame fires, only the scheduled pendingTail exists
      const textBeforeFrame = host1.textContent;
      const frameExecuted = stepFrames();
      const textAfterFrame = host1.textContent;

      // Test 2: Finalize cancels pending rAF callback and freezes remaining text
      const host2 = document.createElement('div');
      container.appendChild(host2);
      const renderer2 = createStreamRenderer(host2, options);

      renderer2.update('First');
      renderer2.update('First, more rapid streaming');
      const pendingBeforeFinalize = pendingRfs.size;
      renderer2.finalize();
      const pendingAfterFinalize = pendingRfs.size;
      const textAfterFinalize = host2.textContent;

      // Executing any residual frames must do nothing because cancelAnimationFrame was called
      const residualSteps = stepFrames();
      const textAfterResidual = host2.textContent;

      // Test 3: Reset clears state and cancels pending frames
      const host3 = document.createElement('div');
      container.appendChild(host3);
      const renderer3 = createStreamRenderer(host3, options);
      renderer3.update('Start');
      renderer3.update('Start and streaming tokens...');
      renderer3.reset();
      const pendingAfterReset = pendingRfs.size;

      // Test 4: Finalized blocks (\n\n) freeze synchronously
      const host4 = document.createElement('div');
      container.appendChild(host4);
      const renderer4 = createStreamRenderer(host4, options);
      renderer4.update('Paragraph 1\n\n');
      renderer4.update('Paragraph 1\n\nParagraph 2');
      stepFrames();
      renderer4.finalize();

      // Test 5: DOM marker replacement during streaming preserves text across immediate finalize
      const host5 = document.createElement('div');
      container.appendChild(host5);
      const renderer5 = createStreamRenderer(host5, options);
      renderer5.update('First chunk\n\n');
      stepFrames();
      // Simulate container rewrite by thinking indicator or tool card
      host5.innerHTML = '<div class="thinking">Thinking...</div>';
      // Stream update on replaced DOM
      renderer5.update('First chunk\n\nSecond chunk following replace');
      stepFrames();
      // Finalize immediately
      renderer5.finalize();
      const host5FinalText = host5.textContent.trim();

      // Test 6: Asynchronous exception inside rAF callback falls back to degraded full render safely
      const host6 = document.createElement('div');
      container.appendChild(host6);
      let crashTailRender = false;
      let tailCrashes = 0;
      const unstableOptions = {
        render: (text) => {
          if (crashTailRender && text === 'pending burst') {
            tailCrashes++;
            throw new Error('Simulated partial tail rAF render crash');
          }
          return `<p>${text}</p>`;
        }
      };
      const renderer6 = createStreamRenderer(host6, unstableOptions);
      renderer6.update('Stable start\n\n');
      stepFrames();
      crashTailRender = true;
      renderer6.update('Stable start\n\npending');
      renderer6.update('Stable start\n\npending burst');
      // Step frame: triggers renderTail inside rAF, which catches and falls back to full render
      stepFrames();
      crashTailRender = false;
      renderer6.finalize();
      const host6FinalText = host6.textContent.trim();

      // Restore
      window.requestAnimationFrame = originalRaf;
      window.cancelAnimationFrame = originalCancelRaf;

      return {
        textAfterFirst,
        textBeforeFrame,
        frameExecuted,
        textAfterFrame,
        pendingBeforeFinalize,
        pendingAfterFinalize,
        textAfterFinalize,
        residualSteps,
        textAfterResidual,
        pendingAfterReset,
        host4Text: host4.textContent.trim(),
        host5FinalText,
        host6FinalText,
        tailCrashes,
      };
    });

    assert.equal(result.tailCrashes, 1, 'partial tail error path genuinely entered');
    assert.equal(result.textAfterFirst, 'A', 'First token renders synchronously for immediate responsiveness');
    assert.equal(result.frameExecuted, 1, 'Multiple rapid updates coalesce into a single animation frame');
    assert.equal(result.textAfterFrame, 'ABCDE', 'Coalesced tail renders full updated content');
    assert.equal(result.pendingBeforeFinalize > 0, true, 'Pending rAF was queued during rapid streaming');
    assert.equal(result.pendingAfterFinalize, 0, 'finalize() successfully cancels pending animation frames');
    assert.equal(result.textAfterFinalize, 'First, more rapid streaming', 'finalize() rendered complete final text');
    assert.equal(result.residualSteps, 0, 'No orphaned rAF callbacks executed after finalize');
    assert.equal(result.pendingAfterReset, 0, 'reset() cancels pending frames');
    assert.equal(result.host4Text.includes('Paragraph 1') && result.host4Text.includes('Paragraph 2'), true);
    assert.equal(
      result.host5FinalText.includes('First chunk') && result.host5FinalText.includes('Second chunk following replace'),
      true,
      'finalize() after self-healing preserves full text without erasing content'
    );
    assert.equal(
      result.host6FinalText,
      'Stable start\n\npending burst',
      'rAF callback error triggers degraded full render without destroying content'
    );
  } finally {
    await browser.close();
  }
});

test('combined rAF/full-render failures preserve authoritative text and recover canonically', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route('http://recovery.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div>' });
      if (path.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: '{}' });
      return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('..' + path, import.meta.url), 'utf8') });
    });
    await page.goto('http://recovery.test/');
    const cases = await page.evaluate(async () => {
      const { createStreamRenderer } = await import('/static/js/streamingRenderer.js');
      const { mdToHtml } = await import('/static/js/markdown.js');
      const canonical = text => { const d = document.createElement('div'); d.innerHTML = mdToHtml(text); return d.innerHTML.replace(/>\s*\n\s*</g, '><').trim(); };
      let next = 0; const pending = new Map(); const cancelled = [];
      window.requestAnimationFrame = cb => { pending.set(++next, cb); return next; };
      window.cancelAnimationFrame = id => { if (pending.has(id)) cancelled.push(pending.get(id)); pending.delete(id); };
      const frame = () => { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(cb => cb()); };
      const results = [];
      for (const recover of [false, true]) {
        const el = document.createElement('div'); let broken = false; let throws = 0;
        const renderer = createStreamRenderer(el, { render: text => { if (broken) { throws++; throw Error('both render paths fail'); } return mdToHtml(text); } });
        renderer.update('prefix\n\ntail'); renderer.update('prefix\n\ntail plus'); broken = true;
        frame(); // auditor reproduction: both tail and full fallback must actually throw
        const afterFailure = el.textContent;
        if (recover) broken = false;
        renderer.finalize();
        const expected = 'prefix\n\ntail plus';
        results.push({ recover, throws, afterFailure, text: el.textContent, html: el.innerHTML, canonical: canonical(expected) });
        broken = false;
        renderer.update(expected + '\n\n```js\nconst a = 1;\n```\n\nEnd'); renderer.finalize();
        results.at(-1).subsequentCanonical = el.innerHTML.replace(/>\s*\n\s*</g, '><').trim() === canonical(expected + '\n\n```js\nconst a = 1;\n```\n\nEnd');
      }
      // Broken first-token/synchronous path must also preserve text safely.
      const el = document.createElement('div'); const bad = createStreamRenderer(el, { render: () => { throw Error('always broken'); } });
      bad.update('<img src=x onerror=alert(1)>\n```js\nx\n'); bad.finalize();
      results.push({ safeText: el.textContent, children: el.childElementCount });
      // Pending callback, DOM rewrite, self-healing, then immediate finalize.
      const host = document.createElement('div'); const healed = createStreamRenderer(host, { render: mdToHtml });
      healed.update('A'); healed.update('AB'); host.innerHTML = 'thinking'; healed.update('ABC'); healed.update('ABCD'); healed.finalize();
      const before = host.innerHTML; frame(); cancelled.forEach(cb => cb());
      results.push({ healed: host.textContent, stable: before === host.innerHTML, pending: pending.size });
      return results;
    });
    for (const result of cases.slice(0, 2)) {
      assert.ok(result.throws >= 2, 'both incremental and full render actually threw');
      assert.equal(result.afterFailure, 'prefix\n\ntail plus', 'complete safe text after failure');
      if (result.recover) assert.equal(result.html.replace(/>\s*\n\s*</g, '><').trim(), result.canonical);
      else assert.equal(result.text, 'prefix\n\ntail plus');
      assert.equal(result.subsequentCanonical, true);
    }
    assert.equal(cases[2].safeText, '<img src=x onerror=alert(1)>\n```js\nx\n');
    assert.equal(cases[2].children, 0, 'fallback never interprets HTML');
    assert.deepEqual(cases[3], { healed: 'ABCD', stable: true, pending: 0 });
  } finally { await browser.close(); }
});
