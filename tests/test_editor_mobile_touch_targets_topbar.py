"""Mobile (<=700px) touch-target sizing for a few remaining editor controls.

Audit (2026-10-13, 390x844): after the earlier 44px pass, the top-menu buttons,
the Layers-header undo/redo/history/compare row, the Quick Edit field/run
button and the layer-opacity slider were still under the touch guideline, so
they were easy to miss with a thumb. Fix is CSS-only (sizing, no layout/order
change), appended as a mobile block in 13-image-editor.css.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_mobile_touch_target_block_present():
    assert ".ge-topbar .ge-btn-sm," in CSS
    assert ".ge-topbar .ge-stacked-btn {" in CSS
    assert "min-height: 34px;" in CSS
    assert "#ge-undo," in CSS
    assert "#ge-redo," in CSS
    assert "#ge-history-btn," in CSS
    assert "#ge-compare-btn {" in CSS
    assert "min-width: 38px;" in CSS
    assert "min-height: 38px;" in CSS
    assert "#ge-ai-command-input {" in CSS
    assert "min-height: 40px;" in CSS
    assert "#ge-ai-command-run {" in CSS
    assert "min-width: 42px;" in CSS


def test_touch_target_rules_are_mobile_scoped():
    idx = CSS.rindex(".ge-topbar .ge-btn-sm,")
    prefix = CSS[:idx]
    media = prefix.rfind("@media (max-width: 700px)")
    assert media != -1
    # the rules sit inside that media block (no closing brace before them)
    assert "}" not in CSS[media + len("@media (max-width: 700px)") : idx]


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
