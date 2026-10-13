from pathlib import Path
import re

from tests.helpers.stylesheets import app_css


CSS = app_css()


def rule_for(selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*(.*?)\n\}}", CSS, re.DOTALL)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


def test_document_editor_uses_main_background_at_rest_and_during_interaction():
    base_rule = CSS.split(".doc-editor-textarea {", 1)[1].split("}", 1)[0]
    interaction_rule = CSS.split(".doc-editor-textarea:hover,", 1)[1].split("}", 1)[0]

    assert "background: var(--bg) !important;" in base_rule
    assert "background: var(--bg) !important;" in interaction_rule


def test_document_editor_surfaces_do_not_bleed_sidebar_background():
    for selector in (".doc-editor-wrap {", ".doc-line-numbers {", ".doc-email-richbody.richtext-mode {"):
        rule = rule_for(selector)
        assert "background: var(--bg);" in rule
        assert "sidebar-bg" not in rule

    highlight_rule = rule_for(".doc-editor-highlight {")
    assert "background: var(--bg) !important;" in highlight_rule


def test_saved_document_button_uses_regular_foreground_color():
    saved_rule = CSS.split('.doc-save-button[data-save-state="saved"]', 1)[1].split("}", 1)[0]
    assert "color: var(--fg);" in saved_rule
    assert "success" not in saved_rule


def test_compact_email_type_picker_is_left_aligned():
    rule = CSS.split(".doc-langpicker-trigger.doc-langpicker-email-compact {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-start;" in rule
    assert "padding: 0 0 0 6px;" in rule


def test_active_markdown_view_tabs_use_main_background():
    active_rule = rule_for(".md-view-toggle .md-view-opt.active {")
    write_rule = rule_for(".doc-md-toolbar.md-write-active #doc-md-view-toggle .md-view-opt.active {")

    assert "background: var(--bg) !important;" in active_rule
    assert "background: var(--bg) !important;" in write_rule
    assert "sidebar-bg" not in write_rule

    preview_surface_rule = rule_for(".doc-md-preview.md-preview-active {")
    assert "background: var(--bg);" in preview_surface_rule
    assert "border-top: 0;" in preview_surface_rule

    first_tab_rule = rule_for(".md-view-toggle .md-view-opt:first-child,")
    last_tab_rule = rule_for(".md-view-toggle .md-view-opt:last-child,")
    assert "background: var(--sidebar-bg, var(--panel)) !important;" in first_tab_rule
    assert "background: var(--sidebar-bg, var(--panel)) !important;" in last_tab_rule

    toolbar_rule = rule_for(".doc-md-toolbar {")
    assert "border-bottom: 1px solid var(--border);" in toolbar_rule
    assert "background: var(--sidebar-bg, var(--panel));" in toolbar_rule


def test_rich_text_highlight_icon_uses_toolbar_accent_styling():
    rule = rule_for(".rich-highlight-icon {")

    assert "display: block;" in rule
    assert "flex-shrink: 0;" in rule
    assert "color: var(--accent-primary, var(--red));" in rule
    assert "#fff" not in rule
