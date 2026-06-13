# tests/test_platform_mission.py
"""Big Boss mission loop: create/plan/dispatch/refresh/report. Spec §6."""
import uuid

import pytest

from services.business_platform import mission, registry
from services.business_platform.envelope import Envelope, sign_envelope
from services.business_platform.hub import ingest
from services.business_platform.approval import pending_for_manager, approve, deny


@pytest.fixture()
def owner_and_company():
    cid = f"mis-co-{uuid.uuid4().hex[:6]}"
    registry.create_company(cid, "general_office", cid,
                            manager_principal_id="human:oleg")
    mission.ensure_big_boss("oleg")
    return "oleg", cid


def _reply(company, conversation_id, status="finished", summary="done"):
    """Simulate the executing company reporting a result back to Big Boss."""
    env = Envelope(
        message_id=f"reply-{uuid.uuid4().hex[:12]}",
        conversation_id=conversation_id,
        idempotency_key=f"reply-{uuid.uuid4().hex[:12]}",
        from_company=company, to_company=mission.BIG_BOSS_COMPANY,
        issued_at="2026-06-13T12:00:00Z",
        intent="status.report", status=status, payload={"summary": summary})
    ingest(env, sign_envelope(env, registry.company_private_key(company)))


def test_ensure_big_boss_idempotent(owner_and_company):
    a = mission.ensure_big_boss("oleg")
    b = mission.ensure_big_boss("oleg")
    assert a["id"] == b["id"] == mission.BIG_BOSS_COMPANY


def test_create_and_plan_steerable(owner_and_company):
    owner, cid = owner_and_company
    m = mission.create_mission("grow traffic", owner,
                               [{"company": cid, "intent": "status.report",
                                 "task": "audit"}])
    assert m["status"] == "planning" and len(m["tasks"]) == 1
    m2 = mission.update_plan(m["id"], [
        {"company": cid, "intent": "status.report", "task": "audit v2"},
        {"company": cid, "intent": "quote.create", "task": "retainer quote"}])
    assert len(m2["tasks"]) == 2


def test_ungated_task_dispatches_then_completes_on_reply(owner_and_company):
    owner, cid = owner_and_company
    m = mission.create_mission("g", owner,
                               [{"company": cid, "intent": "status.report",
                                 "task": "do a thing"}])
    d = mission.dispatch_mission(m["id"])
    assert d["status"] == "running"
    assert d["tasks"][0]["status"] == "dispatched"
    _reply(cid, d["tasks"][0]["conversation_id"], "finished", "all good")
    r = mission.refresh_mission(m["id"])
    assert r["tasks"][0]["status"] == "completed"
    assert r["tasks"][0]["result"] == "all good"
    assert r["status"] == "completed" and "Mission report" in r["report"]


def _gated_intent_for(manager, env_message_id):
    """Find the queued gated intent for a specific dispatched envelope
    (shared in-memory DB accumulates intents across tests)."""
    return [p for p in pending_for_manager(manager)
            if p["envelope_message_id"] == env_message_id]


def test_gated_task_blocks_until_manager_approves(owner_and_company):
    owner, cid = owner_and_company
    m = mission.create_mission("g", owner,
                               [{"company": cid, "intent": "quote.create",
                                 "task": "prepare a quote"}])
    d = mission.dispatch_mission(m["id"])
    # gated -> parked in approval queue, task blocked
    assert d["tasks"][0]["status"] == "blocked"
    assert mission.refresh_mission(m["id"])["tasks"][0]["status"] == "blocked"
    # owner manages bigboss (the SENDER) -> approves
    pend = _gated_intent_for("human:oleg", d["tasks"][0]["envelope_message_id"])
    assert pend
    approve(pend[0]["id"], "human:oleg")
    r1 = mission.refresh_mission(m["id"])
    assert r1["tasks"][0]["status"] == "dispatched"
    _reply(cid, d["tasks"][0]["conversation_id"], "finished", "quoted")
    r2 = mission.refresh_mission(m["id"])
    assert r2["tasks"][0]["status"] == "completed" and r2["status"] == "completed"


def test_gated_denied_task_fails(owner_and_company):
    owner, cid = owner_and_company
    m = mission.create_mission("g", owner,
                               [{"company": cid, "intent": "payment.refund",
                                 "task": "refund X"}])
    d = mission.dispatch_mission(m["id"])
    pend = _gated_intent_for("human:oleg", d["tasks"][0]["envelope_message_id"])
    assert pend
    deny(pend[0]["id"], "human:oleg", reason="not allowed")
    r = mission.refresh_mission(m["id"])
    assert r["tasks"][0]["status"] == "failed"
    assert r["status"] == "failed"


def test_reply_error_marks_task_failed(owner_and_company):
    owner, cid = owner_and_company
    m = mission.create_mission("g", owner,
                               [{"company": cid, "intent": "status.report",
                                 "task": "thing"}])
    d = mission.dispatch_mission(m["id"])
    _reply(cid, d["tasks"][0]["conversation_id"], "error", "blew up")
    r = mission.refresh_mission(m["id"])
    assert r["tasks"][0]["status"] == "failed" and r["status"] == "failed"


def test_principal_shaped_owner_can_approve(owner_and_company):
    """Owner stored as 'human:oleg' must not become 'human:human:oleg' —
    they have to be able to approve Big Boss's gated tasks (codex P1)."""
    _, cid = owner_and_company
    m = mission.create_mission("g", "human:oleg",
                               [{"company": cid, "intent": "quote.create",
                                 "task": "quote"}])
    d = mission.dispatch_mission(m["id"])
    assert registry.is_manager_of("human:oleg", mission.BIG_BOSS_COMPANY)
    pend = _gated_intent_for("human:oleg", d["tasks"][0]["envelope_message_id"])
    assert pend  # visible to the owner


def test_second_owner_gets_manager_rights(owner_and_company):
    """Big Boss persists across owners; a later owner must still be able to
    approve their own mission's gated tasks (codex P2)."""
    _, cid = owner_and_company           # first owner = oleg already registered
    registry.create_company("bob-co", "general_office", "Bob Co",
                            manager_principal_id="human:bob")
    m = mission.create_mission("bob mission", "bob",
                               [{"company": "bob-co", "intent": "quote.create",
                                 "task": "quote"}])
    d = mission.dispatch_mission(m["id"])
    assert registry.is_manager_of("human:bob", mission.BIG_BOSS_COMPANY)
    pend = _gated_intent_for("human:bob", d["tasks"][0]["envelope_message_id"])
    assert pend
    approve(pend[0]["id"], "human:bob")
    assert mission.refresh_mission(m["id"])["tasks"][0]["status"] == "dispatched"


def test_create_requires_goal_and_tasks(owner_and_company):
    owner, cid = owner_and_company
    with pytest.raises(mission.MissionError):
        mission.create_mission("", owner,
                               [{"company": cid, "intent": "x", "task": "y"}])
    with pytest.raises(mission.MissionError):
        mission.create_mission("g", owner, [])
    with pytest.raises(mission.MissionError):
        mission.create_mission("g", owner, [{"company": cid, "intent": "x"}])
