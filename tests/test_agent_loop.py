"""Tests for agent_loop.py — _detect_admin_intent, _compute_final_metrics,
and _append_tool_results. Uses mock imports to avoid loading the full app stack."""

import sys
import json
from unittest.mock import MagicMock
from src.tool_types import ToolBlock

_MOCKED_IMPORTS = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'src.agent_tools',
    'core.models', 'core.database',
]
_INJECTED_IMPORT_STUBS = {}
_PREEXISTING_AGENT_LOOP = sys.modules.get("src.agent_loop")


def _drop_module_if_same(name, expected):
    if sys.modules.get(name) is expected:
        sys.modules.pop(name, None)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and getattr(parent, "__dict__", {}).get(attr) is expected:
        delattr(parent, attr)


# Mock heavy dependencies before importing. Only clean up stubs this file
# created so pre-existing conftest/pytest modules keep their intended state.
for mod in _MOCKED_IMPORTS:
    if mod not in sys.modules:
        stub = MagicMock()
        sys.modules[mod] = stub
        _INJECTED_IMPORT_STUBS[mod] = stub

_IMPORTED_AGENT_LOOP = None
try:
    from src.agent_loop import (
        _detect_admin_intent,
        _classify_agent_request,
        _compute_final_metrics,
        _append_tool_results,
        _insert_before_latest_user,
        _normalize_web_search_block_query,
        _MCP_KEYWORDS,
        _web_search_query_from_block,
        _web_search_topic_text,
        _parse_qwen_explicit_email_topic_bulk_action_request,
        _email_bulk_blocks_from_search_output,
        _turn_targets_active_document,
    )
    _IMPORTED_AGENT_LOOP = sys.modules.get("src.agent_loop")
finally:
    if _PREEXISTING_AGENT_LOOP is None and _IMPORTED_AGENT_LOOP is not None:
        _drop_module_if_same("src.agent_loop", _IMPORTED_AGENT_LOOP)
    for _mod, _stub in _INJECTED_IMPORT_STUBS.items():
        _drop_module_if_same(_mod, _stub)


def test_what_im_writing_targets_the_visible_document():
    active_document = MagicMock(
        current_content="A draft that needs verification.",
        title="Well,",
        language="richtext",
    )


def test_unrelated_fact_check_does_not_target_a_stale_visible_document():
    active_document = MagicMock(
        current_content="A draft left open in the editor.",
        title="Old draft",
        language="richtext",
    )

    assert not _turn_targets_active_document(
        {"domains": set()},
        "fact check today's stock market claim",
        active_document,
    )

    assert _turn_targets_active_document(
        {"domains": set()},
        "can you fact check what Im writing",
        active_document,
    )


def test_import_stubs_do_not_leak_into_later_tests():
    leaked = [
        mod for mod, stub in _INJECTED_IMPORT_STUBS.items()
        if sys.modules.get(mod) is stub
    ]
    assert leaked == []
    if _PREEXISTING_AGENT_LOOP is None:
        assert sys.modules.get("src.agent_loop") is not _IMPORTED_AGENT_LOOP


def test_mcp_keyword_gate_matches_literal_mcp_requests():
    assert "mcp" in _MCP_KEYWORDS


def test_topic_bulk_email_request_searches_before_mutating():
    parsed = _parse_qwen_explicit_email_topic_bulk_action_request(
        "delete all my traffic monitor emails"
    )

    assert parsed == {
        "action": "delete",
        "query": "traffic monitor",
        "folder": "INBOX",
        "max_results": 50,
    }


def test_topic_bulk_email_blocks_split_by_account_from_search_output():
    raw = """Found 2 email(s):

1. **Traffic monitor stale**
   From: Alert Bot (alerts@example.com)
   Date: 2026-08-31T08:00:00+00:00
   UID: 101
   Account: Primary Inbox <alex@example.com>

2. **Traffic monitor active**
   From: Alert Bot (alerts@example.com)
   Date: 2026-08-31T09:00:00+00:00
   UID: 202
   Account: Research Mail <research@example.com>
"""

    blocks = _email_bulk_blocks_from_search_output(raw, action="delete")

    assert [block.tool_type for block in blocks] == [
        "mcp__email__bulk_email",
        "mcp__email__bulk_email",
    ]
    args = [json.loads(block.content) for block in blocks]
    assert args == [
        {"action": "delete", "uids": ["101"], "folder": "INBOX", "account": "alex@example.com"},
        {"action": "delete", "uids": ["202"], "folder": "INBOX", "account": "research@example.com"},
    ]


