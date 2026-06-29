"""Provider endpoint Tailscale URL-resolution tests.

Covers ``resolve_url``: the hop that rewrites an unresolvable hostname to its
Tailscale IP. ROADMAP flags plain-HTTP Tailscale URLs as a self-host trap;
resolve_url is the gate that handles that fallback.
"""
import json
import socket
import types

import pytest

from src import endpoint_resolver as er


# ── resolve_url: Tailscale self-host fallback ──
# ROADMAP flags plain-HTTP Tailscale URLs as a self-host trap; resolve_url is
# the hop that rewrites an unresolvable hostname to its Tailscale IP.

class TestResolveUrlTailscale:
    def setup_method(self):
        # The module memoizes hostname→IP; clear it so cases don't bleed.
        er._tailscale_cache.clear()

    def test_dns_success_returns_url_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            er.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 0))],
        )
        assert er.resolve_url("http://myhost:7000/api") == "http://myhost:7000/api"

    def test_dns_failure_rewrites_to_tailscale_ip(self, monkeypatch):
        def _fail(*a, **k):
            raise socket.gaierror("no DNS")
        monkeypatch.setattr(er.socket, "getaddrinfo", _fail)
        peers = {"Peer": {"x": {
            "HostName": "myhost",
            "DNSName": "myhost.tail.ts.net.",
            "TailscaleIPs": ["100.64.0.5"],
        }}}
        monkeypatch.setattr(
            er.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(peers)),
        )
        # Port is preserved, host swapped for the Tailscale IP.
        assert er.resolve_url("http://myhost:7000/api") == "http://100.64.0.5:7000/api"

    def test_dns_failure_no_peer_match_keeps_url(self, monkeypatch):
        def _fail(*a, **k):
            raise socket.gaierror("no DNS")
        monkeypatch.setattr(er.socket, "getaddrinfo", _fail)
        monkeypatch.setattr(
            er.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps({"Peer": {}})),
        )
        assert er.resolve_url("http://myhost:7000/api") == "http://myhost:7000/api"

    def test_url_without_hostname_is_returned_as_is(self):
        assert er.resolve_url("") == ""
