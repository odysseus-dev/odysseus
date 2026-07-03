import pytest
import email
from email.message import EmailMessage
from unittest.mock import MagicMock

pytest.importorskip("mcp")

import mcp_servers.email_server as es


class FakeIMAPConnection:
    def __init__(self, raw_message_bytes):
        self.raw_message_bytes = raw_message_bytes
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "FETCH":
            # fetch returns (status, [ (envelope, body_bytes), ... ])
            return "OK", [(b"1 (UID 123)", self.raw_message_bytes)]
        return "OK", []

    def logout(self):
        self.calls.append(("logout",))


def test_read_email_attachment_plain_text(monkeypatch):
    # Create email with a plain text attachment
    msg = EmailMessage()
    msg["Subject"] = "Test Email"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg.set_content("This is the email body.")
    msg.add_attachment(b"Hello plain text attachment content", maintype="text", subtype="plain", filename="sample.txt")

    raw_bytes = msg.as_bytes()
    fake_conn = FakeIMAPConnection(raw_bytes)

    monkeypatch.setattr(es, "_imap_connect", lambda account: fake_conn)
    monkeypatch.setattr(es, "_load_config", lambda account: {})

    result = es._read_email_attachment(uid="123", index=0)
    assert "error" not in result
    assert result["filename"] == "sample.txt"
    assert result["content_type"] == "text/plain"
    assert result["content"] == "Hello plain text attachment content"
    assert fake_conn.calls[-1] == ("logout",)


def test_read_email_attachment_html(monkeypatch):
    msg = EmailMessage()
    msg["Subject"] = "Test Email"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg.set_content("This is the email body.")
    msg.add_attachment(
        b"<html><body><h1>Hello HTML Header</h1><p>Body paragraph.</p></body></html>",
        maintype="text",
        subtype="html",
        filename="sample.html"
    )

    raw_bytes = msg.as_bytes()
    fake_conn = FakeIMAPConnection(raw_bytes)

    monkeypatch.setattr(es, "_imap_connect", lambda account: fake_conn)
    monkeypatch.setattr(es, "_load_config", lambda account: {})

    result = es._read_email_attachment(uid="123", index=0)
    assert "error" not in result
    assert result["filename"] == "sample.html"
    assert "Hello HTML Header" in result["content"]
    assert "Body paragraph." in result["content"]


def test_read_email_attachment_pdf(monkeypatch):
    msg = EmailMessage()
    msg["Subject"] = "Test Email"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg.set_content("This is the email body.")
    msg.add_attachment(b"fake pdf content", maintype="application", subtype="pdf", filename="sample.pdf")

    raw_bytes = msg.as_bytes()
    fake_conn = FakeIMAPConnection(raw_bytes)

    monkeypatch.setattr(es, "_imap_connect", lambda account: fake_conn)
    monkeypatch.setattr(es, "_load_config", lambda account: {})

    # Mock pypdf PdfReader
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Hello PDF text content"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    # Patch PdfReader in mcp_servers.email_server
    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: mock_reader)

    result = es._read_email_attachment(uid="123", index=0)
    assert "error" not in result
    assert result["filename"] == "sample.pdf"
    assert result["content_type"] == "application/pdf"
    assert "Hello PDF text content" in result["content"]


def test_read_email_attachment_unsupported(monkeypatch):
    msg = EmailMessage()
    msg["Subject"] = "Test Email"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg.set_content("This is the email body.")
    msg.add_attachment(b"fake png bytes", maintype="image", subtype="png", filename="sample.png")

    raw_bytes = msg.as_bytes()
    fake_conn = FakeIMAPConnection(raw_bytes)

    monkeypatch.setattr(es, "_imap_connect", lambda account: fake_conn)
    monkeypatch.setattr(es, "_load_config", lambda account: {})

    result = es._read_email_attachment(uid="123", index=0)
    assert "error" in result
    assert "Unsupported attachment format" in result["error"]
