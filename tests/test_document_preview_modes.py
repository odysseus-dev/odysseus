"""Behavioral tests for document preview modes and live renderers.

Validates static/js/document/ui/previewModes.js:
1. CSV parsing, escaping, and bidirectional table sync.
2. HTML preview sandboxed iframe srcdoc generation with theme CSS.
3. Markdown preview toggle states and edit button placement.
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


def test_csv_parse_and_escape():
    """Verify parseCSV and csvEscapeField handle edge cases, quotes, newlines, and escaping."""
    script = r"""
      import { parseCSV, csvEscapeField } from './static/js/document/ui/previewModes.js';

      const results = {};

      // 1. Basic CSV parsing
      results.basic = parseCSV("col1,col2,col3\nval1,val2,val3\nval4,val5,val6");

      // 2. Quoted fields containing commas
      results.quotedCommas = parseCSV('name,description\nOdysseus,"A secure, modern workspace"');

      // 3. Quoted fields containing newlines
      results.quotedNewlines = parseCSV('colA,colB\n"line 1\nline 2",valB');

      // 4. Escaped quotes within quoted fields ("")
      results.escapedQuotes = parseCSV('quote\n"He said ""hello"" to everyone"');

      // 5. CRLF line endings and trailing empty rows
      results.crlf = parseCSV("alpha,beta\r\n10,20\r\n\r\n");

      // 6. csvEscapeField behaviors
      results.escapePlain = csvEscapeField("plain text");
      results.escapeWithComma = csvEscapeField("city, state");
      results.escapeWithQuote = csvEscapeField('said "yes"');
      results.escapeWithNewline = csvEscapeField("line1\nline2");
      results.escapeNull = csvEscapeField(null);
      results.escapeEmpty = csvEscapeField("");

      console.log(JSON.stringify(results));
    """
    data = _run_node_script(script)

    assert data["basic"] == [
        ["col1", "col2", "col3"],
        ["val1", "val2", "val3"],
        ["val4", "val5", "val6"],
    ]
    assert data["quotedCommas"] == [
        ["name", "description"],
        ["Odysseus", "A secure, modern workspace"],
    ]
    assert data["quotedNewlines"] == [
        ["colA", "colB"],
        ["line 1\nline 2", "valB"],
    ]
    assert data["escapedQuotes"] == [
        ["quote"],
        ['He said "hello" to everyone'],
    ]
    assert data["crlf"] == [
        ["alpha", "beta"],
        ["10", "20"],
    ]

    assert data["escapePlain"] == "plain text"
    assert data["escapeWithComma"] == '"city, state"'
    assert data["escapeWithQuote"] == '"said ""yes"""'
    assert data["escapeWithNewline"] == '"line1\nline2"'
    assert data["escapeNull"] == ""
    assert data["escapeEmpty"] == ""


