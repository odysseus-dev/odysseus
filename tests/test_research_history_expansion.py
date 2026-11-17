"""Regression coverage for full-height Deep Research history expansion."""

from pathlib import Path
import re

from tests.helpers.stylesheets import app_css, stylesheet_cache_version


ROOT = Path(__file__).resolve().parent.parent


def test_history_list_participates_in_shared_library_expansion_layout():
    panel = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")
    css = app_css()

    assert 'id="research-past-list" class="doclib-grid memory-list research-jobs-list"' in panel
    card = css[css.index("#research-pane .research-history-card {"):]
    card = card[:card.index("}")]
    assert "flex: 1 1 0;" in card
    assert "min-height: 0;" in card
    assert "overflow: hidden;" in card
    assert 'class="memory-tab-panel research-tab-panel"' in panel
    assert 'class="admin-card research-new-job"' in panel
    assert 'id="research-history-filters" class="skills-summary-strip"' in panel
    assert 'class="research-history-search-wrap"' in panel
    assert '${_researchIcon}<span>Research</span>' in panel
    assert '${_magnifyIcon}' in panel
    assert "pane.classList.toggle('research-results-view', tab !== 'research')" in panel
    assert "display: flex; flex-direction: column; gap: 10px;" in css
    assert ".research-tabs {\n  flex: 0 0 auto;\n  margin: -4px -4px 0;" in css
    title = css[css.index("#research-pane .research-history-title-row h2 {"):]
    title = title[:title.index("}")]
    assert "letter-spacing: 0;" in title
    desc = css[css.index("#research-pane .research-history-desc {"):]
    assert "font-size: 11px;" in desc[:desc.index("}")]
    assert "font: inherit; font-size: 12px; cursor: pointer; text-align: left;" in css
    assert "#research-pane .research-history-search-wrap .memory-search-input" in css
    assert "height: 30px;" in css
    assert "background: color-mix(in srgb, var(--fg) 3%, transparent);" in css


def test_expanded_history_report_owns_and_scrolls_the_available_height():
    css = app_css()

    expanded = (
        "#research-pane #research-past-list "
        ".research-job-card.doclib-card-expanded"
    )
    result = f"{expanded} .research-job-result"

    assert expanded in css
    assert "display: flex !important" in css[css.index(expanded):css.index(expanded) + 320]
    assert result in css
    result_rules = css[css.index(result):css.index(result) + 400]
    assert "flex: 1 1 auto !important" in result_rules
    assert "min-height: 0 !important" in result_rules
    assert "overflow-y: auto !important" in result_rules
    assert ".research-history-card:has(.doclib-card-expanded) #research-past-list" in css
    assert (
        "#research-pane #research-past-list:has(.doclib-card-expanded) > "
        ".research-job-card:not(.doclib-card-expanded)"
    ) in css


def test_history_tab_replays_shared_domino_entrance():
    panel = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")

    assert "function _playHistoryCascade()" in panel
    assert "list.classList.add('doclib-just-opened')" in panel
    assert "const enteringHistory = tab === 'history'" in panel
    assert "if (enteringHistory) _playHistoryCascade();" in panel


def test_research_panel_uses_one_versioned_module_instance():
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    renderer = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")

    panel_version = re.search(r"research/panel\.js\?v=([^'\"]+)", app)
    asset_version = re.search(r"/static/app\.js\?v=([^'\"]+)", index)
    assert panel_version and renderer.count(f"research/panel.js?v={panel_version.group(1)}") == 2
    assert asset_version and asset_version.group(1) == stylesheet_cache_version()
