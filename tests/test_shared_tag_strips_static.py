from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
STYLE = app_css()
INDEX = (ROOT / "static/index.html").read_text(encoding="utf-8")
CHIP_SCROLL = (ROOT / "static/js/chipScroll.js").read_text(encoding="utf-8")


def test_filter_tag_rows_share_one_horizontal_strip_contract():
    for selector in (
        ".skills-summary-strip",
        ".memory-category-filters:has(> .memory-cat-chip)",
        ".doclib-lang-chips",
        ".doclib-chips",
        ".notes-labels-bar",
        ".tasks-activity-filters",
        ".gallery-tag-chips",
        ".gallery-album-chips",
        ".gallery-ai-tags",
        ".cal-filters",
    ):
        assert selector in STYLE
    assert "flex-wrap: nowrap !important;" in STYLE
    assert "overflow-x: auto !important;" in STYLE
    assert "touch-action: pan-x;" in STYLE
    assert "scrollbar-width: none;" in STYLE


def test_filter_chips_share_dimensions_and_do_not_stack():
    assert "height: 23px !important;" in STYLE
    assert "border-radius: 6px !important;" in STYLE
    assert "flex: 0 0 auto;" in STYLE
    assert "white-space: nowrap;" in STYLE


def test_filter_tag_rows_share_drag_scroll_and_overflow_arrows():
    for selector in (
        ".skills-summary-strip",
        "#memory-category-filters",
        ".doclib-lang-chips",
        ".doclib-chips",
        ".notes-labels-bar",
        ".tasks-activity-filters",
        ".gallery-tag-chips",
        ".gallery-album-chips",
        ".gallery-ai-tags",
        ".cal-filters",
    ):
        assert selector in CHIP_SCROLL
    assert "doclib-chip-scroll-arrow" in CHIP_SCROLL
    assert "is-pointer-dragging" in CHIP_SCROLL
    assert "Math.abs(delta) <= 4" in CHIP_SCROLL
    assert "new MutationObserver(sync).observe(strip" in CHIP_SCROLL
    assert "attributeFilter: ['class', 'hidden', 'style']" in CHIP_SCROLL
    assert "frame.style.display = stripHidden ? 'none' : ''" in CHIP_SCROLL


def test_memory_and_skills_filters_follow_their_search_fields():
    memory_search = INDEX.index('id="memory-search"')
    memory_filters = INDEX.index('id="memory-category-filters"')
    skills_search = INDEX.index('id="skills-search"')
    skills_summary = INDEX.index('id="skills-summary"')
    assert memory_search < memory_filters
    assert skills_search < skills_summary
