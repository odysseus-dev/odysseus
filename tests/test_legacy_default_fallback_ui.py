"""The retired default fallback editor must not imply active routing."""

from pathlib import Path

from bs4 import BeautifulSoup


_REPO = Path(__file__).resolve().parents[1]


def test_legacy_default_fallback_editor_is_absent():
    soup = BeautifulSoup(
        (_REPO / "static" / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    editor = soup.find(id="set-defaultFallbacks")

    assert editor is None
    assert soup.find(id="set-defaultAddFallback") is None


def test_default_model_save_does_not_rewrite_legacy_fallbacks():
    source = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    start = source.index("async function initDefaultChat()")
    end = source.index("/* ── Utility Model ── */", start)
    default_chat_source = source[start:end]

    assert "settings.default_model_fallbacks" not in default_chat_source
    assert "default_model_fallbacks:" not in default_chat_source
    assert "await _postSettings({" in default_chat_source
    assert "fetch('/api/auth/settings', { method: 'POST'" not in default_chat_source
    assert "default_reasoning_effort:" in default_chat_source
    assert "default_verbosity:" in default_chat_source
    assert "modelControlCapabilities('reasoning_effort'" in default_chat_source
    assert "modelControlCapabilities('verbosity'" in default_chat_source
    assert "['auto', 'off', 'on', 'minimal'" not in default_chat_source
    assert "set-defaultFallbacks" not in default_chat_source
    assert "set-defaultAddFallback" not in default_chat_source


def test_default_control_selects_start_auto_only_until_canonical_evidence_loads():
    soup = BeautifulSoup(
        (_REPO / "static" / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )

    for select_id in ("set-defaultReasoningSelect", "set-defaultVerbositySelect"):
        select = soup.find(id=select_id)
        assert select.has_attr("disabled")
        assert [option.get("value") for option in select.find_all("option")] == [""]
