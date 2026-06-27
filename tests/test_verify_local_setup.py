"""Unit tests for scripts/verify_local_setup.py (no live Docker required)."""

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.verify_local_setup as vls


def test_port_open_true_for_listening_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _host, port = srv.getsockname()
    try:
        assert vls.port_open("127.0.0.1", port, timeout=1.0) is True
    finally:
        srv.close()


def test_port_open_false_for_closed_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    _host, port = srv.getsockname()
    srv.close()
    assert vls.port_open("127.0.0.1", port, timeout=0.5) is False


def test_check_venv_reports_missing_interpreter(tmp_path):
    missing = tmp_path / "venv" / "Scripts" / "python.exe"
    result = vls.check_venv(missing)
    assert result.ok is False
    assert "missing" in result.detail.lower()


def test_check_sidecars_all_ok_when_probes_succeed(monkeypatch):
    monkeypatch.setattr(vls, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(vls, "http_ok", lambda *_a, **_k: True)
    results = vls.check_sidecars()
    assert len(results) == 3
    assert all(r.ok for r in results)


def test_check_sidecars_reports_searxng_down(monkeypatch):
    monkeypatch.setattr(vls, "port_open", lambda *_a, **_k: True)

    def http(url, timeout):
        return url.endswith("/v1/health") or url.endswith(":8091/v1/health")

    monkeypatch.setattr(vls, "http_ok", http)
    results = vls.check_sidecars()
    by_name = {r.name: r for r in results}
    assert by_name["searxng"].ok is False


def test_run_checks_exits_nonzero_when_any_required_check_fails(tmp_path, monkeypatch):
    repo = tmp_path
    venv_py = repo / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")

    monkeypatch.setattr(vls, "check_venv", lambda _p: vls.CheckResult("venv", False, "bad venv"))
    monkeypatch.setattr(vls, "check_sidecars", lambda: [vls.CheckResult("chromadb", True, "ok")])
    code = vls.run_checks(repo_root=repo, venv_python=venv_py)
    assert code == 1
