import json
from pathlib import Path

from src.agent_loop import (
    _classify_agent_request,
    _contextual_link_followup_topic,
    _is_ambiguous_short_low_signal,
    _is_contextual_link_followup,
    _is_terse_link_request,
    _minimal_recent_notes_tool_context_message,
    _looks_like_explicit_notes_only_turn,
    _looks_like_explicit_web_search_request,
    _malformed_write_needs_body_handoff,
    _post_finish_inspection_should_converge,
    _repeated_artifact_mutation_can_finish,
    _minimal_native_tool_prompt,
    _should_use_direct_low_signal_path,
)


def test_web_audio_local_media_request_is_not_public_web_search() -> None:
    prompt = (
        "View /workspace/fixtures/Canon1.png and create output.html with "
        "a Play button using the Web Audio API."
    )

    assert not _looks_like_explicit_web_search_request(
        prompt,
        local_media_turn=True,
    )


def test_explicit_public_web_request_remains_recognized() -> None:
    assert _looks_like_explicit_web_search_request(
        "Search the web for the latest official release notes."
    )


def test_verified_identical_artifact_rewrite_finishes_boundedly() -> None:
    assert _repeated_artifact_mutation_can_finish(
        ["write_file"],
        html_verified=True,
    )
    assert not _repeated_artifact_mutation_can_finish(
        ["write_file"],
        html_verified=False,
    )
    assert not _repeated_artifact_mutation_can_finish(
        ["send_email"],
        html_verified=True,
    )


def test_malformed_text_write_uses_one_bounded_body_handoff() -> None:
    assert _malformed_write_needs_body_handoff(
        {"write_file"},
        ["/workspace/output.html"],
        attempts=0,
    )
    assert not _malformed_write_needs_body_handoff(
        {"write_file"},
        ["/workspace/output.html"],
        attempts=1,
    )
    assert not _malformed_write_needs_body_handoff(
        {"write_file"},
        ["/workspace/output.png"],
        attempts=0,
    )


def test_post_finish_reinspection_without_correction_converges() -> None:
    args = dict(
        finish_nudge_sent=True,
        correction_seen=False,
        force_answer=False,
        verification_only=True,
        current_inspection=True,
        can_complete=True,
    )
    assert _post_finish_inspection_should_converge(**args)
    assert not _post_finish_inspection_should_converge(
        **{**args, "correction_seen": True}
    )
    assert not _post_finish_inspection_should_converge(
        **{**args, "verification_only": False}
    )


def test_explicit_notes_rule_takes_precedence_over_documents() -> None:
    prompt = _minimal_native_tool_prompt({"manage_notes", "manage_documents"})

    assert "When the user explicitly asks about a note or notes" in prompt
    assert "call `manage_notes`, not `manage_documents`" in prompt
    assert "An explicit note or notes request uses `manage_notes` instead" in prompt
    assert "`action: \"search\"`" in prompt
    assert "`action: \"view\"`" in prompt


def test_notes_rule_is_not_injected_when_notes_tool_is_unavailable() -> None:
    prompt = _minimal_native_tool_prompt({"manage_documents"})

    assert "## Notes tool rule" not in prompt
    assert "## Document tool rule" in prompt


def test_minimal_email_rule_prefers_topic_search_then_read() -> None:
    prompt = _minimal_native_tool_prompt({"search_emails", "read_email", "list_emails"})

    assert "## Email tool rule" in prompt
    assert "search_emails" in prompt
    assert "returned UID" in prompt
    assert "Use `list_emails` only for an explicit inbox/list/latest request" in prompt


def test_explicit_personal_note_request_is_notes_only() -> None:
    assert _looks_like_explicit_notes_only_turn(
        "Search my notes for Roast profile tweaks and open the note."
    )


def test_cross_domain_or_file_request_is_not_notes_only() -> None:
    assert not _looks_like_explicit_notes_only_turn(
        "Find the note and add a calendar reminder."
    )
    assert not _looks_like_explicit_notes_only_turn(
        "Edit the notes.py file and run its tests."
    )


def test_minimal_notes_clamp_suppresses_admin_schema_expansion() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "agent_loop.py").read_text()
    clamp = source[source.index("if _minimal_explicit_notes_mode"):]

    assert clamp.index("_needs_admin = False") < clamp.index(
        "if _minimal_explicit_notes_mode and route_tools is not None"
    )


def test_low_signal_direct_path_requires_no_tool_domains() -> None:
    args = dict(
        low_signal_turn=True, casual_low_signal_turn=True,
        ambiguous_short_turn=False, standalone_link_fragment_turn=False,
        existing_conversation=False, qwen38_tool_router=False,
        continuation=False, plan_mode=False, approved_plan=False,
        guide_only=False, active_document_relevant=False, active_email=None,
        workspace=None, forced_tools=False, relevant_tools=None,
        client_active_skills=False, terminal_agent_mode=False,
        has_tui_host_bridge=False,
    )

    assert _should_use_direct_low_signal_path(has_domains=False, **args)
    assert not _should_use_direct_low_signal_path(has_domains=True, **args)


def test_tui_bridge_does_not_block_low_signal_clarification_direct_path() -> None:
    args = dict(
        low_signal_turn=True, casual_low_signal_turn=False,
        ambiguous_short_turn=True, standalone_link_fragment_turn=False,
        existing_conversation=False, qwen38_tool_router=False,
        continuation=False, plan_mode=False, approved_plan=False,
        guide_only=False, active_document_relevant=False, active_email=None,
        workspace=None, has_domains=False, forced_tools=False,
        relevant_tools=None, client_active_skills=False,
        terminal_agent_mode=False, has_tui_host_bridge=True,
    )

    assert _should_use_direct_low_signal_path(**args)


def test_complete_fresh_domain_free_request_does_not_lose_agent_tools() -> None:
    """A natural request is not a fragment merely because routing found no keyword."""
    args = dict(
        low_signal_turn=True, casual_low_signal_turn=False,
        ambiguous_short_turn=False, standalone_link_fragment_turn=False,
        existing_conversation=False, qwen38_tool_router=False,
        continuation=False, plan_mode=False, approved_plan=False,
        guide_only=False, active_document_relevant=False, active_email=None,
        workspace=None, has_domains=False, forced_tools=False,
        relevant_tools=None, client_active_skills=False,
        terminal_agent_mode=False, has_tui_host_bridge=False,
    )

    assert not _should_use_direct_low_signal_path(**args)


