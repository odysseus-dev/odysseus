"""Regression: build_user_content must strip the '[PDF content]:' wrapper with
the prefix-safe helper, not str.lstrip(chars).

The PDF-attach path at build_user_content used
`_process_pdf(path).lstrip("\\n[PDF content]:")`, which treats the argument as a
set of characters and keeps eating leading body characters (so a page that
begins "Page 1 text]: to the board" lost its "P"/"to"). The other call sites
were switched to `strip_pdf_content_marker` (str.removeprefix); this one wasn't.
"""
import os
import tempfile

import src.document_processor as dp
import src.pdf_forms as pdf_forms
import src.pdf_form_doc as pdf_form_doc


class _FakeUploadHandler:
    def is_image_file(self, name, mime):
        return False

    def is_audio_file(self, name, mime):
        return False

    def is_document_file(self, name, mime):
        return True

    def _inside_upload_dir(self, path):
        return True


def test_pdf_body_marker_stripped_without_eating_text(monkeypatch, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    # Shape _process_pdf actually returns: marker, then a page-text marker, then body.
    raw = "\n\n[PDF content]:\n\n[Page 1 text]:\nto the board, the agenda is set"
    monkeypatch.setattr(dp, "_process_pdf", lambda path: raw)
    monkeypatch.setattr(pdf_forms, "has_form_fields", lambda path: False)
    monkeypatch.setattr(pdf_form_doc, "create_plain_pdf_document", lambda **kw: "doc-123")

    resolved = {"fid1": {"path": str(pdf_path), "mime": "application/pdf", "name": "doc.pdf"}}
    content = dp.build_user_content(
        text="here is a pdf",
        attachment_ids=["fid1"],
        upload_dir=str(tmp_path),
        upload_handler=_FakeUploadHandler(),
        session_id="s1",
        resolved_uploads=resolved,
    )

    body = content[0]["text"] if isinstance(content, list) else content
    body_lines = body.splitlines()
    # The leading page marker and page text must survive intact.
    assert "[Page 1 text]:" in body_lines
    assert "to the board, the agenda is set" in body_lines
    # The old lstrip(chars) corruption produced a line like "age 1 text]:" (missing "[P").
    assert "age 1 text]:" not in body_lines
