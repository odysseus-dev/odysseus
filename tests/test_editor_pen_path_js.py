from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_pen_path_module_exists():
    src = read("static/js/editor/tools/pen-path.js")
    for token in [
        "export function createPenPathTool(",
        "ensurePathLayer",
        "drawPath",
        "commitPath",
        "cancelPath",
        "finish",
        "hasDraft",
    ]:
        assert token in src


def test_toolbar_exposes_path_tool():
    src = read("static/js/editor/build/toolbar.js")
    assert "path: { label: 'Pen Path'" in src
    assert "'path'" in src
    assert "['pen', 'path']" in src
    assert "['text', 'shape']" in src


def test_controls_have_path_section():
    src = read("static/js/editor/build/controls.js")
    for token in [
        'id="ge-path-section"',
        'id="ge-path-finish"',
        'id="ge-path-cancel"',
        'id="ge-path-fill"',
        'id="ge-path-stroke"',
    ]:
        assert token in src


def test_gallery_wires_path_tool():
    src = read("static/js/galleryEditor.js")
    for token in [
        "from './editor/tools/pen-path.js'",
        "createPenPathTool(",
        "state.tool === 'path'",
        "_pathTool.begin(e)",
        "_pathTool.cancel()",
        "_ensurePathLayer",
        "_drawPathDraft",
    ]:
        assert token in src


def test_pen_path_module_is_precached():
    src = read("static/sw.js")
    assert "/static/js/editor/tools/pen-path.js" in src
