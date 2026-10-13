from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_yoyo_is_a_builtin_theme_with_its_saved_effect_defaults():
    source = (ROOT / "static/js/theme.js").read_text(encoding="utf-8")

    assert "yoyo:       { bg:'#211f23', fg:'#dfdbd7'" in source
    assert "yoyo:       'ascii-fireflies'" in source
    assert "yoyo:       '#b8e6c1'" in source
    assert ".filter(([name]) => !THEMES[name])" in source


def test_agent_theme_inventory_includes_yoyo():
    interaction = (ROOT / "src/ai_interaction.py").read_text(encoding="utf-8")
    schemas = (ROOT / "src/tool_schemas.py").read_text(encoding="utf-8")

    from src.theme_palette import THEME_PRESETS

    # The agent's preset inventory is the shared palette module's tuple.
    assert THEME_PRESETS[-2:] == ("monolith", "yoyo")
    assert "from src.theme_palette import THEME_PRESETS" in interaction
    assert "blueprint, monolith, yoyo" in schemas
