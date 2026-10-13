"""Regression coverage for provider-bound contract conversation preservation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent_loop import (
    _build_system_prompt, _contract_prompt_domains,
    _contract_allows_early_completion,
    _contract_allows_single_action_terminal, _contract_mutation_signature,
    _request_forbids_execution_retry,
)


@pytest.mark.parametrize("family,tool", [
    ("notes", "manage_notes"), ("calendar", "manage_calendar"),
    ("tasks", "manage_tasks"), ("documents", "manage_documents"),
])
def test_actual_preheretic_prompt_preserves_referential_antecedent(family, tool):
    initial = f"List my {family}. Return at most three titles."
    followup = "List those again, at most three. Read-only; do not change data or send messages."
    messages = [
        {"role": "system", "content": "Superseded routing", "_agent_injected": "prompt"},
        {"role": "user", "content": initial},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {
                "name": tool, "arguments": '{"action":"list"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "Fixture A; Fixture B"},
        {"role": "assistant", "content": "Fixture A; Fixture B"},
        {"role": "user", "content": followup},
    ]
    before = deepcopy(messages)
    output, schemas = _build_system_prompt(
        messages, "odysseus-qwen3.5-tools-pre-heretic", None, None,
        relevant_tools={tool}, preserve_conversation=True,
    )
    assert output[1:] == messages[1:]
    assert schemas == []
    assert messages == before
    assert "Superseded routing" not in str(output)
    assert [m["content"] for m in output if m["role"] == "user"] == [initial, followup]


def test_legacy_standalone_router_still_uses_latest_only():
    messages = [{"role": "user", "content": "List notes"},
                {"role": "assistant", "content": "A, B"},
                {"role": "user", "content": "List those again"}]
    output, _ = _build_system_prompt(messages, "odysseus-qwen3.5-tools-pre-heretic", None, None)
    assert output[1:] == messages[-1:]


def test_preserve_original_system_and_non_system_injected_evidence():
    original = {"role": "system", "content": "Trusted original instruction"}
    evidence = {"role": "tool", "content": "Untrusted source", "tool_call_id": "x",
                "_agent_injected": "context"}
    call = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "x", "type": "function", "function": {"name": "manage_notes", "arguments": "{}"}}]}
    messages = [{"role": "system", "content": "Old merged prompt", "_agent_injected": "merged_prompt",
                 "_agent_base_message": original}, call, evidence,
                {"role": "user", "content": "Continue"}]
    output, _ = _build_system_prompt(messages, "odysseus-qwen3.5-tools-pre-heretic", None, None,
                                     preserve_conversation=True)
    assert output[1:] == [original, call, evidence, messages[-1]]


def test_contract_domains_do_not_inherit_email_from_negative_wording():
    contract = SimpleNamespace(capabilities={"notes", "documents"})
    assert _contract_prompt_domains(contract) == {"notes_calendar_tasks", "documents"}


@pytest.mark.parametrize("family", ["notes", "calendar", "tasks", "documents", "cookbook_admin"])
def test_successful_acquisition_does_not_grant_terminal_ownership(family):
    contract = SimpleNamespace(capabilities={family}, required=set(), offered={"fixture_tool"})
    assert not _contract_allows_early_completion(contract)


def test_compound_turn_cannot_finish_after_one_family():
    assert not _contract_allows_single_action_terminal(SimpleNamespace(capabilities={"notes", "calendar"}))
    assert _contract_allows_single_action_terminal(SimpleNamespace(capabilities={"notes"}))
    assert _contract_allows_single_action_terminal(None)


def test_write_dedupe_preserves_reads_and_distinct_writes():
    contract = SimpleNamespace(capabilities={"notes", "calendar"})
    def signature(content, tool="manage_notes"):
        return _contract_mutation_signature(SimpleNamespace(tool_type=tool, content=content), contract)
    first = signature('{"action":"add","title":"Lunch"}')
    assert first is not None
    assert first == signature('{ "title": "Lunch", "action": "add" }')
    assert first != signature('{"action":"add","title":"Dinner"}')
    assert signature('{"action":"list"}') is None
    assert signature('{"action":"create","title":"Lunch"}', "manage_calendar") is not None


@pytest.mark.parametrize("contract", [
    None,
    SimpleNamespace(capabilities={"tasks"}),
])
def test_exact_mutation_dedupe_applies_to_legacy_and_single_family_turns(contract):
    block = SimpleNamespace(
        tool_type="manage_tasks",
        content='{"action":"delete","task_id":"11111111-1111-1111-1111-111111111111"}',
    )
    assert _contract_mutation_signature(block, contract) == (
        "manage_tasks",
        '{"action":"delete","task_id":"11111111-1111-1111-1111-111111111111"}',
    )
    read = SimpleNamespace(tool_type="manage_tasks", content='{"action":"list"}')
    assert _contract_mutation_signature(read, contract) is None


@pytest.mark.parametrize("text", [
    "Run this read-only test once and report the result.",
    "Execute the command one time; show stdout.",
    "Run this command. Do not retry automatically.",
    "Try it, but don't rerun the command again.",
])
def test_explicit_single_execution_bound_is_detected(text):
    assert _request_forbids_execution_retry(text)


def test_unbounded_execution_request_does_not_invent_retry_limit():
    assert not _request_forbids_execution_retry(
        "Run the command and recover if it fails."
    )


def test_tool_success_flags_do_not_terminate_compound_turn():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/agent_loop.py").read_text())
    flags = {"_qwen_explicit_effectful_completed", "_native_document_tool_completed",
             "_ody_doc_tool_completed", "_doc_stream_create_completed"}
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not any(isinstance(n, ast.Break) for n in ast.walk(node)):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if names & flags:
            assert "_contract_allows_single_action_terminal" in names
            seen.update(names & flags)
    assert seen == flags
