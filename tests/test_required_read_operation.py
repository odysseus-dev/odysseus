import ast
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.agent_loop as al


@dataclass(frozen=True)
class Operation:
    tool_name: str
    args: object
    max_items: int | None = None


def contract(operation, *, permits=True):
    return SimpleNamespace(required_operation=operation, capabilities={"notes"},
                           permits=lambda _: permits)


def notes_operation(limit=3):
    return Operation("manage_notes", MappingProxyType({"action": "list"}), limit)


def notes_result():
    return {"output": "Found 30 notes:\n" + "\n".join(
        f"- [note{i}] **Title {i}**" for i in range(30)), "exit_code": 0}


def test_absent_field_preserves_legacy_behavior():
    assert al._required_safe_read_operation(None) is None
    assert al._required_safe_read_operation(SimpleNamespace()) is None


def test_actual_contract_read_operation_field_is_consumed():
    from src.turn_contract import resolve_turn_contract
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    resolved = resolve_turn_contract(
        capabilities={"notes"}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(), message="Show my first 3 notes",
    )
    block, limit = al._required_safe_read_operation(resolved)
    assert block.tool_type == "manage_notes"
    assert json.loads(block.content) == {"action": "list"}
    assert limit == 3


@pytest.mark.parametrize("name,args", [
    ("manage_notes", {"action": "add", "title": "Do not create"}),
    ("manage_notes", {}), ("manage_calendar", {"action": "delete"}),
    ("web_search", {"query": "IANA"}), ("bash", {"command": "true"}),
    ("read_file", {"path": "/workspace/test"}),
    ("list_emails", {}), ("mcp__email__send_email", {}),
    ("mcp__other__list_email_accounts", {}),
])
def test_recovery_never_acquires_mutation_search_or_shell_scope(name, args):
    assert al._required_safe_read_operation(contract(Operation(name, args))) is None


def test_operation_requires_permission_and_valid_shape():
    assert al._required_safe_read_operation(contract(notes_operation(), permits=False)) is None
    assert al._required_safe_read_operation(contract(Operation("manage_notes", "list"))) is None
    assert al._required_safe_read_operation(contract(Operation("manage_notes", {"action": "list"}, -1))) is None
    compound = contract(notes_operation())
    compound.capabilities.add("calendar")
    assert al._required_safe_read_operation(compound) is None
    resolved = al._required_safe_read_operation(contract({
        "tool_name": "manage_notes", "args": {"action": "list"}, "max_items": 2}))
    assert resolved[1] == 2


def test_warm_offers_do_not_disable_a_single_active_required_read():
    sealed = contract(notes_operation())
    sealed.capabilities = {"notes", "calendar", "email"}
    sealed.active_capabilities = frozenset({"notes"})

    resolved = al._required_safe_read_operation(sealed)

    assert resolved is not None
    assert resolved[0].tool_type == "manage_notes"


def test_compound_active_request_still_disables_terminal_read_recovery():
    sealed = contract(notes_operation())
    sealed.capabilities = {"notes", "calendar"}
    sealed.active_capabilities = frozenset({"notes", "calendar"})

    assert al._required_safe_read_operation(sealed) is None


def test_limit_removes_hidden_note_payload_and_preserves_anchors():
    block, limit = al._required_safe_read_operation(contract(notes_operation()))
    answer = al._required_read_summary(block, notes_result(), limit)
    assert answer.count("#note-") == 3
    assert "Title 3]" not in answer
    assert "ody-more" not in answer
    assert "[Title 0](#note-note0)" in answer


def test_registry_clean_raw_fallback_is_nonempty_and_bounded():
    block = al.ToolBlock("list_cookbook_servers", "{}")
    answer = al._required_read_summary(block, {"output": "AI: 6 configured servers\n- A\n- B\n- C\n- D"}, 3)
    assert "AI:" not in answer
    assert "- C" in answer and "- D" not in answer
    assert al._required_read_summary(block, {"output": ""})


def test_central_dispatcher_denial_never_owns_success_output(monkeypatch):
    from src.tool_execution import execute_tool_block, NO_TOOL_SECURITY_CONTEXT
    monkeypatch.setattr(al, "execute_tool_block", execute_tool_block)
    operation = al._required_safe_read_operation(contract(notes_operation()))
    _, result, answer = asyncio.run(al._dispatch_required_safe_read(
        operation, disabled_tools={"manage_notes"}, security_context=NO_TOOL_SECURITY_CONTEXT))
    assert result["exit_code"] != 0
    assert answer == ""


