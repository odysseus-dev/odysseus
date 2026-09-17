"""Regression tests for SSRF hardening on the manage_endpoints agent tool.

Verifies that the do_manage_endpoints tool function rejects user-supplied URLs
that resolve to link-local (cloud metadata), non-HTTP schemes, or (when
MODELENDPOINT_BLOCK_PRIVATE_IPS=true) private/loopback addresses -- matching
the SSRF guard already enforced on the HTTP route (POST /api/model-endpoints).

The agent tool path bypasses the HTTP route entirely, so it needs its own
validation. A prompt-injection payload smuggled into a skill, note, fetched
page, or email could instruct the model to register a cloud-metadata or
internal-network endpoint via this tool.
"""
import ipaddress
import json
import os
import sys
import types

import pytest

import src.url_safety as _url_safety

_real_check_outbound_url = _url_safety.check_outbound_url


def _fake_resolver(host):
    """Resolver that returns deterministic IPs without real DNS."""
    _HOST_MAP = {
        "169.254.169.254": ["169.254.169.254"],
        "metadata.google.internal": ["169.254.169.254"],
        "localhost": ["127.0.0.1"],
        "127.0.0.1": ["127.0.0.1"],
        "0.0.0.0": ["0.0.0.0"],
        "example.com": ["93.184.216.34"],
        "192.168.1.1": ["192.168.1.1"],
        "10.0.0.1": ["10.0.0.1"],
        "api.openai.com": ["104.18.6.192"],
    }
    if host in _HOST_MAP:
        return _HOST_MAP[host]
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    raise OSError(f"Name or service not known: {host}")


def _check_with_fake_resolver(url, *, block_private=False, resolver=None):
    """Call the real check_outbound_url with our fake resolver."""
    return _real_check_outbound_url(url, block_private=block_private, resolver=_fake_resolver)


def _make_content(base_url, *, name="", api_key=""):
    """Build a JSON content string for the manage_endpoints 'add' action."""
    return json.dumps({
        "action": "add",
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
    })


class _FakeQuery:
    def __init__(self, model):
        self._model = model

    def filter(self, *args):
        return self

    def first(self):
        return None

    def all(self):
        return []


class _FakeDB:
    def __init__(self):
        self.added = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(model)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _FakeModelEndpoint:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _setup_mocks(monkeypatch, db):
    """Set up faked core.database and patched check_outbound_url."""
    fake_core_db = types.ModuleType("core.database")
    fake_core_db.SessionLocal = lambda: db
    fake_core_db.ModelEndpoint = _FakeModelEndpoint
    monkeypatch.setitem(sys.modules, "core.database", fake_core_db)

    monkeypatch.setattr(_url_safety, "check_outbound_url", _check_with_fake_resolver)

    if "src.agent_tools.admin_tools" in sys.modules:
        monkeypatch.delitem(sys.modules, "src.agent_tools.admin_tools", raising=False)


def _get_do_manage_endpoints():
    """Import and return do_manage_endpoints from the reloaded admin_tools."""
    from src.agent_tools.admin_tools import do_manage_endpoints
    return do_manage_endpoints


@pytest.mark.asyncio
async def test_cloud_metadata_blocked(monkeypatch):
    """Cloud metadata IP (169.254.169.254) must be rejected."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("http://169.254.169.254/latest/meta-data/")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "Rejected" in result["error"]
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_non_http_scheme_blocked(monkeypatch):
    """Non-HTTP schemes (file://, gopher://) must be rejected."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("file:///etc/passwd")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "Rejected" in result["error"]
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_loopback_accepted_by_default(monkeypatch):
    """Loopback addresses are accepted by default (local-first design).

    The agent tool uses check_outbound_url directly, which allows
    loopback by default -- this is correct for local model endpoints
    (Ollama, llama.cpp, etc.). Loopback is only blocked in strict mode.
    """
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("http://localhost:11434/v1")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 0
    assert "Added endpoint" in result["response"]
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_strict_mode_blocks_private(monkeypatch):
    """MODELENDPOINT_BLOCK_PRIVATE_IPS=true blocks private IPs."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    monkeypatch.setenv("MODELENDPOINT_BLOCK_PRIVATE_IPS", "true")
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("http://192.168.1.1:8080/v1")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "Rejected" in result["error"]
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_safe_url_accepted(monkeypatch):
    """A safe public URL should be accepted and stored."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("https://api.openai.com/v1", name="OpenAI")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 0
    assert "Added endpoint" in result["response"]
    assert len(db.added) == 1
    assert db.committed is True


@pytest.mark.asyncio
async def test_empty_url_rejected(monkeypatch):
    """An empty base_url must be rejected before SSRF check."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "base_url is required" in result["error"]
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_gopher_scheme_blocked(monkeypatch):
    """gopher:// scheme must be rejected."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("gopher://evil:6379/secret")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "Rejected" in result["error"]
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_error_not_leaked(monkeypatch):
    """Error messages must not leak raw exception text."""
    db = _FakeDB()
    _setup_mocks(monkeypatch, db)
    do_manage_endpoints = _get_do_manage_endpoints()
    content = _make_content("http://169.254.169.254/latest/meta-data/")
    result = await do_manage_endpoints(content, owner="admin")

    assert result["exit_code"] == 1
    assert "Rejected" in result["error"]
    assert "Traceback" not in result["error"]
