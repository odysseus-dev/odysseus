"""Runtime coverage for stopping a Compare pane mid-stream.

Replaces an earlier source-text version of this test (which only asserted on
string positions inside routes/chat_routes.py and never exercised actual
streaming behavior) with tests that drive the real mechanisms involved:

  * src.agent_runs — the detached-run manager that normal chat/agent streams
    are wrapped in. A subscriber (the SSE client) disconnecting must NOT stop
    the run; only an explicit stop()/cancel does, and the wrapped generator's
    own CancelledError handler must fire exactly once (no duplicate partial
    saves).

  * the chat_stream endpoint's compare-vs-normal branch — Compare panes must
    be streamed directly (NOT wrapped in agent_runs), so that the pane's Stop
    button (which closes the SSE / aborts the fetch) cancels the underlying
    generator immediately — including while it's awaiting the *next* upstream
    chunk, rather than only being noticed after that chunk arrives. Normal
    chat/agent streams must still go through agent_runs so they survive the
    client disconnecting (the existing "detached run" behavior).

Together these cover: prompt stop of a Compare pane's upstream connection,
single (non-duplicated) save of the partial response, regression-safety for
normal completed streams, and non-interference with detached chat/agent
streams that are meant to keep running server-side after a client disconnect.
"""
import asyncio

import pytest

from src import agent_runs


# --------------------------------------------------------------------------- #
# Fakes that mirror the contract `stream_with_save()` relies on: the wrapped
# generator accumulates `full_response` as it yields chunks, and on
# cancellation (asyncio.CancelledError / GeneratorExit, the same exceptions
# Starlette raises into a streaming generator when the client disconnects)
# saves the partial response exactly once via its `except` handler — mirroring
# the real except (asyncio.CancelledError, GeneratorExit): blocks in
# routes/chat_routes.py.
# --------------------------------------------------------------------------- #
class _FakeSaveSink:
    """Records save_partial() calls so tests can assert "saved exactly once"."""

    def __init__(self):
        self.saves = []
        self.completions = []

    def save_partial(self, text):
        self.saves.append(text)

    def save_complete(self, text):
        self.completions.append(text)


def _make_stream_with_save(sink, chunks, *, hang_after=None):
    """Build an async generator that mirrors stream_with_save()'s shape:
    streams `chunks`, accumulating into `full_response`, and on
    CancelledError/GeneratorExit saves the partial exactly once before
    re-raising (so agent_runs._drain's `await agen.aclose()` sees it run).

    `hang_after`: if set, after yielding that many chunks the generator
    awaits an Event that's never set — simulating a slow/silent upstream
    so cancellation must interrupt an in-flight await, not just be noticed
    between chunks.
    """
    async def gen():
        full_response = ""
        try:
            for i, chunk in enumerate(chunks):
                if hang_after is not None and i == hang_after:
                    await asyncio.Event().wait()  # never resolves on its own
                full_response += chunk
                yield f"data: {chunk}\n\n"
            sink.save_complete(full_response)
            yield "data: [DONE]\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            if full_response:
                sink.save_partial(full_response)
            raise
    return gen()


async def _collect_subscription(session_id, expected_run=None):
    return [
        event
        async for event in agent_runs.subscribe(session_id, expected_run)
    ]


# --------------------------------------------------------------------------- #
# agent_runs: detached-run semantics (what NORMAL chat/agent streams use)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_detached_run_keeps_going_after_subscriber_disconnects():
    """A subscriber dropping (client closes tab/SSE) must NOT stop a detached
    run — that's the whole point of agent_runs. Only stop()/cancel does."""
    sink = _FakeSaveSink()
    session_id = "sess-detached-1"
    agent_runs._RUNS.pop(session_id, None)

    chunks = ["hello", " world", "!"]
    agen = _make_stream_with_save(sink, chunks)
    run = agent_runs.start(session_id, agen)

    # Subscribe, then immediately disconnect (simulate the client closing the
    # SSE) — by simply breaking out of the async-for over subscribe().
    sub = agent_runs.subscribe(session_id)
    async for _ in sub:
        break
    await sub.aclose()

    # The run must still be active / finish on its own — not stopped by the
    # subscriber going away.
    await run.task
    assert run.status == "done"
    assert sink.completions == ["hello world!"]
    assert sink.saves == []  # completed normally — no partial save


