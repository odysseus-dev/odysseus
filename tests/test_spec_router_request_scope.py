"""Concurrent Spec decisions must remain bound to their own requests."""
import io
import json
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import agent_loop, clean_agent_preview, spec_router


def test_concurrent_identical_requests_do_not_share_router_decisions(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_ROUTER_URL', 'http://router.invalid')
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    monkeypatch.setattr(agent_loop, '_configured_model_tool_surface_raw', lambda *a, **k: 'spec')
    completed = threading.Barrier(2)
    first_done = threading.Event()

    def response(request, timeout):
        if threading.current_thread().name.endswith('_0'):
            choice, probability = 'answer_directly', .99
        else:
            assert first_done.wait(2)
            choice, probability = 't1', .99
        return io.BytesIO(json.dumps({'answers': {'tool': {
            'choice': choice, 'probabilities': {choice: probability},
        }}}).encode())

    monkeypatch.setattr(spec_router.urllib.request, 'urlopen', response)
    schemas = [{'function': {'name': 'manage_notes', 'parameters': {'properties': {}}}}]

    def request(owner):
        spec_router.spec_route_tools('same private question', schemas, return_decision=True)
        if owner == 'alice':
            first_done.set()
        completed.wait(timeout=2)
        return clean_agent_preview.spec_direct_no_tools_p(
            'same private question', 'http://fixture', 'fixture', owner, {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        alice = pool.submit(request, 'alice')
        bob = pool.submit(request, 'bob')
        assert alice.result() == .99
        assert bob.result() is None


@pytest.mark.asyncio
async def test_concurrent_async_sessions_keep_their_own_decisions(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_ROUTER_URL', 'http://router.invalid')
    monkeypatch.setenv('ODYSSEUS_SPEC_DIRECT_NOTOOLS', '1')
    monkeypatch.setattr(agent_loop, '_configured_model_tool_surface_raw', lambda *a, **k: 'spec')

    def response(request, timeout):
        body = json.loads(request.data)
        choice = 'answer_directly' if body['state']['user_message'] == 'alice' else 't1'
        return io.BytesIO(json.dumps({'answers': {'tool': {
            'choice': choice, 'probabilities': {choice: .99},
        }}}).encode())

    monkeypatch.setattr(spec_router.urllib.request, 'urlopen', response)
    ready = asyncio.Event()
    schemas = [{'function': {'name': 'manage_notes', 'parameters': {'properties': {}}}}]

    async def session(owner):
        spec_router.spec_route_tools(owner, schemas, return_decision=True)
        if owner == 'alice':
            await ready.wait()
        else:
            ready.set()
        return clean_agent_preview.spec_direct_no_tools_p(owner, 'http://fixture', 'fixture', owner, {})

    assert await asyncio.gather(session('alice'), session('bob')) == [.99, None]


@pytest.mark.parametrize('response', [
    {'choice': 't0', 'probabilities': {'t0': .99}},
    {'choice': 't1', 'probabilities': {'t1': .99, 't999': .8}},
    {'choice': 't1', 'probabilities': {'t1': 1.5}},
])
def test_invalid_router_response_cannot_choose_a_tool(monkeypatch, response):
    monkeypatch.setenv('ODYSSEUS_SPEC_ROUTER_URL', 'http://router.invalid')
    monkeypatch.setattr(spec_router.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(
        json.dumps({'answers': {'tool': response}}).encode()))
    schemas = [{'function': {'name': 'manage_notes', 'parameters': {'properties': {}}}}]
    picks, decided = spec_router.spec_route_tools('List notes', schemas, return_decision=True)
    assert picks == set()
    assert decided is False


def test_disabled_router_clears_previous_turn_decision(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_SPEC_ROUTER_URL', 'http://router.invalid')
    monkeypatch.setattr(spec_router.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(
        json.dumps({'answers': {'tool': {'choice': 'answer_directly',
                                         'probabilities': {'answer_directly': .99}}}}).encode()))
    spec_router.spec_route_tools('first request', [], return_decision=True)
    assert spec_router.latest_decision()['choice'] == 'answer_directly'
    monkeypatch.delenv('ODYSSEUS_SPEC_ROUTER_URL')
    assert spec_router.spec_route_tools('second request', [], return_decision=True) == (set(), False)
    assert spec_router.latest_decision() is None


@pytest.mark.asyncio
async def test_async_router_does_not_block_event_loop(monkeypatch):
    """When the spec router is stalled or retrying, the asyncio event loop remains
    responsive and concurrent requests can run and complete without blocking."""
    monkeypatch.setenv("ODYSSEUS_SPEC_ROUTER_URL", "http://router.stall.invalid")
    monkeypatch.setenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "2.0")

    stall_started = asyncio.Event()
    unblock_router = asyncio.Event()
    timeline = []

    class _FakeAsyncResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "answers": {
                    "tool": {
                        "choice": "t1",
                        "probabilities": {"t1": 0.95},
                    }
                }
            }

    async def fake_async_post(client, url, json=None, headers=None, timeout=None):
        timeline.append("router_post_start")
        stall_started.set()
        # Simulate network latency or waiting for router:
        # Await unblock event so other tasks must run on the loop while this waits
        await unblock_router.wait()
        timeline.append("router_post_finish")
        return _FakeAsyncResponse()

    monkeypatch.setattr(spec_router.httpx.AsyncClient, "post", fake_async_post)
    schemas = [{"function": {"name": "manage_notes", "parameters": {"properties": {}}}}]

    async def stalled_routing_task():
        timeline.append("task_a_start")
        picks, decided = await spec_router.spec_route_tools_async(
            "Find notes", schemas, return_decision=True
        )
        timeline.append("task_a_end")
        dec = spec_router.latest_decision()
        return picks, decided, dec

    async def concurrent_responsive_task():
        # Wait until the router has started waiting on the event loop
        await stall_started.wait()
        timeline.append("task_b_executed")
        # Task B does work and finishes immediately while router is still stalled!
        unblock_router.set()
        return "task_b_done"

    res_b, res_a = await asyncio.gather(
        concurrent_responsive_task(),
        stalled_routing_task(),
    )

    assert res_b == "task_b_done"
    picks, decided, dec = res_a
    assert picks == {"manage_notes"}
    assert decided is True
    assert dec is not None
    assert dec.get("choice") == "t1"
    # Verify task_b ran and finished while task_a was waiting on the post
    assert timeline == [
        "task_a_start",
        "router_post_start",
        "task_b_executed",
        "router_post_finish",
        "task_a_end",
    ]


@pytest.mark.asyncio
async def test_async_router_cancellation_propagates(monkeypatch):
    """When a request cancels, spec_route_tools_async cancels cleanly."""
    monkeypatch.setenv("ODYSSEUS_SPEC_ROUTER_URL", "http://router.stall.invalid")

    async def hanging_post(*a, **k):
        await asyncio.sleep(10.0)

    monkeypatch.setattr(spec_router.httpx.AsyncClient, "post", hanging_post)
    task = asyncio.create_task(
        spec_router.spec_route_tools_async("hello", [], return_decision=True)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
