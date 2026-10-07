"""Follow-up security fixes for the residual Python CodeQL lane (PR #6503).

A. upload-reference scanners stay linear and match the regexes they replaced.
B. /api/hwfit/* (SSH host probing, host path probing) is admin-only.
C. IMAP/SMTP connections honour the outbound address policy, and the account
   connection test never echoes a non-mail service's bytes.
D. attachment extraction directories are per owner/account, not per folder/UID.
"""

import asyncio
import contextlib
import imaplib
import re
import smtplib
import socket
import ssl
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

import pytest

# -- A. upload reference scanners ---------------------------------------------

_HEX = "a" * 32
_PDF_REF = re.compile(
    r"<!--\s*pdf(?:_form)?_source\b[^>]*\bupload_id=[\"']([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)[\"'][^>]*-->",
    re.IGNORECASE,
)
_ATTACHMENT_REF = re.compile(
    r"\[Attachment:[^\]\r\n]*\|\s*id=([0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?)(?:\s*\||\s*\])",
    re.IGNORECASE,
)


@pytest.mark.parametrize("text", [
    f"<!-- pdf_source upload_id='{_HEX}' -->",
    f"<!--PDF_FORM_SOURCE x upload_id=\"{_HEX}.pdf\" y-->",
    f"<!-- pdf_source upload_id='{_HEX}' upload_id='{'b' * 32}' -->",
    f"<!-- pdf_source upload_id='{_HEX}' ->",
    f"<!-- pdf_sourceupload_id='{_HEX}' -->",
    f"<!--pdf_source <!--pdf_source upload_id='{_HEX}'-->",
    "<!-- pdf_source no id -->",
])
def test_pdf_source_ids_match_reference(text):
    from src.upload_handler import _pdf_source_upload_ids

    assert _pdf_source_upload_ids(text) == _PDF_REF.findall(text)


@pytest.mark.parametrize("text", [
    f"[Attachment: report.pdf | id={_HEX} | 12 KB]",
    f"[attachment: a | b | id={_HEX}.txt]",
    f"[Attachment: a | id={_HEX} | id={'b' * 32}]",
    f"[Attachment: x |\nid={_HEX}]",
    f"[Attachment: [Attachment: n | id={_HEX}]",
    f"[Attachment: n | id={_HEX}x]",
    "[Attachment: no id here]",
])
def test_attachment_reference_ids_match_reference(text):
    from src.upload_handler import _attachment_reference_ids

    assert _attachment_reference_ids(text) == _ATTACHMENT_REF.findall(text)


@pytest.mark.parametrize("flood", [
    "[attachment:" * 20_000,                 # CodeQL's reported shape (#615)
    "[Attachment: a |" * 15_000,
    "<!-- pdf_source upload_id=" * 9_000,    # same sink, other pattern
    "<!--pdf_source " + f"upload_id='{_HEX}' " * 5_000 + "-->",
])
def test_internal_upload_id_extraction_is_linear(flood):
    from src.upload_handler import extract_internal_upload_ids

    started = time.perf_counter()
    extract_internal_upload_ids(flood)
    assert time.perf_counter() - started < 2.0


# -- B. hwfit authorization -----------------------------------------------------

def _hwfit_client(monkeypatch, *, auth_disabled=False):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    import core.middleware as middleware
    import services.hwfit.hardware as hardware
    from routes.hwfit_routes import setup_hwfit_routes

    probes = []
    monkeypatch.setattr(middleware, "auth_disabled", lambda: auth_disabled)
    monkeypatch.setattr(
        hardware, "detect_system",
        lambda **kwargs: probes.append(kwargs) or {"backend": "cpu_x86"},
    )
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == "root")

    @app.middleware("http")
    async def _user(request: Request, call_next):
        request.state.current_user = request.headers.get("x-test-user")
        return await call_next(request)

    app.include_router(setup_hwfit_routes())
    return TestClient(app), probes


@pytest.mark.parametrize("path", [
    "/api/hwfit/system?host=10.0.0.5&ssh_port=2222",
    "/api/hwfit/models?host=169.254.169.254",
    "/api/hwfit/profiles?model_path=/etc&host=",
    "/api/hwfit/image-models?host=internal-db",
])
def test_hwfit_rejects_non_admin_before_any_probe(monkeypatch, path):
    client, probes = _hwfit_client(monkeypatch)
    response = client.get(path, headers={"x-test-user": "alice"})
    assert response.status_code == 403
    assert probes == []


def test_hwfit_allows_admin_and_single_user(monkeypatch):
    client, probes = _hwfit_client(monkeypatch)
    assert client.get("/api/hwfit/system", headers={"x-test-user": "root"}).status_code == 200
    client, probes = _hwfit_client(monkeypatch, auth_disabled=True)
    assert client.get("/api/hwfit/system", headers={"x-test-user": "anyone"}).status_code == 200
    assert probes


# -- C. mail server address policy and test-connection errors -------------------

def test_imap_and_smtp_helpers_refuse_metadata_hosts_before_any_socket(monkeypatch):
    """Real helper paths (no stdlib patching): the policy rejects a literal
    metadata address before a socket is even created. Policy matrix and DNS
    rebinding coverage live in test_codeql_python_residual_closure.py."""
    import routes.email.email_helpers as helpers

    monkeypatch.delenv("EMAIL_BLOCK_PRIVATE_IPS", raising=False)
    with pytest.raises(helpers.MailServerAddressBlocked):
        helpers._open_imap_connection("169.254.169.254", 993, starttls=False, owner="alice")
    with pytest.raises(helpers.MailServerAddressBlocked):
        helpers._send_smtp_message(
            {"smtp_host": "169.254.169.254", "smtp_port": 587, "owner": "alice"}, "a@b", ["c@d"], "x",
        )