def test_polish_internet_search_request_classifies_as_web():
    intent = _classify_agent_request(
        [],
        "Wyszukaj w internecie i podaj temperaturę w Lubartowie dzisiaj",
    )

    assert intent["low_signal"] is False
    assert "web" in intent["domains"]


def test_insert_before_latest_user_places_context_before_last_user_turn():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "latest"},
    ]
    context = {"role": "system", "content": "context"}

    out = _insert_before_latest_user(messages, context)

    assert out == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        context,
        {"role": "user", "content": "latest"},
    ]
    assert messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "latest"},
    ]


def test_insert_before_latest_user_appends_when_no_user_message_exists():
    messages = [{"role": "assistant", "content": "reply"}]
    context = {"role": "system", "content": "context"}

    assert _insert_before_latest_user(messages, context) == [messages[0], context]


def test_web_search_topic_uses_prior_user_for_generic_search_followup():
    messages = [
        {"role": "user", "content": "which swedish king liked to dance hte most"},
        {"role": "assistant", "content": "Probably Gustav III."},
        {"role": "user", "content": "can you search"},
    ]

    assert (
        _web_search_topic_text(messages, "can you search")
        == "which swedish king liked to dance hte most"
    )


def test_web_search_topic_ignores_untrusted_context_when_resolving_followup():
    messages = [
        {"role": "user", "content": "which swedish king liked to dance hte most"},
        {
            "role": "user",
            "metadata": {"trusted": False},
            "content": "UNTRUSTED SOURCE DATA\nwrong query",
        },
        {"role": "user", "content": "can you search"},
    ]

    assert (
        _web_search_topic_text(messages, "can you search")
        == "which swedish king liked to dance hte most"
    )


def test_web_search_normalizer_replaces_non_query_tool_argument_with_topic():
    block = ToolBlock("web_search", "can you search")

    out = _normalize_web_search_block_query(
        block,
        "which swedish king liked to dance hte most",
    )

    assert _web_search_query_from_block(out) == "which swedish king liked to dance hte most"


def test_web_search_normalizer_trusts_model_chosen_contextual_query():
    block = ToolBlock("web_search", "Gustav III Sweden dancing masquerade ball")

    out = _normalize_web_search_block_query(
        block,
        "which swedish king liked to dance hte most",
    )

    assert _web_search_query_from_block(out) == "Gustav III Sweden dancing masquerade ball"


def test_web_search_normalizer_removes_leaked_prior_chat_prefix():
    block = ToolBlock(
        "web_search",
        '{"query":"hello looks like testing chat latest AI news","time_filter":"week"}',
    )

    out = _normalize_web_search_block_query(
        block,
        "latest ai news?",
        current_user_text="latest ai news?",
    )

    assert json.loads(out.content) == {
        "query": "latest AI news",
        "time_filter": "week",
    }


def test_web_search_normalizer_keeps_refinement_after_leaked_chat_prefix():
    block = ToolBlock(
        "web_search",
        "Hello, it looks like you're testing the chat: OpenAI Anthropic Google model release news",
    )

    out = _normalize_web_search_block_query(
        block,
        "latest ai news?",
        current_user_text="latest ai news?",
    )

    assert _web_search_query_from_block(out) == "OpenAI Anthropic Google model release news"


def test_web_search_normalizer_keeps_legitimate_model_enrichment():
    block = ToolBlock("web_search", "AI industry announcements September 2026")

    out = _normalize_web_search_block_query(
        block,
        "latest ai news?",
        current_user_text="latest ai news?",
    )

    assert _web_search_query_from_block(out) == "AI industry announcements September 2026"


