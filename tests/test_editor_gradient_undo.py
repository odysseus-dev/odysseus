"""Undoing an "add effect" step must remove the effect from the layer.

Regression: `_restoreState` rebuilt each layer but never restored `layer.effects`
(only groups got theirs back), so a freshly added gradient survived the first
Ctrl+Z and only disappeared on a second undo that restored an older snapshot.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_restore_state_restores_layer_effects():
    assert "layer.effects = Array.isArray(s.effects)" in EDITOR
    assert "? s.effects.map(effect => _normalizeEffect(effect))" in EDITOR
    assert "layer._effectPreview = null;" in EDITOR


def test_restore_state_still_restores_group_effects():
    assert "effects: Array.isArray(group.effects) ? group.effects.map(effect => _normalizeEffect(effect)) : []," in EDITOR


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
