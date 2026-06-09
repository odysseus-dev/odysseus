"""routes.email_helpers._decode_header must not inject spaces between parts.

email.header.decode_header returns plain-text runs WITH their surrounding
whitespace (e.g. (b"Re: ", None)), so joining the parts with " " produced a
double space after "Re:" on every non-ASCII subject, a spurious space in
"Name <addr>" senders, and violated RFC 2047 6.2, which requires the
whitespace between two adjacent encoded-words to be dropped. The corruption
surfaced on the inbox list, message read, search, and the background pollers.

The sibling mcp_servers.email_server._decode_header was already fixed for this
(see tests/test_mcp_email_decode_header_spaces.py); these pin the same contract
for the routes.email_helpers copy.
"""
import os
import tempfile
from pathlib import Path

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus_decode_hdr_spaces_"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes.email_helpers import _decode_header


def test_prefix_then_encoded_word_single_space():
    # "Re: " (plain text, trailing space) followed by an encoded word must
    # keep exactly one space -- the old " ".join produced "Re:  Jose".
    assert _decode_header("Re: =?utf-8?b?SsOzc2U=?=") == "Re: Jóse"


def test_encoded_word_then_plain_text_single_space():
    assert _decode_header("=?utf-8?b?SsOzc2U=?= Smith") == "Jóse Smith"


def test_adjacent_encoded_words_join_without_space():
    # RFC 2047 6.2: whitespace between two adjacent encoded-words is dropped.
    out = _decode_header("=?iso-8859-1?q?Caf=E9?= =?utf-8?b?5pel5pys?=")
    assert out == "Café日本"


def test_plain_ascii_header_unchanged():
    assert _decode_header("Weekly report") == "Weekly report"
