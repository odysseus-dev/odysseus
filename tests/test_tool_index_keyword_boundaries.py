"""Keyword-hint force-include must match on word boundaries, not substrings.

`get_tools_for_query` force-includes whole tool families when a query mentions
an intent keyword. The match used a raw substring test (`kw in ql`), so short
hints fired inside unrelated words: "fix" in "prefix", "line" in "deadline"/
"online", "serve" in "observe"/"reserve", "reply" in "replying", "unread" in
"unreadable". That bloated the tool set with irrelevant email/document/serve
tools for queries that have nothing to do with them. Same substring-vs-word
pitfall already fixed in topic_analyzer.py.

`retrieve` (which needs a chroma collection) is stubbed out so these tests
exercise only the keyword-hint loop.
"""
from src.tool_index import ToolIndex


def _index():
    ti = ToolIndex.__new__(ToolIndex)
    ti.retrieve = lambda query, k=8: []  # no chroma; isolate the keyword loop
    return ti


def test_substring_inside_word_does_not_force_email_tools():
    ti = _index()
    # "replying" contains "reply"; "unreadable" contains "unread".
    for q in ("i am replying to your github comment", "this document is unreadable"):
        tools = ti.get_tools_for_query(q)
        assert "send_email" not in tools, q
        assert "reply_to_email" not in tools, q


def test_substring_inside_word_does_not_force_document_tools():
    ti = _index()
    # "prefix" contains "fix"; "deadline"/"online" contain "line".
    for q in ("prefix the output with a label", "the deadline is online already"):
        tools = ti.get_tools_for_query(q)
        assert "edit_document" not in tools, q
        assert "update_document" not in tools, q


def test_substring_inside_word_does_not_force_serve_tools():
    ti = _index()
    # "observe"/"reserve" contain "serve".
    tools = ti.get_tools_for_query("please observe the reserve levels")
    assert "serve_model" not in tools
    assert "serve_preset" not in tools


def test_genuine_keywords_still_force_include():
    ti = _index()
    assert "reply_to_email" in ti.get_tools_for_query("reply to this email")
    assert "edit_document" in ti.get_tools_for_query("edit the document")
    assert "serve_model" in ti.get_tools_for_query("serve the model")
