import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.agent_loop as al
from src.agent_tools import ToolBlock
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
from src.tool_policy import (
    WEB_ACCESS_TOOL_NAMES,
    WEB_TOOL_NAMES,
    build_effective_tool_policy,
    detect_guide_only_turn,
    web_search_enabled_for_turn,
)
from src.turn_contract import requested_capabilities
from tests.runtime_evidence_helpers import server_authorized_executor

execute_tool_block = server_authorized_executor(execute_tool_block)


def _collect(gen):
    async def _run():
        return [c async for c in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                out.append(json.loads(chunk[6:]))
            except Exception:
                pass
    return out


def _delta_chunk(text):
    return "data: " + json.dumps({"delta": text}) + "\n\n"


def _patch_loop_basics(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)


def test_detects_strong_guide_only_turns():
    assert detect_guide_only_turn("GUIDE-ONLY MODE. DO NOT USE TOOLS.")
    assert detect_guide_only_turn("NO-TOOLS MODE.")
    assert detect_guide_only_turn("Ask me before using tools.")
    assert detect_guide_only_turn("You are not allowed to:\n- use tools\n- execute commands")


def test_does_not_treat_ordinary_guidance_as_no_tools():
    assert detect_guide_only_turn("Can you guide me through fixing this bug?") is None
    assert detect_guide_only_turn("I have no tools installed in this project.") is None
    assert detect_guide_only_turn("Write the script in the repo; I'll run it locally.") is None
    assert detect_guide_only_turn("Do not run commands that write files; inspect the repo first.") is None
    assert detect_guide_only_turn("Don't execute shell commands unless I approve them.") is None


def test_explicit_private_browser_visible_text_find_is_normalized():
    tool, content = al._parse_explicit_private_browser_inspection(
        "Use the private browser to find the visible text 'Learn more' on the open page."
    )

    assert tool == "private_browser"
    assert json.loads(content) == {"action": "find", "find": "Learn more"}


def test_explicit_private_browser_evaluate_is_normalized():
    tool, content = al._parse_explicit_private_browser_inspection(
        "Use the private browser to evaluate document.location.hostname and report it."
    )

    assert tool == "private_browser"
    assert json.loads(content) == {
        "action": "evaluate",
        "script": "document.location.hostname",
    }


def test_reviewed_unsubscribe_url_starts_in_private_browser():
    tool, content = al._parse_explicit_private_browser_inspection(
        "The user chose Agent Unsubscribe. Use the private_browser tool for this exact unsubscribe URL: "
        "https://example.test/unsubscribe?token=abc123&type=email"
    )

    assert tool == "private_browser"
    assert json.loads(content) == {
        "action": "open",
        "url": "https://example.test/unsubscribe?token=abc123&type=email",
    }


def test_explicit_private_browser_url_open_is_normalized_without_web_search():
    prompt = (
        "Open https://www.ikea.com in the private browser and tell me the page title."
    )
    tool, content = al._parse_explicit_private_browser_inspection(prompt)
    assert tool == "private_browser"
    assert json.loads(content) == {
        "action": "open",
        "url": "https://www.ikea.com",
    }
    assert not al._web_search_unavailable_for_turn(
        {"web"}, {"web_search", "web_fetch"}, prompt, None, None
    )


def test_natural_browser_request_is_not_blocked_by_web_search_toggle():
    prompt = "Browse example.com and inspect its product page."
    assert al._looks_like_explicit_browser_interaction(prompt)
    assert not al._web_search_unavailable_for_turn(
        {"web"}, {"web_search", "web_fetch"}, prompt, None, None
    )


def test_exact_url_fetch_only_contract_is_not_reported_as_web_disabled():
    assert not al._web_search_unavailable_for_turn(
        {"web"}, {"web_search"},
        "Summarize https://example.com/report", None, None,
    )


def test_unsubscribe_url_token_does_not_trigger_token_listing():
    assert al._parse_qwen_explicit_admin_request(
        "Use private_browser to open https://example.test/unsubscribe?token=abc123"
    ) is None


def test_generic_find_language_is_not_forced_into_private_browser():
    assert al._parse_explicit_private_browser_inspection(
        "Find the visible text in my document."
    ) is None


def test_explicit_teacher_request_uses_teacher_tool():
    assert al._parse_explicit_teacher_request(
        "Ask the teacher model to review this answer for persisted tool evidence."
    ) == (
        "ask_teacher",
        "auto\nreview this answer for persisted tool evidence",
    )


def test_named_model_request_is_not_forced_to_teacher():
    assert al._parse_explicit_teacher_request(
        "Ask qwen/qwen3.8-flash to review this answer."
    ) is None


def test_finish_plan_is_an_explicit_plan_request():
    assert al._looks_like_explicit_plan_request(
        "Finish the plan by marking output verification complete."
    )


def test_guide_only_policy_blocks_and_hides_tools():
    policy = build_effective_tool_policy(
        disabled_tools={"web_search"},
        last_user_message="GUIDE-ONLY MODE. DO NOT USE TOOLS.",
    )
    assert policy.mode == "guide_only"
    assert policy.disable_mcp is True
    assert policy.block_all_tool_calls is True
    for tool in ("bash", "python", "web_search", "read_file"):
        assert tool in policy.disabled_tools
        assert tool in policy.hidden_tools
        assert policy.blocks(tool)


def test_normal_policy_preserves_existing_disabled_tools():
    policy = build_effective_tool_policy(
        disabled_tools={"web_search"},
        last_user_message="Please check this normally.",
    )
    assert policy.mode == "normal"
    assert policy.blocks("web_search")
    assert not policy.blocks("bash")


def test_web_search_enabled_for_turn_requires_explicit_enable():
    assert web_search_enabled_for_turn(None, None) is False
    assert web_search_enabled_for_turn("true", None) is True
    assert web_search_enabled_for_turn(None, "true") is True
    assert web_search_enabled_for_turn(True, None) is True
    assert web_search_enabled_for_turn("false", "true") is False
    assert web_search_enabled_for_turn(False, "true") is False


def test_sft_workspace_clamp_does_not_strip_private_web_tools():
    tools = {"bash", "read_file", *WEB_TOOL_NAMES, "ask_user", "update_plan", "ask_teacher"}

    stripped = al._strip_workspace_tools_for_sft(tools, "sft_alex_creator")

    assert WEB_TOOL_NAMES <= stripped
    assert {"ask_user", "update_plan", "ask_teacher"} <= stripped
    assert "bash" in stripped
    assert "read_file" not in stripped


def test_sft_web_session_keeps_workspace_clamp_but_native_terminal_is_exempt():
    assert al._workspace_tools_disabled_for_request(
        "sft_alex_creator", {"surface": "web"}
    )
    assert not al._workspace_tools_disabled_for_request(
        "sft_alex_creator",
        {"surface": "odysseus-native", "terminal_agent": True},
    )
    assert not al._workspace_tools_disabled_for_request(
        "pewds", {"surface": "web"}
    )


def test_compact_prompt_says_current_turn_tools_override_stale_history():
    prompt = al._assemble_prompt({"web_search", "ask_user"}, set(), compact=True)

    assert "Tool availability is turn-local" in prompt
    assert "web_search" in prompt


def test_web_prompt_distinguishes_announcement_from_availability():
    prompt = al._assemble_prompt({"web_search", "web_fetch", "ask_user"}, set(), compact=True)

    assert "distinguish announcement date from release/ship/availability date" in prompt
    assert "announced future product" in prompt


def test_skill_parser_ignores_ambiguous_followup_connectors():
    assert al._parse_explicit_skill_request("Open the most relevant skill from that search.") is None
    assert al._parse_explicit_skill_request("Open the skill from that search.") is None
    assert al._parse_explicit_skill_request("Delete the audit-fixture-demo skill now") == {
        "action": "delete",
        "name": "audit-fixture-demo",
    }


def test_followup_content_extracts_active_document_append_sentence():
    assert (
        al._extract_followup_content_update(
            "Append this sentence to the open document: Tool calls must persist after refresh."
        )
        == "Tool calls must persist after refresh"
    )


def test_memory_marker_lookup_strips_sentence_punctuation():
    block = al._parse_explicit_memory_lookup_request(
        "Find the memory you just saved about marker 20260828_192332-cd9c8407-memory-a."
    )

    assert block is not None
    assert block.tool_type == "manage_memory"
    assert block.content == "search\n20260828_192332-cd9c8407-memory-a"


def test_ody_qwen_text_artifacts_collapse_duplicate_done():
    assert al._normalize_ody_qwen_text_artifacts("Done.Done.") == "Done."
    assert al._normalize_ody_qwen_text_artifacts("Done. Done.") == "Done."


def test_qwen_leaked_tool_text_detects_plain_web_tool_prefix():
    assert al._looks_like_ody_qwen_leaked_tool_text(
        "I need another result.\nweb_search: IKEA official website"
    )


def test_web_search_lookup_trusts_topical_model_query():
    block = al._normalize_web_search_block_query(
        ToolBlock("web_search", '"What\'s Come Over You" song'),
        "Look this up and answer with 2 source links: who sang what in the world's come over you?",
    )

    assert block.content == '"What\'s Come Over You" song'


def test_contextual_web_followup_trusts_topical_model_query():
    block = al._normalize_web_search_block_query(
        ToolBlock("web_search", "How much vram or unified memory will be available"),
        "Search for current Mac chip for ai How much vram or unified memory will be available",
    )

    assert "current mac chip ai" in block.content
    assert "How much vram or unified memory will be available" in block.content
    assert "vram" in block.content.lower()


def test_contextual_browser_discovery_cannot_drop_prior_subject():
    block = al._contextual_browser_opens_to_web_search(
        ToolBlock(
            "private_browser",
            json.dumps({
                "action": "batch",
                "commands": [
                    ["open", "https://example.com/grilled-cheese-recipe"],
                    ["open", "https://example.org/best-grilled-cheese"],
                ],
            }),
        ),
        (
            "use google maps and find closest coffee shop in todoroki "
            "which one has grilled cheese sandwich on the menu?"
        ),
        "which one has grilled cheese sandwich on the menu?",
    )

    assert block.tool_type == "web_search"
    query = json.loads(block.content)["query"].lower()
    assert "todoroki" in query
    assert "coffee" in query
    assert "grilled cheese" in query


def test_contextual_browser_current_page_interaction_is_preserved():
    block = ToolBlock(
        "private_browser",
        json.dumps({"action": "click", "target": "@e14"}),
    )

    assert al._contextual_browser_opens_to_web_search(
        block,
        "browse coffee shops in todoroki open the second result",
        "open the second result",
    ) == block


def test_contextual_browser_open_is_not_rewritten_to_unoffered_web_search():
    block = ToolBlock(
        "private_browser",
        json.dumps({
            "action": "open",
            "url": "http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html",
        }),
    )

    assert al._contextual_browser_opens_to_web_search(
        block,
        "find orange sofas on the catalog page",
        "try again on that page and compare the prices",
        allow_web_search=False,
    ) == block


def test_contextual_web_followup_matrix_restores_missing_subject_anchor():
    cases = [
        (
            "Where is statistically better to live Sweden or Japan",
            "what about schools",
            ("sweden", "japan", "schools"),
        ),
        (
            "Compare Sweden Switzerland and Japan for family quality of life",
            "nursery school levels",
            ("sweden", "switzerland", "japan", "nursery"),
        ),
        (
            "Search for current Mac chip for ai",
            "release date",
            ("current", "mac", "chip", "release"),
        ),
        (
            "Search for current Mac chip for ai",
            "how much unified memory is available",
            ("current", "mac", "chip", "memory"),
        ),
        (
            "Find current RTX 5090 laptop availability",
            "what about pricing",
            ("rtx", "5090", "laptop", "pricing"),
        ),
    ]
    for prior_topic, model_query, expected_terms in cases:
        block = al._normalize_web_search_block_query(
            ToolBlock("web_search", model_query),
            prior_topic,
        )
        normalized = block.content.lower()
        for term in expected_terms:
            assert term in normalized, (prior_topic, model_query, block.content)
        assert "what about" not in normalized
        assert not normalized.startswith("where is statistically")


def test_web_followup_context_directive_includes_prior_answer_context():
    messages = [
        {"role": "user", "content": "Where is statistically better to live, Sweden or Japan?"},
        {
            "role": "assistant",
            "content": (
                "Sweden looked stronger for childcare and family support. "
                "Japan looked stronger on safety and transit. Evidence was mixed for schools."
            ),
            "metadata": {"tool_events": [{"tool": "web_search", "command": "Sweden Japan quality of life"}]},
        },
        {"role": "user", "content": "what about schools?"},
    ]
    topic = al._contextual_public_web_topic_text(messages, "what about schools?", force=True)
    directive = al._web_followup_context_directive(messages, "what about schools?", topic)

    assert "Original user goal: Where is statistically better to live, Sweden or Japan?" in directive
    assert "Prior answer context: Sweden looked stronger for childcare" in directive
    assert "Current follow-up: what about schools?" in directive
    assert "Do not search the literal follow-up alone" in directive


def test_generic_search_followup_reuses_prior_question_without_shell_words():
    messages = [
        {"role": "user", "content": "What year did Ethiopia become independent"},
        {
            "role": "assistant",
            "content": "Ethiopia retained independence apart from an Italian occupation.",
        },
        {"role": "user", "content": "Can you search"},
    ]

    topic = al._contextual_public_web_topic_text(
        messages,
        "Can you search",
        force=True,
    )

    assert topic == "What year did Ethiopia become independent"


def test_generic_search_followup_uses_clean_assistant_topic_as_fallback():
    messages = [
        {"role": "assistant", "content": "The Aurora launch was reported for September 2026."},
        {"role": "user", "content": "Look it up"},
    ]

    topic = al._web_search_assistant_context_text(messages, "Look it up")

    assert topic == "The Aurora launch was reported for September 2026."


def test_youtube_pronoun_followup_inherits_prior_channel_topic():
    messages = [
        {"role": "user", "content": "does anthropic have a youtube channel"},
        {
            "role": "assistant",
            "content": (
                "Yes -- Anthropic has an official YouTube channel at "
                "youtube.com/@anthropic-ai. They also run a separate product "
                "channel for Claude at youtube.com/@claude."
            ),
            "metadata": {
                "tool_events": [
                    {"tool": "web_search", "command": "Anthropic YouTube channel"}
                ]
            },
        },
        {"role": "user", "content": "whats their latest video"},
    ]

    topic = al._contextual_public_web_topic_text(
        messages,
        "whats their latest video",
        force=True,
    )

    assert "does anthropic have a youtube channel" in topic.lower()
    assert "latest video" in topic.lower()
    assert al._looks_like_contextual_public_web_followup(
        "whats their latest video",
        topic,
    )


def test_web_query_source_preference_removes_if_possible_filler():
    query = al._web_search_query_from_user_text(
        "What is the latest unemployment rate in the US? Use BLS if possible"
    )

    assert query == "What is the latest unemployment rate in the US BLS"


def test_product_spec_queries_leave_topical_model_query_alone():
    block = al._normalize_web_search_block_query(
        ToolBlock("web_search", "current laptop gpu memory price"),
        "current laptop gpu memory price",
    )

    assert block.content == "current laptop gpu memory price"


def test_web_retry_preamble_is_not_treated_as_final_answer():
    assert al._looks_like_web_retry_preamble("That search got garbled. Let me retry:")
    assert al._looks_like_web_retry_preamble("The results were off-topic, so I'll search more specifically.")
    assert not al._looks_like_web_retry_preamble("The release date is September 20, 2026.")


def _schema_names(tools):
    return {
        tool.get("function", {}).get("name") or tool.get("name")
        for tool in (tools or [])
    }


def test_calendar_tool_family_carries_into_next_followup(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "moonshotai/kimi-k3",
            [
                {"role": "user", "content": "what's my calendar today"},
                {
                    "role": "assistant",
                    "content": "You have [Dance party](#event-dd48f640-0415-4af9-99f7-e65c86f9dba2) today.",
                    "metadata": {
                        "tool_events": [
                            {
                                "tool": "manage_calendar",
                                "command": '{"action":"list_events"}',
                            }
                        ]
                    },
                },
                {"role": "user", "content": "cancel that"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
            owner="sft_alex_creator",
        )
    )

    names = _schema_names(sent_tools[0])
    assert "manage_calendar" in names


def test_calendar_tool_family_expires_if_followup_did_not_use_it(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "moonshotai/kimi-k3",
            [
                {"role": "user", "content": "what's my calendar today"},
                {
                    "role": "assistant",
                    "content": "You have [Dance party](#event-dd48f640-0415-4af9-99f7-e65c86f9dba2) today.",
                    "metadata": {
                        "tool_events": [
                            {
                                "tool": "manage_calendar",
                                "command": '{"action":"list_events"}',
                            }
                        ]
                    },
                },
                {"role": "user", "content": "cancel that"},
                {"role": "assistant", "content": "I cannot cancel it."},
                {"role": "user", "content": "what about now"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
            owner="sft_alex_creator",
        )
    )

    names = _schema_names(sent_tools[0])
    assert "manage_calendar" not in names


def test_agent_loop_web_intent_cannot_reenable_caller_disabled_web_tools(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "please look up the latest CVEs"}],
            max_rounds=1,
            relevant_tools=set(),
            disabled_tools=set(WEB_TOOL_NAMES),
        )
    )

    assert sent_tools == []


