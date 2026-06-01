"""Tests for the in-process approval channel (src/approvals.py).

Driven with asyncio.run() so they don't depend on pytest-asyncio.
"""
import asyncio

from src import approvals


def test_remember_is_per_session():
    approvals.clear_session("s1")
    assert not approvals.is_remembered("s1", "bash")
    approvals.remember("s1", "bash")
    assert approvals.is_remembered("s1", "bash")
    assert not approvals.is_remembered("s2", "bash")      # other session unaffected
    assert not approvals.is_remembered(None, "bash")       # no session -> always ask
    approvals.clear_session("s1")
    assert not approvals.is_remembered("s1", "bash")


def test_resolve_unknown_id_returns_false():
    assert approvals.resolve("does-not-exist", True) is False


def test_register_resolve_roundtrip():
    async def go():
        aid = approvals.new_id()
        approvals.register(aid)

        async def _decide():
            await asyncio.sleep(0.01)
            return approvals.resolve(aid, approved=True, remember=True)

        task = asyncio.create_task(_decide())
        decision = await approvals.await_decision(aid, timeout=2)
        await task
        return decision

    assert asyncio.run(go()) == {"approved": True, "remember": True}


def test_timeout_auto_denies():
    async def go():
        aid = approvals.new_id()
        approvals.register(aid)
        return await approvals.await_decision(aid, timeout=0.05)

    d = asyncio.run(go())
    assert d["approved"] is False and d["timeout"] is True


def test_decision_is_consumed_once():
    async def go():
        aid = approvals.new_id()
        approvals.register(aid)
        first = approvals.resolve(aid, approved=True)
        await approvals.await_decision(aid, timeout=1)   # consumes + discards
        second = approvals.resolve(aid, approved=True)   # entry gone
        return first, second

    first, second = asyncio.run(go())
    assert first is True and second is False


def test_owner_binding_blocks_cross_user_resolve():
    async def go():
        aid = approvals.new_id()
        approvals.register(aid, owner="alice")
        wrong = approvals.resolve(aid, approved=True, requester="bob")    # not the owner
        right = approvals.resolve(aid, approved=True, requester="alice")  # the owner
        approvals.discard(aid)
        return wrong, right

    wrong, right = asyncio.run(go())
    assert wrong is False and right is True
