"""Tests for src/prompt_budget.py — slim_tool_schema, apply_slim_schemas, budget_for_context."""

import pytest
from src.prompt_budget import (
    slim_tool_schema,
    apply_slim_schemas,
    budget_for_context,
    SMALL_CONTEXT_THRESHOLD,
    TINY_CONTEXT_THRESHOLD,
)


# ---------------------------------------------------------------------------
# slim_tool_schema
# ---------------------------------------------------------------------------

def _make_schema(name, description, required=None, optional_props=None):
    """Helper: build a minimal OpenAI function-tool schema."""
    props = {}
    if required:
        for k in required:
            props[k] = {"type": "string", "description": f"{k} param", "enum": ["a", "b"]}
    if optional_props:
        for k in optional_props:
            props[k] = {"type": "integer", "description": f"optional {k}"}
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
            },
        },
    }
    return schema


class TestSlimToolSchema:
    def test_preserves_name(self):
        s = slim_tool_schema(_make_schema("bash", "Run shell"))
        assert s["function"]["name"] == "bash"

    def test_preserves_description(self):
        s = slim_tool_schema(_make_schema("bash", "Run shell command"))
        assert s["function"]["description"] == "Run shell command"

    def test_preserves_type(self):
        s = slim_tool_schema(_make_schema("bash", "Run shell"))
        assert s["type"] == "function"

    def test_required_params_kept_as_stubs(self):
        s = slim_tool_schema(_make_schema("web_search", "Search", required=["query"]))
        params = s["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params["required"]

    def test_required_param_type_normalized_to_string(self):
        # Enums and detailed types are stripped; stubs are plain strings
        s = slim_tool_schema(_make_schema("web_search", "Search", required=["query"]))
        assert s["function"]["parameters"]["properties"]["query"] == {"type": "string"}

    def test_optional_params_stripped(self):
        s = slim_tool_schema(_make_schema(
            "bash", "Run shell",
            required=["command"],
            optional_props=["timeout", "cwd"],
        ))
        props = s["function"]["parameters"]["properties"]
        assert "command" in props
        assert "timeout" not in props
        assert "cwd" not in props

    def test_no_params_omits_parameters_key(self):
        s = slim_tool_schema(_make_schema("list_models", "List models"))
        assert "parameters" not in s["function"]

    def test_empty_required_omits_parameters_key(self):
        schema = {
            "type": "function",
            "function": {
                "name": "list_models",
                "description": "List models",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        s = slim_tool_schema(schema)
        assert "parameters" not in s["function"]

    def test_passthrough_if_no_function_key(self):
        bad = {"type": "unknown"}
        assert slim_tool_schema(bad) is bad

    def test_multiple_required_params(self):
        s = slim_tool_schema(_make_schema(
            "send_email", "Send email",
            required=["to", "subject", "body"],
        ))
        params = s["function"]["parameters"]
        assert set(params["required"]) == {"to", "subject", "body"}
        assert set(params["properties"].keys()) == {"to", "subject", "body"}


# ---------------------------------------------------------------------------
# apply_slim_schemas
# ---------------------------------------------------------------------------

class TestApplySlimSchemas:
    def _schemas(self, names):
        return [_make_schema(n, f"{n} desc", required=["arg"]) for n in names]

    def test_large_context_unchanged(self):
        schemas = self._schemas(["bash", "python", "web_search"])
        result = apply_slim_schemas(schemas, context_length=128000)
        # Identical objects — no transformation
        assert result == schemas

    def test_zero_context_unchanged(self):
        schemas = self._schemas(["bash", "web_search"])
        result = apply_slim_schemas(schemas, context_length=0)
        assert result == schemas

    def test_tiny_context_returns_empty(self):
        schemas = self._schemas(["bash", "python", "web_search"])
        result = apply_slim_schemas(schemas, context_length=4096)
        assert result == []

    def test_tiny_context_boundary(self):
        schemas = self._schemas(["bash"])
        assert apply_slim_schemas(schemas, TINY_CONTEXT_THRESHOLD) == []

    def test_small_context_slims_schemas(self):
        schemas = [_make_schema("web_search", "Search", required=["query"], optional_props=["time_filter"])]
        result = apply_slim_schemas(schemas, context_length=12000)
        assert len(result) == 1
        # Optional param should be gone
        props = result[0]["function"]["parameters"]["properties"]
        assert "time_filter" not in props

    def test_small_context_always_full_tools_unslimmed(self):
        bash_schema = _make_schema("bash", "Run shell", required=["command"], optional_props=["cwd"])
        result = apply_slim_schemas([bash_schema], context_length=12000)
        # bash is in _ALWAYS_FULL_SCHEMA_TOOLS → returned as-is
        assert result[0] is bash_schema

    def test_small_context_boundary(self):
        schemas = [_make_schema("web_search", "Search", required=["query"])]
        # Exactly at threshold → slim mode
        result = apply_slim_schemas(schemas, SMALL_CONTEXT_THRESHOLD)
        assert len(result) == 1
        # Should be slimmed (no optional extras, just required stub)
        assert result[0] is not schemas[0]

    def test_just_above_threshold_unchanged(self):
        schemas = [_make_schema("web_search", "Search", required=["query"])]
        result = apply_slim_schemas(schemas, SMALL_CONTEXT_THRESHOLD + 1)
        assert result is schemas

    def test_custom_always_full(self):
        w = _make_schema("web_search", "Search", required=["query"])
        result = apply_slim_schemas([w], context_length=12000, always_full=frozenset({"web_search"}))
        assert result[0] is w

    def test_empty_schemas_list(self):
        assert apply_slim_schemas([], context_length=12000) == []


# ---------------------------------------------------------------------------
# budget_for_context
# ---------------------------------------------------------------------------

class TestBudgetForContext:
    def test_large_context_no_override(self):
        b = budget_for_context(128000)
        assert b["skill_max_injected"] is None
        assert b["doc_max_chars"] is None
        assert b["use_compact_prompt"] is False

    def test_zero_context_no_override(self):
        b = budget_for_context(0)
        assert b["skill_max_injected"] is None
        assert b["use_compact_prompt"] is False

    def test_tiny_context_zeroes_skills_and_docs(self):
        b = budget_for_context(4096)
        assert b["skill_max_injected"] == 0
        assert b["doc_max_chars"] == 0
        assert b["use_compact_prompt"] is True

    def test_tiny_context_boundary(self):
        b = budget_for_context(TINY_CONTEXT_THRESHOLD)
        assert b["skill_max_injected"] == 0
        assert b["doc_max_chars"] == 0

    def test_small_context_limits(self):
        b = budget_for_context(12000)
        assert b["skill_max_injected"] == 1
        assert b["doc_max_chars"] == 1500
        assert b["use_compact_prompt"] is True

    def test_small_context_boundary(self):
        b = budget_for_context(SMALL_CONTEXT_THRESHOLD)
        assert b["skill_max_injected"] == 1
        assert b["use_compact_prompt"] is True

    def test_just_above_small_threshold(self):
        b = budget_for_context(SMALL_CONTEXT_THRESHOLD + 1)
        assert b["skill_max_injected"] is None
        assert b["use_compact_prompt"] is False
