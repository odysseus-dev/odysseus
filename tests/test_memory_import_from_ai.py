"""Tests for the Import from AI memory parsing helpers.

Exercises _strip_list_prefix and _parse_ai_response from
routes/memory_routes.py without booting FastAPI or touching the DB.
"""
import os
import sys
import types
from unittest.mock import MagicMock


def _ensure_stub(name: str, **attrs):
    """Stub a module without replacing real ones already loaded.
    Same pattern used by test_auth_regressions.py."""
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        if parent_name not in sys.modules:
            parent = types.ModuleType(parent_name)
            real_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                *parent_name.split("."),
            )
            parent.__path__ = [real_path] if os.path.isdir(real_path) else []
            sys.modules[parent_name] = parent
        else:
            parent = sys.modules[parent_name]
    else:
        parent = None
        child_name = None

    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    if parent is not None and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod


_ensure_stub("core.database",
    SessionLocal=MagicMock(), ModelEndpoint=MagicMock(),
    Session=MagicMock(), ChatMessage=MagicMock(),
    Document=MagicMock(), DocumentVersion=MagicMock(),
    GalleryImage=MagicMock(), GalleryAlbum=MagicMock(), Note=MagicMock(),
    CalendarCal=MagicMock(), CalendarEvent=MagicMock(),
    ScheduledTask=MagicMock(), TaskRun=MagicMock(), McpServer=MagicMock(),
)
_ensure_stub("core.session_manager", SessionManager=MagicMock())
_ensure_stub("services.memory", MemoryManager=MagicMock())
_ensure_stub("services.memory.memory_extractor", audit_memories=MagicMock())

from routes.memory_routes import (
    _strip_list_prefix,
    _parse_ai_response,
    _MEMORY_CATEGORIES,
    _VALID_CATEGORIES,
)


class TestStripListPrefix:
    def test_strips_numbered_dot(self):
        assert _strip_list_prefix("1. hello") == "hello"

    def test_strips_numbered_paren(self):
        assert _strip_list_prefix("2) hello") == "hello"

    def test_strips_numbered_colon(self):
        assert _strip_list_prefix("3: hello") == "hello"

    def test_strips_bullet_dash(self):
        assert _strip_list_prefix("- hello") == "hello"

    def test_strips_bullet_star(self):
        assert _strip_list_prefix("* hello") == "hello"

    def test_strips_bullet_dot(self):
        assert _strip_list_prefix("• hello") == "hello"

    def test_no_prefix_unchanged(self):
        assert _strip_list_prefix("hello world") == "hello world"

    def test_empty_string(self):
        assert _strip_list_prefix("") == ""

    def test_strips_leading_whitespace_before_prefix(self):
        assert _strip_list_prefix("  1. hello") == "hello"

    def test_strips_two_digit_number(self):
        assert _strip_list_prefix("12. I prefer dark mode") == "I prefer dark mode"

    def test_strips_only_one_prefix(self):
        assert _strip_list_prefix("1. 2. nested") == "2. nested"


class TestCategories:
    def test_valid_categories_derived_from_memory_categories(self):
        assert _VALID_CATEGORIES == set(_MEMORY_CATEGORIES)

    def test_memory_categories_contains_expected_values(self):
        for cat in ("identity", "preference", "fact", "contact", "project", "goal", "task"):
            assert cat in _MEMORY_CATEGORIES


class TestParseAiResponse:
    def test_parses_json_array_of_objects(self):
        raw = '[{"text": "I prefer dark mode", "category": "preference"}]'
        result = _parse_ai_response(raw)
        assert result == [{"text": "I prefer dark mode", "category": "preference"}]

    def test_parses_json_array_of_strings(self):
        raw = '["I am a developer", "I live in Berlin"]'
        result = _parse_ai_response(raw)
        assert len(result) == 2
        assert result[0]["text"] == "I am a developer"
        assert result[0]["category"] == "fact"

    def test_strips_backtick_fences(self):
        raw = '```\n[{"text": "I like cats", "category": "preference"}]\n```'
        result = _parse_ai_response(raw)
        assert result == [{"text": "I like cats", "category": "preference"}]

    def test_strips_backtick_json_fences(self):
        raw = '```json\n[{"text": "I like cats", "category": "preference"}]\n```'
        result = _parse_ai_response(raw)
        assert result == [{"text": "I like cats", "category": "preference"}]

    def test_falls_back_to_line_splitting_for_prose(self):
        raw = "I prefer dark mode\nI am a developer\nI live in Berlin"
        result = _parse_ai_response(raw)
        assert len(result) == 3
        assert result[0]["text"] == "I prefer dark mode"
        assert result[0]["category"] == "fact"

    def test_line_splitting_strips_list_prefixes(self):
        raw = "1. I prefer dark mode\n2. I live in Berlin"
        result = _parse_ai_response(raw)
        assert result[0]["text"] == "I prefer dark mode"
        assert result[1]["text"] == "I live in Berlin"

    def test_line_splitting_skips_short_lines(self):
        raw = "ok\nI prefer dark mode\nyes"
        result = _parse_ai_response(raw)
        assert len(result) == 1
        assert result[0]["text"] == "I prefer dark mode"

    def test_unknown_category_defaults_to_fact(self):
        raw = '[{"text": "I like cats", "category": "unknown_cat"}]'
        result = _parse_ai_response(raw)
        assert result[0]["category"] == "fact"

    def test_missing_category_defaults_to_fact(self):
        raw = '[{"text": "I like cats"}]'
        result = _parse_ai_response(raw)
        assert result[0]["category"] == "fact"

    def test_empty_text_items_are_skipped(self):
        raw = '[{"text": "", "category": "fact"}, {"text": "I like cats", "category": "preference"}]'
        result = _parse_ai_response(raw)
        assert len(result) == 1
        assert result[0]["text"] == "I like cats"

    def test_empty_input_returns_empty(self):
        assert _parse_ai_response("") == []
        assert _parse_ai_response("   ") == []

    def test_prose_fallback_caps_at_50_items(self):
        raw = "\n".join(f"Memory number {i} is long enough" for i in range(60))
        result = _parse_ai_response(raw)
        assert len(result) == 50

    def test_list_prefix_stripped_inside_json(self):
        raw = '[{"text": "1. I prefer dark mode", "category": "preference"}]'
        result = _parse_ai_response(raw)
        assert result[0]["text"] == "I prefer dark mode"

    def test_all_memory_categories_are_valid(self):
        for cat in _MEMORY_CATEGORIES:
            raw = f'[{{"text": "some memory text here", "category": "{cat}"}}]'
            result = _parse_ai_response(raw)
            assert result[0]["category"] == cat
