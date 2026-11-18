"""The Send button counts photos that Resend/Edit queued by id.

Resend and Edit of a photo-only message queue the photo's id and click Send
with the message box empty. The Send button treated an empty box with no new
uploads as "New chat", so the click opened a new chat and the photo was never
sent. Driven through `node --input-type=module` against the real module.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_MODULE = (Path(__file__).resolve().parent.parent / "static" / "js" / "composerAttachments.js").as_uri()
_HAS_NODE = shutil.which("node") is not None


def has_attachments(pending, queued):
    script = (f"import {{ composerHasAttachments }} from '{_MODULE}';\n"
              f"console.log(JSON.stringify(composerHasAttachments({json.dumps(pending)}, {json.dumps(queued)})));")
    proc = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_a_photo_queued_by_resend_or_edit_counts():
    assert has_attachments(0, ["file-123"]) is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_an_upload_in_progress_counts():
    assert has_attachments(2, []) is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_nothing_queued_means_no_attachments():
    assert has_attachments(0, []) is False
    assert has_attachments(0, None) is False