def test_typo_heavy_product_problem_is_complete_not_ambiguous_fragment() -> None:
    text = "I have a miro 3 wiking by hwam and smoke isn't exiting properly its brand new"

    assert not _is_ambiguous_short_low_signal(text)


def test_product_review_request_routes_to_web_search_without_search_verb() -> None:
    from src.turn_contract import selected_tools_for_request

    assert selected_tools_for_request("What's Dyson pencil vac reviews") == frozenset({"web_search"})
    assert selected_tools_for_request("What are current reviews for Dyson pencil vac?") == frozenset({"web_search"})
    assert "web" in _classify_agent_request(
        [{"role": "user", "content": "What's Dyson pencil vac reviews"}],
        "What's Dyson pencil vac reviews",
    )["domains"]


def test_standalone_link_fragment_gets_clarification_path() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "agent_loop.py").read_text()
    assert "def _is_terse_link_request" in source
    assert "_standalone_link_fragment_turn = (" in source
    assert "and not _is_contextual_link_followup(messages, _last_user)" in source
    assert '"Which links do you mean? Tell me the topic or website list."' in source
    assert '"deterministic_clarification": True' in source


def test_contextual_link_followup_accepts_referential_typo_tail() -> None:
    messages = [
        {"role": "user", "content": "What are some good sites for public domain art?"},
        {
            "role": "assistant",
            "content": "Good sources include Wikimedia Commons, The Met Open Access, Rijksmuseum, and Smithsonian Open Access.",
        },
        {"role": "user", "content": "sned links for those"},
    ]

    assert _is_terse_link_request("sned links for those")
    assert _is_contextual_link_followup(messages, "sned links for those")
    assert "public domain art" in _contextual_link_followup_topic(messages, "sned links for those").lower()


def test_contextual_link_followup_accepts_bare_resource_reference_only_after_web_context() -> None:
    web_messages = [
        {"role": "user", "content": "What are some good sites for public domain art?"},
        {
            "role": "assistant",
            "content": "Useful public domain art sources include Wikimedia Commons, The Met Open Access, Rijksmuseum, and Smithsonian Open Access.",
        },
        {"role": "user", "content": "for the websites"},
    ]
    email_messages = [
        {"role": "user", "content": "what is my latest email?"},
        {"role": "assistant", "content": "Here is your latest email from Instagram."},
        {"role": "user", "content": "for the websites"},
    ]

    assert _is_terse_link_request("for the websites")
    assert _is_contextual_link_followup(web_messages, "for the websites")
    assert not _is_contextual_link_followup(email_messages, "for the websites")


def test_ambiguous_short_turn_clamps_retrieval_before_vector_search() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "agent_loop.py").read_text()
    assert '_relevant_tools = {"ask_user"}' in source
    assert "_ambiguous_short_turn" in source


def test_calendar_missing_date_guidance_uses_ask_user_not_guessing() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    agent_loop = (root / "src" / "agent_loop.py").read_text()
    tool_index = (root / "src" / "tool_index.py").read_text()
    schemas = (root / "src" / "tool_schemas.py").read_text()

    assert "do not guess a reservation/event date" in agent_loop
    assert "never invent a day for a reservation" in agent_loop
    assert "If create/update lacks a required date, time, or target event, call ask_user once" in tool_index
    assert "include an 'Exact date' option" in schemas


def test_qwen35_tool_router_uses_broad_compact_map() -> None:
    from src.agent_loop import _QWEN38_TOOL_ROUTER_PROMPT, _is_qwen38_tool_router

    assert _is_qwen38_tool_router("odysseus-qwen3.5-9b-tool-router-v4-q4")
    assert _is_qwen38_tool_router("odysseus-qwen3.5-tools-pre-heretic")
    assert _is_qwen38_tool_router("ajax_c375")
    assert _is_qwen38_tool_router("openai/ajax_c375")
    assert "manage_notes: notes/checklists" in _QWEN38_TOOL_ROUTER_PROMPT
    assert "mcp__email__list_emails" in _QWEN38_TOOL_ROUTER_PROMPT
    assert "mcp__email__search_emails" in _QWEN38_TOOL_ROUTER_PROMPT


def test_qwen_tool_router_preserves_explicit_artifact_output_budget() -> None:
    from src.agent_loop import (
        _allow_visual_tool_evidence_for_model,
        _malformed_native_tool_recovery_instruction,
        _qwen_tool_router_output_budget,
    )

    assert _qwen_tool_router_output_budget(8192) == 8192
    assert _qwen_tool_router_output_budget(None) == 1024
    assert _allow_visual_tool_evidence_for_model(
        "odysseus-qwen3.5-tools-pre-heretic"
    )
    assert _allow_visual_tool_evidence_for_model("ajax_c375")
    assert not _allow_visual_tool_evidence_for_model(
        "qwen35-9b-tool-router-v4-firstaction-noschema-adapter"
    )
    recovery = _malformed_native_tool_recovery_instruction({"write_file"})
    assert "both path and content" in recovery
    assert "loops" in recovery


def test_qwen_tool_router_replays_final_prose_after_buffering() -> None:
    from src.agent_loop import _should_emit_buffered_qwen_round

    assert _should_emit_buffered_qwen_round(
        odysseus_finetune=False,
        tool_router=True,
        has_tools=False,
        text="Finished answer",
    )
    assert not _should_emit_buffered_qwen_round(
        odysseus_finetune=False,
        tool_router=True,
        has_tools=True,
        text="I should call a tool",
    )
    assert not _should_emit_buffered_qwen_round(
        odysseus_finetune=False,
        tool_router=True,
        has_tools=False,
        text="Already streamed final answer",
        streamed_live=True,
    )


def test_qwen_agent_stream_releases_answer_after_orphan_think_close() -> None:
    from src.agent_loop import _incremental_qwen_visible_text

    assert _incremental_qwen_visible_text("The user wants a poem.") == ""
    assert _incremental_qwen_visible_text(
        "The user wants a poem.\n</think>\n\nHere"
    ) == "Here"
    assert _incremental_qwen_visible_text(
        "The user wants a poem.\n</think>\n\nHere is the poem"
    ) == "Here is the poem"


def test_qwen_agent_stream_releases_clean_answer_after_prefix_disambiguates() -> None:
    from src.agent_loop import _incremental_qwen_visible_text

    assert _incremental_qwen_visible_text("Th") == ""
    assert _incremental_qwen_visible_text("The waves") == "The waves"