def test_agent_loop_forced_tools_cannot_reenable_caller_disabled_web_tools(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "latest Kubernetes release"}],
            max_rounds=1,
            relevant_tools=set(),
            forced_tools=set(WEB_TOOL_NAMES),
            disabled_tools=set(WEB_TOOL_NAMES),
        )
    )

    assert sent_tools == []


def test_web_disabled_request_returns_feedback_without_calling_model(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_calls = []

    async def _fake_stream(*args, **kwargs):
        model_calls.append((args, kwargs))
        yield _delta_chunk("unexpected")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    chunks = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "search the web for the latest Kubernetes release"}],
            max_rounds=4,
            disabled_tools=set(WEB_ACCESS_TOOL_NAMES),
        )
    )

    events = _events(chunks)
    finals = [event for event in events if event.get("type") == "final_response"]
    assert model_calls == []
    assert finals == [{
        "type": "final_response",
        "content": "Web access is disabled for this turn. Enable web search and resend the request.",
    }]
    assert chunks[-1] == "data: [DONE]\n\n"


def test_web_disabled_context_comparison_uses_prior_result_without_new_lookup(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_calls = []

    async def _fake_stream(_candidates, messages, **kwargs):
        model_calls.append((messages, kwargs.get("tools")))
        yield _delta_chunk("Cedar costs less.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    chunks = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "Find the orange sofas and their prices."},
                {"role": "assistant", "content": "Cedar is $219 and Harbor is $349.", "metadata": {
                    "tool_events": [{"tool": "private_browser", "exit_code": 0}],
                }},
                {"role": "user", "content": "Which of those costs less?"},
            ],
            max_rounds=1,
            disabled_tools=set(WEB_ACCESS_TOOL_NAMES),
        )
    )

    assert model_calls
    assert any(event.get("delta") == "Cedar costs less." for event in _events(chunks))
    assert not any(
        event.get("content", "").startswith("Web access is disabled")
        for event in _events(chunks)
    )


def test_web_disabled_mixed_file_intent_still_calls_model(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_calls = []

    async def _fake_stream(*args, **kwargs):
        model_calls.append((args, kwargs))
        yield _delta_chunk("local result")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(
        al,
        "_classify_agent_request",
        lambda *args, **kwargs: {
            "low_signal": False,
            "continuation": False,
            "domains": {"files", "web"},
            "retrieval_query": "find the latest local repository",
        },
    )

    chunks = _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "find the latest local repository"}],
            max_rounds=1,
            relevant_tools={"host_shell"},
            disabled_tools=set(WEB_ACCESS_TOOL_NAMES),
        )
    )

    assert len(model_calls) == 1
    assert any("local result" in chunk for chunk in chunks)
    assert not any("Web access is disabled" in chunk for chunk in chunks)


def test_weather_status_followup_routes_to_web_not_cookbook(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "Will it rain today in Setagaya?"},
                {"role": "assistant", "content": "It may rain later today in Setagaya."},
                {"role": "user", "content": "Can you give me your status"},
            ],
            max_rounds=1,
            relevant_tools={
                "ask_user",
                "update_plan",
                "list_served_models",
                "list_downloads",
            },
        )
    )

    names = _schema_names(sent_tools[0])
    assert "web_search" in names
    assert "list_served_models" not in names
    assert "list_downloads" not in names
    assert "previous weather or forecast topic" in sent_messages[0][0]["content"]


def test_explicit_model_status_followup_stays_cookbook():
    messages = [
        {"role": "user", "content": "Will it rain today in Setagaya?"},
        {"role": "assistant", "content": "It may rain later today in Setagaya."},
        {"role": "user", "content": "what is my model server status"},
    ]

    assert not al._looks_like_contextual_weather_status_followup(
        messages,
        "what is my model server status",
    )


def test_release_notes_followup_keeps_web_fetch_available(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "What is the latest Ruby release?"},
                {
                    "role": "assistant",
                    "content": "The latest Ruby release is Ruby 4.0.6.",
                    "metadata": {
                        "tool_events": [
                            {"tool": "web_search", "command": "latest Ruby release version"}
                        ]
                    },
                },
                {"role": "user", "content": "Open official release notes."},
            ],
            max_rounds=1,
            relevant_tools={
                "ask_user",
                "update_plan",
                "manage_notes",
                "manage_calendar",
            },
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch"} <= names
    assert "manage_notes" not in names
    assert "follow-up to the prior public web task" in sent_messages[0][0]["content"]
    assert "latest Ruby release" in sent_messages[0][0]["content"]


def test_web_correction_followup_inherits_previous_search_topic(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "Search for current Mac chip for ai"},
                {
                    "role": "assistant",
                    "content": "M6 and M5 Ultra were announced.",
                    "metadata": {
                        "tool_events": [
                            {"tool": "web_search", "command": "latest Apple Mac chip"}
                        ]
                    },
                },
                {"role": "user", "content": "I tried website and can't find 512 ram version"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch"} <= names
    assert "Search for current Mac chip for ai" in sent_messages[0][0]["content"]
    assert "512 ram version" in sent_messages[0][0]["content"]


def test_youtube_latest_video_followup_keeps_prior_entity_context(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "does anthropic have a youtube channel"},
                {
                    "role": "assistant",
                    "content": (
                        "Yes -- Anthropic has an official YouTube channel at "
                        "youtube.com/@anthropic-ai. They also run a Claude channel "
                        "at youtube.com/@claude."
                    ),
                    "metadata": {
                        "tool_events": [
                            {"tool": "web_search", "command": "Anthropic YouTube channel"}
                        ]
                    },
                },
                {"role": "user", "content": "whats their latest video"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "youtube_tool"} <= names
    assert "follow-up to the prior public web task" in sent_messages[0][0]["content"]
    assert "does anthropic have a youtube channel" in sent_messages[0][0]["content"].lower()
    assert "whats their latest video" in sent_messages[0][0]["content"].lower()


def test_web_correction_chain_skips_generic_prior_followup(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "Search for current Mac chip for ai"},
                {
                    "role": "assistant",
                    "content": "M6 and M5 Ultra were announced.",
                    "metadata": {
                        "tool_events": [
                            {"tool": "web_search", "command": "latest Apple Mac chip"}
                        ]
                    },
                },
                {"role": "user", "content": "What's the release date"},
                {
                    "role": "assistant",
                    "content": "The release date is September 22, 2026.",
                    "metadata": {
                        "tool_events": [
                            {"tool": "web_search", "command": "M6 Mac mini M5 Ultra release date"}
                        ]
                    },
                },
                {"role": "user", "content": "You're mixing ram and storage no?"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
        )
    )

    preface = sent_messages[0][0]["content"]
    assert "Search for current Mac chip for ai" in preface
    assert "You're mixing ram and storage" in preface
    assert "What's the release date You're mixing" not in preface


def test_web_followup_prefers_multi_entity_user_topic_over_assistant_summary(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [
                {"role": "user", "content": "Where is better to live Sweden Switzerland or Japan"},
                {
                    "role": "assistant",
                    "content": "Switzerland maximizes income, Sweden balances family life, Japan is safe.",
                    "metadata": {"tool_events": [{"tool": "web_search", "command": "living Sweden Switzerland Japan"}]},
                },
                {"role": "user", "content": "Ok but what about statistics for schools I heard Japan isn't good and have bullies"},
                {
                    "role": "assistant",
                    "content": "Your concern about Japan is partly backed by the data. Bullying in Japan is documented.",
                    "metadata": {"tool_events": [{"tool": "web_search", "command": "Japan school bullying statistics"}]},
                },
                {"role": "user", "content": "Can you compare each countries nursery school levels"},
            ],
            max_rounds=1,
            relevant_tools={"ask_user", "update_plan"},
        )
    )

    preface = sent_messages[0][0]["content"]
    assert "Where is better to live Sweden Switzerland or Japan" in preface
    assert "nursery school levels" in preface
    assert "Prior answer context:" in preface


def test_agent_loop_policy_blocks_disabled_web_tool_call_before_execution(monkeypatch):
    _patch_loop_basics(monkeypatch)
    called = False
    model_called = False

    async def _fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        return ("web_search", {"output": "ran", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal model_called
        model_called = True
        yield _delta_chunk('```web_search\n{"query":"current CVEs"}\n```')
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    policy = build_effective_tool_policy(
        disabled_tools=WEB_TOOL_NAMES,
        last_user_message="please look up the latest CVEs",
    )
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "please look up the latest CVEs"}],
            max_rounds=1,
            relevant_tools={"web_search"},
            disabled_tools=set(policy.all_disabled_names()),
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    finals = [event for event in events if event.get("type") == "final_response"]

    assert called is False
    assert model_called is False
    assert not any(event.get("type") == "tool_start" for event in events)
    assert not any(event.get("type") == "tool_output" for event in events)
    assert finals == [{
        "type": "final_response",
        "content": "Web access is disabled for this turn. Enable web search and resend the request.",
    }]


def test_web_fetch_js_failure_exposes_private_browser_next_round(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools") or [])
        if len(sent_tools) == 1:
            call = {
                "id": "call_fetch",
                "name": "web_fetch",
                "arguments": json.dumps({"url": "https://www.spacex.com/launches/"}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield _delta_chunk("I can use the rendered browser now.")
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        return (
            "Fetch web",
            {
                "error": (
                    "web_fetch: https://www.spacex.com/launches/: "
                    "no readable text content (not HTML, or the page needs JS/login)"
                ),
                "exit_code": 1,
            },
        )

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "open the official SpaceX Starship launches page"}],
            max_rounds=2,
            relevant_tools={"web_search", "web_fetch"},
        )
    )

    assert len(sent_tools) >= 2
    second_round_names = _schema_names(sent_tools[1])
    assert "private_browser" in second_round_names
    assert any(
        "The previous web_fetch failed" in str(message.get("content") or "")
        for message in sent_messages[1]
    )


def test_go_to_web_prompt_routes_private_browser_and_prunes_noise(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I can open that in the browser.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "Go to Airbnb and find stays in Tokyo for next weekend under $150/night."}],
            max_rounds=1,
            relevant_tools={
                "web_search",
                "web_fetch",
                "manage_memory",
                "mcp__email__search_emails",
                "search_hf_models",
                "download_model",
                "ask_user",
                "update_plan",
            },
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser"} <= names
    assert "mcp__email__search_emails" not in names
    assert "search_hf_models" not in names
    assert "download_model" not in names


