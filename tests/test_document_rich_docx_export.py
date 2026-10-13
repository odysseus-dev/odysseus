"""Artifact-level coverage for structured Rich Text Word export."""

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_rich_docx_converter_maps_editor_structure_instead_of_raw_html():
    converter = DOC_JS.split("function _docxHexColor", 1)[1].split(
        "async function exportAsDocx", 1
    )[0]
    exporter = DOC_JS.split("async function exportAsDocx", 1)[1].split(
        "/** Delete the active document", 1
    )[0]

    for helper in (
        "_docxImageFallbackRun",
        "_docxImageWidth",
        "_docxImagePngData",
        "_docxPrepareImages",
        "_docxInlineChildren",
        "_docxParagraphFromElement",
        "_docxListBlocks",
        "_docxTableBlock",
        "_docxFigureBlocks",
        "_docxBlocksFromNodes",
        "_richTextToDocxChildren",
        "_markdownToDocxChildren",
    ):
        assert f"function {helper}" in DOC_JS
    assert "new docx.ExternalHyperlink" in converter
    assert "new docx.ImageRun" in converter
    assert "new docx.PageBreak" in converter
    assert "new docx.Paragraph({ thematicBreak: true })" in converter
    assert "new docx.Table(" in converter
    assert "new docx.TableRow(" in converter
    assert "new docx.TableCell(" in converter
    assert "docx.VerticalAlign?.CENTER" in converter
    assert "docx.HeadingLevel.HEADING_6" in converter
    assert "_isRichTextLang(lang)" in exporter
    assert "_richTextToDocxChildren(text, window.docx)" in exporter
    assert "_markdownToDocxChildren(text, window.docx)" in exporter


def test_browser_word_export_contains_native_rich_docx_ooxml():
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as output:
        output_path = Path(output.name)
    script = rf"""
      import {{ chromium }} from 'playwright';
      const browser = await chromium.launch({{ headless: true }});
      const page = await browser.newPage({{
        viewport: {{ width: 900, height: 700 }},
        acceptDownloads: true,
      }});
      await page.goto(`${{process.env.ODYSSEUS_TEST_STATIC_ORIGIN}}/static/js/documentStats.js`);
      await page.route('**/api/upload/docx-image-test', route => route.fulfill({{
        status: 200,
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64'),
      }}));
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {{
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&docx-export-test=1');
        mod.init('/api');
        mod.injectFreshDoc({{
          id: 'rich-docx-export-doc',
          title: 'Rich export',
          language: 'richtext',
          version_count: 1,
          current_content: '<h1>Project brief</h1><p><strong>Bold</strong>, <em>italic</em>, <u>underlined</u>, <s>struck</s>, and <a href="https://example.com/reference">linked</a>.</p><ol><li>First item</li><li>Second item</li></ol><ul class="rich-checklist"><li data-checked="true">Finished task</li></ul><figure class="richtext-image"><img class="richtext-image richtext-image-size-35 richtext-image-align-center" src="/api/upload/docx-image-test" alt="Export diagram"><div class="richtext-image-caption" role="note" aria-label="Image caption">Figure caption</div></figure><table><tbody><tr><th>Name</th><th>Status</th></tr><tr><td>Alpha</td><td style="vertical-align: middle;">Ready</td></tr></tbody></table><hr><hr class="richtext-page-break"><h2>Second page</h2>',
        }});
        await new Promise(resolve => setTimeout(resolve, 450));
      }});
      await page.locator('#doc-footer-export-btn').click();
      await page.waitForSelector('#doc-export-menu');
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#doc-export-menu .doc-overflow-item').filter({{ hasText: 'Export as Word' }}).click();
      const download = await downloadPromise;
      await download.saveAs({json.dumps(str(output_path))});
      console.log(JSON.stringify({{
        filename: download.suggestedFilename(),
        failure: await download.failure(),
      }}));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["failure"] is None
        assert data["filename"].endswith(".docx")
        assert output_path.stat().st_size > 5000

        with zipfile.ZipFile(output_path) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")
            numbering_xml = package.read("word/numbering.xml").decode("utf-8")
            relationships_xml = package.read("word/_rels/document.xml.rels").decode("utf-8")
            media_names = [
                name for name in package.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            ]
            media = package.read(media_names[0]) if media_names else b""

        assert '&lt;h1&gt;' not in document_xml
        assert '<w:pStyle w:val="Heading1"/>' in document_xml
        assert '<w:pStyle w:val="Heading2"/>' in document_xml
        for text in (
            "Project brief", "Bold", "italic", "underlined", "struck", "linked",
            "First item", "Second item", "[x] ", "Finished task", "Name", "Status",
            "Figure caption", "Alpha", "Ready", "Second page",
        ):
            assert text in document_xml
        assert "<w:b/>" in document_xml
        assert "<w:i/>" in document_xml
        assert '<w:u w:val="single"/>' in document_xml
        assert "<w:strike/>" in document_xml
        assert "<w:hyperlink" in document_xml
        assert "https://example.com/reference" in relationships_xml
        assert "<w:numPr>" in document_xml
        assert '<w:numFmt w:val="decimal"/>' in numbering_xml
        assert "<w:tbl>" in document_xml
        assert "<w:tblHeader/>" in document_xml
        assert '<w:vAlign w:val="center"/>' in document_xml
        assert "<w:pBdr>" in document_xml
        assert '<w:br w:type="page"/>' in document_xml
        assert len(media_names) == 1
        assert media.startswith(b"\x89PNG\r\n\x1a\n")
        assert "relationships/image" in relationships_xml
        assert "<w:drawing>" in document_xml
        assert '<w:jc w:val="center"/>' in document_xml
        assert '<wp:extent cx="2076450" cy="2076450"/>' in document_xml
        assert 'descr="Export diagram"' in document_xml
        assert "[Image: Export diagram]" not in document_xml
    finally:
        output_path.unlink(missing_ok=True)


def test_browser_markdown_word_export_keeps_heading_and_inline_formatting():
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as output:
        output_path = Path(output.name)
    script = rf"""
      import {{ chromium }} from 'playwright';
      const browser = await chromium.launch({{ headless: true }});
      const page = await browser.newPage({{
        viewport: {{ width: 900, height: 700 }},
        acceptDownloads: true,
      }});
      await page.goto(`${{process.env.ODYSSEUS_TEST_STATIC_ORIGIN}}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {{
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&markdown-docx-export-test=1');
        mod.init('/api');
        mod.injectFreshDoc({{
          id: 'markdown-docx-export-doc',
          title: 'Markdown export',
          language: 'markdown',
          version_count: 1,
          current_content: '# Markdown title\n\nA **bold phrase** and *italic phrase*.',
        }});
        await new Promise(resolve => setTimeout(resolve, 450));
      }});
      await page.locator('#doc-footer-export-btn').click();
      await page.waitForSelector('#doc-export-menu');
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#doc-export-menu .doc-overflow-item').filter({{ hasText: 'Export as Word' }}).click();
      const download = await downloadPromise;
      await download.saveAs({json.dumps(str(output_path))});
      console.log(JSON.stringify({{ failure: await download.failure() }}));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["failure"] is None
        with zipfile.ZipFile(output_path) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")
        assert '<w:pStyle w:val="Heading1"/>' in document_xml
        assert "Markdown title" in document_xml
        assert "bold phrase" in document_xml
        assert "italic phrase" in document_xml
        assert "<w:b/>" in document_xml
        assert "<w:i/>" in document_xml
    finally:
        output_path.unlink(missing_ok=True)