def run_terminal_branch(monkeypatch, native_calls, result, operation=None, *, max_tool_calls=3):
    """Execute the actual async branch after parsing, without a provider call."""
    source = Path(al.__file__)
    tree = ast.parse(source.read_text())
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and "_required_read" in {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)})
    executor = AsyncMock(return_value=("manage_notes: list", result))
    monkeypatch.setattr(al, "execute_tool_block", executor)
    context = object()
    policy = object()
    operation = operation or notes_operation()
    state = dict(
        _required_read=al._required_safe_read_operation(contract(operation)),
        guide_only=False, tool_events=[], max_tool_calls=max_tool_calls, native_tool_calls=native_calls,
        _required_read_native_id=al._required_read_native_id,
        _dispatch_required_safe_read=al._dispatch_required_safe_read,
        _compute_final_metrics=lambda *a, **kw: {"tool_events": a[8]},
        session_id="fixture", disabled_tools=set(), _caller_hard_denials=frozenset(), tool_policy=policy,
        owner="fixture", workspace=None, run_security=context, active_document=None,
        client_runtime_context=None, round_num=1, model="fixture-model",
        actual_endpoint_id="fixture-endpoint", actual_endpoint_label="fixture",
        requested_model="fixture-model", round_texts=[], round_models=[],
        round_endpoint_ids=[], round_endpoint_labels=[], _last_route_request_messages=[],
        _last_route_context_length=4096, real_input_tokens=20, real_output_tokens=5,
        has_real_usage=True, time_to_first_token=0.1, _t0=time.time(),
        json=json, time=time,
    )
    wrapper = ast.parse("async def run():\n    yield 'unexpected synthesis'\n").body[0]
    wrapper.body.insert(0, branch)
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, "<required-read-terminal-branch>", "exec"), state)
    async def collect():
        return [event async for event in state["run"]()]
    events = asyncio.run(collect())
    assert "unexpected synthesis" not in events
    executor.assert_awaited_once()
    assert json.loads(executor.call_args.args[0].content) == dict(operation.args)
    assert executor.call_args.args[0].tool_type == operation.tool_name
    assert executor.call_args.kwargs["security_context"] is context
    assert executor.call_args.kwargs["tool_policy"] is policy
    return events


@pytest.mark.parametrize("args", [None, "{broken", '{"action":"add","title":"Bad"}', '{"action":"list"}'])
def test_omitted_invalid_and_valid_model_args_use_immutable_read_and_finish(monkeypatch, args):
    native = [] if args is None else [{"id": "call_native", "name": "manage_notes", "arguments": args}]
    events = run_terminal_branch(monkeypatch, native, notes_result())
    payloads = [json.loads(event[6:]) for event in events if event != "data: [DONE]\n\n"]
    final = next(item for item in payloads if item.get("type") == "final_response")
    assert final["content"].count("#note-") == 3
    assert final["render_owner"] == "structured"
    assert final["replacement_scope"] == "turn"
    metrics = next(item["data"] for item in payloads if item.get("type") == "metrics")
    assert metrics["required_operation_succeeded"] is True
    assert metrics["render_owner"] == "structured"
    assert len(metrics["tool_events"]) == 1
    assert payloads[0]["call_id"] == ("call_native" if args == '{"action":"list"}' else "required-read-1")
    assert events[-1] == "data: [DONE]\n\n"


def test_failed_read_reports_failure_without_retry_or_false_success(monkeypatch):
    events = run_terminal_branch(monkeypatch, [], {"error": "permission denied", "exit_code": 1})
    assert any("permission denied" in event for event in events)
    assert any('"required_operation_succeeded": false' in event for event in events)


def test_zero_tool_budget_means_unlimited_for_required_read(monkeypatch):
    events = run_terminal_branch(
        monkeypatch, [], notes_result(), max_tool_calls=0,
    )
    assert any('"required_operation_succeeded": true' in event for event in events)


@pytest.mark.parametrize("tool", ["list_email_accounts", "mcp__email__list_email_accounts"])
@pytest.mark.parametrize("model_args", [None, "{broken", "{}"])
def test_email_metadata_read_replaces_model_call_and_terminates_once(monkeypatch, tool, model_args):
    operation = Operation(tool, MappingProxyType({}))
    native = [] if model_args is None else [{
        "id": "email-call", "function": {"name": "list_email_accounts", "arguments": model_args},
    }]
    events = run_terminal_branch(monkeypatch, native, {
        "output": "- Personal: fixture@example.test", "exit_code": 0,
    }, operation)
    payloads = [json.loads(event[6:]) for event in events[:-1]]
    assert sum(p.get("type") == "tool_output" for p in payloads) == 1
    final = next(p for p in payloads if p.get("type") == "final_response")
    assert "fixture@example.test" in final["content"]
    assert final["render_owner"] == "structured"
    assert payloads[0]["call_id"] == ("email-call" if model_args == "{}" else "required-read-1")
    assert events[-1] == "data: [DONE]\n\n"


@pytest.mark.parametrize("tool", ["list_email_accounts", "mcp__email__list_email_accounts"])
def test_email_metadata_requires_read_private_and_permission(monkeypatch, tool):
    from src.turn_contract import RequiredReadOperation
    sealed = SimpleNamespace(required_read_operation=RequiredReadOperation(tool),
                             capabilities={"email"}, permits=lambda name: name == tool)
    assert al._required_safe_read_operation(sealed)[0].tool_type == tool
    sealed.permits = lambda _: False
    assert al._required_safe_read_operation(sealed) is None
    sealed.permits = lambda _: True
    monkeypatch.setattr(al, "capabilities_for_action", lambda *a: SimpleNamespace(
        known=True, effects=frozenset()))
    assert al._required_safe_read_operation(sealed) is None
