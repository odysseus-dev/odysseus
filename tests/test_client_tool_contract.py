import json

from routes.chat_routes import _parse_client_runtime_context
from src.agent_loop import _TUI_BRIDGE_TOOL_NAMES
from src.client_tool_contract import (
    TUI_CLIENT_TOOL_NAMES,
    TUI_ROUTED_BRIDGE_TOOL_NAMES,
)
from src.tool_execution import _ROUTED_BRIDGE_TOOLS
from src.tool_policy import known_tool_names


def test_tui_client_tool_contract_has_one_execution_owner_per_tool():
    assert _ROUTED_BRIDGE_TOOLS == TUI_ROUTED_BRIDGE_TOOL_NAMES
    assert _TUI_BRIDGE_TOOL_NAMES == TUI_CLIENT_TOOL_NAMES
    assert TUI_CLIENT_TOOL_NAMES == (
        TUI_ROUTED_BRIDGE_TOOL_NAMES | {"apply_patch", "host_shell"}
    )


def test_every_canonical_tui_client_tool_has_a_model_schema():
    legacy_transport_aliases = {"list_dir", "find_files"}
    assert TUI_CLIENT_TOOL_NAMES - legacy_transport_aliases <= known_tool_names()


def test_runtime_context_parser_accepts_exactly_the_shared_client_tool_contract():
    advertised = [
        {"name": name} for name in sorted(TUI_CLIENT_TOOL_NAMES)
    ] + [{"name": "not_a_real_tool"}]
    context = _parse_client_runtime_context(json.dumps({
        "surface": "odysseus-tui",
        "session_cwd": "/tmp/workspace",
        "host_shell_bridge": {
            "url": "http://127.0.0.1:17654/run", "token": "secret",
        },
        "client_tools": advertised,
    }))

    assert {item["name"] for item in context["client_tools"]} == TUI_CLIENT_TOOL_NAMES


def test_native_cook_context_is_normalized_to_unattended():
    context = _parse_client_runtime_context({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "interaction_mode": "cook",
    })

    assert context["interaction_mode"] == "cook"
    assert context["unattended_mode"] is True
