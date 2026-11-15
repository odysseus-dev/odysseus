"""Unit and regression tests for extracted PDF Tools and Form Annotation Engine.

Validates:
1. Form-backed document detection heuristics.
2. Annotation markdown parsing, round-trip serialization, and value escaping.
3. PDF undo snapshot stack behavior and isolation.
4. Coordinate conversion, bounds calculation, and scale helpers.
5. Modular ES function declarations in the document implementation set.
"""

import json
import subprocess
from pathlib import Path

from tests.helpers.document_source import function_body

ROOT = Path(__file__).resolve().parents[1]


def _run_node_script(script: str) -> dict:
    """Run an ES module node script in the workspace root and parse JSON output."""
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


def test_pdf_tools_functions_are_modularly_defined():
    """Verify that all PDF tool functions exist in the implementation graph."""
    required_functions = [
        "_isFormBackedDoc",
        "_downloadFilledPdf",
        "_saveActiveDocBeforeExport",
        "_annotationRegexGlobal",
        "_escapeAnnotationValue",
        "_unescapeAnnotationValue",
        "_parseAnnotations",
        "_annotationLine",
        "_writeAnnotations",
        "_newAnnotationId",
        "_encodeFieldName",
        "_pdfMarkdownFromLive",
        "_pushPdfUndoSnapshot",
        "_undoPdfPaneAction",
        "_setPdfAnnotationDragging",
        "_wirePdfAnnotationMenuDismiss",
        "_setPdfDropMode",
        "_wirePdfPaneProximity",
        "_renderPdfPane",
        "_buildAnnotation",
        "_removeAnnotation",
        "_aiFillAnnotations",
        "_schedulePdfPaneSave",
        "_setPdfSaveStatus",
        "_savePdfPaneToMarkdown",
        "_refreshPdfPreviewIframe",
        "_setPdfViewActive",
        "clamp",
        "pxToPct",
        "pctToPx",
        "calculateFontSizeCqh",
        "calculateFieldRectPct",
        "calculateAnnotationPlacement",
        "calculateDragMove",
        "calculateResize",
        "initPdfTools",
    ]
    for fn_name in required_functions:
        body = function_body(fn_name)
        assert len(body) > 15, f"Function {fn_name} body should not be empty"


def test_form_backed_document_detection_heuristics():
    """Verify heuristics for identifying form-backed and PDF-backed documents."""
    script = r"""
      import { _isFormBackedDoc } from './static/js/document/ui/pdfTools.js';

      const results = {
        pdfFormString: _isFormBackedDoc('<!-- pdf_form_source upload_id="upl-123.pdf" fields="3" -->\n# Form'),
        pdfSourceString: _isFormBackedDoc('<!-- pdf_source upload_id="doc-456.pdf" -->\n# Packet'),
        withWhitespace: _isFormBackedDoc('<!--   pdf_form_source   upload_id="abc.pdf"   -->'),
        docObjectWithContent: _isFormBackedDoc({ content: '<!-- pdf_form_source upload_id="obj-789.pdf" -->' }),
        plainMarkdown: _isFormBackedDoc('# Regular Markdown Document\n\nNo PDF pointers here.'),
        emptyString: _isFormBackedDoc(''),
        nullValue: _isFormBackedDoc(null),
        undefinedValue: _isFormBackedDoc(undefined),
        emptyDocObject: _isFormBackedDoc({ content: '' }),
        otherComment: _isFormBackedDoc('<!-- regular_comment upload_id="xyz" -->'),
      };

      console.log(JSON.stringify(results));
    """
    out = _run_node_script(script)
    assert out["pdfFormString"] is True
    assert out["pdfSourceString"] is True
    assert out["withWhitespace"] is True
    assert out["docObjectWithContent"] is True
    assert out["plainMarkdown"] is False
    assert out["emptyString"] is False
    assert out["nullValue"] is False
    assert out["undefinedValue"] is False
    assert out["emptyDocObject"] is False
    assert out["otherComment"] is False


