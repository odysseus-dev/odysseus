"""Tests verifying truthful representation of production external bridge execution.

P2-2 invariant: external bridge execution != local containment.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import containment
from src.agent_tools import subprocess_tools
from src import tool_execution as _te


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = "error detail" if status_code >= 400 else ""

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *args, **kwargs):
        return _FakeResponse({"stdout": "remote stdout", "stderr": "", "exit_code": 0})


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    store = tmp_path / "containment_grants.json"
    monkeypatch.setattr(containment, "_store_path", lambda: store)
    return store


@pytest.mark.asyncio
async def test_host_shell_creates_uncontained_external_record(monkeypatch):
    """1. Production bridge execution creates an external/uncontained record.
    2. It cannot be interpreted as contained."""
    monkeypatch.setattr(subprocess_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = subprocess_tools.HostShellTool()
    ctx = {
        "client_runtime_context": {
            "host_shell_bridge": {
                "url": "http://127.0.0.1:17654/run",
                "token": "secret-bridge-token",
            }
        },
        "session_id": "test-session-host-shell",
    }
    result = await tool.execute('{"command": "echo host"}', ctx)

    assert result["exit_code"] == 0
    assert result["output"] == "remote stdout"
    assert "containment" in result
    c = result["containment"]

    # Invariant: external bridge execution != local containment
    assert c["external"] is True
    assert c["contained"] is False
    assert c["mechanism"] == "external_bridge"
    assert c["enforced"] == []
    assert c["executed"] is True

    # Server-owned metadata identifies endpoint without secrets
    assert c.get("endpoint") == "http://127.0.0.1:17654/run"
    assert "secret-bridge-token" not in str(c)


@pytest.mark.asyncio
async def test_host_shell_failure_does_not_become_containment_or_effect_evidence(monkeypatch):
    """5. Bridge failure does not become successful containment/effect evidence."""
    class _FailingClient(_FakeAsyncClient):
        async def post(self, url, *args, **kwargs):
            return _FakeResponse({"error": "bridge exploded"}, status_code=500)

    monkeypatch.setattr(subprocess_tools.httpx, "AsyncClient", _FailingClient)

    tool = subprocess_tools.HostShellTool()
    ctx = {
        "client_runtime_context": {
            "host_shell_bridge": {
                "url": "http://127.0.0.1:17654/run",
                "token": "secret-bridge-token",
            }
        },
        "session_id": "test-session-host-shell-fail",
    }
    result = await tool.execute('{"command": "echo fail"}', ctx)

    assert result["exit_code"] == 1
    assert "bridge returned HTTP 500" in result["error"]
    assert "containment" in result
    c = result["containment"]

    assert c["external"] is True
    assert c["contained"] is False
    assert c["enforced"] == []
    # Failure means not executed
    assert c["executed"] is False


@pytest.mark.asyncio
async def test_routed_bash_and_python_via_bridge_creates_uncontained_external_record():
    """Prove _route_tool_via_bridge generates truthful external records for bash & python."""
    bridge_ctx = {
        "surface": "odysseus-tui",
        "host_shell_bridge": {
            "url": "http://127.0.0.1:17654/run",
            "token": "bridge-token",
        },
    }

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        return {"stdout": "bridge out", "stderr": "", "exit_code": 0}

    from tests.runtime_evidence_helpers import server_authorized_executor

    with patch.object(_te, "_bridge_post", fake_bridge_post), \
         patch.object(_te, "_owner_is_admin", lambda owner: True):
        # Routed bash
        desc, result = await server_authorized_executor(_te.execute_tool_block)(
            SimpleNamespace(tool_type="bash", content="ls -la"),
            session_id="session-routed-bash",
            client_runtime_context=bridge_ctx,
            security_context=_te.NO_TOOL_SECURITY_CONTEXT,
        )
        assert result["exit_code"] == 0
        assert "containment" in result
        cb = result["containment"]
        assert cb["external"] is True
        assert cb["contained"] is False
        assert cb["mechanism"] == "external_bridge"
        assert cb["enforced"] == []
        assert cb["executed"] is True
        assert cb.get("endpoint") == "http://127.0.0.1:17654/run"

        # Routed python
        desc_py, result_py = await server_authorized_executor(_te.execute_tool_block)(
            SimpleNamespace(tool_type="python", content="print('hi')"),
            session_id="session-routed-py",
            client_runtime_context=bridge_ctx,
            security_context=_te.NO_TOOL_SECURITY_CONTEXT,
        )
        assert result_py["exit_code"] == 0
        assert "containment" in result_py
        cp = result_py["containment"]
        assert cp["external"] is True
        assert cp["contained"] is False
        assert cp["mechanism"] == "external_bridge"
        assert cp["enforced"] == []
        assert cp["executed"] is True


@pytest.mark.asyncio
async def test_external_record_does_not_grant_authority(tmp_path):
    """3. The record does not grant execution authority."""
    spec = containment.agent_spec(str(tmp_path), {}, 5)
    grant = containment.declare_external_bridge(
        spec, owner="auth-test-session", endpoint="http://127.0.0.1:17654/run"
    )

    assert grant.external is True
    assert grant.contained is False

    # Attempting to use this grant to run local command must be rejected
    with pytest.raises(ValueError, match="backend does not own"):
        await containment.run(grant, "id")


@pytest.mark.asyncio
async def test_native_local_bash_python_behavior_unchanged(tmp_path, monkeypatch):
    """4. Native local Bash/Python behavior is unchanged."""
    tool_bash = subprocess_tools.BashTool()
    from tests.process_resource_helpers import authorized_handler
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_te, "agent_cwd", lambda: str(workspace))
    ctx = {
        "session_id": "native-session",
    }
    result = await authorized_handler(tool_bash.execute, workspace)("echo 'native run'", ctx)
    assert result["exit_code"] == 0
    assert "native run" in result["output"]
    assert "containment" in result
    c = result["containment"]
    assert c["external"] is False
    assert c["mechanism"] in ("bubblewrap", "process_group")
    assert c["executed"] is True
