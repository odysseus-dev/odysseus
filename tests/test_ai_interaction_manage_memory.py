"""
Tests for do_manage_memory() and its extracted action handlers.

Covers: list, add, edit, delete, search actions.

Uses mocks for memory_manager and memory_vector to stay unit-level.
Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import MagicMock, patch, call
from typing import Dict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_memory_manager(monkeypatch):
    """Inject a mock memory manager into ai_interaction module."""
    mgr = MagicMock()
    mgr.load.return_value = []
    mgr.load_all.return_value = []
    monkeypatch.setattr("src.ai_interaction._memory_manager", mgr)
    return mgr


async def _memory(content: str, owner: str = "user1") -> Dict:
    from src.ai_interaction import do_manage_memory
    return await do_manage_memory(content, owner=owner)


# ---------------------------------------------------------------------------
# No manager
# ---------------------------------------------------------------------------

class TestManageMemoryNoManager:
    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._memory_manager", None)
        result = await _memory("list")
        assert "error" in result


# ---------------------------------------------------------------------------
# list action
# ---------------------------------------------------------------------------

class TestManageMemoryList:
    @pytest.mark.asyncio
    async def test_list_no_memories(self, mock_memory_manager) -> None:
        mock_memory_manager.load.return_value = []
        result = await _memory("list")
        assert "results" in result
        assert "No memories" in result["results"]

    @pytest.mark.asyncio
    async def test_list_returns_memories(self, mock_memory_manager) -> None:
        mock_memory_manager.load.return_value = [
            {"id": "abc12345", "category": "fact", "text": "Python is great"},
            {"id": "def67890", "category": "preference", "text": "Likes dark mode"},
        ]
        result = await _memory("list")
        assert "results" in result
        assert "Python is great" in result["results"]
        assert "Likes dark mode" in result["results"]

    @pytest.mark.asyncio
    async def test_list_with_category_filter(self, mock_memory_manager) -> None:
        mock_memory_manager.load.return_value = [
            {"id": "abc12345", "category": "fact", "text": "Python is great"},
            {"id": "def67890", "category": "preference", "text": "Likes dark mode"},
        ]
        result = await _memory("list\nfact")
        assert "results" in result
        assert "Python is great" in result["results"]
        assert "Likes dark mode" not in result["results"]

    @pytest.mark.asyncio
    async def test_list_truncates_long_text(self, mock_memory_manager) -> None:
        from src.ai_interaction import MEMORY_TEXT_PREVIEW_LIMIT
        long_text = "x" * (MEMORY_TEXT_PREVIEW_LIMIT + 50)
        mock_memory_manager.load.return_value = [
            {"id": "abc12345", "category": "fact", "text": long_text},
        ]
        result = await _memory("list")
        assert "..." in result["results"]


# ---------------------------------------------------------------------------
# add action
# ---------------------------------------------------------------------------

class TestManageMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_missing_text_returns_error(self) -> None:
        result = await _memory("add")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_add_success(self, mock_memory_manager) -> None:
        entry = {"id": "abc12345", "text": "New fact", "category": "fact"}
        mock_memory_manager.add_entry.return_value = entry
        mock_memory_manager.load_all.return_value = []

        result = await _memory("add\nNew fact")
        assert result.get("action") == "add"
        assert "memory_id" in result

    @pytest.mark.asyncio
    async def test_add_with_category(self, mock_memory_manager) -> None:
        entry = {"id": "abc12345", "text": "Prefers Python", "category": "preference"}
        mock_memory_manager.add_entry.return_value = entry
        mock_memory_manager.load_all.return_value = []

        await _memory("add\nPrefers Python\npreference")
        mock_memory_manager.add_entry.assert_called_once_with(
            "Prefers Python", source="ai_agent", category="preference", owner="user1"
        )

    @pytest.mark.asyncio
    async def test_add_default_category_is_fact(self, mock_memory_manager) -> None:
        entry = {"id": "abc12345", "text": "Some fact", "category": "fact"}
        mock_memory_manager.add_entry.return_value = entry
        mock_memory_manager.load_all.return_value = []

        await _memory("add\nSome fact")
        mock_memory_manager.add_entry.assert_called_once_with(
            "Some fact", source="ai_agent", category="fact", owner="user1"
        )

    @pytest.mark.asyncio
    async def test_add_updates_vector_index_if_available(self, mock_memory_manager, monkeypatch) -> None:
        entry = {"id": "abc12345", "text": "New fact", "category": "fact"}
        mock_memory_manager.add_entry.return_value = entry
        mock_memory_manager.load_all.return_value = []

        mock_vector = MagicMock()
        mock_vector.healthy = True
        monkeypatch.setattr("src.ai_interaction._memory_vector", mock_vector)

        await _memory("add\nNew fact")
        mock_vector.add.assert_called_once_with("abc12345", "New fact")


# ---------------------------------------------------------------------------
# edit action
# ---------------------------------------------------------------------------

class TestManageMemoryEdit:
    @pytest.mark.asyncio
    async def test_edit_missing_args_returns_error(self) -> None:
        result = await _memory("edit\nsome_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_unknown_id_returns_error(self, mock_memory_manager) -> None:
        mock_memory_manager.load_all.return_value = [
            {"id": "other_id", "text": "Some text", "owner": "user1"}
        ]
        result = await _memory("edit\nunknown_id\nnew text")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_success(self, mock_memory_manager) -> None:
        mock_memory_manager.load_all.return_value = [
            {"id": "abc12345", "text": "Old text", "owner": "user1"}
        ]
        result = await _memory("edit\nabc1\nnew text")
        assert result.get("action") == "edit"
        mock_memory_manager.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_respects_owner(self, mock_memory_manager) -> None:
        """Cannot edit another user's memory."""
        mock_memory_manager.load_all.return_value = [
            {"id": "abc12345", "text": "Secret", "owner": "other_user"}
        ]
        result = await _memory("edit\nabc1\nnew text", owner="user1")
        assert "error" in result


