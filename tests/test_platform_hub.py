# tests/test_platform_hub.py
"""Hub: signature gate, replay rejection, audit hash chain, gated parking."""
import pytest

from services.business_platform.envelope import (
    Envelope, sign_envelope,
)
from services.business_platform.registry import (
    create_company, company_private_key,
)
from services.business_platform.hub import ingest, poll_inbox, HubError


def _make_company(cid):
    create_company(cid, "travel_agency", cid, manager_principal_id="human:oleg")


def _signed(cid, message_id, intent="status.report", status="finished", to="bigboss"):
    env = Envelope(
        message_id=message_id, conversation_id="c-1", idempotency_key=message_id,
        from_company=cid, to_company=to,
        issued_at="2026-06-13T10:00:00Z", intent=intent, status=status,
        requires_human_approval=False, payload={},
    )
    sig = sign_envelope(env, company_private_key(cid))
    return env, sig


def test_ingest_verifies_signature_and_chains_audit():
    _make_company("hub-c1")
    env1, sig1 = _signed("hub-c1", "hub-m1")
    r1 = ingest(env1, sig1)
    env2, sig2 = _signed("hub-c1", "hub-m2")
    r2 = ingest(env2, sig2)
    assert r1["audit_hash"] != r2["audit_hash"]
    assert r2["prev_audit_hash"] == r1["audit_hash"]


def test_ingest_rejects_bad_signature_and_replay():
    _make_company("hub-c2")
    env, sig = _signed("hub-c2", "hub-m3")
    with pytest.raises(HubError):
        ingest(env, "00" * 64)            # wrong signature
    ingest(env, sig)
    with pytest.raises(HubError):
        ingest(env, sig)                  # replay (same message_id)


def test_gated_intent_parked_not_delivered():
    _make_company("hub-c3")
    _make_company("hub-c4")
    env, sig = _signed("hub-c3", "hub-m4", intent="booking.confirm",
                       status="proposed", to="hub-c4")
    r = ingest(env, sig)
    assert r["gated"] is True
    # not delivered to destination inbox while proposed
    inbox = poll_inbox("hub-c4")
    assert all(e["message_id"] != "hub-m4" for e in inbox)


def test_ungated_message_delivered():
    _make_company("hub-c5")
    _make_company("hub-c6")
    env, sig = _signed("hub-c5", "hub-m5", to="hub-c6")
    ingest(env, sig)
    inbox = poll_inbox("hub-c6")
    assert any(e["message_id"] == "hub-m5" for e in inbox)
    # poll marks delivered: second poll is empty for that message
    inbox2 = poll_inbox("hub-c6")
    assert all(e["message_id"] != "hub-m5" for e in inbox2)
