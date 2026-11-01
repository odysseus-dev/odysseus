"""Exercise loop boundaries without booting the application or its services."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "agent_loop.py"


def load_function(name, namespace=None):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {} if namespace is None else namespace
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


def contract(*families):
    schema = {"type": "function", "function": {"name": "manage_calendar"}}
    return SimpleNamespace(
        capabilities=frozenset(families), required=frozenset(),
        offered=frozenset({"manage_calendar"}) if families else frozenset(),
        permits=lambda name: bool(families) and name == "manage_calendar",
        schemas=lambda: [schema] if families else [],
    )


@pytest.mark.parametrize("families", [("calendar",), ("calendar", "documents")])
def test_contract_work_cannot_finish_through_single_action_shortcut(families):
    allows = load_function("_contract_allows_early_completion")
    assert not allows(contract(*families))
    assert allows(None)
    assert allows(contract())


@pytest.mark.parametrize("policy", [None, SimpleNamespace(blocks=lambda _: False)])
def test_contract_rejection_needs_no_legacy_denial(policy):
    reason = load_function("_tool_rejection_reason")
    rejected = reason("bash", {"bash"}, policy, contract("calendar"))
    assert "outside" in rejected
    assert "Available tool for this turn: manage_calendar." in rejected
    assert "disabled" in reason("bash", {"bash"}, policy)


@pytest.mark.parametrize("surface,is_api,router,expected", [
    ("none", True, False, []),
    ("", False, False, []),
    ("", True, True, []),
    ("compact", True, True, ["manage_calendar"]),
    ("full", True, False, ["manage_calendar", "bash"]),
])
def test_contract_schema_transport(surface, is_api, router, expected):
    fn = schema_function()
    schemas = fn(route(surface, is_api, router))
    assert [s["function"]["name"] for s in schemas] == expected


def route(surface="compact", is_api=True, router=False):
    return {"mcp_schemas": [], "relevant_tools": {"bash"},
            "qwen38_tool_router": router, "tool_surface": surface,
            "is_api_model": is_api, "ody_qwen_finetune_model": False}


def schema_function(force_answer=False, keep_artifacts=False, guide_only=False):
    namespace = {
        "FUNCTION_TOOL_SCHEMAS": [
            {"type": "function", "function": {"name": name}}
            for name in ("manage_calendar", "bash")
        ],
        "normalized_external_tool_schemas": [], "disabled_tools": set(),
        "_pure_web_turn": False, "_native_artifact_runtime": False,
        "_drop_legacy_email_alias_schemas_when_mcp_available": lambda schemas: schemas,
        "_filter_route_tool_schemas": lambda schemas: schemas,
        "turn_contract": contract("calendar"), "guide_only": guide_only,
        "_force_answer": force_answer, "_artifact_recovery_enabled": False,
        "_artifact_creation_requested": False, "_artifact_finish_nudge_sent": False,
        "_artifact_finish_correction_seen": False,
        "_artifact_finish_post_correction_tool_used": False,
        "_artifact_finish_post_correction_mutation_seen": False,
        "_artifact_finish_convergence_sent": False,
        "_post_correction_verification_available": lambda **_: False,
        "_force_answer_keeps_artifact_tools": lambda **_: keep_artifacts,
        "_normalize_model_tool_surface": lambda value: value,
        "_tui_local_workspace_turn": lambda *a, **kw: False,
        "_retrieval_query": "", "_last_user": "", "workspace": None,
        "client_runtime_context": None,
        "_apply_tool_surface_to_schemas": lambda schemas, surface: schemas,
    }
    return load_function("_tool_schemas_for_route", namespace)


def test_synthesis_preserves_existing_artifact_exception():
    assert schema_function(force_answer=True)(route()) == []
    assert schema_function(force_answer=True, keep_artifacts=True)(route())
    assert schema_function(guide_only=True)(route()) == []


def test_early_shortcuts_are_guarded_in_the_loop():
    tree = ast.parse(SOURCE.read_text())
    triggers = {"_early_active_email_reply_body", "_early_active_email_draft_update",
                "_early_active_document_append", "_no_tool_boundary_answer", "_exact_file_edit"}
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        matched = names & triggers
        if matched:
            assert "_contract_allows_early_completion" in names
            found.update(matched)
    assert found == triggers


@pytest.mark.parametrize("eligible,summary", [(False, "Thirty notes"), (True, "")])
def test_successful_tool_continues_to_synthesis_without_eligible_answer(eligible, summary):
    """Run the actual post-tool completion branch, including its break."""
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and "_ody_notes_tool_completed" in {
                    v.id for v in ast.walk(n.test) if isinstance(v, ast.Name)})
    reached = []
    namespace = dict(
        _ody_notes_finetune_mode=False, _ody_qwen_finetune_model=False,
        _qwen38_tool_router=True, _ody_notes_tool_completed=True,
        _deterministic_terminal_eligible=eligible,
        _qwen_note_delete_title="", _qwen_note_view_title="",
        _qwen_latest_email_open=False, _qwen_latest_email_open_completed=False,
        _qwen_latest_email_action=None, _qwen_latest_email_action_completed=False,
        tool_result_records=[{"tool_name": "list_cookbook_servers", "content": "",
                              "result": {"output": "6 configured servers", "exit_code": 0}}],
        _ody_qwen_terminal_tool_summary=lambda *a, **kw: summary,
        _latest_email_action_needs_followup=lambda *a: False, _last_user="List servers",
        full_response="", logger=SimpleNamespace(info=lambda *a: None),
        mark_synthesis=lambda: reached.append(True),
    )
    # Add the actual condition's locator flags with neutral defaults.
    for name in {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}:
        if name.startswith("_qwen_"):
            namespace.setdefault(name, False)
    wrapper = ast.parse("def run():\n    for _ in range(1):\n        pass\n").body[0]
    wrapper.body[0].body = [node, ast.Expr(value=ast.Call(func=ast.Name(id="mark_synthesis", ctx=ast.Load()), args=[], keywords=[]))]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    list(namespace["run"]())
    assert reached == [True]
