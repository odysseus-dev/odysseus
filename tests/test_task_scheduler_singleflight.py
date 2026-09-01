import asyncio

import pytest

from src import task_scheduler


@pytest.fixture(autouse=True)
def clear_shared_cache():
    task_scheduler._shared_cache.clear()
    task_scheduler._shared_cache_pending.clear()
    yield
    task_scheduler._shared_cache.clear()
    task_scheduler._shared_cache_pending.clear()


@pytest.mark.asyncio
async def test_owner_cancellation_releases_waiters_and_allows_retry():
    started = asyncio.Event()

    async def blocked_fetch():
        started.set()
        await asyncio.Future()

    owner = asyncio.create_task(task_scheduler._cached(("test",), 60, blocked_fetch))
    await started.wait()
    waiter = asyncio.create_task(task_scheduler._cached(("test",), 60, blocked_fetch))

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert ("test",) not in task_scheduler._shared_cache_pending

    calls = 0

    async def retry_fetch():
        nonlocal calls
        calls += 1
        return "recovered"

    assert await task_scheduler._cached(("test",), 60, retry_fetch) == "recovered"
    assert calls == 1


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_cancel_shared_fetch():
    release = asyncio.Event()

    async def fetch():
        await release.wait()
        return "value"

    owner = asyncio.create_task(task_scheduler._cached(("test",), 60, fetch))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(task_scheduler._cached(("test",), 60, fetch))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    assert await owner == "value"
    assert await task_scheduler._cached(("test",), 60, fetch) == "value"


@pytest.mark.asyncio
async def test_fetch_error_releases_pending_callers():
    failure = RuntimeError("fetch failed")
    started = asyncio.Event()

    async def failing_fetch():
        started.set()
        raise failure

    owner = asyncio.create_task(task_scheduler._cached(("test",), 60, failing_fetch))
    await started.wait()
    waiter = asyncio.create_task(task_scheduler._cached(("test",), 60, failing_fetch))

    with pytest.raises(RuntimeError, match="fetch failed"):
        await owner
    with pytest.raises(RuntimeError, match="fetch failed"):
        await waiter

    assert ("test",) not in task_scheduler._shared_cache_pending