def test_open_web_player_routes_private_browser_not_ui(monkeypatch):
    _patch_loop_basics(monkeypatch)
    q = "Open Spotify's web player and search for Bach cello suites."
    intent = al._classify_agent_request([{"role": "user", "content": q}], q)
    assert intent["domains"] == {"web"}

    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("Opening it.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": q}],
            max_rounds=1,
            relevant_tools={
                "web_search",
                "web_fetch",
                "ui_control",
                "list_served_models",
                "serve_preset",
                "ask_user",
                "update_plan",
            },
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser"} <= names
    assert "ui_control" not in names
    assert "serve_preset" not in names
    assert "list_served_models" not in names


def test_open_naked_domain_routes_as_pure_web():
    q = "Open npmjs.com and find the weekly downloads for `playwright`."
    intent = al._classify_agent_request([{"role": "user", "content": q}], q)
    assert intent["domains"] == {"web"}
    assert al._looks_like_explicit_browser_interaction(q)


def test_private_browser_bot_check_switches_next_round_to_static_web(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools") or [])
        if len(sent_tools) == 1:
            call = {
                "id": "call_browser",
                "name": "private_browser",
                "arguments": json.dumps({
                    "action": "batch",
                    "commands": [
                        ["open", "https://www.npmjs.com/package/playwright"],
                        ["read"],
                    ],
                }),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield _delta_chunk("I'll use a static source instead.")
        yield "data: [DONE]\n\n"

    async def _fake_exec(block, *args, **kwargs):
        return (
            "Browse web",
            {
                "output": (
                    "# www.npmjs.com\n\n"
                    "## Performing security verification\n\n"
                    "This website uses a security service to protect against malicious bots.\n\n"
                    "Performance and Security by Cloudflare"
                ),
                "exit_code": 0,
            },
        )

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "Open npmjs.com and find the weekly downloads for `playwright`."}],
            max_rounds=2,
            relevant_tools={
                "web_search",
                "web_fetch",
                "private_browser",
                "ui_control",
                "ask_user",
                "update_plan",
            },
        )
    )

    assert len(sent_tools) >= 2
    second_round_names = _schema_names(sent_tools[1])
    assert "web_search" in second_round_names
    assert "web_fetch" in second_round_names
    assert "private_browser" not in second_round_names
    assert any(
        "bot/security verification" in str(message.get("content") or "")
        for message in sent_messages[1]
    )


def test_rendered_page_followup_keeps_private_browser(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll inspect the rendered comments.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [
                {
                    "role": "user",
                    "content": "Open YouTube and find the latest video from the official OpenAI channel.",
                },
                {
                    "role": "assistant",
                    "content": "The newest video is What Codex Unlocks for loveholidays.",
                    "metadata": {
                        "tool_events": [
                            {
                                "tool": "private_browser",
                                "command": "{\"action\":\"batch\",\"commands\":[[\"open\",\"https://www.youtube.com/@OpenAI/videos\"],[\"snapshot\"]]}",
                            }
                        ]
                    },
                },
                {"role": "user", "content": "and what does the comments say?"},
            ],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch", "ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser", "youtube_tool"} <= names
    assert any(
        "follow-up to the prior public web task" in str(message.get("content") or "")
        for message in sent_messages[0]
    )


def test_maps_followup_keeps_private_browser(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_messages.append(messages)
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll check the rendered map.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [
                {
                    "role": "user",
                    "content": "whats the nearest conbini for me from denenchoufu station",
                },
                {
                    "role": "assistant",
                    "content": "Nearest conbini looks like FamilyMart Denen-chofu-ekimae.",
                    "metadata": {
                        "tool_events": [
                            {
                                "tool": "web_search",
                                "command": "Denenchofu station nearest convenience store",
                            }
                        ]
                    },
                },
                {"role": "user", "content": "use google maps?"},
            ],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch", "ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser"} <= names
    assert any(
        "map/navigation/location help" in str(message.get("content") or "")
        for message in sent_messages[0]
    )


def test_closest_parking_routes_private_browser(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    sent_messages = []
    prompt = "from vasaplan stockholm where is closest parking"

    assert al._looks_like_map_browser_request(prompt)

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        sent_messages.append(messages)
        yield _delta_chunk("I'll check parking near Vasaplan.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": prompt}],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch", "ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser"} <= names
    assert any(
        "map/navigation/location help" in str(message.get("content") or "")
        for message in sent_messages[0]
    )


def test_youtube_prompt_routes_youtube_tool_without_noise(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll check YouTube-specific data.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "Open YouTube and find the latest video from the official OpenAI channel."}],
            max_rounds=1,
            relevant_tools={
                "web_search",
                "web_fetch",
                "private_browser",
                "youtube_tool",
                "mcp__email__search_emails",
                "manage_memory",
                "ask_user",
                "update_plan",
            },
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "private_browser", "youtube_tool"} <= names
    assert "mcp__email__search_emails" not in names
    assert "manage_memory" not in names


def test_latest_numbered_videos_routes_youtube_tool(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll check the latest channel uploads.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "whats rainbolts latest 5 videos?"}],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch", "ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert {"web_search", "web_fetch", "youtube_tool"} <= names
    assert "update_plan" not in names


def test_explicit_plan_request_keeps_update_plan(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll draft a plan.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "local-model",
            [{"role": "user", "content": "make a plan for testing search traces"}],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch", "ask_user", "update_plan"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert "update_plan" in names


def test_agent_loop_retries_web_after_retry_preamble(monkeypatch):
    _patch_loop_basics(monkeypatch)
    calls = []

    async def _fake_exec(block, *args, **kwargs):
        query = al._web_search_query_from_block(block)
        calls.append(query)
        if len(calls) == 1:
            return (
                "Check web",
                {
                    "output": "[1] Can You Mix RAM Brands?\n  URL: https://example.test/ram\n  Snippet: PC RAM kits.",
                    "exit_code": 0,
                },
            )
        return (
            "Check web",
            {
                "output": (
                    "[1] Mac mini - Apple\n"
                    "  URL: https://www.apple.com/shop/buy-mac/mac-mini\n"
                    "  Snippet: Configure Mac mini with Apple silicon, unified memory, storage, and availability."
                ),
                "exit_code": 0,
            },
        )

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk(
            "That search got garbled. Let me retry:\n"
            '```web_search\n{"query":"How much vram or unified memory will be available"}\n```'
        )
        yield "data: [DONE]\n\n"

    async def _fake_synth(*args, **kwargs):
        return "Apple lists unified memory and SSD storage separately on the configurator."

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_synth, raising=False)

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Search for current Mac chip for ai"}],
            max_rounds=1,
            relevant_tools={"web_search"},
        )
    )
    events = _events(chunks)

    assert len(calls) == 2
    assert "current mac chip" in calls[1].lower()
    assert "how much vram or unified memory" in calls[1].lower()
    assert any(
        event.get("type") == "tool_start"
        and event.get("fallback") == "web_retry_preamble"
        for event in events
    )
    assert any(
        event.get("type") == "final_response"
        and "unified memory and SSD storage separately" in event.get("content", "")
        for event in events
    )


def test_agent_loop_synthesizes_web_answer_after_tool_preamble(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_exec(block, *args, **kwargs):
        return (
            "Check web",
            {
                "output": (
                    "[1] Japan vs Sweden Education Stats Compared\n"
                    "  URL: https://example.test/education\n"
                    "  Snippet: Japan and Sweden education statistics compared across school outcomes."
                ),
                "exit_code": 0,
            },
        )

    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _delta_chunk(
                "I'll look up current education stats.\n"
                '```web_search\n{"query":"Where is better for school Sweden or Japan stats and compare"}\n```'
            )
        else:
            yield _delta_chunk("Let me get more specific data on test scores and education systems.")
        yield "data: [DONE]\n\n"

    async def _fake_synth(*args, **kwargs):
        return "Japan and Sweden both have strong school systems, with different tradeoffs."

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_synth, raising=False)

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Where is better for school Sweden or Japan? Search stats and compare"}],
            max_rounds=2,
            relevant_tools={"web_search"},
        )
    )
    events = _events(chunks)

    assert any(
        event.get("type") == "final_response"
        and "Japan and Sweden both have strong school systems" in event.get("content", "")
        for event in events
    )


def test_open_calendar_request_uses_ui_control_panel_not_event_dump(monkeypatch):
    # Routing test written for the opt-out posture; the gate default is pinned in
    # tests/test_tool_approval_gate_default.py.
    monkeypatch.setattr("src.tool_capabilities.TOOL_APPROVAL_GATE_ENABLED", False)
    _patch_loop_basics(monkeypatch)
    src = Path(__file__).resolve().parent.parent.joinpath("src", "agent_loop.py").read_text(encoding="utf-8")
    assert 'if isinstance(_ev, dict) and _ev.get("context_only"):' in src
    seen_blocks = []

    async def _fake_exec(block, *args, **kwargs):
        seen_blocks.append(block.tool_type)
        if block.tool_type == "ui_control":
            return (
                "ui_control",
                {
                    "ui_event": "open_panel",
                    "panel": "calendar",
                    "results": "Opening calendar panel",
                    "exit_code": 0,
                },
            )
        return (
            block.tool_type,
            {
                "output": "unexpected tool",
                "exit_code": 1,
            },
        )

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("I'll open your calendar.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    chunks = _collect(
            al.stream_agent_loop(
                "http://local.test/v1",
                "qwen35-email-lora-ttft",
                [{"role": "user", "content": "open up my calendar"}],
                max_rounds=2,
                relevant_tools={"manage_calendar", "ui_control", "ask_user", "update_plan"},
            )
    )
    events = _events(chunks)
    final_texts = [
        event.get("content", "")
        for event in events
        if event.get("type") == "final_response"
    ]

    assert not any(text.startswith("Here are your events") for text in final_texts)
    assert not any("Here's what's on your calendar" in text for text in final_texts)
    assert any(
        event.get("type") == "ui_control"
        and (event.get("data") or {}).get("ui_event") == "open_panel"
        and (event.get("data") or {}).get("panel") == "calendar"
        for event in events
    )
    assert seen_blocks == ["ui_control", "manage_calendar"]
    assert any(
        event.get("type") == "tool_start"
        and event.get("tool") == "manage_calendar"
        and event.get("context_only") is True
        for event in events
    )


def test_calendar_create_response_includes_persistent_event_link(monkeypatch):
    # Routing test written for the opt-out posture; the gate default is pinned in
    # tests/test_tool_approval_gate_default.py.
    monkeypatch.setattr("src.tool_capabilities.TOOL_APPROVAL_GATE_ENABLED", False)
    _patch_loop_basics(monkeypatch)
    from src.user_time import clear_user_time_context, set_user_timezone

    set_user_timezone("Asia/Tokyo", 540)

    from tests.runtime_evidence_helpers import authoritative_executor

    @authoritative_executor
    async def _fake_exec(block, *args, **kwargs):
        if '"list_calendars"' in (block.content or ""):
            return (
                "manage_calendar",
                {
                    "output": "AI: Found 1 calendar(s):\n- Creator Ops (cal-1)",
                    "exit_code": 0,
                },
            )
        return (
            "manage_calendar",
            {
                "output": (
                    "AI: Created event [Dentist appointment](#event-evt-123) "
                    "on 2026-08-28T10:00:00"
                ),
                "response": (
                    "Created event [Dentist appointment](#event-evt-123) "
                    "on 2026-08-28T10:00:00"
                ),
                "uid": "evt-123",
                "dtstart": "2026-08-28T01:00:00Z",
                "anchor": "[Dentist appointment](#event-evt-123)",
                "reminder_note_id": "note-reminder-123",
                "reminder_minutes": 15,
                "exit_code": 0,
            },
        )

    async def _fake_stream(_candidates, messages, **kwargs):
        calls = [
            {
                "id": "call_calendar_list",
                "name": "manage_calendar",
                "arguments": json.dumps({"action": "list_calendars"}),
            },
            {
                "id": "call_calendar_create",
                "name": "manage_calendar",
                "arguments": json.dumps({
                    "action": "create_event",
                    "calendar_href": "cal-1",
                    "summary": "Dentist appointment",
                    "dtstart": "2026-08-28T10:00:00",
                    "dtend": "2026-08-28T10:30:00",
                }),
            },
        ]
        yield _delta_chunk("Done.")
        yield f'data: {json.dumps({"type": "tool_calls", "calls": calls})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    try:
        chunks = _collect(
            al.stream_agent_loop(
                "http://local.test/v1",
                "qwen35-email-lora-ttft",
                [{"role": "user", "content": "create an event tomorrow dentist"}],
                max_rounds=2,
                owner="sft_alex_creator",
                relevant_tools={"manage_calendar", "ask_user", "update_plan"},
            )
        )
    finally:
        clear_user_time_context()
    visible = "\n".join(
        (event.get("delta") or event.get("content") or "")
        for event in _events(chunks)
    )
    metrics_events = [
        event.get("data") or {}
        for event in _events(chunks)
        if event.get("type") == "metrics"
    ]

    assert "Done." in visible
    assert "View event: [Dentist appointment, 10:00 AM 🔔](#event-evt-123)" in visible
    assert metrics_events
    assert any(
        "View event: [Dentist appointment, 10:00 AM 🔔](#event-evt-123)" in text
        for text in metrics_events[-1].get("round_texts", [])
    )


def test_executor_policy_backstop_blocks_tools():
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("bash", "echo should-not-run"),
            tool_policy=policy,
            security_context=NO_TOOL_SECURITY_CONTEXT,
        )
    )
    assert desc == "bash: BLOCKED"
    assert result["exit_code"] == 1
    assert "forbade" in result["error"]