def test_private_browser_product_catalog_requires_multiple_comparable_items() -> None:
    from src.agent_loop import _private_browser_product_catalog_ready

    catalog = """
    Showing results for "chair". We found 1455 products.
    StaticText "Price $ 15.00"
    button "Review: 4.5 out of 5 stars. Total reviews: (316)"
    StaticText "Price $ 199.00"
    button "Review: 4.7 out of 5 stars. Total reviews: (613)"
    """
    assert _private_browser_product_catalog_ready(catalog)
    assert not _private_browser_product_catalog_ready(
        'Showing results for "chair". StaticText "Price $ 15.00"'
    )


def test_interactive_browser_has_room_for_navigation_and_overlay_recovery() -> None:
    from src.clean_agent_preview import (
        INTERACTIVE_BROWSER_TOOL_CALL_LIMIT,
        INTERACTIVE_TOOL_CALL_LIMIT,
    )

    assert INTERACTIVE_BROWSER_TOOL_CALL_LIMIT > INTERACTIVE_TOOL_CALL_LIMIT
    assert INTERACTIVE_TOOL_CALL_LIMIT == 18
    assert INTERACTIVE_BROWSER_TOOL_CALL_LIMIT == 30


def test_email_account_transport_outage_is_not_treated_as_empty_inbox() -> None:
    from src.clean_agent_preview import email_account_backend_unavailable

    assert email_account_backend_unavailable({
        "stdout": (
            "[EMAIL ACCOUNT ERRORS: Primary: [Errno 111] Connection refused]\n"
            "No unread/unresponded emails found."
        ),
        "exit_code": 0,
    })
    assert not email_account_backend_unavailable({
        "stdout": "No unread/unresponded emails found.", "exit_code": 0,
    })


def test_private_browser_open_without_dom_refs_queues_snapshot() -> None:
    from src.agent_loop import _private_browser_open_needs_snapshot

    assert _private_browser_open_needs_snapshot("open", "Opened https://example.com")
    assert not _private_browser_open_needs_snapshot(
        "open", 'link "Shop" [ref=e12]'
    )
    assert not _private_browser_open_needs_snapshot("snapshot", "")


def test_private_browser_product_enter_needs_results_snapshot() -> None:
    from src.agent_loop import _private_browser_product_submit_needs_snapshot

    assert _private_browser_product_submit_needs_snapshot(
        "press", {"key": "Enter"}, "find the largest chair"
    )
    assert not _private_browser_product_submit_needs_snapshot(
        "press", {"key": "Escape"}, "find the largest chair"
    )
    assert not _private_browser_product_submit_needs_snapshot(
        "press", {"key": "Enter"}, "submit the contact form"
    )


def test_injected_untrusted_context_is_not_counted_as_a_prior_user_turn() -> None:
    from src.agent_loop import _user_turn_count
    from src.prompt_security import untrusted_context_message

    messages = [
        untrusted_context_message("saved memory: core", "Lives in Japan"),
        {"role": "user", "content": "Where is Sweden?"},
    ]

    assert _user_turn_count(messages) == 1


def test_orphan_qwen_think_closer_hides_analysis_prefix() -> None:
    from src.agent_loop import _visible_response_text

    raw = "The user asks a geography question. I should answer directly.\n</think>\n\nSweden is in Northern Europe."
    assert _visible_response_text(raw) == "Sweden is in Northern Europe."


def test_what_do_you_remember_is_an_explicit_memory_list_request() -> None:
    from src.agent_loop import _parse_explicit_memory_lookup_request

    block = _parse_explicit_memory_lookup_request("What do you remember about me?")
    assert block is not None
    assert block.tool_type == "manage_memory"
    assert block.content == "list"


def test_qwen_terminal_summary_handles_email_search_results() -> None:
    from src.agent_loop import _ody_qwen_terminal_tool_summary

    summary = _ody_qwen_terminal_tool_summary(
        {
            "tool": "mcp__email__search_emails",
            "command": '{"query":"Runpod","max_results":1}',
            "output": (
                'Found 1 email(s) matching "Runpod":\n\n'
                "1. **Your Runpod receipt [#1244-0851]**\n"
                "   From: Runpod (support@runpod.io)\n"
                "   Date: Thu, 20 Aug 2026 06:28:39 +0000\n"
                "   Folder: INBOX\n"
                "   UID: 91031\n"
                "   Source: cached index"
            ),
        }
    )

    assert "Here is your latest email:" in summary
    assert "Your Runpod receipt" in summary
    assert "UID 91031" in summary


def test_qwen35_tui_workspace_uses_coding_only_prompt() -> None:
    from src.agent_loop import _build_system_prompt

    prompt, _ = _build_system_prompt(
        [{"role": "user", "content": "Edit the parser and run its tests."}],
        "qwen35-9b-tool-router-v4-firstaction-noschema-adapter",
        None,
        None,
        workspace="/workspace/project",
        client_runtime_context={
            "surface": "odysseus-tui",
            "host_shell_bridge": {"url": "http://127.0.0.1:1/run", "token": "x"},
        },
    )

    content = prompt[0]["content"]
    assert "Odysseus TUI workspace tools" in content
    assert "old_string" in content
    assert "mcp__email__search_emails" not in content


def test_verified_tui_coding_summary_uses_tool_evidence() -> None:
    from src.agent_loop import _tui_verified_coding_summary

    summary = _tui_verified_coding_summary([
        {
            "tool": "edit_file",
            "command": json.dumps({"path": "src/parser.py"}),
            "exit_code": 0,
        },
        {
            "tool": "host_shell",
            "command": json.dumps({"command": "pytest -q"}),
            "exit_code": 1,
        },
        {
            "tool": "host_shell",
            "command": json.dumps({"command": "pytest -q"}),
            "exit_code": 0,
        },
    ])

    assert "`src/parser.py`" in summary
    assert "`pytest -q` failed" in summary
    assert "`pytest -q` passed" in summary


def test_partial_tui_mutation_failure_does_not_claim_no_change() -> None:
    from src.agent_loop import _tui_coding_failure_summary

    summary = _tui_coding_failure_summary([
        {
            "tool": "edit_file",
            "command": json.dumps({"path": "src/parser.py"}),
            "exit_code": 0,
        },
    ])

    assert "Changed:" in summary
    assert "`src/parser.py`" in summary
    assert "verification completed" in summary
    assert "No workspace change" not in summary


def test_qwen35_router_repairs_known_dropped_final_letter_artifacts() -> None:
    from src.agent_loop import _normalize_ody_qwen_text_artifacts

    assert _normalize_ody_qwen_text_artifacts(
        "I am Odysseus, an AI assistan."
    ) == "I am Odysseus, an AI assistant."


