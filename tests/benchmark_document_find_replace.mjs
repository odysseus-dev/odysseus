// tests/benchmark_document_find_replace.mjs
/**
 * Reproducible benchmark for the deployed Find & Replace overlay renderer.
 * Exercises the actual deployed /static/js/document/ui/findReplace.js module
 * with real DOM input, match navigation, and real scroll events.
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const ROOT = process.cwd();

async function openFindTestPage(browser) {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
  if (origin) {
    await page.goto(`${origin}/static/js/documentOutline.js`);
    return page;
  }

  await page.route('https://benchmark.invalid/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/harness.html') {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<!doctype html><html><body></body></html>',
      });
      return;
    }
    if (url.pathname.startsWith('/static/')) {
      const rel = url.pathname.slice(1);
      const body = await fs.readFile(path.join(ROOT, rel), 'utf8');
      await route.fulfill({
        status: 200,
        contentType: 'application/javascript',
        body,
      });
      return;
    }
    await route.abort();
  });

  await page.goto('https://benchmark.invalid/harness.html');
  return page;
}

export async function runBenchmark() {
  const browser = await chromium.launch({ headless: true });
  const page = await openFindTestPage(browser);

  await page.setContent(`
    <style>
      .doc-editor-wrap {
        position: relative;
        width: 700px;
        height: 500px;
        overflow: hidden;
      }
      .doc-editor-textarea {
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        width: 100%;
        height: 100%;
        font-family: monospace;
        font-size: 11px;
        line-height: 1.45;
        padding: 10px 12px 10px 48px;
        box-sizing: border-box;
        overflow-y: scroll;
        border: none;
      }
      .doc-find-rect {
        position: absolute;
        left: 48px; right: 8px;
        pointer-events: none;
        z-index: 6;
      }
    </style>
    <div id="doc-find-bar" style="display:none;">
      <input id="doc-find-input" />
      <span id="doc-find-count"></span>
      <button id="doc-find-next" type="button">Next</button>
    </div>
    <div id="doc-editor-wrap" class="doc-editor-wrap">
      <textarea id="doc-editor-textarea" class="doc-editor-textarea"></textarea>
    </div>
  `);

  const results = await page.evaluate(async () => {
    const mod = await import('/static/js/document/ui/findReplace.js');
    const {
      initFindReplace,
      renderFindRects,
      onFindTextareaScroll,
      getFindMatches,
    } = mod;

    const textarea = document.getElementById('doc-editor-textarea');
    const wrap = document.getElementById('doc-editor-wrap');
    const findInput = document.getElementById('doc-find-input');
    const findNext = document.getElementById('doc-find-next');

    initFindReplace({});
    textarea.addEventListener('scroll', () => onFindTextareaScroll());

    // Build realistic document text with 100 matches across paragraphs, including soft-wrapped lines.
    const lines = [];
    let matchCount = 0;
    for (let i = 0; i < 400; i++) {
      if (matchCount < 100 && i % 4 === 0) {
        lines.push(`Line ${i}: FINDME start of line. This is a very long line designed to wrap across multiple visual rows in a 700px container with padding so that another FINDME appears on a soft-wrapped visual row.`);
        matchCount += 2;
      } else {
        lines.push(`Line ${i}: Normal content without search target to provide realistic buffer distance.`);
      }
    }
    textarea.value = lines.join('\n');
    const text = textarea.value;

    const matches = [];
    let pos = 0;
    while ((pos = text.indexOf('FINDME', pos)) !== -1 && matches.length < 100) {
      matches.push([pos, pos + 'FINDME'.length]);
      pos += 'FINDME'.length;
    }

    // 1. Pre-optimization unbatched mirror.scrollHeight measurement baseline
    function renderFindRectsBaseline(targetMatches, currentIdx) {
      wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
      if (!targetMatches || targetMatches.length === 0) return;
      const style = getComputedStyle(textarea);
      const paddingTop = parseFloat(style.paddingTop) || 10;
      const paddingLeft = parseFloat(style.paddingLeft) || 48;
      const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

      let mirror = document.getElementById('doc-find-rect-mirror');
      if (!mirror) {
        mirror = document.createElement('div');
        mirror.id = 'doc-find-rect-mirror';
        mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;' +
          'white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;box-sizing:border-box;';
        wrap.appendChild(mirror);
      }
      mirror.style.font = style.font;
      mirror.style.padding = style.padding;
      mirror.style.borderWidth = style.borderWidth;
      mirror.style.borderStyle = 'solid';
      mirror.style.borderColor = 'transparent';
      mirror.style.width = textarea.clientWidth + 'px';
      mirror.style.tabSize = style.tabSize;
      mirror.style.letterSpacing = style.letterSpacing;
      mirror.style.wordSpacing = style.wordSpacing;
      mirror.style.textIndent = style.textIndent;

      const scrollTop = textarea.scrollTop;
      for (let i = 0; i < targetMatches.length; i++) {
        const [s, e] = targetMatches[i];
        mirror.textContent = text.substring(0, s);
        const startTop = mirror.scrollHeight - paddingTop;
        mirror.textContent = text.substring(0, e);
        const endHeight = mirror.scrollHeight - paddingTop;
        mirror.textContent = '';

        const top = paddingTop + startTop - scrollTop;
        const height = Math.max(endHeight - startTop, lineHeight);
        const rect = document.createElement('div');
        rect.className = 'doc-find-rect' + (i === currentIdx ? ' current' : '');
        rect.style.cssText =
          `position:absolute;left:${paddingLeft}px;right:8px;` +
          `top:${top}px;height:${height}px;` +
          `pointer-events:none;z-index:6;border-radius:2px;`;
        wrap.appendChild(rect);
      }
    }

    const t0 = performance.now();
    renderFindRectsBaseline(matches, 0);
    const baselineInitialMs = performance.now() - t0;
    const baselineCount = wrap.querySelectorAll('.doc-find-rect').length;

    // Clean baseline artifacts before exercising the deployed implementation
    document.getElementById('doc-find-rect-mirror')?.remove();
    wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());

    // 2. Deployed Find initial render via real input event on #doc-find-input
    findInput.value = 'FINDME';
    const t1 = performance.now();
    findInput.dispatchEvent(new Event('input', { bubbles: true }));
    const deployedInitialMs = performance.now() - t1;
    const deployedCount = wrap.querySelectorAll('.doc-find-rect').length;
    const mirror = document.getElementById('doc-find-rect-mirror');
    const recomputesAfterFind = mirror?._findLayoutRecomputes || 0;

    // 3. Measure un-cached scroll recompute (simulating pre-fix scroll invalidation)
    const uncachedScrollTimes = [];
    for (let step = 1; step <= 20; step++) {
      if (mirror) mirror._findCache = null;
      textarea.scrollTop = step * 35;
      const ts = performance.now();
      onFindTextareaScroll();
      uncachedScrollTimes.push(performance.now() - ts);
    }
    const avgUncachedScrollMs = uncachedScrollTimes.reduce((a, b) => a + b, 0) / uncachedScrollTimes.length;
    const recomputesBeforeCachedScroll = mirror?._findLayoutRecomputes || 0;

    // 4. Measure real browser scroll events using deployed cache
    const firstRectBeforeScroll = parseFloat(wrap.querySelector('.doc-find-rect').style.top);
    const scrollStartVal = textarea.scrollTop;
    const cachedScrollTimes = [];
    for (let step = 1; step <= 20; step++) {
      textarea.scrollTop = scrollStartVal + step * 35;
      const ts = performance.now();
      textarea.dispatchEvent(new Event('scroll'));
      cachedScrollTimes.push(performance.now() - ts);
    }
    const firstRectAfterScroll = parseFloat(wrap.querySelector('.doc-find-rect').style.top);
    const expectedScrollDelta = textarea.scrollTop - scrollStartVal;
    const avgCachedScrollMs = cachedScrollTimes.reduce((a, b) => a + b, 0) / cachedScrollTimes.length;
    const recomputesAfterCachedScroll = mirror?._findLayoutRecomputes || 0;

    // 5. Measure match navigation via real click events
    const navTimes = [];
    for (let c = 1; c <= 10; c++) {
      const tn = performance.now();
      findNext.click();
      navTimes.push(performance.now() - tn);
    }
    const avgNavMs = navTimes.reduce((a, b) => a + b, 0) / navTimes.length;
    const recomputesAfterNav = mirror?._findLayoutRecomputes || 0;

    return {
      matches: getFindMatches().length,
      baselineInitialMs,
      baselineCount,
      deployedInitialMs,
      deployedCount,
      avgUncachedScrollMs,
      avgCachedScrollMs,
      avgNavMs,
      recomputesAfterFind,
      recomputesBeforeCachedScroll,
      recomputesAfterCachedScroll,
      recomputesAfterNav,
      cachedScrollDeltaError: Math.abs((firstRectBeforeScroll - firstRectAfterScroll) - expectedScrollDelta),
      optimizedInitialMs: deployedInitialMs,
      optimizedCount: deployedCount,
      baselineMs: baselineInitialMs,
      avgScrollMs: avgCachedScrollMs,
      initialRenderRatio: Number((baselineInitialMs / Math.max(0.01, deployedInitialMs)).toFixed(2)),
      scrollCacheRatio: Number((avgUncachedScrollMs / Math.max(0.001, avgCachedScrollMs)).toFixed(2)),
    };
  });

  await browser.close();
  return results;
}

if (process.argv[1]?.endsWith('benchmark_document_find_replace.mjs')) {
  runBenchmark().then(res => {
    console.log('\n=== Find & Replace Deployed Benchmark ===');
    console.log(`Matches tested:             ${res.matches}`);
    console.log(`Baseline Unbatched Render:  ${res.baselineInitialMs.toFixed(2)} ms (${res.baselineCount} rects)`);
    console.log(`Deployed Initial Render:    ${res.deployedInitialMs.toFixed(2)} ms (${res.deployedCount} rects, ${res.initialRenderRatio}x)`);
    console.log(`Uncached Scroll Recompute:  ${res.avgUncachedScrollMs.toFixed(3)} ms`);
    console.log(`Deployed Cached Scroll:     ${res.avgCachedScrollMs.toFixed(3)} ms (${res.scrollCacheRatio}x, recomputes delta=${res.recomputesAfterCachedScroll - res.recomputesBeforeCachedScroll})`);
    console.log(`Match Navigation (avg):     ${res.avgNavMs.toFixed(3)} ms (recomputes delta=${res.recomputesAfterNav - res.recomputesAfterCachedScroll})`);
    console.log('=========================================\n');
  }).catch(err => {
    console.error(err);
    process.exit(1);
  });
}
