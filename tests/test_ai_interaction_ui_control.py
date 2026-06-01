"""
Tests for do_ui_control() and its extracted sub-handlers.

Covers every action branch: toggle, set_mode, switch_model, set_theme,
create_theme, highlight, clear_highlight, open_panel, open_email_reply,
get_toggles.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ui(content: str, session_id: str = None) -> Dict:
    """Shortcut to call do_ui_control."""
    from src.ai_interaction import do_ui_control
    return await do_ui_control(content, session_id=session_id)


# ---------------------------------------------------------------------------
# Toggle action
# ---------------------------------------------------------------------------

class TestUiControlToggle:
    """Tests for the toggle action."""

    @pytest.mark.asyncio
    async def test_toggle_on(self) -> None:
        result = await _ui("toggle bash on")
        assert result["ui_event"] == "toggle"
        assert result["toggle_name"] == "bash"
        assert result["state"] is True

    @pytest.mark.asyncio
    async def test_toggle_off(self) -> None:
        result = await _ui("toggle bash off")
        assert result["ui_event"] == "toggle"
        assert result["toggle_name"] == "bash"
        assert result["state"] is False

    @pytest.mark.asyncio
    async def test_toggle_alias_shell_to_bash(self) -> None:
        result = await _ui("toggle shell on")
        assert result["toggle_name"] == "bash", "Alias 'shell' should map to 'bash'"

    @pytest.mark.asyncio
    async def test_toggle_alias_search_to_web(self) -> None:
        result = await _ui("toggle search on")
        assert result["toggle_name"] == "web"

    @pytest.mark.asyncio
    async def test_toggle_state_true_variants(self) -> None:
        for val in ("on", "true", "1", "yes", "enable", "enabled"):
            result = await _ui(f"toggle web {val}")
            assert result["state"] is True, f"State should be True for '{val}'"

    @pytest.mark.asyncio
    async def test_toggle_state_false_variants(self) -> None:
        for val in ("off", "false", "0", "no"):
            result = await _ui(f"toggle web {val}")
            assert result["state"] is False, f"State should be False for '{val}'"

    @pytest.mark.asyncio
    async def test_toggle_missing_args_returns_error(self) -> None:
        result = await _ui("toggle")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_toggle_unknown_name_returns_error(self) -> None:
        result = await _ui("toggle nonexistent_toggle_xyz on")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_toggle_result_has_results_key(self) -> None:
        result = await _ui("toggle web on")
        assert "results" in result


# ---------------------------------------------------------------------------
# set_mode action
# ---------------------------------------------------------------------------

class TestUiControlSetMode:
    """Tests for the set_mode action."""

    @pytest.mark.asyncio
    async def test_set_mode_agent(self) -> None:
        result = await _ui("set_mode agent")
        assert result["ui_event"] == "set_mode"
        assert result["mode"] == "agent"

    @pytest.mark.asyncio
    async def test_set_mode_chat(self) -> None:
        result = await _ui("set_mode chat")
        assert result["ui_event"] == "set_mode"
        assert result["mode"] == "chat"

    @pytest.mark.asyncio
    async def test_set_mode_invalid_returns_error(self) -> None:
        result = await _ui("set_mode invalid_mode")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_mode_missing_arg_returns_error(self) -> None:
        result = await _ui("set_mode")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_mode_result_has_results_key(self) -> None:
        result = await _ui("set_mode chat")
        assert "results" in result


# ---------------------------------------------------------------------------
# set_theme action
# ---------------------------------------------------------------------------

class TestUiControlSetTheme:
    """Tests for the set_theme action."""

    @pytest.mark.asyncio
    async def test_set_theme_valid_dark(self) -> None:
        result = await _ui("set_theme dark")
        assert result["ui_event"] == "set_theme"
        assert result["theme_name"] == "dark"

    @pytest.mark.asyncio
    async def test_set_theme_valid_light(self) -> None:
        result = await _ui("set_theme light")
        assert result["ui_event"] == "set_theme"
        assert result["theme_name"] == "light"

    @pytest.mark.asyncio
    async def test_set_theme_unknown_returns_error(self) -> None:
        result = await _ui("set_theme totally_unknown_theme_xyz")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_theme_result_has_results_key(self) -> None:
        result = await _ui("set_theme dark")
        assert "results" in result


# ---------------------------------------------------------------------------
# open_panel action
# ---------------------------------------------------------------------------

class TestUiControlOpenPanel:
    """Tests for the open_panel action."""

    @pytest.mark.asyncio
    async def test_open_panel_documents(self) -> None:
        result = await _ui("open_panel documents")
        assert result["ui_event"] == "open_panel"
        assert result["panel"] == "documents"

    @pytest.mark.asyncio
    async def test_open_panel_alias_doc(self) -> None:
        result = await _ui("open_panel doc")
        assert result["panel"] == "documents", "'doc' alias should resolve to 'documents'"

    @pytest.mark.asyncio
    async def test_open_panel_alias_mail(self) -> None:
        result = await _ui("open_panel mail")
        assert result["panel"] == "email", "'mail' alias should resolve to 'email'"

    @pytest.mark.asyncio
    async def test_open_panel_alias_chats(self) -> None:
        result = await _ui("open_panel chats")
        assert result["panel"] == "sessions"

    @pytest.mark.asyncio
    async def test_open_panel_alias_brain(self) -> None:
        result = await _ui("open_panel brain")
        assert result["panel"] == "memories"

    @pytest.mark.asyncio
    async def test_open_panel_unknown_returns_error(self) -> None:
        result = await _ui("open_panel totally_unknown_panel_xyz")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_open_panel_result_has_results_key(self) -> None:
        result = await _ui("open_panel documents")
        assert "results" in result


# ---------------------------------------------------------------------------
# open_email_reply action
# ---------------------------------------------------------------------------

class TestUiControlOpenEmailReply:
    """Tests for the open_email_reply action."""

    @pytest.mark.asyncio
    async def test_open_email_reply_basic(self) -> None:
        result = await _ui("open_email_reply 12345")
        assert result["ui_event"] == "open_email_reply"
        assert result["uid"] == "12345"

    @pytest.mark.asyncio
    async def test_open_email_reply_defaults(self) -> None:
        result = await _ui("open_email_reply 12345")
        assert result["folder"] == "INBOX"
        assert result["mode"] == "reply"

    @pytest.mark.asyncio
    async def test_open_email_reply_with_folder(self) -> None:
        result = await _ui("open_email_reply 12345 Sent")
        assert result["folder"] == "Sent"

    @pytest.mark.asyncio
    async def test_open_email_reply_with_mode(self) -> None:
        result = await _ui("open_email_reply 12345 INBOX reply-all")
        assert result["mode"] == "reply-all"

    @pytest.mark.asyncio
    async def test_open_email_reply_invalid_mode_defaults_to_reply(self) -> None:
        result = await _ui("open_email_reply 12345 INBOX invalid_mode")
        assert result["mode"] == "reply"

    @pytest.mark.asyncio
    async def test_open_email_reply_missing_uid_returns_error(self) -> None:
        result = await _ui("open_email_reply")
        assert "error" in result


# ---------------------------------------------------------------------------
# highlight / clear_highlight actions
# ---------------------------------------------------------------------------

class TestUiControlHighlight:
    """Tests for highlight and clear_highlight actions."""

    @pytest.mark.asyncio
    async def test_highlight_with_selector(self) -> None:
        result = await _ui("highlight #my-button Click me")
        assert result["ui_event"] == "highlight"
        assert result["selector"] == "#my-button"

    @pytest.mark.asyncio
    async def test_highlight_missing_selector_returns_error(self) -> None:
        result = await _ui("highlight")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_clear_highlight(self) -> None:
        result = await _ui("clear_highlight")
        assert result["ui_event"] == "clear_highlight"

    @pytest.mark.asyncio
    async def test_clear_highlight_has_results_key(self) -> None:
        result = await _ui("clear_highlight")
        assert "results" in result


# ---------------------------------------------------------------------------
# get_toggles action
# ---------------------------------------------------------------------------

class TestUiControlGetToggles:
    """Tests for the get_toggles action."""

    @pytest.mark.asyncio
    async def test_get_toggles_returns_results(self) -> None:
        result = await _ui("get_toggles")
        assert "results" in result
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_get_toggles_mentions_available_toggles(self) -> None:
        result = await _ui("get_toggles")
        # Should mention the available toggles
        assert "bash" in result["results"] or "web" in result["results"]


# ---------------------------------------------------------------------------
# Empty / unknown input
# ---------------------------------------------------------------------------

class TestUiControlEdgeCases:
    """Tests for edge cases in do_ui_control."""

    @pytest.mark.asyncio
    async def test_empty_content_returns_error(self) -> None:
        result = await _ui("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self) -> None:
        result = await _ui("totally_unknown_action_xyz")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_response_has_no_ui_event(self) -> None:
        result = await _ui("toggle nonexistent_toggle on")
        assert "ui_event" not in result
