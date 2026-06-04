"""Owner-scope regression tests for src/teacher_escalation.py.

The teacher escalation path resolves a model endpoint via
`_resolve_model`, which only applies the owner filter when an `owner`
argument is supplied. If owner is not threaded through, the teacher
lookup can match any user's private endpoint (and its decrypted
api_key) in a multi-user deployment. These tests assert that owner is
passed at every relevant call site.
"""

import pytest

from src import teacher_escalation


async def test_call_teacher_passes_owner_to_resolve_model(monkeypatch):
    """_call_teacher must forward owner to _resolve_model."""
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["owner"] = owner
        return ("http://endpoint", "model-id", {})

    async def fake_llm_call_async(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = await teacher_escalation._call_teacher("gpt-test", "prompt", owner="alice")

    assert result == "ok"
    assert seen["owner"] == "alice"


async def test_escalate_and_learn_threads_owner(monkeypatch):
    """escalate_and_learn must pass owner when calling _call_teacher."""
    seen = {}

    async def fake_call_teacher(spec, prompt, owner=None):
        seen["owner"] = owner
        return None  # no skill -> early return, keeps the test focused

    monkeypatch.setattr(teacher_escalation, "_call_teacher", fake_call_teacher)
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: "gpt-teacher")

    await teacher_escalation.escalate_and_learn(
        "request", [], "reply", "reason", owner="alice"
    )

    assert seen["owner"] == "alice"


async def test_run_teacher_inline_threads_owner(monkeypatch):
    """run_teacher_inline must pass owner to _resolve_model (place A)."""
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["owner"] = owner
        return ("http://endpoint", "model-id", {})

    # Gates: teacher enabled + a teacher_model configured.
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: True if key == "teacher_enabled" else "gpt-teacher",
    )
    # Force the Tier 1 regex eval to report failure so escalation proceeds.
    monkeypatch.setattr(
        teacher_escalation,
        "evaluate_turn_regex",
        lambda events, reply: ("failure", "stubbed failure"),
    )
    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)

    # stream_agent_loop is invoked after _resolve_model; stub it to a
    # no-op async generator so we exercise place A without a live model.
    async def fake_stream_agent_loop(*args, **kwargs):
        if False:
            yield ""  # make this an async generator
        return

    monkeypatch.setattr("src.agent_loop.stream_agent_loop", fake_stream_agent_loop)

    gen = teacher_escalation.run_teacher_inline(
        student_endpoint_url="http://student",
        student_messages=[{"role": "user", "content": "do a thing"}],
        student_tool_events=[],
        student_reply="i can't",
        owner="alice",
    )
    async for _ in gen:
        pass

    assert seen["owner"] == "alice"
