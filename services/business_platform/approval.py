"""Manager approval queue over GatedIntent. Spec §3.

approve() releases the parked envelope into the destination inbox with
status 'approved'; deny() keeps it undelivered forever. Only a manager of
the ORIGIN company may decide (the company whose agent proposed the action).
"""
from datetime import datetime, UTC

from core.database import get_db_session, GatedIntent, EnvelopeRecord, Principal
from .registry import is_manager_of


class ApprovalError(ValueError):
    pass


def pending_for_manager(principal_id: str) -> list[dict]:
    """All proposed intents in companies this principal manages."""
    with get_db_session() as db:
        managed = [p.company_id for p in
                   db.query(Principal).filter_by(id=principal_id, is_manager=True)]
        if not managed:
            return []
        rows = (db.query(GatedIntent)
                  .filter(GatedIntent.company_id.in_(managed),
                          GatedIntent.state == "proposed")
                  .order_by(GatedIntent.created_at.asc()).all())
        return [{"id": g.id, "company_id": g.company_id,
                 "gated_class": g.gated_class,
                 "envelope_message_id": g.envelope_message_id,
                 "expires_at": g.expires_at.isoformat() if g.expires_at else None}
                for g in rows]


def _decide(intent_id: str, principal_id: str, new_state: str,
            reason: str = "") -> dict:
    with get_db_session() as db:
        g = db.get(GatedIntent, intent_id)
        if not g:
            raise ApprovalError(f"gated intent {intent_id!r} not found")
        if g.state != "proposed":
            raise ApprovalError(f"intent already {g.state}")
        if not is_manager_of(principal_id, g.company_id):
            raise ApprovalError(
                f"{principal_id!r} is not a manager of {g.company_id!r}")
        g.state = new_state
        g.decided_by = principal_id
        g.decided_at = datetime.now(UTC)
        rec = db.get(EnvelopeRecord, g.envelope_message_id)
        if new_state == "approved" and rec:
            rec.status = "approved"
            rec.delivered = False        # release into destination inbox
        elif new_state == "denied" and rec:
            rec.status = "denied"        # stays delivered=True: never enters inbox
        db.commit()
        return {"id": g.id, "state": g.state, "decided_by": g.decided_by}


def approve(intent_id: str, principal_id: str) -> dict:
    return _decide(intent_id, principal_id, "approved")


def deny(intent_id: str, principal_id: str, reason: str = "") -> dict:
    return _decide(intent_id, principal_id, "denied", reason)


def expire_stale(now: datetime | None = None) -> int:
    """Mark overdue proposed intents expired. Returns count. Spec §5."""
    now = now or datetime.now(UTC)
    with get_db_session() as db:
        rows = (db.query(GatedIntent)
                  .filter(GatedIntent.state == "proposed",
                          GatedIntent.expires_at.isnot(None),
                          GatedIntent.expires_at < now).all())
        for g in rows:
            g.state = "expired"
        db.commit()
        return len(rows)
