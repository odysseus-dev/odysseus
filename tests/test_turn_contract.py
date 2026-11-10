"""Behavior contracts using the production native schema inventory."""
import asyncio
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tool_policy import ToolPolicy, WEB_ACCESS_TOOL_NAMES
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (
    FAMILY_TOOLS, RequiredReadOperation, active_turn_contract, bind_turn_contract, canonical_tool,
    immediately_established_family, requested_capabilities, required_read_operation_for_request,
    requests_independent_web_source, requests_supporting_web_source, resolve_turn_contract,
    preserve_bound_editor_selected_tools, selected_tools_for_request,
    targets_bound_editor_request,
)


def _completed_tool_turn(user_text, *tools):
    return [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": "Done", "metadata": {"tool_events": [
            {"tool": tool, "exit_code": 0, "error": False} for tool in tools
        ]}},
    ]


@pytest.mark.parametrize(("prior", "followup"), [
    ("Is there Rocket League for Switch 2?", "I searched rocket and cannot find it"),
    ("Find the current price of the Framework laptop", "framework is not showing for me"),
    ("Look up the Kyoto railway museum opening hours", "I cannot find the kyoto museum result"),
    ("Check whether Aurora 7 is available on PlayStation", "where is aurora 7 listed"),
])
def test_subject_continuity_keeps_public_web_followups_out_of_shell(prior, followup):
    history = _completed_tool_turn(prior, "web_search", "web_fetch")

    assert immediately_established_family(followup, history) == "search_browser"
    assert requested_capabilities(followup, history) == frozenset({"search_browser"})


def test_subject_continuity_uses_typed_private_domain_without_crossing_to_shell():
    history = _completed_tool_turn("Find my Project Juniper note", "manage_notes")

    assert requested_capabilities(
        "juniper is not showing in the results", history,
    ) == frozenset({"notes"})


def test_explicit_domain_switch_overrides_subject_continuity():
    history = _completed_tool_turn("Find the current Orion browser release", "web_search")

    assert requested_capabilities(
        "search my documents for Orion", history,
    ) == frozenset({"documents"})


def test_mixed_domain_turn_is_not_inherited_as_one_domain():
    history = _completed_tool_turn(
        "Find sources for Atlas and save them to my notes", "web_search", "manage_notes",
    )

    assert immediately_established_family("atlas is missing", history) is None


@pytest.mark.parametrize('prompt', [
    "What's new in Sweden?",
    "What's happening in Sweden?",
    'Give me an update on Sweden',
    'Catch me up on Sweden',
    'What are the top stories in Sweden today?',
    'What should I know about Sweden right now?',
    'Latest developments in AI',
    'Recent AI breakthroughs',
    'AI news',
    'Compare the latest AI models',
    'Best laptops right now',
    'Recommend a laptop based on current reviews',
    'latset ai neews?',
    'whats new in japan rn',
    'what’s new in Sweden',
    'Compare battery chemistries for home storage. Find evidence and explain tradeoffs.',
])
def test_broad_web_briefings_share_one_search_semantic(prompt):
    from src.turn_contract import broad_web_briefing_request

    assert broad_web_briefing_request(prompt)
    assert requested_capabilities(prompt) == frozenset({'search_browser'})


@pytest.mark.parametrize('prompt', [
    'What is the latest Python version?',
    "Who is Sweden's prime minister?",
    'Find the WIKING Miro manual online',
    'How does Rust ownership work?',
])
def test_narrow_lookups_are_not_misclassified_as_broad_briefings(prompt):
    from src.turn_contract import broad_web_briefing_request

    assert not broad_web_briefing_request(prompt)


def resolve(capabilities=(), *, schemas=FUNCTION_TOOL_SCHEMAS, policy=None, required_tools=(),
            required_capabilities=None, selected_tools=None, always_available_tools=(),
            warm_tools=(), message=None):
    return resolve_turn_contract(capabilities=capabilities, schemas=schemas,
                                 policy=policy or ToolPolicy(), required_tools=required_tools,
                                 required_capabilities=required_capabilities,
                                 selected_tools=selected_tools,
                                 always_available_tools=always_available_tools,
                                 warm_tools=warm_tools, message=message)


def test_latest_topic_info_and_common_news_typo_route_to_web_search():
    """Broad current-info prompts must not silently lose the Web surface."""
    for prompt in ("What's latest AI info?", "What's latest AI nees?"):
        assert requested_capabilities(prompt) == frozenset({"search_browser"})


@pytest.mark.parametrize('prompt', [
    'Look up the current stock market and summarize the major US indexes.',
    'Find the latest Nvidia driver for Linux.',
    'Search for today\'s exchange rate for USD to JPY.',
])
def test_explicit_current_lookup_starts_with_native_search(prompt):
    assert selected_tools_for_request(prompt) == frozenset({'web_search'})


@pytest.mark.parametrize("prompt", [
    "I'm using a WIKING fireplace; can I find an English manual online?",
    "Can you look online",
    "Could you search online for the operator's manual?",
])
def test_explicit_online_lookup_requests_route_to_web_search(prompt):
    assert requested_capabilities(prompt) == frozenset({"search_browser"})


def test_musical_notes_do_not_route_to_personal_notes_when_media_tool_is_explicit():
    prompt = (
        "Your first tool call must be inspect_media for /workspace/input/reference.png. "
        "Study the supplied piano score image, then create /workspace/output.html. "
        "Reproduce the score with noteheads and light each piano key while each note sounds. "
        "Preview the page in a browser and fix visual or timing defects before finishing."
    )

    assert selected_tools_for_request(prompt) == frozenset({
        "inspect_media", "write_file", "read_file", "private_browser",
    })
    capabilities = requested_capabilities(prompt)
    assert "notes" not in capabilities
    assert {"media_inspection", "shell_files", "search_browser"} <= capabilities


def test_musical_note_language_alone_never_selects_personal_notes():
    capabilities = requested_capabilities(
        "Inspect the piano score image and identify each note, pitch, stave, clef, and notehead."
    )

    assert "notes" not in capabilities
    assert "media_inspection" in capabilities


def test_successfully_used_tool_stays_offered_when_next_turn_routes_elsewhere():
    contract = resolve(
        {"notes"},
        selected_tools={"manage_notes"},
        warm_tools={"manage_calendar"},
    )
    assert {"manage_notes", "manage_calendar"} <= set(contract.offered)


def test_warm_tool_does_not_bypass_current_policy():
    contract = resolve(
        {"notes"},
        warm_tools={"manage_calendar"},
        policy=ToolPolicy(disabled_tools=frozenset({"manage_calendar"})),
    )
    assert "manage_notes" in contract.offered
    assert "manage_calendar" not in contract.offered


def test_unavailable_warm_family_does_not_block_a_prose_followup():
    contract = resolve({"search_browser"}, required_capabilities=set(),
                       policy=ToolPolicy(disabled_tools=frozenset(FAMILY_TOOLS["search_browser"])))
    assert not contract.unavailable
    assert not contract.required


def test_model_switch_request_offers_discovery_and_the_ui_switch_only():
    message = "while ur in there can u swap me to a lighter model"
    assert selected_tools_for_request(message) == {"list_models", "ui_control"}
    assert requested_capabilities(message) == {"cookbook_admin", "ui"}
    contract = resolve(
        requested_capabilities(message),
        selected_tools=selected_tools_for_request(message),
    )
    assert {"list_models", "ui_control"} <= contract.offered
    assert contract.offered <= {"list_models", "ui_control", "ask_user", "update_plan"}


def test_research_filter_followup_seals_a_library_search():
    history = [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "manage_research", "command": {"action": "list"},
            "error": False, "exit_code": 0,
        }]},
    }]
    operation = required_read_operation_for_request(
        "any of them about battery tech?", history,
    )
    assert operation is not None
    assert operation.tool == "manage_research"
    assert operation.args == {"action": "list", "search": "battery tech"}


def test_empty_family_contract_keeps_core_recovery_tools() -> None:
    contract = resolve(
        frozenset(),
        selected_tools=None,
    )

    assert len(contract.offered & {
        "bash", "python", "read_file", "web_search", "web_fetch", "ask_user",
    }) >= 5
    assert contract.offered


def test_web_contract_keeps_private_browser_only_as_fallback() -> None:
    contract = resolve(
        frozenset(),
        selected_tools={"web_search"},
        message="find current reviews for a product",
    )

    assert "web_search" in contract.offered
    assert "private_browser" in contract.offered
    assert "private_browser" not in {
        "bash", "python", "read_file", "web_search", "web_fetch", "ask_user",
    }


def test_full_inventory_experiment_respects_disabled_families_without_blocking_others():
    from src.turn_contract import resolve_full_inventory_contract
    contract = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(disabled_tools=frozenset(FAMILY_TOOLS["search_browser"])),
    )
    assert contract.selection_mode == "full_compact_experiment"
    assert contract.permits("manage_notes")
    assert contract.permits("manage_calendar")
    assert contract.permits("manage_research")
    assert not contract.permits("web_search")
    assert not contract.permits("private_browser")
    assert not contract.required and not contract.unavailable


@pytest.mark.parametrize("message,family,tool", [
    ("Add lunch to my calendar", "calendar", "manage_calendar"),
    ("Create a note about lunch", "notes", "manage_notes"),
    ("List my scheduled tasks", "tasks", "manage_tasks"),
    ("List my skills", "skills", "manage_skills"),
    ("Forget my saved memories", "memory", "manage_memory"),
    ("Create a document about lunch", "documents", "create_document"),
    ("Check my inbox", "email", "list_emails"),
    ("Search the web for lunch recipes", "search_browser", "web_search"),
    ("Browse example.com and find the pricing page", "search_browser", "private_browser"),
    ("Run pytest", "shell_files", "bash"),
    ("List available models", "cookbook_admin", "list_models"),
])
def test_ten_families_use_real_inventory(message, family, tool):
    capabilities = requested_capabilities(message)
    assert capabilities == {family}
    contract = resolve(capabilities)
    assert tool in contract.offered
    inventory = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert contract.offered == (FAMILY_TOOLS[family] | {"ask_user", "update_plan"}) & inventory
    assert contract.required <= contract.offered <= contract.executable
    assert {s["function"]["name"] for s in contract.schemas()} == contract.offered


def test_local_image_ocr_uses_the_exact_native_tool_contract():
    message = "Extract the exact visible text from /workspace/receipt.png with OCR."
    capabilities = requested_capabilities(message, workspace=True)
    assert capabilities == {"ocr"}
    contract = resolve(capabilities)
    assert contract.required == {"extract_text"}
    assert contract.offered == {"extract_text", "ask_user", "update_plan"}
    assert contract.required <= contract.offered <= contract.executable

    assert requested_capabilities(
        "Use local OCR to extract text from /workspace/screenshot.png. Read only.",
        workspace=True,
    ) == {"ocr"}


@pytest.mark.parametrize("message,expected", [
    ("Can you find the nearest pharmacy?", {"search_browser"}),
    ("Write a JavaScript function", {"shell_files"}),
    ("Create a task", {"tasks"}),
    ("Remind me to buy milk", {"notes"}),
    ("Set a reminder", {"notes"}),
    ("Open the calendar", {"ui"}),
    ("Enable web", {"ui"}),
    ("Search the web for calendar software", {"search_browser"}),
    ("Write an email about the document", {"email"}),
    ("How do I delete calendar events?", set()),
    ("Can you explain notes and tasks?", set()),
    ("What is a language model?", set()),
    ("My cat ate my homework", set()),
    ("", set()),
])
def test_generic_intents_and_conceptual_questions(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("message,expected", [
    ("hey quick thing, can u search the web for gpt-4", {"search_browser"}),
    ("quick search: gpt-4 official source", {"search_browser"}),
    ("hey can u show me my docs?", {"documents"}),
    ("quick one, whats my week look like on the calender?", {"calendar"}),
    ("Quick check: what scheduled taks do I have?", {"tasks"}),
    ("quick one: list my cookbook servers", {"cookbook_admin"}),
])
def test_conversational_request_wrappers_preserve_explicit_family(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("message", [
    "can u also pop the notes panel open for me?",
    "k leave it for now - also pop open the calendar panel",
])
def test_pop_open_panel_routes_to_ui(message):
    assert requested_capabilities(message) == {"ui"}


@pytest.mark.parametrize("message", [
    "set the theme to dark",
    "go dark mode pls",
    "hmm actually switch it back to light",
])
def test_theme_changes_route_to_ui(message):
    assert requested_capabilities(message) == {"ui"}


def test_pull_up_theme_controls_routes_to_ui():
    assert requested_capabilities("hey can you pull up the theme controls for me") == {"ui"}


def test_contextual_ui_view_change_routes_to_ui():
    assert requested_capabilities("now flip it to the models view") == {"ui"}


def test_combined_request_and_explicit_new_request():
    message = "List my calendar events and search the web for lunch recipes"
    assert requested_capabilities(message) == {"calendar", "search_browser"}
    history = [{"role": "user", "content": message}]
    assert requested_capabilities("Create a note", history) == {"notes"}
    assert requested_capabilities("Do that again", history) == {"calendar", "search_browser"}


def test_email_and_document_are_independent_actions():
    assert requested_capabilities("Draft an email and create a document") == {"email", "documents"}


@pytest.mark.parametrize("prompt,tool", [
    ("Create a temporary chat called Review Relay using model provider/model.", "create_session"),
    ("Start a new session named Scratchpad with model provider/model.", "create_session"),
    ("Send the Review Relay chat this message: READY.", "send_to_session"),
    ("Message the Scratchpad session with: CONTINUE.", "send_to_session"),
    ("Search my prior chat transcripts for the exact phrase READY.", "search_chats"),
    ("Find CONTINUE in my previous conversation transcripts.", "search_chats"),
    ("Delete the Review Relay chat now.", "manage_session"),
    ("Remove the Scratchpad session.", "manage_session"),
])
def test_explicit_session_lifecycle_operations_select_session_tool(prompt, tool):
    expected_family = "memory" if tool == "search_chats" else "sessions"
    assert requested_capabilities(prompt) == {expected_family}
    expected_tools = {tool, 'list_sessions'} if tool == 'manage_session' else {tool}
    assert selected_tools_for_request(prompt) == expected_tools


@pytest.mark.parametrize("prompt,tool", [
    ("Show all configured model endpoints and their enabled status.", "manage_endpoints"),
    ("List endpoint configurations and flag disabled ones.", "manage_endpoints"),
    ("Check connected MCP servers and their registered tools.", "manage_mcp"),
    ("List installed MCP server connections.", "manage_mcp"),
    ("List API tokens by name and prefix only.", "manage_tokens"),
    ("Show configured access token names without secrets.", "manage_tokens"),
    ("Check webhook integrations and whether reminders are configured.", "manage_webhooks"),
    ("List configured webhooks and their enabled status.", "manage_webhooks"),
])
def test_explicit_admin_inventory_selects_resource_manager(prompt, tool):
    assert requested_capabilities(prompt) == {"cookbook_admin"}
    assert selected_tools_for_request(prompt) == {tool}


@pytest.mark.parametrize("prompt,tool", [
    ("Ask provider/model for a one-sentence downside case.", "chat_with_model"),
    ("Have provider/model answer this short finance question.", "chat_with_model"),
    ("Run a two-step model pipeline to draft and then tighten the answer.", "pipeline"),
    ("Use a pipeline with provider/first followed by provider/second.", "pipeline"),
])
def test_explicit_model_delegation_selects_execution_surface(prompt, tool):
    assert requested_capabilities(prompt) == {"sessions"}
    assert selected_tools_for_request(prompt) == {tool}


@pytest.mark.parametrize("prompt,tool", [
    ("Send a message from Primary Inbox to person@example.com with subject Status and body Ready.", "send_email"),
    ("Email person@example.com now from my primary account; subject Status, body Ready.", "send_email"),
    ("Reply now to UID 1001 saying: Thanks, I will review it.", "reply_to_email"),
    ("Respond to email UID 2004 with: Confirmed for Thursday.", "reply_to_email"),
])
def test_explicit_outbound_email_operations_select_delivery_tool(prompt, tool):
    assert requested_capabilities(prompt) == {"email"}
    assert selected_tools_for_request(prompt) == {tool}


@pytest.mark.parametrize("prompt", [
    "Read the Primary Inbox email with UID 1001.",
    "Open the message with UID 2004.",
    "Show email UID 3001 from my inbox.",
    "Read message UID abc-123 before replying.",
])
def test_explicit_email_uid_read_selects_reader(prompt):
    assert requested_capabilities(prompt) == {"email"}
    assert selected_tools_for_request(prompt) == {"read_email"}


@pytest.mark.parametrize("prompt", [
    "Open the document with ID 70f10211642252928d705fcf994d65e4.",
    "Read document ID doc_abc-123.",
    "View the document with id report-42.",
    "Open document id 987654321.",
])
def test_document_id_read_is_data_access_not_panel_navigation(prompt):
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "manage_documents"
    assert operation.args["action"] == "read"
    assert requested_capabilities(prompt) == {"documents"}


@pytest.mark.parametrize("prompt", [
    "Look at webhook integrations and whether a reminder webhook exists.",
    "Look at my configured webhooks.",
    "Review webhook integrations and list their enabled status.",
    "Inspect the webhooks configured for reminders.",
])
def test_webhook_inventory_lookups_select_webhook_manager(prompt):
    assert selected_tools_for_request(prompt) == {"manage_webhooks"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}


@pytest.mark.parametrize("prompt", [
    "Review this open document and create one inline suggestion.",
    "Proofread the open document and suggest a correction.",
    "Suggest improvements to this document.",
    "Can you broaden this planning review?",
    "Go deeper on the first claim and separate the evidence.",
    "Lighten up the wording throughout.",
    "Give me feedback on the current draft.",
])
def test_active_document_review_requests_are_document_actions(prompt):
    assert requested_capabilities(prompt, active_document=True) == {"documents"}


@pytest.mark.parametrize("prompt", [
    "Start a concise new research report on retention and return the task id.",
    "Kick off a short research report about retrieval quality and give me the task id.",
    "Run new research on interface language and return its job id.",
    "Research cloud cost forecasts and save the resulting report.",
])
def test_research_artifact_creation_is_not_stolen_by_task_metadata(prompt):
    assert requested_capabilities(prompt) == {"research"}


def test_explicit_odysseus_search_workflow_is_not_stolen_by_task_label_or_subject():
    prompt = (
        "Use Odysseus web_search to discover authoritative evidence; do not answer "
        "from memory. After each search, check whether its snippets cover every claim. "
        "Fetch the strongest source pages and answer only from retrieved evidence.\n\n"
        "Task: Compare Python pathlib Path.resolve(strict=False), Path.absolute(), "
        "and os.path.realpath(strict=os.path.ALLOW_MISSING)."
    )

    assert selected_tools_for_request(prompt) == {"web_search", "web_fetch"}
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "Open that one in the research panel.",
    "Show that saved report in the research sidebar.",
    "Read it in the research view.",
    "Open the latest report in research.",
])
def test_saved_research_surface_uses_research_tool_not_unsupported_ui_panel(prompt):
    assert requested_capabilities(prompt) == {"research"}


@pytest.mark.parametrize("prompt", [
    "Pull up my saved research reports.",
    "Bring up my saved research reports.",
    "Retrieve my saved research reports.",
    "Get my saved research reports.",
])
def test_retrieval_synonyms_route_named_saved_store(prompt):
    assert requested_capabilities(prompt) == {"research"}