@pytest.mark.parametrize("error,expected,leak", [
    (imaplib.IMAP4.abort("unexpected response: b'SSH-2.0-OpenSSH_9.6 internal-db'"),
     "IMAP server did not respond like an IMAP server", "OpenSSH"),
    (smtplib.SMTPConnectError(554, b"-ERR redis 7.2 internal-cache"),
     "SMTP server did not respond like an SMTP server", "redis"),
    (smtplib.SMTPServerDisconnected("Connection unexpectedly closed: HTTP/1.1 400"),
     "SMTP server did not respond like an SMTP server", "HTTP/1.1"),
    (ConnectionRefusedError(111, "Connection refused 10.0.0.5:6379"), "IMAP connection refused", "6379"),
    (socket.timeout("timed out"), "IMAP connection timed out", "10.0"),
    (ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] banner: nginx"),
     "IMAP TLS handshake failed; check the port and security setting", "nginx"),
])
def test_connection_test_errors_never_echo_peer_bytes(error, expected, leak):
    from routes.email.email_helpers import _mail_connection_test_error

    protocol = expected.split()[0]
    message = _mail_connection_test_error(protocol, "10.0.0.5", error)
    assert message == expected
    assert leak not in message


def test_connection_test_keeps_mail_server_auth_responses():
    from routes.email.email_helpers import _mail_connection_test_error

    assert _mail_connection_test_error(
        "IMAP", "imap.example.com", imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials"),
    ) == "[AUTHENTICATIONFAILED] Invalid credentials"
    microsoft = _mail_connection_test_error(
        "SMTP", "smtp.office365.com",
        smtplib.SMTPAuthenticationError(535, b"5.7.139 Authentication unsuccessful"),
    )
    assert "Microsoft no longer accepts" in microsoft


def test_account_connection_test_route_applies_policy_to_both_protocols(monkeypatch):
    import routes.email_routes as email_routes

    monkeypatch.delenv("EMAIL_BLOCK_PRIVATE_IPS", raising=False)
    router = email_routes.setup_email_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if r.path == "/api/email/accounts/test" and "POST" in getattr(r, "methods", set())
    )

    class _Request:
        async def json(self):
            return {
                "imap_host": "169.254.169.254", "imap_port": 993, "imap_user": "u", "imap_password": "p",
                "smtp_host": "169.254.169.254", "smtp_port": 587,
            }

    result = asyncio.run(endpoint(req=_Request(), owner="alice"))
    policy = "server address is not allowed by this server's network policy"
    assert result["ok"] is False
    assert result["imap"]["error"] == f"IMAP {policy}"
    assert result["smtp"]["error"] == f"SMTP {policy}"


# -- D. per-owner/account attachment extraction -------------------------------

def _message_with_attachment(body: bytes) -> bytes:
    msg = MIMEMultipart()
    msg.attach(MIMEText("see attached", "plain"))
    part = MIMEApplication(body, Name="invoice.pdf")
    part["Content-Disposition"] = 'attachment; filename="invoice.pdf"'
    msg.attach(part)
    return msg.as_bytes()


def test_attachment_extract_dir_separates_owners_and_accounts(tmp_path, monkeypatch):
    import routes.email.email_helpers as helpers

    monkeypatch.setattr(helpers, "ATTACHMENTS_DIR", tmp_path)
    alice = helpers.attachment_extract_dir("INBOX", "42", owner="alice", account_id="a1")
    assert alice != helpers.attachment_extract_dir("INBOX", "42", owner="bob", account_id="a1")
    assert alice != helpers.attachment_extract_dir("INBOX", "42", owner="alice", account_id="a2")
    assert alice != helpers.attachment_extract_dir("INBOX/42", "", owner="alice", account_id="a1")
    assert alice.parent == tmp_path.resolve()


def test_two_users_same_folder_uid_cannot_overwrite_each_others_attachment(tmp_path, monkeypatch):
    """Two principals, same INBOX UID 42, same attachment filename.

    Before the fix both extractions landed in ATTACHMENTS_DIR/INBOX_42/, so
    bob's request rewrote the file whose path alice's agent was about to read.
    """
    import routes.email.email_helpers as helpers
    import routes.email_routes as email_routes

    monkeypatch.setattr(helpers, "ATTACHMENTS_DIR", tmp_path)
    mailboxes = {
        "alice": _message_with_attachment(b"%PDF alice private invoice"),
        "bob": _message_with_attachment(b"%PDF bob controlled content"),
    }

    @contextlib.contextmanager
    def _fake_imap(account_id=None, owner=""):
        yield SimpleNamespace(owner=owner, select=lambda *a, **k: ("OK", [b"1"]))

    monkeypatch.setattr(email_routes, "_imap", _fake_imap)
    monkeypatch.setattr(
        email_routes, "_imap_uid_fetch",
        lambda conn, uid, query: ("OK", [(b"42 (RFC822)", mailboxes[conn.owner])]),
    )
    router = email_routes.setup_email_routes()
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/email/attachment-path/{uid}/{index}")

    alice = asyncio.run(endpoint(uid="42", index=0, folder="INBOX", account_id=None, owner="alice"))
    bob = asyncio.run(endpoint(uid="42", index=0, folder="INBOX", account_id=None, owner="bob"))

    assert alice["filename"] == bob["filename"] == "invoice.pdf"
    assert alice["path"] != bob["path"]
    with open(alice["path"], "rb") as handle:
        assert handle.read() == b"%PDF alice private invoice"
