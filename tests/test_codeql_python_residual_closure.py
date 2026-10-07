"""Final closure tests for the residual Python CodeQL lane (PR #6503).

1. MCP attachment extraction: no traversal, per owner/account directories.
2. Mail DNS rebinding: one resolution, connect only to the checked address,
   TLS SNI/verification still against the requested hostname.
3. /accounts/test runs its blocking mail I/O off the event loop.
4. /search/web works under the production CSP and payloads cannot execute.
5. Outbound mail address policy matrix.
"""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from types import SimpleNamespace

import pytest


# -- 1. MCP attachment extraction -------------------------------------------

def _message_with_attachment(body: bytes) -> bytes:
    msg = MIMEMultipart()
    part = MIMEApplication(body, Name="invoice.pdf")
    part["Content-Disposition"] = 'attachment; filename="invoice.pdf"'
    msg.attach(part)
    return msg.as_bytes()


@pytest.fixture
def mcp_mail(tmp_path, monkeypatch):
    import mcp_servers.email_server as es

    root = tmp_path / "mail-attachments"
    root.mkdir()
    monkeypatch.setattr(es, "MAIL_ATTACHMENTS_DIR", str(root))
    monkeypatch.setattr(es, "_fixture_attachment_source", lambda *a, **k: None)
    monkeypatch.setattr(es, "_load_config", lambda account=None: {"account_id": account})
    box = {"raw": _message_with_attachment(b"%PDF one")}

    class _Conn:
        def select(self, *a, **k):
            return ("OK", [b"1"])

        def uid(self, *a):
            return ("OK", [(b"42 (BODY[])", box["raw"])])

        def logout(self):
            pass

    monkeypatch.setattr(es, "_imap_connect", lambda account=None: _Conn())
    return es, root.resolve(), box


@pytest.mark.parametrize("folder,uid", [
    ("/tmp/odysseus-escape", "42"),          # absolute mailbox name replaced the root
    ("../../escape", "42"),                  # relative traversal
    ("INBOX/../../../escape", "42"),          # hierarchy delimiter + traversal
    ("..", ".."),
    ("INBOX", "../../42"),
    ("INBOX/Receipts", "42"),                 # legitimate hierarchical folder
])
def test_mcp_download_attachment_stays_in_one_root_segment(mcp_mail, folder, uid):
    es, root, _box = mcp_mail
    result = es._download_attachment(uid, 0, folder)
    path = Path(result["path"]).resolve()
    assert path.parent.parent == root, path
    assert path.read_bytes() == b"%PDF one"


def test_mcp_download_attachment_isolates_owners_and_accounts(mcp_mail):
    es, _root, box = mcp_mail
    paths = {}
    for owner, account, body in (
        ("alice", "acct-a", b"%PDF alice"),
        ("bob", "acct-a", b"%PDF bob"),
        ("alice", "acct-b", b"%PDF alice second mailbox"),
    ):
        token = es._CURRENT_OWNER.set(owner)
        try:
            box["raw"] = _message_with_attachment(body)
            paths[(owner, account)] = (es._download_attachment("42", 0, "INBOX", account=account)["path"], body)
        finally:
            es._CURRENT_OWNER.reset(token)
    assert len({p for p, _ in paths.values()}) == 3
    for path, body in paths.values():
        assert Path(path).read_bytes() == body


def test_attachment_scope_dir_rejects_symlinked_scope(tmp_path):
    from src.mail_attachment_paths import attachment_scope_dir

    root = tmp_path / "root"
    root.mkdir()
    target = attachment_scope_dir(root, "INBOX", "1", owner="o", account_id="a")
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        attachment_scope_dir(root, "INBOX", "1", owner="o", account_id="a")


def test_attachment_scope_dir_normalises_uid_type(tmp_path):
    from src.mail_attachment_paths import attachment_scope_dir

    assert attachment_scope_dir(tmp_path, "INBOX", 42, owner="o", account_id=None) == \
        attachment_scope_dir(tmp_path, "INBOX", "42", owner="o", account_id="")


