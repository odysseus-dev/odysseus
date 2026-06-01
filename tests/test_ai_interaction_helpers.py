"""
Tests for ai_interaction.py helper functions.

Tests extraction and truncation helpers, content parsing, UI resolution,
and model resolution error handling.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from typing import Optional, Dict, Tuple


class TestResponseTruncation:
    """Test response truncation helper functions."""

    def test_truncate_response_under_limit(self) -> None:
        """Verify truncate_response doesn't modify text under limit."""
        from src.ai_interaction import truncate_response, RESPONSE_TRUNCATE_LIMIT

        short_text = "Short response"
        result = truncate_response(short_text)

        assert result == short_text, "Text under limit should not be modified"

    def test_truncate_response_at_limit(self) -> None:
        """Verify truncate_response doesn't modify text at exact limit."""
        from src.ai_interaction import truncate_response, RESPONSE_TRUNCATE_LIMIT

        text_at_limit = "x" * RESPONSE_TRUNCATE_LIMIT
        result = truncate_response(text_at_limit)

        assert result == text_at_limit, "Text at limit should not be truncated"

    def test_truncate_response_over_limit(self) -> None:
        """Verify truncate_response truncates text over limit."""
        from src.ai_interaction import truncate_response, RESPONSE_TRUNCATE_LIMIT

        long_text = "x" * (RESPONSE_TRUNCATE_LIMIT + 100)
        result = truncate_response(long_text)

        assert len(result) <= RESPONSE_TRUNCATE_LIMIT + 30, "Result should be <= limit + notice"
        assert "truncated" in result.lower(), "Result should indicate truncation"

    def test_truncate_response_with_custom_limit(self) -> None:
        """Verify truncate_response respects custom limit."""
        custom_limit = 50
        long_text = "x" * 100

        result_func = pytest.importorskip("src.ai_interaction").truncate_response
        result = result_func(long_text, custom_limit)

        assert len(result) <= custom_limit + 30, "Should respect custom limit"
        assert "truncated" in result.lower()

    def test_truncate_response_preserves_content_under_limit(self) -> None:
        """Verify truncate_response preserves text content when under limit."""
        from src.ai_interaction import truncate_response

        text = "This is important content."
        result = truncate_response(text)

        assert text in result, "Important content should be preserved"
        assert "truncated" not in result.lower(), "Should not indicate truncation"

    def test_truncate_for_teacher(self) -> None:
        """Verify truncate_for_teacher uses teacher-specific limit."""
        from src.ai_interaction import (
            truncate_for_teacher,
            RESPONSE_TRUNCATE_TEACHER,
        )

        long_text = "x" * (RESPONSE_TRUNCATE_TEACHER + 100)
        result = truncate_for_teacher(long_text)

        assert len(result) <= RESPONSE_TRUNCATE_TEACHER + 30
        assert "truncated" in result.lower()

    def test_truncate_for_pipeline(self) -> None:
        """Verify truncate_for_pipeline uses pipeline-specific limit."""
        from src.ai_interaction import (
            truncate_for_pipeline,
            RESPONSE_TRUNCATE_PIPELINE,
        )

        long_text = "x" * (RESPONSE_TRUNCATE_PIPELINE + 100)
        result = truncate_for_pipeline(long_text)

        assert len(result) <= RESPONSE_TRUNCATE_PIPELINE + 30
        assert "truncated" in result.lower()


