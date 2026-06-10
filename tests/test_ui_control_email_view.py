"""Behavioral tests for the ui_control `email_view` action (src/ai_interaction.py).

`email_view` is a pure function of its input line — no IMAP, no model, no
session — so we can assert the emitted ui_event dict directly.
"""

import asyncio
import json

import src.agent_tools  # noqa: F401  import first: resolves the agent_tools<->tool_schemas circular import (app does this via early agent_tools load)
from src.ai_interaction import do_ui_control
from src.tool_schemas import function_call_to_tool_block


def _run(line):
    return asyncio.run(do_ui_control(line))


def _native(args):
    """Full native path: structured function-call args → ToolBlock content →
    do_ui_control. Native-calling models (OpenRouter etc.) pass structured args,
    so the tool_schemas converter must assemble a valid `email_view` line."""
    tb = function_call_to_tool_block("ui_control", json.dumps(args))
    assert tb is not None
    return asyncio.run(do_ui_control(tb.content))


def test_email_view_folder_only_defaults():
    r = _run("email_view INBOX")
    assert r["ui_event"] == "set_email_view"
    assert r["folder"] == "INBOX"
    assert r["filter"] == "all"
    assert r["from"] == ""
    assert r["has_attachments"] is False


def test_email_view_quoted_folder_with_spaces():
    r = _run('email_view "[Gmail]/All Mail"')
    assert r["folder"] == "[Gmail]/All Mail"


def test_email_view_unread_filter():
    r = _run("email_view INBOX unread")
    assert r["filter"] == "unread"


def test_email_view_unanswered_filter():
    r = _run("email_view INBOX unanswered")
    assert r["filter"] == "unanswered"


def test_email_view_from_and_attachments():
    r = _run("email_view INBOX from:boss@work.com attachments")
    assert r["from"] == "boss@work.com"
    assert r["has_attachments"] is True
    assert r["folder"] == "INBOX"


def test_email_view_no_folder_defaults_to_inbox():
    r = _run("email_view unread")
    assert r["folder"] == "INBOX"
    assert r["filter"] == "unread"


def test_email_view_empty_from_is_no_filter():
    # An empty address after `from:` is intentionally a no-op (no sender filter),
    # not an error — the agent may include a bare `from:` by mistake.
    r = _run("email_view INBOX from:")
    assert r["ui_event"] == "set_email_view"
    assert r["from"] == ""


def test_email_view_filter_keyword_is_case_insensitive():
    r = _run("email_view INBOX UNREAD")
    assert r["filter"] == "unread"


def test_native_email_view_roundtrip_quoted_folder():
    # The converter must quote a spaced folder so it survives shlex parsing.
    r = _native({"action": "email_view", "folder": "[Gmail]/All Mail", "filter": "unread"})
    assert r["ui_event"] == "set_email_view"
    assert r["folder"] == "[Gmail]/All Mail"
    assert r["filter"] == "unread"


def test_native_email_view_from_and_attachments():
    r = _native({"action": "email_view", "folder": "INBOX",
                 "email_from": "boss@work.com", "attachments": True})
    assert r["from"] == "boss@work.com"
    assert r["has_attachments"] is True
    assert r["folder"] == "INBOX"


def test_open_email_reply_rejects_non_numeric_uid():
    # A model that calls open_email_reply to "show" email often passes a folder
    # name or blank UID (which shifts the folder into the uid slot). That must
    # be rejected, not open a broken reply + reset the email view to unfiltered.
    r = _run("open_email_reply INBOX reply")
    assert "error" in r
    assert "ui_event" not in r


def test_open_email_reply_blank_uid_rejected():
    r = _run("open_email_reply")
    assert "error" in r
    assert "ui_event" not in r


def test_open_email_reply_accepts_numeric_uid():
    r = _run("open_email_reply 90186 INBOX reply")
    assert r["ui_event"] == "open_email_reply"
    assert r["uid"] == "90186"
