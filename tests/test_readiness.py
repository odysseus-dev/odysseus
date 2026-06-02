"""Tests for the readiness / integrity self-check (src/readiness.py)."""

import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-readiness-test-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.readiness import (
    check_readiness,
    probe_chromadb,
    probe_searxng,
    probe_email,
    probe_ntfy,
    _check_local_first,
)


# ── Critical checks ───────────────────────────────────────────────────────────

def test_readiness_reports_core_subsystems():
    result = check_readiness()

    assert {"ready", "version", "checks", "timestamp"}.issubset(result.keys())
    checks = result["checks"]
    for name in ("database", "data_dir", "local_first"):
        assert name in checks, f"missing check: {name}"

    assert checks["database"]["ok"] is True, checks["database"]
    assert checks["data_dir"]["ok"] is True, checks["data_dir"]
    assert result["ready"] is True, result


def test_local_first_check_is_informational_never_fatal():
    result = check_readiness()
    lf = result["checks"]["local_first"]
    assert lf["ok"] is True
    assert "local" in lf


def test_informational_checks_present_in_report():
    result = check_readiness()
    checks = result["checks"]
    for name in ("chromadb", "searxng", "email", "ntfy"):
        assert name in checks, f"missing informational check: {name}"


def test_informational_checks_never_block_readiness():
    """Even when all informational probes fail, ready must still be True
    if the critical checks pass."""
    failing = {"ok": False, "error": "simulated failure"}
    with (
        patch("src.readiness.probe_chromadb", return_value=failing),
        patch("src.readiness.probe_searxng",  return_value=failing),
        patch("src.readiness.probe_email",    return_value=failing),
        patch("src.readiness.probe_ntfy",     return_value=failing),
    ):
        result = check_readiness()
    assert result["ready"] is True
    assert result["checks"]["chromadb"] == failing
    assert result["checks"]["searxng"]  == failing
    assert result["checks"]["email"]    == failing
    assert result["checks"]["ntfy"]     == failing


def test_critical_database_failure_marks_not_ready():
    """A database error makes ready=False."""
    from unittest.mock import MagicMock, patch

    bad_engine = MagicMock()
    bad_engine.connect.side_effect = Exception("DB gone")

    with patch("src.readiness._check_database", return_value={"ok": False, "error": "DB gone"}):
        result = check_readiness()
    assert result["ready"] is False


# ── probe_chromadb ────────────────────────────────────────────────────────────

def test_probe_chromadb_unreachable_host():
    with patch("src.readiness._tcp_reachable", return_value=False):
        result = probe_chromadb()
    assert result["ok"] is False
    assert "error" in result


def test_probe_chromadb_package_missing():
    with (
        patch("src.readiness._tcp_reachable", return_value=True),
        patch.dict("sys.modules", {"chromadb": None}),
    ):
        result = probe_chromadb()
    assert result["ok"] is False
    assert "chromadb" in result["error"].lower() or "error" in result


def test_probe_chromadb_heartbeat_failure():
    mock_client = MagicMock()
    mock_client.heartbeat.side_effect = Exception("heartbeat failed")
    mock_chromadb = MagicMock()
    mock_chromadb.HttpClient.return_value = mock_client

    with (
        patch("src.readiness._tcp_reachable", return_value=True),
        patch.dict("sys.modules", {"chromadb": mock_chromadb}),
    ):
        result = probe_chromadb()
    assert result["ok"] is False
    assert "heartbeat failed" in result["error"]


def test_probe_chromadb_success():
    mock_client = MagicMock()
    mock_client.heartbeat.return_value = None
    mock_chromadb = MagicMock()
    mock_chromadb.HttpClient.return_value = mock_client

    with (
        patch("src.readiness._tcp_reachable", return_value=True),
        patch.dict("sys.modules", {"chromadb": mock_chromadb}),
    ):
        result = probe_chromadb()
    assert result["ok"] is True
    assert "host" in result and "port" in result


# ── probe_searxng ─────────────────────────────────────────────────────────────

def test_probe_searxng_unreachable():
    import httpx
    with patch("httpx.head", side_effect=httpx.ConnectError("refused")):
        result = probe_searxng()
    assert result["ok"] is False
    assert "error" in result


def test_probe_searxng_server_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("httpx.head", return_value=mock_resp):
        result = probe_searxng()
    assert result["ok"] is False
    assert result["status"] == 503


def test_probe_searxng_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.head", return_value=mock_resp):
        result = probe_searxng()
    assert result["ok"] is True
    assert result["status"] == 200


# ── probe_email ───────────────────────────────────────────────────────────────

def test_probe_email_no_accounts_configured():
    with patch("src.readiness.probe_email") as mock_probe:
        mock_probe.return_value = {"ok": True, "configured": False}
        result = mock_probe()
    assert result["ok"] is True
    assert result["configured"] is False


def test_probe_email_imap_host_unreachable():
    accounts = [{"imap_host": "mail.example.com", "imap_port": 993,
                 "imap_user": "user@example.com"}]
    with (
        patch("routes.email_helpers._list_email_accounts", return_value=accounts),
        patch("src.readiness._tcp_reachable", return_value=False),
    ):
        from src.readiness import probe_email as _probe
        result = _probe()
    assert result["ok"] is False
    assert result["accounts"][0]["ok"] is False


def test_probe_email_imap_host_reachable():
    accounts = [{"imap_host": "mail.example.com", "imap_port": 993,
                 "imap_user": "user@example.com"}]
    with (
        patch("routes.email_helpers._list_email_accounts", return_value=accounts),
        patch("src.readiness._tcp_reachable", return_value=True),
    ):
        from src.readiness import probe_email as _probe
        result = _probe()
    assert result["ok"] is True
    assert result["accounts"][0]["ok"] is True


def test_probe_email_missing_imap_host():
    accounts = [{"imap_host": "", "imap_user": "user@example.com"}]
    with patch("routes.email_helpers._list_email_accounts", return_value=accounts):
        from src.readiness import probe_email as _probe
        result = _probe()
    assert result["ok"] is False
    assert "no imap_host" in result["accounts"][0]["error"]


# ── probe_ntfy ────────────────────────────────────────────────────────────────

def test_probe_ntfy_not_configured():
    with patch("src.integrations.load_integrations", return_value=[]):
        from src.readiness import probe_ntfy as _probe
        result = _probe()
    assert result["ok"] is True
    assert result["configured"] is False


def test_probe_ntfy_unreachable():
    import httpx
    integrations = [{"preset": "ntfy", "enabled": True, "base_url": "http://ntfy.example.com"}]
    with (
        patch("src.integrations.load_integrations", return_value=integrations),
        patch("httpx.head", side_effect=httpx.ConnectError("refused")),
    ):
        from src.readiness import probe_ntfy as _probe
        result = _probe()
    assert result["ok"] is False
    assert "error" in result


def test_probe_ntfy_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    integrations = [{"preset": "ntfy", "enabled": True, "base_url": "http://ntfy.example.com"}]
    with (
        patch("src.integrations.load_integrations", return_value=integrations),
        patch("httpx.head", return_value=mock_resp),
    ):
        from src.readiness import probe_ntfy as _probe
        result = _probe()
    assert result["ok"] is True
    assert result["status"] == 200


# ── local_first helper ────────────────────────────────────────────────────────

def test_local_first_sqlite_is_local():
    r = _check_local_first("sqlite:///data/app.db")
    assert r["ok"] is True
    assert r["local"] is True


def test_local_first_remote_is_not_local():
    r = _check_local_first("postgresql://user:pass@db.example.com/mydb")
    assert r["ok"] is True
    assert r["local"] is False
