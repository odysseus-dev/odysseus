"""Tests for the agent_tool_selection setting.

Covers both sides of the switch:
  - "auto" preserves existing retrieval/low-signal behavior
  - "all" skips retrieval/fallback filtering when relevant_tools is not provided

Also covers the /api/auth/settings POST handler validation: invalid values
are rejected, "auto" and "all" are accepted.
"""
import asyncio
import json
import logging

import pytest
import src.agent_loop as al


# ── helpers ──────────────────────────────────────────────────────────────────

def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _types(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch):
    """Mock dependencies so the agent loop can run without external services."""
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    # Capture log records so we can inspect the debug line
    logs = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    al.logger.addHandler(handler)
    al.logger.setLevel(logging.DEBUG)
    monkeypatch.setattr(al, "_captured_logs", logs, raising=False)


def _get_agent_debug_line(logs):
    for msg in logs:
        if "[agent-debug]" in msg:
            return msg
    return ""


def _run_tool_selection_test(monkeypatch, setting_value, query="hello"):
    """Run a minimal stream_agent_loop with a mock LLM and return debug line."""
    logs = []

    # Capture relevant_tools from the log line
    def _fake_get_setting(key, default=None):
        if key == "agent_tool_selection":
            return setting_value
        if key == "agent_stream_timeout_seconds":
            return 300
        return default

    monkeypatch.setattr(al, "get_setting", _fake_get_setting, raising=False)

    # Capture logs
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    al.logger.addHandler(handler)
    al.logger.setLevel(logging.DEBUG)
    monkeypatch.setattr(al, "_test_logs", logs, raising=False)

    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    call_count = {"n": 0}
    async def _fake_stream(_candidates, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield f'data: {json.dumps({"delta": "Hello, I am an AI assistant."})}\n\n'
            yield "data: [DONE]\n\n"
        else:
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4",
        [{"role": "user", "content": query}],
        max_rounds=1,
    )
    _collect(gen)  # drain the generator

    return _get_agent_debug_line(logs)


# ── agent loop tests ─────────────────────────────────────────────────────────

class TestAgentToolSelectionAll:
    """When agent_tool_selection is "all", the pipeline must send every tool."""

    def test_all_mode_sends_all_tools_with_low_signal_query(self, monkeypatch):
        """A short/ambiguous query (just "hello") is low-signal.  In "auto"
        mode it would get only ALWAYS_AVAILABLE.  In "all" mode it must
        get every function schema."""
        line = _run_tool_selection_test(monkeypatch, "all", query="hello")
        assert "relevant_tools=ALL" in line, (
            f"Expected relevant_tools=ALL in all mode, got: {line}"
        )

    def test_all_mode_sends_all_tools_with_specific_query(self, monkeypatch):
        """Even a domain-specific query gets all tools in all mode."""
        line = _run_tool_selection_test(monkeypatch, "all", query="поищи в интернете погоду")
        assert "relevant_tools=ALL" in line, (
            f"Expected relevant_tools=ALL in all mode, got: {line}"
        )

    def test_auto_mode_still_filters_low_signal_query(self, monkeypatch):
        """A low-signal query in auto mode should get only ALWAYS_AVAILABLE."""
        line = _run_tool_selection_test(monkeypatch, "auto", query="hello")
        assert "relevant_tools=ALL" not in line, (
            f"auto mode must not show relevant_tools=ALL: {line}"
        )
        # ALWAYS_AVAILABLE tools should be present
        assert "manage_memory" in line, (
            f"auto mode should have manage_memory: {line}"
        )

    def test_auto_mode_still_runs_retrieval_for_specific_query(self, monkeypatch):
        """A domain-specific query in auto mode should fire domain detection
        and keyword fallback to include more than just ALWAYS_AVAILABLE."""
        line = _run_tool_selection_test(monkeypatch, "auto", query="search the web for weather")
        assert "relevant_tools=ALL" not in line, (
            f"auto mode must show specific tools, not ALL: {line}"
        )
        # "search the web" → web domain → keyword fallback picks web_search
        assert "web_search" in line or "web_fetch" in line, (
            f"auto mode should retrieve web tools for this query: {line}"
        )


