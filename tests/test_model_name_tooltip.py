"""Regression for issue #1982 — long model names are clipped with ellipsis in
two surfaces (the model-picker dropdown items and the chat-header model
indicator) with no tooltip, so the suffix/variant tag is undiscoverable.

The fix adds a `title` (native hover tooltip) carrying the full name to both
render sites in static/js/modelPicker.js. The module pulls in browser globals so
it can't be imported under node; this guards the two title assignments at source.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "static/js/modelPicker.js").read_text(encoding="utf-8")


def test_dropdown_item_has_title_tooltip():
    # The dropdown item name span must carry a title with the full display name.
    assert re.search(r"nameSpan\.title\s*=\s*m\.display", SRC), \
        "dropdown model-name span needs a title tooltip (#1982)"


def test_header_indicator_has_title_tooltip():
    # updateModelPicker must set the header label's title to the full model id
    # (empty for the 'Select model' placeholder).
    body = SRC[SRC.index("export function updateModelPicker()"):]
    assert re.search(r"label\.title\s*=\s*modelId\b", body), \
        "header model indicator needs a title tooltip (#1982)"
