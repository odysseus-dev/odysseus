"""Reach contract construction through the real registered chat route.

Only session/context/inference boundaries are stubbed. MCP imports, singleton
registration, schema enumeration, and contract/policy resolution stay real.
"""
from copy import deepcopy
import json

import pytest

from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import active_turn_contract
from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint


@pytest.fixture
def registered_mcp_manager():
    from src.mcp_manager import McpManager
    from src.tool_utils import get_mcp_manager, set_mcp_manager

    previous = get_mcp_manager()
    manager = McpManager()
    # Seed only discovered metadata. The real manager never connects to or
    # executes an external service in these tests.
    manager._tools["email"] = [
        {"name": function["name"], "description": function["description"],
         "input_schema": deepcopy(function["parameters"])}
        for schema in FUNCTION_TOOL_SCHEMAS
        if (function := schema["function"])["name"] in {"list_email_accounts", "list_emails", "send_email"}
    ]
    set_mcp_manager(manager)
    try:
        assert get_mcp_manager() is manager
        yield manager
    finally:
        set_mcp_manager(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["calendar", "email", "plan", "denied", "no_manager",
                                  "discovery", "identity", "discovery_denied", "mixed", "discovery_reader"])
async def test_actual_route_constructs_contract_with_real_mcp_apis(
    monkeypatch, registered_mcp_manager, case,
):
    from routes import chat_routes
    from src import settings, tool_security
    from src.tool_utils import set_mcp_manager

    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    message = "List my calendar events" if case == "calendar" else "Check my inbox"
    if case.startswith("discovery"):
        message = "List my email accounts"
    elif case == "identity":
        message = "What's my email address?"
    elif case == "mixed":
        message = "List my email accounts and send an email"
    monkeypatch.setattr(chat_routes, "coerce_message_and_session",
                        lambda *args, **kwargs: (message, "session-1"))
    # Identity is independent of the regression under test; retain the actual
    # owner-policy function and both route/dispatcher permission mechanisms.
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda owner: True)
    if case == "no_manager":
        set_mcp_manager(None)
    if case == "denied":
        monkeypatch.setattr(settings, "get_setting", lambda key, default=None:
                            ["send_email"] if key == "disabled_tools" else default)
    if case == "discovery_denied":
        monkeypatch.setattr(settings, "get_setting", lambda key, default=None:
                            ["list_email_accounts"] if key == "disabled_tools" else default)

    observed = []

    async def capture_agent(*args, **kwargs):
        selected = kwargs["turn_contract"]
        assert selected is not None
        assert active_turn_contract() is selected
        observed.append((selected, kwargs["tool_policy"], kwargs["disabled_tools"]))
        yield 'data: {"delta":"Route contract constructed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form["message"] = message
    if case == "plan":
        request._form["plan_mode"] = "true"
    if case == "discovery_reader":
        request._form["active_email_uid"] = "test-message"
    response = await endpoint(request)
    assert response.status_code == 200
    chunks = [chunk async for chunk in response.body_iterator]
    assert any("Route contract constructed." in chunk for chunk in chunks)
    assert any("[DONE]" in chunk for chunk in chunks)
    assert len(observed) == 1
    selected, policy, disabled = observed[0]
    assert "host_shell" not in selected.executable
    assert selected.required <= selected.offered <= selected.executable
    assert not any(policy.blocks(name) for name in selected.offered)
    if case.startswith("discovery") or case == "identity":
        assert selected.capabilities == {"email"}
        assert selected.required_read_operation is not None
        assert selected.required_read_operation.tool.endswith("list_email_accounts")
        if case == "discovery_denied":
            assert "list_email_accounts" in selected.unavailable
            assert selected.offered == set()
        else:
            assert selected.required == {"mcp__email__list_email_accounts"}
            assert selected.offered == {"mcp__email__list_email_accounts", "ask_user", "update_plan"}
        for name in ("send_email", "mcp__email__send_email", "list_emails", "mcp__email__list_emails"):
            assert not selected.permits(name)
            assert policy.blocks(name)
    elif case == "calendar":
        assert selected.capabilities == {"calendar"}
        assert selected.required_read_operation.args == {"action": "list_events"}
        assert "manage_calendar" in selected.offered
        assert not selected.permits("web_search")
    elif case in {"plan", "no_manager"}:
        assert not any(name.startswith("mcp__") for name in selected.executable)
        assert "list_emails" in selected.offered
    else:
        # This exact qualified name proves real singleton lookup and the real
        # get_all_openai_schemas API ran inside the route's construction block.
        assert "mcp__email__list_emails" in selected.offered
        assert "list_emails" not in selected.offered
        if case == "denied":
            assert not selected.permits("send_email")
            assert not selected.permits("mcp__email__send_email")
            assert "send_email" in disabled
        else:
            assert selected.permits("send_email")


@pytest.mark.asyncio
async def test_native_product_browser_turn_cannot_bypass_family_contract(
    monkeypatch, registered_mcp_manager,
):
    """A desktop runtime marker must not let RAG replace browser with shell."""
    from routes import chat_routes
    from src import tool_security

    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    message = "Browse example.com and find the pricing page"
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    monkeypatch.setattr(
        tool_security, "owner_is_admin_or_single_user", lambda owner: True,
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"Browser contract constructed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({
        "message": message,
        "compare_mode": "false",
        "client_runtime_context": json.dumps({
            "surface": "odysseus-native",
            "terminal_agent": True,
        }),
    })
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert len(observed) == 1
    contract = observed[0]
    assert contract is not None
    assert contract.capabilities == {"search_browser"}
    assert "private_browser" in contract.offered
    assert "bash" not in contract.offered
    assert "python" not in contract.offered