def test_qwen_tool_router_does_not_get_notes_prompt_override() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "agent_loop.py").read_text()
    block = source[
        source.index("qwen_tool_router_mode = _is_qwen38_tool_router(candidate_model)"):
        source.index("        _runtime_directive = _tui_runtime_directive")
    ]

    assert "doc_mode and not qwen_tool_router_mode" in block
    assert "notes_mode and not qwen_tool_router_mode" in block
    assert "and not qwen_tool_router_mode" in block


def test_qwen35_router_distinguishes_session_listing_from_chat_search() -> None:
    from src.agent_loop import _qwen38_router_tool_names

    assert _qwen38_router_tool_names("List my chat sessions.") == {"list_sessions"}
    assert _qwen38_router_tool_names("Search prior chats for the parser fix.") == {"search_chats"}
    assert "manage_contact" in _qwen38_router_tool_names("List my contacts.")
    assert "manage_research" in _qwen38_router_tool_names("List my saved research reports.")
    assert _qwen38_router_tool_names("List my tasks.") == {"manage_tasks"}


def test_qwen_task_locator_followup_helpers_are_narrow() -> None:
    from src.agent_loop import (
        _parse_qwen_task_mutation_request,
        _single_task_id_from_manage_tasks_list,
    )

    assert _parse_qwen_task_mutation_request("Pause the task named nightly check.") == "pause"
    assert _parse_qwen_task_mutation_request("Resume only that recurring task.") == "resume"
    assert _parse_qwen_task_mutation_request("Delete the disposable task.") == "delete"
    assert _parse_qwen_task_mutation_request("List my tasks.") == ""

    assert _single_task_id_from_manage_tasks_list(
        "Found 1 tasks:\n"
        "1. ODY-EVAL (ec8fd302-90f1-4e41-97fe-0eebedf27334) — active"
    ) == "ec8fd302-90f1-4e41-97fe-0eebedf27334"
    assert _single_task_id_from_manage_tasks_list(
        "Found 2 tasks:\n1. A (a)\n2. B (b)"
    ) == ""


def test_qwen35_router_maps_explicit_web_and_skill_intents() -> None:
    from src.agent_loop import _qwen38_router_tool_names

    assert _qwen38_router_tool_names("What is the latest Qwen release?") == {"web_search"}
    assert _qwen38_router_tool_names("What skills are available for this agent?") == {"manage_skills"}


def test_skill_listing_summary_is_bounded_and_user_facing() -> None:
    from src.agent_loop import _skills_list_summary_from_tool_output

    raw = "\n".join([
        "## Published",
        *[
            f"- **skill-{i}** (general): Very long description that should not be repeated in the compact summary"
            for i in range(10)
        ],
        "## Drafts",
        *[f"- **draft-{i}** [draft]: Draft description should also be removed" for i in range(3)],
    ])
    summary = _skills_list_summary_from_tool_output(raw)
    visible = summary.split("<!-- ody-more-skills:", 1)[0]
    assert summary.startswith("Available skills (10 published, 3 drafts):\n## Published")
    assert "- [skill-7](#skill-skill-7) (general)" in visible
    assert "- [skill-8]" not in visible
    assert "Very long description" not in summary
    assert "...and 5 more skills. Open Skills to browse all." in summary
    assert "<!-- ody-more-skills:" not in summary


def test_high_cardinality_list_summaries_are_bounded() -> None:
    from src.agent_loop import (
        _document_list_summary_from_tool_output,
        _memory_list_summary_from_tool_output,
        _ody_qwen_terminal_tool_summary,
        _registry_list_summary_from_tool_output,
        _research_list_summary_from_tool_output,
        _session_list_summary_from_tool_output,
    )

    docs = "\n".join(
        [
            "AI: Found 50 document(s), sorted most-recent first. Click a title to open:",
            *[
                f"- [Doc {i}](#document-id-{i}) - markdown, {i} chars, updated {i}m ago"
                for i in range(20)
            ],
        ]
    )
    doc_summary = _document_list_summary_from_tool_output(docs)
    assert "Doc 7" in doc_summary
    assert "Doc 8" not in doc_summary
    assert "...and 12 more" in doc_summary
    assert len(doc_summary) < 1200
    already_clipped_docs = (
        "Found 50 document(s), sorted most-recent first.\n"
        + "\n".join(f"- [Doc {i}](#document-id-{i})" for i in range(8))
        + "\n- ...and 42 more"
    )
    assert "...and 42 more" in _document_list_summary_from_tool_output(already_clipped_docs)
    assert "...and 1 more" not in _document_list_summary_from_tool_output(already_clipped_docs)

    research = "\n".join(
        [
            "Research library (13 items):",
            *[
                "- ["
                + ("Very long research title about tool routing and output shaping " * 4)
                + f"{i}](#research-rp-{i}) - id: rp-{i} - {i} sources"
                for i in range(13)
            ],
        ]
    )
    research_summary = _research_list_summary_from_tool_output(research)
    assert "rp-5" in research_summary
    assert "rp-6" not in research_summary
    assert "...and 7 more research reports" in research_summary
    assert len(research_summary) < 1200

    sessions = "\n".join(
        [
            'Found 101 session(s), sorted most-recent first:',
            "- **[\\[eval\\] sessions_list](#session-101dba88-1619-48f9-93ff-8c5bd4eeacd7)** "
            "(id: `101dba88-1619-48f9-93ff-8c5bd4eeacd7`, model: qwen, 1 msgs, last active just now)",
            *[
                f"- **[Chat {i}](#session-{i})** (id: `{i}`, model: qwen, 1 msgs, last active {i}m ago)"
                for i in range(20)
            ],
        ]
    )
    session_summary = _session_list_summary_from_tool_output(sessions)
    assert "\\[eval\\] sessions_list" in session_summary
    assert "Chat 10" in session_summary
    assert "Chat 11" not in session_summary
    assert "id:" not in session_summary
    assert "<!-- ody-more-sessions:" not in session_summary
    assert "...and more sessions (9 hidden)" in session_summary

    model_output = "\n".join(
        ["Available models (565 total):", "**OpenRouter** (openrouter):"]
        + [f"  - `model-{i}`" for i in range(80)]
    )
    model_summary = _ody_qwen_terminal_tool_summary({
        "tool": "list_models",
        "output": model_output,
        "command": "",
    })
    assert "`model-21`" in model_summary
    assert "`model-22`" not in model_summary
    assert "more models omitted" in model_summary
    assert len(model_summary) < 1200

    one_line_registry = "Result: " + ("private registry value " * 1000)
    registry_summary = _registry_list_summary_from_tool_output(one_line_registry)
    assert len(registry_summary) <= 3200

    memories = "\n".join([
        "Found 100 memory entries",
        *[f"- [preference] `m{i}` — preference {i}" for i in range(100)],
    ])
    memory_summary = _memory_list_summary_from_tool_output(memories)
    assert "preference 19" in memory_summary
    assert "preference 20" not in memory_summary
    assert "Open Memory to browse all" in memory_summary
    assert "ody-more-memories" not in memory_summary
    assert len(memory_summary) < 2000