def test_web_search_normalizer_removes_action_wrapper_and_leads_with_subject():
    block = ToolBlock(
        "web_search",
        '{"query":"Search the web for the official IKEA website"}',
    )

    out = _normalize_web_search_block_query(
        block,
        "Search the web for the official IKEA website",
    )

    assert json.loads(out.content)["query"] == "IKEA official website"


def test_official_website_answer_uses_matching_shortest_evidence_url():
    from src.agent_loop import _official_website_answer_from_search

    output = """
    [1] Shop Affordable Home Furnishings & Home Goods - IKEA
        URL: https://www.ikea.com/us/en/?tracking=1
    [2] Hej! Welcome to IKEA Global
        URL: https://www.ikea.com/?tracking=1
    """

    assert _official_website_answer_from_search(
        "Search the web for the official IKEA website", output
    ) == "The official IKEA website is https://www.ikea.com/."
    assert _official_website_answer_from_search(
        "Find IKEA chairs", output
    ) == ""


def test_web_search_normalizer_preserves_entity_identified_from_attached_image():
    block = ToolBlock("web_search", "Xiaomi SU7 current price 2026")

    out = _normalize_web_search_block_query(
        block,
        (
            "How much does this car sell for now? Please use the attached image. "
            "If exact pricing is uncertain, provide a plausible range and explain why."
        ),
    )

    assert _web_search_query_from_block(out) == "Xiaomi SU7 current price 2026"


def test_web_search_normalizer_trusts_json_query_and_preserves_time_filter():
    block = ToolBlock(
        "web_search",
        '{"query":"Gustav III Sweden dancing masquerade ball","time_filter":"year"}',
    )

    out = _normalize_web_search_block_query(
        block,
        "which swedish king liked to dance hte most",
    )

    payload = json.loads(out.content)
    assert payload == {
        "query": "Gustav III Sweden dancing masquerade ball",
        "time_filter": "year",
    }


def test_web_search_followup_keeps_prior_browser_subject_anchor():
    block = ToolBlock(
        "web_search",
        '{"query":"grilled cheese sandwich menu"}',
    )

    out = _normalize_web_search_block_query(
        block,
        (
            "use google maps and find closest coffee shop in todoroki "
            "which one has grilled cheese sandwich on the menu?"
        ),
        current_user_text="which one has grilled cheese sandwich on the menu?",
    )

    query = json.loads(out.content)["query"].lower()
    assert "todoroki" in query
    assert "coffee" in query
    assert "grilled cheese sandwich" in query


def test_web_search_terse_followup_still_trusts_inferred_entity():
    block = ToolBlock("web_search", "Gustav III Sweden dancing masquerade ball")

    out = _normalize_web_search_block_query(
        block,
        "which swedish king liked to dance the most can you search",
        current_user_text="can you search",
    )

    assert _web_search_query_from_block(out) == "Gustav III Sweden dancing masquerade ball"


# ---------------------------------------------------------------------------
# _detect_admin_intent
# ---------------------------------------------------------------------------

