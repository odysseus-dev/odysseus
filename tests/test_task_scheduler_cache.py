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


async def test_cached_owner_cancellation_wakes_waiters_and_allows_retry():
    key = ("cancelled-owner",)
    fetch_started = asyncio.Event()

    async def blocked_fetch():
        fetch_started.set()
        await asyncio.Event().wait()

    owner = asyncio.create_task(task_scheduler._cached(key, 60, blocked_fetch))
    await fetch_started.wait()

    async def unexpected_fetch():
        pytest.fail("a waiter must share the owner's fetch")

    waiter = asyncio.create_task(task_scheduler._cached(key, 60, unexpected_fetch))
    await asyncio.sleep(0)

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=1)

    assert key not in task_scheduler._shared_cache_pending

    async def retry_fetch():
        return "fresh"

    result = await asyncio.wait_for(
        task_scheduler._cached(key, 60, retry_fetch),
        timeout=1,
    )
    assert result == "fresh"


async def test_cached_waiter_cancellation_does_not_cancel_shared_fetch():
    key = ("cancelled-waiter",)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def blocked_fetch():
        fetch_started.set()
        await release_fetch.wait()
        return "shared"

    owner = asyncio.create_task(task_scheduler._cached(key, 60, blocked_fetch))
    await fetch_started.wait()

    async def unexpected_fetch():
        pytest.fail("a waiter must share the owner's fetch")

    waiter = asyncio.create_task(task_scheduler._cached(key, 60, unexpected_fetch))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    pending = task_scheduler._shared_cache_pending[key]
    assert not pending.cancelled()
    assert not owner.done()

    release_fetch.set()
    assert await asyncio.wait_for(owner, timeout=1) == "shared"
    assert key not in task_scheduler._shared_cache_pending

    async def cache_miss():
        pytest.fail("the successful owner result should be cached")

    assert await task_scheduler._cached(key, 60, cache_miss) == "shared"
