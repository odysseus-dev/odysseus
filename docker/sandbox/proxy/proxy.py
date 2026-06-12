#!/usr/bin/env python3
"""
proxy.py — Tier-C egress allowlist proxy.

OPT-IN. The Tier-B sandbox runs with network_mode:none by default (no egress).
When a workflow genuinely needs the internet from sandboxed code (e.g. `pip
install`), the egress overlay puts the sandbox on an INTERNAL network (no direct
route out) together with this proxy, which is the only door to the internet and
only opens it for an explicit host allowlist. Default-deny: anything not on the
list is refused. See docs/security/bash-python-sandbox-scoping.md (Tier C).

Self-contained (stdlib only). Handles HTTPS via CONNECT (what pip/https use);
plain-HTTP absolute-URI requests and any other method are refused.

Config (env):
  PROXY_PORT       listen port (default 8888)
  PROXY_ALLOWLIST  comma-separated host suffixes, e.g.
                   "pypi.org,files.pythonhosted.org,github.com"
                   A target matches if it equals an entry or ends with "."+entry.
"""
from __future__ import annotations

import os
import select
import socket
import threading

PORT = int(os.environ.get("PROXY_PORT", "8888"))
_ALLOW = tuple(
    h.strip().lower() for h in os.environ.get("PROXY_ALLOWLIST", "").split(",") if h.strip()
)
_CONNECT_TIMEOUT = 10
_IDLE_TIMEOUT = 120


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    for entry in _ALLOW:
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _send(conn: socket.socket, status: str) -> None:
    try:
        conn.sendall(f"HTTP/1.1 {status}\r\n\r\n".encode())
    except OSError:
        pass


def _pipe(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, _IDLE_TIMEOUT)
            if x or not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass


def _handle(conn: socket.socket) -> None:
    conn.settimeout(_CONNECT_TIMEOUT)
    upstream = None
    try:
        # Read just the request head (up to the blank line).
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 64 * 1024:
                _send(conn, "400 Bad Request")
                return

        request_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = request_line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            # Only HTTPS-via-CONNECT is supported; everything else is refused.
            _send(conn, "405 Method Not Allowed")
            return

        hostport = parts[1]
        host, _, port_s = hostport.partition(":")
        try:
            port = int(port_s or "443")
        except ValueError:
            _send(conn, "400 Bad Request")
            return

        if not _host_allowed(host):
            _send(conn, "403 Forbidden")
            print(f"[egress-proxy] DENY {host}:{port}", flush=True)
            return

        try:
            upstream = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        except OSError as e:
            _send(conn, "502 Bad Gateway")
            print(f"[egress-proxy] FAIL {host}:{port} ({e})", flush=True)
            return

        _send(conn, "200 Connection established")
        print(f"[egress-proxy] ALLOW {host}:{port}", flush=True)
        conn.settimeout(None)
        upstream.settimeout(None)
        _pipe(conn, upstream)
    except OSError:
        pass
    finally:
        for s in (conn, upstream):
            try:
                if s:
                    s.close()
            except OSError:
                pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(64)
    print(f"[egress-proxy] listening on :{PORT} allowlist={list(_ALLOW) or 'EMPTY (deny-all)'}",
          flush=True)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
