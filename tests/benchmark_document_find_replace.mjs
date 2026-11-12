// tests/benchmark_document_find_replace.mjs
/**
 * Reproducible benchmark measuring Find & Replace highlight rendering performance.
 * Compares unbatched forced-reflow measurement (baseline) against batched Range measurement (optimized).
 */

import { chromium } from 'playwright';

export async function runBenchmark() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });

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
    <div id="doc-editor-wrap" class="doc-editor-wrap">
      <textarea id="doc-editor-textarea" class="doc-editor-textarea"></textarea>
    </div>
  `);

  const results = await page.evaluate(async () => {
    const textarea = document.getElementById('doc-editor-textarea');
    const wrap = document.getElementById('doc-editor-wrap');

    // Build realistic document text with 100 matches across paragraphs, including soft-wrapped lines
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

    // 1. Baseline unbatched measurement loop (from original static/js/document.js:12524-12580)
    function renderFindRectsBaseline(matches, currentIdx) {
      wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
      if (!matches || matches.length === 0) return;
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
      for (let i = 0; i < matches.length; i++) {
        const [s, e] = matches[i];
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

    // 2. Batched Range measurement (optimized static/js/document/findReplace.js)
    function renderFindRectsOptimized(matches, currentIdx) {
      if (!matches || matches.length === 0) {
        wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
        return;
      }
      const style = getComputedStyle(textarea);
      const paddingTop = parseFloat(style.paddingTop) || 10;
      const paddingLeft = parseFloat(style.paddingLeft) || 48;
      const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

      let mirror = document.getElementById('doc-find-rect-mirror');
      if (!mirror) {
        mirror = document.createElement('div');
        mirror.id = 'doc-find-rect-mirror';
        mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;box-sizing:border-box;';
        wrap.appendChild(mirror);
      }

      const clientWidth = textarea.clientWidth;
      const scrollTop = textarea.scrollTop;

      let rowData = null;
      const cache = mirror._findCache;
      if (
        cache &&
        cache.text === text &&
        cache.clientWidth === clientWidth &&
        cache.matches === matches &&
        cache.lineHeight === lineHeight &&
        cache.paddingTop === paddingTop
      ) {
        rowData = cache.rows;
      } else {
        mirror.style.whiteSpace = textarea.wrap === 'off' ? 'pre' : 'pre-wrap';
        mirror.style.wordWrap = textarea.wrap === 'off' ? 'normal' : 'break-word';
        mirror.style.overflowWrap = textarea.wrap === 'off' ? 'normal' : 'break-word';
        mirror.style.font = style.font;
        mirror.style.padding = style.padding;
        mirror.style.borderWidth = style.borderWidth;
        mirror.style.borderStyle = 'solid';
        mirror.style.borderColor = 'transparent';
        mirror.style.width = clientWidth + 'px';
        mirror.style.tabSize = style.tabSize;
        mirror.style.letterSpacing = style.letterSpacing;
        mirror.style.wordSpacing = style.wordSpacing;
        mirror.style.textIndent = style.textIndent;

        mirror.textContent = text;
        const textNode = mirror.firstChild;
        if (!textNode) {
          wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
          return;
        }

        const mirrorRect = mirror.getBoundingClientRect();
        const mirrorTop = mirrorRect.top + paddingTop;
        const range = document.createRange();
        const maxLen = textNode.length;
        rowData = new Array(matches.length);

        for (let i = 0; i < matches.length; i++) {
          const [s, e] = matches[i];
          const sClamped = Math.max(0, Math.min(s, maxLen));
          let eClamped = Math.max(sClamped, Math.min(e, maxLen));
          if (sClamped === eClamped && sClamped < maxLen) {
            eClamped = sClamped + 1;
          }
          range.setStart(textNode, sClamped);
          range.setEnd(textNode, eClamped);
          const r = range.getBoundingClientRect();

          const visualRowStart = Math.max(0, Math.floor((r.top - mirrorTop + (lineHeight * 0.25)) / lineHeight));
          const visualRowEnd = Math.max(visualRowStart, Math.floor((r.bottom - mirrorTop - 1) / lineHeight));
          const rowCount = Math.max(1, visualRowEnd - visualRowStart + 1);
          rowData[i] = [visualRowStart, rowCount];
        }

        mirror._findCache = { text, clientWidth, matches, rows: rowData, lineHeight, paddingTop };
      }

      const existingRects = wrap.querySelectorAll('.doc-find-rect');
      if (existingRects.length === rowData.length) {
        for (let i = 0; i < rowData.length; i++) {
          const [rowStart, count] = rowData[i];
          const top = paddingTop + rowStart * lineHeight - scrollTop;
          const height = count * lineHeight;
          const rect = existingRects[i];
          rect.style.top = top + 'px';
          rect.style.height = height + 'px';
          const isCurrent = i === currentIdx;
          if (rect.classList.contains('current') !== isCurrent) {
            rect.classList.toggle('current', isCurrent);
          }
        }
      } else {
        wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
        const frag = document.createDocumentFragment();
        for (let i = 0; i < rowData.length; i++) {
          const [rowStart, count] = rowData[i];
          const top = paddingTop + rowStart * lineHeight - scrollTop;
          const height = count * lineHeight;
          const rect = document.createElement('div');
          rect.className = 'doc-find-rect' + (i === currentIdx ? ' current' : '');
          rect.style.cssText =
            `position:absolute;left:${paddingLeft}px;right:8px;` +
            `top:${top}px;height:${height}px;` +
            `pointer-events:none;z-index:6;border-radius:2px;`;
          frag.appendChild(rect);
        }
        wrap.appendChild(frag);
      }
    }

    // Benchmark baseline
    const t0 = performance.now();
    renderFindRectsBaseline(matches, 0);
    const baselineMs = performance.now() - t0;
    const baselineCount = wrap.querySelectorAll('.doc-find-rect').length;

    // Reset mirror for fair comparison
    document.getElementById('doc-find-rect-mirror')?.remove();

    // Benchmark optimized initial render
    const t1 = performance.now();
    renderFindRectsOptimized(matches, 0);
    const optimizedInitialMs = performance.now() - t1;
    const optimizedCount = wrap.querySelectorAll('.doc-find-rect').length;

    // Benchmark scroll updates (10 scroll events)
    const scrollTimes = [];
    for (let step = 1; step <= 10; step++) {
      textarea.scrollTop = step * 40;
      const ts = performance.now();
      renderFindRectsOptimized(matches, 0);
      scrollTimes.push(performance.now() - ts);
    }
    const avgScrollMs = scrollTimes.reduce((a, b) => a + b, 0) / scrollTimes.length;

    // Benchmark match index switching (10 steps)
    const navTimes = [];
    for (let c = 1; c <= 10; c++) {
      const tn = performance.now();
      renderFindRectsOptimized(matches, c);
      navTimes.push(performance.now() - tn);
    }
    const avgNavMs = navTimes.reduce((a, b) => a + b, 0) / navTimes.length;

    return {
      matches: matches.length,
      baselineMs,
      baselineCount,
      optimizedInitialMs,
      optimizedCount,
      avgScrollMs,
      avgNavMs,
      speedupFactor: (baselineMs / optimizedInitialMs).toFixed(1) + 'x',
    };
  });

  await browser.close();
  return results;
}

if (process.argv[1]?.endsWith('benchmark_document_find_replace.mjs')) {
  runBenchmark().then(res => {
    console.log('\n=== Find & Replace Benchmark Results ===');
    console.log(`Matches tested:           ${res.matches}`);
    console.log(`Baseline Initial Render:   ${res.baselineMs.toFixed(2)} ms (${res.baselineCount} rects)`);
    console.log(`Optimized Initial Render:  ${res.optimizedInitialMs.toFixed(2)} ms (${res.optimizedCount} rects)`);
    console.log(`Initial Render Speedup:    ${res.speedupFactor}`);
    console.log(`Avg Scroll Update:         ${res.avgScrollMs.toFixed(3)} ms`);
    console.log(`Avg Match Navigation:      ${res.avgNavMs.toFixed(3)} ms`);
    console.log('========================================\n');
  }).catch(err => {
    console.error(err);
    process.exit(1);
  });
}
