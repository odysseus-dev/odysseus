"""Validation and regression coverage for Find & Replace performance and behavior.

Tests:
- Batched Range measurement performance vs baseline forced reflow loop.
- Soft-wrapped line highlight positioning and visual row geometry.
- Current-match vs other-matches highlight classes.
- Literal replacement correctness with special regex characters.
- In-place rect updates on scroll and match navigation.
- Rich-text boundary preservation and highlight cleanup.
"""

import json
import subprocess
from pathlib import Path

from tests.helpers.document_source import document_source, function_body
from tests.helpers.stylesheets import stylesheet_link_tags

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def _run_node_script(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stderr:
        print("STDERR:", result.stderr)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_find_replace_functions_are_modularly_defined():
    """Verify that find & replace functions exist in the implementation graph."""
    for fn_name in (
        "_buildRichFindRanges",
        "_renderRichFindRanges",
        "_clearRichFindHighlights",
        "_openFindBar",
        "_closeFindBar",
        "_setReplaceVisible",
        "_replaceAllLiteral",
        "_replaceFindCurrent",
        "_replaceFindAll",
        "_doFind",
        "renderFindRects",
        "applyFindMarks",
        "initFindReplace",
    ):
        body = function_body(fn_name)
        assert len(body) > 20, f"Function {fn_name} body should not be empty"


def test_render_find_rects_performance_improvement():
    """Verify that batched renderFindRects achieves significant speedup over baseline."""
    script = r"""
      import { runBenchmark } from './tests/benchmark_document_find_replace.mjs';
      const results = await runBenchmark();
      console.log(JSON.stringify(results));
    """
    data = _run_node_script(script)

    assert data["matches"] == 100
    assert data["baselineCount"] == 100
    assert data["optimizedCount"] == 100
    # The optimized implementation should finish in well under 30ms (usually 2-8ms),
    # significantly faster than the unbatched baseline loop (typically 100-300ms).
    assert data["optimizedInitialMs"] < 35.0
    assert data["avgScrollMs"] < 5.0
    assert data["avgNavMs"] < 5.0
    assert data["baselineMs"] > data["optimizedInitialMs"]


def test_wrapped_text_highlight_positioning_accuracy():
    """Verify highlight rectangles accurately track soft-wrapped lines in narrow containers."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 500, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent(`
        <style>
          .doc-editor-wrap {
            position: relative;
            width: 320px;
            height: 400px;
          }
          .doc-editor-textarea {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            width: 100%;
            font-family: monospace;
            font-size: 11px;
            line-height: 20px;
            padding: 10px 12px 10px 48px;
            box-sizing: border-box;
          }
          .doc-find-rect {
            position: absolute;
            left: 48px; right: 8px;
          }
        </style>
        <div id="doc-editor-wrap" class="doc-editor-wrap">
          <textarea id="doc-editor-textarea" class="doc-editor-textarea"></textarea>
        </div>
      `);

      const res = await page.evaluate(async () => {
        const { renderFindRects } = await import('/static/js/document/findReplace.js');
        const ta = document.getElementById('doc-editor-textarea');
        const wrap = document.getElementById('doc-editor-wrap');

        // Line 1 is short (1 visual row, row 0).
        // Line 2 is very long and wraps across multiple visual rows.
        // Target appears on row 0, on a wrapped row of Line 2, and on Line 3.
        ta.value = [
          'First line with targetOne.',
          'Second line with words to fill the width and wrap around repeatedly so that targetTwo is definitely on a soft wrapped row inside this narrow container.',
          'Third line with targetThree.'
        ].join('\n');

        const text = ta.value;
        const matches = [
          [text.indexOf('targetOne'), text.indexOf('targetOne') + 'targetOne'.length],
          [text.indexOf('targetTwo'), text.indexOf('targetTwo') + 'targetTwo'.length],
          [text.indexOf('targetThree'), text.indexOf('targetThree') + 'targetThree'.length],
        ];

        renderFindRects(matches, 1); // targetTwo is current match

        const rects = Array.from(wrap.querySelectorAll('.doc-find-rect')).map(el => ({
          top: parseFloat(el.style.top),
          height: parseFloat(el.style.height),
          isCurrent: el.classList.contains('current'),
        }));

        return { rects };
      });

      console.log(JSON.stringify(res));
      await browser.close();
    """
    data = _run_node_script(script)
    rects = data["rects"]
    assert len(rects) == 3

    # Row 0 starts at paddingTop (10px) with height 20px
    assert rects[0]["top"] == 10.0
    assert rects[0]["height"] == 20.0
    assert not rects[0]["isCurrent"]

    # Target 2 is on a soft-wrapped row, so its top must be strictly greater than row 0
    assert rects[1]["top"] > 30.0
    assert rects[1]["height"] >= 20.0
    assert rects[1]["isCurrent"]

    # Target 3 is on the subsequent line
    assert rects[2]["top"] > rects[1]["top"]
    assert not rects[2]["isCurrent"]


def test_literal_replacement_accuracy_and_special_characters():
    """Verify _replaceAllLiteral handles regex tokens without collision."""
    script = r"""
      import { _replaceAllLiteral } from './static/js/document/findReplace.js';
      const text = 'Hello [world] $1 .* and hello [world] again!';
      const res1 = _replaceAllLiteral(text, '[world]', 'universe');
      const res2 = _replaceAllLiteral(text, '$1', 'USD');
      const res3 = _replaceAllLiteral(text, '.*', 'ALL');
      const res4 = _replaceAllLiteral(text, 'HELLO', 'hi');

      console.log(JSON.stringify({ res1, res2, res3, res4 }));
    """
    data = _run_node_script(script)

    assert data["res1"]["count"] == 2
    assert data["res1"]["text"] == "Hello universe $1 .* and hello universe again!"

    assert data["res2"]["count"] == 1
    assert data["res2"]["text"] == "Hello [world] USD .* and hello [world] again!"

    assert data["res3"]["count"] == 1
    assert data["res3"]["text"] == "Hello [world] $1 ALL and hello [world] again!"

    assert data["res4"]["count"] == 2
    assert data["res4"]["text"] == "hi [world] $1 .* and hi [world] again!"


def test_inplace_rect_reuse_on_scroll_and_navigation():
    """Verify rect DOM nodes are reused in-place on scroll and navigation without thrashing DOM."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 600, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent(`
        <div id="doc-editor-wrap" style="position:relative; width:400px; height:400px;">
          <textarea id="doc-editor-textarea" style="width:100%; height:100%; font:11px monospace; line-height:20px; padding:10px 12px 10px 48px; box-sizing:border-box;">
Line 1: hit
Line 2: hit
Line 3: hit
          </textarea>
        </div>
      `);

      const res = await page.evaluate(async () => {
        const { renderFindRects } = await import('/static/js/document/findReplace.js');
        const ta = document.getElementById('doc-editor-textarea');
        const wrap = document.getElementById('doc-editor-wrap');
        const text = ta.value;

        const matches = [];
        let pos = 0;
        while ((pos = text.indexOf('hit', pos)) !== -1) {
          matches.push([pos, pos + 3]);
          pos += 3;
        }

        // Initial render
        renderFindRects(matches, 0);
        const rectsPass1 = Array.from(wrap.querySelectorAll('.doc-find-rect'));

        // Scroll update
        ta.scrollTop = 40;
        renderFindRects(matches, 0);
        const rectsPass2 = Array.from(wrap.querySelectorAll('.doc-find-rect'));

        // Navigation update
        renderFindRects(matches, 2);
        const rectsPass3 = Array.from(wrap.querySelectorAll('.doc-find-rect'));

        // Check reference identity
        const sameNodesOnScroll = rectsPass1.every((el, idx) => el === rectsPass2[idx]);
        const sameNodesOnNav = rectsPass1.every((el, idx) => el === rectsPass3[idx]);
        const currentMatchIsThird = rectsPass3[2].classList.contains('current') && !rectsPass3[0].classList.contains('current');

        return { sameNodesOnScroll, sameNodesOnNav, currentMatchIsThird };
      });

      console.log(JSON.stringify(res));
      await browser.close();
    """
    data = _run_node_script(script)
    assert data["sameNodesOnScroll"], "DOM rect nodes should be reused in-place on scroll"
    assert data["sameNodesOnNav"], "DOM rect nodes should be reused in-place on navigation"
    assert data["currentMatchIsThird"], "Current match class should update on navigation"


def test_find_and_replace_lifecycle_in_editor():
    """Verify find & replace open, navigate, replace, and close lifecycle in live editor."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      page.on('console', msg => console.error('BROWSER_LOG:', msg.text()));
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');

      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&lifecycle-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'lifecycle-find-doc',
          title: 'Find Lifecycle',
          language: 'markdown',
          current_content: 'Item alpha here\nItem beta here\nItem alpha again\n',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 300));
        document.querySelector('#doc-editor-textarea').focus();
      });

      // Press Ctrl+F to open find bar
      await page.keyboard.press('Control+f');
      const findInput = page.locator('#doc-find-input');
      const findCount = page.locator('#doc-find-count');
      const findBar = page.locator('#doc-find-bar');

      await findInput.fill('alpha');
      const countFirst = await findCount.textContent();

      // Press Enter to go to next match
      await page.keyboard.press('Enter');
      const countNext = await findCount.textContent();

      // Press Shift+Enter to go back to previous match
      await page.keyboard.press('Shift+Enter');
      const countPrev = await findCount.textContent();

      // Toggle replace bar
      await page.locator('#doc-find-replace-toggle').click();
      const replaceInput = page.locator('#doc-replace-input');
      await replaceInput.fill('gamma');

      // Replace all
      await page.locator('#doc-replace-all').click();
      const afterReplaceCount = await findCount.textContent();
      const updatedText = await page.locator('#doc-editor-textarea').inputValue();

      // Press Escape to close find bar
      await replaceInput.focus();
      await page.keyboard.press('Escape');
      const barDisplayAfterEsc = await findBar.evaluate(el => el.style.display);
      const rectsAfterClose = await page.locator('.doc-find-rect').count();

      console.log(JSON.stringify({
        countFirst,
        countNext,
        countPrev,
        afterReplaceCount,
        updatedText,
        barDisplayAfterEsc,
        rectsAfterClose,
      }));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    data = _run_node_script(script)
    assert data["countFirst"] == "1 / 2"
    assert data["countNext"] == "2 / 2"
    assert data["countPrev"] == "1 / 2"
    assert data["afterReplaceCount"] in ("2 replaced", "0 results")
    assert "Item gamma here" in data["updatedText"]
    assert "Item gamma again" in data["updatedText"]
    assert "Item alpha" not in data["updatedText"]
    assert data["barDisplayAfterEsc"] == "none"
    assert data["rectsAfterClose"] == 0

