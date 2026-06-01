"""
Tests for do_manage_rag() and its action handlers.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict


@pytest.fixture(autouse=True)
def mock_managers(monkeypatch):
    rag = MagicMock()
    docs = MagicMock()
    docs.index = []
    monkeypatch.setattr("src.ai_interaction._rag_manager", rag)
    monkeypatch.setattr("src.ai_interaction._personal_docs_manager", docs)
    return rag, docs


async def _rag(content: str) -> Dict:
    from src.ai_interaction import do_manage_rag
    return do_manage_rag(content)


# ---------------------------------------------------------------------------
# list action
# ---------------------------------------------------------------------------

class TestManageRagList:

    @pytest.mark.asyncio
    async def test_list_no_docs_manager_returns_message(self, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._personal_docs_manager", None)
        result = await _rag("list")
        assert "results" in result
        assert "not available" in result["results"].lower()

    @pytest.mark.asyncio
    async def test_list_empty_returns_message(self, mock_managers) -> None:
        _, docs = mock_managers
        docs.index = []
        docs.get_indexed_directories = MagicMock(return_value=[])
        result = await _rag("list")
        assert "results" in result
        assert "No files" in result["results"] or "No" in result["results"]

    @pytest.mark.asyncio
    async def test_list_shows_directories(self, mock_managers) -> None:
        _, docs = mock_managers
        docs.index = []
        docs.get_indexed_directories = MagicMock(return_value=["/home/user/docs"])
        result = await _rag("list")
        assert "/home/user/docs" in result["results"]

    @pytest.mark.asyncio
    async def test_list_shows_files(self, mock_managers) -> None:
        _, docs = mock_managers
        docs.index = [{"name": "report.pdf"}, {"name": "notes.txt"}]
        docs.get_indexed_directories = MagicMock(return_value=[])
        result = await _rag("list")
        assert "report.pdf" in result["results"]
        assert "notes.txt" in result["results"]

    @pytest.mark.asyncio
    async def test_list_truncates_long_file_list(self, mock_managers) -> None:
        _, docs = mock_managers
        docs.index = [{"name": f"file_{i}.txt"} for i in range(60)]
        docs.get_indexed_directories = MagicMock(return_value=[])
        result = await _rag("list")
        assert "more" in result["results"]


# ---------------------------------------------------------------------------
# add_directory action
# ---------------------------------------------------------------------------

class TestManageRagAddDirectory:

    @pytest.mark.asyncio
    async def test_missing_path_returns_error(self) -> None:
        result = await _rag("add_directory")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_nonexistent_directory_returns_error(self) -> None:
        with patch("os.path.isdir", return_value=False):
            result = await _rag("add_directory\n/nonexistent/path")
        assert "error" in result
        assert "not found" in result["error"].lower() or "directory" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_rag_manager_returns_error(self, mock_managers, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._rag_manager", None)
        with patch("os.path.isdir", return_value=True):
            result = await _rag("add_directory\n/some/path")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_add_directory_success(self, mock_managers) -> None:
        rag, _ = mock_managers
        rag.index_personal_documents.return_value = {"indexed": 5}
        with patch("os.path.isdir", return_value=True), \
             patch("os.path.expanduser", return_value="/some/path"):
            result = await _rag("add_directory\n/some/path")
        assert result.get("action") == "add_directory"
        assert "5" in result.get("results", "")


# ---------------------------------------------------------------------------
# remove_directory action
# ---------------------------------------------------------------------------

class TestManageRagRemoveDirectory:

    @pytest.mark.asyncio
    async def test_missing_path_returns_error(self) -> None:
        result = await _rag("remove_directory")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_docs_manager_returns_error(self, mock_managers, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._personal_docs_manager", None)
        result = await _rag("remove_directory\n/some/path")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_remove_directory_success(self, mock_managers) -> None:
        rag, docs = mock_managers
        docs.remove_directory = MagicMock()
        result = await _rag("remove_directory\n/some/path")
        assert result.get("action") == "remove_directory"
        docs.remove_directory.assert_called_once_with("/some/path")

    @pytest.mark.asyncio
    async def test_remove_directory_triggers_index_rebuild(self, mock_managers) -> None:
        rag, docs = mock_managers
        docs.remove_directory = MagicMock()
        await _rag("remove_directory\n/some/path")
        rag.rebuild_index.assert_called_once()


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

class TestManageRagUnknownAction:

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self) -> None:
        result = await _rag("bad_action")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_mentions_valid_actions(self) -> None:
        result = await _rag("bad_action")
        assert "list" in result["error"]
        assert "add_directory" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_content_returns_error(self) -> None:
        result = await _rag("")
        assert "error" in result
