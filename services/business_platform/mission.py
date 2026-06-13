"""Big Boss mission loop — spec §6 (minimal slice).

plan -> bounded dispatch (signed envelopes over the hub) -> timeline status
-> report. No privileged side channel: missions ride the same hub bus as any
other message, so gated tasks stop at the manager approval queue exactly like
a normal cross-company request.

Big Boss is a registered company ("bigboss") with its own Ed25519 identity;
the owner is its manager, so the owner approves Big Boss's own gated actions.
Task terminal status is derived from envelope state + a reply envelope the
executing company sends back (same conversation_id) — no bespoke channel.
"""
import json
import uuid
from datetime import datetime, UTC
from typing import Optional

from core.database import (
    get_db_session, Mission, MissionTask, EnvelopeRecord, GatedIntent,
)
from . import hub, registry
from .envelope import Envelope, sign_envelope

BIG_BOSS_COMPANY = "bigboss"
_TERMINAL = {"completed", "failed"}


class MissionError(ValueError):
    pass


def ensure_big_boss(owner: str) -> dict:
    """Idempotently register the Big Boss company (manager = the owner)."""
    existing = registry.get_company(BIG_BOSS_COMPANY)
    if existing:
        return existing
    return registry.create_company(
        BIG_BOSS_COMPANY, "platform", "Big Boss",
        manager_principal_id=f"human:{owner}")


def _mission_dict(m: Mission, tasks=None) -> dict:
    return {
        "id": m.id, "goal": m.goal, "owner": m.owner, "status": m.status,
        "report": m.report,
        "tasks": [
            {"id": t.id, "seq": t.seq, "target_company": t.target_company,
             "intent": t.intent, "task": t.task_text, "status": t.status,
             "envelope_message_id": t.envelope_message_id,
             "conversation_id": t.conversation_id, "result": t.result}
            for t in sorted(tasks or [], key=lambda x: x.seq)
        ],
    }


def _validate_tasks(tasks) -> None:
    if not isinstance(tasks, list) or not tasks:
        raise MissionError("a mission needs at least one task")
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise MissionError(f"task[{i}] must be a mapping")
        for k in ("company", "intent", "task"):
            if not str(t.get(k, "")).strip():
                raise MissionError(f"task[{i}] missing {k!r}")


def create_mission(goal: str, owner: str, tasks: list) -> dict:
    """Create a mission (status=planning) with its task plan (pending)."""
    if not str(goal).strip():
        raise MissionError("mission needs a non-empty goal")
    _validate_tasks(tasks)
    ensure_big_boss(owner)
    mission_id = f"mis-{uuid.uuid4().hex[:12]}"
    with get_db_session() as db:
        db.add(Mission(id=mission_id, goal=goal, owner=owner,
                       status="planning"))
        for seq, t in enumerate(tasks):
            tid = f"mt-{uuid.uuid4().hex[:12]}"
            db.add(MissionTask(
                id=tid, mission_id=mission_id, seq=seq,
                target_company=str(t["company"]), intent=str(t["intent"]),
                task_text=str(t["task"]),
                conversation_id=f"mission:{mission_id}:task:{tid}",
                status="pending"))
        db.commit()
    return get_mission(mission_id)


def update_plan(mission_id: str, tasks: list) -> dict:
    """Replace the task plan while the mission is still steerable (planning)."""
    _validate_tasks(tasks)
    with get_db_session() as db:
        m = db.get(Mission, mission_id)
        if not m:
            raise MissionError(f"mission {mission_id!r} not found")
        if m.status != "planning":
            raise MissionError(
                f"mission is {m.status}; plan is only editable while planning")
        db.query(MissionTask).filter_by(mission_id=mission_id).delete()
        for seq, t in enumerate(tasks):
            tid = f"mt-{uuid.uuid4().hex[:12]}"
            db.add(MissionTask(
                id=tid, mission_id=mission_id, seq=seq,
                target_company=str(t["company"]), intent=str(t["intent"]),
                task_text=str(t["task"]),
                conversation_id=f"mission:{mission_id}:task:{tid}",
                status="pending"))
        db.commit()
    return get_mission(mission_id)


def get_mission(mission_id: str) -> Optional[dict]:
    with get_db_session() as db:
        m = db.get(Mission, mission_id)
        if not m:
            return None
        tasks = db.query(MissionTask).filter_by(mission_id=mission_id).all()
        return _mission_dict(m, tasks)


