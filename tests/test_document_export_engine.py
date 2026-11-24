"""Dedicated unit and regression tests for the Document Export and DOCX Engine.

Validates:
1. Base export naming sanitization (slashes, non-ASCII, empty titles).
2. HTML export markup generation with clean CSS and theme variables.
3. Visual report generation structure and pre-export persistence.
4. Dynamic docx import handling and error recovery.
"""

import json
import subprocess
from pathlib import Path
from tests.helpers.document_source import function_body

ROOT = Path(__file__).resolve().parents[1]


def _run_node_module(script: str) -> dict:
    """Run an ES module node script and parse JSON output."""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Node script failed with code {result.returncode}:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    return json.loads(result.stdout)


def test_export_engine_functions_are_modularly_defined():
    """Verify all required export functions are defined in the module set."""
    for fn_name in (
        "showExportMenu",
        "exportDocument",
        "_getExportBaseName",
        "_importFromDevice",
        "exportAsHtml",
        "exportAsVisualReport",
        "previewVisualReport",
        "exportAsPdf",
        "exportAsDocx",
        "ensureDocx",
        "_docxHexColor",
        "_docxInlineStyle",
        "_docxTextRuns",
        "_docxImageFallbackRun",
        "_docxImageWidth",
        "_docxImageAlignment",
        "_docxImagePngData",
        "_docxPrepareImages",
        "_docxInlineChildren",
        "_docxParagraphOptions",
        "_docxParagraphFromElement",
        "_docxListBlocks",
        "_docxTableBlock",
        "_docxFigureBlocks",
        "_docxBlocksFromNodes",
        "_richTextToDocxChildren",
        "_markdownToDocxChildren",
        "_richDocxNumbering",
    ):
        body = function_body(fn_name)
        assert len(body) > 15, f"Function {fn_name} body should not be empty"


def test_base_export_naming_sanitization():
    """Validate filename sanitization for slashes, non-ASCII, and empty titles."""
    script = r"""
      import { _getExportBaseName } from './static/js/document/ui/exportDocx.js';

      const results = {
        slashes: _getExportBaseName({ title: 'projects/2026/roadmap' }),
        backslashes: _getExportBaseName({ title: 'c:\\notes\\finance' }),
        symbols: _getExportBaseName({ title: 'Quarterly: Review * 2026?' }),
        nonAscii: _getExportBaseName({ title: 'café résumé naïve' }),
        emojis: _getExportBaseName({ title: '🚀 Launch Plan 📑' }),
        empty: _getExportBaseName({ title: '' }),
        whitespace: _getExportBaseName({ title: '     ' }),
        slashesOnly: _getExportBaseName({ title: '///' }),
        underscoresOnly: _getExportBaseName({ title: '_____' }),
        withVersion: _getExportBaseName({ title: 'Annual Report', version: 3 }),
        emptyWithVersion: _getExportBaseName({ title: '', version: 2 }),
        stringArg: _getExportBaseName('Direct String Document'),
        nullDoc: _getExportBaseName(null),
        undefinedDoc: _getExportBaseName(undefined),
      };

      console.log(JSON.stringify(results));
    """
    res = _run_node_module(script)

    assert res["slashes"] == "projects_2026_roadmap"
    assert res["backslashes"] == "c__notes_finance"
    assert res["symbols"] == "Quarterly_ Review _ 2026_"
    assert res["nonAscii"] == "caf_ r_sum_ na_ve"
    assert res["emojis"] == "__ Launch Plan __"
    assert res["empty"] == "document"
    assert res["whitespace"] == "document"
    assert res["slashesOnly"] == "document"
    assert res["underscoresOnly"] == "document"
    assert res["withVersion"] == "Annual Report_v3"
    assert res["emptyWithVersion"] == "document_v2"
    assert res["stringArg"] == "Direct String Document"
    assert res["nullDoc"] == "document"
    assert res["undefinedDoc"] == "document"


