"""One upload rejection must produce exactly one error message (issue #6235).

Reported behaviour: attaching a file over the 10 MB chat limit produced **two**
errors — a generic "Upload failed. Attachment kept so you can retry." in the
composer (`static/js/chat.js`) and the real reason ("File size exceeds 10 MB
limit") in the attachment strip (`static/js/fileHandler.js`). The reason users
needed sat in the spot they do not read first, and the size message never said
the limit is configurable.

The fix has two halves:

1. ``fileHandler.uploadPending()`` **records** the server's reason instead of
   announcing it — tested behaviourally by running the real module under node
   (`tests/helpers/upload_failure_harness.py`) and watching the toast channel.
2. ``chat.js`` renders the single composer message from that reason via
   ``static/js/uploadFeedback.js`` — the pure policy module is unit-tested
   behaviourally; the call-site wiring is asserted separately, because chat.js
   is a DOM entry module that cannot be imported standalone.
"""
import json
import re

import pytest

from tests.helpers.upload_failure_harness import (
    ATTACH_ONE_VIDEO,
    FEEDBACK,
    HAS_NODE,
    REPO,
    run_plain_script,
    run_script,
)

_CHAT = REPO / "static" / "js" / "chat.js"

_SIZE_REASON = (
    "File size exceeds 10 MB limit "
    "(configurable via ODYSSEUS_CHAT_UPLOAD_MAX_BYTES)"
)

pytestmark = pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")


def test_oversize_rejection_records_reason_and_stays_silent():
    """Exactly one notification: fileHandler must not fire the strip toast."""
    result = run_script(
        ATTACH_ONE_VIDEO
        + f"__queue(__failResponse(400, {{ detail: {json.dumps(_SIZE_REASON)} }}));\n"
        + "const ids = await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ ids, reason: fh.getLastUploadError(),"
        + " toasts: __toasts.length, pending: fh.getPendingCount() }));\n"
    )
    assert result == {
        "ids": [],
        "reason": _SIZE_REASON,
        "toasts": 0,  # chat.js owns the single message
        "pending": 1,  # kept for a retry
    }


def test_structured_detail_is_unwrapped_not_stringified():
    """`validate_file_upload` returns {error, message}; never "[object Object]"."""
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__failResponse(400, { detail: { error: 'FILE_TOO_LARGE',"
        + f" message: {json.dumps(_SIZE_REASON)} }} }}));\n"
        + "await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ reason: fh.getLastUploadError(),"
        + " toasts: __toasts.length }));\n"
    )
    assert result == {"reason": _SIZE_REASON, "toasts": 0}


def test_unparseable_body_falls_back_to_status():
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__brokenJson(502));\n"
        + "await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ reason: fh.getLastUploadError(),"
        + " toasts: __toasts.length }));\n"
    )
    assert result == {"reason": "HTTP 502", "toasts": 0}


def test_network_failure_is_recorded_not_toasted():
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__networkError('connection reset'));\n"
        + "await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ reason: fh.getLastUploadError(),"
        + " toasts: __toasts.length }));\n"
    )
    assert result == {"reason": "connection reset", "toasts": 0}