@pytest.mark.asyncio
async def test_native_transcription_turn_does_not_offer_shell_fallbacks(
    monkeypatch, registered_mcp_manager,
):
    from routes import chat_routes
    from src import tool_security

    endpoint = _chat_stream_endpoint(
        monkeypatch, "agent", {},
        session_model="odysseus-qwen3.5-tools-pre-heretic",
    )
    message = "Transcribe the speech in /workspace/jo.wav."
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    monkeypatch.setattr(
        tool_security, "owner_is_admin_or_single_user", lambda owner: True,
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"Transcription contract constructed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({
        "message": message,
        "cwd": "/tmp/native-workspace",
        "workspace": "/tmp/native-workspace",
        "client_runtime_context": json.dumps({
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
            "input_files": ["/workspace/jo.wav"],
        }),
    })
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert len(observed) == 1
    contract = observed[0]
    assert contract is not None
    assert contract.capabilities == {"transcription"}
    assert contract.offered == {"transcribe_media", "ask_user"}
    assert "bash" not in contract.offered
    assert "python" not in contract.offered
    assert "inspect_media" not in contract.offered
    assert contract.permits("transcribe_media")


@pytest.mark.asyncio
async def test_native_ocr_turn_offers_only_extract_text(
    monkeypatch, registered_mcp_manager,
):
    from routes import chat_routes
    from src import tool_security

    endpoint = _chat_stream_endpoint(
        monkeypatch, "agent", {},
        session_model="odysseus-qwen3.5-tools-pre-heretic",
    )
    message = "Extract the exact visible text from /workspace/receipt.png with OCR."
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    monkeypatch.setattr(
        tool_security, "owner_is_admin_or_single_user", lambda owner: True,
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"OCR contract constructed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({
        "message": message,
        "cwd": "/tmp/native-workspace",
        "workspace": "/tmp/native-workspace",
        "client_runtime_context": json.dumps({
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
            "input_files": ["/workspace/receipt.png"],
        }),
    })
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert len(observed) == 1
    contract = observed[0]
    assert contract is not None
    assert contract.capabilities == {"ocr"}
    assert contract.offered == {"extract_text", "ask_user"}
    assert "inspect_media" not in contract.offered
    assert "bash" not in contract.offered
    assert "python" not in contract.offered
    assert contract.permits("extract_text")


@pytest.mark.asyncio
async def test_native_sft_owner_can_use_confined_read_file_tool(
    monkeypatch, registered_mcp_manager,
):
    from routes import chat_routes
    from src import tool_security

    endpoint = _chat_stream_endpoint(
        monkeypatch, "agent", {},
        session_model="odysseus-qwen3.5-tools-pre-heretic",
    )
    message = "Use read_file to read /workspace/sample.txt. Read only."
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    monkeypatch.setattr(
        tool_security, "owner_is_admin_or_single_user", lambda owner: True,
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"Native read contract constructed."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({
        "message": message,
        "cwd": "/tmp/native-workspace",
        "workspace": "/tmp/native-workspace",
        "client_runtime_context": json.dumps({
            "surface": "odysseus-native",
            "terminal_agent": True,
            "unattended_mode": True,
            "input_files": ["/workspace/sample.txt"],
        }),
    })
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert len(observed) == 1
    contract = observed[0]
    assert contract is not None
    assert contract.capabilities == {"shell_files"}
    assert "read_file" in contract.offered
    assert contract.offered <= contract.executable


@pytest.mark.asyncio
@pytest.mark.parametrize('deny_core', [False, True])
async def test_exact_odysseus_clean_route_offers_only_requested_compact_family(
    monkeypatch, registered_mcp_manager, deny_core,
):
    from routes import chat_routes
    from src import settings, tool_security

    endpoint = _chat_stream_endpoint(
        monkeypatch, "agent", {},
        session_model="odysseus-qwen3.5-tools-pre-heretic",
    )
    if deny_core:
        monkeypatch.setattr(settings, 'get_setting', lambda key, default=None:
            ['bash', 'python', 'read_file', 'web_search', 'web_fetch', 'ask_user']
            if key == 'disabled_tools' else default)
    message = "List my scheduled tasks. Return at most three names and statuses."
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    monkeypatch.setattr(
        tool_security, "owner_is_admin_or_single_user", lambda owner: True,
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"Tasks read."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({"message": message, "compare_mode": "false"})
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert len(observed) == 1
    contract = observed[0]
    assert contract.selection_mode == "clean_compact_v3_preview"
    assert contract.capabilities == {"tasks"}
    assert contract.offered == ({"manage_tasks"} if deny_core else {"manage_tasks", "ask_user"})
    assert not {"bash", "python", "read_file", "web_search", "web_fetch"} & contract.offered
    assert contract.required == {"manage_tasks"}
    assert contract.permits("manage_tasks")


@pytest.mark.asyncio
async def test_native_terminal_workspace_keeps_environment_execution_contract(
    monkeypatch,
):
    from routes import chat_routes

    endpoint = _chat_stream_endpoint(monkeypatch, "agent", {})
    message = "Run pytest in /workspace/project"
    monkeypatch.setattr(
        chat_routes, "coerce_message_and_session",
        lambda *args, **kwargs: (message, "session-1"),
    )
    observed = []

    async def capture_agent(*args, **kwargs):
        observed.append(kwargs.get("turn_contract"))
        yield 'data: {"delta":"Workspace contract retained."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_routes, "stream_agent_loop", capture_agent)
    request = _RouteRequest("agent")
    request._form.update({
        "message": message,
        "compare_mode": "false",
        "client_runtime_context": json.dumps({
            "surface": "odysseus-native",
            "terminal_agent": True,
        }),
    })
    response = await endpoint(request)
    async for _ in response.body_iterator:
        pass

    assert observed == [None]
