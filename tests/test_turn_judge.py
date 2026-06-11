import pytest

from src import turn_judge


@pytest.mark.asyncio
async def test_evaluate_turn_parses_stream_delta(monkeypatch):
    async def fake_stream_llm(*args, **kwargs):
        yield 'data: {"delta":"{\\"failure\\":false,"}\n\n'
        yield 'data: {"delta":"\\"reason\\":\\"ok\\",\\"severity\\":\\"none\\"}"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(turn_judge, "stream_llm", fake_stream_llm)

    result = await turn_judge.evaluate_turn("done", "", model="m", endpoint_url="u")

    assert result == {"failure": False, "reason": "ok", "severity": "none"}


@pytest.mark.asyncio
async def test_evaluate_turn_parses_tool_call_arguments(monkeypatch):
    async def fake_stream_llm(*args, **kwargs):
        yield (
            'data: {"type":"tool_calls","calls":[{"name":"evaluate",'
            '"arguments":"{\\"failure\\":true,\\"reason\\":\\"bad\\",\\"severity\\":\\"high\\"}"}]}\n\n'
        )

    monkeypatch.setattr(turn_judge, "stream_llm", fake_stream_llm)

    result = await turn_judge.evaluate_turn("failed", "", model="m", endpoint_url="u")

    assert result == {"failure": True, "reason": "bad", "severity": "high"}


@pytest.mark.asyncio
async def test_evaluate_turn_falls_back_when_stream_fails(monkeypatch):
    async def fake_stream_llm(*args, **kwargs):
        raise TypeError("bad call")
        yield ""

    monkeypatch.setattr(turn_judge, "stream_llm", fake_stream_llm)

    result = await turn_judge.evaluate_turn(
        "failed",
        "Traceback\nexit code: 1",
        model="m",
        endpoint_url="u",
    )

    assert result["failure"] is True
    assert "traceback" in result["reason"]
