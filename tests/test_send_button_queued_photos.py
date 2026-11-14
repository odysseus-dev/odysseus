"""Resend and Edit queue a photo-only message's photos by id and click Send
with the message box empty. The Send button treated an empty box with no new
uploads as "New chat", so the click opened a new chat and the photos were
never sent."""
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "static" / "app.js"


def test_send_button_counts_photos_queued_by_resend_or_edit():
    src = _APP.read_text(encoding="utf-8")
    start = src.index("function _hasAttachments()")
    body = src[start:src.index("\n  }\n", start)]
    assert "_pendingRegenAttachmentIds()" in body
    assert "_pendingRegenAttachmentIds } from './js/chat.js" in src