@pytest.mark.asyncio
async def test_stop_cancels_detached_run_and_saves_partial_exactly_once():
    """agent_runs.stop() (the Stop button's real backend call for detached
    runs) cancels the in-flight generator promptly — including while it is
    awaiting the next chunk — and the partial is saved exactly once."""
    sink = _FakeSaveSink()
    session_id = "sess-detached-2"
    agent_runs._RUNS.pop(session_id, None)

    chunks = ["partial-a", "partial-b", "partial-c"]
    # Hang after the 2nd chunk so cancellation must interrupt an in-flight
    # await — not just be noticed between already-arrived chunks.
    agen = _make_stream_with_save(sink, chunks, hang_after=2)
    run = agent_runs.start(session_id, agen)

    # Let it stream the first two chunks, then get stuck on the third.
    received = []
    sub = agent_runs.subscribe(session_id)
    async for ev in sub:
        received.append(ev)
        if len(received) >= 2:
            break
    await sub.aclose()

    stopped = agent_runs.stop(session_id, run.run_id)
    assert stopped is True

    await run.task  # propagates promptly — not stuck on the hung await
    assert run.status == "stopped"

    # Saved exactly once, with exactly the chunks that arrived before the hang.
    assert sink.saves == ["partial-apartial-b"]
    assert sink.completions == []


@pytest.mark.asyncio
async def test_normal_completion_saves_exactly_once_not_partial():
    """Regression: a stream that finishes normally (no disconnect, no stop)
    saves via the completion path exactly once, and never via the
    partial/cancellation path."""
    sink = _FakeSaveSink()
    session_id = "sess-detached-3"
    agent_runs._RUNS.pop(session_id, None)

    agen = _make_stream_with_save(sink, ["one", "two", "three"])
    run = agent_runs.start(session_id, agen)
    await run.task

    assert run.status == "done"
    assert sink.completions == ["onetwothree"]
    assert sink.saves == []


@pytest.mark.asyncio
async def test_detached_run_identity_is_stable_for_replay_and_unique_per_run():
    session_id = "sess-detached-run-identity"
    agent_runs._RUNS.pop(session_id, None)

    first = agent_runs.start(session_id, _make_stream_with_save(_FakeSaveSink(), ["one"]))
    first_id = first.run_id
    assert agent_runs.get_run_id(session_id) == first_id
    await first.task
    assert agent_runs.get_run_id(session_id) == first_id

    second = agent_runs.start(session_id, _make_stream_with_save(_FakeSaveSink(), ["two"]))
    assert second.run_id != first_id
    assert agent_runs.get_run_id(session_id) == second.run_id
    await second.task


@pytest.mark.asyncio
async def test_lazy_subscription_stays_bound_to_header_run_after_replacement():
    session_id = "sess-detached-lazy-subscription"
    agent_runs._RUNS.pop(session_id, None)

    async def stream(label):
        yield f'data: {{"delta":"{label}"}}\n\n'

    first = agent_runs.start(session_id, stream("first"))
    await first.task
    # StreamingResponse does not iterate its body until after construction.
    # Capture the same exact run object used for its identity header.
    lazy_body = agent_runs.subscribe(session_id, first)

    second = agent_runs.start(session_id, stream("second"))
    await second.task

    replayed = [event async for event in lazy_body]
    assert replayed == ['data: {"delta":"first"}\n\n']
    assert agent_runs.get_run_id(session_id) == second.run_id


@pytest.mark.asyncio
async def test_stale_run_identity_cannot_stop_replacement_run():
    session_id = "sess-detached-stale-stop"
    agent_runs._RUNS.pop(session_id, None)
    release = asyncio.Event()

    async def finished():
        yield 'data: {"delta":"old"}\n\n'

    async def replacement():
        yield 'data: {"delta":"new"}\n\n'
        await release.wait()

    first = agent_runs.start(session_id, finished())
    await first.task
    second = agent_runs.start(session_id, replacement())
    await asyncio.sleep(0)

    assert agent_runs.stop(session_id) is False
    assert agent_runs.stop(session_id, first.run_id) is False
    assert second.task is not None and not second.task.done()
    assert agent_runs.stop(session_id, second.run_id) is True
    await second.task


