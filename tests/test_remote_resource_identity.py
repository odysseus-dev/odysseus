"""Backend selection is resolution, never an operation or resource grant."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from src.agent_runtime.remote_resources import (
    active_backend_operation, bind_backend_operation, bind_backend_for_operation,
    configuration_incarnation, endpoint_identity, integration_resource, seal_backends,
)
from src.agent_runtime.resources import ExternalResource, NativeBackendResource, ResourceIdentityError
from src.mcp_manager import McpManager
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock


def grant(*tools, resources=None):
    return RequestAuthority("remote-request", "alice", "s", "",
                            tuple(OperationGrant(t) for t in tools), backend_resources=resources)


async def dispatch(authority, tool, content="{}", **kwargs):
    from src import tool_execution as execution
    return await execution.execute_tool_block(ToolBlock(tool, content), owner=authority.owner,
        session_id=authority.session_id, request_authority=authority,
        security_context=kwargs.pop("security_context", execution.NO_TOOL_SECURITY_CONTEXT), **kwargs)


def approval(authority, tool, content="{}", **kwargs):
    store = ToolApprovalStore()
    pending = store.create(owner=authority.owner, session_id=authority.session_id, origin_run_id="run",
        tool_name=tool, content=content, workspace=None, request_authority=authority,
        external_untrusted_context_seen=True, capabilities=capabilities_for_action(tool, content), **kwargs)
    return store.consume(pending.approval_id, decision="approve", owner=authority.owner, session_id=authority.session_id)


@pytest.fixture
def manager(monkeypatch):
    from src import tool_execution as execution
    value = McpManager()
    monkeypatch.setattr(execution, "get_mcp_manager", lambda: value)
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    return value


def connect(manager, server="alpha", tools=("read", "write"), url="https://example.test/mcp?token=SECRET"):
    session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        content=[SimpleNamespace(text="remote result")], isError=False)))
    manager._sessions[server] = session
    manager._tools[server] = [{"name": tool} for tool in tools]
    manager._resource_endpoints[server] = (endpoint_identity(url), configuration_incarnation(url))
    manager._register_resource_connection(server, session)
    return session


@pytest.mark.parametrize("kind", ["availability", "selection", "model_name", "legacy"])
async def test_remote_availability_does_not_create_resource_authority(manager, kind):
    session = connect(manager)
    authority = grant("mcp__alpha__read", resources=()) if kind != "model_name" else grant()
    if kind == "legacy":
        snapshot = grant("mcp__alpha__read").to_dict()
        snapshot["version"] = 2
        authority = RequestAuthority.from_dict(snapshot)
    _, result = await dispatch(authority, "mcp__alpha__read")
    assert result["exit_code"] == 1
    session.call_tool.assert_not_awaited()


async def test_qualified_mcp_binds_exact_tool_and_backend(manager):
    session = connect(manager)
    authority = grant("mcp__alpha__read")
    _, allowed = await dispatch(authority, "mcp__alpha__read", '{"record":"one"}')
    assert allowed["exit_code"] == 0
    session.call_tool.assert_awaited_once_with("read", {"record": "one"})
    _, denied = await dispatch(replace(authority, grants=(OperationGrant("mcp__alpha__write"),)), "mcp__alpha__write")
    assert denied["failure_kind"] == "resource_identity_denied"
    assert active_backend_operation() is None


@pytest.mark.parametrize("change", ["session", "endpoint", "path", "query", "discovery"])
async def test_remote_identity_changes_invalidate_admission_and_exact_approval(manager, change):
    old = connect(manager)
    authority = grant("mcp__alpha__read")
    exact = approval(authority, "mcp__alpha__read", '{"resource":"one"}')
    assert exact.pending.backend_operation is not None
    if change == "session":
        new = connect(manager)
    elif change == "discovery":
        manager._tools["alpha"] = [{"name": "write"}]
    else:
        url = {"endpoint": "https://other.test/mcp", "path": "https://example.test/other",
               "query": "https://example.test/mcp?token=OTHER"}[change]
        manager._resource_endpoints["alpha"] = (endpoint_identity(url), configuration_incarnation(url))
    for extra in ({}, {"exact_approval": exact, "security_context": ToolRunSecurityContext(external_untrusted_context_seen=True)}):
        _, result = await dispatch(authority, "mcp__alpha__read", '{"resource":"one"}', **extra)
        assert result["failure_kind"] == "resource_identity_denied"
    old.call_tool.assert_not_awaited()
    if change == "session":
        new.call_tool.assert_not_awaited()
    assert not exact._claimed


@pytest.mark.parametrize("change", ["tool", "selector", "request", "owner", "session"])
async def test_remote_approval_is_bound_to_operation_and_request(manager, change):
    session = connect(manager)
    authority = grant("mcp__alpha__read", "mcp__alpha__write")
    exact = approval(authority, "mcp__alpha__read", '{"record":"one"}')
    tool, content = "mcp__alpha__read", '{"record":"one"}'
    if change == "tool":
        tool = "mcp__alpha__write"
    elif change == "selector":
        content = '{"record":"two"}'
    else:
        authority = replace(authority, **{"request": {"request_id": "other"}, "owner": {"owner": "bob"},
                                         "session": {"session_id": "other"}}[change])
    _, result = await dispatch(authority, tool, content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["exit_code"] == 1
    session.call_tool.assert_not_awaited()
    assert not exact._claimed


async def test_legacy_exact_remote_approval_is_one_use_and_does_not_mint_backend_scope(manager):
    session = connect(manager)
    authority = grant(resources=())
    exact = approval(authority, "mcp__alpha__read")
    security = ToolRunSecurityContext(external_untrusted_context_seen=True)
    _, result = await dispatch(authority, "mcp__alpha__read", exact_approval=exact, security_context=security)
    assert result["exit_code"] == 0
    _, replay = await dispatch(authority, "mcp__alpha__read", exact_approval=exact, security_context=security)
    assert replay["exit_code"] == 1
    assert session.call_tool.await_count == 1 and authority.backend_resources == ()
    assert "backend_operation" not in exact.pending.public_payload()


async def test_child_cannot_use_parent_ungranted_backend_or_exact_approval(manager):
    session = connect(manager)
    parent = grant("mcp__alpha__read", resources=())
    child = grant("mcp__alpha__read")
    exact = approval(child, "mcp__alpha__read")
    with bind_request_authority(parent):
        _, denied = await dispatch(child, "mcp__alpha__read", exact_approval=exact,
            security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert denied["failure_kind"] == "resource_identity_denied"
    session.call_tool.assert_not_awaited()


def test_remote_snapshots_exclude_credentials_and_cannot_claim_containment(manager):
    connect(manager, url="https://user:PASSWORD@example.test/SECRET_PATH?token=TOKEN")
    authority = grant("mcp__alpha__read")
    snapshot = json.dumps(authority.to_dict())
    assert all(secret not in snapshot for secret in ("PASSWORD", "SECRET_PATH", "TOKEN", "user:"))
    resource = authority.backend_resources[0]
    assert resource.endpoint_id == "https://example.test"
    assert resource.external is True and resource.contained is False
    assert RequestAuthority.from_dict(json.loads(snapshot)) == authority
    with pytest.raises(ValueError):
        replace(resource, contained=True)


async def test_mcp_revalidates_at_transport_and_never_retries_bound_calls(manager):
    manager._resource_owners["memory"] = "alice"
    session = connect(manager, server="memory")
    authority = grant("mcp__memory__read")
    operation = ExactOperation.normalize("mcp__memory__read", "{}")
    bound = bind_backend_for_operation(authority, operation)
    reconnect = AsyncMock()
    manager._reconnect_builtin = reconnect
    session.call_tool.side_effect = RuntimeError("disconnected")
    with bind_backend_operation(bound):
        result = await manager.call_tool(operation.tool, {})
        assert result["exit_code"] == 1
        replacement = connect(manager, server="memory")
        result = await manager.call_tool(operation.tool, {})
        assert result["failure_kind"] == "resource_identity_denied"
        replacement.call_tool.assert_not_awaited()
    reconnect.assert_not_awaited()


@pytest.mark.parametrize("owner", ["", "bob"])
async def test_builtin_memory_backend_requires_its_configured_owner(manager, owner):
    manager._resource_owners["memory"] = owner
    session = connect(manager, server="memory")
    _, result = await dispatch(grant("mcp__memory__read"), "mcp__memory__read")
    assert result["failure_kind"] == "resource_identity_denied"
    session.call_tool.assert_not_awaited()


async def test_native_filesystem_cannot_be_redirected_through_mcp(manager, tmp_path):
    session = connect(manager, server="filesystem", tools=("read_file",))
    (tmp_path / "a").write_text("native contents")
    authority = RequestAuthority("request", "alice", "s", str(tmp_path), (OperationGrant("read_file"),))
    from src import tool_execution as execution
    _, result = await execution.execute_tool_block(ToolBlock("read_file", "a"), owner="alice", session_id="s",
        workspace=str(tmp_path), request_authority=authority, security_context=execution.NO_TOOL_SECURITY_CONTEXT)
    assert result["output"] == "native contents"
    assert isinstance(authority.backend_resources[0], NativeBackendResource)
    session.call_tool.assert_not_awaited()


@pytest.mark.parametrize("change", ["alias", "endpoint", "secret_path"])
async def test_integration_alias_and_configuration_cannot_retarget_approval(monkeypatch, change):
    from src import integrations
    rows = [{"id": "one", "name": "service", "base_url": "https://service.test/SECRET", "enabled": True}]
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    authority = grant("api_call", resources=(integration_resource(rows[0]),))
    exact = approval(authority, "api_call", '{"integration":"service","path":"/record/one"}')
    assert exact.pending.backend_operation.resource.server_id == "one"
    assert "SECRET" not in json.dumps(authority.to_dict())
    if change == "alias":
        rows[:] = [{**rows[0], "id": "two"}]
    else:
        rows[0]["base_url"] = "https://other.test/SECRET" if change == "endpoint" else "https://service.test/OTHER"
    from src import tool_execution as execution
    handler = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    _, result = await dispatch(authority, "api_call", exact.pending.content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["failure_kind"] == "resource_identity_denied"
    handler.assert_not_awaited()


@pytest.mark.parametrize("error", [None, RuntimeError, asyncio.CancelledError])
async def test_scoped_bridge_context_restores_and_replacement_is_ungranted(monkeypatch, error):
    from src import tool_execution as execution
    seen = []
    async def route(*args):
        seen.append(active_backend_operation().resource)
        if error:
            raise error("stop")
        return "bridge", {"exit_code": 0}
    bridge = execution.AgentExecutionBridge(route, frozenset({"host_shell"}), name="test")
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    with execution.bind_execution_bridge(bridge):
        authority = grant("host_shell")
        parent_bound = bind_backend_for_operation(authority, ExactOperation.normalize("host_shell", "parent"))
        with bind_backend_operation(parent_bound):
            if error is asyncio.CancelledError:
                with pytest.raises(error):
                    await dispatch(authority, "host_shell", "pwd")
            else:
                await dispatch(authority, "host_shell", "pwd")
            assert active_backend_operation() is parent_bound
        assert active_backend_operation() is None
    assert seen[0].external and not seen[0].contained
    with execution.bind_execution_bridge(replace(bridge)):
        _, denied = await dispatch(authority, "host_shell", "pwd")
    assert denied["failure_kind"] == "resource_identity_denied"


async def test_tui_endpoint_is_registered_only_at_trusted_admission(monkeypatch):
    from src import tool_execution as execution
    context = {"surface": "odysseus-tui", "host_shell_bridge": {"url": "http://127.0.0.1:17654/run", "token": "TOKEN"}}
    authority = grant("bash", resources=(NativeBackendResource("bash"),))
    _, denied = await dispatch(authority, "bash", "pwd", client_runtime_context=context)
    assert denied["failure_kind"] == "resource_identity_denied"
    authority = replace(authority, backend_resources=seal_backends(["bash"], context=context, owner="alice"))
    monkeypatch.setattr(execution, "_bridge_post", AsyncMock(return_value={"exit_code": 0, "stdout": "external", "stderr": ""}))
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    _, allowed = await dispatch(authority, "bash", "pwd", client_runtime_context=context)
    assert allowed["exit_code"] == 0
    context["host_shell_bridge"]["url"] = "http://127.0.0.1:17655/run"
    _, denied = await dispatch(authority, "bash", "pwd", client_runtime_context=context)
    assert denied["failure_kind"] == "resource_identity_denied"


@pytest.mark.parametrize("change", [None, "url", "token"])
async def test_http_bridge_factory_and_admission_share_config_identity(monkeypatch, change):
    from src import tool_execution as execution
    from routes.chat_routes import _external_execution_bridge
    context = {"external_execution_bridge": {"url": "http://127.0.0.1:17654/execute",
        "token": "SECRET_TOKEN", "supported_tools": ["host_shell"]}}
    authority = grant("host_shell", resources=seal_backends(["host_shell"], context=context, owner="alice"))
    if change:
        context["external_execution_bridge"][change] = "http://127.0.0.1:17655/execute" if change == "url" else "OTHER_TOKEN"
    bridge = _external_execution_bridge(context)
    route = AsyncMock(return_value=("bridge", {"exit_code": 0}))
    bridge = replace(bridge, route_tool=route)
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    with execution.bind_execution_bridge(bridge):
        _, result = await dispatch(authority, "host_shell", "pwd", client_runtime_context=context)
    if change:
        assert result["failure_kind"] == "resource_identity_denied"
        route.assert_not_awaited()
    else:
        assert result["exit_code"] == 0
        route.assert_awaited_once()
    assert "SECRET_TOKEN" not in json.dumps(authority.to_dict())


async def test_backend_alias_cannot_retarget_a_legacy_tool_after_approval(manager, monkeypatch):
    from src import tool_execution as execution
    first = connect(manager, server="web_fetch", tools=("web_fetch",))
    second = connect(manager, server="other", tools=("fetch",))
    authority = grant("web_fetch")
    exact = approval(authority, "web_fetch", "https://page.test/one")
    monkeypatch.setitem(execution._MCP_TOOL_MAP, "web_fetch", ("other", "fetch"))
    _, result = await dispatch(authority, "web_fetch", exact.pending.content, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["failure_kind"] == "resource_identity_denied"
    first.call_tool.assert_not_awaited()
    second.call_tool.assert_not_awaited()


async def test_native_backend_is_pinned_when_mcp_becomes_available(manager, monkeypatch):
    from src import tool_execution as execution
    authority = grant("web_fetch")
    session = connect(manager, server="web_fetch", tools=("web_fetch",))
    fallback = AsyncMock(return_value={"output": "native", "exit_code": 0})
    monkeypatch.setattr(execution, "_direct_fallback", fallback)
    _, result = await dispatch(authority, "web_fetch", "https://page.test/one")
    assert result["output"] == "native"
    session.call_tool.assert_not_awaited()


async def test_integration_inventory_does_not_supply_backend_scope(monkeypatch):
    from src import integrations
    rows = [{"id": "one", "name": "service", "base_url": "https://service.test", "enabled": True}]
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    authority = grant("api_call")
    assert authority.backend_resources == ()
    _, denied = await dispatch(authority, "api_call", '{"integration":"service"}')
    assert denied["failure_kind"] == "resource_identity_denied"


async def test_external_bash_marker_cannot_switch_to_local_background_execution(manager, monkeypatch):
    from src import bg_jobs
    session = connect(manager, server="bash", tools=("bash",))
    launch = AsyncMock()
    monkeypatch.setattr(bg_jobs, "launch", launch)
    _, result = await dispatch(grant("bash"), "bash", "#!bg\npwd")
    assert result["exit_code"] == 0
    assert session.call_tool.await_count == 1
    launch.assert_not_called()


@pytest.mark.parametrize("alias", ["host_shell_bridge", "hostShellBridge"])
def test_host_shell_bridge_aliases_resolve_to_one_external_identity(alias):
    context = {"surface": "odysseus-tui", alias: {"url": "http://127.0.0.1:17654/run", "token": "TOKEN"}}
    resources = seal_backends(["host_shell"], context=context, owner="alice")
    assert len(resources) == 1 and isinstance(resources[0], ExternalResource)
    assert resources[0].external and not resources[0].contained


async def test_host_shell_cannot_reconstruct_an_unsealed_external_backend(monkeypatch):
    from src import tool_execution as execution
    handler = AsyncMock()
    monkeypatch.setattr(execution, "_execute_tool_block_impl", handler)
    _, result = await dispatch(grant("host_shell"), "host_shell", "pwd")
    assert result["failure_kind"] == "resource_identity_denied"
    handler.assert_not_awaited()


async def test_http_backend_without_bound_producer_cannot_fall_back_to_native(monkeypatch):
    from src import tool_execution as execution
    context = {"external_execution_bridge": {"url": "http://127.0.0.1:17654/execute",
        "token": "TOKEN", "supported_tools": ["bash"]}}
    authority = grant("bash", resources=seal_backends(["bash"], context=context, owner="alice"))
    fallback = AsyncMock()
    monkeypatch.setattr(execution, "_direct_fallback", fallback)
    _, denied = await dispatch(authority, "bash", "pwd", client_runtime_context=context)
    assert denied["failure_kind"] == "resource_identity_denied"
    fallback.assert_not_awaited()


async def test_resumed_child_approval_cannot_restore_excluded_backend(manager):
    session = connect(manager)
    child = grant("mcp__alpha__read", resources=()).intersect(grant("mcp__alpha__read"))
    exact = approval(child, "mcp__alpha__read")
    assert exact.pending.backend_operation is None
    _, result = await dispatch(replace(child, inherited=False), "mcp__alpha__read", exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True))
    assert result["failure_kind"] == "resource_identity_denied"
    session.call_tool.assert_not_awaited()


async def test_integration_executes_server_resolved_id_and_revalidates_loaded_configuration(monkeypatch):
    from src import integrations, tool_execution as execution
    row = {"id": "one", "name": "service", "base_url": "https://service.test", "enabled": True}
    monkeypatch.setattr(integrations, "load_integrations", lambda: [dict(row)])
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    authority = grant("api_call", resources=(integration_resource(row),))
    producer = AsyncMock(return_value={"output": "remote", "exit_code": 0})
    original = integrations.execute_api_call
    monkeypatch.setattr(integrations, "execute_api_call", producer)
    _, allowed = await dispatch(authority, "api_call", '{"integration":"service","method":"GET","path":"/record/one"}')
    assert allowed["exit_code"] == 0
    assert producer.await_args.args == ("one", "GET", "/record/one")
    monkeypatch.setattr(integrations, "execute_api_call", original)
    monkeypatch.setattr(integrations, "_find_integration", lambda identifier: {**row, "base_url": "https://other.test"})
    _, denied = await dispatch(authority, "api_call", '{"integration":"service","path":"/record/one"}')
    assert denied["failure_kind"] == "resource_identity_denied"
