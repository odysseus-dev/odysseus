# tests/test_platform_approval.py
"""Approval queue: manager-only decisions, release-on-approve, expiry."""
import pytest

from services.business_platform.envelope import Envelope, sign_envelope
from services.business_platform.registry import create_company, company_private_key
from services.business_platform.hub import ingest, poll_inbox
from services.business_platform.approval import (
    pending_for_manager, approve, deny, ApprovalError,
)


def _gated(cid_from, cid_to, mid):
    env = Envelope(
        message_id=mid, conversation_id="c-app", idempotency_key=mid,
        from_company=cid_from, to_company=cid_to,
        issued_at="2026-06-13T10:00:00Z", intent="quote.create",
        status="proposed", payload={"amount": 100},
    )
    return ingest(env, sign_envelope(env, company_private_key(cid_from)))


def test_approve_releases_to_inbox():
    create_company("app-c1", "travel_agency", "A1", manager_principal_id="human:mgr1")
    create_company("app-c2", "travel_agency", "A2", manager_principal_id="human:mgr2")
    _gated("app-c1", "app-c2", "app-m1")
    pend = pending_for_manager("human:mgr1")
    assert len(pend) == 1 and pend[0]["gated_class"] == "quote"
    approve(pend[0]["id"], "human:mgr1")
    inbox = poll_inbox("app-c2")
    got = [e for e in inbox if e["message_id"] == "app-m1"]
    assert got and got[0]["status"] == "approved"


def test_deny_never_delivers():
    create_company("app-c3", "travel_agency", "A3", manager_principal_id="human:mgr3")
    create_company("app-c4", "travel_agency", "A4", manager_principal_id="human:mgr4")
    _gated("app-c3", "app-c4", "app-m2")
    pend = pending_for_manager("human:mgr3")
    deny(pend[0]["id"], "human:mgr3", reason="too expensive")
    assert all(e["message_id"] != "app-m2" for e in poll_inbox("app-c4"))


def test_non_manager_cannot_decide():
    create_company("app-c5", "travel_agency", "A5", manager_principal_id="human:mgr5")
    create_company("app-c6", "travel_agency", "A6", manager_principal_id="human:mgr6")
    _gated("app-c5", "app-c6", "app-m3")
    pend = pending_for_manager("human:mgr5")
    with pytest.raises(ApprovalError):
        approve(pend[0]["id"], "human:mgr6")   # manager of the WRONG company
