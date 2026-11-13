"""Unit and regression coverage for document diff viewer and review engine.

Validates static/js/document/ui/diffViewer.js:
1. diffLines boundary conditions, whitespace, and chunk grouping.
2. Diff mode entry and DOM element mounting/unmounting.
3. Acceptance and rejection of individual chunks into textarea content.
4. Chunk navigation and status updates.
"""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_node_script(script: str) -> dict:
    """Execute a Node ESM script and parse JSON output."""
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert res.returncode == 0, f"Node script failed with code {res.returncode}:\n{res.stderr}\nScript:\n{script}"
    return json.loads(res.stdout)


def test_diff_lines_boundary_conditions_and_whitespace():
    """Verify diffLines boundary conditions: empty, identical, distinct, whitespace."""
    script = r"""
      import { diffLines, _computeLineDiff, _buildDiffChunks } from './static/js/document/ui/diffViewer.js';

      const results = {};

      // Empty inputs
      results.bothEmpty = diffLines('', '');
      results.oldEmpty = diffLines('', 'alpha\nbeta');
      results.newEmpty = diffLines('alpha\nbeta', '');

      // Identical inputs
      results.identical = diffLines('line1\nline2', 'line1\nline2');

      // Whitespace sensitivity
      results.trailingWhitespace = diffLines('hello', 'hello  ');
      results.indentationDifference = diffLines('  func()', '    func()');
      results.blankLines = diffLines('a\n\nb', 'a\nb');

      // 500,000 cell guard
      results.cellGuardTriggered = diffLines('x\n'.repeat(800), 'y\n'.repeat(800));
      const fallbackCompute = _computeLineDiff('x\n'.repeat(800), 'y\n'.repeat(800));
      results.fallbackDeleteCount = fallbackCompute.filter(e => e.type === 'delete').length;
      results.fallbackInsertCount = fallbackCompute.filter(e => e.type === 'insert').length;

      console.log(JSON.stringify(results));
    """
    data = _run_node_script(script)

    # Empty inputs: JavaScript ''.split('\n') yields [''], so diffLines('', '') yields an equal empty line
    assert data["bothEmpty"] == [{"type": "equal", "text": ""}]
    assert data["oldEmpty"] == [
        {"type": "delete", "text": ""},
        {"type": "insert", "text": "alpha"},
        {"type": "insert", "text": "beta"},
    ]
    assert data["newEmpty"] == [
        {"type": "delete", "text": "alpha"},
        {"type": "delete", "text": "beta"},
        {"type": "insert", "text": ""},
    ]
    assert data["identical"] == [
        {"type": "equal", "text": "line1"},
        {"type": "equal", "text": "line2"},
    ]

    # Whitespace differences must be detected
    assert data["trailingWhitespace"] == [
        {"type": "delete", "text": "hello"},
        {"type": "insert", "text": "hello  "},
    ]
    assert data["indentationDifference"] == [
        {"type": "delete", "text": "  func()"},
        {"type": "insert", "text": "    func()"},
    ]
    assert data["blankLines"] == [
        {"type": "equal", "text": "a"},
        {"type": "delete", "text": ""},
        {"type": "equal", "text": "b"},
    ]

    # Guard and fallback
    assert data["cellGuardTriggered"] is None
    assert data["fallbackDeleteCount"] == 801
    assert data["fallbackInsertCount"] == 801


def test_chunk_grouping_structure():
    """Verify _buildDiffChunks groups contiguous change blocks correctly."""
    script = r"""
      import { _computeLineDiff, _buildDiffChunks } from './static/js/document/ui/diffViewer.js';

      const oldText = 'line1\noldA\nline3\noldB1\noldB2\nline6';
      const newText = 'line1\nnewA\nline3\nnewB1\nnewB2\nline6';

      const entries = _computeLineDiff(oldText, newText);
      const chunks = _buildDiffChunks(entries);

      console.log(JSON.stringify({ chunks }));
    """
    data = _run_node_script(script)
    chunks = data["chunks"]

    assert len(chunks) == 2

    # Chunk 0: replacement at line 1
    assert chunks[0]["id"] == 0
    assert chunks[0]["oldLines"] == ["oldA"]
    assert chunks[0]["newLines"] == ["newA"]
    assert chunks[0]["startLine"] == 1
    assert chunks[0]["resolved"] is False
    assert chunks[0]["accepted"] is False

    # Chunk 1: multi-line replacement (startLine is 4 in interleaved entries view)
    assert chunks[1]["id"] == 1
    assert chunks[1]["oldLines"] == ["oldB1", "oldB2"]
    assert chunks[1]["newLines"] == ["newB1", "newB2"]
    assert chunks[1]["startLine"] == 4
    assert chunks[1]["resolved"] is False
    assert chunks[1]["accepted"] is False


