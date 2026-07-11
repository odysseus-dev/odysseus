"""Tests for src/agent/actor.py"""
from __future__ import annotations
import asyncio
import pytest
from src.agent.actor import (
    Actor, ActorMode, ActorStatus, ActorOutcome, ActorRegistry,
)


def test_actor_creation():
    actor = Actor(id="explore-1", session_id="sess-123", mode=ActorMode.SUBAGENT)
    assert actor.id == "explore-1"
    assert actor.session_id == "sess-123"
    assert actor.mode == ActorMode.SUBAGENT
    assert actor.status == ActorStatus.PENDING
    assert actor.parent_id is None


def test_actor_modes():
    assert ActorMode.SUBAGENT.value == "subagent"
    assert ActorMode.PEER.value == "peer"


def test_actor_statuses():
    assert ActorStatus.PENDING.value == "pending"
    assert ActorStatus.RUNNING.value == "running"
    assert ActorStatus.IDLE.value == "idle"


def test_actor_outcomes():
    assert ActorOutcome.SUCCESS.value == "success"
    assert ActorOutcome.FAILURE.value == "failure"
    assert ActorOutcome.CANCELLED.value == "cancelled"


def test_registry_register():
    reg = ActorRegistry()
    actor = Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    assert reg.get("explore-1") is actor


def test_registry_list_by_session():
    reg = ActorRegistry()
    reg.register(Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT))
    reg.register(Actor(id="a2", session_id="s1", mode=ActorMode.PEER))
    reg.register(Actor(id="a3", session_id="s2", mode=ActorMode.SUBAGENT))
    actors = reg.list_by_session("s1")
    assert len(actors) == 2


def test_registry_list_by_parent():
    reg = ActorRegistry()
    reg.register(Actor(id="main", session_id="s1", mode=ActorMode.SUBAGENT))
    reg.register(Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT, parent_id="main"))
    reg.register(Actor(id="explore-2", session_id="s1", mode=ActorMode.SUBAGENT, parent_id="main"))
    children = reg.list_by_parent("main")
    assert len(children) == 2


def test_registry_allocate_id():
    reg = ActorRegistry()
    id1 = reg.allocate_id("explore")
    id2 = reg.allocate_id("explore")
    assert id1 == "explore-1"
    assert id2 == "explore-2"


def test_registry_allocate_id_different_types():
    reg = ActorRegistry()
    id1 = reg.allocate_id("explore")
    id2 = reg.allocate_id("general")
    assert id1 == "explore-1"
    assert id2 == "general-1"


def test_registry_update_status():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    reg.update_status("a1", ActorStatus.RUNNING)
    assert reg.get("a1").status == ActorStatus.RUNNING


def test_registry_update_outcome():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    reg.update_status("a1", ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS)
    assert reg.get("a1").outcome == ActorOutcome.SUCCESS


def test_registry_list_active():
    reg = ActorRegistry()
    reg.register(Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.RUNNING))
    reg.register(Actor(id="a2", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.IDLE))
    active = reg.list_active()
    assert len(active) == 1
    assert active[0].id == "a1"


def test_registry_render_for_agent():
    reg = ActorRegistry()
    reg.register(Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.RUNNING))
    reg.register(Actor(id="general-1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS))
    text = reg.render_for_agent()
    assert "explore-1" in text
    assert "running" in text.lower()


@pytest.mark.asyncio
async def test_actor_wait_timeout():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    with pytest.raises(asyncio.TimeoutError):
        await reg.wait("a1", timeout=0.1)
