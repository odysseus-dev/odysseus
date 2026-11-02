from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_gallery_selection_dot_has_rendered_dimensions():
    css = app_css()
    rule = css[css.index(".gallery-select-dot {"):]
    rule = rule[:rule.index("}")]

    assert "display: block" in rule
    assert "width: 10px" in rule
    assert "height: 10px" in rule


def test_gallery_selection_highlight_is_painted_above_thumbnail():
    css = app_css()
    selector = ".gallery-card:has(.gallery-select-dot.selected)::after"
    rule = css[css.index(selector):]
    rule = rule[:rule.index("}")]

    assert "position: absolute" in rule
    assert "z-index: 3" in rule
    assert "border: 3px solid var(--accent-primary, var(--red))" in rule
    assert "pointer-events: none" in rule
