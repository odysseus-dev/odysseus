"""Regression guard for the frontend error-surfacing follow-up to #1346.

`uploadPending()` in static/js/fileHandler.js used to read `data.files` from the
`/api/upload` response without checking `res.ok`, so a non-OK response (429 rate
limit, 413 too large, …) was swallowed: the files silently vanished and the chat
sent with no attachments, with no feedback to the user. It now checks `res.ok`
and shows a toast on failure, keeping the pending files for a retry.

fileHandler.js pulls in browser globals so it can't run under node; guard the
fix at the source level.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/fileHandler.js"


def _upload_pending_body() -> str:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("export async function uploadPending()")
    rest = text[start:]
    m = re.search(r"\n(export |function )", rest[1:])
    return rest[: m.start() + 1] if m else rest


def test_upload_pending_checks_response_and_surfaces_error():
    body = _upload_pending_body()
    # Must guard on the HTTP status before trusting the body...
    assert re.search(r"if\s*\(\s*!res\.ok\s*\)", body), "uploadPending must check res.ok"
    # ...and tell the user the upload failed (not swallow it).
    assert "Upload failed" in body
