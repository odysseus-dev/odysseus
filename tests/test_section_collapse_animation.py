from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_section_collapse_waits_for_domino_animations():
    source = (ROOT / "static/js/section-management.js").read_text()

    assert "section.getAnimations({ subtree: true })" in source
    assert "a.animationName === 'section-domino-out'" in source


def test_domino_animation_targets_model_rows():
    css = (ROOT / "static/style.css").read_text()

    assert ".section.section-just-expanded :is(.list-item, .models-row)" in css
    assert ".section.section-just-collapsing :is(.list-item, .models-row)" in css
