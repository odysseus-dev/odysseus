"""Regression guard for the frontend error-surfacing follow-up to #1346.

`uploadPending()` in static/js/fileHandler.js used to read `data.files` from the
`/api/upload` response without checking `res.ok`, so a non-OK response (429 rate
limit, 413 too large, …) was swallowed: the files silently vanished and the chat
sent with no attachments, with no feedback to the user.

This used to be a source-text assertion ("the body contains 'Upload failed'")
because fileHandler.js pulls in browser globals. #6235 moved the *reporting* out
of fileHandler.js into chat.js, which is exactly what a text match cannot tell
apart from a regression: this test now drives the real module under node and
checks the surviving contract — a non-OK response is never swallowed (no ids
returned, the reason is recorded, the pending files are kept for a retry).
"""
import pytest

from tests.helpers.upload_failure_harness import (
    ATTACH_ONE_VIDEO,
    HAS_NODE,
    run_script,
)

pytestmark = pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")


def test_upload_pending_never_swallows_a_failed_upload():
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__failResponse(429, {"
        + " detail: 'Maximum concurrent uploads (5) exceeded' }));\n"
        + "const ids = await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ ids, reason: fh.getLastUploadError(),"
        + " pending: fh.getPendingCount(), cancelled: fh.wasLastUploadCancelled() }));\n"
    )
    assert result == {
        "ids": [],  # never pretend the send carried attachments
        "reason": "Maximum concurrent uploads (5) exceeded",  # not swallowed
        "pending": 1,  # still there for a retry
        "cancelled": False,  # a failure is not a user cancel
    }