def test_agent_loop_blocks_guide_only_fenced_tool_before_start(monkeypatch):
    _patch_loop_basics(monkeypatch)
    called = False

    async def _fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        return ("bash", {"output": "ran", "exit_code": 0})

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("```bash\necho should-not-run\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    policy = build_effective_tool_policy(last_user_message="GUIDE-ONLY MODE. DO NOT USE TOOLS.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "GUIDE-ONLY MODE. DO NOT USE TOOLS."}],
            max_rounds=1,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert called is False
    assert not any(event.get("type") == "tool_start" for event in events)
    blocked = [event for event in events if event.get("type") == "tool_output"]
    assert blocked
    assert blocked[0]["tool"] == "bash"
    assert blocked[0]["exit_code"] == 1


def test_guide_only_hides_api_function_schemas(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"bash", "web_search"},
            tool_policy=policy,
        )
    )

    assert sent_tools == [None]


def test_guide_only_skips_tool_retrieval(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    def _fail_tool_index():
        raise AssertionError("guide-only mode must not retrieve tool candidates")

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "src.tool_index",
        SimpleNamespace(get_tool_index=_fail_tool_index, ALWAYS_AVAILABLE=set()),
    )
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools=None,
            tool_policy=policy,
        )
    )

    assert sent_tools == [None]


def test_guide_only_blocks_document_prestream(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("```create_document\nTitle\nmd\nBody\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"create_document"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert not any(event.get("type") == "doc_stream_open" for event in events)
    assert not any(event.get("type") == "tool_start" for event in events)
    assert any(event.get("type") == "tool_output" and event.get("tool") == "create_document" for event in events)


def test_guide_only_blocks_later_round_document_streaming(monkeypatch):
    _patch_loop_basics(monkeypatch)
    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _delta_chunk("```bash\necho blocked\n```")
        else:
            yield _delta_chunk("```create_document\nTitle\nmd\nBody\n```")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=2,
            relevant_tools={"bash", "create_document"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert calls == 2
    assert not any(event.get("type") == "doc_stream_open" for event in events)
    assert not any(event.get("type") == "doc_stream_delta" for event in events)


def test_guide_only_skips_intent_without_action_nudge(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("I will check the logs.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=2,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )
    events = _events(chunks)
    assert not any(event.get("type") == "agent_step" for event in events)


def test_guide_only_suppresses_active_document_context(monkeypatch):
    _patch_loop_basics(monkeypatch)
    prompt_payloads = []

    async def _fake_stream(_candidates, messages, **kwargs):
        prompt_payloads.append("\n\n".join(str(msg.get("content", "")) for msg in messages))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")
    active_doc = SimpleNamespace(
        id="doc-1",
        current_content="SECRET ACTIVE DOCUMENT CONTENT",
        title="Secret Doc",
        language="markdown",
    )

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"edit_document"},
            tool_policy=policy,
            active_document=active_doc,
        )
    )

    assert prompt_payloads
    assert "SECRET ACTIVE DOCUMENT CONTENT" not in prompt_payloads[0]
    assert "ACTIVE DOCUMENT" not in prompt_payloads[0]
    assert "Relevant skills" not in prompt_payloads[0]


def test_document_my_style_does_not_infer_public_persona(monkeypatch):
    _patch_loop_basics(monkeypatch)
    monkeypatch.setattr(al, "_build_base_prompt", lambda *a, **k: ("BASE", ""), raising=False)
    monkeypatch.setattr(al, "_cached_base_prompt", None, raising=False)
    monkeypatch.setattr(al, "_cached_base_prompt_key", None, raising=False)

    import src.settings as settings
    monkeypatch.setattr(settings, "load_settings", lambda: {"document_writing_style": ""}, raising=False)

    active_doc = SimpleNamespace(
        id="doc-style",
        current_content="A short poem already exists here.",
        title="Morning Poem",
        language="markdown",
    )

    messages, _ = al._build_system_prompt(
        [{"role": "user", "content": "Write as my style"}],
        model="local-model",
        active_document=active_doc,
        mcp_mgr=None,
        relevant_tools={"edit_document", "update_document"},
        suppress_skills=True,
    )
    payload = "\n\n".join(str(msg.get("content", "")) for msg in messages)

    assert "There is no saved document writing style" in payload
    assert "do NOT infer that style from memories, identity, public persona" in payload


def test_guide_only_skips_teacher_escalation(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("Could you tell me what output you see?")
        yield "data: [DONE]\n\n"

    async def _fail_teacher(*_args, **_kwargs):
        raise AssertionError("teacher escalation must not run in guide-only mode")
        yield ""

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "src.teacher_escalation",
        SimpleNamespace(run_teacher_inline=_fail_teacher),
    )
    policy = build_effective_tool_policy(last_user_message="Do not use tools.")

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Do not use tools."}],
            max_rounds=1,
            relevant_tools={"bash"},
            tool_policy=policy,
        )
    )

    assert any("Could you tell me" in chunk for chunk in chunks)


def test_previous_tool_domain_carryover_reads_persisted_session_metadata():
    history_session = SimpleNamespace(history=[
        SimpleNamespace(role="user", content="calendar add go to hokkaido", metadata={}),
        SimpleNamespace(
            role="assistant",
            content="View event: [Go to Hokkaido](#event-evt-1)",
            metadata={
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": json.dumps({"action": "create_event"}),
                        "exit_code": 0,
                    }
                ]
            },
        ),
        SimpleNamespace(
            role="user",
            content="remove Bjorn pickup today, then add reminder to summer festival",
            metadata={},
        ),
    ])
    prompt_messages = [
        {"role": "user", "content": "calendar add go to hokkaido"},
        {"role": "assistant", "content": "View event: [Go to Hokkaido](#event-evt-1)"},
        {
            "role": "user",
            "content": "remove Bjorn pickup today, then add reminder to summer festival",
        },
    ]

    assert al._domain_tools_from_previous_assistant_turn(
        prompt_messages,
        "remove Bjorn pickup today, then add reminder to summer festival",
        history_session=history_session,
    ) == {"notes_calendar_tasks"}


def test_ask_user_calendar_clarification_carries_calendar_domain():
    history_session = SimpleNamespace(history=[
        SimpleNamespace(role="user", content="add birthday 24th", metadata={}),
        SimpleNamespace(
            role="assistant",
            content="I need a couple of details to add that birthday correctly.",
            metadata={
                "tool_events": [
                    {
                        "tool": "ask_user",
                        "command": json.dumps({
                            "question": "Whose birthday is on the 24th, and which month?",
                            "options": [
                                {"label": "September 24"},
                                {"label": "Other month"},
                            ],
                        }),
                        "output": "Asked the user: Whose birthday is on the 24th, and which month?",
                        "exit_code": 0,
                    }
                ]
            },
        ),
        SimpleNamespace(role="user", content="me", metadata={}),
    ])

    assert al._domain_tools_from_previous_assistant_turn(
        [{"role": "user", "content": "me"}],
        "me",
        history_session=history_session,
    ) == {"notes_calendar_tasks"}


def test_calendar_action_continuation_carries_across_one_prose_suggestion():
    history_session = SimpleNamespace(history=[
        SimpleNamespace(role="user", content="whats my events next month?", metadata={}),
        SimpleNamespace(
            role="assistant",
            content="Here is your September calendar.",
            metadata={
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": json.dumps({
                            "action": "list_events",
                            "start": "2026-09-01",
                            "end": "2026-10-01",
                        }),
                        "exit_code": 0,
                    }
                ]
            },
        ),
        SimpleNamespace(
            role="user",
            content="any suggestion when I can book a meeting with sion?",
            metadata={},
        ),
        SimpleNamespace(
            role="assistant",
            content="Thursday Sep 3 at noon is open. Want me to book it?",
            metadata={"round_texts": ["Thursday Sep 3 at noon is open."]},
        ),
        SimpleNamespace(role="user", content="lets add it for thursday then 12pm", metadata={}),
    ])

    assert al._domain_tools_from_previous_assistant_turn(
        [{"role": "user", "content": "lets add it for thursday then 12pm"}],
        "lets add it for thursday then 12pm",
        history_session=history_session,
    ) == {"notes_calendar_tasks"}


def test_qwen_followup_route_respects_caller_disabled_calendar_tool(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    history_session = SimpleNamespace(history=[
        SimpleNamespace(role="user", content="create event next week sunday dog cafe", metadata={}),
        SimpleNamespace(
            role="assistant",
            content="View event: [Go to dog cafe](#event-evt-1)",
            metadata={
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": json.dumps({"action": "create_event"}),
                        "exit_code": 0,
                    }
                ]
            },
        ),
        SimpleNamespace(role="user", content="make that 12:15", metadata={}),
    ])

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll update that event.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "qwen35-email-lora-ttft",
            [
                {"role": "user", "content": "create event next week sunday dog cafe"},
                {"role": "assistant", "content": "View event: [Go to dog cafe](#event-evt-1)"},
                {"role": "user", "content": "make that 12:15"},
            ],
            max_rounds=1,
            owner="sft_alex_creator",
            disabled_tools={"manage_calendar", "manage_notes", "manage_tasks"},
            history_session=history_session,
        )
    )

    names = _schema_names(sent_tools[0])
    assert "manage_calendar" not in names


def test_api_followup_route_respects_caller_disabled_carried_calendar_tool(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []
    history_session = SimpleNamespace(history=[
        SimpleNamespace(role="user", content="create event next week sunday dog cafe", metadata={}),
        SimpleNamespace(
            role="assistant",
            content="View event: [Go to dog cafe](#event-evt-1)",
            metadata={
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": json.dumps({"action": "create_event"}),
                        "exit_code": 0,
                    }
                ]
            },
        ),
        SimpleNamespace(role="user", content="remove the bjorn pickup event today", metadata={}),
    ])

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll remove that calendar event.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://openrouter.ai/api/v1",
            "moonshotai/kimi-k3",
            [
                {"role": "user", "content": "create event next week sunday dog cafe"},
                {"role": "assistant", "content": "View event: [Go to dog cafe](#event-evt-1)"},
                {"role": "user", "content": "remove the bjorn pickup event today"},
            ],
            max_rounds=1,
            owner="sft_alex_creator",
            disabled_tools={"manage_calendar", "manage_notes", "manage_tasks"},
            history_session=history_session,
        )
    )

    names = _schema_names(sent_tools[0])
    assert "manage_calendar" not in names


def test_qwen_router_keeps_calendar_for_event_reminder_followup():
    tools = al._qwen38_router_tool_names(
        "add a reminder to the summer festival event 15 min before"
    )

    assert "manage_calendar" in tools
    assert "manage_tasks" not in tools


def test_qwen_router_keeps_calendar_for_pickup_delete_followup():
    tools = al._qwen38_router_tool_names("remove the bjorn pickup event today")

    assert "manage_calendar" in tools


def test_latest_personal_events_routes_calendar_not_web():
    prompt = "What's my latest events"

    intent = al._classify_agent_request([{"role": "user", "content": prompt}], prompt)
    tools = al._qwen38_router_tool_names(prompt)

    assert "notes_calendar_tasks" in intent["domains"]
    assert "web" not in intent["domains"]
    assert "manage_calendar" in tools
    assert "web_search" not in tools


def test_recurring_event_lookup_routes_calendar_not_tasks():
    tools = al._qwen38_router_tool_names("show my recurring trash events")

    assert "manage_calendar" in tools
    assert "manage_tasks" not in tools


def test_recurring_automation_lifecycle_routes_tasks_not_calendar():
    tools = al._qwen38_router_tool_names(
        "Set up a recurring task that runs every Monday morning to summarize "
        "my open notes, then pause it, resume it later, and finally remove it."
    )

    assert tools == {"manage_tasks"}


def test_recurring_meeting_routes_calendar_not_tasks():
    tools = al._qwen38_router_tool_names(
        "Schedule a recurring team meeting every Monday morning."
    )

    assert "manage_calendar" in tools
    assert "manage_tasks" not in tools


def test_document_title_containing_notes_does_not_offer_notes_product():
    prompt = (
        "Create a document titled 'Q3 planning feedback notes' with exactly one sentence. "
        "Then search the document library, suggest a replacement, and delete that document."
    )

    assert requested_capabilities(prompt) == frozenset({"documents"})


def test_compound_document_lifecycle_is_not_single_action_terminal():
    assert al._request_has_compound_actions(
        "Create a document, search for it, suggest a revision, then delete it."
    )
    assert not al._request_has_compound_actions(
        "Create a document titled Weekly status."
    )


def test_two_source_comparison_is_not_single_fetch_terminal():
    assert al._request_has_compound_actions(
        "Open https://example.com/a and https://example.org/b, compare their "
        "evidence, and cite both sources."
    )


def test_calendar_lookup_requires_fresh_tool_even_before_schema_is_added():
    assert al._calendar_lookup_requires_fresh_tool(
        "show my recurring trash events",
        {"notes_calendar_tasks"},
        set(),
    )


def test_agent_reasoning_preamble_is_replaceable():
    assert al._looks_like_agent_reasoning_preamble(
        "The user wants to open the calendar panel for September 2026. Use ui_control."
    )
    assert not al._looks_like_agent_reasoning_preamble(
        "Here are your events (3):\n- Dentist appointment — Sep 12"
    )
    assert al._looks_like_agent_reasoning_preamble(
        "No preset matches the compact candidates. Nothing was launched. "
        "Now let me verify nothing was started."
    )
    assert al._looks_like_agent_reasoning_preamble(
        "But let me re-examine the visual evidence to be sure."
    )
    assert al._looks_like_agent_reasoning_preamble(
        "I'll need to methodically examine the recording vicinity."
    )
    assert al._looks_like_agent_reasoning_preamble(
        "需要先查看视频内容。使用 inspect_media 来分析视频。"
    )
    assert not al._looks_like_agent_reasoning_preamble(
        "当前信息有限，可能需要查看完整视频或相关资料。"
    )