def test_calendar_empty_result_is_user_facing_for_today() -> None:
    from src.agent_loop import (
        _looks_like_agent_reasoning_preamble,
        _ody_qwen_terminal_tool_summary,
    )

    event = {
        "tool": "manage_calendar",
        "command": json.dumps({
            "action": "list_events",
            "start": "2026-09-09",
            "end": "2026-09-10",
        }),
        "output": "AI: No events between 2026-09-09 and 2026-09-10.",
        "exit_code": 0,
        "events": [],
    }
    assert _ody_qwen_terminal_tool_summary(
        event, user_text="whats todays calendar"
    ) == "You have no events today."
    assert _looks_like_agent_reasoning_preamble(
        "The calendar query returned no events for today. I should answer directly."
    )


def test_directory_list_has_a_direct_terminal_summary() -> None:
    from src.agent_loop import _ody_qwen_terminal_tool_summary

    assert _ody_qwen_terminal_tool_summary({
        "tool": "ls",
        "command": '{"path":"/tmp"}',
        "output": "/tmp:\n  cache/\n  result.txt  (12 B)",
        "exit_code": 0,
    }) == "```text\n/tmp:\n  cache/\n  result.txt  (12 B)\n```"


def test_document_detail_helpers_identify_single_locator() -> None:
    from src.agent_loop import (
        _document_detail_requested,
        _document_read_summary_from_tool_output,
        _single_document_id_from_tool_output,
    )

    assert _document_detail_requested(
        "Find my document titled ODY-EVAL-TOOL-DOCUMENT-SEARCH and tell me its passphrase"
    )
    assert not _document_detail_requested("List my documents")
    one_doc = (
        "Found 1 document(s).\n"
        "- [ODY-EVAL-TOOL-DOCUMENT-SEARCH](#document-8fee6de7-12bb) — markdown"
    )
    two_docs = one_doc + "\n- [Other](#document-53e293de-fc48) — markdown"
    assert _single_document_id_from_tool_output(one_doc) == "8fee6de7-12bb"
    assert _single_document_id_from_tool_output(two_docs) == ""
    assert _document_read_summary_from_tool_output(
        "AI: document fixture passphrase: lapis-otter-419"
    ) == "document fixture passphrase: lapis-otter-419"


def test_calendar_detail_summary_preserves_description_when_requested() -> None:
    from src.agent_loop import (
        _calendar_detail_requested,
        _calendar_list_summary_from_tool_output,
    )

    raw = "\n".join(
        [
            "AI: Found 1 event(s) between 2026-08-21 and 2026-08-23:",
            "- 2026-08-22T09:00:00 -> 2026-08-22T10:00:00: "
            "[ODY-EVAL-TOOL-CALENDAR-SEARCH](#event-abc) #eval",
            "    calendar fixture passphrase: cobalt-sun-531",
        ]
    )
    assert _calendar_detail_requested("Find my calendar event and tell me its passphrase")
    assert "cobalt-sun-531" not in _calendar_list_summary_from_tool_output(raw)
    assert "cobalt-sun-531" in _calendar_list_summary_from_tool_output(raw, include_details=True)


def test_calendar_summary_is_readable_linked_and_expandable(monkeypatch) -> None:
    from src.agent_loop import _calendar_list_summary_from_tool_output
    from datetime import timezone
    import src.user_time

    monkeypatch.setattr(src.user_time, "user_timezone", lambda: timezone.utc)

    raw = "\n".join(
        [
            "AI: Found 3 event(s) between 2026-09-04 and 2026-09-06:",
            "- 2026-09-04T10:00:00Z -> 2026-09-04T11:00:00Z: "
            "[Dance Party](#event-dance) #social (Creator Ops)",
            "- 2026-09-05 (all day): [Travel day](#event-travel) #travel (Creator Ops)",
            "- 2026-09-06T09:30:00 -> 2026-09-06T10:30:00: "
            "[Breakfast](#event-breakfast) #meal (Creator Ops)",
        ]
    )
    summary = _calendar_list_summary_from_tool_output(raw, max_items=1)

    assert "[Dance Party](#event-dance) — Sep 4, 10:00 AM–11:00 AM" in summary
    assert "2026-09-04T10:00:00Z" not in summary
    assert "<!-- ody-more-events:" in summary
    assert "[...and 2 more events](#events-more-" in summary
    assert "[Travel day](#event-travel) — Sep 5 · All day" in summary


def test_calendar_synthesis_is_linkified_from_tool_events() -> None:
    from src.agent_loop import _linkify_calendar_titles_from_tool_events

    answer = "Sep 12\n- Dentist appointment, 1:00 PM\n- Date with Marzia @ Tokyo Tower, 9:30 PM"
    linked = _linkify_calendar_titles_from_tool_events(
        answer,
        [
            {
                "tool": "manage_calendar",
                "exit_code": 0,
                "events": [
                    {"uid": "evt-dentist", "summary": "Dentist appointment"},
                    {"uid": "evt-date", "summary": "Date with Marzia"},
                ],
            }
        ],
    )

    assert "[Dentist appointment](#event-evt-dentist)" in linked
    assert "[Date with Marzia](#event-evt-date)" in linked


def test_note_list_and_synthesis_are_linkified_from_tool_events() -> None:
    from src.agent_loop import (
        _linkify_note_titles_from_tool_events,
        _note_list_summary_from_tool_output,
    )

    raw = "- [note-123] **Japan packing list** [checklist] #travel\n- [note-456] **Book ideas**"
    summary = _note_list_summary_from_tool_output(raw)
    assert "☑️ [Japan packing list](#note-note-123) #travel" in summary
    assert "📝 [Book ideas](#note-note-456)" in summary
    assert "- ☑️" not in summary
    assert "- 📝" not in summary
    assert "[checklist]" not in summary
    assert "[note-123] Japan packing list" not in summary

    linked = _linkify_note_titles_from_tool_events(
        "You have Japan packing list and Book ideas in notes.",
        [{"tool": "manage_notes", "exit_code": 0, "output": raw}],
    )
    assert "[Japan packing list](#note-note-123)" in linked
    assert "[Book ideas](#note-note-456)" in linked