def test_html_export_markup_generation_and_theme_variables():
    """Validate HTML export markup structure, clean CSS, and theme variables."""
    script = r"""
      import { buildHtmlExportMarkup, _richTextExportCss, initExportDocx } from './static/js/document/ui/exportDocx.js';

      initExportDocx({
        getMarkdownModule: () => ({
          sanitizeAllowedHtml: html => String(html || '').replace(/<script[\s\S]*?<\/script>/gi, ''),
        }),
      });

      const richDoc = {
        title: 'Project & Status <2026>',
        language: 'richtext',
        content: '<h1>Header</h1><p>Body paragraph</p><hr class="richtext-page-break">',
      };
      const richHtml = buildHtmlExportMarkup(richDoc);

      initExportDocx({});
      const failClosedHtml = buildHtmlExportMarkup({
        title: 'Fail Closed',
        language: 'richtext',
        content: '<p>Hi</p><script>alert(1)</script>',
      });

      const plainDoc = {
        title: 'Script File',
        language: 'python',
        content: 'def run():\n    return "<script>alert(1)</script>"',
      };
      const plainHtml = buildHtmlExportMarkup(plainDoc);

      const css = _richTextExportCss();

      console.log(JSON.stringify({
        richHtml,
        failClosedHtml,
        plainHtml,
        css,
      }));
    """
    res = _run_node_module(script)

    assert '<script>' not in res["failClosedHtml"]
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in res["failClosedHtml"]

    rich = res["richHtml"]
    assert "<!DOCTYPE html>" in rich
    assert '<meta charset="utf-8">' in rich
    assert "<title>Project &amp; Status &lt;2026&gt;</title>" in rich
    assert '<style>' in rich
    assert '--doc-export-bg:#ffffff;' in rich
    assert '--doc-export-text:#111827;' in rich
    assert '--doc-export-border:#c9ced6;' in rich
    assert '--doc-export-muted:#6b7280;' in rich
    assert 'html,body{background:var(--doc-export-bg,#fff);color:var(--doc-export-text,#000)}' in rich
    assert 'break-after:page' in rich
    assert 'page-break-after:always' in rich
    assert '<h1>Header</h1><p>Body paragraph</p><hr class="richtext-page-break">' in rich

    plain = res["plainHtml"]
    assert "<!DOCTYPE html>" in plain
    assert '<pre style="white-space:pre-wrap;font-size:12px;font-family:monospace;">' in plain
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in plain
    assert '<script>alert(1)</script>' not in plain

    css = res["css"]
    assert ':root{' in css
    assert '--doc-export-accent:#2563eb;' in css
    assert 'table{width:100%;border-collapse:collapse;table-layout:fixed;margin:1em 0}' in css