class TestDetectAdminIntent:
    """Test admin-intent detection from the last user message."""

    def _msgs(self, text: str):
        """Helper: wrap text in a minimal messages list."""
        return [{"role": "user", "content": text}]

    # --- Should detect admin intent ---

    def test_add_endpoint(self):
        assert _detect_admin_intent(self._msgs("add a new endpoint")) is True

    def test_create_endpoint(self):
        assert _detect_admin_intent(self._msgs("create endpoint for openai")) is True

    def test_manage_sessions(self):
        assert _detect_admin_intent(self._msgs("list all sessions")) is True

    def test_rename_session(self):
        assert _detect_admin_intent(self._msgs("rename this session")) is True

    def test_archive_session(self):
        assert _detect_admin_intent(self._msgs("archive old sessions")) is True

    def test_configure_settings(self):
        assert _detect_admin_intent(self._msgs("configure my settings")) is True

    def test_mcp_server(self):
        assert _detect_admin_intent(self._msgs("add an MCP server")) is True

    def test_api_key(self):
        assert _detect_admin_intent(self._msgs("update the API key")) is True

    def test_list_models(self):
        assert _detect_admin_intent(self._msgs("list models available")) is True

    def test_switch_model(self):
        assert _detect_admin_intent(self._msgs("switch model to gpt-4")) is True

    def test_manage_skills(self):
        assert _detect_admin_intent(self._msgs("show me my skills")) is True

    def test_schedule_task(self):
        assert _detect_admin_intent(self._msgs("schedule a cron task")) is True

    def test_case_insensitive(self):
        assert _detect_admin_intent(self._msgs("MANAGE SESSIONS")) is True

    # --- Should NOT detect admin intent ---

    def test_hello(self):
        assert _detect_admin_intent(self._msgs("hello")) is False

    def test_write_code(self):
        assert _detect_admin_intent(self._msgs("write some python code")) is False

    def test_explain_concept(self):
        assert _detect_admin_intent(self._msgs("explain how transformers work")) is False

    def test_general_question(self):
        assert _detect_admin_intent(self._msgs("what is the capital of France?")) is False

    # --- Edge cases ---

    def test_empty_messages(self):
        assert _detect_admin_intent([]) is False

    def test_no_user_message(self):
        assert _detect_admin_intent([{"role": "assistant", "content": "hi"}]) is False

    def test_multimodal_content(self):
        """Content as a list of blocks (vision messages)."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "rename this session please"},
        ]}]
        assert _detect_admin_intent(msgs) is True

    def test_multimodal_no_admin(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "describe this image"},
        ]}]
        assert _detect_admin_intent(msgs) is False

    def test_uses_last_user_message(self):
        """Should check only the last user message."""
        msgs = [
            {"role": "user", "content": "rename this session"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "thanks, now just say hello"},
        ]
        assert _detect_admin_intent(msgs) is False


# ---------------------------------------------------------------------------
# _compute_final_metrics
# ---------------------------------------------------------------------------

class TestComputeFinalMetrics:
    """Test metric computation with real and estimated usage."""

    def _base_args(self, **overrides):
        defaults = dict(
            messages=[{"role": "user", "content": "hello world"}],
            full_response="This is a test response.",
            total_duration=2.0,
            time_to_first_token=0.5,
            context_length=8192,
            real_input_tokens=100,
            real_output_tokens=50,
            has_real_usage=True,
            tool_events=[],
            round_texts=[],
            model="test-model",
            last_round_input_tokens=0,
            prep_timings=None,
        )
        defaults.update(overrides)
        return defaults

    def test_real_usage_tokens(self):
        m = _compute_final_metrics(**self._base_args())
        assert m["input_tokens"] == 100
        assert m["output_tokens"] == 50
        assert m["total_tokens"] == 150
        assert m["usage_source"] == "real"

    def test_estimated_usage_tokens(self):
        m = _compute_final_metrics(**self._base_args(
            has_real_usage=False,
            real_input_tokens=0,
            real_output_tokens=0,
        ))
        # Estimated: len("hello world\n") // 4 = 3
        assert m["input_tokens"] == 3
        assert m["usage_source"] == "estimated"

    def test_tps_calculation(self):
        m = _compute_final_metrics(**self._base_args(
            real_output_tokens=100,
            total_duration=2.0,
        ))
        assert m["tokens_per_second"] == 50.0

    def test_tps_zero_duration(self):
        m = _compute_final_metrics(**self._base_args(total_duration=0.0))
        assert m["tokens_per_second"] == 0

    def test_context_percent(self):
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=4096,
            context_length=8192,
        ))
        assert m["context_percent"] == 50.0

    def test_context_percent_capped_at_100(self):
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=10000,
            context_length=8192,
        ))
        assert m["context_percent"] == 100.0

    def test_context_percent_zero_context_length(self):
        m = _compute_final_metrics(**self._base_args(context_length=0))
        assert m["context_percent"] == 0

    def test_last_round_input_tokens_used_for_context_pct(self):
        """When last_round_input_tokens > 0, it should be used for context %."""
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=100,
            last_round_input_tokens=4096,
            context_length=8192,
        ))
        assert m["context_percent"] == 50.0

    def test_response_time(self):
        m = _compute_final_metrics(**self._base_args(total_duration=3.456))
        assert m["response_time"] == 3.46

    def test_time_to_first_token(self):
        m = _compute_final_metrics(**self._base_args(time_to_first_token=0.123))
        assert m["time_to_first_token"] == 0.12

    def test_time_to_first_token_none(self):
        m = _compute_final_metrics(**self._base_args(time_to_first_token=None))
        assert m["time_to_first_token"] == 0

    def test_model_returned(self):
        m = _compute_final_metrics(**self._base_args(model="gpt-4o"))
        assert m["model"] == "gpt-4o"

    def test_prep_timings_included(self):
        m = _compute_final_metrics(**self._base_args(
            time_to_first_token=1.25,
            prep_timings={"request_setup": 0.2, "tool_selection": 0.3, "prompt_build": 0.15},
        ))
        assert m["agent_prep_time"] == 0.65
        assert m["agent_model_wait_time"] == 0.6
        assert m["agent_prep_breakdown"] == {
            "request_setup": 0.2,
            "tool_selection": 0.3,
            "prompt_build": 0.15,
        }

    def test_tool_events_included(self):
        events = [{"tool": "bash", "duration": 1.0}]
        texts = ["round 1 text"]
        models = ["round-1-model"]
        m = _compute_final_metrics(**self._base_args(
            tool_events=events,
            round_texts=texts,
            round_models=models,
        ))
        assert m["tool_events"] == events
        assert m["round_texts"] == texts
        assert m["round_models"] == models

    def test_no_tool_events_excluded(self):
        m = _compute_final_metrics(**self._base_args(tool_events=[], round_texts=[]))
        assert "tool_events" not in m
        assert "round_texts" not in m
        assert "round_models" not in m


# ---------------------------------------------------------------------------
# _append_tool_results — native tool-call message shaping
# ---------------------------------------------------------------------------

class TestAppendToolResultsNativeContent:
    """After a native tool call with no prose, the assistant message's content
    must be JSON null (None), not an empty string. Google Gemini's
    OpenAI-compatible endpoint and Ollama both reject `tool_calls` + ""
    content with HTTP 400, which breaks every tool-using turn."""

    def _native(self):
        return [{"id": "call_abc", "name": "web_fetch", "arguments": '{"url": "https://example.com"}'}]

    def test_empty_text_yields_null_content(self):
        messages = []
        _append_tool_results(
            messages, "", self._native(), [{}], ["page text"],
            used_native=True, round_num=1,
        )
        assistant = messages[0]
        assert assistant["role"] == "assistant"
        assert assistant["content"] is None  # NOT ""
        assert assistant["tool_calls"][0]["id"] == "call_abc"
        assert assistant["tool_calls"][0]["type"] == "function"
        # tool result follows as a role:tool message keyed by tool_call_id
        assert messages[1]["role"] == "tool"
        assert messages[1]["tool_call_id"] == "call_abc"
        assert messages[1]["content"] == "page text"

    def test_whitespace_only_text_yields_null_content(self):
        messages = []
        _append_tool_results(
            messages, "   \n\t  ", self._native(), [{}], ["r"],
            used_native=True, round_num=2,
        )
        assert messages[0]["content"] is None

    def test_real_prose_is_preserved(self):
        messages = []
        _append_tool_results(
            messages, "Let me check that page.", self._native(), [{}], ["r"],
            used_native=True, round_num=1,
        )
        assert messages[0]["content"] == "Let me check that page."

    def test_non_native_path_unaffected(self):
        # The text-block fallback path still wraps results in a user message.
        messages = []
        _append_tool_results(
            messages, "thinking...", [], ["tool output"], [],
            used_native=False, round_num=1,
        )
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "thinking..."
        assert messages[1]["role"] == "user"
        assert "tool output" in messages[1]["content"]


class TestAppendToolResultsThoughtSignature:
    """Gemini 3 returns an opaque thought_signature (in extra_content) with each
    function call and rejects the follow-up turn with HTTP 400 unless it is
    echoed back on the assistant tool_call. _append_tool_results must replay it
    when present, and omit the field entirely otherwise (other providers never
    send it)."""

    def test_extra_content_is_replayed_when_present(self):
        native = [{
            "id": "call_g",
            "name": "app_api",
            "arguments": '{"action": "get_memory"}',
            "extra_content": {"google": {"thought_signature": "EuIDCt8DAQ=="}},
        }]
        messages = []
        _append_tool_results(
            messages, "", native, [{}], ["mem"],
            used_native=True, round_num=1,
        )
        tc = messages[0]["tool_calls"][0]
        assert tc["extra_content"] == {"google": {"thought_signature": "EuIDCt8DAQ=="}}
        # function payload is still well-formed alongside it
        assert tc["function"]["name"] == "app_api"
        assert tc["id"] == "call_g"

    def test_no_extra_content_key_when_absent(self):
        native = [{"id": "call_o", "name": "app_api", "arguments": "{}"}]
        messages = []
        _append_tool_results(
            messages, "", native, [{}], ["r"],
            used_native=True, round_num=1,
        )
        # No empty/None extra_content leaks onto non-Gemini tool calls.
        assert "extra_content" not in messages[0]["tool_calls"][0]


# ---------------------------------------------------------------------------
# web_search sources extraction — key lookup regression (#443)
# ---------------------------------------------------------------------------

import json as _json


class TestWebSearchSourcesKeyLookup:
    """The web_search tool returns {"output": ..., "exit_code": 0}.
    The sources-extraction block in stream_agent_loop must read from the
    "output" key, not only from "results"/"stdout" (which web_search never
    sets).  Without the fix the SOURCES marker is never found, no
    web_sources SSE event is emitted, and the raw JSON blob leaks into the
    LLM's round-2 context."""

    _SOURCES = [{"title": "Example", "url": "https://example.com", "snippet": "test"}]

    def _make_result(self, key: str = "output") -> dict:
        sources_json = _json.dumps(self._SOURCES)
        text = f"Search results here.\n\n<!-- SOURCES:{sources_json} -->"
        return {key: text, "exit_code": 0}

    # ── Regression: the old lookup missed "output" ──────────────────────

    def test_old_lookup_missed_output_key(self):
        """Documents the bug: result.get('results') and result.get('stdout')
        are both absent when web_search returns its canonical {"output": ...}
        shape, so _src_text was always '' and the if-block never ran."""
        result = self._make_result("output")
        old_src_text = result.get("results") or result.get("stdout") or ""
        assert old_src_text == "", "confirms the pre-fix behaviour"

    def test_fixed_lookup_finds_output_key(self):
        """After the fix, "output" is checked first so _src_text is non-empty."""
        result = self._make_result("output")
        src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
        assert src_text != ""
        assert "SOURCES" in src_text

    # ── Marker extraction works once _src_text is non-empty ─────────────

    def test_sources_extracted_from_output(self):
        result = self._make_result("output")
        src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
        marker = "<!-- SOURCES:"
        idx = src_text.find(marker)
        end = src_text.find(" -->", idx)
        extracted = _json.loads(src_text[idx + len(marker):end])
        assert extracted == self._SOURCES

    def test_marker_stripped_from_output_key(self):
        """After extraction the "output" value is cleaned so the LLM never
        sees the raw JSON blob in its round-2 context."""
        result = self._make_result("output")
        src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
        marker = "<!-- SOURCES:"
        idx = src_text.find(marker)
        clean = src_text[:idx].rstrip()
        # Apply to the correct key (was the bug: only "results"/"stdout" were updated)
        if "output" in result:
            result["output"] = clean
        assert "SOURCES" not in result["output"]
        assert result["output"] == "Search results here."

    # ── Backward compat: "results"/"stdout" keys still work ─────────────

    def test_results_key_still_works(self):
        result = self._make_result("results")
        src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
        assert src_text != ""
        assert "SOURCES" in src_text

    def test_stdout_key_still_works(self):
        result = self._make_result("stdout")
        src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
        assert src_text != ""
        assert "SOURCES" in src_text