def dispatch_mission(mission_id: str) -> dict:
    """Sign + ingest one envelope per pending task; mission -> running."""
    with get_db_session() as db:
        m = db.get(Mission, mission_id)
        if not m:
            raise MissionError(f"mission {mission_id!r} not found")
        if m.status not in ("planning", "running"):
            raise MissionError(f"mission is {m.status}; cannot dispatch")
        priv = registry.company_private_key(BIG_BOSS_COMPANY)
        if not priv:
            raise MissionError("big boss company has no signing key")
        owner = m.owner
        # snapshot into plain tuples: rows detach when the session closes
        pending = [
            (t.id, t.conversation_id, t.target_company, t.intent, t.task_text)
            for t in (db.query(MissionTask)
                        .filter_by(mission_id=mission_id, status="pending")
                        .order_by(MissionTask.seq.asc()).all())]
        m.status = "running"
        db.commit()

    # ingest outside the session loop (hub manages its own session)
    for tid, conversation_id, target_company, intent, task_text in pending:
        message_id = f"msg-{uuid.uuid4().hex[:16]}"
        env = Envelope(
            message_id=message_id, conversation_id=conversation_id,
            idempotency_key=message_id,
            from_subject=f"human:{owner}", from_company=BIG_BOSS_COMPANY,
            to_company=target_company,
            issued_at=datetime.now(UTC).isoformat(),
            intent=intent, status="proposed",
            payload={"task": task_text, "mission_id": mission_id})
        try:
            res = hub.ingest(env, sign_envelope(env, priv))
            new_status = "blocked" if res.get("gated") else "dispatched"
            _set_task(tid, status=new_status, envelope_message_id=message_id)
        except hub.HubError as e:
            _set_task(tid, status="failed", result=f"dispatch error: {e}")

    return get_mission(mission_id)


def _set_task(task_id: str, **fields) -> None:
    with get_db_session() as db:
        t = db.get(MissionTask, task_id)
        if not t:
            return
        for k, v in fields.items():
            setattr(t, k, v)
        db.commit()


def _reply_for(db, task: MissionTask):
    """A result envelope from the target company back to Big Boss."""
    return (db.query(EnvelopeRecord)
              .filter_by(conversation_id=task.conversation_id,
                         from_company=task.target_company,
                         to_company=BIG_BOSS_COMPANY)
              .order_by(EnvelopeRecord.created_at.desc())
              .first())


def refresh_mission(mission_id: str) -> dict:
    """Recompute non-terminal task statuses from envelope state; when all
    tasks are terminal, synthesize the report and finish the mission."""
    with get_db_session() as db:
        m = db.get(Mission, mission_id)
        if not m:
            raise MissionError(f"mission {mission_id!r} not found")
        tasks = (db.query(MissionTask)
                   .filter_by(mission_id=mission_id)
                   .order_by(MissionTask.seq.asc()).all())
        for t in tasks:
            if t.status in _TERMINAL or t.status == "pending":
                continue
            gi = (db.query(GatedIntent)
                    .filter_by(envelope_message_id=t.envelope_message_id)
                    .first()) if t.envelope_message_id else None
            if gi is not None:
                if gi.state in ("denied", "expired"):
                    t.status, t.result = "failed", f"gated intent {gi.state}"
                    continue
                if gi.state == "proposed":
                    t.status = "blocked"
                    continue
                # approved -> fall through to reply check (dispatched/done)
                t.status = "dispatched"
            reply = _reply_for(db, t)
            if reply is not None:
                payload = _safe_json(reply.payload_json)
                summary = (payload.get("summary") if isinstance(payload, dict)
                           else None) or reply.status
                if reply.status == "error":
                    t.status, t.result = "failed", summary
                else:
                    t.status, t.result = "completed", summary

        if tasks and all(t.status in _TERMINAL for t in tasks):
            failed = [t for t in tasks if t.status == "failed"]
            m.status = "failed" if failed else "completed"
            m.report = _build_report(m, tasks)
        db.commit()
    return get_mission(mission_id)


def _safe_json(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _build_report(m: Mission, tasks) -> str:
    lines = [f"Mission report — {m.goal}", ""]
    done = sum(1 for t in tasks if t.status == "completed")
    lines.append(f"{done}/{len(tasks)} tasks completed.")
    lines.append("")
    for t in sorted(tasks, key=lambda x: x.seq):
        lines.append(
            f"- [{t.status}] {t.target_company}/{t.intent}: "
            f"{t.task_text} — {t.result or ''}".rstrip(" —"))
    return "\n".join(lines)
