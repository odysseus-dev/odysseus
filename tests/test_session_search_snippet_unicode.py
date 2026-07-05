"""Regression: _snippet must center on the match even with non-ASCII prefixes.

_snippet located the query with content.lower().find(...) and then sliced the
ORIGINAL content with that index. str.lower() is not length-preserving —
Turkish "İ" (U+0130) lowercases to "i" + a combining dot (two codepoints) — so
an index taken from the lowercased copy is shifted relative to the string being
sliced. With enough expanding characters before the match, the returned snippet
no longer contained the searched term. The match is now located with an
index-preserving case-insensitive search; ASCII behaviour is unchanged.
"""
from src.session_search import _snippet


def test_snippet_keeps_match_when_prefix_expands_under_lowercasing():
    prefix = "İ" * 40  # each lowercases to two codepoints, shifting indices
    content = prefix + "MATCHWORD and here is some context text that follows."
    snip = _snippet(content, "matchword", radius=10)
    assert "MATCHWORD" in snip


def test_snippet_ascii_match_unchanged():
    assert "modal" in _snippet("We talked about modal jazz theory", "modal jazz").lower()
    assert "identifier" in _snippet("We discussed customidentifier routing.", "identifier")


def test_snippet_no_match_returns_prefix():
    assert _snippet("nothing relevant here", "absent", radius=5) == "nothing re"
