"""The note window's CSS reuses spacing values the app already has, and keeps no rule for markup that is gone."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTES_CSS = ROOT / "static" / "css" / "15-notes.css"


def _css():
    return NOTES_CSS.read_text(encoding="utf-8")


def test_no_one_off_spacing_values():
    # 84vh, 1.65em and calc(1.4em * 3) appeared nowhere else in static/css:
    # the window uses 85vh like the other modals, the task text 1.6em, and
    # the context excerpts clamp to three lines.
    css = _css()
    for value in ("84vh", "1.65em", "1.4em"):
        assert value not in css, value


def test_history_title_rule_has_markup():
    css = _css()
    if ".note-window-history-title" not in css:
        return
    sources = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "frontend" / "src" / "features" / "notes").rglob("*.js"))
    assert re.search(r"note-window-history-title\b", sources), ".note-window-history-title is styled but never rendered"
