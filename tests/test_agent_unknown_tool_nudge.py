"""Unknown native tool name → one corrective nudge, not a dead-end (#5202).

When a model emits a native tool call whose name doesn't convert (a
hallucinated or un-namespaced name like `search_files` instead of
`mcp__<id>__search_files`) and writes no text, the round has nothing to run
and nothing to say. It used to break straight to "The model returned an empty
response". Now the loop tells the model which name(s) were wrong plus the exact
valid names and gives it one more round — capped by _MAX_INTENT_NUDGES.
"""
import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    # No owner in the harness → blocked_tools_for_owner would otherwise disable
    # every effectful tool, leaving the schema list (and so the nudge's valid-
    # name list) empty. A real session has tools granted.
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda *a, **k: set(), raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


_BAD_CALL = 'data: ' + json.dumps(
    {"type": "tool_calls", "calls": [{"name": "search_files", "arguments": "{}"}]}
) + '\n\n'


def test_unknown_tool_name_nudges_then_recovers(monkeypatch):
    _patch_common(monkeypatch)
    seen = []          # messages passed to the model on each round
    state = {"round": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append([dict(m) for m in messages])
        state["round"] += 1
        if state["round"] == 1:
            # Hallucinated tool name, no text at all.
            yield _BAD_CALL
            yield "data: [DONE]\n\n"
        else:
            yield f'data: {json.dumps({"delta": "Here is the answer from context."})}\n\n'
            yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-x",
        [{"role": "user", "content": "find the mokka files"}],
        max_rounds=4, relevant_tools={"bash"},
    )
    joined = "".join(_collect(gen))

    # Recovered to a real answer instead of the empty-response dead-end.
    assert "Here is the answer from context." in joined
    assert "empty response" not in joined

    # The corrective nudge reached the second round's message list, naming the
    # bad tool and the exact valid names available.
    assert len(seen) >= 2, "model was not given a second round"
    nudges = [m for m in seen[1]
              if m.get("role") == "system" and "search_files" in (m.get("content") or "")]
    assert nudges, seen[1]
    assert "bash" in nudges[-1]["content"]


def test_unknown_tool_name_nudge_is_capped(monkeypatch):
    _patch_common(monkeypatch)

    async def _fake_stream(_candidates, messages, **kwargs):
        # Never recovers — always emits the same bad call.
        yield _BAD_CALL
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-x",
        [{"role": "user", "content": "find the mokka files"}],
        max_rounds=8, relevant_tools={"bash"},
    )
    joined = "".join(_collect(gen))

    # It must terminate (the shared cap prevents an infinite nudge loop) and,
    # having never produced text or a valid call, end with the empty-response
    # message rather than hanging.
    assert "empty response" in joined


def test_native_call_with_text_is_not_nudged(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append([dict(m) for m in messages])
        # Bad call BUT the model also answered in text — not a silent dead-end,
        # so no nudge; the text stands as the answer.
        yield _BAD_CALL
        yield f'data: {json.dumps({"delta": "I could not find a matching tool, but here is what I know."})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-x",
        [{"role": "user", "content": "find the mokka files"}],
        max_rounds=4, relevant_tools={"bash"},
    )
    joined = "".join(_collect(gen))

    assert "here is what I know" in joined
    # Only one round happened — no corrective second round was needed.
    assert len(seen) == 1, seen