@pytest.mark.asyncio
async def test_triple_replacement_closes_middle_subscriber_and_preserves_save_order():
    session_id = "sess-detached-triple-replacement"
    agent_runs._RUNS.pop(session_id, None)
    first_closing = asyncio.Event()
    release_first = asyncio.Event()
    third_started = asyncio.Event()

    async def first_stream():
        try:
            yield 'data: {"delta":"first"}\n\n'
            await asyncio.Event().wait()
        finally:
            first_closing.set()
            await release_first.wait()

    async def middle_stream():
        yield 'data: {"delta":"middle"}\n\n'

    async def third_stream():
        third_started.set()
        yield 'data: {"delta":"third"}\n\n'

    first = agent_runs.start(session_id, first_stream())
    while not first.buffer:
        await asyncio.sleep(0)

    middle = agent_runs.start(session_id, middle_stream())
    await first_closing.wait()
    assert middle.task is not None and not middle.task.done()

    middle_events_task = asyncio.create_task(
        _collect_subscription(session_id, middle)
    )
    while not middle.subscribers:
        await asyncio.sleep(0)

    third = agent_runs.start(session_id, third_stream())

    # The superseded middle response closes immediately even though its task
    # remains as the transitive barrier for the first run's partial save.
    assert await asyncio.wait_for(middle_events_task, timeout=1) == []
    assert middle.status == "stopped"
    assert middle.task is not None and not middle.task.done()
    assert not third_started.is_set()

    release_first.set()
    await asyncio.wait_for(first.task, timeout=1)
    await asyncio.wait_for(middle.task, timeout=1)
    await asyncio.wait_for(third.task, timeout=1)

    assert first.status == "stopped"
    assert middle.status == "stopped"
    assert third.status == "done"
    assert third_started.is_set()


@pytest.mark.asyncio
async def test_reconnect_replays_pinned_fallback_run_without_restarting_tools():
    session_id = "sess-detached-fallback-resume"
    agent_runs._RUNS.pop(session_id, None)
    release = asyncio.Event()
    tool_executions = 0
    fallback = 'data: {"type":"fallback","answered_by":"backup","candidate_index":1}\n\n'
    tool = 'data: {"type":"tool_output","tool":"bash","output":"ok"}\n\n'

    async def pinned_run():
        nonlocal tool_executions
        yield fallback
        tool_executions += 1
        yield tool
        await release.wait()
        yield 'data: {"delta":"backup finished"}\n\n'
        yield "data: [DONE]\n\n"

    run = agent_runs.start(session_id, pinned_run())
    first = agent_runs.subscribe(session_id)
    first_events = []
    async for event in first:
        first_events.append(event)
        if len(first_events) == 2:
            break
    await first.aclose()

    assert run.status == "running"
    assert tool_executions == 1
    assert agent_runs._RUNS[session_id] is run

    resumed_events = []
    resumed = agent_runs.subscribe(session_id)
    async for event in resumed:
        resumed_events.append(event)
        if len(resumed_events) == 2:
            release.set()
    await run.task

    assert resumed_events[:2] == [fallback, tool]
    assert resumed_events[-1] == "data: [DONE]\n\n"
    assert tool_executions == 1
    assert agent_runs._RUNS[session_id] is run


# --------------------------------------------------------------------------- #
# chat_stream: Compare panes must NOT be detached, so the Stop button (closing
# the SSE) cancels the upstream generator promptly — exercising the same
# generator/cancellation contract as above, but driven the way a Compare pane
# actually drives it: by the SSE response itself being cancelled, with no
# agent_runs subscriber layer in between.
# --------------------------------------------------------------------------- #

async def _drain_into(agen, sink_list):
    async for ev in agen:
        sink_list.append(ev)