@pytest.mark.parametrize("prompt", [
    "Since next week looks open, delete the Loose Ends note.",
    "If the schedule is clear, remove the Reply Queue note.",
    "Given that result, delete the Usability Themes note.",
    "With no upcoming events, remove the Cloud Questions note.",
])
def test_conditional_action_routes_explicit_product_target(prompt):
    assert requested_capabilities(prompt) == {"notes"}


@pytest.mark.parametrize("prompt", [
    "What's scheduled for next week?",
    "Do I have anything next week?",
    "What is coming up next week?",
    "Anything on there for Tuesday?",
])
def test_calendar_panel_open_establishes_typed_followup_context(prompt):
    history = [
        {"role": "user", "content": "Open my calendar panel."},
        {"role": "assistant", "content": "Calendar is open.", "metadata": {
            "tool_events": [{
                "tool": "ui_control",
                "command": '{"action":"open_panel","name":"calendar"}',
                "exit_code": 0,
            }],
        }},
    ]
    assert requested_capabilities(prompt, history) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "Block off Tuesday at 2 PM for review.",
    "Reserve Wednesday at 9 AM for planning.",
    "Move that to 10:30 AM on Monday.",
    "Reschedule it for Friday afternoon.",
])
def test_calendar_action_followup_inherits_successful_calendar_context(prompt):
    history = [
        {"role": "assistant", "content": "Events shown.", "metadata": {
            "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
        }},
    ]
    assert requested_capabilities(prompt, history) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "anything happening tomorrow afternoon",
    "does anything land on weds?",
    "anything tmrw?",
    "anything tomorow?",
])
def test_temporal_event_followup_inherits_successful_calendar_context(prompt):
    history = [
        {"role": "assistant", "content": "Events shown.", "metadata": {
            "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
        }},
    ]
    assert requested_capabilities(prompt, history) == {"calendar"}


def test_conversational_calendar_lookup_routes_to_calendar_without_history():
    assert requested_capabilities("cool, anything on my calendar today?") == {"calendar"}
    assert requested_capabilities("can you also check what i have on today?") == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "whats on today",
    "whats my sched today",
    "whats my cal look like next week",
    "whens my calendar next month looking busy?",
])
def test_terse_personal_calendar_idioms_route_without_history(prompt):
    assert requested_capabilities(prompt) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "what notes have i got right now",
    "whats left on the launch QA checklist?",
    "didnt i have a note about the offsite prep",
])
def test_natural_note_inventory_and_lookup_idioms_route_without_history(prompt):
    assert requested_capabilities(prompt) == {"notes"}


def test_natural_webhook_inventory_selects_webhook_manager():
    prompt = "what webhooks do i have set up?"
    assert selected_tools_for_request(prompt) == {"manage_webhooks"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}


def test_suspicious_inbox_wording_selects_spam_scan():
    prompt = "anything sketchy sitting in my inbox?"
    assert selected_tools_for_request(prompt) == {"scan_spam"}
    assert requested_capabilities(prompt) == {"email"}


