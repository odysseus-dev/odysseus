"""Regression: webhook delivery must pin the TCP connect to the SSRF-approved IP.

validate_webhook_url resolves the host to accept/reject, but the delivery
connect previously re-resolved independently — a DNS record flipping between
the two lookups (rebinding) could slip an internal IP past the check. _deliver
now resolves+validates once via _validated_public_ips and pins the connect to
that IP through _PinnedAsyncTransport. These tests drive the real transport
against local servers so the pin is exercised end-to-end, not mocked away.
"""
import asyncio
import http.server
import ipaddress
import socketserver
import threading

import pytest

from tests.helpers.import_state import clear_module, preserve_import_state

import os
import sys
from unittest.mock import patch

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}), \
        preserve_import_state("src.database", "core.database"):
    clear_module("src.database")
    _core_database = sys.modules.get("core.database")
    if _core_database is not None and not getattr(_core_database, "__file__", None):
        del sys.modules["core.database"]
    import src.webhook_manager as wm


# ---------------------------------------------------------------------------
# _validated_public_ips
# ---------------------------------------------------------------------------

def test_validated_public_ips_rejects_metadata_literal():
    with pytest.raises(ValueError):
        wm._validated_public_ips("http://169.254.169.254/")


def test_validated_public_ips_rejects_loopback_literal():
    with pytest.raises(ValueError):
        wm._validated_public_ips("http://127.0.0.1/")


def test_validated_public_ips_returns_public_literal():
    ips = wm._validated_public_ips("http://93.184.216.34/")
    assert ips == [ipaddress.ip_address("93.184.216.34")]


def test_validated_public_ips_rejects_hostname_resolving_private(monkeypatch):
    # Rebinding shape: a hostname that (now) resolves into loopback space.
    monkeypatch.setattr(wm, "_resolve_hostname_ips",
                        lambda h: [ipaddress.ip_address("127.0.0.1")])
    with pytest.raises(ValueError):
        wm._validated_public_ips("http://evil.rebind.example/")


# ---------------------------------------------------------------------------
# End-to-end: the pinned transport actually routes to the pinned IP
# ---------------------------------------------------------------------------

def _serve(handler):
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def test_pinned_transport_connects_to_pinned_ip():
    """A request whose URL host is a throwaway hostname is still delivered to
    the pinned loopback IP — proving the socket destination comes from the pin,
    not from resolving the URL host."""
    hits = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            hits.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv, port = _serve(_Handler)
    try:
        ip = ipaddress.ip_address("127.0.0.1")
        transport = wm._PinnedAsyncTransport(ip)

        async def go():
            async with __import__("httpx").AsyncClient(
                transport=transport, timeout=5, follow_redirects=False,
            ) as client:
                # Host "unresolvable.invalid" would never resolve; the pin is
                # what makes this reach the loopback server on `port`.
                return await client.post(
                    f"http://unresolvable.invalid:{port}/hook", content=b"{}",
                )

        resp = asyncio.run(go())
        assert resp.status_code == 204
        assert hits == ["/hook"]
    finally:
        srv.shutdown()


def test_deliver_pins_to_validated_ip_end_to_end(monkeypatch):
    """Full _deliver path: a hostname that validation resolves to loopback is
    pinned to loopback and the local server receives the signed POST."""
    received = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            received["body"] = self.rfile.read(length)
            received["event"] = self.headers.get("X-Odysseus-Event")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv, port = _serve(_Handler)

    class _Query:
        def filter(self, *a, **k): return self
        def update(self, values): return None

    class _Db:
        def query(self, _m): return _Query()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    # Make both the validation resolve and the pin target loopback, and treat
    # loopback as allowed for this test (production blocks it — here we only
    # want to prove the pin routes to the validated IP).
    monkeypatch.setattr(wm, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(wm, "_is_private_url", lambda url: False)
    monkeypatch.setattr(wm, "_resolve_hostname_ips",
                        lambda h: [ipaddress.ip_address("127.0.0.1")])
    monkeypatch.setattr(wm, "_ip_is_private", lambda a: False)

    manager = wm.WebhookManager()
    try:
        asyncio.run(manager._deliver(
            "hook-1", f"http://webhook.test:{port}/cb", "s3cret",
            "webhook.test", {"ok": True},
        ))
        assert received.get("event") == "webhook.test"
        assert b'"ok": true' in received["body"]
    finally:
        srv.shutdown()
