import asyncio

import pytest
import src.llm_core as core


@pytest.mark.asyncio
async def test_exiting_foreground_does_not_decrement_other_waiters(monkeypatch):
    monkeypatch.setattr(core, "_LOCAL_MODEL_LOCKS", {})
    monkeypatch.setattr(core, "_LOCAL_MODEL_CURRENT", {})
    monkeypatch.setattr(core, "_LOCAL_MODEL_WAITING_FOREGROUND", {})
    monkeypatch.setattr(core, "_local_model_gate_enabled", lambda: True)
    monkeypatch.setattr(core, "is_local_endpoint", lambda url: True)
    url = "http://local.test/v1"
    key = core._local_model_gate_key(url)
    entered, release = asyncio.Event(), asyncio.Event()
    async def waiter():
        async with core._local_model_slot(url, "test"):
            entered.set()
            await release.wait()
    async with core._local_model_slot(url, "test"):
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert core._LOCAL_MODEL_WAITING_FOREGROUND[key] == 1
    # The queued request has not resumed yet, so it must still be counted.
    assert core._LOCAL_MODEL_WAITING_FOREGROUND[key] == 1
    await entered.wait()
    assert core._LOCAL_MODEL_WAITING_FOREGROUND[key] == 0
    release.set()
    await task