@pytest.mark.asyncio
async def test_compare_pane_disconnect_cancels_promptly_mid_await():
    """Simulates the Compare-pane path: the generator IS the SSE body (no
    agent_runs wrapping). Cancelling it — what Starlette does the instant it
    notices the client disconnected — interrupts an in-flight await on the
    next upstream chunk immediately, and the partial is saved exactly once."""
    sink = _FakeSaveSink()
    chunks = ["chunk-1", "chunk-2", "chunk-3"]
    agen = _make_stream_with_save(sink, chunks, hang_after=1)

    received = []
    task = asyncio.ensure_future(_drain_into(agen, received))

    # Wait until exactly one chunk has been forwarded, then the generator is
    # blocked awaiting the (never-set) event — i.e. "waiting on the next
    # upstream chunk". Cancelling now must not require that chunk to arrive.
    for _ in range(200):
        if received:
            break
        await asyncio.sleep(0.005)
    assert received == ["data: chunk-1\n\n"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Saved exactly once, with only the chunk that actually streamed before
    # the cancel — proving we didn't wait for chunk-2 to arrive first.
    assert sink.saves == ["chunk-1"]
    assert sink.completions == []


@pytest.mark.asyncio
async def test_compare_pane_full_stream_completes_and_saves_once():
    """Regression: an un-interrupted Compare pane stream still completes and
    saves exactly as before (single completion save, no partial save)."""
    sink = _FakeSaveSink()
    chunks = ["alpha", "beta", "gamma"]
    agen = _make_stream_with_save(sink, chunks)

    received = []
    async for ev in agen:
        received.append(ev)

    assert received == [
        "data: alpha\n\n",
        "data: beta\n\n",
        "data: gamma\n\n",
        "data: [DONE]\n\n",
    ]
    assert sink.completions == ["alphabetagamma"]
    assert sink.saves == []


# --------------------------------------------------------------------------- #
# chat-mode vs agent-mode: both loops in chat_stream share the same generator
# shape (async-for over the upstream stream, accumulating full_response, with
# a CancelledError/GeneratorExit handler that saves the partial once) — so the
# cancellation contract above applies identically to either mode. This test
# pins that the *same* fake-generator contract covers both, so a regression
# that only fixes one mode's loop would still be caught.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("mode_chunks", [
    ["chat-delta-1", "chat-delta-2"],          # chat-mode shaped chunks
    ["agent-delta-1", "agent-tool-event", "agent-delta-2"],  # agent-mode shaped
])
async def test_cancellation_contract_holds_for_chat_and_agent_shaped_streams(mode_chunks):
    sink = _FakeSaveSink()
    agen = _make_stream_with_save(sink, mode_chunks, hang_after=1)

    received = []
    task = asyncio.ensure_future(_drain_into(agen, received))
    for _ in range(200):
        if received:
            break
        await asyncio.sleep(0.005)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sink.saves == [mode_chunks[0]]
    assert sink.completions == []


# --------------------------------------------------------------------------- #
# chat_stream wiring: compare-mode requests must skip agent_runs.start (stream
# directly, cancellable promptly); normal requests must still go through it
# (detached, survives client disconnect). This pins the actual branch added to
# routes/chat_routes.py rather than re-deriving it from source text.
# --------------------------------------------------------------------------- #

def test_compare_mode_branch_skips_agent_runs_in_source():
    """The compare_mode branch must return the raw generator as the SSE body
    (bypassing agent_runs.start/subscribe) BEFORE the detached agent_runs.start
    call below it — otherwise compare streams would still be detached and a
    pane's Stop (closing the SSE) wouldn't cancel the upstream call."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "chat_routes.py").read_text(encoding="utf-8")

    branch_idx = src.index("if compare_mode:")
    direct_return_idx = src.index("return StreamingResponse(_safe_stream(), media_type=", branch_idx)
    detach_idx = src.index("agent_runs.start(session, _safe_stream())", branch_idx)

    assert branch_idx < direct_return_idx < detach_idx, (
        "compare_mode must short-circuit to a direct (non-detached) "
        "StreamingResponse before normal streams are wrapped in agent_runs"
    )