# ---------------------------------------------------------------------------
# delete action
# ---------------------------------------------------------------------------

class TestManageMemoryDelete:
    @pytest.mark.asyncio
    async def test_delete_missing_id_returns_error(self) -> None:
        result = await _memory("delete")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_unknown_id_returns_error(self, mock_memory_manager) -> None:
        mock_memory_manager.load_all.return_value = [
            {"id": "other_id", "text": "Some text", "owner": "user1"}
        ]
        result = await _memory("delete\nunknown_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_memory_manager) -> None:
        mock_memory_manager.load_all.return_value = [
            {"id": "abc12345", "text": "To delete", "owner": "user1"}
        ]
        result = await _memory("delete\nabc1")
        assert result.get("action") == "delete"
        mock_memory_manager.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_respects_owner(self, mock_memory_manager) -> None:
        """Cannot delete another user's memory."""
        mock_memory_manager.load_all.return_value = [
            {"id": "abc12345", "text": "Secret", "owner": "other_user"}
        ]
        result = await _memory("delete\nabc1", owner="user1")
        assert "error" in result


# ---------------------------------------------------------------------------
# search action
# ---------------------------------------------------------------------------

class TestManageMemorySearch:
    @pytest.mark.asyncio
    async def test_search_missing_query_returns_error(self) -> None:
        result = await _memory("search")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, mock_memory_manager) -> None:
        mock_memory_manager.load.return_value = []
        mock_memory_manager.get_relevant_memories = MagicMock(return_value=[])

        result = await _memory("search\nsome query")
        assert "results" in result
        assert "No memories" in result["results"]

    @pytest.mark.asyncio
    async def test_search_uses_vector_search_when_available(self, mock_memory_manager) -> None:
        memories = [{"id": "abc12345", "category": "fact", "text": "Python is great"}]
        mock_memory_manager.load.return_value = memories
        mock_memory_manager.get_relevant_memories = MagicMock(return_value=memories)

        result = await _memory("search\nPython")
        assert "results" in result
        assert "Python is great" in result["results"]
        mock_memory_manager.get_relevant_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_falls_back_to_text_search(self, mock_memory_manager) -> None:
        """When get_relevant_memories is absent, fall back to text search."""
        memories = [{"id": "abc12345", "category": "fact", "text": "Python is great"}]
        mock_memory_manager.load.return_value = memories
        del mock_memory_manager.get_relevant_memories  # Remove the method

        result = await _memory("search\nPython")
        assert "results" in result
        assert "Python is great" in result["results"]


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

class TestManageMemoryUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self) -> None:
        result = await _memory("unknown_action_xyz")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_mentions_valid_actions(self) -> None:
        result = await _memory("bad_action")
        assert "error" in result
        error_msg = result["error"]
        assert any(a in error_msg for a in ("list", "add", "edit", "delete", "search"))
