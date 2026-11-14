"""Detached-run replay memory is bounded, live subscribers lose nothing, and Stop says whether the run really ended."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import agent_runs  # noqa: E402


def sse(kind, size):
    return 'data: ' + json.dumps({'type': kind, 'data': 'x' * size}) + '\n\n'


def test_replay_buffer_is_bounded_live_subscribers_get_everything_and_late_ones_are_told(monkeypatch):
    monkeypatch.setattr(agent_runs, '_MAX_BUFFER_BYTES', 200_000)

    async def scenario():
        async def gen():
            for _ in range(100):
                yield sse('tool_result', 10_000)
                await asyncio.sleep(0)
            yield 'data: [DONE]\n\n'
        run = agent_runs.start('bounds-1', gen())
        live = []

        async def consume():
            async for ev in agent_runs.subscribe('bounds-1', run):
                live.append(ev)
        await asyncio.gather(consume(), run.task)
        late = [ev async for ev in agent_runs.subscribe('bounds-1', run)]
        return run, live, late
    run, live, late = asyncio.run(scenario())
    assert len([e for e in live if 'tool_result' in e]) == 100                      # a connected client never loses an event
    assert run.buffer_bytes <= 200_000 + 11_000 and run.head > 0                    # retained replay stays near the cap
    assert late[0] == agent_runs._REPLAY_GAP_EVENT                                  # a late reconnect is told that older output is gone
    assert late[-1].strip() == 'data: [DONE]' and 0 < len([e for e in late if 'tool_result' in e]) < 100


def test_one_oversize_event_is_shortened_in_the_replay_copy_only(monkeypatch):
    big = sse('tool_result', 600_000)

    async def scenario():
        async def gen():
            yield big
            yield 'data: [DONE]\n\n'
        run = agent_runs.start('bounds-2', gen())
        live = [ev async for ev in agent_runs.subscribe('bounds-2', run)]
        return run, live
    run, live = asyncio.run(scenario())
    kept = [e for e in run.buffer if e and 'tool_result' in e][0]
    assert len(kept) < 80_000 and json.loads(kept.split('data: ', 1)[1])['type'] == 'tool_result' and 'not kept for replay' in kept


def test_stop_reports_requested_versus_confirmed():
    async def scenario(cleanup_seconds, wait):
        async def gen():
            try:
                yield 'data: {"type": "delta"}\n\n'
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(cleanup_seconds)        # a tool call that needs time to unwind
        run = agent_runs.start('stop-%s' % cleanup_seconds, gen())
        await asyncio.sleep(0.05)
        wrong = await agent_runs.stop_confirmed('stop-%s' % cleanup_seconds, 'not-this-run', timeout=0.1)
        result = await agent_runs.stop_confirmed('stop-%s' % cleanup_seconds, run.run_id, timeout=wait)
        await asyncio.wait({run.task}, timeout=2)
        return wrong, result
    wrong, quick = asyncio.run(scenario(0, 1.0))
    assert wrong['requested'] is False and quick == {'requested': True, 'confirmed': True, 'state': 'stopped'}
    _, slow = asyncio.run(scenario(0.6, 0.1))
    assert slow == {'requested': True, 'confirmed': False, 'state': 'stopping'}
