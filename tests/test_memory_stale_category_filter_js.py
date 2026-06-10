"""Issue #2776 — buildCategoryChips must drop a stale active-category filter.

memory.js is a browser ES module with a DOM-touching import chain, so it can't be
imported in node in isolation. Per the repo's convention for DOM-coupled guards
(static source assertion), this pins that buildCategoryChips resets activeCategory
to 'all' when the active category no longer exists — otherwise deleting the last
memory of that category strands the panel on "No matches."
"""
import re
from pathlib import Path

SRC = Path("static/js/memory.js").read_text(encoding="utf-8")


def _build_category_chips_body():
    start = SRC.index("function buildCategoryChips()")
    rest = SRC[start + len("function buildCategoryChips()"):]
    m = re.search(r"\n(?:export\s+)?(?:async\s+)?function ", rest)
    return rest[: m.start()] if m else rest


def test_build_category_chips_resets_stale_active_category():
    body = _build_category_chips_body()
    assert "cats.has(activeCategory)" in body
    assert re.search(
        r"activeCategory\s*!==\s*'all'\s*&&\s*!cats\.has\(activeCategory\)\)\s*activeCategory\s*=\s*'all'",
        body,
    ), body


def test_guard_runs_before_chip_render():
    body = _build_category_chips_body()
    # must reset before the sorted chip list is built (so the active chip matches)
    assert body.index("cats.has(activeCategory)") < body.index("const sorted")