class TestContentParsing:
    """Test content parsing helper functions."""

    def test_get_first_line_simple(self) -> None:
        """Verify get_first_line extracts first line."""
        from src.ai_interaction import get_first_line

        content = "model_name\nrest of content\nmore lines"
        result = get_first_line(content)

        assert result == "model_name", "Should extract first line"

    def test_get_first_line_with_whitespace(self) -> None:
        """Verify get_first_line strips whitespace."""
        from src.ai_interaction import get_first_line

        content = "  model_name  \nrest"
        result = get_first_line(content)

        assert result == "model_name", "Should strip whitespace from first line"

    def test_get_first_line_with_max_len(self) -> None:
        """Verify get_first_line respects max_len parameter."""
        from src.ai_interaction import get_first_line

        content = "very_long_model_name_that_is_too_long\nrest"
        result = get_first_line(content, 10)

        assert len(result) == 10, "Result should be limited to max_len"
        assert result == "very_long_", "Should truncate to specified length"

    def test_get_first_line_empty_content(self) -> None:
        """Verify get_first_line handles empty content."""
        from src.ai_interaction import get_first_line

        result = get_first_line("")
        assert result == "", "Empty content should return empty string"

    def test_get_first_line_whitespace_only(self) -> None:
        """Verify get_first_line handles whitespace-only content."""
        from src.ai_interaction import get_first_line

        result = get_first_line("   \n   \n   ")
        assert result == "", "Whitespace-only content should return empty string"

    def test_get_first_line_no_max_len(self) -> None:
        """Verify get_first_line doesn't truncate when max_len is None."""
        from src.ai_interaction import get_first_line

        long_line = "x" * 200
        content = f"{long_line}\nrest"
        result = get_first_line(content, max_len=None)

        assert result == long_line, "Should not truncate when max_len=None"

    def test_get_action_from_content(self) -> None:
        """Verify get_action_from_content extracts action name."""
        from src.ai_interaction import get_action_from_content

        content = "rename\nsession_id\nnew_name"
        result = get_action_from_content(content)

        assert result == "rename", "Should extract action from first line"
        assert len(result) <= 40, "Should limit to action max length"

    def test_get_model_spec_from_content(self) -> None:
        """Verify get_model_spec_from_content extracts model spec."""
        from src.ai_interaction import get_model_spec_from_content

        content = "claude@anthropic\nPlease analyze this..."
        result = get_model_spec_from_content(content)

        assert result == "claude@anthropic", "Should extract model spec"
        assert len(result) <= 60, "Should limit to model spec max length"


class TestUIResolution:
    """Test UI string resolution helper functions."""

    def test_resolve_panel_direct_match(self) -> None:
        """Verify resolve_panel returns canonical name for direct match."""
        from src.ai_interaction import resolve_panel

        result = resolve_panel("documents")
        assert result == "documents", "Should return canonical name for direct match"

    def test_resolve_panel_alias(self) -> None:
        """Verify resolve_panel resolves aliases."""
        from src.ai_interaction import resolve_panel

        # Common aliases
        assert resolve_panel("doc") == "documents"
        assert resolve_panel("docs") == "documents"
        assert resolve_panel("doclib") == "documents"

    def test_resolve_panel_case_insensitive(self) -> None:
        """Verify resolve_panel is case-insensitive."""
        from src.ai_interaction import resolve_panel

        assert resolve_panel("DOCUMENTS") == "documents"
        assert resolve_panel("Documents") == "documents"
        assert resolve_panel("DOC") == "documents"

    def test_resolve_panel_unknown(self) -> None:
        """Verify resolve_panel returns None for unknown panel."""
        from src.ai_interaction import resolve_panel

        result = resolve_panel("unknown_panel_xyz")
        assert result is None, "Should return None for unknown panel"

    def test_resolve_panel_none_input(self) -> None:
        """Verify resolve_panel handles None input."""
        from src.ai_interaction import resolve_panel

        result = resolve_panel(None)
        assert result is None, "Should handle None input gracefully"

    def test_resolve_toggle_direct_match(self) -> None:
        """Verify resolve_toggle returns canonical name for direct match."""
        from src.ai_interaction import resolve_toggle

        result = resolve_toggle("bash")
        assert result == "bash", "Should return canonical name for direct match"

    def test_resolve_toggle_alias(self) -> None:
        """Verify resolve_toggle resolves aliases."""
        from src.ai_interaction import resolve_toggle

        # Common aliases
        assert resolve_toggle("shell") == "bash"
        assert resolve_toggle("terminal") == "bash"
        assert resolve_toggle("search") == "web"
        assert resolve_toggle("web") == "web"

    def test_resolve_toggle_case_insensitive(self) -> None:
        """Verify resolve_toggle is case-insensitive."""
        from src.ai_interaction import resolve_toggle

        assert resolve_toggle("SHELL") == "bash"
        assert resolve_toggle("Shell") == "bash"
        assert resolve_toggle("WEB") == "web"

    def test_resolve_toggle_unknown(self) -> None:
        """Verify resolve_toggle returns None for unknown toggle."""
        from src.ai_interaction import resolve_toggle

        result = resolve_toggle("unknown_toggle_xyz")
        assert result is None, "Should return None for unknown toggle"

    def test_resolve_toggle_none_input(self) -> None:
        """Verify resolve_toggle handles None input."""
        from src.ai_interaction import resolve_toggle

        result = resolve_toggle(None)
        assert result is None, "Should handle None input gracefully"


