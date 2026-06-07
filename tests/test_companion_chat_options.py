"""Unit tests for the companion chat-option translator.

`_chat_run_options` maps the phone's per-turn toggles ({agent, web, terminal,
research} + attachments) onto the exact chat_stream form fields, mirroring the
desktop's static/js/chat.js. These are pure dict-in/dict-out helpers, so we test
them directly (same style as tests/test_companion_readonly.py).
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# companion.routes only touches core.database lazily inside handlers, but stub it
# so importing the module is robust regardless of collection order.
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["core.database"] = _db

from companion.routes import _chat_run_options, _has_attachments


def test_plain_chat_is_the_default():
    assert _chat_run_options({}) == {"mode": "chat"}


def test_agent_mode_sets_agent():
    assert _chat_run_options({"agent": True}) == {"mode": "agent"}


def test_terminal_implies_agent_and_allows_bash():
    out = _chat_run_options({"terminal": True})
    assert out["mode"] == "agent"
    assert out["allow_bash"] == "true"


def test_web_is_presearch_in_chat_mode():
    out = _chat_run_options({"web": True})
    assert out["mode"] == "chat"
    assert out["use_web"] == "true"
    assert "allow_web_search" not in out


def test_web_is_a_tool_in_agent_mode():
    out = _chat_run_options({"agent": True, "web": True})
    assert out["mode"] == "agent"
    assert out["allow_web_search"] == "true"
    assert "use_web" not in out


def test_research_overrides_agent_tools():
    out = _chat_run_options({"research": True, "agent": True, "terminal": True, "web": True})
    assert out["mode"] == "chat"
    assert out["use_research"] == "true"
    # research is self-contained: no agent tool flags leak through
    assert "allow_bash" not in out
    assert "allow_web_search" not in out


def test_attachments_serialized_as_json_list():
    out = _chat_run_options({"attachments": ["a.png", "b.pdf"]})
    assert json.loads(out["attachments"]) == ["a.png", "b.pdf"]


def test_attachments_ignored_when_empty_or_wrong_type():
    assert "attachments" not in _chat_run_options({"attachments": []})
    assert "attachments" not in _chat_run_options({"attachments": "nope"})


def test_has_attachments():
    assert _has_attachments({"attachments": ["x"]}) is True
    assert _has_attachments({"attachments": []}) is False
    assert _has_attachments({}) is False
    assert _has_attachments({"attachments": "x"}) is False