def test_trailing_answer_promise_is_removed_without_losing_factual_answer():
    text = (
        "The /tmp directory is empty (only contains . and .. entries). "
        "I should report this clearly to the user."
    )

    assert al._strip_trailing_answer_promise(text) == (
        "The /tmp directory is empty (only contains . and .. entries)."
    )


def test_trailing_answer_promise_cleanup_does_not_rewrite_normal_answer():
    text = "The report is ready. You should send it to the user when approved."

    assert al._strip_trailing_answer_promise(text) == text


def test_can_now_provide_answer_is_internal_preamble():
    assert al._looks_like_agent_reasoning_preamble(
        "The command executed successfully. I can now provide the answer."
    )


def test_read_only_empty_bash_listing_has_concise_terminal_summary():
    event = {
        "tool": "bash",
        "command": "ls -la /tmp",
        "output": (
            "total 0\n"
            "drwxrwxrwt 2 root root 40 Sep 8 20:00 .\n"
            "drwxr-xr-x 1 root root 80 Sep 8 20:00 .."
        ),
    }

    assert al._ody_qwen_terminal_tool_summary(event) == "`/tmp` is empty."


def test_email_account_identity_boundary_is_not_inbox_lookup():
    assert al._is_email_account_identity_request("What's my email?")
    assert al._is_email_account_identity_request("What is my email address?")
    assert al._is_email_account_identity_request("List my connected email accounts")
    assert not al._is_email_account_identity_request("What's my latest email?")
    assert not al._is_email_account_identity_request("Show my unread emails")


def test_email_scan_announcement_is_a_tool_preamble():
    assert al._is_tool_preamble(
        "I'll scan the Primary Inbox for spam and look at the Junk folder, without making any changes."
    )
    assert al._is_tool_preamble(
        "I'll re-scan the inbox and re-list the Junk folder to verify."
    )


def test_email_draft_no_tool_boundary_does_not_capture_real_reply_actions():
    assert al._qwen_no_tool_boundary_answer(
        "Write a short email saying thanks, but do not send it."
    )
    assert al._qwen_no_tool_boundary_answer(
        "Use AI Reply for email UID 1 to draft a response. Do not send it."
    ) is None
    assert al._qwen_no_tool_boundary_answer(
        "Coordinate onboarding:\n"
        "1. Read each HR email\n"
        "2. Check calendars\n"
        "3. Send manager notifications; if no manager, save an email draft "
        "and do not send it\n"
        "4. Create follow-up items"
    ) is None


def test_admin_report_parser_does_not_capture_status_reporting_verbs():
    tool, content = al._parse_qwen_explicit_admin_request(
        "List Cookbook downloads and report the SmolLM2 download status."
    )
    assert tool == "list_downloads"
    assert content == ""
    tool, content = al._parse_qwen_explicit_admin_request(
        "List my saved research reports."
    )
    assert tool == "manage_research"
    assert json.loads(content)["action"] == "list"


def test_explicit_admin_router_ignores_negated_tool_mentions():
    assert al._parse_qwen_explicit_admin_request(
        "Use Cookbook search to find Gemma models. Do not use the configured model list, and do not download anything."
    ) is None


def test_simple_calendar_lookup_fallback_latest_events(monkeypatch):
    tool, args = al._parse_simple_calendar_tool_request("what's my latest events")

    parsed = json.loads(args)
    assert tool == "manage_calendar"
    assert parsed["action"] == "list_events"
    assert parsed["start"]
    assert parsed["end"]


def test_simple_calendar_lookup_accepts_missing_chat_apostrophes():
    tool, args = al._parse_simple_calendar_tool_request(
        "whats todays calendar"
    )

    parsed = json.loads(args)
    assert tool == "manage_calendar"
    assert parsed["action"] == "list_events"
    assert parsed["start"]
    assert parsed["end"]
    assert al._parse_simple_calendar_tool_request("whats my calendar") is not None
    assert al._calendar_bounds_for_prompt(
        "whats todays calendar", today="2026-09-09"
    ) == ("2026-09-09", "2026-09-10")


def test_simple_calendar_lookup_does_not_treat_definition_as_user_lookup():
    assert al._parse_simple_calendar_tool_request("What is a calendar?") is None
    assert al._is_personal_tool_definition_turn("What is a calendar?")
    assert al._parse_simple_calendar_tool_request("What is calendar software?") is None
    assert al._parse_explicit_memory_lookup_request("What does computer memory mean?") is None


def test_simple_calendar_lookup_handles_do_i_have_events_wording():
    tool, args = al._parse_simple_calendar_tool_request("Do I have any events today?")
    assert tool == "manage_calendar"
    parsed = json.loads(args)
    assert parsed["action"] == "list_events"
    assert parsed["start"]
    assert parsed["end"]


def test_notes_panel_open_is_not_reinterpreted_as_note_search():
    assert al._parse_simple_notes_tool_request("open the notes panel") is None


def test_scheduled_tasks_plural_selects_personal_task_domain():
    intent = al._classify_agent_request([], "Show my scheduled tasks")
    assert "notes_calendar_tasks" in intent["domains"]


def test_simple_calendar_lookup_fallback_uses_recent_event_title():
    messages = [
        {"role": "user", "content": "add event"},
        {
            "role": "assistant",
            "content": "View event: [Coffee with Priya, 5:00 PM](#event-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee)",
        },
        {"role": "user", "content": "show that event"},
    ]

    tool, args = al._parse_simple_calendar_tool_request(
        "show that event",
        messages,
    )

    parsed = json.loads(args)
    assert tool == "manage_calendar"
    assert parsed["action"] == "list_events"
    assert parsed["query"] == "Coffee with Priya"


def test_simple_calendar_tag_update_fallback():
    tool, args = al._parse_simple_calendar_tool_request(
        "change the SFT calendar smoke Hokkaido trip tag to personal"
    )

    parsed = json.loads(args)
    assert tool == "manage_calendar"
    assert parsed["action"] == "update_event"
    assert parsed["summary"] == "SFT calendar smoke Hokkaido trip"
    assert parsed["tag"] == "personal"


def test_recurring_calendar_create_parser_uses_monthly_ordinal_rule(monkeypatch):
    tool, args = al._parse_qwen_explicit_create_request(
        "add recurring event every 2nd Thursday of the month 7pm SFT calendar smoke book club"
    )

    parsed = json.loads(args)
    assert tool == "manage_calendar"
    assert parsed["action"] == "create_event"
    assert parsed["rrule"] == "FREQ=MONTHLY;BYDAY=2TH"
    assert parsed["summary"] == "SFT calendar smoke book club"
    assert "T19:00:00" in parsed["dtstart"]


def test_next_month_calendar_reservation_without_day_uses_ask_user():
    tool, args = al._parse_ambiguous_calendar_date_ask_user(
        "event next month dinner at Skytree Tokyo 9:30 reservation id 59i2323 remind me day before"
    )

    parsed = json.loads(args)
    assert tool == "ask_user"
    assert "What day in" in parsed["question"]
    assert "Skytree Tokyo" in parsed["question"]
    assert parsed["options"][0]["label"] == "Exact date"


def test_api_calendar_lookup_respects_caller_disabled_calendar_schema(monkeypatch):
    _patch_loop_basics(monkeypatch)
    sent_tools = []

    async def _fake_stream(_candidates, messages, **kwargs):
        sent_tools.append(kwargs.get("tools") or [])
        yield _delta_chunk("I'll check your calendar.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://openrouter.ai/api/v1",
            "moonshotai/kimi-k3",
            [{"role": "user", "content": "whats my events today"}],
            max_rounds=1,
            owner="sft_alex_creator",
            disabled_tools={"manage_calendar", "manage_notes", "manage_tasks"},
        )
    )

    names = _schema_names(sent_tools[0])
    assert "manage_calendar" not in names


def test_calendar_lookup_requires_fresh_tool_for_availability_and_next_event():
    domains = {"notes_calendar_tasks"}
    tools = {"manage_calendar", "ask_user"}

    assert al._calendar_lookup_requires_fresh_tool(
        "do I have anything Friday afternoon",
        domains,
        tools,
    )
    assert al._calendar_lookup_requires_fresh_tool(
        "when is my next appointment",
        domains,
        tools,
    )
    assert not al._calendar_lookup_requires_fresh_tool(
        "add birthday on the 24th",
        domains,
        tools,
    )


def test_explicit_open_up_calendar_routes_to_ui_control():
    assert al._parse_explicit_open_panel_request("open up my calendar") == (
        "ui_control",
        "open_panel calendar",
    )


def test_show_skill_note_memory_requests_do_not_open_panels():
    assert al._parse_explicit_open_panel_request("show my skills") is None
    assert al._parse_explicit_open_panel_request("show my notes") is None
    assert al._parse_explicit_open_panel_request("show my memories") is None
    assert al._parse_explicit_open_panel_request("show my documents") is None
    assert al._parse_explicit_open_panel_request("open skills") == (
        "ui_control",
        "open_panel skills",
    )
    assert al._parse_explicit_open_panel_request("open documents") == (
        "ui_control",
        "open_panel documents",
    )
    assert al._parse_explicit_open_panel_request("Now open documents.") == (
        "ui_control",
        "open_panel documents",
    )
    assert al._parse_explicit_open_panel_request("Return to documents.") == (
        "ui_control",
        "open_panel documents",
    )
    assert al._parse_explicit_open_panel_request("Go back and open gallery again.") == (
        "ui_control",
        "open_panel gallery",
    )


@pytest.mark.parametrize("message,theme", [
    ("set the theme to dark", "dark"),
    ("go dark mode pls", "dark"),
    ("hmm actually switch it back to light", "light"),
])
def test_explicit_theme_change_parser_binds_set_action(message, theme):
    assert al._parse_explicit_theme_change_request(message) == (
        "ui_control", json.dumps({"action": "set_theme", "name": theme})
    )


def test_explicit_theme_change_parser_does_not_steal_discussion():
    assert al._parse_explicit_theme_change_request("is dark mode easier on the eyes?") is None


def test_personal_task_list_routes_to_task_manager():
    intent = al._classify_agent_request([], "show me my tasks")
    assert "notes_calendar_tasks" in intent["domains"]
    block = al._parse_explicit_task_state_request("show me my tasks")
    assert block.tool_type == "manage_tasks"
    assert json.loads(block.content) == {"action": "list"}

    natural_intent = al._classify_agent_request([], "what tasks are on my list?")
    assert not natural_intent["low_signal"]
    assert "notes_calendar_tasks" in natural_intent["domains"]


def test_personal_inventory_natural_wording_routes_to_domain_tools():
    task = al._parse_explicit_task_state_request("what tasks are on my list?")
    assert task is not None
    assert task.tool_type == "manage_tasks"
    assert json.loads(task.content) == {"action": "list"}

    assert al._parse_explicit_skill_request("what skills do I have?") == {
        "action": "list"
    }

    document_tool, document_args = al._parse_qwen_explicit_document_request(
        "what documents do I have?"
    )
    assert document_tool == "manage_documents"
    assert json.loads(document_args) == {"action": "list"}


def test_skill_search_is_not_normalized_to_public_web_search():
    prompt = "search my skills for testing"
    intent = al._classify_agent_request([], prompt)
    assert "skills" in intent["domains"]
    assert "web" not in intent["domains"]
    assert al._parse_explicit_skill_request(prompt) == {
        "action": "search",
        "query": "testing",
    }


def test_web_search_no_results_is_not_usable_answer_evidence():
    assert not al._web_search_output_has_answer_evidence(
        "search the web for the current weather in Stockholm",
        "Query: current weather in Stockholm\nThe web search returned no results.",
    )


def test_web_search_cambridge_place_name_is_not_mistaken_for_dictionary_result():
    assert al._web_search_output_has_answer_evidence(
        "Latest news in AI",
        (
            "[1] Suno rolls out licensed AI models\n"
            "Snippet: Cambridge song-creation startup Suno unveiled new AI models."
        ),
    )


def test_web_search_actual_dictionary_result_is_not_evidence_for_news_query():
    assert not al._web_search_output_has_answer_evidence(
        "Latest news in AI",
        (
            "[1] AI definition | Cambridge Dictionary\n"
            "    URL: https://dictionary.cambridge.org/dictionary/english/ai\n"
            "    Snippet: Meaning of AI in English."
        ),
    )


def test_web_search_one_dictionary_row_does_not_veto_other_relevant_rows():
    assert al._web_search_output_has_answer_evidence(
        "Latest news in AI",
        (
            "[1] AI definition | Cambridge Dictionary\n"
            "    URL: https://dictionary.cambridge.org/dictionary/english/ai\n"
            "    Snippet: Meaning of AI in English.\n\n"
            "[2] Anthropic researcher leaves AI laboratory\n"
            "    URL: https://example.com/news/anthropic-ai\n"
            "    Snippet: The researcher raised concerns about increasingly capable models.\n\n"
            "[3] Suno launches licensed AI music models\n"
            "    URL: https://example.com/news/suno-ai\n"
            "    Snippet: The company introduced a new family of music models."
        ),
    )


def test_web_model_insufficient_evidence_detection_is_generic():
    assert al._web_model_reports_insufficient_evidence(
        "The results don't provide a clear answer about the comparison."
    )
    assert al._web_model_reports_insufficient_evidence(
        "I couldn't verify the requested detail from these pages."
    )
    assert not al._web_model_reports_insufficient_evidence(
        "There is no definitive winner because quality depends on the stated criteria."
    )


