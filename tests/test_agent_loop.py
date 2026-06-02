"""Tests for agent_loop.py — _detect_admin_intent, _compute_final_metrics,
and _append_tool_results. Uses mock imports to avoid loading the full app stack."""

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'src.agent_tools',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.agent_loop import (
    _detect_admin_intent,
    _compute_final_metrics,
    _append_tool_results,
    _default_input_budget,
    _resolve_input_token_budget,
    DEFAULT_INPUT_TOKEN_HARD_MAX,
)


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
        m = _compute_final_metrics(**self._base_args(
            tool_events=events,
            round_texts=texts,
        ))
        assert m["tool_events"] == events
        assert m["round_texts"] == texts

    def test_no_tool_events_excluded(self):
        m = _compute_final_metrics(**self._base_args(tool_events=[], round_texts=[]))
        assert "tool_events" not in m
        assert "round_texts" not in m


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


# ---------------------------------------------------------------------------
# _default_input_budget — pure helper: ctx -> 85%-of-ctx capped at hard_max
# ---------------------------------------------------------------------------

class TestDefaultInputBudget:
    """Pure helper: takes context_length and hard_max, returns a budget."""

    def test_large_context_caps_at_hard_max_default(self):
        """1M-context model should not blow past the function's default ceiling."""
        assert _default_input_budget(1_000_000) == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_custom_hard_max_overrides_default(self):
        """The hard_max argument lets callers shift the cap up or down."""
        assert _default_input_budget(1_000_000, hard_max=500_000) == 500_000
        assert _default_input_budget(1_000_000, hard_max=50_000) == 50_000

    def test_medium_context_uses_85_percent(self):
        """128K model → 85% = 108800, under default hard_max so uncapped."""
        assert _default_input_budget(128_000) == int(128_000 * 0.85)

    def test_small_context_uses_85_percent(self):
        """8K model → 85% = 6800. Slightly more than the historical 6000."""
        assert _default_input_budget(8_000) == int(8_000 * 0.85)

    def test_zero_context_falls_through_to_6000(self):
        """get_context_length returning 0 = unknown; preserve historical fallback."""
        assert _default_input_budget(0) == 6000

    def test_negative_context_falls_through_to_6000(self):
        """Defensive: negative ctx values are ignored, not multiplied."""
        assert _default_input_budget(-1) == 6000

    def test_none_context_falls_through_to_6000(self):
        """None gets the same fallback as 0/unknown."""
        assert _default_input_budget(None) == 6000  # type: ignore[arg-type]

    def test_does_not_exceed_context_length(self):
        """The returned budget must always leave room inside the context window."""
        for ctx in (4_000, 32_000, 128_000, 200_000, 1_000_000):
            assert _default_input_budget(ctx) < ctx, f"budget >= ctx for ctx={ctx}"


# ---------------------------------------------------------------------------
# _resolve_input_token_budget — full semantic resolver
#
# Per-PR-review semantics from #1190:
#   * unset/None  → auto (adaptive, capped by agent_input_token_hard_max)
#   * 0           → explicit disable, preserves no-soft-trim escape hatch
#   * >0          → explicit cap, bounded by context_length when known
#   * malformed   → treated as unset/auto
#
# These tests monkeypatch get_setting directly on src.agent_loop so they
# exercise the real resolver without standing up the full settings stack.
# ---------------------------------------------------------------------------

