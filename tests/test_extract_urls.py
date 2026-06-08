"""extract_urls must keep a *balanced* trailing ')' while still trimming
prose-glued punctuation.

The old cleanup stripped any trailing ')' unconditionally, which corrupted URLs
that legitimately end in one (Wikipedia disambiguation links being the common
case). The fix only drops an *unbalanced* ')'.
"""
from src.chat_helpers import extract_urls


def test_keeps_balanced_trailing_paren():
    text = "see https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert extract_urls(text) == [
        "https://en.wikipedia.org/wiki/Python_(programming_language)"
    ]


def test_strips_unbalanced_trailing_paren_from_prose():
    # The closing paren belongs to the sentence, not the URL.
    assert extract_urls("(see https://example.com)") == ["https://example.com"]


def test_strips_trailing_sentence_punctuation():
    assert extract_urls("go to https://example.com.") == ["https://example.com"]
    assert extract_urls("https://example.com, then continue") == [
        "https://example.com"
    ]


def test_strips_trailing_punctuation_after_balanced_close():
    # Keep the balanced ')' but drop the sentence period after it.
    text = "ref https://en.wikipedia.org/wiki/Foo_(bar)."
    assert extract_urls(text) == ["https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_nested_balanced_parens_preserved():
    text = "https://example.com/a_(b_(c))"
    assert extract_urls(text) == ["https://example.com/a_(b_(c))"]
