"""The retired default fallback editor must not imply active routing."""

from pathlib import Path

from bs4 import BeautifulSoup


_REPO = Path(__file__).resolve().parents[1]


def test_legacy_default_fallback_editor_is_hidden():
    soup = BeautifulSoup(
        (_REPO / "static" / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    editor = soup.find(id="set-defaultFallbacks")

    assert editor is not None
    assert editor.find_parent(class_="settings-row").has_attr("hidden")


def test_default_model_save_does_not_rewrite_legacy_fallbacks():
    source = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    start = source.index("async function initDefaultChat()")
    end = source.index("/* ── Utility Model ── */", start)
    default_chat_source = source[start:end]

    assert "settings.default_model_fallbacks" not in default_chat_source
    assert "default_model_fallbacks:" not in default_chat_source
    assert "default_reasoning_effort:" in default_chat_source
    assert "default_verbosity:" in default_chat_source
