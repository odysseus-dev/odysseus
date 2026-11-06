from types import SimpleNamespace

from scripts.eval_odysseus_tool_use import (
    _hard_turn_timeout,
    _command_contract_ok,
    _reported_model,
    _sse_events,
    _stream_exception_if_empty,
    _summary_exit_code,
    _tool_sequence_matches,
    run_case,
)
from scripts.eval_odysseus_crud import build_summary
from scripts.eval_qwen35_tool_router_extended import cleanup_notes


def test_shared_eval_runner_accepts_extended_evaluator_timeout_name() -> None:
    assert _hard_turn_timeout(SimpleNamespace(hard_case_timeout=30)) == 30.0


def test_shared_eval_runner_defaults_without_timeout_attribute() -> None:
    assert _hard_turn_timeout(SimpleNamespace()) == 0.0


def test_eval_summary_reports_selected_model_over_discovered_model() -> None:
    args = SimpleNamespace(selected_model="router/v12", model="router/v13")
    assert _reported_model(args) == "router/v12"


def test_crud_summary_survives_partial_or_infrastructure_records() -> None:
    summary = build_summary(
        [{
            "workflow": "memory",
            "turns": [{
                "native_call_ok": None,
                "first_action_ok": None,
                "tool_count_ok": None,
                "exact_args_ok": None,
                "execution_ok": None,
                "duplicate_textual_call": None,
                "stream_errors": [{"type": "infra_exception"}],
            }],
        }],
        "router",
        "fixture",
    )
    assert summary["turns"] == 1
    assert summary["native_success"] == 0
    assert summary["stream_errors"] == 1


def test_eval_sse_parser_preserves_standard_event_name() -> None:
    class Response:
        def iter_lines(self):
            return iter((
                "event: error",
                'data: {"error":"upstream unavailable","status":502}',
                "",
            ))

    assert list(_sse_events(Response())) == [
        {
            "type": "error",
            "error": "upstream unavailable",
            "status": 502,
        }
    ]


def test_empty_sse_stream_is_not_treated_as_a_valid_turn() -> None:
    assert _stream_exception_if_empty([], [], None) == "empty SSE stream"
    assert _stream_exception_if_empty([], [], "upstream error") == "upstream error"


def test_eval_summary_exit_code_rejects_failed_execution() -> None:
    failed = {"execution_ok": False, "response_quality_ok": True, "duplicate_textual_call": False}
    passed = {"execution_ok": True, "response_quality_ok": True, "duplicate_textual_call": False}
    assert _summary_exit_code([failed]) == 1
    assert _summary_exit_code([passed]) == 0


def test_eval_tool_contract_accepts_ordered_coding_sequence() -> None:
    assert _tool_sequence_matches(
        ["read_file", "edit_file"], "read_file->edit_file"
    )
    assert not _tool_sequence_matches(
        ["edit_file", "read_file"], "read_file->edit_file"
    )


def test_notes_search_contract_rejects_broad_list_with_ignored_search_key() -> None:
    assert not _command_contract_ok(
        "notes_search",
        [{
            "tool": "manage_notes",
            "command": '{"action":"list","search":"ODY-EVAL-TOOL-NOTES-SEARCH"}',
        }],
    )
    assert _command_contract_ok(
        "notes_search",
        [{
            "tool": "manage_notes",
            "command": '{"action":"search","text":"ODY-EVAL-TOOL-NOTES-SEARCH"}',
        }],
    )


