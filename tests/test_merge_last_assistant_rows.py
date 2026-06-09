"""merge-last-assistant must not delete tool/system rows between the messages.

The in-memory merge removes the second assistant message plus only the
"continue" user message between the last two assistant replies. The DB path
deleted the ENTIRE index range between them, destroying any tool/system/user
rows in between — so on reload the DB lost messages the in-memory history
kept (data loss + count desync). _merge_continue_rows_to_delete makes the DB
deletion mirror the in-memory rule.
"""
from types import SimpleNamespace

from routes.history_routes import _merge_continue_rows_to_delete


def _m(role, content=""):
    return SimpleNamespace(role=role, content=content)


def test_tool_message_between_is_not_deleted():
    u, a1, tool, a2 = _m("user", "q"), _m("assistant", "a1"), _m("tool", "RESULT"), _m("assistant", "a2")
    rows = _merge_continue_rows_to_delete([u, a1, tool, a2], a1, a2)
    assert rows == [a2]            # only the 2nd assistant
    assert tool not in rows        # the tool result survives


def test_continue_user_message_is_deleted():
    u, a1, cont, a2 = (_m("user", "q"), _m("assistant", "a1"),
                       _m("user", "(the previous response was interrupted)"), _m("assistant", "a2"))
    rows = _merge_continue_rows_to_delete([u, a1, cont, a2], a1, a2)
    assert a2 in rows and cont in rows and len(rows) == 2


def test_adjacent_assistants_delete_only_second():
    a1, a2 = _m("assistant", "a1"), _m("assistant", "a2")
    assert _merge_continue_rows_to_delete([a1, a2], a1, a2) == [a2]


def test_plain_user_between_not_deleted():
    a1, usr, a2 = _m("assistant", "a1"), _m("user", "a real follow-up question"), _m("assistant", "a2")
    rows = _merge_continue_rows_to_delete([a1, usr, a2], a1, a2)
    assert rows == [a2] and usr not in rows