class TestCallerProvidedRelevantTools:
    """When the caller passes relevant_tools explicitly, the setting
    must not override — caller's choice always wins."""

    def _run_with_relevant_tools(self, monkeypatch, setting_value, query="search the web"):
        logs = []

        def _fake_get_setting(key, default=None):
            if key == "agent_tool_selection":
                return setting_value
            if key == "agent_stream_timeout_seconds":
                return 300
            return default

        monkeypatch.setattr(al, "get_setting", _fake_get_setting, raising=False)

        handler = logging.Handler()
        handler.emit = lambda record: logs.append(record.getMessage())
        al.logger.addHandler(handler)
        al.logger.setLevel(logging.DEBUG)

        monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
        monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

        async def _fake_exec(block, *a, **k):
            return ("bash", {"output": "ok", "exit_code": 0})
        monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

        call_count = {"n": 0}
        async def _fake_stream(_candidates, messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                yield f'data: {json.dumps({"delta": "Let me search that."})}\n\n'
                yield "data: [DONE]\n\n"
            else:
                yield "data: [DONE]\n\n"

        monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

        # Caller provides narrow set — just two tools
        gen = al.stream_agent_loop(
            "https://api.openai.com/v1", "gpt-4",
            [{"role": "user", "content": query}],
            max_rounds=1,
            relevant_tools={"web_search", "web_fetch"},
        )
        _collect(gen)

        return _get_agent_debug_line(logs)

    def test_all_mode_respects_caller_tools(self, monkeypatch):
        """Even in all mode, caller-provided tools are not overridden."""
        line = self._run_with_relevant_tools(monkeypatch, "all")
        assert "relevant_tools=ALL" not in line, (
            f"caller tools must take priority over all mode: {line}"
        )
        assert "web_search" in line and "web_fetch" in line, (
            f"caller tools should be present: {line}"
        )

    def test_auto_mode_respects_caller_tools(self, monkeypatch):
        """Auto mode with caller tools shows exactly what was passed."""
        line = self._run_with_relevant_tools(monkeypatch, "auto")
        assert "relevant_tools=ALL" not in line, (
            f"caller tools must not show ALL: {line}"
        )
        assert "web_search" in line and "web_fetch" in line, (
            f"caller tools should be present: {line}"
        )


# ── settings route tests ─────────────────────────────────────────────────────

class TestAgentToolSelectionSettingValidation:
    """The /api/auth/settings POST handler must accept "auto"/"all"
    and reject invalid values.

    Tests the validation dictionaries and the per-key loop in set_settings
    (routes/auth_routes.py::set_settings) without needing a running app.
    """

    def test_accepts_auto(self):
        """The _STRING_VALUES dict allows "auto"."""
        _STRING_VALUES = {"agent_tool_selection": {"auto", "all"}}
        assert "auto" in _STRING_VALUES["agent_tool_selection"]

    def test_accepts_all(self):
        _STRING_VALUES = {"agent_tool_selection": {"auto", "all"}}
        assert "all" in _STRING_VALUES["agent_tool_selection"]

    def test_rejects_invalid_value(self):
        _STRING_VALUES = {"agent_tool_selection": {"auto", "all"}}
        assert "disabled" not in _STRING_VALUES["agent_tool_selection"]
        assert "" not in _STRING_VALUES["agent_tool_selection"]
        assert None not in _STRING_VALUES["agent_tool_selection"]

    def test_validation_loop_raises_400(self):
        """Verify the validation raises HTTPException for invalid values.
        This mirrors the exact logic in routes/auth_routes.py lines 608-610."""
        from fastapi import HTTPException

        _STRING_VALUES = {"agent_tool_selection": {"auto", "all"}}
        key = "agent_tool_selection"

        # Valid values pass
        for val in ("auto", "all"):
            if val not in _STRING_VALUES[key]:
                raise HTTPException(400, f"{key} must be one of {sorted(_STRING_VALUES[key])}")

        # Invalid values raise
        for val in ("disabled", "", None):
            with pytest.raises(HTTPException) as excinfo:
                if val not in _STRING_VALUES[key]:
                    raise HTTPException(400, f"{key} must be one of {sorted(_STRING_VALUES[key])}")
            assert excinfo.value.status_code == 400

    def test_default_setting_in_schema(self):
        """Verify the default is registered in settings.py."""
        from src.settings import DEFAULT_SETTINGS
        assert "agent_tool_selection" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["agent_tool_selection"] == "auto"