def test_fixture_search_contracts_reject_broad_lists() -> None:
    assert not _command_contract_ok(
        "documents_search_fixture",
        [{"tool": "manage_documents", "command": '{"action":"list"}'}],
    )
    assert not _command_contract_ok(
        "documents_search_fixture",
        [{"tool": "manage_documents", "command": '{"action":"find","query":"ODY-EVAL-TOOL-DOCUMENT-SEARCH"}'}],
    )
    assert _command_contract_ok(
        "documents_search_fixture",
        [
            {"tool": "manage_documents", "command": '{"action":"find","query":"ODY-EVAL-TOOL-DOCUMENT-SEARCH"}'},
            {"tool": "manage_documents", "command": '{"action":"read","document_id":"doc-fixture"}'},
        ],
    )
    assert not _command_contract_ok(
        "tasks_search_fixture",
        [{"tool": "manage_tasks", "command": '{"action":"list"}'}],
    )
    assert _command_contract_ok(
        "tasks_search_fixture",
        [{"tool": "manage_tasks", "command": '{"action":"list","name":"ODY-EVAL-TOOL-TASK-SEARCH"}'}],
    )
    assert not _command_contract_ok(
        "calendar_search_fixture",
        [{"tool": "manage_calendar", "command": '{"action":"list_events","query":"ODY-EVAL-TOOL-CALENDAR-SEARCH"}'}],
    )
    assert _command_contract_ok(
        "calendar_search_fixture",
        [{
            "tool": "manage_calendar",
            "command": (
                '{"action":"list_events","query":"ODY-EVAL-TOOL-CALENDAR-SEARCH",'
                '"start":"2026-08-21","end":"2026-08-23"}'
            ),
        }],
    )


def test_eval_local_runtime_context_sends_tui_workspace_form_fields(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"id": "session-1"}

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(("data: [DONE]", ""))

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *_args):
            return False

    class Client:
        def post(self, url, data=None, **_kwargs):
            if url.endswith("/api/session"):
                return Response()
            captured.update(data or {})
            return Response()

        def stream(self, _method, _url, data=None, **_kwargs):
            captured.update(data or {})
            return Stream()

        def delete(self, *_args, **_kwargs):
            return Response()

    args = SimpleNamespace(
        base_url="http://backend",
        endpoint="http://model/v1",
        selected_endpoint_url="http://model/v1/chat/completions",
        endpoint_id="router-1",
        selected_model="router",
        model="router",
        prompt_mode="auto",
        client_runtime_context={
            "surface": "odysseus-tui",
            "session_cwd": "/workspace/project",
        },
        timeout=5,
        auto_approve=False,
    )

    monkeypatch.setattr(
        "scripts.eval_odysseus_tool_use._cookie",
        lambda _path: "cookie",
    )
    run_case(Client(), args, "local", "inspect this repo", "host_shell")

    assert captured["cwd"] == "/workspace/project"
    assert captured["workspace"] == "/workspace/project"


def test_eval_local_runtime_context_accepts_cwd_alias(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"id": "session-1"}

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(("data: [DONE]", ""))

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *_args):
            return False

    class Client:
        def post(self, url, data=None, **_kwargs):
            if url.endswith("/api/session"):
                return Response()
            captured.update(data or {})
            return Response()

        def stream(self, _method, _url, data=None, **_kwargs):
            captured.update(data or {})
            return Stream()

        def delete(self, *_args, **_kwargs):
            return Response()

    args = SimpleNamespace(
        base_url="http://backend",
        endpoint="http://model/v1",
        selected_endpoint_url="http://model/v1/chat/completions",
        endpoint_id="router-1",
        selected_model="router",
        model="router",
        prompt_mode="auto",
        client_runtime_context={
            "surface": "odysseus-tui",
            "cwd": "/workspace/project",
        },
        timeout=5,
        auto_approve=False,
    )

    monkeypatch.setattr(
        "scripts.eval_odysseus_tool_use._cookie",
        lambda _path: "cookie",
    )
    run_case(Client(), args, "local", "inspect this repo", "host_shell")

    assert captured["cwd"] == "/workspace/project"
    assert captured["workspace"] == "/workspace/project"


def test_extended_eval_cleanup_only_removes_tagged_fixture_notes() -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "notes": [
                    {"id": "fixture", "title": "ODY-EVAL-EXT-CREATE"},
                    {"id": "user", "title": "Keep this note"},
                    {"id": "", "title": "ODY-EVAL-EXT-NO-ID"},
                ]
            }

    class Client:
        def __init__(self):
            self.deleted = []

        def get(self, url, timeout):
            return Response()

        def delete(self, url, timeout):
            self.deleted.append(url)

    client = Client()
    cleanup_notes(client, "http://backend")

    assert client.deleted == ["http://backend/api/notes/fixture"]
