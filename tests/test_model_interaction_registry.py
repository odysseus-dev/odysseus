"""Tests for the model-interaction tools after their move to the agent_tools
registry (#3629): chat_with_model, ask_teacher, list_models.

The implementations now live in src/agent_tools/model_interaction_tools.py
(moved out of src/ai_interaction.py). These assert (1) the handlers are
registered in TOOL_HANDLERS, (2) each handler runs the moved logic and threads
session_id/owner from the ctx, and (3) tool_execution.py dispatches them
through the registry rather than the legacy dispatch_ai_tool elif.
"""
import asyncio
from pathlib import Path

import src.ai_interaction as ai_interaction
import src.llm_core as llm_core
import src.database as database
from src.agent_tools import TOOL_HANDLERS
from src.agent_tools import model_interaction_tools as mit

_MODEL_TOOLS = ("chat_with_model", "ask_teacher", "list_models")


def test_model_interaction_tools_registered():
    for name in _MODEL_TOOLS:
        assert name in TOOL_HANDLERS, f"{name} missing from TOOL_HANDLERS"


def test_chat_with_model_threads_owner_and_returns(monkeypatch):
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://x", "model-x", {})

    async def fake_call(url, model, messages, headers=None, timeout=None):
        seen["message"] = messages[-1]["content"]
        return "hi back"

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    res = asyncio.run(mit.ChatWithModelTool().execute(
        "model-x\nhello there", {"owner": "alice", "session_id": "s1"}))

    assert res == {"model": "model-x", "response": "hi back"}
    assert seen["owner"] == "alice"
    assert seen["spec"] == "model-x"
    assert seen["message"] == "hello there"


def test_ask_teacher_threads_owner_and_marks_teacher(monkeypatch):
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["owner"] = owner
        return ("http://x", "teacher-x", {})

    async def fake_call(url, model, messages, headers=None, timeout=None):
        return "do this and that"

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    res = asyncio.run(mit.AskTeacherTool().execute(
        "teacher-x\nI am stuck", {"owner": "bob"}))

    assert res["teacher"] is True
    assert res["response"] == "do this and that"
    assert seen["owner"] == "bob"


def _patch_teacher(monkeypatch, teacher_model):
    import src.settings as settings
    seen = {}

    def fake_resolve(spec, owner=None):
        seen["spec"] = spec
        return ("http://x", spec, {})

    async def fake_call(url, model, messages, headers=None, timeout=None):
        return "advice"

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    monkeypatch.setattr(
        settings, "get_setting",
        lambda key, default=None: teacher_model if key == "teacher_model" else default,
    )
    return seen


def test_ask_teacher_configured_model_overrides_requested_model(monkeypatch):
    """#5525: the calling model must not pick a different (e.g. paid cloud)
    endpoint when the admin pinned teacher_model."""
    seen = _patch_teacher(monkeypatch, "local-teacher")

    res = asyncio.run(mit.AskTeacherTool().execute("gpt-paid-cloud\nI am stuck", {}))

    assert seen["spec"] == "local-teacher"
    assert res["model"] == "local-teacher"


def test_ask_teacher_auto_uses_configured_model(monkeypatch):
    seen = _patch_teacher(monkeypatch, "local-teacher")
    asyncio.run(mit.AskTeacherTool().execute("auto\nI am stuck", {}))
    assert seen["spec"] == "local-teacher"


def test_ask_teacher_unconfigured_keeps_requested_model(monkeypatch):
    seen = _patch_teacher(monkeypatch, "")
    asyncio.run(mit.AskTeacherTool().execute("some-model\nI am stuck", {}))
    assert seen["spec"] == "some-model"


def test_ask_teacher_unconfigured_auto_errors(monkeypatch):
    seen = _patch_teacher(monkeypatch, "")
    res = asyncio.run(mit.AskTeacherTool().execute("auto\nI am stuck", {}))
    assert "No teacher model configured" in res["error"]
    assert "spec" not in seen


def test_list_models_no_endpoints(monkeypatch):
    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return []

    class _S:
        def query(self, *a, **k):
            return _Q()

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: _S())

    res = asyncio.run(mit.ListModelsTool().execute("", {}))
    assert res == {"results": "No enabled model endpoints configured."}


def test_dispatched_via_registry_not_dispatch_ai_tool():
    """The model tools route through the registry (_document_tool_dispatch), and
    are no longer in the dispatch_ai_tool elif tuple."""
    source = (Path(__file__).resolve().parent.parent / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert 'elif tool in ("chat_with_model", "ask_teacher", "list_models"):' in source

    marker = "from src.ai_interaction import dispatch_ai_tool"
    idx = source.index(marker)
    branch_head = source.rfind("elif tool in (", 0, idx)
    legacy_tuple = source[branch_head:idx]
    for name in _MODEL_TOOLS:
        assert f'"{name}"' not in legacy_tuple, f"{name} still routed via dispatch_ai_tool"
