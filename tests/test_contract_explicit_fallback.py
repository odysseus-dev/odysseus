"""Execute the production explicit-intent fallback branch in isolation."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent_loop import ToolBlock, _has_accepted_contract_tool_call


def run_fallback(blocks, contract):
    source = Path(__file__).resolve().parents[1] / "src" / "agent_loop.py"
    tree = ast.parse(source.read_text())
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and any(isinstance(n, ast.Name) and n.id == "_has_accepted_contract_tool_call"
                          for n in ast.walk(node.test)))
    # Test this branch's condition and mutations, not adjacent recovery branches.
    branch.orelse = []
    native = [{"id": "call_search_1", "type": "function", "function": {
        "name": "web_search", "arguments": '{"query":"IANA example domains"}'}}]
    converted = list(native)
    state = {
        "_has_accepted_contract_tool_call": _has_accepted_contract_tool_call,
        "turn_contract": contract, "tool_blocks": blocks,
        "_qwen_explicit_tool": "web_search",
        "_qwen_explicit_args": "Search IANA. Return one link. Do not send messages.",
        "_caller_relevant_tools": None, "tool_events": [], "_call_freq": {},
        "_has_successful_tool_evidence": lambda *a: False,
        "ToolBlock": ToolBlock, "converted_calls": converted,
        "native_tool_calls": native, "used_native": True,
        "full_response": "model preamble", "round_response": "model preamble",
        "logger": SimpleNamespace(info=lambda *a: None),
    }
    exec(compile(ast.Module(body=[branch], type_ignores=[]), str(source), "exec"), state)
    return state, native, converted


@pytest.mark.parametrize("tool,args", [
    ("web_search", '{"query":"IANA example domains","count":1}'),
    ("web_fetch", '{"url":"https://www.iana.org/help/example-domains"}'),
    ("web_search", "IANA example domains"),
])
def test_in_scope_call_keeps_arguments_identity_and_native_ids(tool, args):
    blocks = [ToolBlock(tool, args)]
    contract = SimpleNamespace(permits=lambda name: name in {"web_search", "web_fetch"})
    state, native, converted = run_fallback(blocks, contract)
    assert state["tool_blocks"] is blocks
    assert state["tool_blocks"][0].content == args
    assert state["native_tool_calls"] is native
    assert state["converted_calls"] is converted
    assert state["native_tool_calls"][0]["id"] == "call_search_1"
    assert state["used_native"] is True
    assert state["full_response"] == "model preamble"


@pytest.mark.parametrize("blocks", [[], [ToolBlock("manage_notes", '{"action":"list"}')]])
def test_fallback_remains_when_no_accepted_contract_call(blocks):
    contract = SimpleNamespace(permits=lambda name: name == "web_search")
    state, _, _ = run_fallback(blocks, contract)
    assert state["tool_blocks"][0].content == state["_qwen_explicit_args"]
    assert state["native_tool_calls"] == []
    assert state["used_native"] is False


def test_standalone_behavior_is_unchanged():
    state, _, _ = run_fallback([ToolBlock("web_search", "IANA example domains")], None)
    assert state["tool_blocks"][0].content == state["_qwen_explicit_args"]
    assert state["converted_calls"] == []


def test_mixed_batch_with_accepted_call_is_not_replaced():
    blocks = [ToolBlock("web_search", "IANA"), ToolBlock("manage_notes", "list")]
    contract = SimpleNamespace(permits=lambda name: name == "web_search")
    state, _, _ = run_fallback(blocks, contract)
    # Existing downstream policy gates still own rejection of the other call.
    assert state["tool_blocks"] is blocks