def test_annotation_escaping_and_unescaping_roundtrip():
    """Verify that multi-line values and backslashes escape and round-trip without loss."""
    script = r"""
      import {
        _escapeAnnotationValue,
        _unescapeAnnotationValue,
      } from './static/js/document/ui/pdfTools.js';

      const cases = [
        'Simple text',
        'Line 1\nLine 2\nLine 3',
        'Backslash \\ and newline \n mixed',
        'Path C:\\Documents\\Forms\\file.pdf',
        'Quotes "single\' and brackets [x]',
        '',
        null,
      ];

      const results = cases.map(val => {
        const escaped = _escapeAnnotationValue(val);
        const unescaped = _unescapeAnnotationValue(escaped);
        return {
          original: val == null ? '' : val,
          escaped,
          unescaped,
          hasRawNewline: escaped.includes('\n'),
        };
      });

      console.log(JSON.stringify(results));
    """
    results = _run_node_script(script)
    for r in results:
        assert not r["hasRawNewline"], f"Escaped text must not contain raw newline: {r['escaped']}"
        assert r["original"] == r["unescaped"], f"Roundtrip failed for {r['original']!r}"


def test_annotation_markdown_parsing_and_serialization():
    """Verify annotation bullet line generation, parsing, and write roundtrip."""
    script = r"""
      import {
        _annotationLine,
        _parseAnnotations,
        _writeAnnotations,
        _newAnnotationId,
      } from './static/js/document/ui/pdfTools.js';

      const annotations = [
        { id: 'ann-1', page: 1, x: 10.5, y: 20.25, w: 15.0, h: 3.5, kind: 'text', lineHeight: 1.4, fontSize: 12, value: 'First line\nSecond line' },
        { id: 'ann-2', page: 1, x: 50.0, y: 60.0, w: 2.5, h: 2.5, kind: 'check', lineHeight: 1.3, fontSize: 11, value: '[ ]' },
        { id: 'ann-3', page: 2, x: 25.0, y: 75.0, w: 22.0, h: 6.0, kind: 'signature', lineHeight: 1.3, fontSize: 11, value: 'signature:sig-99' },
        { id: 'ann-4', page: 2, x: 5.0, y: 90.0, w: 8.0, h: 2.5, kind: 'text', lineHeight: 1.3, fontSize: 11, value: '' },
      ];

      const initialMd = '# Title\n\nSome body text.\n';
      const writtenMd = _writeAnnotations(initialMd, annotations);
      const parsed = _parseAnnotations(writtenMd);

      const emptyWritten = _writeAnnotations(writtenMd, []);
      const newId1 = _newAnnotationId();
      const newId2 = _newAnnotationId();

      console.log(JSON.stringify({
        writtenMd,
        parsed,
        emptyWritten,
        newId1,
        newId2,
        hasAnnotationsHeading: writtenMd.includes('## Annotations'),
        emptyHasAnnotationsHeading: emptyWritten.includes('## Annotations'),
      }));
    """
    out = _run_node_script(script)
    assert out["hasAnnotationsHeading"] is True
    assert out["emptyHasAnnotationsHeading"] is False

    parsed = out["parsed"]
    assert len(parsed) == 4

    assert parsed[0]["id"] == "ann-1"
    assert parsed[0]["page"] == 1
    assert parsed[0]["x"] == 10.5
    assert parsed[0]["y"] == 20.25
    assert parsed[0]["w"] == 15.0
    assert parsed[0]["h"] == 3.5
    assert parsed[0]["kind"] == "text"
    assert parsed[0]["lineHeight"] == 1.4
    assert parsed[0]["fontSize"] == 12.0
    assert parsed[0]["value"] == "First line\nSecond line"

    assert parsed[1]["id"] == "ann-2"
    assert parsed[1]["kind"] == "check"

    assert parsed[2]["id"] == "ann-3"
    assert parsed[2]["kind"] == "signature"
    assert parsed[2]["value"] == "signature:sig-99"

    assert parsed[3]["id"] == "ann-4"
    assert parsed[3]["value"] == ""

    assert out["newId1"].startswith("ann-")
    assert out["newId2"].startswith("ann-")
    assert out["newId1"] != out["newId2"]