# -- 2. DNS rebinding: resolve once, connect to the checked address ------------

class _LineServer:
    """Tiny loopback server speaking just enough IMAP or SMTP for a handshake."""

    def __init__(self, protocol):
        self.protocol = protocol
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn, conn.makefile("rwb") as io:
            io.write(b"* OK ready\r\n" if self.protocol == "imap" else b"220 fake ESMTP\r\n")
            io.flush()
            for raw in io:
                line = raw.decode().strip()
                if self.protocol == "imap":
                    tag, _, command = line.partition(" ")
                    if command.upper().startswith("CAPABILITY"):
                        io.write(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n".encode())
                    else:
                        io.write(f"* BYE\r\n{tag} OK bye\r\n".encode())
                        io.flush()
                        return
                elif line.upper().startswith(("EHLO", "HELO")):
                    io.write(b"250 fake\r\n")
                else:
                    io.write(b"221 bye\r\n")
                    io.flush()
                    return
                io.flush()

    def close(self):
        self.sock.close()


@pytest.fixture
def rebinding_dns(monkeypatch):
    """`rebind.test` answers loopback once, then the metadata address."""
    real = socket.getaddrinfo
    lookups = []

    def _resolve(host, port, *args, **kwargs):
        if host == "rebind.test":
            lookups.append(host)
            ip = "127.0.0.1" if len(lookups) == 1 else "169.254.169.254"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    return lookups


class _RecordingTLS:
    """Stands in for an SSLContext: records the SNI name, keeps the socket plain."""

    def __init__(self):
        self.server_hostname = None

    def wrap_socket(self, sock, server_hostname=None, **_kwargs):
        self.server_hostname = server_hostname
        return sock


@pytest.mark.parametrize("tls", [False, True])
def test_imap_connects_to_the_checked_address_only(rebinding_dns, tls):
    from routes.email.email_helpers import _PolicyIMAP4, _PolicyIMAP4_SSL

    server = _LineServer("imap")
    try:
        if tls:
            context = _RecordingTLS()
            conn = _PolicyIMAP4_SSL("rebind.test", server.port, block_private=False, timeout=5, ssl_context=context)
            assert context.server_hostname == "rebind.test"
        else:
            conn = _PolicyIMAP4("rebind.test", server.port, block_private=False, timeout=5)
        assert conn.sock.getpeername()[0] == "127.0.0.1"
        conn.logout()
    finally:
        server.close()
    assert rebinding_dns == ["rebind.test"]  # a second lookup would have answered 169.254.169.254


@pytest.mark.parametrize("tls", [False, True])
def test_smtp_connects_to_the_checked_address_only(rebinding_dns, tls):
    from routes.email.email_helpers import _PolicySMTP, _PolicySMTP_SSL

    server = _LineServer("smtp")
    try:
        if tls:
            context = _RecordingTLS()
            smtp = _PolicySMTP_SSL("rebind.test", server.port, block_private=False, timeout=5, context=context)
            assert context.server_hostname == "rebind.test"
        else:
            smtp = _PolicySMTP("rebind.test", server.port, block_private=False, timeout=5)
        assert smtp.sock.getpeername()[0] == "127.0.0.1"
        assert smtp.ehlo()[0] == 250
        smtp.quit()
    finally:
        server.close()
    assert rebinding_dns == ["rebind.test"]


@pytest.mark.parametrize("protocol", ["imap", "smtp"])
@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("ipv6_first", [False, True])
def test_mail_connects_to_dual_stack_localhost(monkeypatch, protocol, tls, ipv6_first):
    from routes.email.email_helpers import (
        _PolicyIMAP4, _PolicyIMAP4_SSL, _PolicySMTP, _PolicySMTP_SSL,
    )

    real = socket.getaddrinfo
    lookups = []
    ips = ["::1", "127.0.0.1"] if ipv6_first else ["127.0.0.1", "::1"]

    def resolve(host, port, *args, **kwargs):
        if host != "localhost":
            return real(host, port, *args, **kwargs)
        lookups.append(host)
        assert lookups == ["localhost"]
        return [info for ip in ips for info in real(ip, port, *args, **kwargs)]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    server = _LineServer(protocol)
    context = _RecordingTLS()
    try:
        if protocol == "imap":
            cls = _PolicyIMAP4_SSL if tls else _PolicyIMAP4
            kwargs = {"ssl_context": context} if tls else {}
        else:
            cls = _PolicySMTP_SSL if tls else _PolicySMTP
            kwargs = {"context": context} if tls else {}
        conn = cls("localhost", server.port, block_private=False, timeout=5, **kwargs)
        try:
            assert conn.sock.getpeername()[0] == "127.0.0.1"
            if tls:
                assert context.server_hostname == "localhost"
            if protocol == "smtp":
                assert conn.ehlo()[0] == 250
        finally:
            if protocol == "imap":
                conn.logout()
            else:
                conn.quit()
    finally:
        server.close()
    assert lookups == ["localhost"]


def test_any_denied_answer_in_a_mixed_resolution_blocks_the_connection():
    from src.url_safety import OutboundAddressBlocked, connect_outbound_tcp

    def _mixed(host, port, *args):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
        ]

    with pytest.raises(OutboundAddressBlocked):
        connect_outbound_tcp("mixed.test", 993, block_private=False, resolver=_mixed)


