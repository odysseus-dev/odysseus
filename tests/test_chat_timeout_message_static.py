from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_timeout_catch_can_read_is_agent():
    """The catch block reads _isAgent, so it must not be a try-scoped const (#6764)."""
    src = (ROOT / "static/js/chat.js").read_text()
    assert re.search(r"^\s*let _isAgent = false;", src, re.M)
    assert not re.search(r"\bconst _isAgent\b", src)
    assert re.search(r"^\s*_isAgent = \(_tState\.mode", src, re.M)