def test_note_list_truncation_stays_plain_text() -> None:
    from src.agent_loop import _note_list_summary_from_tool_output

    raw = "\n".join(f"- [note-{i}] **Note {i}**" for i in range(4))
    summary = _note_list_summary_from_tool_output(raw, max_items=2)

    assert "[...and 2 more notes](#notes-more-" in summary
    assert "<!-- ody-more-notes:" in summary
    assert "[Note 2](#note-note-2)" in summary
    assert "<details" not in summary
    assert "<summary" not in summary


def test_note_list_summary_unwraps_clean_executor_result_envelope() -> None:
    from src.agent_loop import _note_list_summary_from_tool_output

    raw = json.dumps({
        "results": "- [note-123] **Fixture note**",
        "exit_code": 0,
    })
    assert _note_list_summary_from_tool_output(raw) == (
        "Here are your notes (1):\n📝 [Fixture note](#note-note-123)"
    )


def test_recent_calendar_context_uses_structured_events_beyond_truncated_output() -> None:
    from src.agent_loop import _minimal_recent_notes_tool_context_message

    events = [
        {
            "uid": f"event-{idx}",
            "summary": f"Event {idx}",
            "dtstart": f"2026-08-{idx:02d}T09:00:00",
            "dtend": f"2026-08-{idx:02d}T09:30:00",
            "calendar": "Creator Ops",
        }
        for idx in range(1, 21)
    ]
    messages = [
        {"role": "user", "content": "open calendar"},
        {
            "role": "assistant",
            "content": "Done.",
            "metadata": {
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": '{"action":"list_events","start":"2026-08-01","end":"2026-09-01"}',
                        "output": "Found 20 event(s): " + "x" * 1200,
                        "events": events,
                        "context_only": True,
                    }
                ]
            },
        },
        {"role": "user", "content": "remove Event 20"},
    ]

    context = _minimal_recent_notes_tool_context_message(messages)

    assert context is not None
    text = context["content"]
    assert "event-20" in text
    assert "title=Event 20" in text


def test_calendar_open_panel_inherits_recent_list_range() -> None:
    from src.agent_loop import _inherit_calendar_open_range_from_tool_events

    result = {"ui_event": "open_panel", "panel": "calendar", "results": "Opening calendar panel"}
    tool_events = [
        {
            "tool": "manage_calendar",
            "command": '{"action":"list_events","start":"2026-09-01T00:00:00","end":"2026-10-01T00:00:00"}',
            "output": "Found September events",
        }
    ]

    _inherit_calendar_open_range_from_tool_events(result, tool_events)

    assert result["view"] == "month"
    assert result["target_date"] == "2026-09-01"


def test_manage_calendar_list_events_includes_reminder_metadata() -> None:
    import asyncio
    import json
    import uuid

    from src.tools.calendar import do_manage_calendar

    owner = f"calendar-reminder-fixture-{uuid.uuid4()}"
    title = f"Reminder Fixture {uuid.uuid4()}"
    created = asyncio.run(do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": title,
        "dtstart": "2035-01-02T10:00:00",
        "dtend": "2035-01-02T10:30:00",
        "reminder_minutes": 15,
    }), owner=owner))
    assert created.get("exit_code") == 0, created

    listed = asyncio.run(do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2035-01-02T00:00:00",
        "end": "2035-01-03T00:00:00",
    }), owner=owner))

    assert listed.get("exit_code") == 0, listed
    event = next(ev for ev in listed["events"] if ev["summary"] == title)
    assert event["has_reminder"] is True
    assert event["reminder_minutes"] == 15
    assert event["reminder_note_id"]


def test_late_tool_summary_fallback_preserves_synthesized_answers() -> None:
    src = Path(__file__).resolve().parent.parent.joinpath("src", "agent_loop.py").read_text(encoding="utf-8")

    assert "_visible_response_text(full_response)" in src
    assert "and _tool_name in {" in src
    assert '"manage_calendar",' in src
    assert '"manage_documents",' in src
    assert '"mcp__email__read_email",' in src
    assert "if _tool_name == \"manage_calendar\" and _tool_action in {\"list\", \"list_events\"}:" in src
    assert "if _visible_response_text(full_response):\n                    break" in src


def test_read_only_shell_is_a_canonical_terminal_renderer() -> None:
    from src.agent_loop import _ody_qwen_terminal_tool_summary

    assert _ody_qwen_terminal_tool_summary({
        "tool": "bash",
        "command": '{"command":"pwd"}',
        "output": "/workspace",
    }) == "```text\n/workspace\n```"


def test_calendar_list_relative_range_args_become_iso_dates() -> None:
    from src.agent_loop import _normalize_calendar_list_range_args

    normalized, changed = _normalize_calendar_list_range_args(
        {"action": "list_events", "start": "next week"},
        today="2026-08-20",
    )

    assert changed
    assert normalized == {
        "action": "list_events",
        "start": "2026-08-24",
        "end": "2026-08-31",
    }


def test_broad_calendar_list_discards_model_invented_range_and_query() -> None:
    from src.agent_loop import _normalize_calendar_list_range_args

    normalized, changed = _normalize_calendar_list_range_args(
        {
            "action": "list_events",
            "start": "2026-09-11T15:00:00Z",
            "end": "2026-09-11T18:00:00Z",
            "query": "Today's schedule: 15:30 Meeting, 16:00 Call",
        },
        today="2026-09-11",
        user_text="whats on my calendar? just three titles and times",
    )

    assert changed
    assert normalized == {
        "action": "list_events",
        "start": "2026-09-11",
        "end": "2026-10-11",
    }


def test_calendar_create_strips_accidental_utc_suffix_for_local_wall_time() -> None:
    from src.agent_loop import _normalize_calendar_create_relative_args

    normalized, changed = _normalize_calendar_create_relative_args(
        {
            "action": "create_event",
            "summary": "Take out trash",
            "dtstart": "2026-08-31T08:00:00Z",
            "dtend": "2026-08-31T08:30:00Z",
        },
        "add recurring event every Monday 8am take out trash",
    )

    assert changed
    assert normalized["dtstart"] == "2026-08-31T08:00:00"
    assert normalized["dtend"] == "2026-08-31T08:30:00"