def test_visual_report_generation_structure():
    """Validate visual report export endpoints, pre-save flush, and download trigger."""
    script = r"""
      import { initExportDocx, exportAsVisualReport, previewVisualReport } from './static/js/document/ui/exportDocx.js';

      let saveCalled = false;
      let fetchUrl = '';
      let fetchOpts = {};
      let createdAnchor = null;
      let clicked = false;
      let removed = false;

      // Mock DOM and network dependencies
      const targetDoc = { id: 'doc-report-123', title: 'Q4 Performance', version: 1 };

      globalThis.fetch = async (url, opts) => {
        fetchUrl = url;
        fetchOpts = opts;
        return {
          ok: true,
          status: 200,
          statusText: 'OK',
          blob: async () => ({ size: 1024, type: 'text/html' }),
          text: async () => '<html><body>Visual Report Body</body></html>',
        };
      };

      globalThis.URL = {
        createObjectURL: () => 'blob:mock-visual-report-url',
        revokeObjectURL: () => {},
      };

      globalThis.document = {
        createElement: (tag) => {
          if (tag === 'a') {
            createdAnchor = {
              href: '',
              download: '',
              click: () => { clicked = true; },
              remove: () => { removed = true; },
            };
            return createdAnchor;
          }
          return {};
        },
        body: {
          appendChild: () => {},
        },
      };

      initExportDocx({
        getApiBase: () => 'https://example.odysseus.internal',
        getActiveDocId: () => targetDoc.id,
        getActiveDoc: () => targetDoc,
        saveActiveDocBeforeExport: async () => { saveCalled = true; },
        getUiModule: () => ({ showToast: () => {}, showError: () => {} }),
      });

      await exportAsVisualReport(targetDoc);

      const exportResult = {
        saveCalled,
        fetchUrl,
        credentials: fetchOpts?.credentials,
        downloadFilename: createdAnchor?.download,
        clicked,
        removed,
      };

      // Preview window test
      let previewWritten = [];
      let previewClosed = 0;
      let previewOpened = 0;
      globalThis.window = {
        open: (url, target) => {
          previewOpened++;
          return {
            document: {
              open: () => {},
              write: (chunk) => { previewWritten.push(chunk); },
              close: () => { previewClosed++; },
            },
          };
        },
      };

      await previewVisualReport(targetDoc);

      const previewResult = {
        previewOpened,
        previewClosed,
        previewWritten,
      };

      console.log(JSON.stringify({ exportResult, previewResult }));
    """
    res = _run_node_module(script)

    exp = res["exportResult"]
    assert exp["saveCalled"] is True
    assert exp["fetchUrl"] == "https://example.odysseus.internal/api/document/doc-report-123/visual-report"
    assert exp["credentials"] == "same-origin"
    assert exp["downloadFilename"] == "Q4 Performance_v1-visual-report.html"
    assert exp["clicked"] is True
    assert exp["removed"] is True

    prev = res["previewResult"]
    assert prev["previewOpened"] == 1
    assert prev["previewClosed"] >= 2
    assert any("Visual Report Body" in chunk for chunk in prev["previewWritten"])


def test_docx_dynamic_import_and_error_recovery():
    """Validate dynamic docx loading and resilient error recovery after network failures."""
    script = r"""
      import { ensureDocx } from './static/js/document/ui/exportDocx.js';

      let scriptElements = [];
      let nextScriptOutcome = 'error'; // 'error' or 'success'

      globalThis.window = {};
      globalThis.document = {
        createElement: (tag) => {
          if (tag === 'script') {
            const script = {
              src: '',
              onload: null,
              onerror: null,
              removed: false,
              remove: function() { this.removed = true; },
            };
            scriptElements.push(script);
            return script;
          }
          return {};
        },
        head: {
          appendChild: (el) => {
            // Trigger failure or success asynchronously
            setTimeout(() => {
              if (nextScriptOutcome === 'error') {
                el.onerror?.(new Error('Network offline'));
              } else {
                globalThis.window.docx = { Document: class {}, Packer: {} };
                el.onload?.();
              }
            }, 10);
          },
        },
      };

      // 1. Initial attempt fails
      nextScriptOutcome = 'error';
      let firstAttemptFailed = false;
      try {
        await ensureDocx();
      } catch (err) {
        firstAttemptFailed = true;
      }

      const scriptCountAfterFailure = scriptElements.length;
      const firstScriptRemoved = scriptElements[0]?.removed;

      // 2. Error recovery: second attempt must retry rather than returning a permanently cached rejection
      nextScriptOutcome = 'success';
      const docxResult = await ensureDocx();
      const scriptCountAfterSuccess = scriptElements.length;
      const successResolved = Boolean(docxResult && docxResult === globalThis.window.docx);

      // 3. Subsequent call returns cached resolved promise without injecting a third script
      const thirdCallResult = await ensureDocx();
      const scriptCountAfterThird = scriptElements.length;

      console.log(JSON.stringify({
        firstAttemptFailed,
        scriptCountAfterFailure,
        firstScriptRemoved,
        scriptCountAfterSuccess,
        successResolved,
        scriptCountAfterThird,
      }));
    """
    res = _run_node_module(script)

    assert res["firstAttemptFailed"] is True
    assert res["scriptCountAfterFailure"] == 1
    assert res["firstScriptRemoved"] is True
    assert res["scriptCountAfterSuccess"] == 2
    assert res["successResolved"] is True
    assert res["scriptCountAfterThird"] == 2


