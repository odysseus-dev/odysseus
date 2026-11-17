"""Select Subject (offline one-click subject selection).

Static contract tests: the offline saliency engine, its wiring into the
`selection.subject` control op, the Select-menu entry and the sw cache bump.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "static" / "js" / "editor" / "select-subject.js"
EDITOR = ROOT / "static" / "js" / "galleryEditor.js"
TOPBAR = ROOT / "static" / "js" / "editor" / "build" / "topbar.js"
WIRE = ROOT / "static" / "js" / "editor" / "wire-topbar-menus.js"
SW = ROOT / "static" / "sw.js"


def test_module_exists_and_is_offline():
    src = MODULE.read_text()
    assert "export function computeSubjectMask(" in src
    assert "fetch(" not in src
    assert "XMLHttpRequest" not in src


def test_engine_uses_border_background_otsu_and_largest_blob():
    src = MODULE.read_text()
    # background estimated from the border cells
    assert "Background colour = average of the border cells" in src
    assert "addCell(sx, 0);" in src
    assert "addCell(sx, sh - 1);" in src
    assert "addCell(0, sy);" in src
    assert "addCell(sw - 1, sy);" in src
    # Otsu threshold helper
    assert "function otsuThreshold(hist, total) {" in src
    assert "const threshold = (otsuThreshold(hist, sal.length) / 255) * maxSal;" in src
    # largest connected component (4-neighbour flood fill)
    assert "Keep the largest connected component (4-neighbour flood fill)." in src
    assert "if (!best || group.length > best.length) best = group;" in src
    assert "if (!best || best.length < 0.005 * sw * sh) return null;" in src
    # guard when there is no distinct subject
    assert "if (maxSal < 0.12) return null;" in src
    # smooth upsample into a full-size mask
    assert "mctx.imageSmoothingQuality = 'high';" in src
    assert "mctx.drawImage(small, 0, 0, width, height);" in src


def test_editor_imports_and_helper():
    src = EDITOR.read_text()
    assert "import { computeSubjectMask as _computeSubjectMask } from './editor/select-subject.js';" in src
    assert "function _applyOfflineSubjectSelection(layer, mode = 'replace') {" in src
    assert "_computeSubjectMask(data, w, h, {});" in src
    assert "_commitSelectionMask(mask, layer, mode, 'subject');" in src


def test_control_op_prefers_offline_saliency():
    src = EDITOR.read_text()
    assert "'selection.subject':" in src
    assert "const offline = _applyOfflineSubjectSelection(layer, mode);" in src
    assert "if (offline) return { mode, method: 'saliency', width: offline.width, height: offline.height };" in src
    # still has the SAM + approximate fallbacks
    assert "method: 'sam'" in src
    assert "method: 'approx'" in src
    assert "_samWithRetry(layer, { text }, mode, null);" in src


def test_select_menu_entry_and_wiring():
    topbar = TOPBAR.read_text()
    assert 'data-selection-action="subject"' in topbar
    assert '<button class="dropdown-item-compact" data-selection-action="subject"><span>Subject</span></button>' in topbar
    wire = WIRE.read_text()
    assert "else if (action === 'subject') { window.geEditorControl?.invoke?.('selection.subject', {}); handled = true; }" in wire


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW.read_text())
