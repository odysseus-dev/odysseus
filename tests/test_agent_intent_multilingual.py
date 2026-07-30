import asyncio
import json

import pytest

import src.agent_loop as al
from src.tool_policy import build_effective_tool_policy


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _events(chunks):
    events = []
    for chunk in chunks:
        if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
            continue
        try:
            events.append(json.loads(chunk[6:]))
        except json.JSONDecodeError:
            pass
    return events


def _patch_loop(monkeypatch, response):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": response})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)


def _run_loop(
    response,
    monkeypatch,
    *,
    relevant_tools=frozenset({"bash"}),
    max_rounds=1,
    tool_policy=None,
):
    _patch_loop(monkeypatch, response)
    return _events(
        _collect(
            al.stream_agent_loop(
                "http://local.test/v1",
                "local-model",
                [{"role": "user", "content": "Inspect the system and act if needed."}],
                max_rounds=max_rounds,
                relevant_tools=set(relevant_tools),
                tool_policy=tool_policy,
            )
        )
    )


@pytest.mark.parametrize(
    "response",
    [
        "Jag ska kontrollera loggarna nu.",
        "これからログを確認します。",
        "سأتحقق من السجلات الآن.",
        "Voy a revisar los registros ahora.",
        "我来检查日志。",
    ],
)
def test_multilingual_pending_uses_local_phrase_table(monkeypatch, response):
    assert al._MULTILINGUAL_INTENT_RE.search(response)
    events = _run_loop(response, monkeypatch)
    assert any(event.get("type") == "agent_step" for event in events)


def test_multilingual_terminal_answer_is_not_nudged(monkeypatch):
    response = "ログを確認しました。エラーはありません。"
    assert al._MULTILINGUAL_INTENT_RE.search(response) is None
    events = _run_loop(response, monkeypatch)
    assert not any(event.get("type") == "agent_step" for event in events)
    assert not any(event.get("type") == "intent_nudge_exhausted" for event in events)


def test_multilingual_supervisor_never_calls_a_model(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("intent supervision must not make a classifier call")

    monkeypatch.setattr("src.llm_core.llm_call_async", should_not_run)
    events = _run_loop(
        "Jag ska kontrollera loggarna nu.",
        monkeypatch,
        max_rounds=3,
    )
    assert any(event.get("type") == "agent_step" for event in events)


def test_multilingual_pending_flows_through_existing_nudge_cap(monkeypatch):
    events = _run_loop(
        "Jag ska kontrollera loggarna nu.",
        monkeypatch,
        max_rounds=5,
    )
    guard = next(
        event for event in events if event.get("type") == "intent_nudge_exhausted"
    )
    assert guard["reason"] == "intent_without_action_nudge_cap"
    assert guard["nudges"] == 2


def test_english_fast_path_still_nudges_without_multilingual_scan(monkeypatch):
    events = _run_loop("Let me check the logs", monkeypatch, max_rounds=3)
    assert any(event.get("type") == "agent_step" for event in events)


@pytest.mark.parametrize(
    ("response", "relevant_tools", "tool_policy"),
    [
        (
            "これからログを確認します。",
            {"bash"},
            build_effective_tool_policy(last_user_message="Do not use tools."),
        ),
        (
            "これからログを確認します。" + ("これは長い説明です。" * 50),
            {"bash"},
            None,
        ),
        (
            "これからログを確認します。\n```text\nexample\n```",
            {"bash"},
            None,
        ),
        (
            "これからログを確認します。",
            {"ask_user"},
            None,
        ),
    ],
)
def test_local_multilingual_detector_skips_non_action_context(
    monkeypatch, response, relevant_tools, tool_policy
):
    events = _run_loop(
        response,
        monkeypatch,
        relevant_tools=relevant_tools,
        tool_policy=tool_policy,
    )
    assert not any(event.get("type") == "agent_step" for event in events)
    assert not any(event.get("type") == "intent_nudge_exhausted" for event in events)