def test_import_from_device_extracted_module_and_export_extensions():
    """Verify the extracted importFromDevice module and exportDocx extension mapping."""
    script = r"""
      import { _importFromDevice, initImportFromDevice } from './static/js/document/ui/importFromDevice.js';
      import { exportDocument } from './static/js/document/ui/exportDocx.js';

      const downloads = [];
      globalThis.document = {
        getElementById: () => null,
        createElement: (tag) => {
          if (tag !== 'a') return {};
          return {
            href: '',
            download: '',
            click() { downloads.push(this.download); },
          };
        },
      };
      globalThis.URL = {
        createObjectURL: () => 'blob:test',
        revokeObjectURL: () => {},
      };

      exportDocument(null, { doc: { title: 'Script', language: 'python' }, content: 'print(1)' });
      exportDocument(null, { doc: { title: 'Chart', language: 'svg' }, content: '<svg/>' });
      exportDocument(null, { doc: { title: 'Config', language: 'xml' }, content: '<cfg/>' });

      console.log(JSON.stringify({
        hasImport: typeof _importFromDevice === 'function',
        hasInit: typeof initImportFromDevice === 'function',
        downloads,
      }));
    """
    res = _run_node_module(script)

    assert res["hasImport"] is True
    assert res["hasInit"] is True
    assert res["downloads"] == ["Script.py", "Chart.svg", "Config.xml"]


def test_docx_inline_and_richtext_export_rejects_unsafe_links_and_fails_closed():
    """DOCX inline hyperlinks and rich-text export must reject dangerous URLs and escape if sanitizer is absent."""
    script = r"""
      import { _docxInlineChildren, _richTextToDocxChildren, initExportDocx } from './static/js/document/ui/exportDocx.js';

      globalThis.Node = { ELEMENT_NODE: 1, TEXT_NODE: 3 };
      class FakeTextRun { constructor(opts) { this.kind = 'TextRun'; Object.assign(this, opts); } }
      class FakeExternalHyperlink { constructor(opts) { this.kind = 'ExternalHyperlink'; Object.assign(this, opts); } }
      class FakeParagraph { constructor(opts) { this.kind = 'Paragraph'; Object.assign(this, opts); } }
      const fakeDocx = {
        TextRun: FakeTextRun,
        ExternalHyperlink: FakeExternalHyperlink,
        Paragraph: FakeParagraph,
        HeadingLevel: { HEADING_1: 'h1', HEADING_2: 'h2', HEADING_3: 'h3', HEADING_4: 'h4', HEADING_5: 'h5', HEADING_6: 'h6' },
      };

      function makeLink(href, text) {
        return {
          nodeType: 1,
          tagName: 'A',
          getAttribute: (n) => (n === 'href' ? href : null),
          childNodes: [{ nodeType: 3, nodeValue: text }],
        };
      }

      const inline = _docxInlineChildren(
        [
          makeLink('https://example.com/ok', 'Valid HTTPS'),
          makeLink('mailto:team@example.com', 'Valid Mailto'),
          makeLink('javascript:alert(1)', 'Bad JS'),
          makeLink('java\tscript:alert(1)', 'Bad Tab JS'),
          makeLink('https://', 'Malformed Empty Host'),
        ],
        fakeDocx
      );

      // Without sanitizer or DOMParser, _richTextToDocxChildren fails closed safely without throwing or passing raw HTML.
      initExportDocx({});
      const emptyNoParser = await _richTextToDocxChildren('<script>alert(1)</script>', fakeDocx);

      console.log(JSON.stringify({
        links: inline.filter(n => n.kind === 'ExternalHyperlink').map(n => n.link),
        texts: inline.filter(n => n.kind === 'TextRun').map(n => n.text),
        emptyNoParser,
      }));
    """
    res = _run_node_module(script)

    assert res["links"] == ["https://example.com/ok", "mailto:team@example.com"]
    assert res["texts"] == ["Bad JS", "Bad Tab JS", "Malformed Empty Host"]
    assert res["emptyNoParser"] == []