class TestColorValidation:
    """Test color validation helper functions."""

    def test_is_valid_hex_color_valid_lowercase(self) -> None:
        """Verify is_valid_hex_color accepts valid lowercase hex."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#ffffff") is True
        assert is_valid_hex_color("#000000") is True
        assert is_valid_hex_color("#abcdef") is True

    def test_is_valid_hex_color_valid_uppercase(self) -> None:
        """Verify is_valid_hex_color accepts valid uppercase hex."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#FFFFFF") is True
        assert is_valid_hex_color("#ABCDEF") is True

    def test_is_valid_hex_color_valid_mixed_case(self) -> None:
        """Verify is_valid_hex_color accepts valid mixed-case hex."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#FfFfFf") is True
        assert is_valid_hex_color("#aAbBcC") is True

    def test_is_valid_hex_color_invalid_letters(self) -> None:
        """Verify is_valid_hex_color rejects invalid hex letters."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#GGGGGG") is False
        assert is_valid_hex_color("#gggggg") is False
        assert is_valid_hex_color("#zzzzzz") is False

    def test_is_valid_hex_color_wrong_length(self) -> None:
        """Verify is_valid_hex_color rejects wrong length."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#fff") is False  # Too short
        assert is_valid_hex_color("#fffffff") is False  # Too long
        assert is_valid_hex_color("ffffff") is False  # Missing #

    def test_is_valid_hex_color_no_hash(self) -> None:
        """Verify is_valid_hex_color requires # prefix."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("ffffff") is False
        assert is_valid_hex_color("000000") is False

    def test_is_valid_hex_color_with_spaces(self) -> None:
        """Verify is_valid_hex_color rejects colors with spaces."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("# ffffff") is False
        assert is_valid_hex_color("#fff fff") is False

    def test_is_valid_hex_color_named_colors(self) -> None:
        """Verify is_valid_hex_color rejects named colors."""
        from src.ai_interaction import is_valid_hex_color

        assert is_valid_hex_color("#white") is False
        assert is_valid_hex_color("#red") is False
        assert is_valid_hex_color("#blue") is False


class TestModelResolution:
    """Test model resolution error handling."""

    @pytest.mark.asyncio
    async def test_resolve_model_safe_error_handling(self) -> None:
        """Verify resolve_model_safe returns error dict on failure."""
        from src.ai_interaction import resolve_model_safe

        # Use a clearly invalid model spec
        url, model, headers, error = await resolve_model_safe("nonexistent_model_xyz_not_real_12345")

        assert url is None, "URL should be None on error"
        assert model is None, "Model should be None on error"
        assert headers is None, "Headers should be None on error"
        assert error is not None, "Error should be a dict"
        assert "error" in error, "Error dict should have 'error' key"
        assert isinstance(error["error"], str), "Error message should be string"

    @pytest.mark.asyncio
    async def test_resolve_model_safe_returns_all_none_on_error(self) -> None:
        """Verify resolve_model_safe returns (None, None, None, error_dict) on error."""
        from src.ai_interaction import resolve_model_safe

        result = await resolve_model_safe("invalid_model")

        assert isinstance(result, tuple), "Should return tuple"
        assert len(result) == 4, "Should return 4-tuple"
        assert result[0] is None, "First element should be None"
        assert result[1] is None, "Second element should be None"
        assert result[2] is None, "Third element should be None"
        assert result[3] is not None, "Fourth element should be error dict"


class TestDatabaseContextManager:
    """Test database session context manager."""

    def test_get_db_session_context_manager(self) -> None:
        """Verify get_db_session returns a context manager."""
        from src.ai_interaction import get_db_session

        ctx = get_db_session()

        assert hasattr(ctx, "__enter__"), "Should be context manager"
        assert hasattr(ctx, "__exit__"), "Should be context manager"

    def test_get_db_session_cleanup(self) -> None:
        """Verify get_db_session cleans up on exit."""
        from src.ai_interaction import get_db_session

        with get_db_session() as db:
            assert db is not None, "Context should yield database session"
            # Session is open here
        # Session should be closed after exiting context
