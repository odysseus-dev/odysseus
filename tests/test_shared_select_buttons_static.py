from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def _occurrences(text, needle):
    """Every index of needle, so an assertion does not depend on which copy of
    a selector the cascade happens to put first."""
    out, i = [], text.find(needle)
    while i != -1:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def test_bulk_select_triggers_share_dot_and_x_icons():
    css = app_css()

    for selector in (
        "#memory-select-btn",
        "#skills-select-btn",
        "#notes-select-btn",
        "#tasks-select-btn",
        "#doclib-chats-select-btn",
        "#doclib-arc-select-btn",
        "#doclib-research-select-btn",
        "#doclib-select-btn",
        "#gallery-select-btn",
        "#gallery-albums-select-btn",
        "#gallery-editor-drafts-select",
        "#email-lib-select-btn",
        "#archive-select-btn",
        "#lib-select-btn",
        "#hwfit-cache-select",
    ):
        assert selector in css

    assert "cy='11'" in css
    assert ").active::before" in css
    assert "#skills-select-btn::before" in css
    assert "translateY(1px)" in css


def test_library_titles_have_highlighted_respective_icons():
    source = (ROOT / "static" / "js" / "documentLibrary.js").read_text()

    assert source.count('class="doclib-section-title-icon"') == 4
    assert "color: var(--accent, var(--red));" in app_css()
    for title in ("Chats", "Archive", "Research", "Documents"):
        assert f">{title} <span" in source


def test_library_select_follows_tidy():
    source = (ROOT / "static" / "js" / "documentLibrary.js").read_text()
    css = app_css()

    assert source.index('id="doclib-chats-tidy-btn"') < source.index('id="doclib-chats-select-btn"')
    assert source.index('id="doclib-research-tidy-btn"') < source.index('id="doclib-research-select-btn"')
    assert source.index('id="doclib-tidy-btn"') < source.index('id="doclib-select-btn"')
    rule_start = css.rindex('[data-doclib-panel="documents"] #doclib-tidy-btn,')
    rule = css[rule_start:rule_start + 150]
    assert "#doclib-select-btn" in rule
    assert "top: 1px;" in rule

    chats_start = css.rindex("#doclib-panel-chats #doclib-chats-tidy-btn,")
    chats_rule = css[chats_start:chats_start + 200]
    assert "#doclib-chats-select-btn" in chats_rule
    assert "#doclib-arc-select-btn" in chats_rule
    assert "top: 1px;" in chats_rule

    research_start = css.rindex("#doclib-panel-research #doclib-research-tidy-btn,")
    research_rule = css[research_start:research_start + 150]
    assert "#doclib-research-select-btn" in research_rule
    assert "top: -0.5px;" in research_rule


def test_library_filter_chips_match_skills_count_markup():
    source = (ROOT / "static" / "js" / "documentLibrary.js").read_text()
    css = app_css()

    assert "function _setLibraryCountChipContent" in source
    assert "chip.replaceChildren(text, value)" in source
    assert source.count("className = 'skills-summary-chip'") >= 4
    assert "label + ' (' + count + ')'" not in source
    assert ".doclib-lang-chips > :is(.memory-cat-chip, .skills-summary-chip)" in css
    assert "min-height: 25px;" in css


def test_launch_cookbook_filter_chips_match_skills():
    source = (ROOT / "static" / "js" / "cookbookServe.js").read_text()

    assert 'class="skills-summary-chip active" data-serve-tag=""' in source
    assert 'class="skills-summary-chip" data-serve-tag=' in source
    assert '<span>All</span><strong>${allModels.length}</strong>' in source
    assert "querySelector('[data-serve-tag].active')" in source
    assert "querySelectorAll('[data-serve-tag]')" in source
    assert 'class="memory-cat-chip" data-serve-tag=' not in source


def test_launch_search_select_and_tags_move_up_together():
    css = app_css()

    rule = css[css.index("#serve-search,"):css.index("#serve-search,") + 140]
    assert "#hwfit-cache-select," in rule
    assert "#serve-tags" in rule
    assert "top: -2px;" in rule

    select_start = css.rindex("#hwfit-cache-select {")
    select_rule = css[select_start:select_start + 150]
    assert "top: -3px;" in select_rule
    list_start = css.index("#hwfit-cached-list {", css.index("#hwfit-cache-select {"))
    list_rule = css[list_start:list_start + 90]
    assert "top: -6px;" in list_rule

    bulk_start = css.index("#serve-bulk-bar {")
    bulk_rule = css[bulk_start:bulk_start + 100]
    assert "top: -6px;" in bulk_rule


def test_launch_server_selector_and_model_search_move_up_together():
    css = app_css()

    # The stylesheets are split across several files, so this selector group
    # is not guaranteed to be the first occurrence of its opening selector.
    # Check every occurrence instead of a fixed offset into the cascade.
    rules = [css[i:i + 130] for i in _occurrences(css, "#hwfit-server-select,")]
    assert any(
        "#hwfit-search" in rule and "top: -2px !important;" in rule
        for rule in rules
    ), "no #hwfit-server-select rule groups #hwfit-search with top: -2px !important"


def test_launch_heading_uses_accented_flame_icon():
    source = (ROOT / "static" / "js" / "cookbook.js").read_text()

    heading = source[source.index('id="serve-stats"') - 500:source.index('id="serve-stats"') + 120]
    assert ">Launch <span id=\"serve-stats\"" in heading
    assert "color:var(--accent, var(--red));" in heading


def test_launch_heading_reports_cached_model_count():
    source = (ROOT / "static" / "js" / "cookbookServe.js").read_text()

    assert "function _syncServeStats()" in source
    assert "stats.textContent = `${count} model${count === 1 ? '' : 's'}`;" in source
    assert source.count("_syncServeStats();") >= 2


def test_launch_search_matches_document_library_search_treatment():
    css = app_css()

    rule = css[css.index("#hwfit-search {", css.index("#hwfit-server-select,")):][:700]
    assert "height: 30px;" in rule
    assert "font-family: inherit;" in rule
    assert "font-size: 11px;" in rule
    assert "background-image: url(" in rule
    assert "padding-left: 28px;" in rule

    cached_search_start = css.rindex("#serve-search {")
    cached_search_rule = css[cached_search_start:cached_search_start + 800]
    assert "background-image: url(" in cached_search_rule
    assert "background-position: 9px center;" in cached_search_rule
    assert "padding-left: 28px;" in cached_search_rule


def test_memory_filter_chips_match_skills_and_are_not_cropped():
    source = (ROOT / "static" / "js" / "memory.js").read_text()
    css = app_css()

    assert "skills-summary-chip memory-filter-chip" in source
    assert "btn.replaceChildren(label, count)" in source
    assert "cat === 'all' ? memories.length : counts[cat] || 0" in source
    assert "#memory-category-filters > .skills-summary-chip" in css
    rule = css[css.index("#memory-category-filters {"):css.index("#memory-category-filters {") + 180]
    assert "min-height: 25px;" in rule
    assert "padding: 1px 0 !important;" in rule