def test_success_clears_previous_failure():
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__failResponse(429, {"
        + " detail: 'Maximum concurrent uploads (5) exceeded' }));\n"
        + "await fh.uploadPending({ sessionId: 's' });\n"
        + "const first = fh.getLastUploadError();\n"
        + "await fh.addFiles([{ name: 'ok.txt', size: 10, type: 'text/plain' }]);\n"
        + "__queue(__ok({ files: [{ id: 'abc' }] }));\n"
        + "const ids = await fh.uploadPending({ sessionId: 's' });\n"
        + "console.log(JSON.stringify({ first, ids,"
        + " reason: fh.getLastUploadError(), toasts: __toasts.length }));\n"
    )
    assert result == {
        "first": "Maximum concurrent uploads (5) exceeded",
        "ids": ["abc"],
        "reason": "",
        "toasts": 0,
    }


def test_cancel_keeps_its_own_single_toast():
    """Cancelling is not a failure: it keeps the one 'Upload cancelled' toast."""
    result = run_script(
        ATTACH_ONE_VIDEO
        + "__queue(__hang());\n"
        + "const pending = fh.uploadPending({ sessionId: 's' });\n"
        + "fh.cancelUpload();\n"
        + "await pending;\n"
        + "console.log(JSON.stringify({ toasts: __toasts, cancelled:"
        + " fh.wasLastUploadCancelled(), reason: fh.getLastUploadError() }));\n"
    )
    assert result["cancelled"] is True
    assert result["reason"] == ""
    assert result["toasts"] == ["Upload cancelled"]


def test_message_composition_keeps_reason_and_retry_hint():
    """The composer sentence carries the reason once, plus the retry hint."""
    result = run_plain_script(
        f"const feedback = await import('{FEEDBACK.as_posix()}');\n"
        + "console.log(JSON.stringify({\n"
        + f"  sized: feedback.formatUploadFailure({json.dumps(_SIZE_REASON)}),\n"
        + "  bare: feedback.formatUploadFailure(''),\n"
        + "  already: feedback.formatUploadFailure('Upload failed: nope.'),\n"
        + "  list: feedback.extractUploadErrorReason("
        + "  { detail: [{ msg: 'field required' }] }, 422),\n"
        + "  top: feedback.extractUploadErrorReason({ error: 'BOOM' }, 400),\n"
        + "  empty: feedback.extractUploadErrorReason(null, 0),\n"
        + "  garbage: feedback.extractUploadErrorReason('nope', 500),\n"
        + "}));\n"
    )
    assert result["sized"] == (
        f"Upload failed: {_SIZE_REASON}. Attachment kept so you can retry."
    )
    assert result["bare"] == "Upload failed. Attachment kept so you can retry."
    assert result["already"] == "Upload failed: nope. Attachment kept so you can retry."
    assert result["list"] == "field required"
    assert result["top"] == "BOOM"
    assert result["empty"] == "network error"
    assert result["garbage"] == "HTTP 500"


def test_chat_renders_the_single_message_from_the_recorded_reason():
    """Wiring check for the DOM-coupled call site (chat.js cannot be imported).

    The #6235 review objected to a test that asserted *source text* instead of
    behaviour. This one is still a call-site assertion, but only because
    chat.js is an entry module with a dozen browser imports; every piece of
    logic it leans on is covered behaviourally above. What is left to pin is
    that the composer message is built from the recorded reason, and that the
    old generic literal is gone so it cannot shadow the reason again.
    """
    source = _CHAT.read_text(encoding="utf-8")
    assert "fileHandlerModule.getLastUploadError()" in source
    assert "uploadFeedback.formatUploadFailure(_reason)" in source
    assert "'Upload failed. Attachment kept so you can retry.'" not in source
    branch_start = source.index(
        "wasLastUploadCancelled && !fileHandlerModule.wasLastUploadCancelled()"
    )
    branch = source[branch_start:branch_start + 700]
    # `uiModule.showError && uiModule.showError(...)` is one guarded call.
    assert branch.count("showError(") == 1, branch


def test_upload_feedback_is_precached_and_every_loader_agrees_on_the_url():
    """Two cache-bust hazards that would quietly undo this fix.

    ``fileHandler.js`` is loaded from six places, and the service worker
    precaches its exact URL. If those URLs disagree, the browser keeps two
    module instances (or serves the old cached one), so the single-message
    contract silently regresses in production while every unit test stays
    green. Likewise the new policy module must be precached, or an offline
    load of ``fileHandler.js`` fails to resolve its import.
    """
    js = REPO / "static"
    loaders = [
        js / "js" / "chat.js",
        js / "js" / "chatRenderer.js",
        js / "js" / "gallery.js",
        js / "app.js",
        js / "sw.js",
        js / "index.html",
    ]
    seen = set()
    for path in loaders:
        found = re.findall(r"fileHandler\.js\?v=[A-Za-z0-9_.-]+", path.read_text(encoding="utf-8"))
        assert found, f"{path.name} no longer references fileHandler.js"
        seen.update(found)
    assert len(seen) == 1, f"fileHandler.js loaded under conflicting URLs: {sorted(seen)}"

    sw = (js / "sw.js").read_text(encoding="utf-8")
    assert "'/static/js/uploadFeedback.js'" in sw, "uploadFeedback.js must be precached"
