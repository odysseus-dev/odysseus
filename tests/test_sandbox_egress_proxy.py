"""Tier-C egress proxy: allowlist enforcement (default-deny).

Launches docker/sandbox/proxy/proxy.py locally and drives it with raw CONNECT
requests: an allowed host tunnels (200), a disallowed host is refused (403), and
non-CONNECT methods are rejected (405). No Docker required — the network topology
(internal net forcing traffic through the proxy) is declarative in the egress
overlay and checked manually; this pins the allowlist logic that gates it.
"""
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

_PROXY = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                      "docker", "sandbox", "proxy", "proxy.py")

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX sockets")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def echo_server():
    """An 'allowed' upstream: a local TCP echo server on 127.0.0.1."""
    port = _free_port()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    stop = threading.Event()

    def serve():
        srv.settimeout(0.3)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (socket.timeout, OSError):
                continue
            data = c.recv(1024)
            if data:
                c.sendall(data)
            c.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield port
    stop.set()
    srv.close()


@pytest.fixture()
def proxy():
    port = _free_port()
    env = {**os.environ, "PROXY_PORT": str(port), "PROXY_ALLOWLIST": "127.0.0.1,pypi.org"}
    proc = subprocess.Popen([sys.executable, _PROXY], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Wait for the listener.
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read().decode())
            time.sleep(0.05)
    else:
        proc.terminate()
        raise RuntimeError("proxy never listened")
    yield port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _connect_request(proxy_port: int, target: str) -> str:
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=3)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    resp = s.recv(256).decode("latin-1", "replace")
    return s, resp.split("\r\n", 1)[0]


def test_allowed_host_tunnels(proxy, echo_server):
    s, status = _connect_request(proxy, f"127.0.0.1:{echo_server}")
    assert "200" in status, status
    # The tunnel is live: bytes echo back through it.
    s.sendall(b"ping")
    assert s.recv(16) == b"ping"
    s.close()


def test_disallowed_host_refused(proxy):
    s, status = _connect_request(proxy, "evil.example.com:443")
    assert "403" in status, status
    s.close()


def test_suffix_match_allows_subdomain(proxy):
    # pypi.org is allowlisted → files.pypi.org matches by suffix. The connect to
    # the (nonexistent) host fails upstream (502), but it is NOT refused (403).
    s, status = _connect_request(proxy, "sub.pypi.org:443")
    assert "403" not in status, status
    s.close()


def test_non_connect_method_rejected(proxy):
    s = socket.create_connection(("127.0.0.1", proxy), timeout=3)
    s.sendall(b"GET http://evil.example.com/ HTTP/1.1\r\nHost: evil.example.com\r\n\r\n")
    status = s.recv(256).decode("latin-1", "replace").split("\r\n", 1)[0]
    assert "405" in status, status
    s.close()
