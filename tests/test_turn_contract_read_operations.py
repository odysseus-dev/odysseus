"""Sealed read intent behavior; no inference, handlers, or external services."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (
    RequiredReadOperation, TurnContract, requested_capabilities,
    required_read_operation_for_request, resolve_turn_contract,
    selected_tools_for_request,
)


def resolve(message, *, history=(), **kwargs):
    return resolve_turn_contract(
        capabilities=kwargs.pop("capabilities", requested_capabilities(message, history)),
        schemas=kwargs.pop("schemas", FUNCTION_TOOL_SCHEMAS),
        policy=kwargs.pop("policy", ToolPolicy()), message=message, history=history, **kwargs,
    )


@pytest.mark.parametrize(
    "message,tool,action,maximum",
    [
        ("can i see what documents are in the editor? a few titles is enough", "manage_documents", "list", 3),
        ("pull up my noes, just a quick look", "manage_notes", "list", None),
        ("I need a quick look at my calendar events. Three titles max, read-only. Don't modify or message anyone.", "manage_calendar", "list_events", 3),
        ("cookbook servers?", "list_cookbook_servers", None, None),
        ("Quick calendar listing: max three titles. Read-only inspection.", "manage_calendar", "list_events", 3),
        ("hey can u glance at my calendar? just want a few event titles, nothing else", "manage_calendar", "list_events", 3),
        ("morning — anythin on my calendar today? just the headlines please", "manage_calendar", "list_events", None),
        ("What have you got stored in memory for me? Three bullets, no changes.", "manage_memory", "list", 3),
        ("gimme my notes, top three titles pls", "manage_notes", "list", 3),
        ("show me my calendar — couple titles, no edits", "manage_calendar", "list_events", 3),
        ("List stored mems, max 3, no changes.", "manage_memory", "list", 3),
        ("pull my calendar events, cap at three, read only", "manage_calendar", "list_events", 3),
        ("rundown my calendar, three at most", "manage_calendar", "list_events", 3),
    ],
)
def test_natural_safe_inventory_requests(message, tool, action, maximum):
    operation = required_read_operation_for_request(message)
    assert operation is not None
    assert operation.tool == tool
    assert operation.args.get("action") == action
    assert operation.max_items == maximum
    assert requested_capabilities(message) == {
        "cookbook_admin" if tool == "list_cookbook_servers" else {
            "manage_documents": "documents",
            "manage_notes": "notes",
            "manage_calendar": "calendar",
            "manage_memory": "memory",
        }[tool]
    }


def test_natural_document_prefix_search_seals_query():
    message = (
        "im trying to find a doc i made earlier, title starts with "
        "'Suggestion audit 20260829_220912-177730bb'"
    )
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(
        "manage_documents",
        {"action": "list", "search": "Suggestion audit 20260829_220912-177730bb"},
    )
    assert requested_capabilities(message) == {"documents"}


def test_read_only_unsubscribe_scan_seals_account_scope():
    message = (
        "go through my Primary inbox and flag newsletters or mailing lists that "
        "give an unsubscribe option. dont change anything yet."
    )
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(
        "scan_email_unsubscribes",
        {"folder": "INBOX", "account": "Primary Inbox"},
    )
    assert requested_capabilities(message) == {"email"}


def test_unsubscribe_scan_accepts_inbox_for_newsletters_wording():
    message = (
        "scan my primary inbox for newsletters or mailing lists that offer an "
        "unsubscribe option"
    )
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(
        "scan_email_unsubscribes",
        {"folder": "INBOX", "account": "Primary Inbox"},
    )
    assert requested_capabilities(message) == {"email"}


@pytest.mark.parametrize(("message", "expected"), [
    (
        "what notes do i have? keep it to a handful of titles",
        RequiredReadOperation("manage_notes", {"action": "list"}, 3),
    ),
    (
        "what events do i have? keep it to a few titles max",
        RequiredReadOperation("manage_calendar", {"action": "list_events"}, 3),
    ),
])
def test_natural_personal_inventory_inherits_approximate_limit(message, expected):
    assert required_read_operation_for_request(message) == expected


@pytest.mark.parametrize("message", [
    "show me whats scheduled, only a handful of titles pls",
    "whats coming up? short answer, 3 titles tops",
    "what have I got coming up over the next seven days?",
])
def test_natural_schedule_inventory_is_a_bounded_calendar_read(message):
    operation = required_read_operation_for_request(message)
    assert operation is not None
    assert operation.tool == "manage_calendar"
    assert operation.args == {"action": "list_events"}
    if "next seven" not in message:
        assert operation.max_items == 3


def test_read_only_contact_resolution_ignores_presentation_suffix():
    assert required_read_operation_for_request(
        "Resolve Priya Shah in my contacts, read only please",
    ) == RequiredReadOperation(
        "manage_contact", {"action": "search", "query": "Priya Shah"},
    )


def test_exact_read_repeat_inherits_same_limit_clause():
    history = [
        {"role": "user", "content": "list my calendar events, three max"},
        {"role": "assistant", "content": "Three events", "metadata": {
            "tool_events": [{
                "tool": "manage_calendar", "command": {"action": "list_events"},
                "exit_code": 0,
            }],
        }},
    ]
    assert required_read_operation_for_request(
        "do that again, same limit", history,
    ) == RequiredReadOperation("manage_calendar", {"action": "list_events"}, 3)


def test_exact_skill_repeat_accepts_for_me_clause_and_new_limit():
    history = [
        {"role": "user", "content": "list my skills, only the first three"},
        {"role": "assistant", "content": "Three skills", "metadata": {
            "tool_events": [{
                "tool": "manage_skills", "command": {"action": "list"},
                "exit_code": 0,
            }],
        }},
    ]
    assert required_read_operation_for_request(
        "k, list those again for me, max three", history,
    ) == RequiredReadOperation("manage_skills", {"action": "list"}, 3)


@pytest.mark.parametrize(("message", "expected"), [
    (
        "which search backend am i on right now?",
        RequiredReadOperation(
            "manage_settings", {"action": "get", "key": "search_provider"},
        ),
    ),
    (
        "what time filter is my search set to by default?",
        RequiredReadOperation("manage_settings", {"action": "list"}),
    ),
    (
        "show me the whole search group",
        RequiredReadOperation("manage_settings", {"action": "list"}),
    ),
])
def test_search_configuration_questions_seal_settings_reads(message, expected):
    assert required_read_operation_for_request(message) == expected
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_common_official_typo_keeps_web_lookup():
    message = "i need an offical link for GPT-4, look it up on the web"
    assert requested_capabilities(message) == {"search_browser"}
    assert selected_tools_for_request(message) == {"web_search"}


def test_calendar_repeat_preserves_executed_range_and_new_limit():
    history = [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "manage_calendar",
            "command": {
                "action": "list_events",
                "start": "2026-09-12T00:00:00",
                "end": "2026-09-13T00:00:00",
            },
            "exit_code": 0,
        }]},
    }]
    operation = required_read_operation_for_request(
        "thanks, do that again but stick to three and don't modify anything",
        history,
    )
    assert operation == RequiredReadOperation(
        "manage_calendar",
        {
            "action": "list_events",
            "start": "2026-09-12T00:00:00",
            "end": "2026-09-13T00:00:00",
        },
        3,
    )


@pytest.mark.parametrize(
    "message,query",
    [
        ("hey can you search my skill library for anything about email workflows?", "email workflows"),
        ("look thru my skills for email workflow guidance", "email workflow guidance"),
    ],
)
def test_skill_library_search_owns_incidental_email_word(message, query):
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_skills", {"action": "search", "query": query},
    )
    assert requested_capabilities(message) == {"skills"}


def test_approximate_calendar_count_is_a_contract_limit():
    assert required_read_operation_for_request(
        "can u check my calendar and gimme like three event titles"
    ) == RequiredReadOperation(
        "manage_calendar", {"action": "list_events"}, 3,
    )


def test_explicit_email_attachment_read_is_sealed_and_available():
    message = "open attachment 0 on email UID 112 and tell me what it is"
    assert selected_tools_for_request(message) == {"download_attachment"}
    assert requested_capabilities(message) == {"email"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "download_attachment", {"uid": "112", "index": 0},
    )
    contract = resolve(
        message,
        selected_tools=selected_tools_for_request(message),
    )
    assert {
        name.rsplit("__", 1)[-1] for name in contract.offered
        if name not in {"ask_user", "update_plan"}
    } == {
        "download_attachment"
    }
    assert {name.rsplit("__", 1)[-1] for name in contract.required} == {
        "download_attachment"
    }


@pytest.mark.parametrize("message", [
    "pull up attachment 0 from message 112",
    "theres a creator payout sample attached to email 112, can u read it for me",
])
def test_natural_email_attachment_references_are_sealed(message):
    assert selected_tools_for_request(message) == {"download_attachment"}
    assert requested_capabilities(message) == {"email"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "download_attachment", {"uid": "112", "index": 0},
    )


def test_quick_web_lookup_with_official_link_selects_search():
    message = "quick web lookup for gpt-4, one official link is enough."
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_available_tools_execution_boilerplate_is_not_a_tool_inventory_request():
    message = (
        "Solve the task efficiently before the timeout. Use the available tools and "
        "as many iterative steps as needed. Fetch today's papers, classify every paper "
        "into exactly one category, and report which paper is most relevant."
    )
    assert required_read_operation_for_request(message) is None


@pytest.mark.parametrize(("message", "expected"), [
    (
        "whats on my agenda today? just the titles, dont change anything",
        RequiredReadOperation("manage_calendar", {"action": "list_events"}),
    ),
    (
        "wich agent tools are switched off right now?",
        RequiredReadOperation("manage_settings", {"action": "list_tools"}),
    ),
    (
        "show me who is on my blocked senders list",
        RequiredReadOperation("manage_email_state", {"action": "list_blocked"}),
    ),
    (
        "quick check — who am i blocking in email",
        RequiredReadOperation("manage_email_state", {"action": "list_blocked"}),
    ),
    (
        "something feels off in my inbox — can u check for spam?",
        RequiredReadOperation("scan_spam", {}),
    ),
    (
        "wots the state of my cookbook model servers — anything crashed or stuck?",
        RequiredReadOperation("list_served_models", {}),
    ),
    (
        "can u list my notes? just a cpl titles, dont change antyhing",
        RequiredReadOperation("manage_notes", {"action": "list"}, 2),
    ),
    (
        "what notes do i have? keep it short — few titles",
        RequiredReadOperation("manage_notes", {"action": "list"}, 3),
    ),
    (
        "show me my saved cookbook serve presets — but dont launch anything",
        RequiredReadOperation("list_serve_presets", {}),
    ),
    (
        "find me the skill about email workflows, however you label it",
        RequiredReadOperation("manage_skills", {"action": "search", "query": "email workflows"}),
    ),
    (
        "can u look through my skills and see if anything covers email workflows?",
        RequiredReadOperation("manage_skills", {"action": "search", "query": "email workflows"}),
    ),
    (
        "can you look thru my skils and see if any of them cover email workflows?",
        RequiredReadOperation("manage_skills", {"action": "search", "query": "email workflows"}),
    ),
    (
        "list my cal events please — three titles, read only",
        RequiredReadOperation("manage_calendar", {"action": "list_events"}, 3),
    ),
    (
        "resolve priya shah in my contacts",
        RequiredReadOperation("manage_contact", {"action": "search", "query": "priya shah"}),
    ),
])
def test_unseen_safe_inventory_wording_is_sealed(message, expected):
    assert required_read_operation_for_request(message) == expected
    assert requested_capabilities(message)


def test_explicit_teacher_review_uses_teacher_tool():
    message = "teacher review pls, check this for tool-grouding: the action worked"
    assert selected_tools_for_request(message) == {"ask_teacher"}
    assert requested_capabilities(message) == {"cookbook_admin"}
    contract = resolve(message, selected_tools=selected_tools_for_request(message))
    assert "ask_teacher" in contract.offered


def test_sent_mail_followup_switches_from_contact_to_email():
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "manage_contact", "exit_code": 0, "error": False,
    }]}}]
    assert requested_capabilities(
        "did i ever actually send them anything?", history,
    ) == {"email"}


def test_titled_document_editor_request_keeps_data_and_ui_tools():
    message = (
        "i had a doc going earlier called 'SFT Harness Repair Notes' — "
        "pull it up in the editor for me"
    )
    assert selected_tools_for_request(message) == {
        "manage_documents", "ui_control",
    }
    assert requested_capabilities(message) == {"documents", "ui"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_documents",
        {"action": "list", "search": "SFT Harness Repair Notes"},
    )


@pytest.mark.parametrize("message,tool,args", [
    ("List my notes", "manage_notes", {"action": "list"}),
    ("Show my calendar events", "manage_calendar", {"action": "list_events"}),
    ("List my calendars", "manage_calendar", {"action": "list_calendars"}),
    ("List my email accounts", "list_email_accounts", {}),
    ("List my configured email accounts", "list_email_accounts", {}),
    ("What's my email address?", "list_email_accounts", {}),
    ("List my scheduled tasks", "manage_tasks", {"action": "list"}),
    ("List my documents", "manage_documents", {"action": "list"}),
    ("Read my saved memories", "manage_memory", {"action": "list"}),
    ("Can you show my skills?", "manage_skills", {"action": "list"}),
    ("List my saved skills", "manage_skills", {"action": "list"}),
    ("List available models", "list_models", {}),
    ("List cached models", "list_cached_models", {}),
    ("List locally cached models", "list_cached_models", {}),
    ("List served models", "list_served_models", {}),
    ("List downloads", "list_downloads", {}),
    ("List serve presets", "list_serve_presets", {}),
    ("List cookbook servers", "list_cookbook_servers", {}),
    ("List my saved research reports", "manage_research", {"action": "list"}),
    ("List my chat sessions", "list_sessions", {}),
    ("List my contacts", "manage_contact", {"action": "list"}),
    (
        "Find the best model to run on my hardware",
        "app_api",
        {
            "action": "call",
            "method": "GET",
            "path": "/api/hwfit/models?fit_only=true&limit=10&sort=fit",
        },
    ),
    ("Read note id abcd1234", "manage_notes", {"action": "view", "id": "abcd1234"}),
    ("Read document id doc-123", "manage_documents", {"action": "read", "document_id": "doc-123"}),
    ("View skill id release-check", "manage_skills", {"action": "view", "name": "release-check"}),
])
def test_explicit_reads_resolve_real_schema_operations(message, tool, args):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, args)
    contract = resolve(message)
    assert contract.required_read_operation == operation
    assert tool in contract.required & contract.offered & contract.executable
    assert not contract.unavailable
    schema = next(s["function"] for s in contract.schemas() if s["function"]["name"] == tool)
    properties = schema["parameters"]["properties"]
    assert set(args) <= properties.keys()
    assert set(schema["parameters"].get("required", [])) <= args.keys()
    if "action" in args:
        assert args["action"] in properties["action"]["enum"]
    assert contract.audit()["required_read_operation"] == operation.audit()


def test_research_completion_followup_lists_reports_from_recent_research_context():
    history = [
        {"role": "assistant", "metadata": {"tool_events": [{
            "tool": "trigger_research", "exit_code": 0, "error": False,
        }]}},
    ]
    operation = required_read_operation_for_request(
        "when its done, how do i find it again?", history,
    )
    assert operation == RequiredReadOperation("manage_research", {"action": "list"})
    assert resolve(
        "when its done, how do i find it again?", history=history,
    ).required == {"manage_research"}


def test_explicit_limit_is_presentation_bound_and_survives_exact_repeat():
    message = "Show my first 3 notes"
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation("manage_notes", {"action": "list"}, max_items=3)
    assert "limit" not in operation.args
    history = [{"role": "user", "content": message}]
    assert resolve("Show them again", history=history).required_read_operation == operation


def test_app_api_cannot_be_sealed_as_an_arbitrary_admin_read():
    with pytest.raises(ValueError, match="limited to declared GET endpoints"):
        RequiredReadOperation("app_api", {
            "action": "call", "method": "POST", "path": "/api/cookbook/state",
        })


@pytest.mark.parametrize("followup", ["Do that again", "Repeat it", "Same list again", "Again!"])
def test_exact_repeat_resolves_nearest_user_read_and_ignores_assistant_suggestions(followup):
    history = [SimpleNamespace(role="user", content="Read document id doc-7"),
               {"role": "assistant", "content": "You could delete it or search the web."}]
    operation = required_read_operation_for_request(followup, history)
    assert operation == RequiredReadOperation("manage_documents", {"action": "read", "document_id": "doc-7"})
    assert resolve(followup, history=history).required_read_operation == operation


@pytest.mark.parametrize("initial,followup,tool", [
    ("List available models", "Refresh that same model catalog list", "list_models"),
    ("List served models", "Refresh that same served-model list", "list_served_models"),
    ("List downloads", "Refresh that same downloads list", "list_downloads"),
    ("List serve presets", "Refresh that same serve-preset list", "list_serve_presets"),
    ("List locally cached models", "Refresh that same cached-model list", "list_cached_models"),
])
def test_refresh_named_list_repeats_exact_safe_read(initial, followup, tool):
    history = [{"role": "user", "content": initial},
               {"role": "assistant", "content": "The requested list."}]
    expected = RequiredReadOperation(tool)
    assert required_read_operation_for_request(followup, history) == expected
    contract = resolve(followup, history=history)
    assert contract.required_read_operation == expected
    assert contract.required == {tool}


@pytest.mark.parametrize("initial,tool,args", [
    ("List my saved research reports. Return at most three titles. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "manage_research", {"action": "list"}),
    ("List my chat sessions. Return at most three names. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "list_sessions", {}),
    ("List my contacts. Return at most three names. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "manage_contact", {"action": "list"}),
])
def test_supplemental_inventory_reads_and_referential_repeat_are_sealed(initial, tool, args):
    operation = required_read_operation_for_request(initial)
    assert operation == RequiredReadOperation(tool, args, max_items=3)
    history = [{"role": "user", "content": initial}]
    repeated = required_read_operation_for_request(
        "List those again, at most three. Read-only; do not change data or send messages.",
        history,
    )
    assert repeated == operation
    assert resolve(initial).required == {tool}
    assert resolve("List those again, at most three. Read-only; do not change data or send messages.",
                   history=history).required == {tool}


def test_repeat_chain_accepts_history_including_current_turn():
    history = [{"role": "user", "content": "List my email accounts"},
               {"role": "user", "content": "Repeat that"},
               {"role": "user", "content": "Do that again"}]
    assert required_read_operation_for_request("Do that again", iter(history)) == RequiredReadOperation("list_email_accounts")


def test_typoed_return_to_latest_email_inventory_after_another_family():
    history = [
        {"role": "user", "content": "whats my emaol adress?"},
        {"role": "assistant", "content": "Two accounts.", "metadata": {
            "tool_events": [{"tool": "list_email_accounts", "exit_code": 0}],
        }},
        {"role": "user", "content": "show my notse now"},
        {"role": "assistant", "content": "Your notes.", "metadata": {
            "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
        }},
    ]

    assert required_read_operation_for_request(
        "back to emaol show 2 latest", history
    ) == RequiredReadOperation("list_emails", {"max_results": 2}, 2)


@pytest.mark.parametrize("intervening", ["Delete my notes", "What is a prime number?", "Search the web for notes"])
def test_repeat_does_not_reach_past_a_new_user_intent(intervening):
    history = [{"role": "user", "content": "List my notes"},
               {"role": "user", "content": intervening}]
    assert required_read_operation_for_request("Do that again", history) is None


@pytest.mark.parametrize("message", [
    "Create a note", "Delete document id doc-1", "Run my tasks", "Send an email",
    "List my email accounts and send an email", "List my notes and calendar",
    "List my notes; delete the old ones", "Run ls", "List files in my workspace",
    "Search my notes for lunch", "Summarize my documents", "Research calendar software",
    "How do I list my notes?", "Can you explain how to read a document?",
    "Read the document about lunch", "Read note id abc and delete it",
    "List my events tomorrow", "List models on the remote server",
    "Show those from yesterday", "Repeat it but only the first 2", "Again, delete it",
    "List 0 notes", "List my notes except archived ones",
])
def test_non_exact_or_unsafe_requests_never_force_an_operation(message):
    history = [{"role": "user", "content": "List my notes"}]
    assert required_read_operation_for_request(message, history) is None
    assert resolve(message, history=history).required_read_operation is None


def test_read_operation_copies_arguments_and_audit_is_detached():
    args = {"action": "view", "id": "note-1"}
    operation = RequiredReadOperation("manage_notes", args, max_items=1)
    args["action"] = "delete"
    assert operation.args["action"] == "view"
    with pytest.raises(TypeError):
        operation.args["id"] = "note-2"
    with pytest.raises(FrozenInstanceError):
        operation.tool = "bash"
    with pytest.raises(FrozenInstanceError):
        operation.max_items = 99
    contract = resolve("Read note id note-1", required_read_operation=operation)
    audit = contract.audit()
    audit["required_read_operation"]["args"]["action"] = "delete"
    assert contract.required_read_operation.args == {"action": "view", "id": "note-1"}
    with pytest.raises(FrozenInstanceError):
        contract.required_read_operation = None


@pytest.mark.parametrize("tool,args", [
    ("bash", {}), ("send_email", {}), ("web_search", {}),
    ("manage_notes", {"action": "delete", "id": "note-1"}),
    ("manage_notes", {"action": "search", "query": "lunch"}),
    ("manage_tasks", {"action": "run"}),
    ("manage_memory", {"action": "list", "command": "delete\nall"}),
    ("manage_notes", {"action": "list", "content": "replacement"}),
    ("manage_notes", {"action": "list", "archived": []}),
    ("manage_documents", {"action": "read"}),
    ("manage_skills", {"action": "view", "name": ""}),
    ("list_email_accounts", {"_odysseus_owner": "someone-else"}),
    ("list_email_accounts", {"action": None}),
])
def test_type_rejects_mutations_and_unsafe_argument_shapes(tool, args):
    with pytest.raises(ValueError):
        RequiredReadOperation(tool, args)


@pytest.mark.parametrize("value", [0, -1, True, "5", 1.5])
def test_max_items_requires_positive_integer(value):
    with pytest.raises(ValueError):
        RequiredReadOperation("list_models", max_items=value)


@pytest.mark.parametrize("mode", ["missing", "disabled", "hidden", "all", "unselected", "wrong_family"])
def test_required_read_cannot_run_unavailable_or_outside_scope(mode):
    kwargs = {}
    if mode == "missing":
        kwargs["schemas"] = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] != "list_models"]
    elif mode in {"disabled", "hidden"}:
        kwargs["policy"] = ToolPolicy(**{f"{mode}_tools": frozenset({"list_models"})})
    elif mode == "all":
        kwargs["policy"] = ToolPolicy(block_all_tool_calls=True)
    elif mode == "unselected":
        kwargs["selected_tools"] = {"list_downloads"}
    else:
        kwargs["capabilities"] = {"calendar"}
    contract = resolve("List available models", **kwargs)
    assert contract.required_read_operation == RequiredReadOperation("list_models")
    assert "list_models" in contract.unavailable
    assert contract.offered == contract.required == set()
    assert contract.schemas() == []


def test_other_missing_requirement_also_marks_sealed_read_unavailable():
    contract = resolve("List my notes", required_tools={"send_email"})
    assert {"send_email", "manage_notes"} <= contract.unavailable
    assert contract.offered == set()
    assert contract.required_read_operation.tool == "manage_notes"


@pytest.mark.parametrize("denied", [False, True])
def test_required_account_read_resolves_to_real_mcp_schema_or_reports_denial(denied):
    schema = deepcopy(next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "list_email_accounts"))
    schema["function"]["name"] = "mcp__email__list_email_accounts"
    contract = resolve("List my email accounts", schemas=[*FUNCTION_TOOL_SCHEMAS, schema],
                       selected_tools={"list_email_accounts"},
                       policy=ToolPolicy(disabled_tools=frozenset({"list_email_accounts"}) if denied else frozenset()))
    if denied:
        assert "list_email_accounts" in contract.unavailable
        assert not contract.offered
    else:
        assert contract.required_read_operation.tool == "mcp__email__list_email_accounts"
        assert contract.required == {"mcp__email__list_email_accounts"}


def test_existing_constructor_and_resolver_api_remain_compatible():
    empty = TurnContract(frozenset(), frozenset(), frozenset(), frozenset(), frozenset(), ())
    assert empty.required_read_operation is None
    legacy = resolve_turn_contract(capabilities={"cookbook_admin"}, schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy())
    assert legacy.required_read_operation is None
    assert "required_read_operation" not in legacy.audit()
    assert "list_models" not in legacy.required
    with pytest.raises(ValueError, match="available"):
        replace(empty, required_read_operation=RequiredReadOperation("list_models"))


def test_exact_live_notes_prompt_and_repeat_keep_safe_operation_and_limit():
    message = (
        "List my notes. Return at most three titles. Read-only inspection; "
        "do not change data or send messages. Keep the answer concise."
    )
    expected = RequiredReadOperation("manage_notes", {"action": "list"}, max_items=3)
    assert required_read_operation_for_request(message) == expected
    contract = resolve(message)
    assert contract.required_read_operation == expected
    assert contract.required == {"manage_notes"}
    followup = "List those again, at most three. Read-only; do not change data or send messages."
    history = [{"role": "user", "content": message},
               {"role": "assistant", "content": "Three note titles."}]
    assert required_read_operation_for_request(followup, history) == expected
    assert resolve(followup, history=history).required_read_operation == expected


@pytest.mark.parametrize("message", ["List my email accounts.", "Show my email accounts."])
def test_exact_live_metadata_mode_email_prompt(message):
    assert resolve(message).required_read_operation == RequiredReadOperation("list_email_accounts")


@pytest.mark.parametrize("message", [
    "List my notes and delete the old ones. Read-only; do not change data or send messages.",
    "List my notes. Return at most three titles and send them to me.",
    "List my notes. Send an email. Keep the answer concise.",
    "List my notes. Read-only; do not change data or send messages except to Bob.",
    "List those again, at most three, then delete them. Read-only.",
    "Summarize my notes. Return at most three titles. Read-only inspection.",
    "List my notes. Return at most zero titles.",
])
def test_presentation_suffixes_never_hide_real_compound_actions(message):
    assert required_read_operation_for_request(message, [{"role": "user", "content": "List my notes"}]) is None


def test_safe_suffix_limit_is_not_lost_when_repeat_inherits_plain_request():
    history = [{"role": "user", "content": "List my notes"}]
    operation = required_read_operation_for_request("List those again, at most three. Read-only.", history)
    assert operation == RequiredReadOperation("manage_notes", {"action": "list"}, 3)
    assert required_read_operation_for_request("List my notes. Return at most 5 titles. Return at most three titles.").max_items == 3


@pytest.mark.parametrize("message,tool", [
    ("Can you show my notes? I only need three titles. Read-only, keep it short.", "manage_notes"),
    ("what notes have i got? 3 titles tops", "manage_notes"),
    ("list my memorries, three short ones, read-only please", "manage_memory"),
])
def test_natural_bounded_read_wording_keeps_three_item_contract(message, tool):
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        tool, {"action": "list"}, max_items=3,
    )


def test_natural_bounded_repeat_inherits_three_item_contract():
    history = [{"role": "user", "content": "Show my scheduled tasks"}]
    assert required_read_operation_for_request(
        "list those again, three tops", history,
    ) == RequiredReadOperation("manage_tasks", {"action": "list"}, max_items=3)


@pytest.mark.parametrize("message", [
    "list those again, same three",
    "same again but cap it at three, read only",
    "again but only show 3",
    "same as before, three tops",
])
def test_additional_natural_bounded_repeats_inherit_contract(message):
    history = [{"role": "user", "content": "Show my scheduled tasks"}]
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_tasks", {"action": "list"}, max_items=3,
    )


@pytest.mark.parametrize("message,tool,args,limit", [
    ("List my scheduled tasks. Return at most three names and statuses. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "manage_tasks", {"action": "list"}, 3),
    ("List my saved memories. Return at most three short entries. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "manage_memory", {"action": "list"}, 3),
    ("List configured Cookbook servers. Return only names and status. Read-only inspection; do not change data or send messages. Keep the answer concise.",
     "list_cookbook_servers", {}, None),
])
def test_exact_live_output_wording_remains_a_safe_read(message, tool, args, limit):
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        tool, args, max_items=limit,
    )


def test_latest_inbox_projection_is_inventory_not_topic_search():
    assert required_read_operation_for_request(
        "List my latest three inbox emails with sender and subject."
    ) == RequiredReadOperation(
        "list_emails", {"folder": "INBOX", "max_results": 3}, max_items=3,
    )


def test_bounded_calendar_inventory_preserves_dates_and_query():
    assert required_read_operation_for_request(
        "List calendar events from 2030-02-01 through 2030-02-02 containing fixture-marker."
    ) == RequiredReadOperation(
        "manage_calendar",
        {
            "action": "list_events",
            "start": "2030-02-01",
            "end": "2030-02-02",
            "query": "fixture-marker",
        },
    )
