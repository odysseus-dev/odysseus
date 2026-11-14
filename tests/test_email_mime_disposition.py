"""Disposition types, rather than filenames or casing, select MIME parts."""
from email import message_from_bytes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from mcp_servers import email_server as mcp
from routes.email import email_helpers as web


DISPOSITIONS = ["attachment", "ATTACHMENT", "AtTaChMeNt", "inline", "INLINE", None]


def message(kind, disposition):
    msg = MIMEMultipart("mixed")
    content = "Separate file" if disposition and disposition.lower() == "attachment" else "Inline body"
    part = MIMEText(content if kind == "plain" else f"<p>{content}</p>", kind, "utf-8")
    if disposition:
        # A filename containing 'attachment' must not turn an inline body into a file.
        part.add_header("Content-Disposition", disposition, filename="attachment." + kind)
    msg.attach(part)
    msg.attach(MIMEText("<p>Fallback body</p>", "html", "utf-8"))
    return message_from_bytes(msg.as_bytes())


@pytest.mark.parametrize("extract", [web._extract_text, mcp._extract_text], ids=["web", "mcp"])
@pytest.mark.parametrize("kind", ["plain", "html"])
@pytest.mark.parametrize("disposition", DISPOSITIONS)
def test_body_selection_uses_case_insensitive_disposition_type(extract, kind, disposition):
    attached = disposition and disposition.lower() == "attachment"
    assert extract(message(kind, disposition)) == ("Fallback body" if attached else "Inline body")


@pytest.mark.parametrize("disposition", DISPOSITIONS)
def test_html_selection_ignores_attached_html_in_any_case(disposition):
    attached = disposition and disposition.lower() == "attachment"
    expected = "Fallback body" if attached else "Inline body"
    assert web._extract_html(message("html", disposition)) == f"<p>{expected}</p>"


@pytest.mark.parametrize("module", [web, mcp], ids=["web", "mcp"])
@pytest.mark.parametrize("kind", ["plain", "html"])
@pytest.mark.parametrize("disposition", DISPOSITIONS)
def test_attachment_indices_and_download_bytes_agree(module, kind, disposition, tmp_path):
    msg = message(kind, disposition)
    metadata = module._list_attachments_from_msg(msg)
    if disposition and disposition.lower() == "attachment":
        assert len(metadata) == 1
        assert metadata[0]["index"] == 0
        assert metadata[0]["filename"] == "attachment." + kind
        path = module._extract_attachment_to_disk(msg, 0, tmp_path)
        expected = b"Separate file" if kind == "plain" else b"<p>Separate file</p>"
        assert Path(path).read_bytes() == expected
    else:
        assert metadata == []
        assert module._extract_attachment_to_disk(msg, 0, tmp_path) is None


@pytest.mark.parametrize("disposition", ["attachment", "ATTACHMENT", "inline"])
def test_inline_image_keeps_its_attachment_index_and_content_id(disposition, tmp_path):
    from email.mime.base import MIMEBase

    msg = MIMEMultipart("related")
    msg.attach(MIMEText("<p>Body</p>", "html"))
    image = MIMEBase("image", "png")
    image.set_payload(b"owned image bytes")
    image.add_header("Content-Disposition", disposition, filename="owned.png")
    image["Content-ID"] = "<owned-image>"
    msg.attach(image)
    metadata = web._list_attachments_from_msg(msg)
    assert len(metadata) == 1
    assert metadata[0]["index"] == 0
    assert metadata[0]["content_id"] == "owned-image"
    assert metadata[0]["is_inline"] == (disposition == "inline")
    assert web._extract_attachment_to_disk(msg, 0, tmp_path).read_bytes() == b"owned image bytes"