def test_recent_persisted_calendar_anchor_is_not_treated_as_fabricated() -> None:
    from types import SimpleNamespace
    from src.agent_loop import _calendar_anchor_was_already_persisted

    uid = "484cca72-5b50-4ded-a6d2-c5ce8632da55"
    session = SimpleNamespace(history=[
        {"role": "assistant", "content": f"[Workout](#event-{uid}) — Sep 10, 1:00 AM"},
    ])
    assert _calendar_anchor_was_already_persisted(
        f"The second event is [Workout](#event-{uid}).", session,
    )
    assert not _calendar_anchor_was_already_persisted(
        "[Invented](#event-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)", session,
    )


def test_calendar_ordinal_weekday_month_rrule_normalizes() -> None:
    from src.agent_loop import (
        _calendar_ordinal_week_ask_user_block,
        _normalize_calendar_ordinal_weekday_rrule,
    )

    normalized, changed = _normalize_calendar_ordinal_weekday_rrule(
        {
            "action": "create_event",
            "summary": "Take out unburnable trash",
            "dtstart": "2026-08-31T08:00:00",
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
        },
        "create trash event every first Monday of the month and last Monday of the month at 8am",
    )

    assert changed
    assert normalized["rrule"] == "FREQ=MONTHLY;BYDAY=1MO,-1MO"
    assert _calendar_ordinal_week_ask_user_block(
        "create an event for the first monday of the week and the last monday of the week take out trash"
    ) is not None

    normalized, changed = _normalize_calendar_ordinal_weekday_rrule(
        {
            "action": "create_event",
            "summary": "Pay rent",
            "dtstart": "2026-09-10T08:00:00",
            "rrule": "FREQ=WEEKLY;BYDAY=TH",
        },
        "add pay rent every 2nd Thursday of the month at 8am",
    )
    assert changed
    assert normalized["rrule"] == "FREQ=MONTHLY;BYDAY=2TH"

    normalized, changed = _normalize_calendar_ordinal_weekday_rrule(
        {
            "action": "create_event",
            "summary": "Water plants",
            "dtstart": "2026-09-27T08:00:00",
        },
        "schedule water plants every last Sunday of the month at 8am",
    )
    assert changed
    assert normalized["rrule"] == "FREQ=MONTHLY;BYDAY=-1SU"


def test_calendar_create_keeps_explicit_utc_suffix_when_user_asked_for_utc() -> None:
    from src.agent_loop import _normalize_calendar_create_relative_args

    args = {
        "action": "create_event",
        "summary": "Ops check",
        "dtstart": "2026-08-31T08:00:00Z",
    }
    normalized, changed = _normalize_calendar_create_relative_args(
        args,
        "add ops check at 8am UTC",
    )

    assert changed is False
    assert normalized is args


def test_contextual_calendar_delete_request_parser() -> None:
    from src.agent_loop import _contextual_calendar_action_request

    assert _contextual_calendar_action_request("delete that actually") == "delete_event"
    assert _contextual_calendar_action_request("cancel it") == "delete_event"
    assert _contextual_calendar_action_request("what about that") == ""


def test_explicit_skill_mutation_is_structured_for_qwen_router() -> None:
    from src.agent_loop import _parse_explicit_skill_request

    add = _parse_explicit_skill_request(
        "Add one disposable draft skill named parser-fixture with description "
        "'temporary fixture', procedure ['do nothing'], verification ['confirm fixture'], status draft."
    )
    assert add is not None
    assert add["action"] == "add"
    assert add["name"] == "parser-fixture"
    assert add["description"] == "temporary fixture"
    assert add["procedure"] == ["do nothing"]
    assert add["verification"] == ["confirm fixture"]
    assert add["status"] == "draft"
    assert _parse_explicit_skill_request(
        "Delete only the disposable skill named parser-fixture."
    ) == {"action": "delete", "name": "parser-fixture"}
    assert _parse_explicit_skill_request(
        "Verify that disposable skill parser-fixture is absent by searching/listing skills."
    ) == {"action": "search", "query": "parser-fixture"}
    assert _parse_explicit_skill_request(
        "Use the TDD skill for this task."
    ) is None
    assert _parse_explicit_skill_request("show my skills") == {"action": "list"}
    assert _parse_explicit_skill_request("what are my skills?") == {"action": "list"}


def test_qwen_explicit_crud_create_requests_keep_required_arguments() -> None:
    from src.agent_loop import _parse_qwen_explicit_create_request

    tool, args = _parse_qwen_explicit_create_request(
        "Create a temporary normal note titled Fixture-123 with content 'temporary fixture'."
    )
    assert tool == "manage_notes"
    assert json.loads(args) == {
        "action": "add", "title": "Fixture-123", "content": "temporary fixture"
    }

    tool, args = _parse_qwen_explicit_create_request(
        "Create one temporary calendar event titled Fixture-123 on 2030-01-02 "
        "from 10:00 to 11:00, description 'temporary fixture'."
    )
    assert tool == "manage_calendar"
    assert json.loads(args)["dtstart"] == "2030-01-02T10:00"
    assert json.loads(args)["dtend"] == "2030-01-02T11:00"

    tool, args = _parse_qwen_explicit_create_request(
        "add event next week monday, go to school"
    )
    assert tool == "manage_calendar"
    assert json.loads(args)["action"] == "create_event"
    assert json.loads(args)["summary"] == "go to school"
    assert json.loads(args)["all_day"] is True

    tool, args = _parse_qwen_explicit_create_request(
        "Add one temporary saved memory with exact marker Fixture-123 and text "
        "'temporary fixture'; category fact."
    )
    assert tool == "manage_memory"
    assert args == "add\nFixture-123: temporary fixture\nfact"

    tool, args = _parse_qwen_explicit_create_request(
        "Create a temporary editor document titled Fixture-123 with exactly this short content: temporary fixture."
    )
    assert tool == "create_document"
    assert args == "Fixture-123\nmarkdown\ntemporary fixture"

    tool, args = _parse_qwen_explicit_create_request(
        "Add one temporary fake contact named Fixture-123, email fixture-123@invalid.example, phone +1-202-555-0199."
    )
    assert tool == "manage_contact"
    assert json.loads(args)["email"] == "fixture-123@invalid.example"


