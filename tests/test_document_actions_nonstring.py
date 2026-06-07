"""Regression: document_actions title/content helpers must tolerate non-strings.

_norm_title/_content_fingerprint/_real_len used `(x or "")`, which only guards
falsy; a non-string (e.g. an int) is truthy, so `.strip()`/`re.sub(..., x)`
raised. They now coerce non-strings to "".
"""
from src.document_actions import _norm_title, _content_fingerprint, _real_len


def test_non_string_inputs_do_not_crash():
    assert _norm_title(123) == ""
    assert _content_fingerprint(123) == ""
    assert _real_len(["x"]) == 0


def test_valid_inputs_unchanged():
    assert _norm_title("  Hello   World ") == "hello world"
    assert _real_len("# Title") == len("Title")
