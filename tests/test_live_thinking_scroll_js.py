"""Frontend regression coverage for live reasoning panel scroll behavior."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_live_thinking_stream_updates_preserve_panel_scroll_position():
    source = (_REPO / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "previousThinkScrollTop = thinkBox ? thinkBox.scrollTop : 0" in source
    assert "thinkBox.scrollTop = previousThinkScrollTop" in source
    assert "thinkBox.scrollTop = thinkBox.scrollHeight" not in source