def test_direct_email_listing_terminal_summary_uses_user_request():
    summary = al._ody_qwen_terminal_tool_summary(
        {
            "tool": "mcp__email__list_emails",
            "command": '{"folder":"INBOX","max_results":5}',
            "output": (
                "Found 1 email(s):\n\n"
                "1. **Project update**\n"
                "   From: Pat <pat@example.com>\n"
                "   Date: 2026-09-09\n"
                "   UID: 42"
            ),
        },
        user_text="show me my latest inbox emails",
    )

    assert summary.startswith("Here is your latest email:")
    assert "Project update" in summary


def test_served_models_word_order_routes_to_cookbook_runtime():
    prompt = "list the models currently being served"
    intent = al._classify_agent_request([], prompt)
    assert not intent["low_signal"]
    assert "cookbook" in intent["domains"]
    assert al._parse_qwen_explicit_admin_request(prompt) == (
        "list_served_models",
        "",
    )


def test_explicit_open_calendar_month_view_preserves_view():
    assert al._parse_explicit_open_panel_request("open calendar month view") == (
        "ui_control",
        "open_panel calendar month",
    )


def test_explicit_open_calendar_month_names_and_years():
    assert al._parse_explicit_open_panel_request("open calendar december") == (
        "ui_control",
        "open_panel calendar month 2026-12",
    )
    assert al._parse_explicit_open_panel_request("open calendar October 2028") == (
        "ui_control",
        "open_panel calendar month 2028-10",
    )
    assert al._parse_explicit_open_panel_request("open calendar 2028 october") == (
        "ui_control",
        "open_panel calendar month 2028-10",
    )
    assert al._parse_explicit_open_panel_request("open calendar next year october") == (
        "ui_control",
        "open_panel calendar month 2027-10",
    )
    assert al._parse_explicit_open_panel_request("open calendar october next year") == (
        "ui_control",
        "open_panel calendar month 2027-10",
    )
    assert al._parse_explicit_open_panel_request("open calendar next year") == (
        "ui_control",
        "open_panel calendar year 2027-01",
    )


def test_fabricated_calendar_event_anchor_requires_calendar_tool():
    assert al._fabricated_calendar_event_anchor_without_tool(
        "View event: [Sail home](#event-cf8da629-177d-4faf-bdd2-1ff167f52b0125)",
        {"manage_calendar", "ui_control"},
    )
    assert not al._fabricated_calendar_event_anchor_without_tool(
        "View event: [Sail home](#event-cf8da629-177d-4faf-bdd2-1ff167f52b0125)",
        {"ui_control"},
    )


def test_calendar_create_claim_requires_create_event_evidence():
    expected = al._calendar_expected_mutation_actions("add birthday on the 24th")

    assert not al._has_successful_calendar_action_evidence(
        [
            {
                "tool": "manage_calendar",
                "command": '{"action": "list_calendars"}',
                "exit_code": 0,
            }
        ],
        expected,
    )
    assert al._has_successful_calendar_action_evidence(
        [
            {
                "tool": "manage_calendar",
                "command": '{"action": "create_event"}',
                "exit_code": 0,
            }
        ],
        expected,
    )


def test_drop_rejected_round_response_removes_embedded_rejected_text():
    rejected = "Fake done with [Birthday](#event-a1b2c3d4-bundle2)"
    full = f"prefix debug {rejected} final answer"

    assert al._drop_rejected_round_response(full, rejected) == "prefix debug  final answer"


def test_notes_lookup_requires_fresh_manage_notes_action():
    domains = {"notes_calendar_tasks"}
    tools = {"manage_notes", "ask_user"}

    assert al._notes_request_requires_fresh_tool(
        "search notes for passport",
        domains,
        tools,
    )
    assert al._notes_request_requires_fresh_tool(
        "show notes tagged errands",
        domains,
        tools,
    )
    assert al._notes_request_requires_fresh_tool(
        "delete the packing list",
        domains,
        tools,
    )


def test_notes_create_claim_requires_add_note_evidence():
    expected = al._notes_expected_actions("create a note saying Marzia likes jasmine tea")

    assert not al._has_successful_notes_action_evidence(
        [
            {
                "tool": "manage_notes",
                "command": '{"action": "list"}',
                "exit_code": 0,
            }
        ],
        expected,
    )
    assert al._has_successful_notes_action_evidence(
        [
            {
                "tool": "manage_notes",
                "command": '{"action": "add"}',
                "exit_code": 0,
            }
        ],
        expected,
    )


def test_notes_delete_action_wins_over_list_word():
    expected = al._notes_expected_actions("delete the grocery list")

    assert expected == {"delete", "remove"}
    assert not al._has_successful_notes_action_evidence(
        [
            {
                "tool": "manage_notes",
                "command": '{"action": "list"}',
                "exit_code": 0,
            }
        ],
        expected,
    )


def test_packing_list_routes_to_notes_domain():
    intent = al._classify_agent_request(
        [{"role": "user", "content": "delete the packing list"}],
        "delete the packing list",
    )

    assert "notes_calendar_tasks" in intent["domains"]


def test_simple_notes_fallback_parses_checklist_create():
    tool, raw = al._parse_simple_notes_tool_request(
        "make a checklist called SFT smoke v2 packing list with passport, charger, headphones"
    )
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args["action"] == "add"
    assert args["note_type"] == "checklist"
    assert args["title"] == "SFT smoke v2 packing list"
    assert [item["text"] for item in args["checklist_items"]] == [
        "passport",
        "charger",
        "headphones",
    ]


def test_simple_notes_fallback_parses_note_saying_with_label():
    tool, raw = al._parse_simple_notes_tool_request(
        "create a note saying Marzia likes jasmine tea v2 and tag it personal"
    )
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {
        "action": "add",
        "title": "Marzia likes jasmine tea v2",
        "content": "Marzia likes jasmine tea v2",
        "label": "personal",
    }


def test_simple_notes_fallback_parses_tagged_notes_lookup():
    tool, raw = al._parse_simple_notes_tool_request("show notes tagged errands")
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {"action": "list", "label": "errands"}


def test_simple_notes_fallback_parses_pinned_notes_lookup():
    tool, raw = al._parse_simple_notes_tool_request("show my pinned notes")
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {"action": "list", "pinned": True}


def test_simple_notes_fallback_parses_reminder_notes_lookup():
    tool, raw = al._parse_simple_notes_tool_request("show my reminder notes")
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {"action": "list", "reminders": True}


def test_simple_notes_fallback_parses_checklist_remaining_lookup():
    tool, raw = al._parse_simple_notes_tool_request("what is left on the launch QA checklist?")
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {"action": "search", "query": "launch QA"}


def test_simple_notes_fallback_cleans_read_note_query_words():
    tool, raw = al._parse_simple_notes_tool_request("read the Tokyo packing idea Suica note")
    args = json.loads(raw)

    assert tool == "manage_notes"
    assert args == {"action": "search", "query": "Tokyo packing idea Suica"}


def test_note_title_pairs_can_identify_duplicate_matching_body_lookup():
    raw = "\n".join(
        [
            "- [search-seeds] **Search seed prompts - NQ 200 - 2026-08-26** [PINNED] #search-sft",
            "- [pinned-launch] **Pinned Launch Checklist** [PINNED] #work",
            "- [qa-one] **SFT notes audit launch QA** [checklist] #work",
            "- [qa-two] **SFT notes audit launch QA** [checklist] #work",
        ]
    )
    pairs = al._note_title_id_pairs_from_tool_output(raw)
    terms = ["launch", "qa"]
    matching = [
        (title, note_id)
        for title, note_id in pairs
        if all(term in title.lower() for term in terms)
    ]

    assert matching == [
        ("SFT notes audit launch QA", "qa-one"),
        ("SFT notes audit launch QA", "qa-two"),
    ]


def test_false_unavailable_tool_claim_detects_selected_calendar_tool():
    text = (
        "I don't have access to calendar tools this turn, so I can't remove "
        "the pickup or add the reminder."
    )

    assert al._false_unavailable_tool_claim(
        text,
        {"manage_calendar", "ask_user", "update_plan"},
    ) == "manage_calendar"


def test_false_unavailable_tool_claim_ignores_unselected_calendar_tool():
    text = "I don't have access to calendar tools this turn."

    assert al._false_unavailable_tool_claim(
        text,
        {"web_search", "ask_user"},
    ) == ""


def test_qwen_router_selects_admin_inventory_tools():
    assert "manage_settings" in al._qwen38_router_tool_names("show which agent tools are currently disabled")
    assert "manage_settings" in al._qwen38_router_tool_names("turn image generation back on now")
    assert "manage_tokens" in al._qwen38_router_tool_names("list API tokens by name and prefix only")
    assert "manage_webhooks" in al._qwen38_router_tool_names("list webhook integrations")
    assert "manage_mcp" in al._qwen38_router_tool_names("list MCP servers")


def test_qwen_explicit_admin_requests_build_safe_args():
    assert al._parse_qwen_explicit_admin_request("show which agent tools are currently disabled") == (
        "manage_settings",
        json.dumps({"action": "list_tools"}),
    )
    assert al._parse_qwen_explicit_admin_request("turn image generation back on now") == (
        "manage_settings",
        json.dumps({"action": "enable_tool", "tool": "images"}),
    )
    assert al._parse_qwen_explicit_admin_request("list API tokens by name and prefix only") == (
        "manage_tokens",
        json.dumps({"action": "list"}),
    )
    assert al._parse_qwen_explicit_admin_request("list webhook integrations") == (
        "manage_webhooks",
        json.dumps({"action": "list"}),
    )


def test_qwen_explicit_session_delete_uses_recent_session_link():
    messages = [
        {
            "role": "assistant",
            "content": "Found it: [audit helper 20260828](#session-8003653f) (model: moonshotai/kimi-k3).",
        }
    ]

    assert al._parse_qwen_explicit_session_action(
        "Delete the audit helper 20260828 scratch chat.",
        messages,
    ) == (
        "manage_session",
        json.dumps({"action": "delete", "session_id": "8003653f"}),
    )


def test_qwen_explicit_session_delete_ignores_conversational_filler():
    messages = [{
        "role": "assistant",
        "content": "[audit relay abc-sessions](#session-cd51fdc8)",
    }]
    assert al._parse_qwen_explicit_session_action(
        "Delete the audit relay abc-sessions scratch chat now.", messages
    ) == (
        "manage_session",
        json.dumps({"action": "delete", "session_id": "cd51fdc8"}),
    )


def test_qwen_internal_app_api_requests_do_not_route_to_model_endpoints():
    assert al._parse_qwen_explicit_admin_request(
        "Use the internal app API catalog to list safe gallery endpoints."
    ) == ("app_api", json.dumps({"action": "endpoints", "filter": "gallery"}))
    assert al._parse_qwen_explicit_admin_request(
        "Use the safe internal app API to read the gallery list now."
    ) == (
        "app_api",
        json.dumps({"action": "call", "method": "GET", "path": "/api/gallery/library"}),
    )


def test_qwen_explicit_session_send_uses_recent_session_link():
    messages = [{
        "role": "assistant",
        "content": "Created [audit relay alpha](#session-relay-123).",
    }]
    assert al._parse_qwen_explicit_session_send(
        "Send that audit relay chat this message: Reply with exactly RECEIVED.",
        messages,
    ) == (
        "send_to_session",
        "relay-123\nReply with exactly RECEIVED.",
    )


def test_explicit_cookbook_followups_use_recent_tool_event_session_id():
    messages = [{
        "role": "assistant",
        "content": "The model server is starting.",
        "metadata": {
            "tool_events": [{
                "tool": "serve_model",
                "command": '{"port": 18091}',
                "output": "Serving tiny model (session: serve-f8ad8b8a)",
            }],
        },
    }, {
        "role": "assistant",
        "content": "Running: 1 LIVE, 8 cookbook-tracked, session serve-f8ad8b8a.",
        "metadata": {"tool_events": [{"tool": "list_served_models"}]},
    }]

    tool, content = al._parse_explicit_cookbook_task_action(
        "Show me the last 120 lines of its server logs.", messages
    )
    assert tool == "tail_serve_output"
    assert json.loads(content) == {"session_id": "serve-f8ad8b8a", "tail": 120}

    tool, content = al._parse_explicit_cookbook_task_action(
        "Stop that server now.", messages
    )
    assert tool == "stop_served_model"
    assert json.loads(content) == {"session_id": "serve-f8ad8b8a"}


def test_qwen_explicit_settings_list_is_preemptive():
    assert al._parse_qwen_explicit_admin_request(
        "List current settings without changing them."
    ) == ("manage_settings", json.dumps({"action": "list"}))


def test_qwen_model_delegation_is_not_rewritten_as_model_listing():
    assert not al._is_qwen_explicit_model_list_request(
        "Ask another available model for a one-sentence definition."
    )
    assert al._is_qwen_explicit_model_list_request(
        "List the available models I can delegate a short question to."
    )
    assert not al._is_qwen_explicit_model_list_request(
        "Extract each model's scores from Table 2, which includes MMT-Bench results."
    )


def test_compact_route_preserves_explicit_teacher_delegation_on_web_classified_text():
    tools = {
        "ask_teacher",
        "chat_with_model",
        "list_models",
        "web_search",
        "web_fetch",
        "private_browser",
        "ask_user",
    }

    selected = al._compact_native_route_tools(
        tools,
        "Use ask_teacher to check whether this sentence is verifiable and concise.",
        {"web"},
    )

    assert {"ask_teacher", "chat_with_model", "list_models"} <= selected


def test_qwen_saved_research_listing_is_preemptive():
    assert al._parse_qwen_explicit_admin_request(
        "List my saved research reports and find the most recent completed SearXNG report."
    ) == (
        "manage_research",
        json.dumps({"action": "list", "search": "searxng"}),
    )


