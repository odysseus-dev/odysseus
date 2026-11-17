import asyncio
import json
import pytest
import logging
import src.tool_execution as tool_execution
from tests.runtime_evidence_helpers import server_authorized_executor

from src.tool_execution import (
    AgentExecutionBridge,
    NO_TOOL_SECURITY_CONTEXT,
    bind_execution_bridge,
    execute_tool_block,
    get_active_execution_bridge,
)


execute_tool_block = server_authorized_executor(execute_tool_block)


@pytest.mark.parametrize("content", ['{"path":"/workspace/x"}', '{"path":"/workspace/x","content":null}',
                                   '/workspace/x', '/workspace/x\n', '/workspace/x\n \t',
                                   json.dumps({'path': '/workspace/x.py', 'content': '```python\n\n```'})])
def test_bridge_refuses_missing_or_ambiguous_empty_content(monkeypatch, tmp_path, content):
    async def forbidden(*args, **kwargs):
        raise AssertionError("ambiguous write reached remote producer")
    monkeypatch.setattr(tool_execution, "_client_bridge", lambda context: {"url": "http://bridge", "token": "x"})
    monkeypatch.setattr(tool_execution, "_bridge_post", forbidden)
    async def invoke():
        token = tool_execution._active_workspace.set(str(tmp_path))
        try:
            return await tool_execution._route_tool_via_bridge("write_file", content, "s", {})
        finally:
            tool_execution._active_workspace.reset(token)
    _, result = asyncio.run(invoke())
    assert result["exit_code"] == 1
    assert "content" in result["error"] or "JSON" in result["error"]


@pytest.mark.parametrize("body", ["", " \t", "normal source"])
def test_bridge_honors_explicit_json_string_content(monkeypatch, tmp_path, body):
    import base64
    seen = []
    async def post(bridge, endpoint, payload, **kwargs):
        seen.append((endpoint, base64.b64decode(payload["content_b64"])))
        return {"output": "written", "exit_code": 0}
    monkeypatch.setattr(tool_execution, "_client_bridge", lambda context: {"url": "http://bridge", "token": "x"})
    monkeypatch.setattr(tool_execution, "_bridge_post", post)
    async def invoke():
        token = tool_execution._active_workspace.set(str(tmp_path))
        try:
            return await tool_execution._route_tool_via_bridge("write_file", json.dumps({"path": "/workspace/x", "content": body}), "s", {})
        finally:
            tool_execution._active_workspace.reset(token)
    _, result = asyncio.run(invoke())
    assert result["exit_code"] == 0
    assert seen == [("/write", body.encode())]


@pytest.mark.parametrize("content", ['/workspace/x\n', '{"path":"/workspace/x"}', '/workspace/x\n \t'])
def test_scoped_bridge_cannot_blindly_write_implicit_empty(content):
    async def forbidden(*args):
        raise AssertionError("scoped bridge received an ambiguous destructive write")
    bridge = AgentExecutionBridge(forbidden, frozenset({'write_file'}), 'empty-intent-test')
    async def invoke():
        with bind_execution_bridge(bridge):
            block = Block(content)
            block.tool_type = 'write_file'
            return await execute_tool_block(block, security_context=NO_TOOL_SECURITY_CONTEXT)
    _, result = asyncio.run(invoke())
    assert result['exit_code'] == 1
    assert 'content' in result['error']


class Block:
    tool_type = "host_shell"

    def __init__(self, content: str) -> None:
        self.content = content


def test_registry_dispatch_preserves_session_id_for_native_handlers(monkeypatch) -> None:
    seen = {}

    async def fallback(tool, content, **kwargs):
        seen.update(kwargs)
        return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_direct_fallback", fallback)

    async def invoke():
        block = Block('{"location":"Lisbon"}')
        block.tool_type = "get_weather"
        return await execute_tool_block(
            block,
            session_id="runtime-session",
            security_context=NO_TOOL_SECURITY_CONTEXT,
        )

    description, result = asyncio.run(invoke())
    assert description.startswith("registry: get_weather")
    assert result["exit_code"] == 0
    assert seen["session_id"] == "runtime-session"


def test_scoped_execution_bridge_routes_after_security_and_resets() -> None:
    seen = []

    async def route(tool, content, session_id, runtime):
        seen.append((tool, content, session_id, runtime))
        return f"{tool}: scoped", {"output": "ok", "exit_code": 0}

    bridge = AgentExecutionBridge(
        route_tool=route,
        supported_tools=frozenset({"host_shell"}),
        name="test-environment",
    )

    async def invoke():
        with bind_execution_bridge(bridge):
            assert get_active_execution_bridge() is bridge
            return await execute_tool_block(
                Block("pwd"),
                session_id="run-1",
                owner="pewds",
                security_context=NO_TOOL_SECURITY_CONTEXT,
                client_runtime_context={"surface": "test"},
            )

    description, result = asyncio.run(invoke())

    assert description == "host_shell: scoped"
    assert result == {"output": "ok", "exit_code": 0}
    assert seen == [("host_shell", "pwd", "run-1", {"surface": "test"})]
    assert get_active_execution_bridge() is None


