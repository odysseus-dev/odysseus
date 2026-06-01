"""
Tests for dispatch_ai_tool() — the public entry point.

Verifies that every tool name routes to the correct handler and that
the return format (description, result_dict) is always correct.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Dict, Tuple


async def _dispatch(tool: str, content: str = "", session_id: str = "s1", owner: str = "u1") -> Tuple:
    from src.ai_interaction import dispatch_ai_tool
    return await dispatch_ai_tool(tool, content, session_id=session_id, owner=owner)


# ---------------------------------------------------------------------------
# Return format contract
# ---------------------------------------------------------------------------

class TestDispatchReturnFormat:
    """Verify dispatch_ai_tool always returns (str, dict)."""

    @pytest.mark.asyncio
    async def test_returns_tuple(self) -> None:
        with patch("src.ai_interaction.do_list_sessions", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            result = await _dispatch("list_sessions")
        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_description_is_string(self) -> None:
        with patch("src.ai_interaction.do_list_sessions", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            desc, _ = await _dispatch("list_sessions")
        assert isinstance(desc, str)

    @pytest.mark.asyncio
    async def test_result_is_dict(self) -> None:
        with patch("src.ai_interaction.do_list_sessions", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            _, result = await _dispatch("list_sessions")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_dict(self) -> None:
        desc, result = await _dispatch("totally_unknown_tool_xyz")
        assert "error" in result
        assert isinstance(desc, str)


# ---------------------------------------------------------------------------
# Routing — each tool reaches its handler
# ---------------------------------------------------------------------------

class TestDispatchRouting:
    """Verify each tool name dispatches to the correct do_* function."""

    @pytest.mark.asyncio
    async def test_chat_with_model_routes(self) -> None:
        with patch("src.ai_interaction.do_chat_with_model", new_callable=AsyncMock) as m:
            m.return_value = {"response": "hi"}
            desc, _ = await _dispatch("chat_with_model", "model_name\nHello")
        m.assert_called_once()
        assert "chat_with_model" in desc

    @pytest.mark.asyncio
    async def test_create_session_routes(self) -> None:
        with patch("src.ai_interaction.do_create_session", new_callable=AsyncMock) as m:
            m.return_value = {"session_id": "abc"}
            desc, _ = await _dispatch("create_session", "My session\nmodel_name")
        m.assert_called_once()
        assert "create_session" in desc

    @pytest.mark.asyncio
    async def test_list_sessions_routes(self) -> None:
        with patch("src.ai_interaction.do_list_sessions", new_callable=AsyncMock) as m:
            m.return_value = {"results": "sessions"}
            desc, _ = await _dispatch("list_sessions")
        m.assert_called_once()
        assert "list_sessions" in desc

    @pytest.mark.asyncio
    async def test_send_to_session_routes(self) -> None:
        with patch("src.ai_interaction.do_send_to_session", new_callable=AsyncMock) as m:
            m.return_value = {"response": "hi"}
            desc, _ = await _dispatch("send_to_session", "session_id\nHello")
        m.assert_called_once()
        assert "send_to_session" in desc

    @pytest.mark.asyncio
    async def test_pipeline_routes(self) -> None:
        with patch("src.ai_interaction.do_pipeline", new_callable=AsyncMock) as m:
            m.return_value = {"results": "done"}
            desc, _ = await _dispatch("pipeline", "model | do something")
        m.assert_called_once()
        assert "pipeline" in desc

    @pytest.mark.asyncio
    async def test_manage_session_routes(self) -> None:
        with patch("src.ai_interaction.do_manage_session", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            desc, _ = await _dispatch("manage_session", "list")
        m.assert_called_once()
        assert "manage_session" in desc

    @pytest.mark.asyncio
    async def test_manage_memory_routes(self) -> None:
        with patch("src.ai_interaction.do_manage_memory", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            desc, _ = await _dispatch("manage_memory", "list")
        m.assert_called_once()
        assert "manage_memory" in desc

    @pytest.mark.asyncio
    async def test_list_models_routes(self) -> None:
        with patch("src.ai_interaction.do_list_models", new_callable=AsyncMock) as m:
            m.return_value = {"results": "models"}
            desc, _ = await _dispatch("list_models")
        m.assert_called_once()
        assert "list_models" in desc

    @pytest.mark.asyncio
    async def test_ui_control_routes(self) -> None:
        with patch("src.ai_interaction.do_ui_control", new_callable=AsyncMock) as m:
            m.return_value = {"ui_event": "toggle"}
            desc, _ = await _dispatch("ui_control", "toggle bash on")
        m.assert_called_once()
        assert "ui_control" in desc

    @pytest.mark.asyncio
    async def test_ask_teacher_routes(self) -> None:
        with patch("src.ai_interaction.do_ask_teacher", new_callable=AsyncMock) as m:
            m.return_value = {"response": "guidance"}
            desc, _ = await _dispatch("ask_teacher", "auto\nMy problem")
        m.assert_called_once()
        assert "ask_teacher" in desc

    @pytest.mark.asyncio
    async def test_second_opinion_routes(self) -> None:
        with patch("src.ai_interaction.do_second_opinion", new_callable=AsyncMock) as m:
            m.return_value = {"response": "opinion"}
            desc, _ = await _dispatch("second_opinion", "model_name")
        m.assert_called_once()
        assert "second_opinion" in desc


# ---------------------------------------------------------------------------
# Description uses content helpers (not raw split)
# ---------------------------------------------------------------------------

class TestDispatchDescriptions:
    """Verify descriptions are derived from content correctly."""

    @pytest.mark.asyncio
    async def test_chat_description_uses_model_spec(self) -> None:
        with patch("src.ai_interaction.do_chat_with_model", new_callable=AsyncMock) as m:
            m.return_value = {}
            desc, _ = await _dispatch("chat_with_model", "gpt-4\nHello there")
        assert "gpt-4" in desc

    @pytest.mark.asyncio
    async def test_description_truncates_long_content(self) -> None:
        very_long = "x" * 200
        with patch("src.ai_interaction.do_chat_with_model", new_callable=AsyncMock) as m:
            m.return_value = {}
            desc, _ = await _dispatch("chat_with_model", f"{very_long}\nHello")
        # Description should not be excessively long
        assert len(desc) < 100

    @pytest.mark.asyncio
    async def test_list_sessions_with_keyword_in_description(self) -> None:
        with patch("src.ai_interaction.do_list_sessions", new_callable=AsyncMock) as m:
            m.return_value = {"results": "ok"}
            desc, _ = await _dispatch("list_sessions", "python projects")
        assert "python projects" in desc

    @pytest.mark.asyncio
    async def test_manage_session_action_in_description(self) -> None:
        with patch("src.ai_interaction.do_manage_session", new_callable=AsyncMock) as m:
            m.return_value = {}
            desc, _ = await _dispatch("manage_session", "rename\nsome_id\nnew name")
        assert "rename" in desc
