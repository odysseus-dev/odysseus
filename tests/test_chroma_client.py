"""Regression tests for the ChromaDB singleton client (issue #326).

Covers the fast-fail preflight (so an unreachable ChromaDB doesn't block
startup for the full OS connection timeout) and the rule that a failed
connection must not poison the cached singleton.
"""
import socket
import time

import pytest

import src.chroma_client as cc


def _free_port() -> int:
    """Bind to port 0, grab the assigned port, release it — nothing listens."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_port_open_false_for_closed_port_and_is_fast():
    port = _free_port()
    t0 = time.monotonic()
    assert cc._port_open("127.0.0.1", port, timeout=1.0) is False
    # The whole point: we fail fast, nowhere near the 30-60s OS timeout.
    assert time.monotonic() - t0 < 5.0


def test_port_open_true_for_listening_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    try:
        assert cc._port_open(host, port, timeout=1.0) is True
    finally:
        srv.close()


def test_get_chroma_client_does_not_cache_when_unreachable(monkeypatch):
    pytest.importorskip("chromadb")
    cc.reset_client()
    monkeypatch.setenv("CHROMADB_HOST", "127.0.0.1")
    monkeypatch.setenv("CHROMADB_PORT", str(_free_port()))
    with pytest.raises(RuntimeError):
        cc.get_chroma_client()
    # A failed connection must leave the singleton unset so a later call
    # (once ChromaDB is up) can succeed.
    assert cc._client is None
