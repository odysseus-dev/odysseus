import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts" / "odysseus_conversation_qa.py"
SPEC = importlib.util.spec_from_file_location("odysseus_conversation_qa", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_json_parser_accepts_fenced_teacher_output():
    assert MODULE._json_from_text('```json\n{"ok": true}\n```') == {"ok": True}


def test_json_parser_recovers_first_complete_gateway_object():
    assert MODULE._json_from_text('result: {"ok": true}{"ignored": true}') == {"ok": True}


def test_json_parser_prefers_a_complete_judge_verdict_over_nested_schema():
    text = '{broken "schema":{"verdict":"pass|fail|uncertain"} ' \
           'answer={"verdict":"pass","score":91}'
    assert MODULE._json_from_text(text)["verdict"] == "pass"


def test_compact_turn_retains_contract_tools_result_and_metrics():
    result = MODULE.compact_turn("Show notes", "list notes", [
        {"type": "turn_contract", "capabilities": ["notes"], "offered": ["manage_notes"]},
        {"type": "tool_start", "tool": "manage_notes", "command": '{"action":"list"}'},
        {"type": "tool_output", "tool": "manage_notes", "output": "fixture result"},
        {"type": "metrics", "data": {"round_texts": ["Here are your notes."], "input_tokens": 10}},
        {"type": "message_saved"},
    ])
    assert result["contract"]["capabilities"] == ["notes"]
    assert result["tool_calls"][0]["tool"] == "manage_notes"
    assert result["final"] == "Here are your notes."
    assert result["saved"] is True


def test_auditor_rejects_referent_selected_from_invisible_overflow():
    result = {
        "turns": [
            {"user": "list three notes"},
            {"user": "show me the newest one"},
        ],
        "observed": [
            {"final": "[Visible](#note-visible-id)", "tool_calls": []},
            {"user": "show me the newest one", "tool_calls": [{
                "tool": "manage_notes",
                "command": json.dumps({"action": "view", "id": "hidden-id"}),
            }]},
        ],
    }

    failure = MODULE.ungrounded_visible_referent(result)

    assert failure["failure_category"] == "ungrounded_visible_referent"
    assert failure["failed_turns"] == [2]


def test_auditor_accepts_referent_selected_from_visible_anchor():
    result = {
        "turns": [{"user": "list notes"}, {"user": "show the first one"}],
        "observed": [
            {"final": "[Visible](#note-note-123)", "tool_calls": []},
            {"user": "show the first one", "tool_calls": [{
                "tool": "manage_notes",
                "command": json.dumps({"action": "view", "id": "#note-note-123"}),
            }]},
        ],
    }

    assert MODULE.ungrounded_visible_referent(result) is None


def test_create_session_disables_background_memory_extraction():
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response({"id": "qa-session"}) if url.endswith("/api/session") else Response({})

    args = SimpleNamespace(
        base_url="http://127.0.0.1:7011", target_endpoint_id="endpoint", target_model="model",
    )
    session_id = MODULE.create_session(Client(), args, {"family": "notes", "id": "seed"})

    assert session_id == "qa-session"
    assert calls[1] == (
        "http://127.0.0.1:7011/api/session/qa-session/memory-extraction",
        {"json": {"enabled": False}, "timeout": 30},
    )


def test_run_turn_selects_model_specific_odysseus_runtime():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(())

    class Client:
        def stream(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return Response()

    args = SimpleNamespace(
        base_url="http://127.0.0.1:7011", target_endpoint_id="endpoint",
        target_model="model", timeout=30, routing_experiment="recent_model_choice",
    )
    MODULE.run_turn(Client(), args, "session", "show notes", family="notes")

    assert captured["headers"]["x-odysseus-routing-experiment"] == "recent_model_choice"


def test_family_seeds_are_multiturn_and_non_destructive():
    forbidden = ("delete ", "send this now", "torrent")
    for spec in MODULE.FAMILY_SEEDS.values():
        assert spec["seeds"]
        for flow in spec["seeds"]:
            assert len(flow) >= 2
            assert not any(word in prompt.lower() for word in forbidden for prompt in flow)


def test_flow_may_mutate_uses_expected_action_and_imperative_wording():
    for user in (
        "jot a reminder to call the dentist tomorow at 9am",
        "if thats a gap, jot it in my notes so i dont forget",
        "ok ping me every morning at 7 with the pollen level",
        "shift that one 45 min later if it won't collide with anything",
        "tick off the one about the privacy policy",
        "get rid of that week ahead note",
        "help me draft a reply to lisa",
        "ok scrap it",
        "switch it to personal",
        "huh ok, take dan off it",
    ):
        assert MODULE.flow_may_mutate({
            "family": "switching",
            "turns": [{"user": user, "expect": "Perform the requested mutation."}],
        })
    assert MODULE.flow_may_mutate({
        "family": "cookbook_admin",
        "turns": [{
            "user": "spin up a scratch chat named relay check",
            "expect": "Creates the requested session",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "notes",
        "turns": [{"user": "put that in my notes", "expect": "manage_notes action='add'"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{
            "user": "sticck that in a note for me",
            "expect": "Use manage_notes add",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "email",
        "turns": [{"user": "please draft a reply", "expect": "Creates a reviewable reply"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "shell_files",
        "turns": [{"user": "ok cool, save that output to a file", "expect": "Writes the file"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{"user": "nice — save how u did that as a skill", "expect": "Adds a skill"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "shell_files",
        "turns": [{
            "user": "drop that output into a file called shellcheck.txt",
            "expect": "Use write_file to save the captured output.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{
            "user": "ok remind me to check this again tomorow morning",
            "expect": "Create a scheduled reminder.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "shell_files",
        "turns": [
            {"user": "use bash to do a read-only check", "expect": "bash runs the exact command"},
            {"user": "run it again", "expect": "bash re-runs the command"},
            {"user": "save that output to hostcheck.txt in the workspace",
             "expect": "write_file creating hostcheck.txt with the captured command output"},
        ],
    })
    assert MODULE.flow_may_mutate({
        "family": "ui",
        "turns": [
            {"user": "open my calendar", "expect": "Open the panel"},
            {"user": "whats on next week?", "expect": "List events"},
            {"user": "now open notes and make a short note called week ahead",
            "expect": "Open notes and create the note"},
        ],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{"user": "note down which one is for work so i dont forget", "expect": "Save it"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "shell_files",
        "turns": [{"user": "in a temp dir make two files and calculate checksums", "expect": "Inspect them"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "shell_files",
        "turns": [{"user": "also write the report", "expect": "Create report"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "cookbook_admin",
        "turns": [{"user": "Launch my SD3.5 preset.", "expect": "Call serve_preset"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "calendar",
        "turns": [{
            "user": "clear my reminder for tomorrow's lunch",
            "expect": "Use manage_calendar update_event to remove the reminder",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "cookbook_admin",
        "turns": [{"user": "Stop the first server.", "expect": "Call stop_served_model"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "notes",
        "turns": [{
            "user": "keep that somewhere for me",
            "expect": "Use manage_notes add with the summary",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "cookbook_admin",
        "turns": [{
            "user": "get those weights locally",
            "expect": "Call download_model and return the session ID",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{
            "user": "cool, grab Qwen/Qwen3-8B locally but only the *.safetensors files",
            "expect": "Call download_model and return the tracked session ID",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "switching",
        "turns": [{
            "user": "ok can u drop all that into a note called model shortlist",
            "expect": "Create the note",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "tasks",
        "turns": [{
            "user": "set up a daily remider at 9am to review the SFT traces",
            "expect": "Create a scheduled task using manage_tasks.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "documents",
        "turns": [{
            "user": "can you expand this document",
            "expect": "Read the active document, then apply the expansion edits.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "memory",
        "turns": [{
            "user": "hey can you stash a temporary note for me",
            "expect": "Calls the memory tool family to add a new memory.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "skills",
        "turns": [{
            "user": "nope, so start a draft skil called trace-audit",
            "expect": "Uses the skills tool add action creating a draft skill.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "tasks",
        "turns": [
            {"user": "is the notes recap task still paused?", "expect": "List it."},
            {"user": "turn it back on please", "expect": "Call manage_tasks with resume."},
        ],
    })
    assert MODULE.flow_may_mutate({
        "family": "documents",
        "turns": [{
            "user": "with that log still open, tack this on the end: keep the evidence",
            "expect": "Append the sentence through a targeted edit.",
        }],
    })
    assert not MODULE.flow_may_mutate({
        "family": "notes",
        "turns": [{"user": "which note did I create most recently?", "expect": "Read-only answer"}],
    })


def test_flow_may_mutate_treats_new_research_as_durable_but_not_status_checks():
    assert MODULE.flow_may_mutate({
        "family": "research",
        "turns": [{"user": "research why Boston terriers are great", "expect": "Starts research"}],
    })


def test_read_only_negative_mutation_language_does_not_trigger_isolation():
    assert not MODULE.flow_may_mutate({
        "family": "notes",
        "turns": [{
            "user": "List three notes. Read-only; don't change or send anything.",
            "expect": (
                "Call manage_notes with action=list; do not edit, delete, or create anything."
            ),
        }],
    })
    assert not MODULE.flow_may_mutate({
        "family": "calendar",
        "turns": [{
            "user": "Check Friday without changing my calendar.",
            "expect": "Use manage_calendar list_events. No edits.",
        }],
    })
    assert not MODULE.flow_may_mutate({
        "family": "memory",
        "turns": [{
            "user": "List my memories.",
            "expect": (
                "Call manage_memory with action=list; does not add, edit, or delete anything."
            ),
        }],
    })


def test_positive_mutation_before_or_after_safety_clause_is_retained():
    assert MODULE.flow_may_mutate({
        "family": "email",
        "turns": [{
            "user": "Draft a reply, but do not send it.",
            "expect": "Create a reviewable draft without sending it.",
        }],
    })
    assert MODULE.flow_may_mutate({
        "family": "notes",
        "turns": [{
            "user": "Don't delete the old note; create a new one called scratch.",
            "expect": "Use manage_notes add.",
        }],
    })


def test_fixture_isolation_admits_only_owner_scoped_local_mutations():
    assert MODULE.flow_has_locally_restorable_mutation({
        "family": "notes",
        "turns": [{"user": "add a note called scratch", "expect": "manage_notes add"}],
    })
    assert MODULE.flow_has_locally_restorable_mutation({
        "family": "tasks",
        "turns": [{"user": "pause the recap task", "expect": "manage_tasks pause"}],
    })
    assert not MODULE.flow_has_locally_restorable_mutation({
        "family": "email",
        "turns": [{"user": "send that reply", "expect": "send_email"}],
    })
    assert not MODULE.flow_has_locally_restorable_mutation({
        "family": "shell_files",
        "turns": [{"user": "write it to result.txt", "expect": "write_file"}],
    })
    assert not MODULE.flow_has_locally_restorable_mutation({
        "family": "notes",
        "turns": [{"user": "show my notes", "expect": "manage_notes list"}],
    })


def test_fixture_isolated_replay_restores_around_every_flow(monkeypatch, tmp_path):
    baseline = {"format": "fixture"}
    restores = []
    replayed = []
    monkeypatch.setattr(MODULE, "snapshot_owner", lambda *_args: baseline)
    monkeypatch.setattr(
        MODULE, "restore_owner",
        lambda db, snapshot, owner, data: restores.append((db, snapshot, owner, data)),
    )
    monkeypatch.setattr(
        MODULE, "replay_flow",
        lambda flow, _args, _cookie: replayed.append(flow["id"]) or {"id": flow["id"]},
    )
    args = SimpleNamespace(
        fixture_db=tmp_path / "app.db", owner="sft_alex_creator", data_dir=tmp_path,
    )

    results = MODULE.replay_flows_with_fixture_isolation(
        [{"id": "one"}, {"id": "two"}], args, "cookie",
    )

    assert replayed == ["one", "two"]
    assert results == [{"id": "one"}, {"id": "two"}]
    assert len(restores) == 5
    assert all(row[1] is baseline and row[2] == "sft_alex_creator" for row in restores)


def test_fixture_isolated_replay_restores_after_unexpected_failure(monkeypatch, tmp_path):
    restores = []
    monkeypatch.setattr(MODULE, "snapshot_owner", lambda *_args: {"format": "fixture"})
    monkeypatch.setattr(MODULE, "restore_owner", lambda *_args: restores.append(1))
    monkeypatch.setattr(
        MODULE, "replay_flow",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    args = SimpleNamespace(
        fixture_db=tmp_path / "app.db", owner="sft_alex_creator", data_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="unexpected"):
        MODULE.replay_flows_with_fixture_isolation([{"id": "one"}], args, "cookie")

    assert len(restores) == 3
    assert not MODULE.flow_may_mutate({
        "family": "research",
        "turns": [{"user": "is the research still running?", "expect": "Checks status"}],
    })
    assert MODULE.flow_may_mutate({
        "family": "research",
        "turns": [
            {"user": "research Boston terriers", "expect": "Starts a job"},
            {"user": "is it still running?", "expect": "Checks status"},
        ],
    })


def test_orphaned_opening_followup_is_not_a_valid_standalone_flow():
    assert MODULE.flow_has_orphaned_opening_followup({
        "turns": [{
            "user": "rerun that marker and hostname command",
            "expect": "Call bash again with the same read-only command.",
        }],
    })
    assert MODULE.flow_has_orphaned_opening_followup({
        "turns": [{"user": "same result as last time?", "expect": "Compare it."}],
    })
    assert MODULE.flow_has_orphaned_opening_followup({
        "turns": [{"user": "pull those up again", "expect": "Repeat it."}],
    })
    assert MODULE.flow_has_orphaned_opening_followup({
        "turns": [{
            "user": "your previous reply got cut off while reading that thread; pick up where you left off",
            "expect": "Continue the missing prior browser read.",
        }],
    })


def test_self_contained_opening_is_not_rejected_for_later_followups():
    assert not MODULE.flow_has_orphaned_opening_followup({
        "turns": [{
            "user": "pull my calendar events up again",
            "expect": "List the explicitly named calendar events.",
        }],
    })


@pytest.mark.parametrize("tool", [
    "python", "read_file", "write_file", "edit_file", "apply_patch",
])
def test_webui_sft_audit_rejects_native_workspace_only_expectations(tool):
    flow = {
        "source_seed_id": f"native-only-{tool}",
        "family": "shell_files",
        "turns": [{"user": "inspect the workspace", "expect": f"Use {tool} to do it"}],
    }
    assert not MODULE.flow_is_auditable(flow)
    assert not MODULE.flow_has_orphaned_opening_followup({
        "turns": [
            {"user": "print a marker and the hostname", "expect": "Run bash."},
            {"user": "same result as last time?", "expect": "Compare it."},
        ],
    })
    assert not MODULE.flow_has_orphaned_opening_followup({
        "turns": [{"user": "rerun the nightly backup task", "expect": "Run it."}],
    })


def test_known_ambiguous_generated_seed_is_quarantined():
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "56b28e74-c890-46a9-a7f5-e3b96b0c8032:2",
        "turns": [{"user": "open up sprint-notes so i can see it"}],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "f62677e6-9bd5-4528-9112-d45e37c9fa6a:2",
        "turns": [
            {"user": "Can you pull up my calendar for the next seven days?"},
            {"user": "Anything Friday afternoon I should prep for?"},
        ],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "8ec45076-dfe3-4751-9954-0cc2fe4a7b3a:1",
        "turns": [{"user": "remind me tomorrow", "expect": "Use manage_tasks."}],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "f313b13a-efe9-455f-9d5e-216a4f6d27a5:1",
        "turns": [
            {"user": "list three memories"},
            {"user": "when did i save the first one?", "expect": "Inspect its date."},
        ],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "a2830b66-79e1-4bf9-990d-5b35502f1198:1",
        "turns": [{"user": "what do i have coming up?"}],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "68e92e46-5274-48cf-8eb0-846f3036aaf2:1",
        "turns": [{"user": "can you expand this document"}],
    })
    assert not MODULE.flow_is_auditable({
        "source_seed_id": "4522013c-db9e-498c-aeb6-d6fd3dcb9a9a:1",
        "turns": [{
            "user": "anything on my calendar today? just the headlines",
            "expect": "Return at most three titles.",
        }],
    })
    assert MODULE.flow_is_auditable({
        "source_seed_id": "valid-seed:1",
        "turns": [{"user": "open the document named sprint-notes"}],
    })


def test_safe_judge_turns_teacher_failure_into_uncertain(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(MODULE, "judge_flow", fail)
    result = MODULE.safe_judge_flow(None, {"family": "notes"})
    assert result["verdict"] == "uncertain"
    assert result["failure_category"] == "judge_unavailable"


def test_safe_judge_quarantines_replay_transport_failure_without_calling_teacher(monkeypatch):
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("teacher must not judge missing replay evidence")

    monkeypatch.setattr(MODULE, "judge_flow", should_not_run)
    result = MODULE.safe_judge_flow(None, {
        "turns": [{"user": "show notes"}],
        "observed": [{"errors": ["ConnectError('[Errno 111] Connection refused')"]}],
    })

    assert result["verdict"] == "uncertain"
    assert result["failure_category"] == "replay_transport_unavailable"
    assert result["reproduction"] == ["show notes"]


def test_safe_judge_uses_fallback_model(monkeypatch):
    calls = []

    def judge(endpoint, _result):
        calls.append(endpoint.model)
        if endpoint.model == "deepseek":
            raise RuntimeError("bad json")
        return {"verdict": "pass", "score": 90}

    monkeypatch.setattr(MODULE, "judge_flow", judge)
    primary = MODULE.TeacherEndpoint("http://judge", "key", "deepseek")
    fallback = MODULE.TeacherEndpoint("http://judge", "key", "kimi")

    result = MODULE.safe_judge_flow(primary, {"family": "notes"}, fallback)

    assert result["verdict"] == "pass"
    assert result["judge_fallback"] == "kimi"
    assert calls == ["deepseek", "kimi"]


def test_safe_judge_bounds_provider_attempts_per_model(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise MODULE.httpx.ReadTimeout("judge stalled")

    monkeypatch.setattr(MODULE.httpx, "post", unavailable)
    primary = MODULE.TeacherEndpoint("https://judge.invalid/v1", "secret", "primary")
    fallback = MODULE.TeacherEndpoint("https://judge.invalid/v1", "secret", "fallback")

    result = MODULE.safe_judge_flow(primary, {
        "family": "notes",
        "turns": [{"user": "show my notes", "expect": "Call manage_notes."}],
        "observed": [{
            "user": "show my notes",
            "expected": "Call manage_notes.",
            "contract": {"offered": ["manage_notes"]},
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "final": "I cannot access notes.",
        }],
    }, fallback)

    assert result["verdict"] == "uncertain"
    assert len(calls) == 2


def test_judge_rejects_placeholder_evidence(monkeypatch):
    monkeypatch.setattr(MODULE, "teacher_json", lambda *_args, **_kwargs: {
        "verdict": "fail", "score": 55, "owner": "model_sft",
        "failure_category": "missing_account_scope", "summary": "...",
        "failed_turns": [2], "evidence": ["..."], "reproduction": ["show mail"],
    })

    try:
        MODULE.judge_flow(None, {"turns": [], "observed": []})
    except RuntimeError as exc:
        assert "placeholder" in str(exc)
    else:
        raise AssertionError("placeholder verdict was accepted")


def test_judge_retries_a_malformed_top_level_list_once(monkeypatch):
    calls = []

    def teacher(_endpoint, payload, **_kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return [1]
        return {
            "verdict": "pass", "score": 96, "owner": "none",
            "failure_category": "", "summary": "The requested behavior succeeded.",
            "failed_turns": [], "evidence": ["The required tool call completed."],
            "reproduction": ["show my notes"],
        }

    monkeypatch.setattr(MODULE, "teacher_json", teacher)

    result = MODULE.judge_flow(None, {"turns": [], "observed": []})

    assert result["verdict"] == "pass"
    assert len(calls) == 2
    assert "complete_odysseus_tool_catalog" in calls[0]
    assert "complete_odysseus_tool_catalog" not in calls[1]
    assert calls[1]["previous_invalid_output"] == [1]


def test_judge_does_not_accept_a_repeated_malformed_list(monkeypatch):
    calls = []

    def teacher(*_args, **_kwargs):
        calls.append(1)
        return []

    monkeypatch.setattr(MODULE, "teacher_json", teacher)

    try:
        MODULE.judge_flow(None, {"turns": [], "observed": []})
    except RuntimeError as exc:
        assert "schema correction failed" in str(exc)
    else:
        raise AssertionError("malformed verdict was accepted")
    assert len(calls) == 2


def test_safe_judge_corrects_missing_unoffered_tool_to_harness_routing(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "model_sft",
        "failure_category": "missing_required_tool_call", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "observed": [{
            "contract": {"capabilities": [], "offered": []},
            "tool_calls": [],
        }],
    })

    assert result["owner"] == "harness_routing"
    assert result["judge_reported_owner"] == "model_sft"


def test_safe_judge_corrects_false_success_when_named_tool_was_unoffered(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "model_sft",
        "failure_category": "false_success", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "turns": [{"user": "run hostname", "expect": "Use bash and report output."}],
        "observed": [{"contract": {"offered": []}, "tool_calls": [], "final": "done"}],
    })

    assert result["owner"] == "harness_routing"
    assert result["judge_reported_owner"] == "model_sft"


def test_safe_judge_assigns_canonical_list_limit_failure_to_harness(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "model_sft",
        "failure_category": "item_limit_violation", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "turns": [{"user": "show my notes, three at most", "expect": "List at most three notes."}],
        "observed": [{
            "user": "show my notes, three at most",
            "contract": {"offered": ["manage_notes"]},
            "tool_calls": [{"tool": "manage_notes", "command": '{"action":"list"}'}],
            "tool_results": [{"tool": "manage_notes", "output": "four notes"}],
            "final": "One\nTwo\nThree\nFour",
        }],
    })

    assert result["owner"] == "harness_execution"
    assert result["failure_category"] == "canonical_result_limit"
    assert result["judge_reported_owner"] == "model_sft"


def test_safe_judge_corrects_wrong_choice_when_expected_tool_was_offered(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "harness_routing",
        "failure_category": "wrong_tool_family", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "turns": [{"expect": "Call web_fetch on the selected article URL."}],
        "observed": [{
            "contract": {"offered": ["web_fetch", "private_browser"]},
            "tool_calls": [{"tool": "private_browser", "command": "{}"}],
        }],
    })

    assert result["owner"] == "model_sft"
    assert result["judge_reported_owner"] == "harness_routing"


def test_safe_judge_corrects_missing_call_when_expected_tool_was_offered(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "harness_execution",
        "failure_category": "missing_tool_call", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "turns": [{"expect": "Call list_cookbook_servers again."}],
        "observed": [{
            "contract": {"offered": ["list_cookbook_servers", "list_served_models"]},
            "tool_calls": [],
        }],
    })

    assert result["owner"] == "model_sft"
    assert result["judge_reported_owner"] == "harness_execution"


def test_safe_judge_keeps_missing_offered_tool_model_owned(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "model_sft",
        "failure_category": "missing_tool_call", "failed_turns": [1],
    })
    result = MODULE.safe_judge_flow(None, {
        "observed": [{
            "contract": {"capabilities": ["notes"], "offered": ["manage_notes"]},
            "tool_calls": [],
        }],
    })

    assert result["owner"] == "model_sft"
    assert "judge_reported_owner" not in result


def test_safe_judge_does_not_blame_harness_for_empty_followup_after_model_miss(monkeypatch):
    monkeypatch.setattr(MODULE, "judge_flow", lambda *_args, **_kwargs: {
        "verdict": "fail", "owner": "model_sft",
        "failure_category": "missing_required_tool_call", "failed_turns": [1, 2],
    })
    result = MODULE.safe_judge_flow(None, {
        "turns": [
            {"expect": "Call manage_email_state action=list_blocked."},
            {"expect": "Answer from the prior blocked-sender evidence."},
        ],
        "observed": [
            {
                "contract": {"offered": ["mcp__email__manage_email_state"]},
                "tool_calls": [],
            },
            {"contract": {"offered": []}, "tool_calls": []},
        ],
    })

    assert result["owner"] == "model_sft"
    assert "judge_reported_owner" not in result


def test_teacher_catalog_contains_complete_native_tool_surface():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    catalog = MODULE.compact_tool_catalog()
    names = {item["name"] for item in catalog["tools"]}
    expected = {item["function"]["name"] for item in FUNCTION_TOOL_SCHEMAS}
    assert names == expected
    assert catalog["tool_count"] == len(FUNCTION_TOOL_SCHEMAS)
    assert {"manage_calendar", "manage_notes", "private_browser", "ui_control"} <= names


def test_flows_from_file_strips_prior_observations(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"flows": [{
        "id": "web-1", "family": "search_browser", "purpose": "follow-up",
        "turns": [{"user": "Go to IKEA's site", "expect": "browse"},
                  {"user": "Open the first chair", "expect": "continue"}],
        "observed": [{"final": "stale"}], "judge": {"verdict": "fail"},
        "session_id": "old", "url": "old",
    }]}), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["search_browser"])

    assert len(flows) == 1
    assert set(flows[0]).isdisjoint({"observed", "judge", "session_id", "url"})


def test_flows_from_file_accepts_historical_single_turn_probe(tmp_path):
    path = tmp_path / "historical.json"
    path.write_text(json.dumps({"flows": [{
        "id": "one", "family": "notes", "purpose": "routing probe",
        "turns": [{"user": "show notes", "expect": "list notes"}],
    }]}), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["notes"])

    assert len(flows) == 1
    assert len(flows[0]["turns"]) == 1


def test_flows_from_file_can_select_failures_from_judged_run(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"results": [
        {"id": "bad", "family": "notes", "turns": [{"user": "show notes"}],
         "judge": {"verdict": "fail"}, "observed": []},
        {"id": "good", "family": "notes", "turns": [{"user": "list notes"}],
         "judge": {"verdict": "pass"}, "observed": []},
    ]}), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["notes"], prior_verdict="fail")

    assert [flow["id"] for flow in flows] == ["bad"]
    assert "judge" not in flows[0]


def test_flows_from_file_can_select_failure_owner(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"results": [
        {"id": "route", "family": "notes", "turns": [{"user": "show notes"}],
         "judge": {"verdict": "fail", "owner": "harness_routing"}},
        {"id": "model", "family": "notes", "turns": [{"user": "show notes"}],
         "judge": {"verdict": "fail", "owner": "model_sft"}},
    ]}), encoding="utf-8")

    flows = MODULE.flows_from_file(
        path, ["notes"], prior_verdict="fail", prior_owner="harness_routing",
    )

    assert [flow["id"] for flow in flows] == ["route"]


def test_flows_from_file_can_select_transport_contaminated_replays(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"flows": [
        {"id": "transport", "family": "notes", "turns": [{"user": "show notes"}],
         "observed": [{"errors": ["ConnectError: connection refused"]}]},
        {"id": "clean", "family": "notes", "turns": [{"user": "list notes"}],
         "observed": [{"errors": [], "final": "Notes"}]},
    ]}), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["notes"], transport_only=True)

    assert [flow["id"] for flow in flows] == ["transport"]


def test_flows_from_file_accepts_resumable_jsonl(tmp_path):
    path = tmp_path / "cooked.jsonl"
    rows = [{
        "id": f"notes-{number}", "family": "notes", "purpose": "follow-up",
        "turns": [{"user": "show notes"}, {"user": "open the first"}],
    } for number in range(2)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["notes"])

    assert [flow["id"] for flow in flows] == ["notes-0", "notes-1"]


def test_audited_source_seed_ids_counts_unique_completed_judgments_only(tmp_path):
    (tmp_path / "run-one.json").write_text(json.dumps({
        "target_model": "target",
        "results": [
            {"source_seed_id": "seed-1", "judge": {"verdict": "fail"}},
            {"source_seed_id": "seed-1", "judge": {"verdict": "pass"}},
            {"source_seed_id": "seed-2", "judge": {"verdict": "uncertain"}},
        ],
    }), encoding="utf-8")
    (tmp_path / "run-other.json").write_text(json.dumps({
        "target_model": "other",
        "results": [{"source_seed_id": "seed-3", "judge": {"verdict": "pass"}}],
    }), encoding="utf-8")

    assert MODULE.audited_source_seed_ids(tmp_path, "target") == {"seed-1"}


def test_audited_source_seed_ids_separates_routing_runtime(tmp_path):
    (tmp_path / "run-baseline.json").write_text(json.dumps({
        "target_model": "target", "routing_experiment": "baseline",
        "results": [{"source_seed_id": "base", "judge": {"verdict": "pass"}}],
    }), encoding="utf-8")
    (tmp_path / "run-model-choice.json").write_text(json.dumps({
        "target_model": "target", "routing_experiment": "recent_model_choice",
        "results": [{"source_seed_id": "choice", "judge": {"verdict": "fail"}}],
    }), encoding="utf-8")

    assert MODULE.audited_source_seed_ids(
        tmp_path, "target", "recent_model_choice",
    ) == {"choice"}


def test_audited_source_seed_ids_excludes_judged_transport_failures(tmp_path):
    (tmp_path / "run-contaminated.json").write_text(json.dumps({
        "target_model": "target",
        "results": [
            {
                "source_seed_id": "seed-false-pass",
                "judge": {"verdict": "pass"},
                "observed": [{"errors": ["ConnectError: connection refused"]}],
            },
            {
                "source_seed_id": "seed-false-fail",
                "judge": {"verdict": "fail"},
                "observed": [{"errors": ["7011 did not become ready"]}],
            },
            {
                "source_seed_id": "seed-valid",
                "judge": {"verdict": "pass"},
                "observed": [{"errors": [], "final": "Notes shown"}],
            },
        ],
    }), encoding="utf-8")

    assert MODULE.audited_source_seed_ids(tmp_path, "target") == {"seed-valid"}


def test_write_coverage_manifest_reports_unique_seed_progress(tmp_path):
    corpus = [
        {"id": "retry-a", "source_seed_id": "seed-1", "family": "notes"},
        {"id": "retry-b", "source_seed_id": "seed-1", "family": "notes"},
        {"id": "flow-2", "source_seed_id": "seed-2", "family": "calendar"},
    ]

    result = MODULE.write_coverage_manifest(
        tmp_path / "coverage.json", corpus,
        audited_ids={"seed-1", "outside-corpus"}, target_model="target",
    )

    assert result["corpus_seeds"] == 2
    assert result["audited_unique_seeds"] == 1
    assert result["pending_unique_seeds"] == 1
    assert result["coverage_percent"] == 50.0
    assert result["by_family"]["notes"] == {"total": 1, "audited": 1, "pending": 0}
    assert result["by_family"]["calendar"] == {"total": 1, "audited": 0, "pending": 1}


def test_append_ledger_groups_repeated_failure_classes(tmp_path):
    path = tmp_path / "ledger.md"
    results = [{
        "family": "notes", "url": f"http://example/{number}",
        "judge": {
            "verdict": "fail", "owner": "harness_routing",
            "failure_category": "missing_tool", "summary": "Notes tool was absent.",
        },
    } for number in range(3)]

    MODULE.append_ledger(path, "stamp", results)

    text = path.read_text(encoding="utf-8")
    assert "(3 occurrences)" in text
    assert text.count("representative replay") == 1
    assert "http://example/0" in text
    assert "http://example/1" not in text


def test_balanced_flows_caps_and_round_robins_families():
    flows = [
        {"id": "n1", "family": "notes"},
        {"id": "n2", "family": "notes"},
        {"id": "c1", "family": "calendar"},
        {"id": "n3", "family": "notes"},
        {"id": "c2", "family": "calendar"},
    ]
    assert [flow["id"] for flow in MODULE.balanced_flows(flows, 1)] == ["n1", "c1"]
    assert MODULE.balanced_flows(flows, None) is flows


def test_balanced_flows_prevents_global_limit_from_spending_one_family_first():
    flows = [
        {"id": "s1", "family": "search_browser"},
        {"id": "s2", "family": "search_browser"},
        {"id": "t1", "family": "tasks"},
        {"id": "t2", "family": "tasks"},
        {"id": "u1", "family": "ui"},
        {"id": "u2", "family": "ui"},
    ]
    selected = MODULE.balanced_flows(flows, 2)
    assert [flow["id"] for flow in selected] == [
        "s1", "t1", "u1", "s2", "t2", "u2",
    ]


def test_judge_replayed_flows_checkpoints_incrementally_in_replay_order(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "safe_judge_flow", lambda _teacher, result, _fallback: {
        "verdict": "pass", "score": int(result["id"]),
    })
    replayed = [{"id": str(number), "family": "notes"} for number in range(3)]
    checkpoint = tmp_path / "run.json"

    results = MODULE.judge_replayed_flows(
        replayed, None, None, workers=2, checkpoint=checkpoint,
        target_model="target", judge_model="judge",
    )

    assert [row["id"] for row in results] == ["0", "1", "2"]
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["complete"] is True
    assert saved["judged"] == saved["total"] == 3
    assert [row["id"] for row in saved["results"]] == ["0", "1", "2"]


def test_judge_replayed_flows_resumes_completed_verdicts(tmp_path, monkeypatch):
    calls = []

    def judge(_teacher, result, _fallback):
        calls.append(result["id"])
        return {"verdict": "pass", "score": 100}

    monkeypatch.setattr(MODULE, "safe_judge_flow", judge)
    replayed = [{"id": str(number), "family": "notes"} for number in range(3)]
    resumed = [{**replayed[0], "judge": {"verdict": "fail", "score": 10}}]

    results = MODULE.judge_replayed_flows(
        replayed, None, None, workers=2, checkpoint=tmp_path / "run.json",
        target_model="target", judge_model="judge", resume_results=resumed,
    )

    assert calls == ["1", "2"]
    assert [row["judge"]["verdict"] for row in results] == ["fail", "pass", "pass"]


def test_judge_resume_retries_uncertain_verdicts(tmp_path, monkeypatch):
    calls = []

    def judge(_teacher, result, _fallback):
        calls.append(result["id"])
        return {"verdict": "pass", "score": 100}

    monkeypatch.setattr(MODULE, "safe_judge_flow", judge)
    replayed = [{"id": "0", "family": "tasks"}, {"id": "1", "family": "tasks"}]
    resumed = [
        {**replayed[0], "judge": {"verdict": "fail", "score": 20}},
        {**replayed[1], "judge": {
            "verdict": "uncertain", "score": 0,
            "failure_category": "judge_unavailable",
        }},
    ]

    results = MODULE.judge_replayed_flows(
        replayed, None, None, workers=1, checkpoint=tmp_path / "run.json",
        target_model="target", judge_model="judge", resume_results=resumed,
    )

    assert calls == ["1"]
    assert [row["judge"]["verdict"] for row in results] == ["fail", "pass"]


def test_resume_replay_rows_uses_checkpoint_evidence_without_judgments():
    payload = {"results": [
        {"id": "one", "family": "notes", "observed": [{"final": "x"}],
         "judge": {"verdict": "pass"}},
        {"id": "two", "family": "tasks", "observed": [{"final": "y"}],
         "judge": {"verdict": "uncertain"}},
    ]}

    rows = MODULE.resume_replay_rows(payload)

    assert [row["id"] for row in rows] == ["one", "two"]
    assert [row["observed"] for row in rows] == [
        [{"final": "x"}], [{"final": "y"}],
    ]
    assert all("judge" not in row for row in rows)


def test_compact_catalog_distinguishes_note_reminders_from_automated_tasks():
    tools = {tool["name"]: tool for tool in MODULE.compact_tool_catalog()["tools"]}
    assert "due_date" in tools["manage_notes"]["purpose"]
    assert "one-off reminder" in tools["manage_tasks"]["purpose"]


def test_judge_ownership_treats_missing_note_due_date_as_model_failure():
    result = {"observed": [{
        "user": "ok remind me to check this again tomorrow morning",
        "contract": {"offered": ["manage_notes"]},
        "tool_calls": [{
            "tool": "manage_notes",
            "command": '{"action":"add","title":"Check this again"}',
        }],
    }]}
    judged = {
        "verdict": "fail", "owner": "harness_routing",
        "failure_category": "wrong_family_routing", "failed_turns": [1],
    }

    corrected = MODULE.normalize_judge_ownership(result, judged)

    assert corrected["owner"] == "model_sft"
    assert corrected["failure_category"] == "missing_reminder_due_date"
    assert corrected["judge_reported_owner"] == "harness_routing"


def test_flows_from_file_accepts_repair_manifest_candidates(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"candidates": [{
        "source_seed_id": "seed-1", "family": "notes",
        "turns": [{"user": "show notes", "expect": "list notes"}],
        "judge": {"verdict": "fail", "owner": "model_sft"},
    }]}), encoding="utf-8")

    flows = MODULE.flows_from_file(path, ["notes"])

    assert len(flows) == 1
    assert flows[0]["source_seed_id"] == "seed-1"
    assert flows[0]["id"] == "seed-1"
    assert "judge" not in flows[0]


def test_replay_flow_records_session_start_failure_instead_of_aborting_batch(monkeypatch):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(MODULE.httpx, "Client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(
        MODULE, "create_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("temporarily down")),
    )
    args = type("Args", (), {"public_url": "http://example.test"})()

    result = MODULE.replay_flow({
        "id": "seed", "family": "notes", "turns": [{"user": "show notes"}],
    }, args, "cookie")

    assert result["session_id"] == ""
    assert result["url"] == ""
    assert "temporarily down" in result["observed"][0]["errors"][0]