def test_pdf_undo_snapshot_stack_behavior():
    """Verify undo snapshot push, deduplication, stack cap, and doc isolation."""
    script = r"""
      import {
        _pdfUndoStackByDoc,
        _pushPdfUndoSnapshot,
        _undoPdfPaneAction,
        initPdfTools,
      } from './static/js/document/ui/pdfTools.js';

      const mockDocs = new Map([
        ['doc-a', { id: 'doc-a', content: '# Initial Content A' }],
        ['doc-b', { id: 'doc-b', content: '# Initial Content B' }],
      ]);

      let currentDocId = 'doc-a';

      initPdfTools({
        getActiveDocId: () => currentDocId,
        getDoc: (id) => mockDocs.get(id),
      });

      _pdfUndoStackByDoc.clear();

      // Push initial state
      _pushPdfUndoSnapshot('doc-a');
      const size1 = (_pdfUndoStackByDoc.get('doc-a') || []).length;

      // Duplicate consecutive push should be ignored
      _pushPdfUndoSnapshot('doc-a');
      const sizeAfterDuplicate = (_pdfUndoStackByDoc.get('doc-a') || []).length;

      // Mutate content and push again
      mockDocs.get('doc-a').content = '# Mutated Content A';
      _pushPdfUndoSnapshot('doc-a');
      const sizeAfterMutate = (_pdfUndoStackByDoc.get('doc-a') || []).length;

      // Document B isolation
      _pushPdfUndoSnapshot('doc-b');
      const sizeDocB = (_pdfUndoStackByDoc.get('doc-b') || []).length;

      // Verify max stack limit (50)
      for (let i = 0; i < 70; i++) {
        mockDocs.get('doc-a').content = `# Snapshot ${i}`;
        _pushPdfUndoSnapshot('doc-a');
      }
      const cappedSize = (_pdfUndoStackByDoc.get('doc-a') || []).length;

      console.log(JSON.stringify({
        size1,
        sizeAfterDuplicate,
        sizeAfterMutate,
        sizeDocB,
        cappedSize,
      }));
    """
    out = _run_node_script(script)
    assert out["size1"] == 1
    assert out["sizeAfterDuplicate"] == 1
    assert out["sizeAfterMutate"] == 2
    assert out["sizeDocB"] == 1
    assert out["cappedSize"] == 50


def test_coordinate_conversion_and_scale_helpers():
    """Verify mathematical helpers for percentage coordinate conversion, placement, and drag/resize."""
    script = r"""
      import {
        clamp,
        pxToPct,
        pctToPx,
        calculateFontSizeCqh,
        calculateFieldRectPct,
        calculateAnnotationPlacement,
        calculateDragMove,
        calculateResize,
      } from './static/js/document/ui/pdfTools.js';

      const results = {};

      // Clamp
      results.clampWithin = clamp(50, 0, 100);
      results.clampBelow = clamp(-10, 0, 100);
      results.clampAbove = clamp(120, 0, 100);

      // Percentage and pixel conversion
      results.pct1 = pxToPct(50, 200);
      results.pctZeroTotal = pxToPct(50, 0);
      results.px1 = pctToPx(25, 200);
      results.roundTripPx = pctToPx(pxToPct(88, 500), 500);

      // Font size container-query height scale
      results.cqh11 = calculateFontSizeCqh(11);
      results.cqh22 = calculateFontSizeCqh(22);

      // Field rect conversion
      results.fieldRect = calculateFieldRectPct([100, 200, 300, 400], 1000, 1000);

      // Annotation placement calculation
      const pageRect = { left: 100, top: 100, width: 800, height: 1000 };
      results.textPlacement = calculateAnnotationPlacement({
        clientX: 300,
        clientY: 400,
        pageRect,
        mode: 'text',
        pageNumber: 1,
        lastLineHeight: 1.6,
        lastFontSize: 14,
      });

      results.checkPlacement = calculateAnnotationPlacement({
        clientX: 300,
        clientY: 400,
        pageRect,
        mode: 'check',
        pageNumber: 1,
      });

      // Drag move calculation
      const dragStart = { mx: 200, my: 200, x: 20, y: 30 };
      results.dragMove = calculateDragMove(
        dragStart,
        { mx: 280, my: 250 },
        pageRect,
        15,
        5
      );

      // Resize calculation
      const resizeStart = { mx: 300, my: 300, w: 10, h: 4 };
      results.resize = calculateResize(
        resizeStart,
        { mx: 380, my: 350 },
        pageRect,
        20,
        30
      );

      console.log(JSON.stringify(results));
    """
    out = _run_node_script(script)

    assert out["clampWithin"] == 50
    assert out["clampBelow"] == 0
    assert out["clampAbove"] == 100

    assert out["pct1"] == 25.0
    assert out["pctZeroTotal"] == 0.0
    assert out["px1"] == 50.0
    assert abs(out["roundTripPx"] - 88.0) < 1e-6

    assert out["cqh11"] == 1.5
    assert out["cqh22"] == 3.0

    field_rect = out["fieldRect"]
    assert field_rect["left"] == 10.0
    assert field_rect["top"] == 20.0
    assert field_rect["width"] == 20.0
    assert field_rect["height"] == 20.0

    text_ann = out["textPlacement"]
    assert text_ann["page"] == 1
    assert text_ann["kind"] == "text"
    assert text_ann["lineHeight"] == 1.6
    assert text_ann["fontSize"] == 14
    assert text_ann["x"] == 25.0  # (300 - 100) / 800 * 100 = 25%
    assert text_ann["y"] == 30.0  # (400 - 100) / 1000 * 100 = 30%

    check_ann = out["checkPlacement"]
    assert check_ann["kind"] == "check"
    assert check_ann["value"] == "[ ]"
    # Check stamp is centered: 25% - (2.5 / 2) = 23.75%
    assert check_ann["x"] == 23.75
    # (400 - 100) / 1000 * 100 = 30% - (2.5 / 2) = 28.75%
    assert check_ann["y"] == 28.75

    drag = out["dragMove"]
    # dx = (280 - 200) / 800 * 100 = 10%. start.x + 10% = 30%
    assert drag["x"] == 30.0
    # dy = (250 - 200) / 1000 * 100 = 5%. start.y + 5% = 35%
    assert drag["y"] == 35.0

    resize = out["resize"]
    # dw = (380 - 300) / 800 * 100 = 10%. start.w + 10% = 20%
    assert resize["w"] == 20.0
    # dh = (350 - 300) / 1000 * 100 = 5%. start.h + 5% = 9%
    assert resize["h"] == 9.0


