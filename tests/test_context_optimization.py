"""Tests for the Agent Context Optimization PR.

Covers:
  - Schema compression (_first_sentence, _compress_parameters, compress_schemas)
  - Context budget report (context_budget_report)
  - Context compactor thresholds (get_compact_threshold, _protect_recent_for_context)

NOTE: Pure function tests only — no full app context needed.
The agent_loop integration tests (prompt tiers) require full app init and
are deferred to manual verification.
"""
import json
import sys
import os
import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers: import functions in isolation to avoid circular imports ──────

def _import_schema_funcs():
    """Import compression functions without triggering the full tool_schemas
    module-level imports (which cause circular import with agent_tools)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_tool_schemas_funcs",
        os.path.join(os.path.dirname(__file__), "..", "src", "tool_schemas.py"),
        submodule_search_locations=[],
    )
    # We can't use the full module loader because of circular deps.
    # Instead, just exec the function definitions we need.
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "tool_schemas.py")
    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()

    # Extract just the compression functions at the end of the file
    marker = "# ---------------------------------------------------------------------------"
    idx = source.find(marker)
    if idx == -1:
        pytest.skip("Could not find compression functions in tool_schemas.py")

    func_source = source[idx:]
    ns = {}
    exec(func_source, ns)
    return ns


def _import_compactor_funcs():
    """Import compactor functions without full module init."""
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "context_compactor.py")
    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()

    # Extract the constants and the two new functions
    ns = {}
    # Execute just the top-level constant definitions and our new functions
    lines = source.split("\n")
    relevant_lines = []
    in_function = False
    for line in lines:
        # Include constants
        if line.startswith("COMPACT_THRESHOLD") or line.startswith("SUMMARY_MAX_TOKENS") or line.startswith("SMALL_CONTEXT_LIMIT"):
            relevant_lines.append(line)
        # Include our new functions
        elif line.startswith("def get_compact_threshold") or line.startswith("def _protect_recent_for_context"):
            in_function = True
            relevant_lines.append(line)
        elif in_function:
            if line and not line[0].isspace() and not line.startswith("#"):
                in_function = False
            else:
                relevant_lines.append(line)

    exec("\n".join(relevant_lines), ns)
    return ns


# Cache the imports
_schema_ns = None
_compactor_ns = None

def get_schema_ns():
    global _schema_ns
    if _schema_ns is None:
        _schema_ns = _import_schema_funcs()
    return _schema_ns

def get_compactor_ns():
    global _compactor_ns
    if _compactor_ns is None:
        _compactor_ns = _import_compactor_funcs()
    return _compactor_ns


# ── Schema compression ──────────────────────────────────────────────────────

class TestFirstSentence:
    def test_normal_sentence(self):
        fn = get_schema_ns()["_first_sentence"]
        assert fn("Search the web for info. Use this for lookups.") == "Search the web for info."

    def test_no_period(self):
        fn = get_schema_ns()["_first_sentence"]
        result = fn("A" * 200)
        assert len(result) <= 121  # 120 + "…"
        assert result.endswith("…")

    def test_empty(self):
        fn = get_schema_ns()["_first_sentence"]
        assert fn("") == ""

    def test_single_sentence(self):
        fn = get_schema_ns()["_first_sentence"]
        assert fn("Hello world.") == "Hello world."

    def test_exclamation(self):
        fn = get_schema_ns()["_first_sentence"]
        assert fn("Do it! Now do more.") == "Do it!"

    def test_question(self):
        fn = get_schema_ns()["_first_sentence"]
        assert fn("What is this? More text.") == "What is this?"


class TestCompressParameters:
    def test_flatten_nested_object(self):
        fn = get_schema_ns()["_compress_parameters"]
        params = {
            "type": "object",
            "properties": {
                "colors": {
                    "type": "object",
                    "description": "Theme colors",
                    "properties": {
                        "bg": {"type": "string"},
                        "fg": {"type": "string"},
                    },
                },
            },
        }
        result = fn(params)
        colors = result["properties"]["colors"]
        assert "properties" not in colors
        assert colors["type"] == "object"
        assert "Theme colors" in colors["description"]

    def test_trim_long_enum(self):
        fn = get_schema_ns()["_compress_parameters"]
        params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["a", "b", "c", "d", "e", "f", "g", "h"],
                    "description": "Action to take",
                },
            },
        }
        result = fn(params)
        action = result["properties"]["action"]
        assert len(action["enum"]) == 5
        assert "+3 more" in action["description"]

    def test_strip_optional_descriptions(self):
        fn = get_schema_ns()["_compress_parameters"]
        params = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Required name"},
                "color": {"type": "string", "description": "Optional color"},
            },
        }
        result = fn(params, strip_descriptions=True)
        assert "description" in result["properties"]["name"]
        assert "description" not in result["properties"]["color"]

    def test_preserves_required_descriptions(self):
        fn = get_schema_ns()["_compress_parameters"]
        params = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "The name"},
            },
        }
        result = fn(params, strip_descriptions=True)
        assert result["properties"]["name"]["description"] == "The name"

    def test_flatten_array_of_objects(self):
        fn = get_schema_ns()["_compress_parameters"]
        params = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "done": {"type": "boolean"},
                        },
                    },
                },
            },
        }
        result = fn(params)
        items_prop = result["properties"]["items"]
        assert items_prop["type"] == "array"
        assert items_prop["items"] == {"type": "object"}


class TestCompressSchemas:
    def _make_schema(self, name="test_tool", desc="Do something. With many details.", params=None):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": params or {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {"type": "string", "description": "The action"},
                        "optional_param": {"type": "string", "description": "Some optional description that is long"},
                        "nested": {
                            "type": "object",
                            "description": "Nested obj",
                            "properties": {"inner": {"type": "string"}},
                        },
                    },
                },
            },
        }

    def test_no_compression_for_large_context(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=65536)
        assert result == schemas

    def test_no_compression_for_zero_context(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=0)
        assert result == schemas

    def test_micro_strips_optional_descriptions(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=4096)
        props = result[0]["function"]["parameters"]["properties"]
        assert "description" in props["action"]  # required
        assert "description" not in props["optional_param"]  # optional

    def test_micro_shortens_function_description(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=4096)
        assert result[0]["function"]["description"] == "Do something."

    def test_small_keeps_optional_descriptions(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=16384)
        props = result[0]["function"]["parameters"]["properties"]
        assert "description" in props["optional_param"]

    def test_medium_flattens_nested(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema()]
        result = fn(schemas, context_length=24000)
        nested = result[0]["function"]["parameters"]["properties"]["nested"]
        assert "properties" not in nested
        assert nested["type"] == "object"

    def test_reduces_token_count(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema(f"tool_{i}") for i in range(8)]
        original = json.dumps(schemas)
        compressed = json.dumps(fn(schemas, context_length=8192))
        assert len(compressed) < len(original) * 0.85  # at least 15% smaller

    def test_preserves_function_names(self):
        fn = get_schema_ns()["compress_schemas"]
        schemas = [self._make_schema(f"tool_{i}") for i in range(5)]
        result = fn(schemas, context_length=4096)
        names = [s["function"]["name"] for s in result]
        assert names == [f"tool_{i}" for i in range(5)]


# ── Context budget report ────────────────────────────────────────────────────

class TestContextBudgetReport:
    def test_basic_report(self):
        from src.context_budget import context_budget_report
        report = context_budget_report(
            system_tokens=2000, schema_tokens=1500,
            history_tokens=500, context_length=8192,
        )
        assert report["total_tokens"] == 4000
        assert report["tokens_available"] == 4192
        assert report["percent_used"] == pytest.approx(48.8, abs=0.1)
        assert report["warning"] is False
        assert report["tier"] == "micro"

    def test_warning_when_over_threshold(self):
        from src.context_budget import context_budget_report
        report = context_budget_report(
            system_tokens=5000, schema_tokens=3000,
            history_tokens=2000, context_length=10000,
        )
        assert report["warning"] is True

    def test_tier_classification(self):
        from src.context_budget import context_budget_report
        assert context_budget_report(100, 100, 100, 4096)["tier"] == "micro"
        assert context_budget_report(100, 100, 100, 8192)["tier"] == "micro"
        assert context_budget_report(100, 100, 100, 16384)["tier"] == "small"
        assert context_budget_report(100, 100, 100, 32768)["tier"] == "medium"
        assert context_budget_report(100, 100, 100, 65536)["tier"] == "large"
        assert context_budget_report(100, 100, 100, 131072)["tier"] == "large"

    def test_zero_context_length(self):
        from src.context_budget import context_budget_report
        report = context_budget_report(100, 100, 100, 0)
        assert report["percent_used"] == 0
        assert report["warning"] is False


# ── Context compactor thresholds ──────────────────────────────────────────────

class TestCompactThreshold:
    def test_micro_threshold(self):
        fn = get_compactor_ns()["get_compact_threshold"]
        assert fn(4096) == 0.60
        assert fn(8192) == 0.60

    def test_small_threshold(self):
        fn = get_compactor_ns()["get_compact_threshold"]
        assert fn(12000) == 0.70
        assert fn(16384) == 0.70

    def test_medium_threshold(self):
        fn = get_compactor_ns()["get_compact_threshold"]
        assert fn(24000) == 0.75
        assert fn(32768) == 0.75

    def test_large_threshold(self):
        fn = get_compactor_ns()["get_compact_threshold"]
        assert fn(65536) == 0.85
        assert fn(131072) == 0.85


class TestProtectRecent:
    def test_micro_protect(self):
        fn = get_compactor_ns()["_protect_recent_for_context"]
        assert fn(4096) == 4
        assert fn(8192) == 4

    def test_small_protect(self):
        fn = get_compactor_ns()["_protect_recent_for_context"]
        assert fn(16384) == 6

    def test_medium_protect(self):
        fn = get_compactor_ns()["_protect_recent_for_context"]
        assert fn(32768) == 8

    def test_large_protect(self):
        fn = get_compactor_ns()["_protect_recent_for_context"]
        assert fn(65536) == 10
        assert fn(131072) == 10
