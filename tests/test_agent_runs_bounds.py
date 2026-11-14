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
    assert wrong['requested'] is False and {k: quick[k] for k in ('requested', 'confirmed', 'state')} == {'requested': True, 'confirmed': True, 'state': 'stopped'} and quick['tools_may_still_run'] is False
    _, slow = asyncio.run(scenario(0.6, 0.1))
    assert {k: slow[k] for k in ('requested', 'confirmed', 'state')} == {'requested': True, 'confirmed': False, 'state': 'stopping'}


def _slow_client_scenario(buffer_bytes, events, size, name):
    """One producer, a fast client, and a client that stops reading after its first event until the run is over. Returns (run, fast, slow, peak)."""
    async def scenario():
        gate = asyncio.Event()
        peak = {'events': 0, 'bytes': 0}

        async def gen():
            for i in range(events):
                yield 'data: ' + json.dumps({'type': 'tool_result', 'i': i, 'data': 'x' * size}) + '\n\n'
                for sub in list(run.subscribers):
                    peak['events'] = max(peak['events'], sub.qsize())
                    peak['bytes'] = max(peak['bytes'], sub.pending_bytes)
                await asyncio.sleep(0)
            yield 'data: [DONE]\n\n'
        run = agent_runs.start(name, gen())
        fast, slow = [], []

        async def fast_client():
            async for ev in agent_runs.subscribe(name, run):
                fast.append(ev)

        async def slow_client():
            async for ev in agent_runs.subscribe(name, run):
                slow.append(ev)
                if len(slow) == 1:
                    await gate.wait()                       # not reading while the run produces everything

        tasks = [asyncio.ensure_future(fast_client()), asyncio.ensure_future(slow_client())]
        await run.task
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        return run, fast, slow, peak
    return asyncio.run(scenario())


def _indexes(events):
    return [json.loads(e.split('data: ', 1)[1])['i'] for e in events if e.startswith('data: {') and '"tool_result"' in e]


def test_a_client_that_stops_reading_cannot_grow_memory_and_catches_up_losslessly_from_the_replay_buffer(monkeypatch):
    monkeypatch.setattr(agent_runs, '_MAX_BUFFER_BYTES', 400 * 1024 * 1024)
    run, fast, slow, peak = _slow_client_scenario(None, 1500, 64 * 1024, 'slow-1')
    assert peak['bytes'] > agent_runs._SUBSCRIBER_MAX_BYTES // 2                      # the non-reading client really did fill its queue up to the cap
    assert peak['events'] <= agent_runs._SUBSCRIBER_MAX_EVENTS and peak['bytes'] <= agent_runs._SUBSCRIBER_MAX_BYTES + 70_000   # per-client memory is bounded
    assert _indexes(fast) == list(range(1500))                                      # a keeping-up client loses nothing
    assert _indexes(slow) == list(range(1500))                                      # the lagging one gets every event, in order, once
    assert agent_runs._REPLAY_GAP_EVENT not in slow and slow[-1].strip() == 'data: [DONE]'


def test_a_lagging_client_is_told_when_the_replay_buffer_no_longer_holds_what_it_missed(monkeypatch):
    monkeypatch.setattr(agent_runs, '_MAX_BUFFER_BYTES', 3 * 1024 * 1024)
    run, fast, slow, peak = _slow_client_scenario(None, 600, 64 * 1024, 'slow-2')
    assert _indexes(fast) == list(range(600))
    got = _indexes(slow)
    assert agent_runs._REPLAY_GAP_EVENT in slow, 'a hole in the stream must be announced, never silent'
    assert got[0] == 0 and got == sorted(set(got)) and got[-1] == 599 and len(got) < 600       # in order, no duplicates, ends at the last event
    assert slow[-1].strip() == 'data: [DONE]' and slow.index(agent_runs._REPLAY_GAP_EVENT) > 0
    assert peak['bytes'] <= agent_runs._SUBSCRIBER_MAX_BYTES + 70_000


def test_the_end_of_a_run_reaches_a_client_whose_queue_was_full():
    run, fast, slow, peak = _slow_client_scenario(None, 400, 4_000, 'slow-3')
    assert slow[-1].strip() == 'data: [DONE]' and _indexes(slow) == list(range(400))


def test_stop_goes_through_stopping_to_stopped_and_names_the_tool_it_interrupted():
    async def scenario():
        async def gen():
            try:
                yield 'data: {"type": "tool_start", "tool": "terminal", "command": "sleep 60"}\n\n'
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(0.4)                   # unwinding takes a moment
        run = agent_runs.start('stop-tools', gen())
        await asyncio.sleep(0.05)
        wrong = agent_runs.run_state('stop-tools', 'someone-elses-run')
        before = agent_runs.run_state('stop-tools', run.run_id)
        result = await agent_runs.stop_confirmed('stop-tools', run.run_id, timeout=0.1)
        mid = agent_runs.run_state('stop-tools', run.run_id)
        await asyncio.wait({run.task}, timeout=3)
        after = agent_runs.run_state('stop-tools', run.run_id)
        return wrong, before, result, mid, after
    wrong, before, result, mid, after = asyncio.run(scenario())
    assert wrong['state'] == 'none' and before['state'] == 'running' and before['confirmed'] is False
    assert result['requested'] is True and result['confirmed'] is False and result['state'] == 'stopping'
    assert mid == {'state': 'stopping', 'confirmed': False, 'interrupted_tools': [], 'tools_may_still_run': False}
    assert after['state'] == 'stopped' and after['confirmed'] is True
    assert after['interrupted_tools'] == [{'tool': 'terminal', 'command': 'sleep 60'}] and after['tools_may_still_run'] is True


def test_a_stop_that_races_a_finishing_run_does_not_leave_it_stuck_in_stopping():
    async def scenario():
        async def gen():
            yield 'data: {"type": "tool_start", "tool": "t", "command": "c"}\n\n'
            yield 'data: {"type": "tool_output", "tool": "t"}\n\n'
            yield 'data: [DONE]\n\n'
        run = agent_runs.start('stop-race', gen())
        run.status = 'stopping'                              # the Stop landed just as the generator was ending
        await asyncio.wait({run.task}, timeout=3)
        return run, agent_runs.run_state('stop-race', run.run_id)
    run, state = asyncio.run(scenario())
    assert run.status == 'done' and state['state'] == 'done' and state['confirmed'] and state['interrupted_tools'] == []