def test_local_model_discovery_selects_hugging_face_search_and_persistence_switches_to_notes():
    prompt = "im after a small qwen instruct model i could actually run at home"
    assert selected_tools_for_request(prompt) == {"search_hf_models"}
    assert requested_capabilities(prompt) == {"search_browser"}

    history = [{"role": "assistant", "content": "Candidates.", "metadata": {
        "tool_events": [{"tool": "search_hf_models", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "anything in the 3-4b range that people actually use?", history,
    ) == {"search_browser"}
    assert requested_capabilities(
        "nice, jott those down somewhere i can find later", history,
    ) == {"notes"}


def test_unrelated_model_terms_do_not_combine_into_hugging_face_discovery():
    prompt = (
        "You are in a restricted environment. Use the available tools. Prepare my daily "
        "arXiv paper digest. Classify papers under Multimodal / Vision-Language Models. Based "
        "on my research interests, highlight papers I might find interesting. If any "
        "paper benchmarks against CapRL, extract the comparison results. Save the digest "
        "to /tmp_workspace/results/digest.md.\n"
        "| CapRL-3B | result |"
    )
    assert selected_tools_for_request(prompt) != {"search_hf_models"}
    assert "search_browser" in requested_capabilities(prompt)


def test_execution_boilerplate_does_not_route_to_background_tasks():
    prompt = (
        "Solve the task efficiently before the timeout (600s). Use the available tools. "
        "Unpack /tmp_workspace/images.tar and classify the images into output folders."
    )
    capabilities = requested_capabilities(prompt)
    assert capabilities == {"shell_files"}


def test_referential_web_source_relationship_keeps_web_tools_warm():
    history = [{"role": "assistant", "content": "Official link.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "is that the same one linked from the pip docs?", history,
    ) == {"search_browser"}


def test_adjacent_recent_topic_followup_keeps_web_search_warm():
    history = [{"role": "assistant", "content": "Recent Germany news.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "anything new on the economy there?", history,
    ) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "settling an argument here — wheres the best barbeque in the world",
    "what country has the best meat?",
    "when exactly did ethiopia gain independence",
])
def test_implicit_verification_questions_offer_web_tools(prompt):
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "kansas city vs texas, which one actually",
    "i mean the 1941 stuff and the treaties before it",
])
def test_web_topic_refinement_without_search_word_keeps_web_warm(prompt):
    history = [{"role": "assistant", "content": "Search result.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_web_refinement_retains_one_failed_unblocked_search_attempt():
    history = [{"role": "assistant", "content": "No results.", "metadata": {
        "tool_events": [{
            "tool": "web_search", "exit_code": 1, "error": True,
            "execution_attempted": True, "blocked": False,
        }],
    }}]
    assert requested_capabilities(
        "so if i only care about beef which country wins", history,
    ) == {"search_browser"}


def test_settings_modal_is_ui_navigation():
    assert selected_tools_for_request("pop open the settings modal") == {"ui_control"}
    assert requested_capabilities("pop open the settings modal") == {"ui"}


def test_current_month_topic_question_routes_web_search():
    prompt = "anything new in quantum computing this month?"
    assert selected_tools_for_request(prompt) == {"web_search"}
    assert requested_capabilities(prompt) == {"search_browser"}


def test_two_url_comparison_does_not_treat_documentation_as_document_library():
    prompt = (
        "Use Odysseus web retrieval tools to open both URLs and compare RFC 9309 "
        "with Google's robots.txt documentation, citing the evidence.\n"
        "https://www.rfc-editor.org/rfc/rfc9309.txt\n"
        "https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt"
    )
    assert selected_tools_for_request(prompt) == {"web_fetch"}
    assert requested_capabilities(prompt) == {"search_browser"}
    assert required_read_operation_for_request(prompt) is None


def test_two_url_comparison_does_not_read_saved_memory_from_evidence_warning():
    prompt = (
        "Open both URLs before answering; do not answer from memory. Compare "
        "their evidence and cite both sources.\n"
        "https://science.nasa.gov/mars/facts/\n"
        "https://science.nasa.gov/earth/facts/"
    )
    assert selected_tools_for_request(prompt) == {"web_fetch"}
    assert requested_capabilities(prompt) == {"search_browser"}
    assert required_read_operation_for_request(prompt) is None


def test_exact_pdf_ocr_artifact_workflow_uses_compact_native_tool_chain():
    prompt = (
        "Use OCR/media inspection on the image-only PDF 'invoice_scan.pdf'. "
        "Extract the requested values. Write a concise Markdown report to "
        "'result.md', then read the saved file to verify it before finishing."
    )
    assert selected_tools_for_request(prompt) == {
        "inspect_media", "extract_text", "write_file", "read_file",
    }


def test_explicit_pdf_evidence_or_artifact_chain_stays_compact():
    prompt = (
        "Inspect invoice.pdf with exactly one evidence tool: use extract_text "
        "or inspect_media. Then use write_file for result.md, use read_file "
        "to verify it, and stop."
    )
    assert selected_tools_for_request(prompt) == {
        "inspect_media", "extract_text", "write_file", "read_file",
    }


@pytest.mark.parametrize("prompt", [
    "ok one quick lookup to confirm search works - 'sqlite wal mode'",
    "does a normal search actually respect that? try one on python 3.13 whats new",
])
def test_explicit_search_test_switches_from_settings_to_web(prompt):
    assert selected_tools_for_request(prompt) == {"web_search"}
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "quick japan news rundown?",
    "hey whats goin on in japan right now? quick version pls",
])
def test_colloquial_current_news_routes_web(prompt):
    assert selected_tools_for_request(prompt) == {"web_search"}
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize(("prompt", "tool", "family"), [
    ("jot a reminder to call the dentist tomorow at 9am", "manage_notes", "notes"),
    ("if thats a gap, jot it in my notes so i dont forget", "manage_notes", "notes"),
    ("ok ping me every morning at 7 with the pollen level", "manage_tasks", "tasks"),
])
def test_explicit_colloquial_store_actions_select_the_named_tool(prompt, tool, family):
    assert selected_tools_for_request(prompt) == {tool}
    assert requested_capabilities(prompt) == {family}


def test_time_sensitive_search_followup_keeps_web_family():
    history = [{"role": "assistant", "content": "No useful results.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities("Is tomorrow bad for allergies?", history) == {"search_browser"}
    assert requested_capabilities(
        "k — anything else big happenin there this week?", history,
    ) == {"search_browser"}


def test_contact_to_email_history_switch_is_explicit():
    history = [{"role": "assistant", "content": "Casey Morgan", "metadata": {
        "tool_events": [{"tool": "manage_contact", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "is this the same casey i emailed last month?", history,
    ) == {"email"}
    assert requested_capabilities(
        "also check if shes saved under priya shaw or priya sha by mistake", history,
    ) == {"contacts"}


def test_personal_commitment_on_the_books_routes_calendar():
    prompt = "do i still have that coffee with priya on the books?"
    assert selected_tools_for_request(prompt) == {"manage_calendar"}
    assert requested_capabilities(prompt) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "next month date with marzia at tokyo tower 9:30, reservation code 502i93jf",
    "lunch with maria tomorrow 12:30, the place is called Toe",
])
def test_implicit_dated_commitment_routes_calendar(prompt):
    assert selected_tools_for_request(prompt) == {"manage_calendar"}
    assert requested_capabilities(prompt) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "wheres the official site for the python packaging user guide?",
    "where do i find the official site for the python packaging user guide?",
])
def test_where_is_official_site_routes_web(prompt):
    assert selected_tools_for_request(prompt) == {"web_search"}
    assert requested_capabilities(prompt) == {"search_browser"}


def test_hf_search_model_card_followup_keeps_web_family():
    history = [{"role": "assistant", "content": "Top pick: Qwen", "metadata": {
        "tool_events": [{"tool": "search_hf_models", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "ok open the top pick's model card so i can read the license before i decide",
        history,
    ) == {"search_browser"}


def test_email_draft_paragraph_revision_switches_to_documents():
    history = [{"role": "assistant", "content": "Draft ready.", "metadata": {
        "tool_events": [{"tool": "mcp__email__draft_email_reply", "exit_code": 0}],
    }}]
    assert requested_capabilities("tighten the middle paragraph", history) == {"documents"}


@pytest.mark.parametrize("prompt", [
    "review SFT traces at 9am",
    "yeah make it a daily thing and keep the prompt short",
])
def test_scheduled_work_shorthand_routes_tasks(prompt):
    assert selected_tools_for_request(prompt) == {"manage_tasks"}
    assert requested_capabilities(prompt) == {"tasks"}


@pytest.mark.parametrize("tool,prompt,expected", [
    ("manage_notes", "check off the smoke test line", {"notes"}),
    ("mcp__email__scan_spam", "ok what's left flagged?", {"email"}),
    ("manage_webhooks", "which events is each one listening for?", {"cookbook_admin"}),
    ("manage_calendar", "which day is the heaviest", {"calendar"}),
    ("manage_calendar", "just show me the week of the 15th", {"calendar"}),
])
def test_typed_result_followups_keep_the_executed_family(tool, prompt, expected):
    history = [{"role": "assistant", "content": "Done.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == expected


@pytest.mark.parametrize("prompt", [
    "whats on this week",
    "what do i have today",
    "do i have anything on friday afternoon?",
    "whats my sept look like",
])
def test_implicit_personal_calendar_time_queries_route_without_calendar_noun(prompt):
    assert requested_capabilities(prompt) == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "and tomorow?",
    "show me next week then",
    "k whats the next thing after that",
    "cool, where is that one again?",
    "anything in the first week of it",
    "is the 22nd clear",
])
def test_calendar_result_temporal_and_item_followups_stay_calendar(prompt):
    history = [{"role": "assistant", "content": "Events.", "metadata": {
        "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"calendar"}


@pytest.mark.parametrize("prompt,tool", [
    ("quick lookup to back that up please", "web_search"),
    ("yeah do a quick search on that", "web_search"),
    ("fetch the top result and pull the bit about layout order", "web_fetch"),
    ("open the second link and get the paragraph about tables", "web_fetch"),
])
def test_explicit_evidence_followups_select_search_or_fetch(prompt, tool):
    assert selected_tools_for_request(prompt) == {tool}
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "k can you open calendar that month",
    "opne that in the calendar view",
    "open that up in the calendar panel so i can see it",
])
def test_calendar_surface_followup_is_ui_only_and_does_not_require_a_relist(prompt):
    history = [{"role": "assistant", "content": "October events.", "metadata": {
        "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
    }}]
    assert selected_tools_for_request(prompt) == {"ui_control"}
    assert required_read_operation_for_request(prompt, history) is None
    assert requested_capabilities(prompt, history) == {"ui"}


def test_typoed_inbox_summary_requires_email_inventory_and_keeps_followup():
    prompt = "Summarize my inboxs last 3 emails"
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "list_emails"
    assert operation.max_items == 3
    assert requested_capabilities(prompt) == {"email"}

    history = [{"role": "assistant", "content": "Three messages.", "metadata": {
        "tool_events": [{"tool": "mcp__email__list_emails", "exit_code": 0}],
    }}]
    assert requested_capabilities("k which ones waiting on me", history) == {"email"}
    assert requested_capabilities("Let's review Casey's", history) == {"email"}


def test_latest_named_youtube_upload_routes_discovery_and_video_tools():
    prompt = "what does steams latest youtube video say"
    assert selected_tools_for_request(prompt) == {"web_search", "youtube_tool"}
    assert requested_capabilities(prompt) == {"search_browser"}

    history = [{"role": "assistant", "content": "Video summary.", "metadata": {
        "tool_events": [{"tool": "youtube_tool", "exit_code": 0}],
    }}]
    assert requested_capabilities("hows old is it", history) == {"search_browser"}
    assert requested_capabilities("ok and does it mention any sale", history) == {"search_browser"}


@pytest.mark.parametrize("prompt,tools", [
    ("does anthropic have a youtube channel", {"web_search"}),
    ("whats openai's latest video", {"web_search", "youtube_tool"}),
    ("whats pewdiepies latest video", {"web_search", "youtube_tool"}),
])
def test_named_channel_discovery_routes_web_and_youtube(prompt, tools):
    assert selected_tools_for_request(prompt) == tools
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "whats rainbolts latest 5 videos?",
    "casey neistat latest video?",
    "has abroad in japan uploaded?",
])
def test_natural_creator_upload_queries_route_web_and_youtube(prompt):
    assert selected_tools_for_request(prompt) == {"web_search", "youtube_tool"}
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "which one is about airports?",
    "what does he say the answer is in that one?",
    "when did it go up?",
    "how is his last 3 videos doing",
    "which one did best?",
    "is it long or a short?",
    "open it",
    "whats the feedback on it",
    "any common complaints?",
])
def test_creator_video_followups_keep_search_browser(prompt):
    history = [{"role": "assistant", "content": "Latest upload.", "metadata": {
        "tool_events": [{"tool": "youtube_tool", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_natural_mcp_inventory_and_followup_keep_admin_tools():
    prompt = "wich mcp servers are hooked up?"
    assert selected_tools_for_request(prompt) == {"manage_mcp"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}

    history = [{"role": "assistant", "content": "MCP servers.", "metadata": {
        "tool_events": [{"tool": "manage_mcp", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "what tools does the filesystem one expose?", history
    ) == {"cookbook_admin"}

    operation = required_read_operation_for_request(
        "show me all the mcp tools available, read only pls"
    )
    assert operation is not None
    assert operation.tool == "manage_mcp"
    assert operation.args == {"action": "list_tools"}


@pytest.mark.parametrize("prompt", [
    "when did it go up?",
    "open it so i can watch later",
    "try that again",
])
def test_immediate_referential_followup_keeps_verified_failed_tool_family(prompt):
    history = [
        {"role": "user", "content": "has abroad in japan uploaded?"},
        {"role": "assistant", "content": "That lookup failed.", "metadata": {
            "tool_events": [{
                "tool": "youtube_tool", "exit_code": 1, "error": True,
                "execution_attempted": True, "blocked": False,
            }],
        }},
    ]
    assert requested_capabilities(prompt, history) == {"search_browser"}


@pytest.mark.parametrize("prompt,tools,family", [
    ("any webhooks hooked up?", {"manage_webhooks"}, "cookbook_admin"),
    ("switch image gen off for a bit", {"manage_settings"}, "cookbook_admin"),
    (
        "Find the latest stable Rust release and tell me one breaking-adjacent change developers should notice.",
        {"web_search"},
        "search_browser",
    ),
    ("hey what chats have i got going rite now?", {"list_sessions"}, "sessions"),
    (
        "show unread from my second account",
        {"list_email_accounts", "list_emails"},
        "email",
    ),
    (
        "open google flights alt search for tokyo to stockholm next month",
        {"private_browser"},
        "search_browser",
    ),
    (
        "open steam and find the current reviews for factorio",
        {"private_browser"},
        "search_browser",
    ),
])
def test_natural_inventory_settings_and_site_operations(prompt, tools, family):
    assert selected_tools_for_request(prompt) == tools
    assert requested_capabilities(prompt) == {family}


@pytest.mark.parametrize("tool,prompt,family", [
    ("manage_webhooks", "is one of them for reminders?", "cookbook_admin"),
    ("manage_settings", "alright put it back on now", "cookbook_admin"),
    ("manage_settings", "chek thats its really back on", "cookbook_admin"),
    ("list_sessions", "wich one did i touch most recently?", "sessions"),
    ("mcp__email__list_emails", "any new ones on that account since?", "email"),
    ("mcp__email__list_emails", "k list them", "email"),
    ("private_browser", "yes compare with other sources", "search_browser"),
    ("private_browser", "ok which one wins on total travel time?", "search_browser"),
    ("private_browser", "what percent are positive?", "search_browser"),
])
def test_typed_inventory_and_site_followups_keep_family(tool, prompt, family):
    history = [{"role": "assistant", "content": "Result.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {family}


@pytest.mark.parametrize("prompt", ["how many views", "and likes?"])
def test_result_attributes_keep_immediately_failed_youtube_family(prompt):
    history = [
        {"role": "user", "content": "whats markipliers latest video"},
        {"role": "assistant", "content": "Lookup failed.", "metadata": {
            "tool_events": [{
                "tool": "youtube_tool", "exit_code": 1, "error": True,
                "execution_attempted": True, "blocked": False,
            }],
        }},
    ]
    assert requested_capabilities(prompt, history) == {"search_browser"}


@pytest.mark.parametrize("prompt,history", [
    (
        "alright put it back on now",
        [
            {"role": "user", "content": "switch image gen off for a bit"},
            {"role": "assistant", "content": "I cannot do that."},
        ],
    ),
    (
        "chek thats its really back on",
        [
            {"role": "user", "content": "switch image gen off for a bit"},
            {"role": "assistant", "content": "I cannot do that."},
            {"role": "user", "content": "alright put it back on now"},
            {"role": "assistant", "content": "No change was made."},
        ],
    ),
])
def test_referential_action_inherits_recent_explicit_user_operation_without_tool_event(
    prompt, history
):
    assert requested_capabilities(prompt, history) == {"cookbook_admin"}


@pytest.mark.parametrize("prompt", [
    "which settings did they use for terminal bench 2.1?",
    "quote the exact line so i can see it",
])
def test_fetched_page_detail_and_quote_followups_keep_web_family(prompt):
    history = [{"role": "assistant", "content": "Fetched model page.", "metadata": {
        "tool_events": [{"tool": "web_fetch", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_waiting_inbox_and_newest_attachment_followup_keep_email_family():
    prompt = "anything waiting in my inbox?"
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "list_emails"
    assert requested_capabilities(prompt) == {"email"}

    history = [{"role": "assistant", "content": "Newest email.", "metadata": {
        "tool_events": [{"tool": "mcp__email__list_emails", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "what does the file thats attached to the newest one say?", history
    ) == {"email"}


@pytest.mark.parametrize("prompt,tools,family", [
    (
        "And compared to if i use anthropic models, whats the equivallent?",
        {"web_search"},
        "search_browser",
    ),
    ("whats the latest nvidia driver for linux?", {"web_search"}, "search_browser"),
    (
        "navigate nitori.jp and find option for desks",
        {"private_browser"},
        "search_browser",
    ),
    (
        "I've got a big pile of trash behind my house and need someone to haul it away. "
        "I'm in Japan — can you find services that will give me a quote?",
        {"web_search"},
        "search_browser",
    ),
])
def test_natural_external_comparison_release_navigation_and_service_search(
    prompt, tools, family
):
    assert selected_tools_for_request(prompt) == tools
    assert requested_capabilities(prompt) == {family}


@pytest.mark.parametrize("prompt", [
    "quick breif of my latest emails",
    "find anything urgent that came in recently",
])
def test_natural_recent_email_briefs_require_inventory(prompt):
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "list_emails"
    assert requested_capabilities(prompt) == {"email"}


@pytest.mark.parametrize("tool,prompt,family", [
    ("web_search", "which ones closest in price to the cheap one", "search_browser"),
    ("web_search", "open that pricing page, i wanna look myself", "search_browser"),
    ("web_search", "is that the production branch or a beta?", "search_browser"),
    ("web_search", "k does it support kernal 6.12", "search_browser"),
    ("private_browser", "only show ones under 20000 yen", "search_browser"),
    ("private_browser", "open the top one and tell me the dimensions", "search_browser"),
    ("web_search", "which ones have an app where i can just snap a photo and get a price?", "search_browser"),
    ("web_search", "pick from quote apps the 2 that look most trustworthy and tell me why", "search_browser"),
    ("mcp__email__list_emails", "summarize what each one says", "email"),
    ("mcp__email__list_emails", "anything from the bank in there?", "email"),
    ("mcp__email__list_emails", "open that one", "email"),
])
def test_external_and_email_result_followups_retain_typed_family(tool, prompt, family):
    history = [{"role": "assistant", "content": "Results.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {family}


@pytest.mark.parametrize("prompt,history", [
    (
        "only show ones under 20000 yen",
        [
            {"role": "user", "content": "navigate nitori.jp and find option for desks"},
            {"role": "assistant", "content": "The site did not load."},
        ],
    ),
    (
        "open the top one and tell me the dimensions",
        [
            {"role": "user", "content": "navigate nitori.jp and find option for desks"},
            {"role": "assistant", "content": "The site did not load."},
            {"role": "user", "content": "only show ones under 20000 yen"},
            {"role": "assistant", "content": "Please open it yourself."},
        ],
    ),
])
def test_browser_followups_inherit_recent_explicit_navigation_without_tool_evidence(
    prompt, history
):
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_visible_editor_owns_style_rewrite_despite_email_words_in_style_payload():
    prompt = (
        'Rewrite the open document to match my configured Writing Style setting. '
        'Preserve the meaning and create inline suggestions only; do not apply changes.\n\n'
        'Use a concise, friendly tone. Acknowledge the sender request and sign off with '
        'the mailbox owner first name.'
    )
    assert requested_capabilities(prompt, active_document=True) == {"documents"}


@pytest.mark.parametrize("prompt", [
    "is it actually official or some fan account",
    "whats their latest video",
    "hmm are you sure thats the newest? that one looked kinda old",
    "do people like it?",
    "when did it come out",
    "whats the video about?",
    "what are comments",
])
def test_youtube_discovery_followups_keep_search_browser(prompt):
    history = [{"role": "assistant", "content": "Channel result.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


@pytest.mark.parametrize("prompt,maximum", [
    ("Give me a rundown of my latest emails", None),
    ("Summarize latest emails for each account", None),
])
def test_natural_latest_email_summaries_require_email_inventory(prompt, maximum):
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "list_emails"
    assert operation.max_items == maximum
    assert requested_capabilities(prompt) == {"email"}


@pytest.mark.parametrize("prompt", [
    "anything in there thats urgent",
    "which account has the most unread",
])
def test_email_summary_followups_keep_email(prompt):
    history = [{"role": "assistant", "content": "Messages.", "metadata": {
        "tool_events": [{"tool": "mcp__email__list_emails", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"email"}


@pytest.mark.parametrize("prompt", [
    "give me a list of my recent chats with links i can actually click",
    "help me find that scratch chat i made a bit ago",
])
def test_session_history_language_routes_sessions_not_web(prompt):
    assert requested_capabilities(prompt) == {"sessions"}
    assert "list_sessions" in selected_tools_for_request(prompt)


@pytest.mark.parametrize("prompt", [
    "now just the important ones",
    "which model is it on",
    "cool, keep it but mark it important",
])
def test_session_result_followups_keep_sessions(prompt):
    history = [{"role": "assistant", "content": "Chats.", "metadata": {
        "tool_events": [{"tool": "list_sessions", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"sessions"}


def test_fragmented_calendar_create_keeps_family_across_clarifications():
    prompts = [
        "next week add calendar meeting with alon",
        "friday",
        "1 hour should be fine",
        "just give me an exact time that works",
    ]
    history = []
    for prompt in prompts:
        assert requested_capabilities(prompt, history) == {"calendar"}
        history.extend([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "What time?"},
        ])


def test_natural_model_inventory_and_followups_route_cookbook_admin():
    prompt = "what models are available to me right now?"
    assert selected_tools_for_request(prompt) == {"list_models"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}
    history = [{"role": "assistant", "content": "Models.", "metadata": {
        "tool_events": [{"tool": "list_models", "exit_code": 0}],
    }}]
    assert requested_capabilities("any of them qwen?", history) == {"cookbook_admin"}
    assert requested_capabilities(
        "ok and where are those served from? just curious, dont change anything", history,
    ) == {"cookbook_admin"}


def test_busiest_day_calendar_wording_routes_calendar():
    assert requested_capabilities("Whats my busiest day next week?") == {"calendar"}
    assert requested_capabilities("What do I have after 5pm today?") == {"calendar"}


@pytest.mark.parametrize("prompt", [
    "open calendar 2028 august",
    "open calendar 2027 december",
])
def test_calendar_ui_accepts_year_before_month(prompt):
    assert selected_tools_for_request(prompt) == {"ui_control"}
    assert requested_capabilities(prompt) == {"ui"}


@pytest.mark.parametrize("prompt", [
    "latest nvidia linux driver?",
    "has veritasium uploaded anything?",
    "did veritasium post a new one?",
])
def test_latest_release_and_upload_discovery_routes_web(prompt):
    assert "web_search" in selected_tools_for_request(prompt)
    assert requested_capabilities(prompt) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "show me how far along the model downloads are, keep it short",
    "whats in flight in the cookbook download queue?",
])
def test_natural_download_progress_selects_download_inventory(prompt):
    assert selected_tools_for_request(prompt) == {"list_downloads"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}


def test_release_notes_are_web_content_and_remembering_url_is_memory():
    assert selected_tools_for_request("can you open the ruby release notes") == {"web_search", "web_fetch"}
    assert requested_capabilities("can you open the ruby release notes") == {"search_browser"}
    history = [{"role": "assistant", "content": "Ruby release page.", "metadata": {
        "tool_events": [{"tool": "web_fetch", "exit_code": 0}],
    }}]
    assert requested_capabilities("is that the newest version or an older page", history) == {"search_browser"}
    assert requested_capabilities(
        "remember that release notes url so i dont have to ask again", history,
    ) == {"memory"}


@pytest.mark.parametrize("prompt", [
    "anything sitting in my inbox undone",
    "whats still undone in my mail",
])
def test_unfinished_mail_wording_routes_email(prompt):
    assert requested_capabilities(prompt) == {"email"}


@pytest.mark.parametrize("prompt", [
    "open Helios launch recap",
    "open the Helios launch recap one",
    "open the attachment too",
])
def test_email_result_open_followups_stay_email(prompt):
    history = [{"role": "assistant", "content": "Messages.", "metadata": {
        "tool_events": [{"tool": "mcp__email__list_emails", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"email"}


def test_google_maps_navigation_uses_private_browser_and_keeps_followups():
    prompt = "use google maps to navigate from shinjuku station to tokyo tower"
    assert selected_tools_for_request(prompt) == {"private_browser"}
    assert requested_capabilities(prompt) == {"search_browser"}
    history = [{"role": "assistant", "content": "Directions.", "metadata": {
        "tool_events": [{"tool": "private_browser", "exit_code": 0}],
    }}]
    assert requested_capabilities("which lines and how long does it take?", history) == {"search_browser"}
    assert requested_capabilities("and the last train back tonight?", history) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "Go to http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html and find orange sofas.",
    "Visit http://localhost:7011/static/test-fixtures/browser-catalog.html then click the first sofa.",
    "Open https://example.com and report the rendered heading.",
])
def test_explicit_navigation_selects_private_browser_for_any_url_host(prompt):
    assert selected_tools_for_request(prompt) == {"private_browser"}
    assert requested_capabilities(prompt) == {"search_browser"}


def test_private_browser_navigation_does_not_require_web_search_toggle():
    prompt = (
        "Go to http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html "
        "and find orange sofas."
    )
    selected = selected_tools_for_request(prompt)
    contract = resolve(
        requested_capabilities(prompt),
        selected_tools=selected,
        required_tools=selected,
        policy=ToolPolicy(disabled_tools=frozenset({"web_search", "web_fetch"})),
    )
    assert contract.required == {"private_browser"}
    assert contract.permits("private_browser")
    assert not contract.unavailable


def test_webhook_status_and_event_followup_route_webhook_manager():
    prompt = "just show me the webhook status, dont change a thing"
    assert selected_tools_for_request(prompt) == {"manage_webhooks"}
    assert requested_capabilities(prompt) == {"cookbook_admin"}
    history = [{"role": "assistant", "content": "Hooks.", "metadata": {
        "tool_events": [{"tool": "manage_webhooks", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "what events does the second one listen to?", history,
    ) == {"cookbook_admin"}


def test_project_code_search_overrides_warm_web_family():
    history = [{"role": "assistant", "content": "NumPy docs.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "check my project for any leftover numpy.matrix calls", history,
    ) == {"shell_files"}


def test_driver_upgrade_followup_offers_web_and_local_inspection():
    history = [{"role": "assistant", "content": "Driver release.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities("any wayland fixes in it", history) == {"search_browser"}
    assert requested_capabilities("Should i upgrade today?", history) == {"search_browser", "shell_files"}


@pytest.mark.parametrize("prompt", [
    "Block off Tuesday at 2 PM for review.",
    "Reserve Wednesday at 9 AM for planning.",
    "Block Thursday morning for focused work.",
    "Reserve Friday afternoon for Journal Club prep.",
])
def test_time_block_actions_route_to_calendar_without_hidden_history(prompt):
    assert requested_capabilities(prompt) == {"calendar"}


def test_personal_schedule_routes_to_calendar_instead_of_inheriting_warm_email():
    history = [
        {"role": "user", "content": "whats my email"},
        {"role": "assistant", "content": "You have two accounts.", "metadata": {
            "tool_events": [{"tool": "mcp__email__list_email_accounts"}],
        }},
        {"role": "user", "content": "whats my last 5 emails"},
        {"role": "assistant", "content": "Here are five messages.", "metadata": {
            "tool_events": [{"tool": "mcp__email__list_emails"}],
        }},
    ]
    assert requested_capabilities("whats my schedule this week?", history) == {"calendar"}
    assert requested_capabilities("List my scheduled tasks", history) == {"tasks"}


@pytest.mark.parametrize("prompt,expected", [
    ("What notes do I have with the settings label?", {"notes"}),
    ("What's in my memory?", {"memory"}),
    ("What skills do I have?", {"skills"}),
    ("What events are on my calendar for the next seven days?", {"calendar"}),
    ("What's sitting in my Primary Inbox right now?", {"email"}),
    ("What messages are waiting in my inbox?", {"email"}),
    ("Which emails are in our mailbox?", {"email"}),
    ("What is currently in my mail?", {"email"}),
])
def test_personal_store_lookup_grammar_routes_named_store(prompt, expected):
    """Natural lookup questions route by their named private store."""
    assert requested_capabilities(prompt) == expected


@pytest.mark.parametrize("prompt", [
    "Open the Settings cleanup note.",
    "Open the Calendar prep note.",
    "Show the Email follow-up note.",
    "Read the Model review note.",
])
def test_direct_note_object_owns_incidental_family_words_in_title(prompt):
    """A note title must not grant authority to a family named inside it."""
    assert requested_capabilities(prompt) == {"notes"}


@pytest.mark.parametrize("prompt", [
    "Add a note called Ops prep with an item to confirm Monday's meeting.",
    "Create a note titled Calendar cleanup containing: review the event list.",
    "Write a note named Email follow-up saying to check the inbox tomorrow.",
    "Save a note called Model review with a reminder to inspect endpoints.",
])
def test_direct_note_creation_owns_incidental_family_words_in_content(prompt):
    assert requested_capabilities(prompt) == {"notes"}


@pytest.mark.parametrize("prompt", [
    "Search my prior chat transcripts for the exact phrase ALPHA and show the matching chat.",
    "Search my chats for BETA and show me the matching conversation.",
    "Find GAMMA in my previous conversations and list the matching chats.",
    "Look through my past chat transcripts for DELTA and open the matching chat.",
])
def test_chat_search_with_result_display_is_one_memory_operation(prompt):
    assert selected_tools_for_request(prompt) == {"search_chats"}
    assert requested_capabilities(prompt) == {"memory"}


@pytest.mark.parametrize("prompt", [
    "Show my calendar from September 9 to September 18.",
    "Show my calendar for Tuesday.",
    "List the calendar for the rest of this month.",
    "What's on my calendar next week?",
])
def test_calendar_queries_with_time_scope_request_data_not_panel_navigation(prompt):
    assert requested_capabilities(prompt) == {"calendar"}


def test_scheduled_task_create_is_not_stolen_by_calendar_router():
    assert requested_capabilities(
        "Create a one-off scheduled task named cleanup for 2030-01-01 at 00:00 UTC."
    ) == {"tasks"}


def test_contextual_document_edit_keeps_document_family():
    assert requested_capabilities(
        "In that document, replace alpha-state with beta-state."
    ) == {"documents"}


def test_skill_description_change_is_not_stolen_by_shell_router():
    assert requested_capabilities(
        "Change that skill description from alpha-state helper to beta-state helper."
    ) == {"skills"}


def test_workspace_report_with_dated_external_verification_keeps_web_tools():
    message = (
        "Scan /workspace/fixtures/paper.pdf, verify which cited preprints were "
        "officially published as of March 19, 2026, and save "
        "/workspace/updated_publications.csv."
    )

    assert requested_capabilities(message, workspace=True) == {
        "shell_files", "search_browser",
    }


def test_workspace_artifact_from_explicit_url_keeps_web_tools():
    message = (
        "Read the Biography section at:\n\n"
        "- https://example.com/history\n\n"
        "Extract the named people and save one Markdown file per person "
        "under /workspace/results/."
    )

    assert requested_capabilities(message, workspace=True) == {
        "shell_files", "search_browser",
    }


def test_named_external_paper_table_artifact_keeps_web_tools_without_a_url():
    message = (
        'From the paper "Example Vision Suite", merge data from Table 2 and '
        'Table 4, then save /workspace/merged.csv and /workspace/chart.png.'
    )

    assert requested_capabilities(message, workspace=True) == {
        "shell_files", "search_browser",
    }


def test_local_pdf_table_artifact_does_not_add_external_web_capability():
    message = (
        'Based on the paper at /workspace/fixtures/paper.pdf, extract Table 2 '
        'and save /workspace/summary.csv.'
    )

    assert requested_capabilities(message, workspace=True) == {"shell_files"}


@pytest.mark.parametrize("followup", ["Delete it", "Create another", "Do that again"])
def test_followup_uses_antecedent_not_calendar_guess(followup):
    history = [{"role": "user", "content": "Create a note"},
               {"role": "assistant", "content": "I created it. Calendar is available too."}]
    assert requested_capabilities(followup, history) == {"notes"}
    assert requested_capabilities(followup) == {"unknown"}


def test_followup_chain_current_message_and_expiration():
    history = [{"role": "user", "content": "Create a note"},
               {"role": "user", "content": "Create another"},
               {"role": "user", "content": "Delete it"}]
    assert requested_capabilities("Delete it", history) == {"notes"}
    assert requested_capabilities("Thanks, that helps", history) == set()
    history.append({"role": "user", "content": "What is a prime number?"})
    assert requested_capabilities("Do that again", history) == {"unknown"}


def test_active_context_does_not_expand_explicit_request():
    assert requested_capabilities("Shorten it", active_document=True) == {"documents"}
    assert requested_capabilities("Write reply", active_document=True) == {"documents"}
    assert requested_capabilities("Draft a reply", active_document=True) == {"documents"}
    assert requested_capabilities("Reply", active_document=True) == {"documents"}
    assert requested_capabilities("Write reply to this", active_document=True) == {"documents"}
    assert requested_capabilities("Write reply this email", active_document=True) == {"documents"}
    assert requested_capabilities("Write this text into the editor", active_document=True) == {"documents"}
    assert requested_capabilities("Write a note", active_document=True) == {"notes"}
    assert requested_capabilities("Write a JavaScript function", active_document=True) == {"shell_files"}
    assert requested_capabilities("Create another document", active_document=True) == {"documents"}
    assert requested_capabilities("Create a note", active_document=True, workspace=True) == {"notes"}
    assert requested_capabilities("Hello", active_document=True, workspace=True) == set()


@pytest.mark.parametrize("message,expected,tool", [
    ("What’s my email", {"email"}, "list_emails"),
    ("What's my notes", {"notes"}, "manage_notes"),
    ("notes and calendar", {"notes", "calendar"}, "manage_calendar"),
    ("List my notes and calendar", {"notes", "calendar"}, "manage_notes"),
    ("Research battery recycling", {"research"}, "trigger_research"),
    ("List my research", {"research"}, "manage_research"),
    ("List my contacts", {"contacts"}, "manage_contact"),
    ("Find my contact", {"contacts"}, "resolve_contact"),
    ("List my sessions", {"sessions"}, "list_sessions"),
    ("Create a session", {"sessions"}, "create_session"),
    ("Switch the theme", {"ui"}, "ui_control"),
    ("Open the calendar", {"ui"}, "ui_control"),
    ("Search my memories for project Aurora.", {"memory"}, "manage_memory"),
    ("Search my calendar for dentist appointments.", {"calendar"}, "manage_calendar"),
    ("Now open documents.", {"ui"}, "ui_control"),
    ("Return to documents.", {"ui"}, "ui_control"),
    ("Go back and open the gallery again.", {"ui"}, "ui_control"),
])
def test_supplemental_product_capabilities(message, expected, tool):
    capabilities = requested_capabilities(message)
    assert capabilities == expected
    contract = resolve(capabilities)
    assert tool in contract.offered
    assert not contract.unavailable


def test_negated_output_file_clause_does_not_grant_shell_tools_to_transcription():
    message = (
        "Transcribe the speech in /workspace/jo.wav. "
        "Read only and do not create an output file."
    )
    assert requested_capabilities(message, workspace=True) == {"transcription"}


def test_unknown_capability_requires_explicit_clarification():
    capabilities = requested_capabilities("Do something")
    assert capabilities == {"unknown"}
    for capabilities in (capabilities, {"unrecognized_family"}):
        contract = resolve(capabilities)
        assert contract.unavailable == {f"capability:{f}" for f in capabilities}
        assert not contract.offered
        assert contract.audit()["unavailable"]


@pytest.mark.parametrize("family", ["research", "contacts", "sessions", "ui"])
def test_missing_supplemental_inventory_is_explicit(family):
    contract = resolve({family}, schemas=[])
    assert contract.unavailable == {f"capability:{family}"}
    assert contract.schemas() == []


@pytest.mark.parametrize("policy", [ToolPolicy(), ToolPolicy(block_all_tool_calls=True)])
def test_empty_selection_means_no_tools(policy):
    contract = resolve(policy=policy, selected_tools=())
    assert contract.offered == contract.required == contract.unavailable == frozenset()
    assert contract.schemas() == []
    assert not contract.permits("manage_calendar")


@pytest.mark.parametrize('policy', [
    ToolPolicy(disabled_tools=frozenset({'bash', 'python', 'read_file'})),
    ToolPolicy(block_all_tool_calls=True),
])
def test_unclassified_recovery_tools_respect_explicit_denials(policy):
    contract = resolve(policy=policy)
    assert not {'bash', 'python', 'read_file'} & contract.offered
    assert not any(policy.blocks(name) for name in contract.offered)
    if policy.block_all_tool_calls:
        assert not contract.offered


@pytest.mark.parametrize("policy", [
    ToolPolicy(disabled_tools=frozenset({"manage_calendar"})),
    ToolPolicy(hidden_tools=frozenset({"manage_calendar"})),
    ToolPolicy(block_all_tool_calls=True),
])
def test_explicit_denials_win_over_required_capability(policy):
    contract = resolve({"calendar"}, policy=policy)
    assert not contract.permits("manage_calendar")
    assert "manage_calendar" not in contract.executable
    assert contract.required == set()
    assert contract.unavailable == {"manage_calendar"}


def test_web_toggle_cannot_expand_calendar_and_denial_limits_combined_request():
    enabled = resolve({"calendar"})
    disabled = resolve({"calendar"}, policy=ToolPolicy(disabled_tools=WEB_ACCESS_TOOL_NAMES))
    assert enabled.schemas() == disabled.schemas()
    assert not enabled.offered & WEB_ACCESS_TOOL_NAMES
    combined = resolve({"calendar", "search_browser"},
                       policy=ToolPolicy(disabled_tools=WEB_ACCESS_TOOL_NAMES))
    assert "manage_calendar" in combined.offered
    assert not combined.offered & WEB_ACCESS_TOOL_NAMES


@pytest.mark.parametrize("missing", [True, False])
def test_required_model_catalog_unavailability_is_auditable(missing):
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if not missing or s["function"]["name"] != "list_models"]
    policy = ToolPolicy() if missing else ToolPolicy(disabled_tools=frozenset({"list_models"}))
    contract = resolve(requested_capabilities("List available models"), schemas=schemas,
                       policy=policy, required_tools={"list_models"})
    assert contract.audit()["unavailable"] == ["list_models"]
    assert contract.required == contract.offered == set()
    assert contract.schemas() == []
    assert resolve({"cookbook_admin"}, required_tools={"list_models"}).required == {"list_models"}


def test_other_cookbook_actions_do_not_require_catalog():
    contract = resolve({"cookbook_admin"}, policy=ToolPolicy(disabled_tools=frozenset({"list_models"})))
    assert contract.required == contract.unavailable == set()
    assert "download_model" in contract.offered


def test_missing_requirement_cannot_substitute_another_selected_capability():
    contract = resolve({"calendar", "search_browser"},
                       policy=ToolPolicy(disabled_tools=frozenset({"manage_calendar"})))
    assert contract.unavailable == {"manage_calendar"}
    assert contract.offered == contract.required == set()
    assert contract.schemas() == []
    assert not contract.permits("web_search")


def test_explicit_requirement_cannot_expand_selection():
    contract = resolve({"calendar"}, required_tools={"web_search"})
    assert contract.unavailable == {"web_search"}
    assert contract.offered == set()


@pytest.fixture
def email_inventory():
    # MCP uses the same function-schema shape; retain the real parameters.
    schema = deepcopy(next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "send_email"))
    schema["function"]["name"] = "mcp__email__send_email"
    return [*deepcopy(FUNCTION_TOOL_SCHEMAS), schema]


def test_email_prefers_mcp_schema_and_accepts_equivalent_dispatch_name(email_inventory):
    contract = resolve({"email"}, schemas=email_inventory, required_tools={"send_email"})
    assert contract.required == {"mcp__email__send_email"}
    assert contract.unavailable == set()
    assert "mcp__email__send_email" in contract.offered
    assert "send_email" not in contract.offered
    assert contract.permits("send_email")
    assert contract.permits("mcp__email__send_email")
    assert not contract.permits("mcp__other__send_email")
    assert canonical_tool("mcp__other__send_email") == "mcp__other__send_email"


@pytest.mark.parametrize("denied", ["send_email", "mcp__email__send_email"])
@pytest.mark.parametrize("field", ["disabled_tools", "hidden_tools"])
def test_email_denials_apply_in_both_directions(email_inventory, denied, field):
    policy = ToolPolicy(**{field: frozenset({denied})})
    for schemas in (email_inventory, FUNCTION_TOOL_SCHEMAS):
        contract = resolve({"email"}, schemas=schemas, policy=policy)
        assert not contract.permits("send_email")
        assert not contract.permits("mcp__email__send_email")


def test_mcp_permission_denial(email_inventory):
    contract = resolve({"email"}, schemas=email_inventory, policy=ToolPolicy(disable_mcp=True))
    assert not any(n.startswith("mcp__") for n in contract.executable)
    assert "send_email" in contract.offered


def test_candidate_schema_changes_cannot_mutate_contract():
    inventory = deepcopy(FUNCTION_TOOL_SCHEMAS)
    contract = resolve({"calendar"}, schemas=inventory)
    expected = contract.schemas()
    for _ in range(3):
        candidate = contract.schemas()
        candidate[0]["function"]["parameters"].clear()
        candidate.pop()
        assert contract.schemas() == expected
    for schema in inventory:
        schema["function"].clear()
    assert contract.schemas() == expected
    with pytest.raises(FrozenInstanceError):
        contract.offered = frozenset()


@pytest.mark.asyncio
async def test_dispatcher_enforces_bound_contract_without_external_mutations(monkeypatch):
    from src import tool_execution, tool_implementations
    from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
    from tests.runtime_evidence_helpers import server_authorized_executor
    execute_tool_block = server_authorized_executor(execute_tool_block)

    handler = AsyncMock(return_value={"events": [], "exit_code": 0})
    monkeypatch.setattr(tool_implementations, "do_manage_calendar", handler)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    block = SimpleNamespace(tool_type="manage_calendar", content='{"action":"list"}')
    calendar = resolve({"calendar"})
    empty = resolve()

    async def dispatch(contract):
        with bind_turn_contract(contract):
            await asyncio.sleep(0)  # Interleave tasks while each binding is active.
            assert active_turn_contract() is contract
            return await execute_tool_block(block, security_context=NO_TOOL_SECURITY_CONTEXT)

    previous = active_turn_contract()
    allowed, denied = await asyncio.gather(dispatch(calendar), dispatch(empty))
    assert allowed[1]["exit_code"] == 0
    assert denied[1]["failure_kind"] == "turn_contract_denied"
    handler.assert_awaited_once()
    assert active_turn_contract() is previous
    with bind_turn_contract(calendar):
        with pytest.raises(RuntimeError), bind_turn_contract(empty):
            raise RuntimeError("test restoration")
        assert active_turn_contract() is calendar
    assert active_turn_contract() is previous


@pytest.mark.parametrize("message,family,tool", [
    ("Generate an image of a cat", "image_generation", "generate_image"),
    ("Create an image of a calendar", "image_generation", "generate_image"),
    ("Can you make me a picture of a cat?", "image_generation", "generate_image"),
    ("Upscale this image", "image_editing", "edit_image"),
    ("Please remove the background from this photo", "image_editing", "edit_image"),
    ("Transcribe this audio file", "transcription", "transcribe_media"),
    ("Could you transcribe this video?", "transcription", "transcribe_media"),
    ("Transcribe /workspace/interview.wav", "transcription", "transcribe_media"),
    ("Inspect this video", "media_inspection", "inspect_media"),
    ("Inspect /workspace/diagram.png", "media_inspection", "inspect_media"),
    ("Inspect this PDF", "media_inspection", "inspect_media"),
])
def test_declared_media_actions_offer_only_the_supported_tool(message, family, tool):
    capabilities = requested_capabilities(message)
    assert capabilities == {family}
    contract = resolve(capabilities)
    assert contract.required == {tool}
    assert contract.offered == {tool, "ask_user", "update_plan"}
    assert contract.unavailable == set()
    actual = next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == tool)
    assert actual in contract.schemas()


@pytest.mark.parametrize("family,tool", [
    ("image_generation", "generate_image"),
    ("image_editing", "edit_image"),
    ("transcription", "transcribe_media"),
    ("media_inspection", "inspect_media"),
])
@pytest.mark.parametrize("unavailable", ["missing", "disabled", "hidden", "all"])
def test_required_media_tool_unavailable_cannot_substitute_shell(family, tool, unavailable):
    schemas = FUNCTION_TOOL_SCHEMAS
    policy = ToolPolicy()
    if unavailable == "missing":
        schemas = [s for s in schemas if s["function"]["name"] != tool]
    elif unavailable == "disabled":
        policy = ToolPolicy(disabled_tools=frozenset({tool}))
    elif unavailable == "hidden":
        policy = ToolPolicy(hidden_tools=frozenset({tool}))
    else:
        policy = ToolPolicy(block_all_tool_calls=True)
    contract = resolve({family, "shell_files"}, schemas=schemas, policy=policy)
    assert tool in contract.unavailable
    assert contract.offered == contract.required == set()
    assert contract.schemas() == []
    assert not contract.permits("bash")


@pytest.mark.parametrize("message,expected", [
    ("How do I generate an image?", set()),
    ("Can you explain how to transcribe audio?", set()),
    ("What is image generation?", set()),
    ("The image has a blue background", set()),
    ("Edit this image", {"unknown"}),
    ("Generate something", {"unknown"}),
    ("Transcribe something", {"unknown"}),
    ("Search the web for image generation", {"search_browser"}),
    ("Open the gallery", {"ui"}),
])
def test_media_mapping_does_not_guess_unsupported_actions(message, expected):
    assert requested_capabilities(message) == expected


def test_media_combined_requests_and_referential_repeat():
    assert requested_capabilities("Create a note and generate an image of a cat") == {
        "notes", "image_generation",
    }
    assert requested_capabilities("Inspect this video and transcribe its audio") == {
        "media_inspection", "transcription",
    }
    history = [{"role": "user", "content": "Upscale this image"}]
    assert requested_capabilities("Do that again", history) == {"image_editing"}


@pytest.mark.parametrize("message", [
    "List my email accounts", "What's my email address?", "What’s my email address?",
    "What's my email?", "whats my email?",
    "Please show my email accounts.", "Can you list my email accounts, please?",
    "What are my email addresses?",
])
def test_account_discovery_requires_and_offers_only_metadata_operation(message):
    selected = selected_tools_for_request(message)
    assert selected == {"list_email_accounts"}
    capabilities = requested_capabilities(message)
    assert capabilities == {"email"}
    contract = resolve(capabilities, selected_tools=selected, required_tools=selected)
    assert contract.offered == {"list_email_accounts", "ask_user", "update_plan"}
    assert contract.required == {"list_email_accounts"}
    assert not contract.unavailable


@pytest.mark.parametrize("message", [
    "List my email accounts and send an email", "What's my email address? Send an email.",
    "List my email accounts; check my calendar", "Send my email address to Bob",
    "List my emails", "Read my email", "How do I list my email accounts?",
    "List my email accounts and calendar", "List my email accounts\nDelete my email",
])
def test_account_discovery_does_not_narrow_other_or_mixed_instructions(message):
    assert selected_tools_for_request(message) is None


def test_single_html_url_read_selects_only_web_fetch():
    message = (
        "Fetch and summarize "
        "https://investors.example.com/news/company-agrees-to-acquire-example"
    )
    selected = selected_tools_for_request(message)
    assert selected == {"web_fetch"}
    contract = resolve(
        requested_capabilities(message),
        selected_tools=selected,
        required_tools=selected,
    )
    assert contract.offered == {"web_fetch", "ask_user", "update_plan"}


@pytest.mark.parametrize("message", [
    "Summarize https://youtu.be/example",
    "Extract https://example.com/paper.pdf",
    "Read https://example.com/data and save /workspace/result.md",
])
def test_interactive_specialized_or_compound_urls_keep_family_scope(message):
    assert selected_tools_for_request(message) is None


def test_explicit_url_navigation_and_click_selects_private_browser():
    assert selected_tools_for_request(
        "Open https://example.com and click the details link"
    ) == {"private_browser"}


@pytest.mark.parametrize("message,tool", [
    ("Use get_workspace to inspect the current workspace.", "get_workspace"),
    ("Now use ls to list that same workspace directory.", "ls"),
    ("Use read_file to read /workspace/sample.txt.", "read_file"),
    ("Use write_file to create /workspace/output.txt.", "write_file"),
    ("Use python to calculate 2 + 2.", "python"),
])
def test_explicit_native_tool_request_seals_that_operation(message, tool):
    selected = selected_tools_for_request(message)
    assert selected == {tool}
    capabilities = requested_capabilities(message, workspace=True)
    assert capabilities == {"shell_files"}
    contract = resolve(capabilities, selected_tools=selected, required_tools=selected)
    assert contract.required == {tool}
    assert tool in contract.offered
    assert not ({"get_workspace", "ls", "read_file", "write_file", "python"} - {tool}) & contract.offered


def test_compound_explicit_native_tools_retain_family_scope():
    assert selected_tools_for_request(
        "Use write_file to create it, then use read_file to verify it."
    ) is None


def test_compound_account_discovery_retains_explicit_send():
    message = "List my email accounts and send an email"
    contract = resolve(requested_capabilities(message), selected_tools=selected_tools_for_request(message))
    assert contract.permits("send_email")
    assert contract.permits("list_email_accounts")


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_account_discovery_alias_and_permission_semantics(qualified, denied):
    schema = deepcopy(next(s for s in FUNCTION_TOOL_SCHEMAS
                           if s["function"]["name"] == "list_email_accounts"))
    if qualified:
        schema["function"]["name"] = "mcp__email__list_email_accounts"
    schemas = [*FUNCTION_TOOL_SCHEMAS, schema]
    policy = ToolPolicy(disabled_tools=frozenset({"mcp__email__list_email_accounts"}) if denied else frozenset())
    contract = resolve({"email"}, schemas=schemas, policy=policy,
                       selected_tools={"list_email_accounts"}, required_tools={"list_email_accounts"})
    if denied:
        assert "list_email_accounts" in contract.unavailable
        assert contract.offered == set()
    else:
        assert contract.required == {schema["function"]["name"]}
        assert contract.offered == {schema["function"]["name"], "ask_user", "update_plan"}


def test_missing_account_discovery_schema_cannot_offer_other_email_tools():
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] != "list_email_accounts"]
    contract = resolve({"email"}, schemas=schemas, selected_tools={"list_email_accounts"},
                       required_tools={"list_email_accounts"})
    assert "list_email_accounts" in contract.unavailable
    assert contract.schemas() == []


def test_optional_selection_is_a_narrowing_boundary_and_empty_means_none():
    assert resolve({"email"}, selected_tools=[]).offered == set()
    assert resolve({"calendar"}, selected_tools={"manage_calendar", "send_email"}).offered == {
        "manage_calendar", "ask_user", "update_plan",
    }


def test_conversational_email_followups_inherit_email_family():
    history = [
        {"role": "user", "content": "Show my inbox"},
        {"role": "assistant", "content": "Here are your messages."},
    ]
    for prompt in (
        "Yes, open it",
        "reply saying thanks",
        "What does the attachment say?",
        "Can you unarchive this?",
    ):
        assert requested_capabilities(prompt, history) == {"email"}


@pytest.mark.parametrize("prompt", [
    "got anything more detaild about the flooding?",
    "whos it from?",
])
def test_email_evidence_detail_followups_retain_email_family(prompt):
    history = [{"role": "assistant", "content": "Inbox match.", "metadata": {
        "tool_events": [{"tool": "list_emails", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"email"}


def test_connected_mail_accounts_select_account_inventory():
    message = "Which mail accounts are connected? Just listing, don't touch anything."
    assert requested_capabilities(message) == {"email"}
    assert selected_tools_for_request(message) == {"list_email_accounts"}


@pytest.mark.parametrize("message", [
    "wich emial accounts do i have hooked up?",
    "what mail accounts are configured here?",
])
def test_typoed_connected_mail_accounts_select_inventory(message):
    assert selected_tools_for_request(message) == {"list_email_accounts"}
    assert requested_capabilities(message) == {"email"}


def test_source_link_followup_selects_fresh_web_search():
    message = "where does that come from, link me the source u used"
    assert requests_supporting_web_source(message)
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_explicit_hugging_face_search_owns_cookbook_wording():
    message = (
        "use the cookbook hugging face search to find official compact gemma instruct models "
        "— pls dont use my configured endpoint model list and dont download anything"
    )
    assert selected_tools_for_request(message) == {"search_hf_models"}
    assert requested_capabilities(message) == {"search_browser"}


def test_local_cache_comparison_switches_from_hf_search_to_cookbook_inventory():
    message = "compare that with whats cached locally"
    assert selected_tools_for_request(message) == {"list_cached_models"}
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_explicit_repo_download_selects_tracked_cookbook_download():
    message = (
        "cool, grab Qwen/Qwen3-8B locally but only the *.safetensors files. "
        "gimme the tracked download session id"
    )
    assert selected_tools_for_request(message) == {"download_model"}
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_arxiv_source_download_is_not_a_model_download():
    message = (
        "Download the source package from https://arxiv.org/abs/2501.07888, "
        "extract every table, and save each one to /tmp_workspace/results/1.tex."
    )
    assert selected_tools_for_request(message) != {"download_model"}
    assert requested_capabilities(message) == {"search_browser", "shell_files"}


def test_single_web_page_with_workspace_outputs_is_not_sealed_to_fetch_only():
    message = (
        "Visit https://example.org/catalog and save every item under "
        "/tmp_workspace/results/items/ plus a summary.jsonl file."
    )
    assert selected_tools_for_request(message) is None
    assert requested_capabilities(message) == {"search_browser", "shell_files"}


def test_even_have_email_accounts_selects_account_inventory():
    message = "do i even have any email accounts connected here?"
    assert selected_tools_for_request(message) == {"list_email_accounts"}
    assert requested_capabilities(message) == {"email"}


@pytest.mark.parametrize("message", [
    "i need a quick rundown of my calender",
    "what does my week look like?",
])
def test_calendar_overview_phrasing_seals_calendar_read(message):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation("manage_calendar", {"action": "list_events"})
    assert requested_capabilities(message) == {"calendar"}


def test_quick_cal_abbreviation_seals_capped_calendar_read():
    message = "can i get a quick peek at my cal? max 3 titles and no changes."
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_calendar", {"action": "list_events"}, 3,
    )
    assert requested_capabilities(message) == {"calendar"}


def test_calendar_repeat_with_new_tomorrow_window_requires_fresh_read():
    history = [{"role": "assistant", "content": "Events", "metadata": {
        "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
    }}]
    message = "same again but maybe only tomorrow's?"
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_calendar", {"action": "list_events"},
    )
    assert requested_capabilities(message, history) == {"calendar"}


def test_calendar_next_events_repeat_preserves_prior_cap_and_requires_fresh_read():
    history = [
        {"role": "user", "content": "can i get a quick peek at my cal? max 3 titles and no changes."},
        {"role": "assistant", "content": "Three events", "metadata": {
            "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
        }},
    ]
    message = "now do that again but from my next events."
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_calendar", {"action": "list_events"}, 3,
    )
    assert requested_capabilities(message, history) == {"calendar"}


def test_skills_library_look_through_seals_search():
    message = "Look through my skills for email workflow guidance."
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_skills", {"action": "search", "query": "email workflow guidance"},
    )
    assert requested_capabilities(message) == {"skills"}


def test_headlines_from_place_route_to_public_search():
    assert requested_capabilities("gimme the headlines from japan, short") == {"search_browser"}


@pytest.mark.parametrize("message", [
    "open e-mail",
    "swap over to my notes panel",
])
def test_hyphenated_email_and_swap_panel_navigation_route_ui(message):
    assert requested_capabilities(message) == {"ui"}


def test_calendar_view_followup_retains_ui_family():
    history = [{"role": "assistant", "content": "Calendar panel is open.", "metadata": {
        "tool_events": [{"tool": "ui_control", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "cool, flip it over to the week view", history,
    ) == {"ui"}
    assert selected_tools_for_request("cool, flip it over to the week view") == {
        "ui_control",
    }


def test_calendar_agenda_followup_does_not_require_repeating_view_word():
    history = [{"role": "assistant", "content": "Calendar week view is open.", "metadata": {
        "tool_events": [{"tool": "ui_control", "exit_code": 0}],
    }}]
    message = "cool put it back to agenda"
    assert selected_tools_for_request(message) == {"ui_control"}
    assert requested_capabilities(message, history) == {"ui"}


def test_typoed_whats_in_skill_library_is_a_bounded_skill_list():
    message = "wats in my skill library? three names max, read only pls"
    history = [{"role": "assistant", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert selected_tools_for_request(message) == {"manage_skills"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_skills", {"action": "list"}, 3,
    )
    assert requested_capabilities(message, history) == {"skills"}


@pytest.mark.parametrize(('message', 'tool', 'action', 'family'), [
    ("give me my calender for this week", "manage_calendar", "list_events", "calendar"),
    ("give me my upcoming events pls", "manage_calendar", "list_events", "calendar"),
    ("wat scheduled taks do i have set up rn?", "manage_tasks", "list", "tasks"),
])
def test_natural_typoed_private_inventory_requests_are_sealed(message, tool, action, family):
    assert selected_tools_for_request(message) == {tool}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        tool, {"action": action},
    )
    assert requested_capabilities(message) == {family}


def test_quick_official_link_lookup_with_trailing_safety_clause_routes_web():
    message = (
        "quick lookup on GPT-4 - one official link, that's all. "
        "don't change anything or message anyone"
    )
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_skill_email_workflow_query_routes_library_not_inbox():
    message = "do i have any skills that cover handling my email workflow?"
    assert selected_tools_for_request(message) == {"manage_skills"}
    assert requested_capabilities(message) == {"skills"}
    operation = required_read_operation_for_request(message)
    assert operation.tool == "manage_skills"
    assert operation.args == {"action": "search", "query": "handling my email workflow"}


def test_source_page_followup_retains_recent_web_family():
    history = [{"role": "assistant", "content": "Flooding source found.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "ok pull the source page for the main story so i can skim it", history,
    ) == {"search_browser"}


def test_exact_selected_operation_beats_warm_search_family():
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "search_hf_models", "exit_code": 0,
    }]}}]
    assert requested_capabilities(
        "compare that with whats cached locally", history,
    ) == {"cookbook_admin"}
    assert requested_capabilities(
        "cool, grab Qwen/Qwen3-8B locally but only the *.safetensors files. "
        "gimme the tracked download session id", history,
    ) == {"cookbook_admin"}


@pytest.mark.parametrize("message", [
    "quick lookup — find an official page for GPT-4 and give me just one link.",
    "i need a official reference link for GPT-4. just one, from the source itself.",
])
def test_natural_official_reference_requests_select_web_search(message):
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_positive_wrapper_does_not_hide_known_page_fetch():
    message = "great, open that page and tell me the heading at the top."
    assert selected_tools_for_request(message) == {"web_fetch"}
    assert requested_capabilities(message) == {"search_browser"}


def test_older_chat_search_beats_incidental_calendar_and_tool_words():
    message = "find older chats where i talked about calendar tools"
    assert selected_tools_for_request(message) == {"search_chats"}
    assert requested_capabilities(message) == {"memory"}


def test_typoed_stick_result_into_note_routes_note_destination():
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "manage_calendar", "exit_code": 0,
    }]}}]
    assert requested_capabilities("sticck that in a note for me", history) == {"notes"}


def test_natural_skill_library_question_seals_skill_search():
    message = "any skill in my library about handling email?"
    assert selected_tools_for_request(message) == {"manage_skills"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_skills", {"action": "search", "query": "handling email"},
    )
    assert requested_capabilities(message) == {"skills"}


@pytest.mark.parametrize("message,name", [
    ("view the cookbook skill", "cookbook"),
    ("view my email skill", "email"),
    ("open the release-check skill", "release-check"),
])
def test_named_skill_view_seals_skill_reader(message, name):
    operation = RequiredReadOperation("manage_skills", {"action": "view", "name": name})
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"skills"}


def test_explicit_note_search_owns_incidental_email_subject():
    message = "search my notes for email templates then"
    assert required_read_operation_for_request(message) is None
    assert selected_tools_for_request(message) == {"manage_notes"}
    assert requested_capabilities(message) == {"notes"}


def test_model_delegation_inventory_seals_model_catalog():
    message = "which models can i hand work off to?"
    operation = RequiredReadOperation("list_models")
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_model_catalog_narrowing_followup_reuses_catalog_reader():
    history = [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "list_models",
            "command": "{}",
            "output": "model-a\nmodel-b",
            "exit_code": 0,
        }]},
    }]
    message = "narrow it down to the small fast ones"
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "list_models",
    )
    assert requested_capabilities(message, history) == {"cookbook_admin"}


def test_trailing_then_does_not_hide_serve_preset_inventory():
    message = "cool, list my serve presets then"
    operation = RequiredReadOperation("list_serve_presets")
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"cookbook_admin"}


@pytest.mark.parametrize("message", [
    "Find the IANA example domains page and return one link, please.",
    "cool - do a quick lookup on searxng release notes to check it actually works",
    "ok thanks, quick search to confirm its working: searxng status page",
])
def test_explicit_quick_public_lookup_selects_web_search(message):
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_explicit_native_web_workflow_outranks_incidental_task_words():
    message = (
        "Use 1-3 web_search calls, then web_fetch the strongest Git sources. "
        "Compare pull.ff, merge.ff, and git pull --rebase."
    )
    assert requested_capabilities(message) == {"search_browser"}
    assert selected_tools_for_request(message) == {"web_search", "web_fetch"}


def test_explicit_native_web_workflow_preserves_workspace_artifact_tools():
    message = (
        "Use Odysseus web_search to locate each RFC, then web_fetch every page. "
        "Create /tmp_workspace/results/inventory.jsonl, read the local inputs, "
        "and run the Python validator before finishing."
    )
    selected = selected_tools_for_request(message)
    assert selected == {
        "web_search", "web_fetch", "read_file", "write_file", "edit_file", "python",
    }
    assert requested_capabilities(message) == {"search_browser", "shell_files"}


def test_negative_memory_evidence_rule_does_not_select_personal_memory():
    message = (
        "Use Odysseus web_search and web_fetch for every paper, then write "
        "/tmp_workspace/results/paper_digest.md. Every value must come from "
        "the fetched page, never memory. Sort every paper list and show the result."
    )
    assert required_read_operation_for_request(message) is None


@pytest.mark.parametrize(
    "evidence_rule",
    [
        "extract every field without trusting memory",
        "do not answer from memory alone",
        "do not write the inventory from memory",
    ],
)
def test_web_evidence_variants_do_not_select_personal_memory(evidence_rule):
    message = (
        "Use web_search and web_fetch, then show an author list and write "
        f"/tmp_workspace/results/report.md; {evidence_rule}."
    )
    assert required_read_operation_for_request(message) is None


def test_explicit_web_required_outputs_preserve_workspace_tools():
    message = (
        "Run web_search and web_fetch first. Required outputs in "
        "/tmp_workspace/results: report.jsonl and provenance.md."
    )
    assert selected_tools_for_request(message) == frozenset(
        {"web_search", "web_fetch", "read_file", "write_file", "edit_file", "python"}
    )
    assert requested_capabilities(message) == {"search_browser", "shell_files"}


def test_typoed_webhook_inventory_selects_admin_reader():
    message = "list my webhoks again"
    assert selected_tools_for_request(message) == {"manage_webhooks"}
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_typoed_private_browser_request_selects_browser_automation():
    message = (
        "open https://example.com with the private browesr and tell me the "
        "rendered page title. no web search, no fetch"
    )
    assert selected_tools_for_request(message) == {"private_browser"}
    assert requested_capabilities(message) == {"search_browser"}


@pytest.mark.parametrize("message", [
    "how many results does my search return at a time?",
    "alright check the search prefs again and paste the whole block",
    "is there a region or language pref on my search?",
])
def test_natural_search_preference_questions_select_settings(message):
    assert selected_tools_for_request(message) == {"manage_settings"}
    assert requested_capabilities(message) == {"cookbook_admin"}


@pytest.mark.parametrize("message", [
    "then do one real search for the latest home assistant release so i know it works",
    "fine, do a quick lookup for weather in oslo as a sanity check",
])
def test_search_sanity_checks_select_web_search(message):
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_explicit_named_contact_lookup_seals_contact_search():
    message = "who is priya shah in my contacts again?"
    operation = RequiredReadOperation(
        "manage_contact", {"action": "search", "query": "priya shah"},
    )
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"contacts"}


def test_can_i_see_named_skill_seals_skill_view():
    message = "can i see the email skill pls"
    operation = RequiredReadOperation(
        "manage_skills", {"action": "view", "name": "email"},
    )
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"skills"}


@pytest.mark.parametrize("message", [
    "whats coming up this month?",
    "what events do i got coming up soon?",
    "hey whats on my plate this week? anything i should know about",
])
def test_natural_upcoming_schedule_questions_select_calendar(message):
    operation = RequiredReadOperation("manage_calendar", {"action": "list_events"})
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"calendar"}


def test_open_calendar_at_named_month_is_ui_navigation():
    message = "now open calendar septmber 2026"
    assert selected_tools_for_request(message) == {"ui_control"}
    assert requested_capabilities(message) == {"ui"}


def test_named_skill_section_seals_skill_view():
    message = "show the verification section of the email skill"
    operation = RequiredReadOperation(
        "manage_skills", {"action": "view", "name": "email"},
    )
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"skills"}


def test_natural_notes_inventory_question_preserves_limit():
    message = "what notes are there? three titles max, just reading"
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_notes", {"action": "list"}, 3,
    )
    assert requested_capabilities(message) == {"notes"}


def test_newsy_clarification_selects_web_search():
    message = "the newsy kind"
    assert selected_tools_for_request(message) == {"web_search"}
    assert requested_capabilities(message) == {"search_browser"}


def test_cookbook_download_activity_selects_download_reader():
    message = "whats downloading in cookbook atm"
    assert selected_tools_for_request(message) == {"list_downloads"}
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_weekly_inbox_summary_selects_email_reader():
    message = "summarize my inbox this week"
    assert selected_tools_for_request(message) == {"list_emails"}
    assert requested_capabilities(message) == {"email"}


def test_what_do_i_have_on_today_selects_calendar():
    message = "what do i have on today?"
    operation = RequiredReadOperation("manage_calendar", {"action": "list_events"})
    assert required_read_operation_for_request(message) == operation
    assert requested_capabilities(message) == {"calendar"}


@pytest.mark.parametrize("message", [
    "find the metadata for youtube video https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "one more time — metadata for https://www.youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_youtube_url_structurally_selects_youtube_tool(message):
    assert selected_tools_for_request(message) == {"youtube_tool"}
    assert requested_capabilities(message) == {"search_browser"}


@pytest.mark.parametrize("message", [
    "cheers — now pull up the memories panel, i want to check a saved marker",
    "hey can you pop my calender panel open for me",
])
def test_natural_panel_navigation_selects_ui_control(message):
    assert selected_tools_for_request(message) == {"ui_control"}
    assert requested_capabilities(message) == {"ui"}


def test_open_settings_area_is_ui_navigation():
    message = "can you open the settings area"
    assert selected_tools_for_request(message) == {"ui_control"}
    assert requested_capabilities(message) == {"ui"}


def test_loaded_skill_followup_stays_with_skill_content():
    history = [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "manage_skills",
            "command": '{"action":"view","name":"email"}',
            "output": "Email skill contents",
            "exit_code": 0,
        }]},
    }]
    assert requested_capabilities(
        "does it reference any email template?", history,
    ) == {"skills"}


def test_combined_readonly_short_suffix_preserves_note_limit():
    message = "give me my notes, three titles max, read-only and short"
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_notes", {"action": "list"}, 3,
    )


def test_keep_it_to_a_few_suffix_caps_task_inventory():
    message = "what automations do i have set up? keep it to a few"
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_tasks", {"action": "list"}, 3,
    )


@pytest.mark.parametrize("message,family,tool,args", [
    (
        "show me the second one again",
        "notes",
        "manage_notes",
        {"action": "view", "id": "note-raw-2"},
    ),
    (
        "Open the first one.",
        "documents",
        "manage_documents",
        {"action": "read", "document_id": "doc-raw-1"},
    ),
])
def test_ordinal_collection_followup_binds_latest_visible_anchor(message, family, tool, args):
    prefix = "note" if family == "notes" else "document"
    ids = ["note-raw-1", "note-raw-2"] if family == "notes" else ["doc-raw-1", "doc-raw-2"]
    history = [{"role": "assistant", "content": (
        f"- [First](#{prefix}-{ids[0]})\n- [Second](#{prefix}-{ids[1]})"
    )}]
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        tool, args,
    )


def test_never_mind_panel_switch_selects_new_ui_target():
    message = "never mind, open my notes instead"
    assert requested_capabilities(message) == {"ui"}


def test_explicit_old_chat_search_clause_selects_transcript_search():
    message = "have we talked abt this before? search my old chats for tool grounding so we can compare"
    assert selected_tools_for_request(message) == {"search_chats"}
    assert requested_capabilities(message) == {"memory"}
    history = [{"role": "assistant", "content": "Reviewed.", "metadata": {
        "tool_events": [{"tool": "chat_with_model", "exit_code": 0}],
    }}]
    assert requested_capabilities(message, history) == {"memory"}


def test_tell_me_more_inherits_immediately_preceding_web_lookup():
    history = [
        {"role": "user", "content": "Latest news in Japan"},
        {"role": "assistant", "content": "Recent news includes flooding in Nagoya."},
    ]
    assert requested_capabilities("Tell me more about the flooding?", history) == {"search_browser"}


def test_what_else_did_it_say_inherits_exact_url_fetch():
    history = [
        {
            "role": "user",
            "content": (
                "Fetch and summarize "
                "https://example.com/newsroom/acquisition"
            ),
        },
        {
            "role": "assistant",
            "content": "Here is a summary of the announcement.",
            "metadata": {
                "tool_events": [
                    {"tool": "web_fetch", "exit_code": 0, "error": False}
                ]
            },
        },
    ]
    assert requested_capabilities("What else did it say about Miro?", history) == {
        "search_browser"
    }


@pytest.mark.parametrize("prompt", [
    "is there anything in there about the rail strike",
    "where did you get that from, give me the link",
    "double-check thats the official docs, not a blog",
])
def test_web_evidence_followups_retain_successful_web_family(prompt):
    history = [{"role": "assistant", "content": "Search results.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_general_official_link_lookup_selects_web_not_hugging_face_catalog():
    prompt = "search gpt-4 and return one official link"
    assert selected_tools_for_request(prompt) == {"web_search"}


def test_explicit_hugging_face_official_link_lookup_keeps_model_catalog_choice_open():
    prompt = "search Hugging Face for gpt-4 and return one official link"
    assert selected_tools_for_request(prompt) is None


def test_adjacent_latest_lookup_retains_successful_web_family():
    history = [{"role": "assistant", "content": "Official docs.", "metadata": {
        "tool_events": [{"tool": "web_fetch", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "now look for the latest packaging spec version and what changed", history,
    ) == {"search_browser"}


@pytest.mark.parametrize("prompt", [
    "k, now pull up that page and tell me what it says at the very top",
    "cool, drop that link again on its own line",
])
def test_web_page_and_link_continuations_outrank_incidental_words(prompt):
    history = [{"role": "assistant", "content": "Official result.", "metadata": {
        "tool_events": [{"tool": "web_fetch", "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == {"search_browser"}


def test_official_docs_page_followup_stays_with_prior_web_result():
    history = [{"role": "assistant", "content": "Official result.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "which one is the real official docs? open that page and tell me the main parts",
        history,
    ) == {"search_browser"}


def test_can_u_look_up_online_routes_to_web():
    assert requested_capabilities(
        "Can u look up the official Python packaging guide online?"
    ) == {"search_browser"}


def test_quick_web_search_routes_to_web():
    assert requested_capabilities(
        "quick web search for GPT-4, one official source pls"
    ) == {"search_browser"}


def test_tell_me_more_without_context_does_not_invent_a_family():
    assert requested_capabilities("Tell me more about the flooding?") == frozenset()


@pytest.mark.parametrize("tool,followup,expected", [
    ("manage_notes", "whats in the top one?", {"notes"}),
    ("manage_memory", "which of those did i add most recently?", {"memory"}),
    ("list_cookbook_servers", "what models are already cached on that workstation one", {"cookbook_admin"}),
])
def test_contextual_result_selector_inherits_successful_family(tool, followup, expected):
    history = [{"role": "assistant", "content": "Result", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(followup, history) == expected


def test_bare_ordinal_does_not_preselect_skills_over_prior_calendar():
    history = [{"role": "assistant", "content": "Events shown.", "metadata": {
        "tool_events": [{"tool": "manage_calendar", "exit_code": 0}],
    }}]
    assert selected_tools_for_request("open the first one.") is None
    assert requested_capabilities("open the first one.", history) == {"calendar"}


def test_pull_that_one_up_retains_successful_collection_family():
    history = [{"role": "assistant", "content": "No matching note.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "pull that one up so i can see the items on it", history,
    ) == {"notes"}


@pytest.mark.parametrize("tool,prompt,expected", [
    ("manage_notes", "Is there one with a packing list for the trip?", {"notes"}),
    ("manage_notes", "was there anything in them about the car service", {"notes"}),
    ("manage_research", "any of them about battery tech?", {"research"}),
])
def test_contextual_store_filters_retain_successful_family(tool, prompt, expected):
    history = [{"role": "assistant", "content": "Items shown.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0}],
    }}]
    assert requested_capabilities(prompt, history) == expected


@pytest.mark.parametrize("message,expected", [
    ("Gimme a quick list of my notes, max three titles.", {"notes"}),
    ("what notes have i got? 3 titles tops", {"notes"}),
    ("what docs do i have in here?", {"documents"}),
    ("what have u got saved about me in memory?", {"memory"}),
    ("memories please, 3 max, no changes", {"memory"}),
    ("what do u remember about me?", {"memory"}),
    ("gimme a quick peek at my stored memories", {"memory"}),
    ("Can you tell me what mailboxes I've connected?", {"email"}),
    ("show me my automations", {"tasks"}),
    ("just tell me how many notes i have in total", {"notes"}),
    ("whats on my notes list? three at most, dont touch anything", {"notes"}),
    ("whats in my notes rite now", {"notes"}),
    ("got any cookbook servers configured at all?", {"cookbook_admin"}),
    ("k whats my notes", {"notes"}),
    ("does any note mention germany?", {"notes"}),
    ("give me my calendar events, short", {"calendar"}),
    ("i need a read-only peek at cookbook servers. just the server names and if they're up", {"cookbook_admin"}),
    ("my documents, list them for me", {"documents"}),
    ("gimme a quick peek at my stored memories, 3 max, short lines, no writes", {"memory"}),
])
def test_natural_personal_inventory_phrasing_routes_to_store(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("prompt", [
    "show cookbook servres",
    "list cookbok sevres, max three",
    "can you show my cookbook srvers",
])
def test_cookbook_server_typos_route_to_server_inventory(prompt):
    assert requested_capabilities(prompt) == {"cookbook_admin"}
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.tool == "list_cookbook_servers"


def test_show_personal_documents_again_is_data_read_not_panel_navigation():
    assert requested_capabilities("show my documents again") == {"documents"}
    assert required_read_operation_for_request(
        "show my documents again"
    ) == RequiredReadOperation("manage_documents", {"action": "list"})


def test_explicit_skills_panel_owns_trailing_browse_rationale():
    assert requested_capabilities(
        "can you just open the Skills panel so i can browse them myself too"
    ) == {"ui"}


def test_append_line_to_recent_note_owns_incidental_weekday():
    history = [{"role": "assistant", "content": "Note created.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "and add a line at the end reminding me to prep the friday one", history,
    ) == {"notes"}


@pytest.mark.parametrize("tool,message,expected", [
    ("manage_notes", "pull that one up so i can see the items on it", {"notes"}),
    ("manage_notes", "does it have any checklist items?", {"notes"}),
    ("manage_notes", "Dont edit it, just tell me if the milk item is checked.", {"notes"}),
    ("list_email_accounts", "ok, how many unread are sitting in the first one?", {"email"}),
    ("list_email_accounts", "which one is my work one?", {"email"}),
    ("manage_calendar", "What time was the second one again?", {"calendar"}),
    ("manage_skills", "the first one sounds handy — what does it actually do?", {"skills"}),
    ("web_search", "who reported it", {"search_browser"}),
    ("web_search", "any reason to doubt that source?", {"search_browser"}),
    ("manage_calendar", "nice, set a reminder 30 mins before it.", {"calendar"}),
    ("manage_calendar", "back to my calendar - whats next after that?", {"calendar"}),
    ("web_search", "now look for the latest packaging spec version and what changed", {"search_browser"}),
    ("web_search", "k, now pull up that page and tell me what it says at the very top", {"search_browser"}),
    ("manage_skills", "publish it and call it official-source-lookup", {"skills"}),
    ("manage_skills", "make sure its published and named web-source-check", {"skills"}),
    ("search_emails", "got anything more detaild about the flooding?", {"email"}),
    ("search_emails", "whos it from?", {"email"}),
    ("list_cookbook_servers", "anything downloading on those atm?", {"cookbook_admin"}),
    ("manage_documents", "ok open the 2nd one and tell me what its about in a couple lines", {"documents"}),
    ("manage_documents", "is there a python one in there too?", {"documents"}),
    ("manage_skills", "k, whats the description on the first one?", {"skills"}),
    ("manage_skills", "now just search — anything in there about git?", {"skills"}),
    ("bash", "While you're at it, add whoami to the same read-only command.", {"shell_files"}),
    ("manage_research", "also search my reports for anything about browser privacy", {"research"}),
    ("web_search", "tnx. set me a calendar reminder tomorrow morning to actually read that link", {"calendar"}),
    ("bash", "Great. Now run that same command again and tell me if anything changed.", {"shell_files"}),
])
def test_referential_questions_retain_recent_successful_family(tool, message, expected):
    history = [{"role": "assistant", "content": "Results shown.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0}],
    }}]
    assert requested_capabilities(message, history) == expected


def test_unrelated_question_with_number_one_does_not_inherit_store_family():
    history = [{"role": "assistant", "content": "Notes shown.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
    }}]
    assert requested_capabilities("what is one plus one?", history) == set()


def test_official_source_link_lookup_routes_to_web_not_shell():
    assert requested_capabilities("find me one official source link for gpt-4") == {"search_browser"}


def test_dash_wrapped_skill_save_is_an_explicit_skill_action():
    assert requested_capabilities("nice — save how u did that as a skill so we can reuse it") == {"skills"}


def test_turn_prior_approach_into_reusable_skill_is_explicit_skill_action():
    assert requested_capabilities(
        "that was clean. can you turn that into a skill i can reuse later"
    ) == {"skills"}


def test_web_result_can_switch_to_reusable_skill_even_with_web_words_in_name():
    history = [{"role": "assistant", "content": "Official source shown.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(
        "nice - stash this approach as a reusable skill, something like 'official source lookup'",
        history,
    ) == {"skills"}


def test_show_documents_again_is_data_inventory_not_panel_navigation():
    assert requested_capabilities("show my documents again") == {"documents"}


def test_drop_prior_result_into_named_note_routes_to_notes():
    assert requested_capabilities(
        "k, drop that link into a note called 'links to read' so i dont lose it"
    ) == {"notes"}


def test_collect_sources_and_save_note_routes_both_capabilities():
    assert requested_capabilities(
        "cool, grab a couple of sources and stick the top pick in a new note"
    ) == {"search_browser", "notes"}


def test_dig_deeper_routes_to_deep_research():
    assert requested_capabilities("nice, can you dig deeper on that for me?") == {"research"}


@pytest.mark.parametrize("message", [
    "run that same read-only marker + hostname command again",
    "rerun that marker and hostname command",
])
def test_hostname_command_replays_route_to_shell(message):
    assert requested_capabilities(message) == {"shell_files"}


@pytest.mark.parametrize("message,expected", [
    ("thanks - swich it over to the notes panel instead, i wanna jot something down", {"ui"}),
    ("search my skills for email workflow guidnce", {"skills"}),
    ("im trying to find whatever skill covers my email routine", {"skills"}),
    ("make a short note called 'week ahead' that just lists whats on my calendar this week", {"notes", "calendar"}),
    ("small research run on the history of searxng pls", {"research"}),
])
def test_cross_family_target_and_natural_research_routing(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("panel_name", ["memory", "memories", "brain"])
@pytest.mark.parametrize("message,expected", [
    ("whats actually in there right now?", {"memory"}),
    ("while im here, note that i like meetings kept after 10am", {"memory"}),
])
def test_memory_panel_followups_retain_memory_family(panel_name, message, expected):
    history = [{"role": "assistant", "content": "Panel opened.", "metadata": {
        "tool_events": [{
            "tool": "ui_control", "exit_code": 0,
            "command": {"action": "open_panel", "name": panel_name},
        }],
    }}]
    assert requested_capabilities(message, history) == expected


@pytest.mark.parametrize("message", [
    "cool — when its done, how do i find it again?",
    "ok open the newest one for me",
    "wheres it gonna show up when its finished?",
    "open whichever searxng report exists",
    "if its done, read me the top findings",
])
def test_research_job_followups_retain_research_family(message):
    history = [{"role": "assistant", "content": "Research started.", "metadata": {
        "tool_events": [{"tool": "trigger_research", "exit_code": 0}],
    }}]
    assert requested_capabilities(message, history) == {"research"}


def test_contextual_item_detail_retains_successful_skill_family():
    history = [{"role": "assistant", "content": "Skills shown.", "metadata": {
        "tool_events": [{"tool": "manage_skills", "exit_code": 0}],
    }}]
    assert requested_capabilities("tell me what the first one does", history) == {"skills"}


def test_terse_again_with_limit_repeats_successful_family():
    history = [{"role": "assistant", "content": "Tasks shown.", "metadata": {
        "tool_events": [{"tool": "manage_tasks", "exit_code": 0}],
    }}]
    assert requested_capabilities("again pls, max 3", history) == {"tasks"}
    assert required_read_operation_for_request(
        "again pls, max 3", history
    ) == RequiredReadOperation("manage_tasks", {"action": "list"}, 3)


def test_cached_models_question_selects_cached_inventory_tool():
    assert selected_tools_for_request(
        "what models are already cached on that workstation one"
    ) == {"list_cached_models"}


def test_any_cached_models_question_selects_cached_inventory_tool():
    message = "any models already cached on that server?"
    assert requested_capabilities(message) == {"cookbook_admin"}
    assert selected_tools_for_request(message) == {"list_cached_models"}


def test_cached_models_common_model_typo_seals_cached_inventory_read():
    message = "What modles are cached on the workstation?"
    assert requested_capabilities(message) == {"cookbook_admin"}
    assert selected_tools_for_request(message) == {"list_cached_models"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "list_cached_models",
    )


def test_launch_named_serve_preset_routes_to_cookbook_and_selects_launcher():
    message = "Launch my SD3.5 preset."
    assert requested_capabilities(message) == {"cookbook_admin"}
    assert selected_tools_for_request(message) == {"serve_preset"}


def test_show_notes_with_compact_count_is_data_read_not_panel_navigation():
    operation = required_read_operation_for_request("show my notes, just three titles")
    assert requested_capabilities("show my notes, just three titles") == {"notes"}
    assert operation == RequiredReadOperation("manage_notes", {"action": "list"}, 3)


def test_conversational_lead_does_not_hide_explicit_notes_read():
    message = "Hey, quick one — list my notes, just up to three titles."
    assert requested_capabilities(message) == {"notes"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_notes", {"action": "list"}, 3,
    )


def test_em_dash_item_limit_keeps_memory_request_as_data_read():
    message = "show me my memories — three max and keep them short. dont change anything"
    assert requested_capabilities(message) == {"memory"}
    assert required_read_operation_for_request(message) == RequiredReadOperation(
        "manage_memory", {"action": "list"}, 3,
    )


def test_contextual_memory_filter_selects_memory_search():
    history = [{"role": "assistant", "content": "Memories shown.", "metadata": {
        "tool_events": [{"tool": "manage_memory", "exit_code": 0}],
    }}]
    message = "anything in there about coffee?"
    assert requested_capabilities(message, history) == {"memory"}
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_memory", {"action": "search", "text": "coffee"}, None,
    )


@pytest.mark.parametrize("tool,message,expected", [
    ("web_search", "any updates from the last day?", {"search_browser"}),
    ("manage_memory", "any of em contacts?", {"memory"}),
    ("manage_notes", "put todays date in the note title too", {"notes"}),
    ("manage_notes", "Add a checklist item under it: call the bank tomorrow.", {"notes"}),
])
def test_short_contextual_followups_retain_the_latest_successful_family(tool, message, expected):
    history = [{"role": "assistant", "content": "Done.", "metadata": {
        "tool_events": [{"tool": tool, "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(message, history) == expected


def test_open_referenced_document_in_editor_offers_ui_and_document_context():
    history = [{"role": "assistant", "content": "Document created.", "metadata": {
        "tool_events": [{"tool": "create_document", "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(
        "now open that in the document editor so i can see it", history,
    ) == {"documents", "ui"}

    note_history = [{"role": "assistant", "content": "Created.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(
        "now open that in the document editor so i can see it", note_history,
    ) == {"documents", "ui"}


def test_human_skillz_typo_routes_to_skills_inventory():
    assert requested_capabilities("quick skills check: names of max 3 skillz pls") == {"skills"}


def test_skill_property_followup_is_not_stolen_by_the_email_topic():
    history = [{"role": "assistant", "content": "Three skills shown.", "metadata": {
        "tool_events": [{"tool": "manage_skills", "exit_code": 0, "error": False}],
    }}]
    assert requested_capabilities(
        "does one of them cover email triage?", history,
    ) == {"skills"}


def test_local_disk_free_lookup_routes_to_shell_files():
    assert requested_capabilities("how much disk is free here") == {"shell_files"}


@pytest.mark.parametrize("message", [
    "can u use bashh to run pwd?",
    "run this read only command and tell me what it prints:\n```sh\nprintf '%s\\n' ODY_CHECK; hostname\n```",
])
def test_natural_shell_requests_with_typos_or_fences_route_to_shell(message):
    assert requested_capabilities(message, workspace=True) == {"shell_files"}


@pytest.mark.parametrize("message", [
    "can you do a quick read-only check for me? echo the marker ODY_SHELL_FILES_READONLY and cat /etc/hostname",
    "Can you do a quick read-only test? Echo the marker ODY_SHELL_FILES_READONLY, then cat /etc/hostname.",
])
def test_explicit_readonly_command_sequence_routes_to_shell(message):
    assert requested_capabilities(message) == {"shell_files"}


def test_system_identity_followup_retains_successful_shell_family():
    history = [{"role": "assistant", "content": "Command output.", "metadata": {
        "tool_events": [{"tool": "bash", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "also who am i logged in as and how long has it been up", history,
    ) == {"shell_files"}


def test_conversational_current_events_and_topic_followup_route_to_web():
    assert requested_capabilities("whats happening in japan lately") == {"search_browser"}
    history = [{"role": "assistant", "content": "Japan news.", "metadata": {
        "tool_events": [{"tool": "web_search", "exit_code": 0}],
    }}]
    assert requested_capabilities("more on the flooding pls", history) == {"search_browser"}


def test_referential_grab_and_read_retains_web_search_family():
    history = [{"role": "assistant", "content": "Search results.", "metadata": {
        "tool_events": [{
            "tool": "web_search", "command": {"query": "Germany current events"},
            "exit_code": 0,
        }],
    }}]

    assert requested_capabilities("grab the top story and read it", history) == {
        "search_browser",
    }
    assert requested_capabilities(
        "now pull the page title and last-updated date off that link", history,
    ) == {"search_browser"}


def test_natural_document_list_slang_routes_to_documents():
    assert requested_capabilities("gimme my doc list, just three") == {"documents"}


@pytest.mark.parametrize("message", [
    "Open the theme settings.",
    "open up theme settings for me",
    "can you open theme settings",
])
def test_theme_settings_navigation_only_routes_to_ui(message):
    assert requested_capabilities(message) == {"ui"}


@pytest.mark.parametrize("message", [
    "cookbook server status check, names only plz",
    "cookbook servers, brief pls",
])
def test_natural_cookbook_server_inventory_routes_to_admin(message):
    assert requested_capabilities(message) == {"cookbook_admin"}


def test_saved_documents_inventory_question_routes_to_documents():
    assert requested_capabilities("quick, what documents do i have saved?") == {"documents"}


@pytest.mark.parametrize("message,expected", [
    ("what skills do I got? just a few names", {"skills"}),
    ("quick doc list pls, 3 titles", {"documents"}),
])
def test_colloquial_inventory_requests_route_to_named_store(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("message,expected", [
    ("what mail accounts r hooked up to odysseus?", {"email"}),
    ("give me up to three doc titles from my library", {"documents"}),
    ("quick memory dump - whats saved?", {"memory"}),
])
def test_additional_colloquial_private_inventory_requests(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("target,expected", [
    ("documents", {"documents"}),
    ("notes", {"notes"}),
    ("memories", {"memory"}),
    ("skills", {"skills"}),
    ("tasks", {"tasks"}),
])
def test_explicit_readonly_inventory_wrapper_routes_named_store(target, expected):
    assert requested_capabilities(
        f"I want a quick read-only list of my {target}. At most three titles."
    ) == expected


@pytest.mark.parametrize("message,tool,action,maximum", [
    ("my notes pls, three titles max", "manage_notes", "list", 3),
    ("my skills, max 3", "manage_skills", "list", 3),
    ("whats coming up on my calender? up to three titles with times, read-only, dont change anything", "manage_calendar", "list_events", 3),
    ("Hey can you pull up my saved memores? Only show me the first 3 short ones. Read-only plz, don't change or send anything.", "manage_memory", "list", 3),
    ("I need a quick read-only peek at my saved memores. Just three short ones.", "manage_memory", "list", 3),
    ("give me the top 3 titles in my documents", "manage_documents", "list", 3),
    ("what scheduled tasks do i have? three names + status max", "manage_tasks", "list", 3),
    ("what do you remember about me? show me like three things max", "manage_memory", "list", 3),
])
def test_personal_inventory_variants_seal_one_capped_read(message, tool, action, maximum):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, {"action": action}, maximum)
    assert requested_capabilities(message)


@pytest.mark.parametrize("target,tool", [
    ("skills", "manage_skills"),
    ("notes", "manage_notes"),
    ("tasks", "manage_tasks"),
    ("documents", "manage_documents"),
    ("memory", "manage_memory"),
])
def test_misspelled_whats_in_personal_store_library_seals_list(target, tool):
    message = f"wuts in my {target} library? three names max, read-only pls"
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, {"action": "list"}, 3)
    assert requested_capabilities(message)


@pytest.mark.parametrize("message,tool,maximum", [
    ("Give me my note titles, max three.", "manage_notes", 3),
    ("skills list pls", "manage_skills", None),
])
def test_terse_personal_store_title_lists_are_sealed(message, tool, maximum):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, {"action": "list"}, maximum)
    assert requested_capabilities(message)


@pytest.mark.parametrize('message,tool,action,maximum', [
    ('my notes pls, three titles max', 'manage_notes', 'list', 3),
    ('my skills, max 3', 'manage_skills', 'list', 3),
    ('give me the top 3 titles in my documents', 'manage_documents', 'list', 3),
    (
        'whats coming up on my calender? up to three titles with times, read-only, dont change anything',
        'manage_calendar', 'list_events', 3,
    ),
    (
        "Hey can you pull up my saved memores? Only show me the first 3 short ones. Read-only plz, don't change or send anything.",
        'manage_memory', 'list', 3,
    ),
    (
        'I need a quick read-only peek at my saved memores. Just three short ones.',
        'manage_memory', 'list', 3,
    ),
    (
        'what scheduled tasks do i have? three names + status max',
        'manage_tasks', 'list', 3,
    ),
])
def test_natural_capped_inventory_phrasing_seals_read_operation(
    message, tool, action, maximum,
):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, {'action': action}, maximum)
    family = {
        'manage_notes': 'notes', 'manage_skills': 'skills',
        'manage_documents': 'documents', 'manage_calendar': 'calendar',
        'manage_memory': 'memory', 'manage_tasks': 'tasks',
    }[tool]
    assert requested_capabilities(message) == {family}


def test_current_ai_week_question_routes_to_web_search_family():
    assert requested_capabilities("What's new in AI this week?") == {'search_browser'}


@pytest.mark.parametrize('message', [
    'Can you confirm one of those with the original page?',
    'Can you confrim one of those with the original page?',
    'verify that against the official source link',
])
def test_source_verification_followup_selects_fetch_not_repeat_search(message):
    assert selected_tools_for_request(message) == {'web_fetch'}
    assert requested_capabilities(message) == {'search_browser'}


@pytest.mark.parametrize('message', [
    'open that link and tell me the main heading',
    'pull up the page and tell me what it says at the top',
    'check that source for the publication date',
])
def test_referential_page_read_selects_fetch(message):
    assert selected_tools_for_request(message) == {'web_fetch'}
    assert requested_capabilities(message) == {'search_browser'}


def test_multiple_explicit_text_sources_select_fetch_for_comparison():
    message = (
        "Use Odysseus web retrieval tools to open both authoritative URLs, "
        "compare their evidence, and explain the result citing both sources. "
        "Sources: https://www.rfc-editor.org/rfc/rfc6585.txt and "
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429"
    )

    assert selected_tools_for_request(message) == {"web_fetch"}
    assert requested_capabilities(message) == {"search_browser"}


def test_multiple_urls_with_interaction_select_the_interactive_browser():
    message = (
        "Open https://example.com and https://example.org, click the current "
        "result on each, and compare what is rendered."
    )

    # The interactive browser supports navigation and clicks on both pages;
    # text-only retrieval cannot satisfy this request.
    assert selected_tools_for_request(message) == {"private_browser"}


@pytest.mark.parametrize('message', [
    'double check that against a proper source and give me the link',
    'cross-check it with another credible source and cite the URL',
])
def test_independent_source_verification_selects_fresh_search(message):
    assert selected_tools_for_request(message) == {'web_search'}
    assert requested_capabilities(message) == {'search_browser'}
    assert requests_independent_web_source(message)


@pytest.mark.parametrize('message,tool,args,maximum,family', [
    ('skills please — names only, no edits', 'manage_skills', {'action': 'list'}, None, 'skills'),
    ('memories pls, three short entries, read only', 'manage_memory', {'action': 'list'}, 3, 'memory'),
    ('I need my errand stuff, show notes tagged errands', 'manage_notes', {'action': 'list', 'label': 'errands'}, None, 'notes'),
    ("while that's open, bring up my calender for this week", 'manage_calendar', {'action': 'list_events'}, None, 'calendar'),
])
def test_natural_read_only_inventory_phrasing_seals_operation(message, tool, args, maximum, family):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, args, maximum)
    assert requested_capabilities(message) == {family}


def test_unread_account_choice_after_account_list_requires_unread_inbox_read():
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "list_email_accounts", "exit_code": 0,
    }]}}]
    message = "which one should i check first for unread?"
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "list_emails", {"folder": "INBOX", "unread_only": True}, None,
    )
    assert requested_capabilities(message, history) == {"email"}


def test_open_official_page_selects_fetch():
    message = "open the official page and tell me the main sections"
    assert selected_tools_for_request(message) == {"web_fetch"}
    assert requested_capabilities(message) == {"search_browser"}


def test_show_email_accounts_in_panel_routes_to_ui_not_email_data():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "list_email_accounts", "exit_code": 0},
    ]}}]
    assert requested_capabilities(
        "ok now show my email accounts in the panel", history,
    ) == {"ui"}


@pytest.mark.parametrize("message", [
    "cool, while your at it pop open my notes panel too",
    "nice, now opne the Skills panel so i can poke thru those myself",
])
def test_typo_tolerant_panel_navigation_routes_to_ui(message):
    assert requested_capabilities(message) == {"ui"}


def test_filename_named_like_product_routes_to_filesystem():
    assert requested_capabilities(
        "can you chek whether notes.txt already exists before i add stuff to it?",
        workspace=True,
    ) == {"shell_files"}


def test_compound_notes_readback_and_open_view_keeps_both_capabilities():
    history = [{"role": "assistant", "content": "Three notes.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "read those again at most three, then open the notes view.", history,
    ) == {"notes", "ui"}


def test_note_down_after_account_lookup_switches_to_notes():
    history = [{"role": "assistant", "content": "Two email accounts.", "metadata": {
        "tool_events": [{"tool": "list_email_accounts", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "note down which one is for work so i dont forget", history,
    ) == {"notes"}


def test_misspelled_calendar_switch_overrides_recent_tasks():
    history = [{"role": "assistant", "content": "Three tasks.", "metadata": {
        "tool_events": [{"tool": "manage_tasks", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "also whats on my calender tomorow morning?", history,
    ) == {"calendar"}


def test_explicit_calendar_comparison_switches_from_recent_notes():
    history = [{"role": "assistant", "content": "Three notes.", "metadata": {
        "tool_events": [{"tool": "manage_notes", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "is there a calendar thing tied to any of them? just check, dont change anything",
        history,
    ) == {"calendar"}


def test_calendar_title_request_and_compound_schedule_panel_request_route():
    assert requested_capabilities(
        "give me up to three event titles from my calendar, nothing else. read-only.",
    ) == {"calendar"}
    assert requested_capabilities(
        "same list again, and open the schedule panel.",
    ) == {"ui"}

    history = [{"role": "assistant", "content": "Events listed.", "metadata": {
        "tool_events": [{
            "tool": "manage_calendar", "command": {"action": "list_events"}, "exit_code": 0,
        }],
    }}]
    assert requested_capabilities(
        "read those back to me again, at most three, and pop open the calendar panel.",
        history,
    ) == {"calendar", "ui"}


def test_split_pull_up_calendar_request_routes_as_a_standalone_read():
    assert requested_capabilities("pull my calendar events up again") == {"calendar"}


@pytest.mark.parametrize("message", [
    "whats 9x7, do it with python",
    "calculate 12**9 using python",
])
def test_explicit_python_execution_after_conversational_text_routes_to_shell(message):
    assert requested_capabilities(message) == {"shell_files"}


def test_location_first_temporary_file_workflow_routes_to_shell():
    message = (
        "in a temp dir make two small text files, get their checksums, then try to "
        "checksum a third file that isnt there, recover by listing the folder, and "
        "report the valid checksums"
    )
    assert requested_capabilities(message) == {"shell_files"}


def test_open_notes_and_create_note_keeps_ui_and_notes_capabilities():
    assert requested_capabilities(
        "now open notes and make a short note called 'week ahead' that sums that up",
    ) == {"ui", "notes"}


def test_same_read_only_command_followup_retains_shell_family():
    history = [{"role": "assistant", "content": "Command output.", "metadata": {
        "tool_events": [{"tool": "bash", "exit_code": 0}],
    }}]
    assert requested_capabilities(
        "Run the same read-only command one more time and report what it prints.",
        history,
        workspace=True,
    ) == {"shell_files"}


def test_explicit_corrected_inline_command_routes_to_shell_without_bash_noun():
    history = [
        {"role": "user", "content": "Use bash to run a failing command."},
        {"role": "assistant", "content": "It exited 7."},
        {"role": "user", "content": "What was its exit code?"},
        {"role": "assistant", "content": "7"},
    ]
    assert requested_capabilities(
        "Now run this corrected read-only command and show its output: "
        "printf 'RECOVERY_OK\\n'",
        history,
        workspace=True,
    ) == {"shell_files"}


def test_explicit_note_existence_lookup_keeps_notes_available():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "manage_notes", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "is there any note about the cabin trip anywhere?", history,
    ) == {"notes"}


def test_typoed_scheduled_jobs_lookup_is_a_sealed_task_read():
    prompt = "give me a quick look at my scheduld jobs, three max."
    assert requested_capabilities(prompt) == {"tasks"}
    assert required_read_operation_for_request(prompt) == RequiredReadOperation(
        "manage_tasks", {"action": "list"}, 3,
    )


def test_automated_tasks_few_limit_is_inherited_by_same_short_list():
    first = "What automated tasks do I have set up? Just list a few names and whether they're active."
    operation = required_read_operation_for_request(first)
    assert operation == RequiredReadOperation("manage_tasks", {"action": "list"}, 3)
    history = [
        {"role": "user", "content": first},
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "manage_tasks", "exit_code": 0, "error": False},
        ]}},
    ]
    repeated = required_read_operation_for_request(
        "and can you show the same short list again? I want to verify nothing changed.", history,
    )
    assert repeated == RequiredReadOperation("manage_tasks", {"action": "list"}, 3)


@pytest.mark.parametrize("prompt", [
    "show me my nots, maybe first three titles.",
    "what notes do I have? keep it to three titles.",
    "list my calendar events, top 3 titles",
])
def test_conversational_read_limits_are_sealed(prompt):
    operation = required_read_operation_for_request(prompt)
    assert operation is not None
    assert operation.max_items == 3


def test_recent_calendar_handles_implicit_dated_availability_followup():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "manage_calendar", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "did I already put anything on friday afternoon? just checking", history,
    ) == {"calendar"}


def test_recent_web_result_handles_embedded_fetch_confirmation():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "web_search", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "does that page confirm the model name? fetch it and check", history,
    ) == {"search_browser"}


def test_current_ai_and_source_open_followups_keep_web_tools_available():
    assert requested_capabilities("Anything important in AI today?") == {"search_browser"}
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "web_search", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "Great, open one of the sources you used.", history,
    ) == {"search_browser"}
    assert requested_capabilities(
        "Double-check the top story with a direct source.", history,
    ) == {"search_browser"}


@pytest.mark.parametrize(("prompt", "expected"), [
    (
        "my calendar events pls, max 3 with times, read-only",
        RequiredReadOperation("manage_calendar", {"action": "list_events"}, 3),
    ),
    (
        "i lost track of my docs, can u list em? only need 3 titles",
        RequiredReadOperation("manage_documents", {"action": "list"}, 3),
    ),
    (
        "which agent tools are disabled at the moment?",
        RequiredReadOperation("manage_settings", {"action": "list_tools"}),
    ),
    (
        "list my gallery images thru the internal app api and pick out the first image id",
        RequiredReadOperation("app_api", {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        }),
    ),
])
def test_natural_inventory_reads_are_sealed(prompt, expected):
    assert required_read_operation_for_request(prompt) == expected
    assert requested_capabilities(prompt)


def test_natural_gallery_inventory_is_a_sealed_owner_scoped_read():
    prompt = (
        "hey, can u look thru my gallery and tell me wich image is the first one "
        "in there? just list them for now, dont change anything yet"
    )
    assert required_read_operation_for_request(prompt) == RequiredReadOperation(
        "app_api", {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        },
    )
    assert requested_capabilities(prompt) == {"cookbook_admin"}


def test_gallery_ordinal_upscale_uses_successful_gallery_read_context():
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "app_api",
        "command": {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        },
        "exit_code": 0,
        "error": False,
    }]}}]
    assert requested_capabilities(
        "ok, upscale that first one by 2x for me", history,
    ) == {"image_editing"}
    history.append({"role": "assistant", "metadata": {"tool_events": [{
        "tool": "edit_image",
        "command": {"image_id": "owned-image", "action": "upscale", "scale": 2},
        "exit_code": 0,
        "error": False,
    }]}})
    assert requested_capabilities(
        "list the gallery again and confirm the new upscaled record actually shows up",
        history,
    ) == {"cookbook_admin"}


def test_typoed_same_memory_repeat_inherits_the_unfiltered_list():
    history = [
        {"role": "user", "content": "list my saved memories, three short entries please"},
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "manage_memory", "command": '{"action":"list"}',
             "exit_code": 0, "error": False},
        ]}},
    ]
    assert required_read_operation_for_request(
        "those agian, max three, read only", history,
    ) == RequiredReadOperation("manage_memory", {"action": "list"}, 3)


def test_gallery_read_upscale_and_recheck_keep_typed_tools():
    first = ("hey, can u look thru my gallery and tell me wich image is the first one in there? "
             "just list them for now, dont change anything yet")
    operation = required_read_operation_for_request(first)
    assert operation == RequiredReadOperation("app_api", {
        "action": "call", "method": "GET", "path": "/api/gallery/library",
    })
    history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "app_api",
        "command": {"action": "call", "method": "GET", "path": "/api/gallery/library"},
        "exit_code": 0, "error": False,
    }]}}]
    assert requested_capabilities("ok, upscale that first one by 2x for me", history) == {
        "image_editing",
    }
    assert required_read_operation_for_request(
        "now re-check the gallery and tell me if the upscaled copy actually shows up",
        history,
    ) == RequiredReadOperation("app_api", {
        "action": "call", "method": "GET", "path": "/api/gallery/library",
    })


def test_model_inventory_contains_the_existing_image_editor():
    from src.turn_contract import resolve_full_inventory_contract
    contract = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS, policy=ToolPolicy(),
    )
    assert "edit_image" in contract.offered


def test_email_result_followups_survive_typos_and_cross_family_request():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "mcp__email__list_email_accounts", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "which accnt has the invoice from the print shop? read only pls", history,
    ) == {"email"}
    assert requested_capabilities(
        "read me the latest one from them, and also check my calendar for print shop dates", history,
    ) == {"email", "calendar"}


@pytest.mark.parametrize("followup", [
    "ok list those again but tell me if theres more than one",
    "show me the list then",
    "those again — is my work one in there?",
])
def test_contextual_account_relist_with_question_reuses_account_reader(followup):
    history = [
        {"role": "user", "content": "show my email acounts"},
        {"role": "assistant", "metadata": {"tool_events": [{
            "tool": "mcp__email__list_email_accounts", "exit_code": 0,
        }]}},
    ]
    assert required_read_operation_for_request(followup, history) == RequiredReadOperation(
        "list_email_accounts",
    )


def test_save_prior_link_to_note_routes_note_mutation():
    assert requested_capabilities("ok cool, also save that link to a note for me") == {"notes"}


def test_referential_one_liner_save_routes_to_notes_after_web():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "web_search", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "jot that one-liner down in a quick note", history,
    ) == {"notes"}


def test_typoed_original_page_confirmation_keeps_web_tools_warm():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "web_search", "exit_code": 0, "error": False},
    ]}}]
    assert requested_capabilities(
        "Can you confrim one of those with the original page?", history,
    ) == {"search_browser"}


def test_compound_latest_email_and_calendar_selects_both_reads():
    assert selected_tools_for_request(
        "read me the latest one from them, and also check my calendar for print shop dates this month"
    ) == {"search_emails", "read_email", "manage_calendar"}


@pytest.mark.parametrize("tool,prompt,expected_tool", [
    ("manage_tasks", "ok list those again, only three, and dont touch anything", "manage_tasks"),
    ("manage_notes", "can you show the same titles again? I'm checking if I missed one.", "manage_notes"),
])
def test_exact_repeat_with_safety_or_rationale_seals_latest_read(tool, prompt, expected_tool):
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": tool, "exit_code": 0, "error": False},
    ]}}]
    operation = required_read_operation_for_request(prompt, history)
    assert operation is not None
    assert operation.tool == expected_tool


def test_exact_repeat_inherits_prior_numeric_cap():
    history = [
        {"role": "user", "content": "show me my nots, maybe first three titles."},
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "manage_notes", "exit_code": 0, "error": False},
        ]}},
    ]
    operation = required_read_operation_for_request(
        "can you show the same titles again? I'm checking if I missed one.", history,
    )
    assert operation == RequiredReadOperation("manage_notes", {"action": "list"}, 3)


def test_memory_few_limit_and_short_repeat_are_sealed_reads():
    opening = "show me my saved memories, only a few"
    first = required_read_operation_for_request(opening)
    assert first == RequiredReadOperation("manage_memory", {"action": "list"}, 3)

    history = [
        {"role": "user", "content": opening},
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "manage_memory", "exit_code": 0, "error": False},
        ]}},
    ]
    assert required_read_operation_for_request(
        "can you show those again? just the short versions", history,
    ) == RequiredReadOperation("manage_memory", {"action": "list"}, 3)


def test_skill_list_filter_followup_stays_in_skills_and_seals_search():
    history = [{"role": "assistant", "metadata": {"tool_events": [
        {"tool": "manage_skills", "exit_code": 0, "error": False},
    ]}}]
    message = "any of those about email or docs?"
    assert requested_capabilities(message, history) == {"skills"}
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_skills", {"action": "search", "query": "email or docs"}
    )


@pytest.mark.parametrize("tool,followup", [
    ("search_hf_models", "Search those again, but narrow it to 9B models."),
    ("youtube_tool", "Now get its transcript with the same YouTube tool."),
    ("pdf_extract", "From that same PDF, extract passages about positional encoding."),
])
def test_referential_web_subtool_followup_uses_latest_successful_tool_family(tool, followup):
    history = [
        {"role": "user", "content": "Initial public fixture request"},
        {"role": "assistant", "content": "Result", "metadata": {
            "tool_events": [{"tool": tool, "exit_code": 0, "error": False}],
        }},
    ]
    assert requested_capabilities(followup, history) == {"search_browser"}


def test_failed_tool_event_is_not_a_referential_warm_antecedent():
    history = [
        {"role": "assistant", "content": "Failed", "metadata": {
            "tool_events": [{"tool": "youtube_tool", "exit_code": 1, "error": True}],
        }},
    ]
    assert requested_capabilities("Now get its transcript", history) == frozenset()


def test_recent_executed_family_can_be_recalled_after_another_family():
    history = [
        {"role": "user", "content": "Show my calendar"},
        {"role": "assistant", "content": "Events", "metadata": {
            "tool_events": [{"tool": "manage_calendar"}],
        }},
        {"role": "user", "content": "Show my email accounts"},
        {"role": "assistant", "content": "Accounts", "metadata": {
            "tool_events": [{"tool": "mcp__email__list_email_accounts"}],
        }},
    ]
    assert requested_capabilities("Back to my calendar", history) == {"calendar"}
    assert requested_capabilities(
        "Back to my calendar—what's next after that?", history
    ) == {"calendar"}


def test_family_recall_expires_after_six_user_turns():
    history = [
        {"role": "user", "content": "Show my calendar"},
        {"role": "assistant", "content": "Events", "metadata": {
            "tool_events": [{"tool": "manage_calendar"}],
        }},
    ]
    for index in range(7):
        history.extend((
            {"role": "user", "content": f"Unrelated question {index}"},
            {"role": "assistant", "content": "Answer"},
        ))
    assert requested_capabilities("Calendar again", history) == frozenset()


def test_unexecuted_family_name_is_not_a_warm_recall():
    assert requested_capabilities("Calendar again", []) == frozenset()


def test_misspelled_action_inherits_immediately_preceding_calendar():
    history = [
        {"role": "user", "content": "Can you add a meeting on Monday?"},
        {"role": "assistant", "content": "Created event Meeting."},
    ]
    assert requested_capabilities("Chnage title to Jessica", history) == {"calendar"}


def test_misspelled_action_without_context_does_not_guess_a_family():
    assert requested_capabilities("Chnage title to Jessica") == {"unknown"}


def test_warm_family_is_offered_without_becoming_required():
    contract = resolve(
        {"calendar", "email"},
        required_capabilities={"email"},
    )
    assert "manage_calendar" in contract.offered
    assert "manage_calendar" not in contract.required


def test_natural_email_lookup_variants_route_without_history():
    for prompt in (
        "Any emails from Casey?",
        "What's todays emails?",
        "Do I have unread email?",
    ):
        assert requested_capabilities(prompt) == {"email"}


@pytest.mark.parametrize("message,expected", [
    ("What's my caledar this week?", {"calendar"}),
    ("List my skils", {"skills"}),
    ("show my emals", {"email"}),
    ("search noes for passport", {"notes"}),
    ("show cookbok downloads", {"cookbook_admin"}),
    ("Use bssh to run pwd", {"shell_files"}),
    ("Use bsah to run pwd", {"shell_files"}),
    ("Lst my noets", {"notes"}),
    ("Lst my calndar events", {"calendar"}),
    ("Lst my configured emial accounts", {"email"}),
    ("Lst my scheduled taks", {"tasks"}),
    ("Lst my documnts", {"documents"}),
    ("Lst my saved memries", {"memory"}),
    ("Lst my saved skils", {"skills"}),
    ("Lst configured Cookbok servers", {"cookbook_admin"}),
    ("What is a caledar?", set()),
    ("The skils discussion was interesting", set()),
])
def test_conservative_typo_family_routing(message, expected):
    assert requested_capabilities(message) == expected


def test_common_contraction_and_turn_prefix_typos_route_new_family_requests():
    assert requested_capabilities("whats my notes") == {"notes"}
    assert requested_capabilities("now show my noes") == {"notes"}


def test_typoed_personal_calendar_question_seals_safe_read_contract():
    message = "Wat is on my calender this week?"
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(
        "manage_calendar", {"action": "list_events"}
    )
    assert requested_capabilities(message) == {"calendar"}


def test_what_about_explicit_family_switch_routes_the_named_family():
    history = [
        {"role": "user", "content": "whats my 5 latest"},
        {
            "role": "assistant",
            "content": "Here are your latest emails.",
            "metadata": {
                "tool_events": [
                    {"tool": "mcp__email__list_emails", "exit_code": 0, "error": False}
                ]
            },
        },
    ]

    assert requested_capabilities("what about my notes", history) == {"notes"}
    assert requested_capabilities("what about my calendar", history) == {"calendar"}
    assert requested_capabilities("what about my tasks", history) == {"tasks"}
    operation = required_read_operation_for_request("what about my notes", history)
    assert operation == RequiredReadOperation("manage_notes", {"action": "list"})


def test_contextual_latest_email_read_is_operation_specific():
    history = [
        {"role": "assistant", "content": "Accounts listed.", "metadata": {
            "tool_events": [{"tool": "mcp__email__list_email_accounts", "exit_code": 0}],
        }},
    ]
    message = "whats my 5 latest"
    operation = required_read_operation_for_request(message, history)
    assert operation == RequiredReadOperation(
        "list_emails", {"max_results": 5}, max_items=5
    )
    contract = resolve_turn_contract(
        capabilities=requested_capabilities(message, history),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        message=message,
        history=history,
    )
    assert {canonical_tool(name) for name in contract.offered} == {
        "list_emails", "ask_user", "update_plan",
    }


def test_ordinal_email_followup_binds_prior_server_owned_uid_and_account():
    tool_output = {
        "stdout": (
            "Found 3 email(s):\n\n"
            "1. **First**\n   UID: 701\n   Account: Primary <primary@example.test>\n\n"
            "2. **Second**\n   UID: 702\n   Account: Work <work@example.test>\n\n"
            "3. **Third**\n   UID: 703\n   Account: Primary <primary@example.test>"
        )
    }
    history = [{
        "role": "assistant",
        "content": "Three emails listed.",
        "metadata": {"tool_events": [{
            "tool": "mcp__email__list_emails",
            "output": json.dumps(tool_output),
            "exit_code": 0,
            "error": False,
        }]},
    }]
    message = "Read the second email from the earlier inbox list and summarize it."
    assert selected_tools_for_request(message) == {"read_email"}
    operation = required_read_operation_for_request(message, history)
    assert operation == RequiredReadOperation(
        "read_email", {"uid": "702", "account": "work@example.test"}
    )
    contract = resolve_turn_contract(
        capabilities=requested_capabilities(message, history),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        message=message,
        history=history,
    )
    assert {canonical_tool(name) for name in contract.offered} == {
        "read_email", "ask_user", "update_plan",
    }

    clean_history = [{
        "role": "assistant",
        "content": "Three emails listed.",
        "metadata": {"clean_v3_turn": [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-list", "type": "function",
                "function": {"name": "mcp__email__list_emails", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call-list", "content": json.dumps(tool_output)},
            {"role": "assistant", "content": "Three emails listed."},
        ]},
    }]
    assert required_read_operation_for_request(message, clean_history) == operation
    assert required_read_operation_for_request(
        message, clean_history[0]["metadata"]["clean_v3_turn"]
    ) == operation


def test_ordinal_skill_followup_binds_prior_server_owned_name():
    output = json.dumps({"results": "- **alpha-skill**\n- **beta-skill**\n- **gamma-skill**"})
    history = [{
        "role": "assistant", "content": "Skills listed.", "metadata": {
            "tool_events": [{
                "tool": "manage_skills", "command": json.dumps({"action": "list"}),
                "output": output, "exit_code": 0, "error": False,
            }],
        },
    }]
    message = "Show the second skill from the earlier skill list."
    assert selected_tools_for_request(message) == {"manage_skills"}
    operation = required_read_operation_for_request(message, history)
    assert operation == RequiredReadOperation(
        "manage_skills", {"action": "view", "name": "beta-skill"}
    )


def test_contextual_ordinal_skill_procedure_binds_prior_server_owned_name():
    output = json.dumps({"results": "- **alpha-skill**\n- **beta-skill**\n- **gamma-skill**"})
    history = [{
        "role": "assistant", "content": "Skills listed.", "metadata": {
            "tool_events": [{
                "tool": "manage_skills", "command": json.dumps({"action": "list"}),
                "output": output, "exit_code": 0, "error": False,
            }],
        },
    }]

    message = "view the first one — whats its procedure?"
    assert requested_capabilities(message, history) == {"skills"}
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_skills", {"action": "view", "name": "alpha-skill"}
    )


def test_skill_detail_pronoun_reuses_latest_successful_server_owned_view():
    history = [{
        "role": "assistant", "content": "Here is beta-skill.", "metadata": {
            "tool_events": [{
                "tool": "manage_skills",
                "command": {"action": "view", "name": "beta-skill"},
                "output": "# beta-skill\n\n## Procedure\n1. Check it.",
                "exit_code": 0,
                "error": False,
            }],
        },
    }]

    message = "Now read its full procedure and verification steps. Do not execute the procedure."
    assert required_read_operation_for_request(message, history) == RequiredReadOperation(
        "manage_skills", {"action": "view", "name": "beta-skill"}
    )


@pytest.mark.parametrize("message", [
    "can you also check what i have on today?",
    "cool, anything on my calendar today?",
])
def test_explicit_calendar_switch_overrides_prior_notes_context(message):
    history = [{
        "role": "assistant", "content": "Notes listed.", "metadata": {
            "tool_events": [{
                "tool": "manage_notes", "command": {"action": "list"}, "exit_code": 0,
            }],
        },
    }]

    assert requested_capabilities(message, history) == {"calendar"}


def test_calendar_existence_comparison_overrides_prior_notes_context():
    history = [{
        "role": "assistant", "content": "Notes listed.", "metadata": {
            "tool_events": [{
                "tool": "manage_notes", "command": {"action": "list"}, "exit_code": 0,
            }],
        },
    }]

    assert requested_capabilities(
        "is there a calendar thing tied to any of them? just check, dont change anything",
        history,
    ) == {"calendar"}


@pytest.mark.parametrize("message,tool,action", [
    ("Show my noes", "manage_notes", "list"),
    ("List my scheduled taks", "manage_tasks", "list"),
    ("List my saved memo ries", "manage_memory", "list"),
    ("List my skils", "manage_skills", "list"),
    ("Show cookbok servers", "list_cookbook_servers", None),
])
def test_explicit_typo_lists_create_sealed_safe_reads(message, tool, action):
    operation = required_read_operation_for_request(message)
    assert operation.tool == tool
    assert operation.args.get("action") == action


@pytest.mark.parametrize("message,tool,action", [
    ("Lst my noets", "manage_notes", "list"),
    ("Lst my calndar events", "manage_calendar", "list_events"),
    ("Lst my configured emial accounts", "list_email_accounts", None),
    ("Lst my scheduled taks", "manage_tasks", "list"),
    ("Lst my documnts", "manage_documents", "list"),
    ("Lst my saved memries", "manage_memory", "list"),
    ("Lst my saved skils", "manage_skills", "list"),
    ("Lst configured Cookbok servers", "list_cookbook_servers", None),
])
def test_misspelled_read_action_and_target_still_seal_safe_reads(message, tool, action):
    operation = required_read_operation_for_request(message)
    assert operation == RequiredReadOperation(tool, {"action": action} if action else {})


def test_repeat_inherits_typo_sealed_safe_read():
    history = [{"role": "user", "content": "Show my noes"},
               {"role": "assistant", "content": "Notes listed."}]
    operation = required_read_operation_for_request("List those again, at most three.", history)
    assert operation.tool == "manage_notes"
    assert operation.args == {"action": "list"}
    assert operation.max_items == 3


@pytest.mark.parametrize("prior,tool,action", [
    ("What's my caledar this week?", "manage_calendar", "list_events"),
    ("What emil accounts do I have?", "list_email_accounts", None),
])
def test_repeat_inherits_single_safe_family_from_typo_lookup(prior, tool, action):
    history = [{"role": "user", "content": prior},
               {"role": "assistant", "content": "Results shown."}]
    operation = required_read_operation_for_request("List those again, at most three.", history)
    assert operation.tool == tool
    assert operation.args.get("action") == action


def test_misspelled_search_target_outranks_incidental_python_and_safety_words():
    message = ("Seach the web for the official Python packaging guide. "
               "Read-only inspection; do not change data or send messages.")
    assert requested_capabilities(message) == {"search_browser"}


@pytest.mark.parametrize("message,expected", [
    ("What time was the second calendar event from earlier? Check my calendar again.", {"calendar"}),
    ("Which Cookbook server from earlier is the default? Check the server list again.", {"cookbook_admin"}),
    ("Open the first web result from earlier and summarize it.", {"search_browser"}),
    ("Return to that browser page, open the Learn more link, and report the destination heading.", {"search_browser"}),
])
def test_explicit_family_outranks_false_workspace_routing_in_interleaved_followups(message, expected):
    assert requested_capabilities(message) == expected


@pytest.mark.parametrize("message", [
    "In this open document, change Monday to Tuesday.",
    "On the current doc, add a final status line.",
    "In my active email draft, write that Friday works.",
    "I want you to fix my text, find flaws, and especially fix misinformation.",
    "I need you to proofread and revise this.",
    "I'd like you to polish this text.",
    "Clean this up.",
    "Improve this.",
    "Correct the mistakes.",
    "Fact-check this.",
    "Remove any misinformation.",
    "Apply those fixes.",
    "Do the edits.",
    "Go ahead with the revisions.",
    "Work on this.",
])
def test_leading_bound_editor_target_routes_document_mutations(message):
    assert targets_bound_editor_request(message)
    capabilities = requested_capabilities(message, active_document=True)
    assert capabilities == {"documents"}
    contract = resolve(capabilities)
    assert {"edit_document", "update_document", "suggest_document"} <= set(contract.offered)


@pytest.mark.parametrize("message", [
    "Use the web to fact-check this and fix the document.",
    "Search the web, verify the claims, and correct this document.",
    "Check sources and update this.",
    "Add citations and correct misinformation.",
])
def test_bound_editor_verification_keeps_web_and_document_tools(message):
    capabilities = requested_capabilities(message, active_document=True)
    assert capabilities == {"documents", "search_browser"}
    contract = resolve(capabilities)
    assert {"edit_document", "update_document", "suggest_document", "web_search"} <= set(contract.offered)


def test_exact_web_selection_cannot_erase_bound_editor_writers():
    selected = preserve_bound_editor_selected_tools(
        "Look this up online and correct this document.",
        {"web_search"},
        active_document=True,
    )
    assert selected == {
        "web_search", "edit_document", "update_document", "suggest_document",
    }


def test_non_editor_selection_is_not_expanded_by_visible_document():
    assert preserve_bound_editor_selected_tools(
        "Search for the latest AI news.",
        {"web_search"},
        active_document=True,
    ) == {"web_search"}


@pytest.mark.parametrize("message, selected, expected_other_tool", [
    ("Search for the latest AI news.", {"web_search"}, "web_search"),
    ("Here is my entire essay for review: " + "A paragraph. " * 600, {"web_search"}, None),
    ("Hello", None, None),
])
def test_open_editor_tools_survive_request_narrowing(message, selected, expected_other_tool):
    requested = requested_capabilities(message, active_document=True)
    contract = resolve(
        set(requested) | {"documents"},
        required_capabilities=requested,
        selected_tools=selected,
        always_available_tools=FAMILY_TOOLS["documents"],
        message=message,
    )
    assert FAMILY_TOOLS["documents"] <= contract.offered
    if expected_other_tool:
        assert expected_other_tool in contract.offered


def test_open_editor_tools_still_obey_disabled_tool_policy():
    contract = resolve(
        {"search_browser", "documents"},
        required_capabilities={"search_browser"},
        selected_tools={"web_search"},
        always_available_tools=FAMILY_TOOLS["documents"],
        policy=ToolPolicy(disabled_tools=frozenset(FAMILY_TOOLS["documents"])),
    )
    assert not (FAMILY_TOOLS["documents"] & contract.offered)
    assert "web_search" in contract.offered


def test_discourse_prefixed_personal_family_switch_routes_normally():
    assert requested_capabilities("actually show my notes") == {"notes"}


def test_misspelled_research_action_routes_research_family():
    assert requested_capabilities(
        "reserch why boston terriers make good companion dogs"
    ) == {"research"}


def test_referential_show_prefers_latest_successful_tool_family():
    history = [
        {"role": "user", "content": "list my skills"},
        {"role": "assistant", "content": "Skills listed", "metadata": {
            "tool_events": [{"tool": "manage_skills", "exit_code": 0, "error": False}],
        }},
        {"role": "user", "content": "which one is about email?"},
        {"role": "assistant", "content": "The email-related skill is X."},
    ]
    assert requested_capabilities("show it", history) == {"skills"}
    assert required_read_operation_for_request("show it", history) is None
    contract = resolve_turn_contract(
        capabilities=requested_capabilities("show it", history),
        schemas=FUNCTION_TOOL_SCHEMAS,
        policy=ToolPolicy(),
        required_capabilities={"skills"},
        message="show it",
        history=history,
    )
    assert "manage_skills" in contract.offered
    assert not contract.unavailable
def test_long_typoed_read_requests_route_to_the_named_product():
    assert requested_capabilities(
        "hey can you pull up my documets? just the first few titles, nothing fancy."
    ) == {"documents"}
    assert requested_capabilities(
        "What cookbok servers are configured right now? Just show me, don't change anything."
    ) == {"cookbook_admin"}
    assert requested_capabilities(
        "can u use bssh to run pwd real quick? just read-only, dont change anything"
    ) == {"shell_files"}


def test_web_sites_do_not_fuzzy_collide_with_local_files():
    assert requested_capabilities(
        "Find best sites for torrenting films"
    ) == {"search_browser"}
    assert requested_capabilities(
        "I want to torrent films what's the best website"
    ) == {"search_browser"}


def test_explicit_inbox_container_outranks_message_note_noun():
    assert requested_capabilities(
        "read that Priya note in the Primary inbox so ive got the context"
    ) == {"email"}
    operation = required_read_operation_for_request(
        "read that Priya note in the Primary inbox so ive got the context"
    )
    assert operation.tool == "search_emails"
    assert dict(operation.args) == {
        "query": "Priya", "folder": "INBOX", "account": "Primary",
    }


def test_email_panel_navigation_with_trailing_context_stays_ui_only():
    assert requested_capabilities(
        "open the email panel with that draft still available so I can check it before I hit send"
    ) == {"ui"}


def test_reply_draft_wording_routes_to_email_without_a_family_noun():
    assert requested_capabilities(
        "now put together a polite reply draft saying thursday suits better — leave it for me to review"
    ) == {"email"}


def test_tool_toggle_inspection_seals_safe_settings_read():
    operation = required_read_operation_for_request("i wanna eyeball the tool toggles in there")
    assert operation.tool == "manage_settings"
    assert dict(operation.args) == {"action": "list_tools"}


def test_explicit_rerun_inherits_prior_requested_family_even_after_failure():
    history = [
        {"role": "user", "content": "run a read-only shell check and print hostname"},
        {"role": "assistant", "content": "hostname command failed"},
    ]
    assert requested_capabilities(
        "great - just re-run the same read-only check so I can confirm it's stable.", history
    ) == {"shell_files"}


def test_recurring_task_lifecycle_owns_weekday_and_note_input_details():
    assert requested_capabilities(
        "Set up a recurring task that runs every Monday morning to summarize "
        "my open notes, then pause it, resume it later, and finally remove it."
    ) == {"tasks"}


def test_recurring_calendar_event_remains_calendar():
    assert requested_capabilities(
        "Create a recurring calendar event every Monday morning for the team meeting."
    ) == {"calendar"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Inspect /workspace/input/reference.png, recreate all musical notes in "
            "/workspace/output.html, then preview the page in a browser.",
            {"media_inspection", "shell_files", "search_browser"},
        ),
        (
            "First view /workspace/input/reference.png, recreate the piano score with "
            "correct noteheads in /workspace/output.html, and inspect the rendered result.",
            {"media_inspection", "shell_files", "search_browser"},
        ),
        (
            "Transcribe every distinct caption in /workspace/input/video.mp4 from "
            "00:04 through 00:12 and return only the words.",
            {"transcription"},
        ),
    ],
)
def test_explicit_workspace_media_workflow_owns_content_nouns_and_timestamps(
    message, expected
):
    assert requested_capabilities(message) == expected