def test_explicit_document_create_accepts_normal_wording_and_spaced_title():
    assert al._parse_qwen_explicit_create_request(
        "Create a document titled Suggestion audit abc-123 with exactly this sentence: The weekly report is very good."
    ) == (
        "create_document",
        "Suggestion audit abc-123\nmarkdown\nThe weekly report is very good.",
    )


def test_explicit_email_uid_actions_are_normalized_once():
    tool, content = al._parse_explicit_email_uid_action(
        "Use AI Reply for email UID 1 in the Primary Inbox and leave it reviewable."
    )
    assert tool == "mcp__email__ai_draft_email_reply"
    assert json.loads(content) == {
        "uid": "1",
        "folder": "INBOX",
        "account": "Primary Inbox",
    }

    tool, content = al._parse_explicit_email_uid_action(
        "Read email UID 10 in the Primary Inbox before replying."
    )
    assert tool == "mcp__email__read_email"
    assert json.loads(content) == {
        "uid": "10",
        "folder": "INBOX",
        "account": "Primary Inbox",
    }

    assert al._parse_explicit_email_uid_action(
        "Mark email UID 10 as unread in the Primary Inbox."
    ) == (
        "mcp__email__mark_email_read",
        json.dumps({"uid": "10", "folder": "INBOX", "read": False, "account": "Primary Inbox"}),
    )
    assert al._parse_explicit_email_uid_action(
        "Mark Lena Ortiz's matching email UID 10 as unread in the Primary Inbox."
    )[0] == "mcp__email__mark_email_read"
    assert al._parse_explicit_email_uid_action(
        "Unarchive email UID 3 back to the Primary Inbox now."
    ) == (
        "mcp__email__manage_email_state",
        json.dumps({"action": "unarchive", "uid": "3", "folder": "Archive", "account": "Primary Inbox"}),
    )
    assert al._parse_explicit_email_uid_action(
        "Send a reply now to email UID 10 saying: Thanks, I have the next steps."
    ) == (
        "mcp__email__reply_to_email",
        json.dumps({"uid": "10", "folder": "INBOX", "body": "Thanks, I have the next steps."}),
    )


def test_explicit_email_search_uses_named_account():
    tool, content = al._parse_explicit_email_search_tool(
        "Search the Primary Inbox for messages from Lena Ortiz."
    )
    assert tool == "mcp__email__search_emails"
    assert json.loads(content) == {
        "query": "Lena Ortiz",
        "max_results": 10,
        "account": "Primary Inbox",
    }


def test_email_inventory_projection_is_not_parsed_as_search_query():
    assert al._parse_explicit_email_search_tool(
        "List my latest three inbox emails with sender and subject."
    ) is None


def test_email_immediate_send_recognizes_explicit_email_and_reply_wording():
    assert al._email_immediate_send_requested("Send an email now to alex@example.com.")
    assert al._email_immediate_send_requested("Send a reply now to UID 10.")
    assert al._email_immediate_send_requested("请直接发送处理通知邮件给客户服务部。")
    assert not al._email_immediate_send_requested("Send an email to Alex saying hello.")


def test_email_mixed_send_and_review_policy_preserves_drafts():
    request = "符合条件的直接发送通知；其余仅保存草稿，需要上级审批。"

    assert al._email_immediate_send_requested(request)
    assert al._email_draft_review_requested(request)


def test_explicit_read_only_email_request_forbids_mutation_tools_only():
    request = (
        "Find the most recent inbox message and report its sender. "
        "Do not send, draft, or modify anything."
    )

    for tool in (
        "mcp__email__draft_email_reply",
        "mcp__email__send_email",
        "mcp__email__mark_email_read",
        "mcp__email__delete_email",
    ):
        assert al._email_mutation_forbidden(request, tool)

    assert not al._email_mutation_forbidden(request, "mcp__email__list_emails")
    assert not al._email_mutation_forbidden(request, "mcp__email__read_email")
    assert not al._email_mutation_forbidden(
        "Find UID 702 and draft a reply saying thanks; do not send it.",
        "mcp__email__draft_email_reply",
    )
    assert not al._email_mutation_forbidden(
        "Draft an email suggesting the free slot. Do not modify any calendar event.",
        "mcp__email__draft_email",
    )
    assert not al._email_mutation_forbidden(
        "Draft an email suggesting the slot. Do not send the email and do not "
        "create, delete, or modify any calendar event.",
        "mcp__email__draft_email",
    )


def test_qwen_explicit_session_current_chat_actions_use_manage_session():
    assert al._parse_qwen_explicit_session_action(
        "Rename this current audit chat to manage-session-audit-abc Use the tool directly and report the result.",
        [],
    ) == (
        "manage_session",
        json.dumps({
            "action": "rename",
            "session_id": "current",
            "value": "manage-session-audit-abc",
        }),
    )
    assert al._parse_qwen_explicit_session_action("Archive this current audit chat", []) == (
        "manage_session",
        json.dumps({"action": "archive", "session_id": "current"}),
    )
    assert al._parse_qwen_explicit_session_action("Unarchive this current audit chat", []) == (
        "manage_session",
        json.dumps({"action": "unarchive", "session_id": "current"}),
    )


def test_qwen_explicit_session_create_uses_create_session_format():
    assert al._parse_qwen_explicit_session_create(
        "Create a scratch chat named audit helper abc using model moonshotai/kimi-k3."
    ) == (
        "create_session",
        "audit helper abc\nmoonshotai/kimi-k3",
    )


def test_qwen_explicit_session_find_uses_list_sessions_filter():
    assert al._parse_qwen_explicit_session_find("List my chats") == (
        "list_sessions",
        "",
    )
    assert al._parse_qwen_explicit_session_find(
        "Find the audit helper abc chat in my chat list."
    ) == (
        "list_sessions",
        "audit helper abc",
    )
    assert al._parse_qwen_explicit_session_find(
        "Find previous chats mentioning calendar tools"
    ) is None


def test_qwen_explicit_chat_transcript_search_uses_search_chats():
    assert al._parse_qwen_explicit_chat_transcript_search(
        "Find previous chats mentioning calendar tools"
    ) == (
        "search_chats",
        "calendar tools",
    )
    assert al._parse_qwen_explicit_chat_transcript_search(
        "Search past chats for audit marker 20260828_204528-bb9906f6 Use the tool directly"
    ) == (
        "search_chats",
        "audit marker 20260828_204528-bb9906f6",
    )


def test_qwen_explicit_resolve_contact_uses_resolve_tool():
    assert al._parse_qwen_explicit_resolve_contact("Find the email address for Casey Morgan") == (
        "resolve_contact",
        json.dumps({"name": "Casey Morgan"}),
    )
    assert al._parse_qwen_explicit_resolve_contact("Resolve Priya Shah in my contacts") == (
        "resolve_contact",
        json.dumps({"name": "Priya Shah"}),
    )


def test_qwen_explicit_email_attachment_uid_uses_attachment_tool():
    assert al._parse_qwen_explicit_download_attachment_request(
        "Open attachment 0 from email UID 112 and summarize it"
    ) == {
        "uid": "112",
        "index": 0,
        "folder": "INBOX",
    }
    assert al._parse_qwen_explicit_download_attachment_request(
        "Read the first PDF attachment from UID 108"
    ) == {
        "uid": "108",
        "index": 0,
        "folder": "INBOX",
    }


def test_qwen_explicit_unsubscribe_scan_and_action():
    assert al._parse_qwen_explicit_unsubscribe_scan_request(
        "Scan recent email headers for unsubscribe candidates"
    ) == {
        "folder": "INBOX",
        "limit": 25,
        "max_scan": 500,
    }
    assert al._parse_qwen_explicit_unsubscribe_email_request(
        "Unsubscribe from email UID 126 using method 0"
    ) == {
        "uid": "126",
        "folder": "INBOX",
        "method_index": 0,
        "allow_web": False,
    }
    assert al._parse_qwen_explicit_unsubscribe_email_request(
        "Preview unsubscribing from UID 126; do not unsubscribe"
    ) is None


def test_qwen_explicit_bulk_email_uses_bulk_tool_args():
    assert al._parse_qwen_explicit_bulk_email_request(
        "Mark emails UID 162 and UID 163 as read"
    ) == {
        "action": "mark_read",
        "uids": ["162", "163"],
        "folder": "INBOX",
    }
    assert al._parse_qwen_explicit_bulk_email_request(
        "Archive UIDs 162, 163 in one bulk action"
    ) == {
        "action": "archive",
        "uids": ["162", "163"],
        "folder": "INBOX",
    }
    assert al._parse_qwen_explicit_bulk_email_request(
        "Mark email UID 162 as read"
    ) is None


def test_qwen_explicit_block_sender_uses_block_tool_args():
    assert al._parse_qwen_explicit_block_sender_request(
        "Block sender alerts@secure-rowan-login.co but do not delete existing messages"
    ) == {
        "sender": "alerts@secure-rowan-login.co",
        "folder": "INBOX",
        "move_existing": False,
        "reason": "User explicitly requested sender block.",
    }
    assert al._parse_qwen_explicit_block_sender_request(
        "Should I block alerts@secure-rowan-login.co?"
    ) is None


def test_notes_about_calendar_context_still_route_to_notes():
    prompt = (
        "Open my notes panel and create a short note called audit-calendar-note-abc "
        "summarizing that calendar context."
    )

    assert al._parse_simple_notes_tool_request(prompt) == (
        "manage_notes",
        '{"action": "add", "title": "audit-calendar-note-abc", "content": "that calendar context"}',
    )
    assert al._notes_request_requires_fresh_tool(
        prompt,
        {"notes_calendar_tasks"},
        {"ui_control", "manage_notes"},
    )


def test_notes_panel_open_plus_create_keeps_both_tool_calls(monkeypatch):
    # Routing test written for the opt-out posture; the gate default is pinned in
    # tests/test_tool_approval_gate_default.py.
    monkeypatch.setattr("src.tool_capabilities.TOOL_APPROVAL_GATE_ENABLED", False)
    _patch_loop_basics(monkeypatch)
    seen_blocks = []

    async def _fake_exec(block, *args, **kwargs):
        seen_blocks.append((block.tool_type, block.content))
        if block.tool_type == "ui_control":
            return "ui_control", {
                "ui_event": "open_panel",
                "panel": "notes",
                "results": "Opening notes panel",
                "exit_code": 0,
            }
        return "manage_notes", {
            "response": "Note created: audit-calendar-note-abc",
            "exit_code": 0,
        }

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("ui_control open_panel notes")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://openrouter.ai/api/v1",
            "moonshotai/kimi-k3",
            [{
                "role": "user",
                "content": (
                    "Open my notes panel and create a short note called "
                    "audit-calendar-note-abc summarizing that calendar context."
                ),
            }],
            max_rounds=1,
            owner="sft_alex_creator",
            relevant_tools={"ui_control", "manage_notes"},
        )
    )

    assert seen_blocks[0] == ("ui_control", "open_panel notes")
    assert seen_blocks[1][0] == "manage_notes"
    assert json.loads(seen_blocks[1][1])["action"] == "add"


def test_calendar_lookup_without_note_create_does_not_route_to_notes():
    prompt = "What events are visible on my calendar next week?"

    assert al._parse_simple_notes_tool_request(prompt) is None
    assert not al._notes_request_requires_fresh_tool(
        prompt,
        {"notes_calendar_tasks"},
        {"ui_control", "manage_notes", "manage_calendar"},
    )


def test_block_sender_terminal_summary_replaces_model_preamble():
    assert al._ody_qwen_terminal_tool_summary(
        {
            "tool": "mcp__email__block_sender",
            "command": json.dumps({"sender": "alerts@secure-rowan-login.co"}),
            "output": (
                "Already blocked: alerts@secure-rowan-login.co\n"
                "Moved 0 current message(s) to Junk.\n"
                "Future matching fixture mail will appear in Junk."
            ),
        },
        user_text="Block sender alerts@secure-rowan-login.co",
    ) == (
        "Already blocked: alerts@secure-rowan-login.co\n"
        "Moved 0 current message(s) to Junk.\n"
        "Future matching fixture mail will appear in Junk."
    )


def test_search_chats_is_in_api_schema_allowlist():
    assert "search_chats" in al._ADMIN_TOOLS


def test_successful_tool_evidence_prevents_explicit_call_repeat():
    assert al._has_successful_tool_evidence(
        [
            {
                "tool": "manage_settings",
                "command": '{"action":"list_tools"}',
                "output": "Currently disabled: (none).",
                "exit_code": 0,
            }
        ],
        "manage_settings",
    ) is True


def test_absolute_path_routes_to_files_not_web_only():
    intent = al._classify_agent_request(
        [{"role": "user", "content": "create /tmp/odysseus-sft-demo.txt with two lines"}],
        "create /tmp/odysseus-sft-demo.txt with two lines",
    )
    tools = al._qwen38_router_tool_names("create /tmp/odysseus-sft-demo.txt with two lines")

    assert "files" in intent["domains"]
    assert "write_file" in tools


def test_kimi_admin_request_is_forced_when_model_answers_without_tool(monkeypatch):
    _patch_loop_basics(monkeypatch)
    # This scenario exercises deterministic execution for an authorized
    # single-user/admin session. Public owners must continue to have
    # manage_settings denied by blocked_tools_for_owner.
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda _owner: set())
    seen_blocks = []

    async def _fake_exec(block, *args, **kwargs):
        seen_blocks.append((block.tool_type, block.content))
        return block.tool_type, {"response": "Currently disabled: (none).", "exit_code": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("I can't determine that from here.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://openrouter.ai/api/v1",
            "moonshotai/kimi-k3",
            [{"role": "user", "content": "show which agent tools are currently disabled"}],
            max_rounds=1,
            owner="sft_alex_creator",
            relevant_tools={"manage_settings"},
        )
    )

    assert seen_blocks == [("manage_settings", json.dumps({"action": "list_tools"}))]


