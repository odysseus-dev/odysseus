import asyncio

from src import agent_runs


def test_done_is_published_only_after_generator_cleanup():
    async def scenario():
        session_id = "terminal-order-test"
        cleanup_finished = asyncio.Event()

        async def stream():
            yield 'data: {"delta":"answer"}\n\n'
            yield "data: [DONE]\n\n"
            await asyncio.sleep(0)
            cleanup_finished.set()

        run = agent_runs.start(session_id, stream())
        received = []
        async for event in agent_runs.subscribe(session_id, run):
            received.append(event)
            if event.strip() == "data: [DONE]":
                assert cleanup_finished.is_set()

        assert [event.strip() for event in received] == [
            'data: {"delta":"answer"}',
            "data: [DONE]",
        ]
        agent_runs._RUNS.pop(session_id, None)

    asyncio.run(scenario())


def test_finish_request_is_bound_to_the_exact_active_run():
    async def scenario():
        session_id = 'finish-editor-run-test'
        gate = asyncio.Event()

        async def stream():
            await gate.wait()
            yield 'data: [DONE]\n\n'

        run = agent_runs.start(session_id, stream())
        await asyncio.sleep(0)
        assert not agent_runs.request_finish(session_id, 'stale-run-id')
        assert not agent_runs.should_finish(session_id)
        assert agent_runs.request_finish(session_id, run.run_id)
        assert agent_runs.should_finish(session_id)
        gate.set()
        async for _ in agent_runs.subscribe(session_id, run):
            pass
        assert not agent_runs.should_finish(session_id)
        agent_runs._RUNS.pop(session_id, None)

    asyncio.run(scenario())
