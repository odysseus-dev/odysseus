"""The dock tab-strip overflow control shows an ellipsis, not a chevron."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_overflow_control_uses_ellipsis():
    dock = _read("static/js/editor/build/dock.js")
    assert '<circle cx="6" cy="12" r="1.9"/>' in dock
    assert '<circle cx="12" cy="12" r="1.9"/>' in dock
    assert '<circle cx="18" cy="12" r="1.9"/>' in dock


def test_right_chevron_removed_from_dock():
    dock = _read("static/js/editor/build/dock.js")
    assert 'polyline points="9 6 15 12 9 18"' not in dock
