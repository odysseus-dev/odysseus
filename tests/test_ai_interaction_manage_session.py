"""
Tests for do_manage_session() and its refactored sub-handlers.

Covers: list, rename, archive, unarchive, delete, important, unimportant,
truncate, fork, switch/open/select/view actions.

Uses mocks for session_manager and get_db_session to stay unit-level.
Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()  # Distinguish "not passed" from None


def _make_db_mock(first_result=_SENTINEL) -> Tuple:
    """Return a (ctx, mock_db, mock_db_sess) tuple.

    mock_db.query(X).filter(...).first() always returns first_result.
    Pass first_result=None to simulate "not found".
    Omit first_result to get a fresh MagicMock.
    """
    mock_db_sess = MagicMock() if first_result is _SENTINEL else first_result
    mock_db = MagicMock()
    # Support both single-filter and double-filter query chains
    filter_mock = MagicMock()
    filter_mock.filter.return_value = filter_mock
    filter_mock.first.return_value = mock_db_sess
    mock_db.query.return_value.filter.return_value = filter_mock

    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx, mock_db, mock_db_sess


def _null_db_mock() -> Tuple:
    """Return a DB mock where .first() returns None (session not found)."""
    return _make_db_mock(first_result=None)


@pytest.fixture(autouse=True)
def mock_session_manager(monkeypatch):
    """Inject a mock session manager into ai_interaction module."""
    mgr = MagicMock()
    mgr.get_sessions_for_user.return_value = {}
    monkeypatch.setattr("src.ai_interaction._session_manager", mgr)
    return mgr


async def _manage(content: str, session_id: str = "sess1", owner: str = "user1") -> Dict:
    from src.ai_interaction import do_manage_session
    return do_manage_session(content, session_id=session_id, owner=owner)


# ---------------------------------------------------------------------------
# Missing / unavailable manager
# ---------------------------------------------------------------------------

class TestManageSessionNoManager:
    """Tests when session manager is not available."""

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr("src.ai_interaction._session_manager", None)
        result = await _manage("list")
        assert "error" in result


# ---------------------------------------------------------------------------
# list action
# ---------------------------------------------------------------------------

class TestManageSessionList:
    """Tests for the list action."""

    @pytest.mark.asyncio
    async def test_list_action_dispatches_to_list_sessions(self) -> None:
        """manage_session with 'list' should delegate to list_sessions logic."""
        # do_list_sessions is now sync, so patch with a plain MagicMock.
        with patch("src.ai_interaction.do_list_sessions", new_callable=MagicMock) as mock_list:
            mock_list.return_value = {"results": "listed"}
            result = await _manage("list")
        mock_list.assert_called_once()
        assert result == {"results": "listed"}


# ---------------------------------------------------------------------------
# switch / open / select / view actions
# ---------------------------------------------------------------------------

class TestManageSessionSwitch:
    """Tests for the switch/open/select/view actions."""

    @pytest.mark.asyncio
    async def test_switch_missing_session_id_returns_error(self) -> None:
        result = await _manage("switch")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_switch_unknown_session_returns_error(self) -> None:
        with patch("src.ai_interaction._session_action_view") as mock_view:
            mock_view.return_value = {"error": "not found"}
            result = await _manage("switch\nunknown_session_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_open_alias_works(self) -> None:
        """'open' should route the same as 'switch'."""
        with patch("src.ai_interaction._session_action_view") as mock_view:
            mock_view.return_value = {"error": "not found"}
            result = await _manage("open\nunknown_id")
        assert "error" in result


# ---------------------------------------------------------------------------
# rename action
# ---------------------------------------------------------------------------

class TestManageSessionRename:
    """Tests for the rename action."""

    @pytest.mark.asyncio
    async def test_rename_missing_session_id_returns_error(self) -> None:
        result = await _manage("rename")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rename_missing_new_name_returns_error(self) -> None:
        ctx, mock_db, mock_db_sess = _make_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("rename\nsome_session_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rename_unknown_session_returns_error(self) -> None:
        ctx, mock_db, _ = _null_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("rename\nunknown_session\nnew name")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rename_success(self, mock_session_manager) -> None:
        mock_sess = MagicMock()
        mock_sess.name = "old name"
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("rename\nsome_id\nnew name")

        assert result.get("action") == "rename"
        assert "new name" in result.get("results", "")
        assert mock_sess.name == "new name"


# ---------------------------------------------------------------------------
# archive / unarchive actions
# ---------------------------------------------------------------------------

class TestManageSessionArchive:
    """Tests for archive and unarchive actions."""

    @pytest.mark.asyncio
    async def test_archive_unknown_session_returns_error(self) -> None:
        ctx, _, _ = _null_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("archive\nunknown_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_archive_success(self) -> None:
        mock_sess = MagicMock()
        mock_sess.name = "My Chat"
        mock_sess.archived = False
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("archive\nsome_id")

        assert result.get("action") == "archive"
        assert mock_sess.archived is True

    @pytest.mark.asyncio
    async def test_unarchive_success(self) -> None:
        mock_sess = MagicMock()
        mock_sess.name = "My Chat"
        mock_sess.archived = True
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("unarchive\nsome_id")

        assert result.get("action") == "unarchive"
        assert mock_sess.archived is False


# ---------------------------------------------------------------------------
# delete action
# ---------------------------------------------------------------------------

class TestManageSessionDelete:
    """Tests for the delete action."""

    @pytest.mark.asyncio
    async def test_delete_current_session_returns_error(self) -> None:
        """Deleting the active session should be refused."""
        mock_sess = MagicMock()
        mock_sess.is_important = False
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("delete\nsess1", session_id="sess1")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_unknown_session_returns_error(self) -> None:
        ctx, _, _ = _null_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("delete\nunknown_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_starred_session_returns_error(self) -> None:
        mock_sess = MagicMock()
        mock_sess.is_important = True
        mock_sess.name = "Important Chat"
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("delete\nother_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_session_manager) -> None:
        mock_sess = MagicMock()
        mock_sess.is_important = False
        mock_sess.name = "Old Chat"
        mock_session_manager.delete_session.return_value = True
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("delete\nother_id")

        assert result.get("action") == "delete"


# ---------------------------------------------------------------------------
# truncate action
# ---------------------------------------------------------------------------

class TestManageSessionTruncate:
    """Tests for the truncate action."""

    @pytest.mark.asyncio
    async def test_truncate_unknown_session_returns_error(self) -> None:
        ctx, _, _ = _null_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("truncate\nunknown_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_truncate_uses_default_keep_count(self, mock_session_manager) -> None:
        from src.ai_interaction import SESSION_TRUNCATE_DEFAULT_KEEP
        mock_sess = MagicMock()
        mock_session_manager.truncate_messages.return_value = True
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("truncate\nsome_id")

        mock_session_manager.truncate_messages.assert_called_once_with(
            "some_id", SESSION_TRUNCATE_DEFAULT_KEEP
        )
        assert result.get("action") == "truncate"

    @pytest.mark.asyncio
    async def test_truncate_with_custom_keep_count(self, mock_session_manager) -> None:
        mock_sess = MagicMock()
        mock_session_manager.truncate_messages.return_value = True
        ctx, _, _ = _make_db_mock(first_result=mock_sess)

        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("truncate\nsome_id\n25")

        mock_session_manager.truncate_messages.assert_called_once_with("some_id", 25)


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

class TestManageSessionUnknownAction:
    """Tests for unknown action handling."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self) -> None:
        ctx, _, _ = _make_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("totally_unknown_action_xyz\nsome_id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_mentions_valid_actions(self) -> None:
        ctx, _, _ = _make_db_mock()
        with patch("src.ai_interaction.get_db_session", ctx):
            result = await _manage("not_an_action\nsome_id")
        assert "error" in result
        error_msg = result["error"]
        assert any(action in error_msg for action in ("rename", "archive", "delete", "list"))


# ---------------------------------------------------------------------------
# Input parsing helpers (extracted from _parse_manage_session_input)
# ---------------------------------------------------------------------------

class TestParseSessionJson:
    """Tests for _parse_session_json()."""

    def test_basic_json(self) -> None:
        from src.ai_interaction import _parse_session_json
        result = _parse_session_json('{"action": "rename", "session_id": "abc", "value": "New"}')
        assert result == ("rename", "abc", "New", "")

    def test_session_alias_keys(self) -> None:
        from src.ai_interaction import _parse_session_json
        # 'session' and 'id' are accepted aliases for session_id
        assert _parse_session_json('{"action": "delete", "session": "s1"}')[1] == "s1"
        assert _parse_session_json('{"action": "delete", "id": "s2"}')[1] == "s2"

    def test_value_alias_keys(self) -> None:
        from src.ai_interaction import _parse_session_json
        # name/new_name/title/keep_count are accepted aliases for value
        assert _parse_session_json('{"action": "rename", "session_id": "s", "name": "N"}')[2] == "N"
        assert _parse_session_json('{"action": "rename", "session_id": "s", "title": "T"}')[2] == "T"
        assert _parse_session_json('{"action": "truncate", "session_id": "s", "keep_count": "5"}')[2] == "5"

    def test_filter_key(self) -> None:
        from src.ai_interaction import _parse_session_json
        assert _parse_session_json('{"action": "list", "filter": "python"}')[3] == "python"

    def test_invalid_json_returns_none(self) -> None:
        from src.ai_interaction import _parse_session_json
        assert _parse_session_json("{not valid json") is None

    def test_non_dict_json_returns_none(self) -> None:
        from src.ai_interaction import _parse_session_json
        assert _parse_session_json("[1, 2, 3]") is None


class TestParseSessionLines:
    """Tests for _parse_session_lines()."""

    def test_three_lines(self) -> None:
        from src.ai_interaction import _parse_session_lines
        action, sid, value, _ = _parse_session_lines("rename\nabc\nNew Name")
        assert action == "rename"
        assert sid == "abc"
        assert value == "New Name"

    def test_action_only(self) -> None:
        from src.ai_interaction import _parse_session_lines
        action, sid, value, _ = _parse_session_lines("list")
        assert action == "list"
        assert sid == ""
        assert value is None

    def test_empty_returns_blank_action(self) -> None:
        from src.ai_interaction import _parse_session_lines
        action, sid, value, lf = _parse_session_lines("")
        assert action == ""

    def test_list_filter_joins_remaining_lines(self) -> None:
        from src.ai_interaction import _parse_session_lines
        _, _, _, list_filter = _parse_session_lines("list\npython\nprojects")
        assert "python" in list_filter

    def test_action_lowercased(self) -> None:
        from src.ai_interaction import _parse_session_lines
        action, _, _, _ = _parse_session_lines("RENAME\nabc\nNew")
        assert action == "rename"
