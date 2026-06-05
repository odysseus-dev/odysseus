"""mcp email server _decode_header must not inject spaces between parts.

email.header.decode_header returns plain-text runs WITH their surrounding
whitespace (e.g. (b"Re: ", None)), so joining parts with " " produced a
double space after "Re:" on every non-ASCII subject, a spurious space in
"Name <addr>" senders, and violated RFC 2047 6.2 which requires whitespace
between two adjacent encoded-words to be dropped.
"""
import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


def test_prefix_then_encoded_word_single_space():
    assert es._decode_header("Re: =?utf-8?b?SsOzc2U=?=") == "Re: J\u00f3se"


def test_encoded_word_then_plain_text():
    assert es._decode_header("=?utf-8?b?SsOzc2U=?= Smith") == "J\u00f3se Smith"


def test_adjacent_encoded_words_join_without_space():
    out = es._decode_header("=?iso-8859-1?q?Caf=E9?= =?utf-8?b?5pel5pys?=")
    assert out == "Caf\u00e9\u65e5\u672c"


def test_plain_ascii_header_unchanged():
    assert es._decode_header("Weekly report") == "Weekly report"


def test_empty_header():
    assert es._decode_header("") == ""
