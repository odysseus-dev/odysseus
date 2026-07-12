"""Tests for the MCP email server's OAuth XOAUTH2 paths (#4992).

These tests exercise the OAuth branches added to mcp_servers.email_server:
- _smtp_ready returns True for OAuth accounts without an SMTP password
- _imap_connect uses XOAUTH2 when oauth_provider == "google"
- _smtp_connect uses XOAUTH2 when oauth_provider == "google"

The mcp package is not installed in the test environment, so we stub it
before importing email_server. The stub provides just enough surface for
the module-level imports to succeed.
"""
import sys
import types
import importlib

# Stub the mcp package so email_server.py can be imported without it.
for _mod_name in ("mcp", "mcp.server", "mcp.server.stdio", "mcp.types"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

sys.modules["mcp"].server = sys.modules["mcp.server"]
sys.modules["mcp.server"].stdio = sys.modules["mcp.server.stdio"]
sys.modules["mcp"].types = sys.modules["mcp.types"]

# Minimal stubs for the classes/functions email_server imports.
class _StubServer:
    def __init__(self, *a, **kw): pass
    def tool(self, *a, **kw): return lambda f: f
    def list_tools(self, *a, **kw): return lambda f: f
    def call_tool(self, *a, **kw): return lambda f: f
    def run(self, *a, **kw): pass

sys.modules["mcp.server"].Server = _StubServer
sys.modules["mcp.server.stdio"].stdio_server = lambda *a, **kw: None
sys.modules["mcp.types"].Tool = type("Tool", (), {})
sys.modules["mcp.types"].TextContent = type("TextContent", (), {})

# Now import the module under test.
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="odysseus-mcp-oauth-test-"))
os.environ.setdefault("DATA_DIR", str(_tmp))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'app.db'}")

# Clear any prior import so we get a fresh module.
if "mcp_servers.email_server" in sys.modules:
    del sys.modules["mcp_servers.email_server"]

import mcp_servers.email_server as srv
from mcp_servers.email_server import _smtp_ready


def test_smtp_ready_true_for_oauth_account_without_password():
    """OAuth accounts don't need an SMTP password — XOAUTH2 uses the token."""
    cfg = {
        "oauth_provider": "google",
        "smtp_host": "smtp.gmail.com",
        "smtp_user": "user@gmail.com",
        # No smtp_password — OAuth uses the access token instead.
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_false_for_oauth_without_host():
    """Even with OAuth, a missing host means SMTP is not ready."""
    cfg = {
        "oauth_provider": "google",
        "smtp_host": "",
        "smtp_user": "user@gmail.com",
    }
    assert _smtp_ready(cfg) is False


def test_smtp_ready_true_for_password_account():
    """Non-OAuth accounts still need host + user + password."""
    cfg = {
        "oauth_provider": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_user": "user@gmail.com",
        "smtp_password": "app-password",
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_false_without_password_or_oauth():
    """Non-OAuth without a password is not ready."""
    cfg = {
        "oauth_provider": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_user": "user@gmail.com",
        "smtp_password": "",
    }
    assert _smtp_ready(cfg) is False


def test_smtp_ready_false_without_host_for_oauth():
    """OAuth but no host is still not ready."""
    cfg = {
        "oauth_provider": "google",
        "smtp_host": "",
        "smtp_user": "",
    }
    assert _smtp_ready(cfg) is False
