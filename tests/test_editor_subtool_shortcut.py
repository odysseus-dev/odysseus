"""A slot's main keyboard key must keep the sub-tool you last picked.

The gradient / paint-bucket slot (and the brush / eraser / pixel slot) share one
toolbar button. Picking Paint Bucket from the flyout re-renders the slot face as
bucket, so pressing G must re-activate the face (keeping Paint Bucket) rather
than falling through to the hidden gradient entry and snapping back.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
KEYS = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_slot_key_reactivates_the_current_member():
    assert "const flyoutItem = toolbar.querySelector(`[data-tool-option=\"${toolId}\"]`);" in KEYS
    assert "const slotEl = flyoutItem ? flyoutItem.closest('.ge-tool-slot') : null;" in KEYS
    assert "const firstMember = slotEl.querySelector('.ge-tool-flyout-item[data-tool-option]');" in KEYS
    assert "if (toolId === defaultTool) toolBtn = slotEl.querySelector('.ge-tool-slot-face');" in KEYS
    assert "toolBtn = toolBtn || toolbar.querySelector(`[data-tool=\"${toolId}\"]`) || flyoutItem;" in KEYS


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
