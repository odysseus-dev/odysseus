"""The message composer must render shell operators one character at a time."""

from pathlib import Path


def test_message_composer_disables_programming_font_ligatures():
    css = (
        Path(__file__).resolve().parents[1] / "static" / "style.css"
    ).read_text(encoding="utf-8")

    textarea_rule = css.split(".chat-input-bar textarea#message {", 1)[1].split("}", 1)[0]
    ghost_rule = css.split(".ghost-text-overlay {", 1)[1].split("}", 1)[0]

    for rule in (textarea_rule, ghost_rule):
        assert "font-variant-ligatures: none" in rule
        assert '"liga" 0' in rule
        assert '"calt" 0' in rule