def test_ask_user_time_reply_inherits_calendar_context() -> None:
    messages = [
        {"role": "user", "content": "Add meeting with Sion tomorrow"},
        {
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_events": [
                    {
                        "tool": "ask_user",
                        "command": json.dumps({
                            "question": "What time tomorrow should I schedule the meeting with Sion?",
                            "options": [
                                {"label": "10:00 AM"},
                                {"label": "1:00 PM"},
                            ],
                        }),
                        "ask_user": {
                            "question": "What time tomorrow should I schedule the meeting with Sion?",
                            "options": [
                                {"label": "10:00 AM"},
                                {"label": "1:00 PM"},
                            ],
                            "multi": False,
                        },
                    }
                ]
            },
        },
        {"role": "user", "content": "1:00 PM"},
    ]

    intent = _classify_agent_request(messages, "1:00 PM")

    assert intent["continuation"] is True
    assert intent["low_signal"] is False
    assert "notes_calendar_tasks" in intent["domains"]
    assert "meeting with Sion" in intent["retrieval_query"]
    assert "1:00 PM" in intent["retrieval_query"]


def test_ask_user_date_reply_inherits_calendar_context() -> None:
    messages = [
        {
            "role": "user",
            "content": "event for next month dinner at skytree tokyo 9:30 reservation id 59i2323 remind me day before",
        },
        {
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_events": [
                    {
                        "tool": "ask_user",
                        "command": json.dumps({
                            "question": "Which date in September 2026 is the Skytree Tokyo dinner reservation?",
                            "options": [
                                {"label": "Exact date"},
                                {"label": "Cancel"},
                            ],
                        }),
                        "ask_user": {
                            "question": "Which date in September 2026 is the Skytree Tokyo dinner reservation?",
                            "options": [
                                {"label": "Exact date"},
                                {"label": "Cancel"},
                            ],
                            "multi": False,
                        },
                    }
                ]
            },
        },
        {"role": "user", "content": "September 14"},
    ]

    intent = _classify_agent_request(messages, "September 14")

    assert intent["continuation"] is True
    assert intent["low_signal"] is False
    assert "notes_calendar_tasks" in intent["domains"]
    assert "skytree tokyo" in intent["retrieval_query"].lower()
    assert "September 14" in intent["retrieval_query"]


def test_minimal_notes_context_preserves_ask_user_question() -> None:
    messages = [
        {"role": "user", "content": "Add meeting with Sion tomorrow"},
        {
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_events": [
                    {
                        "tool": "ask_user",
                        "command": '{"question":"What time tomorrow for meeting with Sion?"}',
                        "output": "Awaiting user selection.",
                        "ask_user": {
                            "question": "What time tomorrow for meeting with Sion?",
                            "options": [{"label": "1:00 PM"}, {"label": "3:00 PM"}],
                        },
                    }
                ]
            },
        },
        {"role": "user", "content": "1:00 PM"},
    ]

    context = _minimal_recent_notes_tool_context_message(messages)

    assert context is not None
    assert "[ask_user]" in context["content"]
    assert "meeting with Sion" in context["content"]
    assert "Awaiting user selection" in context["content"]


def test_qwen_explicit_note_delete_extracts_exact_title() -> None:
    from src.agent_loop import _parse_qwen_explicit_note_delete

    assert _parse_qwen_explicit_note_delete(
        "Delete the exact temporary note titled Fixture-123. Search for it first if needed."
    ) == "Fixture-123"
    assert _parse_qwen_explicit_note_delete("Delete the old thing") is None

    from src.agent_loop import (
        _parse_qwen_explicit_calendar_absence_verify,
        _parse_qwen_explicit_calendar_delete,
    )

    assert _parse_qwen_explicit_calendar_delete(
        "Delete only the temporary calendar event titled Fixture-123."
    ) == "Fixture-123"
    assert _parse_qwen_explicit_calendar_delete(
        "Delete the second event from that list."
    ) is None
    assert _parse_qwen_explicit_calendar_absence_verify(
        "Verify that calendar event Fixture-123 is absent. Search the 2030-01-02 range; do not create anything."
    ) == {
        "action": "list_events",
        "start": "2030-01-02",
        "end": "2030-01-03",
        "query": "Fixture-123",
    }
    assert _parse_qwen_explicit_calendar_delete(
        "Verify that calendar event Fixture-123 is absent. Search the 2030-01-02 range; do not create anything."
    ) is None

    from src.agent_loop import _parse_qwen_explicit_memory_search

    assert _parse_qwen_explicit_memory_search(
        "Search saved memory for the exact marker Fixture-123."
    ) == "Fixture-123"
    assert _parse_qwen_explicit_memory_search(
        "Delete the memory with exact marker Fixture-123; search first."
    ) is None
    from src.agent_loop import _parse_qwen_explicit_memory_delete

    assert _parse_qwen_explicit_memory_delete(
        "Delete only the temporary memory containing exact marker Fixture-123."
    ) == "Fixture-123"

    from src.agent_loop import _parse_qwen_explicit_document_request

    tool, args = _parse_qwen_explicit_document_request(
        "Edit the active document Fixture-123: replace 'temporary fixture' with 'updated fixture'."
    )
    assert tool == "edit_document"
    assert "<<<FIND>>>\ntemporary fixture" in args
    assert "<<<REPLACE>>>\nupdated fixture" in args
    assert _parse_qwen_explicit_document_request(
        "Delete only the editor document titled Fixture-123."
    ) == ("manage_documents", '{"action": "delete"}')

    from src.agent_loop import _parse_qwen_explicit_contact_request

    tool, args = _parse_qwen_explicit_contact_request(
        "Update the exact contact named Fixture-123; change the phone to +1-202-555-0188."
    )
    assert tool == "manage_contact"
    assert json.loads(args) == {
        "action": "update", "name": "Fixture-123", "phone": "+1-202-555-0188"
    }


def test_recent_odysseus_anchor_refs_reads_persisted_history() -> None:
    from core.models import ChatMessage, Session
    from src.agent_loop import _recent_odysseus_anchor_refs

    history_session = Session(
        id="s1",
        name="eval",
        endpoint_url="http://example.invalid/v1",
        model="fixture-model",
        history=[
            ChatMessage(
                "assistant",
                "Created event [Fixture](#event-11111111-2222-3333-4444-555555555555)",
                metadata={
                    "tool_events": [
                        {
                            "tool": "manage_notes",
                            "output": "AI: Note created: \"Fixture\" (id: abc12345)",
                        }
                    ]
                },
            ),
            ChatMessage(
                "assistant",
                "[View note: Fixture](#note-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)",
            ),
        ],
    )

    refs = _recent_odysseus_anchor_refs(
        [{"role": "user", "content": "Update that note."}],
        history_session,
    )

    assert refs == {
        "note_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "event_uid": "11111111-2222-3333-4444-555555555555",
        "event_title": "Fixture",
    }