def test_decomposed_pdf_submodules_and_per_doc_isolation():
    """Verify pdf/geometry, pdf/annotations, and pdf/persistence standalone boundaries."""
    script = r"""
      import { calculateDragMove } from './static/js/document/ui/pdf/geometry.js';
      import { _isFormBackedDoc, _annotationRegexGlobal } from './static/js/document/ui/pdf/annotations.js';
      import {
        _pdfPaneAnnotationsByDoc,
        _pdfUndoStackByDoc,
        _cleanupPdfDocState,
      } from './static/js/document/ui/pdf/persistence.js';

      const moved = calculateDragMove(
        { x: 10, y: 10, mx: 200, my: 200 },
        { mx: 280, my: 250 },
        { width: 800, height: 1000 },
        20,
        10
      );
      _pdfPaneAnnotationsByDoc.set('doc-a', [{ id: 'a1' }]);
      _pdfPaneAnnotationsByDoc.set('doc-b', [{ id: 'b1' }]);
      _pdfUndoStackByDoc.set('doc-a', ['snap-a']);
      _pdfUndoStackByDoc.set('doc-b', ['snap-b']);

      _cleanupPdfDocState('doc-a');

      console.log(JSON.stringify({
        moved,
        isForm: _isFormBackedDoc('<!-- pdf_source upload_id="x" -->'),
        hasRegex: _annotationRegexGlobal() instanceof RegExp,
        aPresent: _pdfPaneAnnotationsByDoc.has('doc-a'),
        bPresent: _pdfPaneAnnotationsByDoc.has('doc-b'),
        aUndo: _pdfUndoStackByDoc.has('doc-a'),
        bUndo: _pdfUndoStackByDoc.get('doc-b'),
      }));
    """
    out = _run_node_script(script)

    assert out["moved"]["x"] == 20.0
    assert out["moved"]["y"] == 15.0
    assert out["isForm"] is True
    assert out["hasRegex"] is True
    assert out["aPresent"] is False
    assert out["bPresent"] is True
    assert out["aUndo"] is False
    assert out["bUndo"] == ["snap-b"]