def test_kimi_session_delete_followup_is_forced_from_recent_link(monkeypatch):
    _patch_loop_basics(monkeypatch)
    seen_blocks = []
    messages = [
        {"role": "user", "content": "find audit helper chat"},
        {"role": "assistant", "content": "Found it: [audit helper abc](#session-8003653f)."},
        {"role": "user", "content": "delete the audit helper abc scratch chat"},
    ]

    async def _fake_exec(block, *args, **kwargs):
        seen_blocks.append((block.tool_type, block.content))
        return block.tool_type, {"results": "Session 'audit helper abc' deleted", "exit_code": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        yield _delta_chunk("Done -- deleted it.")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://openrouter.ai/api/v1",
            "moonshotai/kimi-k3",
            messages,
            max_rounds=1,
            owner="sft_alex_creator",
            relevant_tools={"list_sessions", "manage_session"},
        )
    )

    assert seen_blocks == [
        ("manage_session", json.dumps({"action": "delete", "session_id": "8003653f"}))
    ]


def test_qwen_cached_model_status_uses_cookbook_cached_tool():
    assert al._parse_qwen_explicit_admin_request(
        "List cached models, still without launching anything."
    ) == ("list_cached_models", "")
    assert al._parse_qwen_explicit_admin_request(
        "Show my downloaded models on disk"
    ) == ("list_cached_models", "")


def test_qwen_download_status_uses_cookbook_download_tool():
    assert al._parse_qwen_explicit_admin_request(
        "Show active downloads without changing anything"
    ) == ("list_downloads", "")
    assert al._parse_qwen_explicit_admin_request(
        "What is downloading right now?"
    ) == ("list_downloads", "")


def test_cookbook_download_cancel_uses_cancel_download_not_server_stop():
    messages = [{
        "role": "assistant",
        "content": "Download started (session: cookbook-abcd1234).",
        "metadata": {
            "tool_events": [{
                "tool": "download_model",
                "output": "Download started (session: cookbook-abcd1234)",
            }],
        },
    }]

    assert al._parse_explicit_cookbook_task_action(
        "Cancel that download using its tracked session ID.", messages
    ) == ("cancel_download", json.dumps({"session_id": "cookbook-abcd1234"}))


def test_qwen_cookbook_server_status_uses_cookbook_tools():
    assert al._parse_qwen_explicit_admin_request(
        "List configured Cookbook servers"
    ) == ("list_cookbook_servers", "")
    assert al._parse_qwen_explicit_admin_request(
        "Show currently running Cookbook model servers"
    ) == ("list_served_models", "")
    assert al._parse_qwen_explicit_admin_request(
        "List saved Cookbook serve presets"
    ) == ("list_serve_presets", "")


def test_qwen_model_registry_still_uses_list_models():
    assert al._parse_qwen_explicit_admin_request(
        "List available models"
    ) == ("list_models", "")


def test_qwen_hardware_model_recommendation_uses_hwfit_scan():
    tool, content = al._parse_qwen_explicit_admin_request(
        "Find the best model to run on my hardware"
    )

    assert tool == "app_api"
    assert json.loads(content) == {
        "action": "call",
        "method": "GET",
        "path": "/api/hwfit/models",
        "query": {"fit_only": "true", "limit": 10, "sort": "fit"},
    }


def test_model_endpoints_take_precedence_over_model_catalog():
    assert al._parse_qwen_explicit_admin_request(
        "List configured model endpoints and summarize which ones are enabled."
    ) == ("manage_endpoints", json.dumps({"action": "list"}))


def test_explicit_research_start_routes_to_trigger_research():
    assert al._parse_qwen_explicit_admin_request(
        "Start a concise new research report about SearXNG privacy defaults and return its task id."
    ) == (
        "trigger_research",
        json.dumps({"topic": "SearXNG privacy defaults"}),
    )


def test_explicit_two_model_pipeline_builds_structured_steps():
    assert al._parse_explicit_pipeline_request(
        "Run a two-step pipeline using z-ai/glm-5.3-flash to draft a one-sentence SFT trace check, "
        "then qwen/qwen3.8-flash to tighten it."
    ) == (
        "pipeline",
        json.dumps({
            "steps": [
                {
                    "model": "z-ai/glm-5.3-flash",
                    "instruction": "draft a one-sentence SFT trace check",
                },
                {
                    "model": "qwen/qwen3.8-flash",
                    "instruction": "tighten it",
                },
            ],
        }),
    )


def _junk_scan_context():
    return [{
        "role": "assistant",
        "content": "I found two spam candidates.",
        "metadata": {
            "tool_events": [{
                "tool": "mcp__email__scan_spam",
                "command": json.dumps({"folder": "Junk", "account": "Primary Inbox"}),
                "output": (
                    "Found 2 likely spam candidate(s) from 2 recent email(s).\n"
                    "1. **Fake invoice**\n"
                    "   From: Scam One <one@example.test>\n"
                    "   UID: 148\n"
                    "   Account: Primary Inbox <alex.rowan@rowan.studio>\n"
                    "2. **Fake grant**\n"
                    "   From: Scam Two <two@example.test>\n"
                    "   UID: 149\n"
                    "   Account: Primary Inbox <alex.rowan@rowan.studio>\n"
                ),
            }],
        },
    }]


def test_spam_delete_first_junk_message_does_not_block_or_move_everything():
    text = "Delete the first clearly synthetic Junk message. Do not block its sender."

    assert al._contextual_spam_confirmation_action(text) == "delete"
    blocks = al._contextual_spam_confirmation_blocks(_junk_scan_context(), text, [], set())

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__delete_email"
    assert json.loads(blocks[0].content) == {
        "uid": "148",
        "folder": "Junk",
        "permanent": False,
        "account": "alex.rowan@rowan.studio",
    }


def test_spam_mutation_is_not_retried_after_failed_attempt_in_same_turn():
    text = "Delete the first clearly synthetic Junk message. Do not block its sender."
    attempted = [{
        "tool": "mcp__email__delete_email",
        "command": json.dumps({"uid": "148", "folder": "Junk"}),
        "output": "No matching UID found",
    }]

    assert al._contextual_spam_confirmation_blocks(
        _junk_scan_context(), text, attempted, set()
    ) == []


def test_spam_singular_followup_uses_uid_linked_in_previous_synthesis():
    messages = _junk_scan_context()
    messages[0]["content"] = "The clearest synthetic one is [Fake grant](#email-149)."
    text = "Delete the clearly synthetic message you just identified. Do not block its sender."

    blocks = al._contextual_spam_confirmation_blocks(messages, text, [], set())

    assert len(blocks) == 1
    assert blocks[0].tool_type == "mcp__email__delete_email"
    assert json.loads(blocks[0].content)["uid"] == "149"


def test_explicit_rescan_spam_without_email_noun_uses_junk_folder():
    assert al._parse_qwen_explicit_spam_scan_request(
        "Scan for spam again in Junk and confirm that exact message is gone."
    ) == {"folder": "Junk", "limit": 10, "max_scan": 100}


def test_compound_inbox_and_junk_scan_is_left_for_multi_tool_planning():
    assert al._parse_qwen_explicit_spam_scan_request(
        "Scan the Primary Inbox for spam and identify one synthetic message already in Junk."
    ) is None


def test_compact_native_prompt_relies_on_schemas_without_relisting_tools():
    prompt = al._assemble_prompt(
        {"inspect_media", "private_browser", "read_file", "write_file"},
        compact=True,
    )

    assert "Only the current turn's tool schemas are available" in prompt
    assert "## Available tools" not in prompt
    assert "`inspect_media`" not in prompt
    assert len(prompt) < 1200

    ordinary_prompt = al._assemble_prompt({"manage_notes"}, compact=True)
    assert "User wording may contain typos" in ordinary_prompt


def test_compact_native_artifact_prompt_omits_unrelated_assistant_rules():
    prompt = al._assemble_prompt(
        {"inspect_media", "private_browser", "read_file", "write_file", "ls"},
        compact=True,
    )

    assert "creating a workspace artifact" in prompt
    assert "create and verify every requested output" in prompt
    assert "manage_memory" not in prompt
    assert "set one with `/workspace" not in prompt
    assert len(prompt) < 400


def test_native_artifact_workspace_uses_bounded_non_coding_guidance():
    messages = [{
        "role": "user",
        "content": "Inspect /workspace/fixtures/reference.png and create /workspace/output.html",
    }]
    context = {
        "surface": "odysseus-native",
        "terminal_agent": True,
        "completion_requirements": {"required_artifacts": ["/workspace/output.html"]},
    }

    assert al._is_native_artifact_workspace_turn(messages, context) is True
    rules = al._native_artifact_workspace_rules("/workspace")
    assert "Workspace artifact mode" in rules
    assert "never call them inaccessible without a failed tool result" in rules
    assert "hidden tests" not in rules.lower()
    assert len(rules) < 700


def test_native_media_workspace_uses_bounded_non_coding_guidance():
    rules = al._native_media_workspace_rules("/workspace")

    assert "Workspace media mode" in rules
    assert "use `extract_text` first" in rules
    assert "use `inspect_media` first" in rules
    assert "Do not use bash/Python/ffprobe/OpenCV/ffmpeg" in rules
    assert "one bounded overview" in rules
    assert "never call it inaccessible without a failed tool result" in rules
    assert "transcribe only speech/audio" in rules
    assert "Workspace coding mode" not in rules
    assert len(rules) < 1100


def test_compact_native_media_analysis_removes_coding_noise():
    selected = al._compact_native_media_analysis_tools(
        {
            "apply_patch", "bash", "edit_file", "get_workspace", "glob",
            "grep", "inspect_media", "ls", "python", "read_file",
            "todowrite", "transcribe_media", "write_file",
        },
        text="Read the flashing words shown in /workspace/fixtures/video.webm",
        media_inputs=["/workspace/fixtures/video.webm"],
    )

    assert selected == {"bash", "inspect_media", "ls", "python", "read_file"}


def test_compact_native_media_analysis_clamps_explicit_ocr_to_native_tool():
    selected = al._compact_native_media_analysis_tools(
        {
            "apply_patch", "extract_text", "inspect_media", "ls", "python",
            "read_file", "transcribe_media", "write_file",
        },
        text=(
            "Use local OCR to extract the exact visible text from "
            "/workspace/tests/fixtures/vl/quarterly-dashboard.png."
        ),
        media_inputs=["/workspace/tests/fixtures/vl/quarterly-dashboard.png"],
    )

    assert selected == {"extract_text"}


def test_native_coding_turn_keeps_coding_workspace_guidance():
    messages = [{"role": "user", "content": "Fix the parser in this repository"}]
    context = {"surface": "odysseus-native", "terminal_agent": True}

    assert al._is_native_artifact_workspace_turn(messages, context) is False
    assert "Workspace coding mode" in al._workspace_coding_rules("/workspace")


def test_native_local_media_artifact_schema_boundary_removes_route_noise():
    def schema(name):
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    inspect_media = next(
        item for item in al.FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    schemas = [inspect_media] + [
        schema(name) for name in (
            "private_browser", "python", "read_file", "write_file",
            "web_search", "web_fetch", "edit_file", "apply_patch",
        )
    ]
    filtered = al._compact_native_artifact_schemas(
        schemas,
        text=(
            "View /workspace/fixtures/map.png and generate "
            "/workspace/output.html to reproduce it."
        ),
        artifacts=["/workspace/output.html"],
        media_inputs=["/workspace/fixtures/map.png"],
    )

    assert {
        item["function"]["name"] for item in filtered
    } == {"inspect_media", "private_browser", "python", "read_file", "write_file"}
    inspect_schema = next(
        item for item in filtered if item["function"]["name"] == "inspect_media"
    )
    assert set(inspect_schema["function"]["parameters"]["properties"]) == {
        "path", "max_dimension", "query", "crop",
    }


def test_native_video_artifact_hides_pdf_only_inspection_arguments():
    schema = next(
        item for item in al.FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )

    specialized = al._specialize_inspect_media_schema(
        schema,
        ["/workspace/fixtures/source.mp4"],
    )

    properties = specialized["function"]["parameters"]["properties"]
    assert specialized is not schema
    assert "segments" in properties
    assert "exports" in properties
    assert "page" not in properties
    assert "pages" not in properties


def test_successful_media_export_is_recorded_for_mutation_deduplication():
    signatures = set()
    block = ToolBlock(
        "inspect_media",
        '{"path":"/workspace/source.mp4","timestamp":"00:00:02",'
        '"output_path":"/workspace/frame.jpg"}',
    )

    assert al._record_successful_workspace_mutation(
        signatures, block, {"output": "Created still image", "exit_code": 0}
    ) is True
    assert al._workspace_mutation_signature(block) in signatures
    assert al._record_successful_workspace_mutation(
        set(), block, {"error": "decode failed", "exit_code": 1}
    ) is False


def test_native_artifact_schema_boundary_preserves_caller_contract():
    schemas = [{
        "type": "function",
        "function": {"name": "environment_action", "parameters": {}},
    }]

    filtered = al._compact_native_artifact_schemas(
        schemas,
        text="Create /workspace/output.html",
        artifacts=["/workspace/output.html"],
        media_inputs=[],
        preserved_names={"environment_action"},
    )

    assert filtered == schemas


def test_spam_scan_repeat_matching_ignores_json_key_order():
    block = ToolBlock(
        "mcp__email__scan_spam",
        '{"folder":"Junk","limit":10,"max_scan":100}',
    )
    event = {
        "tool": "mcp__email__scan_spam",
        "command": '{"max_scan":100,"limit":10,"folder":"Junk"}',
    }

    assert al._tool_block_matches_event_args(block, event) is True
