import json

import pytest

from mcp_servers import pdv_control_server


def test_pdv_mcp_boundary_requires_loopback_and_hex_key(tmp_path, monkeypatch):
    key = tmp_path / "adapter.key"
    key.write_text("a" * 64, encoding="ascii")
    monkeypatch.setenv("ODYSSEUS_PDV_ADAPTER_KEY_FILE", str(key))
    monkeypatch.setenv("PDV_EXECUTION_OS_URL", "https://remote.invalid:4173")
    with pytest.raises(RuntimeError, match="loopback"):
        pdv_control_server._boundary()
    monkeypatch.setenv("PDV_EXECUTION_OS_URL", "http://127.0.0.1:4173")
    assert pdv_control_server._boundary() == ("http://127.0.0.1:4173", "a" * 64)


@pytest.mark.asyncio
async def test_pdv_mcp_allowed_result_preserves_provenance(monkeypatch):
    async def approved(tool_name, arguments):
        return {"allowed": True, "result": {"service": "pdv-execution-os"}, "provenance": {"source_system": "pdv-execution-os", "tool_name": tool_name}}
    monkeypatch.setattr(pdv_control_server, "_invoke", approved)
    result = await pdv_control_server.call_tool("pdv_health_status", {})
    payload = json.loads(result[0].text)
    assert payload["provenance"]["source_system"] == "pdv-execution-os"
    assert payload["provenance"]["tool_name"] == "health.status"


@pytest.mark.asyncio
async def test_pdv_mcp_unknown_tool_never_reaches_transport(monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("transport must not run")
    monkeypatch.setattr(pdv_control_server, "_invoke", forbidden)
    with pytest.raises(ValueError, match="Unknown PDV tool"):
        await pdv_control_server.call_tool("shell_exec", {"command": "whoami"})


@pytest.mark.asyncio
async def test_pdv_mcp_transport_denial_is_protocol_failure(monkeypatch):
    async def denied(*_args):
        raise RuntimeError("PDV MCP invocation refused (403, TOOL_NOT_ALLOWLISTED)")
    monkeypatch.setattr(pdv_control_server, "_invoke", denied)
    with pytest.raises(RuntimeError, match="TOOL_NOT_ALLOWLISTED"):
        await pdv_control_server.call_tool("pdv_health_status", {})