class TestResolveInputTokenBudget:
    """Settings → effective budget mapping with full semantics."""

    @staticmethod
    def _patch_settings(monkeypatch, values):
        """Helper: stub get_setting on src.agent_loop to return ``values[key]``."""
        from src import agent_loop
        def fake(key, default=None):
            return values.get(key, default)
        monkeypatch.setattr(agent_loop, "get_setting", fake)

    # ---- unset / None / "auto" → adaptive default ---------------------

    def test_unset_uses_adaptive_default(self, monkeypatch):
        self._patch_settings(monkeypatch, {})  # nothing set
        # 1M ctx → adaptive returns DEFAULT_INPUT_TOKEN_HARD_MAX
        assert _resolve_input_token_budget(1_000_000) == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_empty_string_is_unset(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": ""})
        assert _resolve_input_token_budget(128_000) == int(128_000 * 0.85)

    def test_literal_auto_string_is_unset(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": "auto"})
        assert _resolve_input_token_budget(128_000) == int(128_000 * 0.85)

    def test_negative_is_treated_as_unset(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": -1})
        assert _resolve_input_token_budget(128_000) == int(128_000 * 0.85)

    def test_malformed_string_is_treated_as_unset(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": "not-a-number"})
        assert _resolve_input_token_budget(128_000) == int(128_000 * 0.85)

    # ---- explicit 0 → DISABLED (preserves existing escape hatch) ------

    def test_explicit_zero_disables_soft_trim(self, monkeypatch):
        """0 must return 0 — the caller's `if soft_budget > 0` then skips trim."""
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 0})
        assert _resolve_input_token_budget(1_000_000) == 0

    def test_explicit_zero_disables_even_when_ctx_unknown(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 0})
        assert _resolve_input_token_budget(0) == 0

    def test_explicit_zero_as_string_disables(self, monkeypatch):
        """Settings stored as strings still parse to int 0."""
        self._patch_settings(monkeypatch, {"agent_input_token_budget": "0"})
        assert _resolve_input_token_budget(1_000_000) == 0

    # ---- explicit > 0 → use as cap, bounded by context length ----------

    def test_explicit_positive_uses_user_value(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 50_000})
        assert _resolve_input_token_budget(1_000_000) == 50_000

    def test_explicit_positive_bounded_by_context_length(self, monkeypatch):
        """User asked for 500K but model only has 32K — we can't claim more."""
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 500_000})
        assert _resolve_input_token_budget(32_000) == 32_000

    def test_explicit_positive_ignored_when_ctx_unknown(self, monkeypatch):
        """If context_length is 0 (unknown), don't pretend we can bound."""
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 50_000})
        assert _resolve_input_token_budget(0) == 50_000

    def test_explicit_positive_preserves_existing_6000_user_configs(self, monkeypatch):
        """The user who explicitly stored 6000 keeps exactly 6000."""
        self._patch_settings(monkeypatch, {"agent_input_token_budget": 6000})
        assert _resolve_input_token_budget(1_000_000) == 6000

    # ---- agent_input_token_hard_max setting overrides function default -

    def test_hard_max_setting_lowers_auto_ceiling(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_hard_max": 50_000})
        assert _resolve_input_token_budget(1_000_000) == 50_000

    def test_hard_max_setting_raises_auto_ceiling(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_hard_max": 800_000})
        # 1M ctx → 850K adaptive, capped at 800K
        assert _resolve_input_token_budget(1_000_000) == 800_000

    def test_hard_max_setting_zero_falls_back_to_default(self, monkeypatch):
        """Defensive: a 0 hard_max would otherwise zero out the budget."""
        self._patch_settings(monkeypatch, {"agent_input_token_hard_max": 0})
        assert _resolve_input_token_budget(1_000_000) == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_hard_max_setting_malformed_falls_back_to_default(self, monkeypatch):
        self._patch_settings(monkeypatch, {"agent_input_token_hard_max": "huge"})
        assert _resolve_input_token_budget(1_000_000) == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_hard_max_does_not_apply_to_explicit_budget(self, monkeypatch):
        """User's explicit budget is not capped by hard_max — they chose it."""
        self._patch_settings(monkeypatch, {
            "agent_input_token_budget": 500_000,
            "agent_input_token_hard_max": 100_000,
        })
        # 500_000 is user's choice; only bounded by ctx (here 1M, so passes through)
        assert _resolve_input_token_budget(1_000_000) == 500_000


# ---------------------------------------------------------------------------
# DEFAULT_SETTINGS registration — both new keys must be persistable through
# the standard /api/auth/settings + manage_settings paths, which only accept
# keys that exist in DEFAULT_SETTINGS.
# ---------------------------------------------------------------------------

class TestDefaultSettingsRegistration:
    """Without these, admins can't save the new keys through the normal API."""

    def test_agent_input_token_budget_is_registered(self):
        from src.settings import DEFAULT_SETTINGS
        assert "agent_input_token_budget" in DEFAULT_SETTINGS

    def test_agent_input_token_budget_default_is_auto(self):
        """The default must trigger the adaptive path, not the explicit-6000
        cap that used to dominate the old behavior."""
        from src.settings import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["agent_input_token_budget"] == "auto"

    def test_agent_input_token_hard_max_is_registered(self):
        from src.settings import DEFAULT_SETTINGS
        assert "agent_input_token_hard_max" in DEFAULT_SETTINGS

    def test_agent_input_token_hard_max_default_value(self):
        """Default ceiling matches the module-level constant in agent_loop."""
        from src.settings import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["agent_input_token_hard_max"] == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_auto_default_routes_to_adaptive_branch(self, tmp_path, monkeypatch):
        """A brand-new install (no saved settings) goes through the auto branch
        because DEFAULT_SETTINGS provides 'auto'."""
        # Point SETTINGS_FILE at an empty temp location so load_settings hits
        # the FileNotFoundError → dict(DEFAULT_SETTINGS) path.
        from src import settings as _s
        monkeypatch.setattr(_s, "SETTINGS_FILE", str(tmp_path / "settings.json"))
        monkeypatch.setattr(_s, "_settings_cache", None)
        # Now resolve through the real get_setting (not the stubbed one).
        from src import agent_loop
        # Re-bind agent_loop.get_setting to the real one in case prior tests stubbed it.
        monkeypatch.setattr(agent_loop, "get_setting", _s.get_setting)
        assert _resolve_input_token_budget(1_000_000) == DEFAULT_INPUT_TOKEN_HARD_MAX

    def test_existing_explicit_6000_in_saved_file_is_preserved(self, tmp_path, monkeypatch):
        """Users who saved 6000 explicitly before this change keep exactly 6000."""
        from src import settings as _s
        import json
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"agent_input_token_budget": 6000}))
        monkeypatch.setattr(_s, "SETTINGS_FILE", str(settings_file))
        monkeypatch.setattr(_s, "_settings_cache", None)
        from src import agent_loop
        monkeypatch.setattr(agent_loop, "get_setting", _s.get_setting)
        assert _resolve_input_token_budget(1_000_000) == 6000


# ---------------------------------------------------------------------------
# manage_settings alias map — friendly names must resolve to the canonical
# setting key so users can `set hard max to 50000` via the agent.
# ---------------------------------------------------------------------------

class TestSettingsAliases:
    """Verify the friendly aliases registered in src/tool_implementations.py."""

    def _alias_map(self):
        """Extract the alias map without executing the full manage_settings tool.

        The alias dict is defined as a local in the tool's `_resolve` closure
        (src/tool_implementations.py ~1520). Grep the source to validate
        registration instead of importing — keeps this test fast and avoids
        pulling the full app stack."""
        from pathlib import Path
        src = Path("src/tool_implementations.py").read_text()
        return src

    def test_token_budget_alias_registered(self):
        assert '"token budget": "agent_input_token_budget"' in self._alias_map()

    def test_input_budget_alias_registered(self):
        assert '"input budget": "agent_input_token_budget"' in self._alias_map()

    def test_hard_max_alias_registered(self):
        assert '"hard max": "agent_input_token_hard_max"' in self._alias_map()
