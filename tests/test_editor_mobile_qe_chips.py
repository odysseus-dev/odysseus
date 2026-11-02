import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_mobile_quick_edit_uses_chip_row():
    # The suggestion popup is turned into an in-card horizontal chip row on phones.
    assert ".ge-ai-command:not(.ge-ai-command-collapsed) {" in CSS
    assert "flex-direction: column;" in CSS
    assert ".ge-ai-command-suggestions {\n    position: static;\n    order: 1;" in CSS or (
        "position: static;" in CSS and "order: 1;" in CSS
    )
    assert "flex-direction: row;" in CSS
    assert "overflow-x: auto;" in CSS


def test_mobile_quick_edit_orders_form_after_chips():
    assert ".ge-ai-command-form { order: 2; }" in CSS
    assert ".ge-ai-command-status { order: 3; }" in CSS
    assert ".ge-ai-command-stack { order: 4; }" in CSS


def test_mobile_quick_edit_chip_hides_columns():
    assert ".ge-ai-command-suggestion-icon," in CSS
    assert ".ge-ai-command-suggestion-kind," in CSS
    assert ".ge-ai-command-suggestion-hint {" in CSS
    assert ".ge-ai-command-suggestion-main {" in CSS


def test_quick_edit_collapsed_bar_stays_translucent_over_the_canvas():
    # Hovering anywhere over the canvas must not drag the collapsed Quick Edit
    # bar fully in front of the image (annoying when zoomed in); only pointing
    # at the bar itself, or focusing it, brings it to full strength.
    assert ".ge-canvas-area:hover .ge-ai-command-collapsed {" in CSS
    assert "opacity: 0.55;" in CSS
    assert ".ge-canvas-area .ge-ai-command-collapsed:hover,\n.ge-canvas-area .ge-ai-command-collapsed:focus-within {" in CSS
    assert "opacity: 0.94;" in CSS


def test_sw_version_is_current():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