# -- 5. Address/principal policy matrix ----------------------------------------

_ALWAYS_DENIED = [
    "169.254.169.254", "fe80::1", "0.0.0.0", "::", "224.0.0.1", "240.0.0.1",
    "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "64:ff9b::a00:5",
    "ff02::1", "::2", "100::1", "::ffff:0.0.0.0", "::ffff:224.0.0.1",
    "::ffff:240.0.0.1", "64:ff9b::7f00:1", "64:ff9b::6440:1",
    "64:ff9b::f000:1", "64:ff9b:1::7f00:1",
]
_PRIVATE = ["127.0.0.1", "::1", "::ffff:127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.10", "100.64.0.1", "fd00::1"]
_PUBLIC = ["93.184.216.34", "2606:2800:220:1::1"]


@pytest.fixture
def fake_sockets(monkeypatch):
    import src.url_safety as url_safety

    connected = []

    class _Sock:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def bind(self, address):
            pass

        def connect(self, sockaddr):
            connected.append(sockaddr[0])

        def close(self):
            pass

    monkeypatch.setattr(url_safety.socket, "socket", _Sock)
    return connected


def _answer(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return lambda host, port, *args: [(family, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.mark.parametrize("block_private", [False, True])
@pytest.mark.parametrize("ip", _ALWAYS_DENIED)
def test_metadata_and_special_ranges_are_always_denied(fake_sockets, ip, block_private):
    from src.url_safety import OutboundAddressBlocked, connect_outbound_tcp

    with pytest.raises(OutboundAddressBlocked):
        connect_outbound_tcp("h", 993, block_private=block_private, resolver=_answer(ip))
    assert fake_sockets == []


@pytest.mark.parametrize("ip", _PRIVATE)
def test_private_ranges_follow_the_principal_policy(fake_sockets, ip):
    from src.url_safety import OutboundAddressBlocked, connect_outbound_tcp

    connect_outbound_tcp("h", 993, block_private=False, resolver=_answer(ip))
    assert fake_sockets == [ip]
    with pytest.raises(OutboundAddressBlocked):
        connect_outbound_tcp("h", 993, block_private=True, resolver=_answer(ip))
    assert fake_sockets == [ip]


@pytest.mark.parametrize("block_private", [False, True])
@pytest.mark.parametrize("ipv6_first", [False, True])
def test_dual_stack_localhost_tcp_resolves_once(fake_sockets, block_private, ipv6_first):
    from src.url_safety import OutboundAddressBlocked, connect_outbound_tcp

    lookups = []
    ips = ["::1", "127.0.0.1"] if ipv6_first else ["127.0.0.1", "::1"]

    def resolve(host, port, *args):
        lookups.append(host)
        assert lookups == ["localhost"]
        return [info for ip in ips for info in _answer(ip)(host, port, *args)]

    if block_private:
        with pytest.raises(OutboundAddressBlocked, match="loopback address blocked"):
            connect_outbound_tcp("localhost", 993, block_private=True, resolver=resolve)
        assert fake_sockets == []
    else:
        sock = connect_outbound_tcp("localhost", 993, block_private=False, resolver=resolve)
        sock.close()
        assert fake_sockets == [ips[0]]
    assert lookups == ["localhost"]


@pytest.mark.parametrize("ip", _PUBLIC)
def test_public_mail_servers_are_always_allowed(fake_sockets, ip):
    from src.url_safety import connect_outbound_tcp

    connect_outbound_tcp("h", 993, block_private=True, resolver=_answer(ip))
    assert fake_sockets == [ip]


@pytest.mark.parametrize("trusted,env,owner,blocked", [
    (False, {}, "bob", True),                                   # multi-user, non-admin: denied
    (False, {}, "", True),                                      # multi-user, no principal: denied
    (True, {}, "admin", False),                                 # admin (or single-user mode)
    (False, {"EMAIL_ALLOW_PRIVATE_IPS": "true"}, "bob", False),  # operator opt-in for LAN mail
    (True, {"EMAIL_BLOCK_PRIVATE_IPS": "true"}, "admin", True),  # operator lockdown wins
    (False, {"EMAIL_ALLOW_PRIVATE_IPS": "true", "EMAIL_BLOCK_PRIVATE_IPS": "true"}, "bob", True),
])
def test_private_mail_destination_principal_matrix(monkeypatch, trusted, env, owner, blocked):
    import src.tool_security as tool_security
    from routes.email.email_helpers import _mail_private_blocked

    for key in ("EMAIL_ALLOW_PRIVATE_IPS", "EMAIL_BLOCK_PRIVATE_IPS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda o: trusted)
    assert _mail_private_blocked(owner) is blocked


def test_single_user_mode_allows_lan_mail_servers(monkeypatch):
    from routes.email.email_helpers import _mail_private_blocked

    for key in ("EMAIL_ALLOW_PRIVATE_IPS", "EMAIL_BLOCK_PRIVATE_IPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert _mail_private_blocked("") is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_non_admin_cannot_probe_loopback_through_the_connection_test(monkeypatch, host):
    import routes.email_routes as email_routes
    import src.tool_security as tool_security

    for key in ("EMAIL_ALLOW_PRIVATE_IPS", "EMAIL_BLOCK_PRIVATE_IPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda o: False)
    endpoint = _accounts_test_endpoint(email_routes)

    class _Request:
        async def json(self):
            return {"imap_host": host, "imap_port": 6379, "imap_user": "u", "imap_password": "p",
                    "smtp_host": "10.0.0.5", "smtp_port": 25}

    result = asyncio.run(endpoint(req=_Request(), owner="bob"))
    assert result["imap"]["error"] == "IMAP server address is not allowed by this server's network policy"
    assert result["smtp"]["error"] == "SMTP server address is not allowed by this server's network policy"


# -- 3. /accounts/test blocking I/O stays off the event loop -----------------------

def _accounts_test_endpoint(email_routes):
    router = email_routes.setup_email_routes()
    return next(
        r.endpoint for r in router.routes
        if r.path == "/api/email/accounts/test" and "POST" in getattr(r, "methods", set())
    )


def test_connection_test_does_not_block_the_event_loop(monkeypatch):
    import routes.email_routes as email_routes

    def _slow_open(host, port, **kwargs):
        time.sleep(1.0)  # a mail server that never answers within the timeout
        raise socket.timeout("timed out")

    monkeypatch.setattr(email_routes, "_open_imap_connection", _slow_open)
    endpoint = _accounts_test_endpoint(email_routes)

    class _Request:
        async def json(self):
            return {"imap_host": "imap.example.com", "imap_port": 993, "imap_user": "u", "imap_password": "p"}

    async def _main():
        gaps = []
        done = asyncio.Event()

        async def _heartbeat():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        beat = asyncio.create_task(_heartbeat())
        started = time.perf_counter()
        results = await asyncio.gather(*(endpoint(req=_Request(), owner="alice") for _ in range(3)))
        elapsed = time.perf_counter() - started
        done.set()
        await beat
        return results, max(gaps), elapsed

    results, worst_gap, elapsed = asyncio.run(_main())
    assert worst_gap < 0.5, f"event loop stalled for {worst_gap:.2f}s"
    assert elapsed < 2.5  # three 1s probes overlapped instead of serialising on the loop
    for result in results:
        assert result == {"ok": False, "imap": {"ok": False, "error": "IMAP connection timed out"}, "smtp": None}


def test_connection_test_keeps_its_authentication_dependency():
    import routes.email_routes as email_routes
    from routes.email.email_routes import require_user

    router = email_routes.setup_email_routes()
    route = next(
        r for r in router.routes
        if r.path == "/api/email/accounts/test" and "POST" in getattr(r, "methods", set())
    )
    assert any(dep.call is require_user for dep in route.dependant.dependencies)


# -- 4. /search/web under the production CSP ---------------------------------------

_PAYLOAD = (
    "</script><script>document.title='PWNED'</script>"
    "<img src=x onerror=\"document.body.dataset.pwned='1'\">"
)
_SOURCES = [
    {"title": "Example result", "url": "https://example.com/a", "snippet": "first"},
    {"title": "Script link", "url": "javascript:document.body.dataset.pwned='2'", "snippet": "second"},
]


def _csp_app():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from core.middleware import SecurityHeadersMiddleware
    from routes.search.search_routes import setup_search_routes

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/api/search")
    async def _stub_search():
        return {"sources": _SOURCES}

    @app.get("/csp-control")
    async def _control():
        return HTMLResponse("<html><body><script>document.body.dataset.ran='1'</script></body></html>")

    app.include_router(setup_search_routes(SimpleNamespace()))
    return app


@pytest.mark.parametrize("q", ["weather in lisbon", _PAYLOAD])
def test_search_page_script_carries_the_response_csp_nonce(q):
    from fastapi.testclient import TestClient

    response = TestClient(_csp_app()).get("/search/web", params={"q": q})
    csp = response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    scripts = re.findall(r"<script\b[^>]*>", response.text, re.I)
    assert scripts == [f'<script nonce="{nonce}">']
    markup = response.text.split("<script", 1)[0]
    assert "<img" not in markup.lower()  # the payload is only ever present HTML-escaped


def _chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


@pytest.fixture
def live_csp_server():
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_csp_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _render(chrome, url, profile):
    out = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
         f"--user-data-dir={profile}", "--virtual-time-budget=5000", "--dump-dom", url],
        capture_output=True, text=True, timeout=90,
    )
    return out.stdout


@pytest.mark.skipif(_chrome() is None, reason="needs a headless Chrome/Chromium")
def test_search_page_runs_under_production_csp_and_payload_does_not(live_csp_server, tmp_path):
    chrome = _chrome()
    # Control: the production CSP really blocks nonce-less inline script.
    control = _render(chrome, f"{live_csp_server}/csp-control", tmp_path / "p0")
    assert "<body>" in control and "data-ran" not in control

    normal = _render(chrome, f"{live_csp_server}/search/web?q=weather", tmp_path / "p1")
    assert "2 results" in normal                                   # the page's script executed
    assert 'href="https://example.com/a"' in normal
    assert "javascript:" not in normal.split('id="results"', 1)[1]  # non-http result URL not linked

    hostile = _render(
        chrome, f"{live_csp_server}/search/web?q={urllib.parse.quote(_PAYLOAD)}", tmp_path / "p2",
    )
    assert "2 results" in hostile                                  # search still ran with that query
    assert hostile.split("<title>", 1)[1].split("</title>", 1)[0] != "PWNED"  # escaped text, not run
    assert "data-pwned" not in hostile