def test_explicit_personal_note_action_survives_media_workflow_sealing():
    assert requested_capabilities(
        "Inspect /workspace/input/score.png, then save a note in my notes called "
        "Practice Review with the musical details."
    ) == {"media_inspection", "notes"}


def test_watch_workspace_video_is_media_not_generic_shell():
    assert requested_capabilities(
        "Watch the entire match in /workspace/input/video.mp4 and count how many "
        "points Bob won. Return a JSON object in the final answer."
    ) == {"media_inspection"}


def test_review_workspace_video_is_media_not_document_or_web_review():
    message = (
        "Review the video at /workspace/input/video.mp4 which shows a fictional table tennis "
        "match between Alex Chen and Jamie Rivera. The video is a highlights reel. For each "
        "player, count the total number of match points they faced and how many of those they "
        "saved. Ensure you have watched the entire video and verified your counts. Output your "
        "answer in the final message."
    )
    assert requested_capabilities(message) == {"media_inspection"}
    assert selected_tools_for_request(message) is None


def test_explicit_media_verification_does_not_require_browser_family():
    assert requested_capabilities(
        "First inspect /workspace/input/reference.png. Create /workspace/output.html, "
        "then inspect the rendered result using inspect_media on the file or a screenshot."
    ) == {"media_inspection", "shell_files"}


def test_chinese_video_to_image_artifact_keeps_media_and_workspace_tools():
    assert requested_capabilities(
        "请完整浏览 /workspace/input/video.mp4，为每款零食截图并拼成清单图，"
        "输出到 /workspace/snack_checklist.jpg，交付前打开成品图检查。"
    ) == {"media_inspection", "shell_files"}
