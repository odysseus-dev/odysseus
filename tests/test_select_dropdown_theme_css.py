from pathlib import Path


STYLE_CSS = Path(__file__).resolve().parents[1] / "static" / "style.css"


def _style_text() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def test_native_select_options_use_theme_tokens():
    css = _style_text()

    assert "--select-option-bg:" in css
    assert "--select-option-fg:" in css
    assert "--select-option-active-bg:" in css
    assert "select option,\n    select optgroup" in css
    assert "background-color: var(--select-option-bg);" in css
    assert "color: var(--select-option-fg);" in css
    assert "select option:checked" in css
    assert "background-color: var(--select-option-active-bg);" in css


def test_light_theme_keeps_native_selects_light():
    css = _style_text()

    light_theme_start = css.index(":root.light {")
    light_theme_end = css.index("}", light_theme_start)
    light_theme_block = css[light_theme_start:light_theme_end]

    assert "--select-bg: #eaeaea;" in light_theme_block
    assert "--select-option-bg: var(--panel);" in light_theme_block
    assert ":root.light select { color-scheme: light; }" in css