def test_diff_mode_dom_mounting_and_unmounting():
    """Verify enterDiffMode and exitDiffMode DOM lifecycle in a real browser page."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div id="container">
          <div id="doc-diff-toggle-btn" class="doc-action-icon-btn">Toggle</div>
          <div id="doc-editor-wrap">
            <div id="doc-editor-highlight"><div id="doc-editor-code"></div></div>
            <div id="doc-line-numbers"></div>
            <textarea id="doc-editor-textarea">line 1\nold A\nline 3</textarea>
          </div>
        </div>
      `);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/diffViewer.js');

        const oldContent = 'line 1\nold A\nline 3';
        const newContent = 'line 1\nnew A\nline 3';

        // 1. Enter diff mode
        mod.enterDiffMode(oldContent, newContent);

        const wrap = document.getElementById('doc-editor-wrap');
        const textarea = document.getElementById('doc-editor-textarea');
        const codeEl = document.getElementById('doc-editor-code');
        const toolbar = document.getElementById('doc-diff-toolbar');
        const toggleBtn = document.getElementById('doc-diff-toggle-btn');
        const statusEl = document.getElementById('diff-toolbar-status');

        const mounted = {
          isActive: mod.isDiffModeActive(),
          wrapHasDiffModeClass: wrap.classList.contains('diff-mode'),
          textareaReadOnly: textarea.readOnly,
          codeHasDiffDataset: codeEl.dataset.hasDiff === '1',
          toolbarExists: Boolean(toolbar),
          toggleBtnActive: toggleBtn.classList.contains('active'),
          statusText: statusEl ? statusEl.textContent : '',
          diffEqualLines: codeEl.querySelectorAll('.diff-line-equal').length,
          diffDelLines: codeEl.querySelectorAll('.diff-line-del').length,
          diffAddLines: codeEl.querySelectorAll('.diff-line-add').length,
          hasPrevBtn: Boolean(document.getElementById('doc-diff-prev-btn')),
          hasNextBtn: Boolean(document.getElementById('doc-diff-next-btn')),
          hasAcceptAllBtn: Boolean(document.getElementById('doc-diff-accept-all-btn')),
          hasRejectAllBtn: Boolean(document.getElementById('doc-diff-reject-all-btn')),
        };

        // 2. Exit diff mode with discard
        mod.exitDiffMode(true, { persist: false });

        const unmounted = {
          isActiveAfterExit: mod.isDiffModeActive(),
          wrapHasDiffModeClass: wrap.classList.contains('diff-mode'),
          textareaReadOnly: textarea.readOnly,
          codeHasDiffDataset: Boolean(codeEl.dataset.hasDiff),
          toolbarExists: Boolean(document.getElementById('doc-diff-toolbar')),
          toggleBtnActive: toggleBtn.classList.contains('active'),
          textareaValue: textarea.value,
        };

        return { mounted, unmounted };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)
    m = data["mounted"]
    u = data["unmounted"]

    # Mount assertions
    assert m["isActive"] is True
    assert m["wrapHasDiffModeClass"] is True
    assert m["textareaReadOnly"] is True
    assert m["codeHasDiffDataset"] is True
    assert m["toolbarExists"] is True
    assert m["toggleBtnActive"] is True
    assert "0 / 1 changes resolved" in m["statusText"]
    assert m["diffEqualLines"] == 2
    assert m["diffDelLines"] == 1
    assert m["diffAddLines"] == 1
    assert m["hasPrevBtn"] is True
    assert m["hasNextBtn"] is True
    assert m["hasAcceptAllBtn"] is True
    assert m["hasRejectAllBtn"] is True

    # Unmount assertions
    assert u["isActiveAfterExit"] is False
    assert u["wrapHasDiffModeClass"] is False
    assert u["textareaReadOnly"] is False
    assert u["codeHasDiffDataset"] is False
    assert u["toolbarExists"] is False
    assert u["toggleBtnActive"] is False
    assert u["textareaValue"] == "line 1\nold A\nline 3"


def test_chunk_acceptance_and_rejection_textarea_content():
    """Verify resolving chunks individually applies choices progressively into textarea."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div id="container">
          <div id="doc-diff-toggle-btn">Toggle</div>
          <div id="doc-editor-wrap">
            <div id="doc-editor-highlight"><div id="doc-editor-code"></div></div>
            <div id="doc-line-numbers"></div>
            <textarea id="doc-editor-textarea"></textarea>
          </div>
        </div>
      `);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/diffViewer.js');
        const textarea = document.getElementById('doc-editor-textarea');

        let saved = [];
        mod.initDiffViewer({
          saveDocument: () => { saved.push(textarea.value); },
          syncHighlighting: () => {},
          updateLineNumbers: () => {},
        });

        const oldContent = 'intro\nold 1\nmiddle\nold 2\noutro';
        const newContent = 'intro\nnew 1\nmiddle\nnew 2\noutro';

        mod.enterDiffMode(oldContent, newContent);

        // Accept Chunk 0 ('new 1')
        mod._resolveChunk(0, true);
        const afterChunk0 = textarea.value;

        // Reject Chunk 1 ('old 2')
        mod._resolveChunk(1, false);
        const afterChunk1 = textarea.value;

        return {
          afterChunk0,
          afterChunk1,
          savedCount: saved.length,
          lastSaved: saved[saved.length - 1],
        };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)

    # After Chunk 0 accepted, Chunk 1 is not resolved so keeps old
    assert data["afterChunk0"] == "intro\nnew 1\nmiddle\nold 2\noutro"
    # After Chunk 1 rejected, final result uses new 1 and old 2
    assert data["afterChunk1"] == "intro\nnew 1\nmiddle\nold 2\noutro"
    assert data["savedCount"] >= 2
    assert data["lastSaved"] == "intro\nnew 1\nmiddle\nold 2\noutro"


def test_chunk_navigation_and_status_updates():
    """Verify navigation buttons and status bar text update as chunks are resolved."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div id="container">
          <div id="doc-diff-toggle-btn">Toggle</div>
          <div id="doc-editor-wrap">
            <div id="doc-editor-highlight"><div id="doc-editor-code"></div></div>
            <div id="doc-line-numbers"></div>
            <textarea id="doc-editor-textarea"></textarea>
          </div>
        </div>
      `);

      const result = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/diffViewer.js');

        const oldContent = 'first\nold A\nsecond\nold B\nthird\nold C\nend';
        const newContent = 'first\nnew A\nsecond\nnew B\nthird\nnew C\nend';

        mod.enterDiffMode(oldContent, newContent);

        const statusEl = document.getElementById('diff-toolbar-status');
        const s0 = statusEl.textContent;

        // Navigate forward
        mod._navigateToDiffChunk('next');
        const currentAfterNext = mod.getCurrentChunkIndex();

        // Navigate backward
        mod._navigateToDiffChunk('prev');
        const currentAfterPrev = mod.getCurrentChunkIndex();

        // Resolve one chunk
        mod._resolveChunk(0, true);
        const s1 = statusEl.textContent;

        // Resolve second chunk
        mod._resolveChunk(1, false);
        const s2 = statusEl.textContent;

        // Resolve all via Reject All for clean teardown test
        mod._resolveAllChunks(false);

        return {
          s0,
          s1,
          s2,
          currentAfterNext,
          currentAfterPrev,
          activeAfterTeardown: mod.isDiffModeActive(),
        };
      });

      console.log(JSON.stringify(result));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["s0"] == "0 / 3 changes resolved"
    assert data["s1"] == "1 / 3 changes resolved"
    assert data["s2"] == "2 / 3 changes resolved"
    assert data["currentAfterNext"] == 1
    assert data["currentAfterPrev"] == 0
    assert data["activeAfterTeardown"] is False
