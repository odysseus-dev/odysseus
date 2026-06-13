"""Hub broker: verify -> dedupe -> audit-chain -> gate-or-deliver. Spec §1, §3.

The hub is PASSIVE message plane: it never executes intents. Gated intents
park in the approval queue (GatedIntent); everything else lands in the
destination company's inbox (EnvelopeRecord.delivered=False until polled).
Audit ledger is hash-chained: audit_hash = sha256(prev_hash || canonical).
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, UTC

from core.database import get_db_session, EnvelopeRecord, GatedIntent
from .envelope import Envelope, canonical_bytes, verify_envelope, classify_intent
from .registry import company_public_key

GENESIS = "GENESIS"
DEFAULT_GATE_TTL_HOURS = 24


class HubError(ValueError):
    pass


def _chain_hash(prev_hash: str, env: Envelope) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical_bytes(env)).hexdigest()


def ingest(env: Envelope, signature_hex: str) -> dict:
    """Accept one envelope into the hub. Raises HubError on any rejection."""
    pub = company_public_key(env.from_company)
    if not pub:
        raise HubError(f"unknown sender company {env.from_company!r}")
    if not verify_envelope(env, signature_hex, pub):
        raise HubError("signature verification failed")

    with get_db_session() as db:
        if db.get(EnvelopeRecord, env.message_id):
            raise HubError(f"replay: message_id {env.message_id!r} already ingested")

        last = (db.query(EnvelopeRecord)
                  .order_by(EnvelopeRecord.created_at.desc(),
                            EnvelopeRecord.message_id.desc())
                  .first())
        prev_hash = last.audit_hash if last else GENESIS
        audit_hash = _chain_hash(prev_hash, env)

        gated_class = classify_intent(env.intent)
        gated = gated_class is not None

        rec = EnvelopeRecord(
            message_id=env.message_id, conversation_id=env.conversation_id,
            causation_id=env.causation_id,
            from_subject=env.from_subject, from_company=env.from_company,
            to_subject=env.to_subject, to_company=env.to_company,
            intent=env.intent, status=env.status.value,
            trust_level="untrusted",          # inbound is ALWAYS untrusted data
            requires_human_approval=gated,
            payload_json=json.dumps(env.payload, sort_keys=True),
            signature=signature_hex,
            audit_hash=audit_hash, prev_audit_hash=prev_hash,
            delivered=gated,                  # gated: never enters inbox as-is
        )
        db.add(rec)
        if gated:
            db.add(GatedIntent(
                id=str(uuid.uuid4()), envelope_message_id=env.message_id,
                company_id=env.from_company, gated_class=gated_class,
                state="proposed",
                expires_at=datetime.now(UTC) + timedelta(hours=DEFAULT_GATE_TTL_HOURS),
            ))
        db.commit()
        return {"message_id": env.message_id, "audit_hash": audit_hash,
                "prev_audit_hash": prev_hash, "gated": gated,
                "gated_class": gated_class}


def poll_inbox(company_id: str, limit: int = 50) -> list[dict]:
    """Fetch undelivered envelopes for a company and mark them delivered."""
    with get_db_session() as db:
        rows = (db.query(EnvelopeRecord)
                  .filter_by(to_company=company_id, delivered=False)
                  .order_by(EnvelopeRecord.created_at.asc())
                  .limit(limit).all())
        out = []
        for r in rows:
            r.delivered = True
            out.append({
                "message_id": r.message_id, "conversation_id": r.conversation_id,
                "from_company": r.from_company, "intent": r.intent,
                "status": r.status, "trust_level": r.trust_level,
                "payload": json.loads(r.payload_json),
            })
        db.commit()
        return out