def test_scoped_execution_bridges_are_isolated_across_concurrent_rollouts() -> None:
    async def invoke(label: str):
        async def route(tool, content, session_id, runtime):
            await asyncio.sleep(0)
            assert get_active_execution_bridge().name == label
            return f"{tool}: {label}", {"output": label, "exit_code": 0}

        bridge = AgentExecutionBridge(
            route_tool=route,
            supported_tools=frozenset({"host_shell"}),
            name=label,
        )
        with bind_execution_bridge(bridge):
            return await execute_tool_block(
                Block("pwd"),
                session_id=label,
                owner="pewds",
                security_context=NO_TOOL_SECURITY_CONTEXT,
            )

    async def run_both():
        return await asyncio.gather(invoke("rollout-a"), invoke("rollout-b"))

    results = asyncio.run(run_both())
    assert [result[1]["output"] for result in results] == ["rollout-a", "rollout-b"]
    assert get_active_execution_bridge() is None


def test_scoped_execution_bridge_does_not_bypass_disabled_tool_gate() -> None:
    called = False

    async def route(tool, content, session_id, runtime):
        nonlocal called
        called = True
        return tool, {"exit_code": 0}

    bridge = AgentExecutionBridge(route, frozenset({"host_shell"}))

    async def invoke():
        with bind_execution_bridge(bridge):
            return await execute_tool_block(
                Block("pwd"),
                disabled_tools={"host_shell"},
                security_context=NO_TOOL_SECURITY_CONTEXT,
            )

    description, result = asyncio.run(invoke())
    assert description == "host_shell: BLOCKED"
    assert result["exit_code"] == 1
    assert called is False


def test_scoped_execution_bridge_failure_logs_compact_warning(caplog) -> None:
    async def route(tool, content, session_id, runtime):
        raise RuntimeError("Command timed out after 900 seconds")

    bridge = AgentExecutionBridge(
        route_tool=route,
        supported_tools=frozenset({"host_shell"}),
        name="test-environment",
    )

    async def invoke():
        with bind_execution_bridge(bridge):
            return await execute_tool_block(
                Block("sleep 9999"),
                owner="pewds",
                security_context=NO_TOOL_SECURITY_CONTEXT,
            )

    with caplog.at_level(logging.WARNING):
        description, result = asyncio.run(invoke())

    assert description == "host_shell: external execution failed"
    assert result["exit_code"] == 1
    assert result["execution_bridge"] == "test-environment"
    assert "Command timed out after 900 seconds" in result["error"]
    assert "Traceback" not in caplog.text
    assert "Scoped execution bridge test-environment failed for tool=host_shell" in caplog.text


def test_tui_bridge_write_file_rejects_text_for_binary_artifact(monkeypatch, tmp_path) -> None:
    """The bridge path must preserve rendered media just like local writes."""
    called = False

    async def fake_post(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"output": "should not run", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_client_bridge", lambda _context: {"url": "http://bridge", "token": "x"})
    monkeypatch.setattr(tool_execution, "_bridge_post", fake_post)

    async def invoke():
        token = tool_execution._active_workspace.set(str(tmp_path))
        try:
            return await tool_execution._route_tool_via_bridge(
                "write_file",
                '{"path":"/tmp_workspace/results/screenshot.png","content":"The screenshot is complete."}',
                "run-1",
                {"surface": "odysseus-tui"},
            )

        finally:
            tool_execution._active_workspace.reset(token)

    description, result = asyncio.run(invoke())

    assert description == "write_file: /tmp_workspace/results/screenshot.png"
    assert result["exit_code"] == 1
    assert result["binary_artifact_preserved"] is True
    assert "binary artifact path" in result["error"]
    assert called is False


def test_scoped_bridge_cannot_bypass_binary_text_write_guard() -> None:
    called = False

    async def route(tool, content, session_id, runtime):
        nonlocal called
        called = True
        return tool, {"output": "should not run", "exit_code": 0}

    bridge = AgentExecutionBridge(route, frozenset({"write_file"}), name="task-workspace")

    async def invoke():
        block = Block("/tmp_workspace/results/screenshot.png\ncompletion prose")
        block.tool_type = "write_file"
        with bind_execution_bridge(bridge):
            return await execute_tool_block(
                block,
                security_context=NO_TOOL_SECURITY_CONTEXT,
            )

    description, result = asyncio.run(invoke())

    assert description == "write_file: /tmp_workspace/results/screenshot.png"
    assert result["exit_code"] == 1
    assert result["binary_artifact_preserved"] is True
    assert called is False