def test_csv_table_preview_and_bidirectional_sync():
    """Verify CSV table preview generation, DOM editing, and bidirectional sync to textarea."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div id="doc-editor-wrap"><textarea id="doc-editor-textarea"></textarea></div>
        <div id="doc-csv-preview" style="display:none"></div>
        <div id="doc-header-actions"></div>
      `);

      const outcome = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/previewModes.js');
        const textarea = document.getElementById('doc-editor-textarea');
        const preview = document.getElementById('doc-csv-preview');
        const wrap = document.getElementById('doc-editor-wrap');

        let headerSynced = false;
        mod.initPreviewModes({
          getTextarea: () => textarea,
          getEditorWrap: () => wrap,
          getCsvPreview: () => preview,
          syncHeaderActions: () => { headerSynced = true; },
        });

        // 1. Initial toggle with CSV data
        textarea.value = 'Item,Price,Notes\nWidget,19.99,"Standard, stock"\nGadget,49.99,"Special"';
        mod.toggleCsvPreview();

        const tableMounted = !!preview.querySelector('.csv-table');
        const wrapHidden = wrap.style.display === 'none';
        const previewVisible = preview.style.display === '';
        const thCount = preview.querySelectorAll('thead th').length;
        const trCount = preview.querySelectorAll('tbody tr').length;

        // 2. Live edit in table DOM: edit Widget price and Notes
        const cells = preview.querySelectorAll('tbody tr:first-child td');
        cells[1].textContent = '24.99';
        cells[2].textContent = 'Updated, urgent';

        // 3. Bidirectional sync back to textarea
        let inputEventDispatched = false;
        textarea.addEventListener('input', () => { inputEventDispatched = true; }, { once: true });
        mod.syncTableToTextarea(preview, textarea);

        const syncedValue = textarea.value;

        // 4. Toggle back to source code view
        mod.toggleCsvPreview();
        const wrapRestored = wrap.style.display === '';
        const previewHidden = preview.style.display === 'none';

        // 5. Toggle with empty CSV surfaces error output
        textarea.value = '';
        mod.toggleCsvPreview();
        const errorOutput = document.getElementById('doc-run-output');
        const errorVisible = errorOutput && errorOutput.style.display === 'block';
        const errorText = errorOutput?.textContent || '';

        return {
          tableMounted,
          wrapHidden,
          previewVisible,
          thCount,
          trCount,
          inputEventDispatched,
          syncedValue,
          wrapRestored,
          previewHidden,
          headerSynced,
          errorVisible,
          errorText,
        };
      });

      console.log(JSON.stringify(outcome));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["tableMounted"] is True
    assert data["wrapHidden"] is True
    assert data["previewVisible"] is True
    assert data["thCount"] == 3
    assert data["trCount"] == 2
    assert data["inputEventDispatched"] is True
    assert data["syncedValue"] == 'Item,Price,Notes\nWidget,24.99,"Updated, urgent"\nGadget,49.99,Special\n'
    assert data["wrapRestored"] is True
    assert data["previewHidden"] is True
    assert data["headerSynced"] is True
    assert data["errorVisible"] is True
    assert "No data: CSV is empty or unparseable." in data["errorText"]


def test_themed_render_srcdoc_generation():
    """Verify _themedRenderSrcdoc produces sandboxed HTML with injected theme variables."""
    script = r"""
      import { _themedRenderSrcdoc } from './static/js/document/ui/previewModes.js';

      const results = {};

      // 1. Snippet without <head>
      const snippet = '<div class="banner">Welcome</div>';
      results.snippet = _themedRenderSrcdoc(snippet, 'html');

      // 2. Full document with <head>
      const fullDoc = '<!DOCTYPE html><html><head><title>Test App</title></head><body>Main</body></html>';
      results.fullDoc = _themedRenderSrcdoc(fullDoc, 'html');

      // 3. SVG vector rendering
      const svgCode = '<svg viewBox="0 0 100 100"><rect width="100" height="100" fill="red"/></svg>';
      results.svg = _themedRenderSrcdoc(svgCode, 'svg');

      // 4. XML rendering
      const xmlCode = '<feed><title>Updates</title></feed>';
      results.xml = _themedRenderSrcdoc(xmlCode, 'xml');

      console.log(JSON.stringify(results));
    """
    data = _run_node_script(script)

    # Snippet has injected style prepended
    assert "<style>html,body{margin:0;min-height:100%;background:" in data["snippet"]
    assert '<div class="banner">Welcome</div>' in data["snippet"]

    # Full document injects style inside <head>
    assert "<head><style>html,body{margin:0;min-height:100%;background:" in data["fullDoc"]
    assert "<title>Test App</title>" in data["fullDoc"]

    # SVG is wrapped in HTML body
    assert data["svg"].startswith("<!doctype html><html><head><style>")
    assert '<rect width="100" height="100" fill="red"/>' in data["svg"]

    # XML is wrapped in HTML body
    assert data["xml"].startswith("<!doctype html><html><head><style>")
    assert "<feed><title>Updates</title></feed>" in data["xml"]


def test_html_preview_toggle_and_exit_lifecycle():
    """Verify HTML preview toggling, theme root style inheritance, and exit cleanup."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <style>
          :root {
            --bg: #1c1c1e;
            --fg: #f2f2f7;
          }
        </style>
        <div id="doc-editor-wrap"><textarea id="doc-editor-textarea"></textarea></div>
        <div id="doc-md-preview" style="display:none"></div>
        <iframe id="doc-html-preview" style="display:none"></iframe>
        <select id="doc-language-select"><option value="html" selected>HTML</option></select>
      `);

      const outcome = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/previewModes.js');
        const textarea = document.getElementById('doc-editor-textarea');
        const iframe = document.getElementById('doc-html-preview');
        const wrap = document.getElementById('doc-editor-wrap');
        const mdPreview = document.getElementById('doc-md-preview');

        let tabsRendered = 0;
        mod.initPreviewModes({
          getTextarea: () => textarea,
          getEditorWrap: () => wrap,
          getHtmlPreview: () => iframe,
          getMdPreview: () => mdPreview,
          getLanguage: () => 'html',
          renderTabs: () => { tabsRendered++; },
        });

        // 1. Initial state
        const initialActive = mod.isHtmlPreviewActive();

        // 2. Activate HTML preview
        textarea.value = '<h1>Interactive Demo</h1><p>Running inside sandbox iframe.</p>';
        mod.toggleHtmlPreview();

        const activeAfterToggle = mod.isHtmlPreviewActive();
        const iframeVisible = iframe.style.display === '';
        const wrapHidden = wrap.style.display === 'none';
        const srcdoc = iframe.srcdoc;
        const tabsAfterOpen = tabsRendered;

        // 3. Exit HTML preview
        mod.exitHtmlPreview();

        const activeAfterExit = mod.isHtmlPreviewActive();
        const iframeHidden = iframe.style.display === 'none';
        const iframeCleared = iframe.srcdoc === '';
        const wrapRestored = wrap.style.display === '';
        const tabsAfterExit = tabsRendered;

        return {
          initialActive,
          activeAfterToggle,
          iframeVisible,
          wrapHidden,
          srcdoc,
          tabsAfterOpen,
          activeAfterExit,
          iframeHidden,
          iframeCleared,
          wrapRestored,
          tabsAfterExit,
        };
      });

      console.log(JSON.stringify(outcome));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["initialActive"] is False
    assert data["activeAfterToggle"] is True
    assert data["iframeVisible"] is True
    assert data["wrapHidden"] is True
    assert "#1c1c1e" in data["srcdoc"]
    assert "#f2f2f7" in data["srcdoc"]
    assert "<h1>Interactive Demo</h1>" in data["srcdoc"]
    assert data["tabsAfterOpen"] == 1

    assert data["activeAfterExit"] is False
    assert data["iframeHidden"] is True
    assert data["iframeCleared"] is True
    assert data["wrapRestored"] is True
    assert data["tabsAfterExit"] == 2


def test_markdown_preview_toggle_states_and_hover_edit_button():
    """Verify Markdown preview toggle states, hover edit button installation, and click action."""
    script = r"""
      import { chromium } from 'playwright';

      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);

      await page.setContent(`
        <div id="doc-editor-wrap"><textarea id="doc-editor-textarea"></textarea></div>
        <div id="doc-md-preview" style="display:none"></div>
        <select id="doc-language-select"><option value="markdown" selected>Markdown</option></select>
      `);

      const outcome = await page.evaluate(async () => {
        const mod = await import('/static/js/document/ui/previewModes.js');
        const textarea = document.getElementById('doc-editor-textarea');
        const preview = document.getElementById('doc-md-preview');
        const wrap = document.getElementById('doc-editor-wrap');

        const docs = new Map([
          ['doc-1', { id: 'doc-1', language: 'markdown', content: '' }],
        ]);
        let activeDocId = 'doc-1';
        let headersSynced = 0;

        mod.initPreviewModes({
          getTextarea: () => textarea,
          getEditorWrap: () => wrap,
          getMdPreview: () => preview,
          getActiveDocId: () => activeDocId,
          getDocs: () => docs,
          getLanguage: () => 'markdown',
          syncHeaderActions: () => { headersSynced++; },
        });

        // 1. Initial state
        const initialVisible = mod._isMarkdownPreviewVisible();

        // 2. Activate preview with markdown content
        textarea.value = '# Modern Odysseus\n\nPreview mode active.';
        mod.toggleMarkdownPreview();

        const previewVisible = mod._isMarkdownPreviewVisible();
        const wrapHidden = wrap.style.display === 'none';
        const docRemembered = docs.get('doc-1')._markdownPreviewActive;
        const htmlContent = preview.innerHTML;

        // 3. Verify Edit button was prepended
        const editButton = preview.querySelector('.doc-preview-hover-edit');
        const editButtonInstalled = !!editButton;
        const editButtonTitle = editButton?.getAttribute('title');
        const editButtonAria = editButton?.getAttribute('aria-label');
        const editButtonIsFirst = preview.firstElementChild === editButton;

        // 4. Test idempotency of _installMarkdownPreviewEditButton
        mod._installMarkdownPreviewEditButton(preview);
        const buttonCount = preview.querySelectorAll('.doc-preview-hover-edit').length;

        // 5. Click the Edit button to return to write mode
        let textareaFocused = false;
        textarea.focus = () => { textareaFocused = true; };

        editButton.click();

        // Wait for requestAnimationFrame to fire focus handler
        await new Promise(r => requestAnimationFrame(r));

        const previewHiddenAfterClick = preview.style.display === 'none';
        const wrapRestoredAfterClick = wrap.style.display === '';
        const docRememberedAfterClick = docs.get('doc-1')._markdownPreviewActive;

        return {
          initialVisible,
          previewVisible,
          wrapHidden,
          docRemembered,
          hasHeading: htmlContent.includes('Modern Odysseus'),
          editButtonInstalled,
          editButtonTitle,
          editButtonAria,
          editButtonIsFirst,
          buttonCount,
          previewHiddenAfterClick,
          wrapRestoredAfterClick,
          docRememberedAfterClick,
          textareaFocused,
        };
      });

      console.log(JSON.stringify(outcome));
      await browser.close();
    """
    data = _run_node_script(script)

    assert data["initialVisible"] is False
    assert data["previewVisible"] is True
    assert data["wrapHidden"] is True
    assert data["docRemembered"] is True
    assert data["hasHeading"] is True

    assert data["editButtonInstalled"] is True
    assert data["editButtonTitle"] == "Edit document"
    assert data["editButtonAria"] == "Edit document"
    assert data["editButtonIsFirst"] is True
    assert data["buttonCount"] == 1

    assert data["previewHiddenAfterClick"] is True
    assert data["wrapRestoredAfterClick"] is True
    assert data["docRememberedAfterClick"] is False
    assert data["textareaFocused"] is True
